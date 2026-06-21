"""SQLite database for query logging and statistics."""

import sqlite3
import os
import time
import logging
from pathlib import Path
from typing import Dict, List

logger = logging.getLogger("agentsearch.database")

QUERY_LOG_RETENTION_DAYS = int(os.getenv("QUERY_LOG_RETENTION_DAYS", "30"))
SQLITE_VACUUM_MIN_DELETED_ROWS = int(os.getenv("SQLITE_VACUUM_MIN_DELETED_ROWS", "1000"))


class QueryDatabase:
    """Simple SQLite database for tracking queries and engine performance."""
    
    def __init__(self, db_path: str | None = None):
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        self.db_path = db_path or str(data_dir / "query_log.db")
        self.timeout = float(os.getenv("SQLITE_TIMEOUT", "1.0"))
        self.disabled = False
        self._ensure_data_dir()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=self.timeout)
        conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        return conn
    
    def _ensure_data_dir(self):
        """Ensure the data directory exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    
    def _init_db(self):
        """Initialize the database schema."""
        try:
            with self._connect() as conn:
                try:
                    conn.execute("PRAGMA journal_mode = WAL")
                except sqlite3.OperationalError as exc:
                    logger.debug("SQLite WAL mode unavailable: %s", exc)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS query_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        query TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        engine TEXT NOT NULL,
                        result_count INTEGER NOT NULL,
                        response_time_ms REAL
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_query_timestamp
                    ON query_log(query, timestamp)
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_engine
                    ON query_log(engine)
                """)
                conn.commit()
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc).lower():
                self.disabled = True
                return
            raise
    
    async def log_query(self, query: str, engines: List[str], result_count: int, response_time_ms: float):
        """Log a search query with bounded SQLite lock waiting."""
        if self.disabled:
            return
        timestamp = time.time()
        rows = [(query, timestamp, engine, result_count, response_time_ms) for engine in engines]

        try:
            with self._connect() as conn:
                conn.executemany("""
                    INSERT INTO query_log (query, timestamp, engine, result_count, response_time_ms)
                    VALUES (?, ?, ?, ?, ?)
                """, rows)
                conn.commit()
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc).lower():
                self.disabled = True
                return
            raise
    
    async def get_stats(self) -> Dict:
        """Get query statistics."""
        if self.disabled:
            return {
                'total_queries': 0,
                'queries_per_engine': {},
                'avg_results_per_engine': {},
            }
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row

            # Total queries
            total = conn.execute("SELECT COUNT(DISTINCT query) FROM query_log").fetchone()[0]

            # Queries per engine
            engine_counts = {}
            for row in conn.execute("SELECT engine, COUNT(*) as count FROM query_log GROUP BY engine"):
                engine_counts[row['engine']] = row['count']

            # Average results per engine
            avg_results = {}
            for row in conn.execute("""
                SELECT engine, AVG(result_count) as avg_results
                FROM query_log
                GROUP BY engine
            """):
                avg_results[row['engine']] = round(row['avg_results'], 2)

            return {
                'total_queries': total,
                'queries_per_engine': engine_counts,
                'avg_results_per_engine': avg_results
            }

    def maintain(
        self,
        *,
        query_log_retention_days: int = QUERY_LOG_RETENTION_DAYS,
        vacuum_min_deleted_rows: int = SQLITE_VACUUM_MIN_DELETED_ROWS,
    ) -> Dict:
        """Prune old query logs, then optimize SQLite."""
        if self.disabled:
            return {
                "old_query_logs": 0,
                "deleted_rows": 0,
                "query_log_retention_days": query_log_retention_days,
                "vacuumed": False,
                "disabled": True,
            }

        retention_seconds = max(query_log_retention_days, 0) * 86400

        try:
            cutoff = time.time() - retention_seconds
            conn = self._connect()
            try:
                cursor = conn.execute(
                    "DELETE FROM query_log WHERE timestamp < ?", (cutoff,)
                )
                old_query_logs = max(cursor.rowcount, 0)
                conn.commit()

                conn.execute("PRAGMA optimize")
                conn.commit()

                vacuumed = old_query_logs >= vacuum_min_deleted_rows > 0
                if vacuumed:
                    conn.execute("VACUUM")

                return {
                    "old_query_logs": old_query_logs,
                    "deleted_rows": old_query_logs,
                    "query_log_retention_days": query_log_retention_days,
                    "vacuumed": vacuumed,
                    "disabled": False,
                }
            finally:
                conn.close()
        except sqlite3.OperationalError as exc:
            if "readonly" in str(exc).lower():
                self.disabled = True
                return {
                    "old_query_logs": 0,
                    "deleted_rows": 0,
                    "query_log_retention_days": query_log_retention_days,
                    "vacuumed": False,
                    "disabled": True,
                }
            raise


# Global instance
query_db = QueryDatabase()
