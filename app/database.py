"""SQLite database for query logging and statistics."""

import sqlite3
import asyncio
import time
from pathlib import Path
from typing import Dict, List, Optional
from contextlib import asynccontextmanager


class QueryDatabase:
    """Simple SQLite database for tracking queries and engine performance."""
    
    def __init__(self, db_path: str = "/app/data/query_log.db"):
        self.db_path = db_path
        self._ensure_data_dir()
        self._init_db()
    
    def _ensure_data_dir(self):
        """Ensure the data directory exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
    
    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
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
    
    async def log_query(self, query: str, engines: List[str], result_count: int, response_time_ms: float):
        """Log a search query with results."""
        timestamp = time.time()
        
        # Run in thread pool to avoid blocking
        def _insert():
            with sqlite3.connect(self.db_path) as conn:
                for engine in engines:
                    conn.execute("""
                        INSERT INTO query_log (query, timestamp, engine, result_count, response_time_ms)
                        VALUES (?, ?, ?, ?, ?)
                    """, (query, timestamp, engine, result_count, response_time_ms))
                conn.commit()
        
        await asyncio.get_event_loop().run_in_executor(None, _insert)
    
    async def get_stats(self) -> Dict:
        """Get query statistics."""
        def _get_stats():
            with sqlite3.connect(self.db_path) as conn:
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
        
        return await asyncio.get_event_loop().run_in_executor(None, _get_stats)


# Global instance
query_db = QueryDatabase()