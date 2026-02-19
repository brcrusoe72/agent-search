"""In-memory TTL cache for search results."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheEntry:
    """A single cached value with expiry."""

    value: Any
    expires_at: float


@dataclass
class Cache:
    """Simple in-memory TTL cache.

    Prevents hammering search engines with duplicate queries.
    Thread-safe enough for async FastAPI (single-process GIL).
    """

    ttl: int = 3600
    max_size: int = 1000
    _store: dict[str, CacheEntry] = field(default_factory=dict)

    @staticmethod
    def _key(query: str, engines: str, count: int) -> str:
        """Generate a cache key from search parameters."""
        raw = f"{query}|{engines}|{count}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, query: str, engines: str = "", count: int = 10) -> Any | None:
        """Get a cached value if it exists and hasn't expired."""
        key = self._key(query, engines, count)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry.expires_at:
            del self._store[key]
            return None
        return entry.value

    def set(self, query: str, engines: str, count: int, value: Any) -> None:
        """Cache a value with TTL."""
        key = self._key(query, engines, count)
        # Evict oldest if over limit
        if len(self._store) >= self.max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
            del self._store[oldest_key]
        self._store[key] = CacheEntry(value=value, expires_at=time.time() + self.ttl)

    def clear(self) -> None:
        """Clear all cached entries."""
        self._store.clear()

    @property
    def size(self) -> int:
        """Number of entries (including potentially expired)."""
        return len(self._store)
