"""SQLite-backed content cache for extracted page content.

Caches fetched content by URL so the same article isn't re-extracted
across multiple consumers (CEO pipeline, direct queries, etc.).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger("agentsearch.content_cache")

# Default TTLs
DEFAULT_TTL = 86400       # 24 hours for articles
NEWS_TTL = 3600           # 1 hour for news
YOUTUBE_TTL = 604800      # 7 days for YouTube transcripts
PDF_TTL = 604800          # 7 days for PDFs
FETCH_LOG_RETENTION_DAYS = int(os.getenv("FETCH_LOG_RETENTION_DAYS", "30"))
SQLITE_VACUUM_MIN_DELETED_ROWS = int(os.getenv("SQLITE_VACUUM_MIN_DELETED_ROWS", "1000"))

# Strategy → TTL mapping
STRATEGY_TTL = {
    "youtube": YOUTUBE_TTL,
    "pdf": PDF_TTL,
    "wayback": DEFAULT_TTL * 7,   # Wayback content is stable
    "google-cache": DEFAULT_TTL,
    "direct": DEFAULT_TTL,
    "readability": DEFAULT_TTL,
    "ua-rotate": DEFAULT_TTL,
    "search-about": DEFAULT_TTL,
    "adapter-403": DEFAULT_TTL,
    "adapter-empty": DEFAULT_TTL,
    "adapter-parse": DEFAULT_TTL,
}


class ContentCache:
    """SQLite-backed content cache with per-strategy TTLs."""

    def __init__(self, db_path: str | None = None):
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        self.db_path = db_path or str(data_dir / "content_cache.db")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._stats = {"hits": 0, "misses": 0, "sets": 0, "evictions": 0}

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS content_cache (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    content TEXT NOT NULL,
                    strategy TEXT,
                    chars INTEGER,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    domain TEXT,
                    strategy TEXT,
                    success INTEGER NOT NULL,
                    chars INTEGER DEFAULT 0,
                    strategies_tried TEXT,
                    error TEXT,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_expires ON content_cache(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fl_domain ON fetch_log(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fl_ts ON fetch_log(timestamp)")
            conn.commit()

    @staticmethod
    def _url_hash(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    async def get(self, url: str) -> Optional[str]:
        """Get cached content for URL. Returns None if not cached or expired."""
        def _get():
            h = self._url_hash(url)
            now = time.time()
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT content FROM content_cache WHERE url_hash = ? AND expires_at > ?",
                    (h, now),
                ).fetchone()
                return row[0] if row else None

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _get)
        if result is not None:
            self._stats["hits"] += 1
        else:
            self._stats["misses"] += 1
        return result

    async def set(self, url: str, content: str, strategy: str) -> None:
        """Cache content for a URL with strategy-appropriate TTL."""
        ttl = STRATEGY_TTL.get(strategy, DEFAULT_TTL)

        def _set():
            h = self._url_hash(url)
            now = time.time()
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO content_cache
                    (url_hash, url, content, strategy, chars, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (h, url, content, strategy, len(content), now, now + ttl))
                conn.commit()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _set)
        self._stats["sets"] += 1

    async def log_fetch(
        self,
        url: str,
        strategy: Optional[str],
        success: bool,
        chars: int = 0,
        strategies_tried: Optional[list[str]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log a fetch attempt for analytics."""
        from urllib.parse import urlparse
        domain = urlparse(url).netloc

        def _log():
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO fetch_log
                    (url, domain, strategy, success, chars, strategies_tried, error, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    url, domain, strategy, int(success), chars,
                    ",".join(strategies_tried) if strategies_tried else None,
                    error, time.time(),
                ))
                conn.commit()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _log)

    async def get_stats(self) -> dict:
        """Get cache and fetch statistics."""
        def _stats():
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                now = time.time()

                # Cache stats
                total_cached = conn.execute(
                    "SELECT COUNT(*) FROM content_cache WHERE expires_at > ?", (now,)
                ).fetchone()[0]

                # Fetch stats
                total_fetches = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0]
                successful = conn.execute(
                    "SELECT COUNT(*) FROM fetch_log WHERE success = 1"
                ).fetchone()[0]

                # Strategy effectiveness
                strategy_stats = {}
                for row in conn.execute("""
                    SELECT strategy, COUNT(*) as total,
                           SUM(success) as successes,
                           AVG(chars) as avg_chars
                    FROM fetch_log
                    WHERE strategy IS NOT NULL
                    GROUP BY strategy
                    ORDER BY total DESC
                """):
                    strategy_stats[row["strategy"]] = {
                        "total": row["total"],
                        "successes": row["successes"],
                        "success_rate": row["successes"] / row["total"] if row["total"] > 0 else 0,
                        "avg_chars": round(row["avg_chars"] or 0),
                    }

                # Domain failure rates (top failing domains)
                failing_domains = []
                for row in conn.execute("""
                    SELECT domain, COUNT(*) as total,
                           SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures
                    FROM fetch_log
                    WHERE timestamp > ?
                    GROUP BY domain
                    HAVING failures > 2
                    ORDER BY failures DESC
                    LIMIT 20
                """, (now - 86400 * 7,)):
                    failing_domains.append({
                        "domain": row["domain"],
                        "total": row["total"],
                        "failures": row["failures"],
                        "failure_rate": row["failures"] / row["total"],
                    })

                return {
                    "cache": {
                        "active_entries": total_cached,
                        "memory_hits": self._stats["hits"],
                        "memory_misses": self._stats["misses"],
                        "total_sets": self._stats["sets"],
                    },
                    "fetches": {
                        "total": total_fetches,
                        "successful": successful,
                        "success_rate": successful / total_fetches if total_fetches > 0 else 0,
                    },
                    "strategies": strategy_stats,
                    "failing_domains": failing_domains,
                }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _stats)

    async def evict_expired(self) -> int:
        """Remove expired cache entries. Returns count removed."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM content_cache WHERE expires_at < ?", (time.time(),)
            )
            conn.commit()
            count = cursor.rowcount
        self._stats["evictions"] += count
        return count

    def maintain(
        self,
        *,
        fetch_log_retention_days: int = FETCH_LOG_RETENTION_DAYS,
        vacuum_min_deleted_rows: int = SQLITE_VACUUM_MIN_DELETED_ROWS,
    ) -> dict:
        """Prune expired cache and old fetch logs, then optimize SQLite."""
        retention_seconds = max(fetch_log_retention_days, 0) * 86400

        now = time.time()
        fetch_cutoff = now - retention_seconds
        conn = sqlite3.connect(self.db_path)
        try:
            expired_cursor = conn.execute(
                "DELETE FROM content_cache WHERE expires_at < ?", (now,)
            )
            expired_cache = max(expired_cursor.rowcount, 0)

            fetch_cursor = conn.execute(
                "DELETE FROM fetch_log WHERE timestamp < ?", (fetch_cutoff,)
            )
            old_fetch_logs = max(fetch_cursor.rowcount, 0)
            conn.commit()

            conn.execute("PRAGMA optimize")
            conn.commit()

            deleted_rows = expired_cache + old_fetch_logs
            vacuumed = deleted_rows >= vacuum_min_deleted_rows > 0
            if vacuumed:
                conn.execute("VACUUM")

            result = {
                "expired_content_cache": expired_cache,
                "old_fetch_logs": old_fetch_logs,
                "deleted_rows": deleted_rows,
                "fetch_log_retention_days": fetch_log_retention_days,
                "vacuumed": vacuumed,
            }
        finally:
            conn.close()
        self._stats["evictions"] += result["expired_content_cache"]
        return result
