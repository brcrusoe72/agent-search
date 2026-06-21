"""Data models for AgentSearch API responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class SearchResult:
    """A single search result."""

    title: str
    url: str
    snippet: str
    engines: List[str] = field(default_factory=list)
    score: float = 0.0
    position: int = 0
    content: Optional[str] = None


@dataclass
class SearchMeta:
    """Metadata about a search response."""

    query: str = ""
    total: int = 0
    engines_used: List[str] = field(default_factory=list)
    cached: bool = False
    response_time_ms: float = 0.0
    queries_used: Optional[List[str]] = None
    mode: Optional[str] = None
    engine_attempts: List[dict[str, Any]] = field(default_factory=list)
    fallback_reason: Optional[str] = None
    upstream_status: str = "ok"
    upstream_errors: List[str] = field(default_factory=list)
    unresponsive_engines: List[str] = field(default_factory=list)


@dataclass
class SearchResponse:
    """Top-level search response."""

    results: List[SearchResult] = field(default_factory=list)
    meta: Optional[SearchMeta] = None


@dataclass
class ReadResult:
    """Response from content extraction."""

    url: str = ""
    content: Optional[str] = None
    strategy: Optional[str] = None
    chars: int = 0
    cached: bool = False
    strategies_tried: List[str] = field(default_factory=list)
    error: Optional[str] = None
    success: bool = False


@dataclass
class BatchReadResponse:
    """Response from batch content extraction."""

    results: List[ReadResult] = field(default_factory=list)
    total: int = 0
    successful: int = 0
    failed: int = 0


@dataclass
class BrowserLink:
    """Link extracted from a rendered page."""

    text: str = ""
    url: str = ""


@dataclass
class BrowserFetchResponse:
    """Response from browser-render extraction."""

    url: str = ""
    final_url: str = ""
    title: str = ""
    content: Optional[str] = None
    chars: int = 0
    links: List[BrowserLink] = field(default_factory=list)
    success: bool = False
    strategy: str = "browser-render"
    error: Optional[str] = None
    challenge_detected: bool = False
    blocked_reason: Optional[str] = None
    render_time_ms: float = 0.0


@dataclass
class NewsResult:
    """A structured news result."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    source: Optional[str] = None
    published: Optional[str] = None
    engines: List[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class NewsResponse:
    """Top-level news response."""

    results: List[NewsResult] = field(default_factory=list)
    meta: Optional[SearchMeta] = None


@dataclass
class JobResult:
    """A structured job search result."""

    title: str = ""
    url: str = ""
    snippet: str = ""
    company: Optional[str] = None
    location: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    source: Optional[str] = None


@dataclass
class JobSearchResponse:
    """Top-level job search response."""

    results: List[JobResult] = field(default_factory=list)
    meta: Optional[SearchMeta] = None


@dataclass
class HealthResponse:
    """Health check response."""

    status: str = ""
    searxng_available: bool = False
    search_available: bool = False
    upstream_status: str = "unknown"
    upstream_errors: List[str] = field(default_factory=list)
    unresponsive_engines: List[str] = field(default_factory=list)
    version: str = ""
