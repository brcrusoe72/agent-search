"""Pydantic models for AgentSearch API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class SearchResult(BaseModel):
    """A single deduplicated search result."""
    title: str
    url: str
    snippet: str
    engines: list[str] = Field(description="Engines that returned this result")
    score: float = Field(description="Relevance score (higher = more engines agreed)")
    position: int = Field(description="Position in final ranked list")
    content: str | None = Field(None, description="Extracted page content (when fetch=true)")


class SearchMeta(BaseModel):
    """Metadata about the search response."""
    query: str
    total: int
    engines_used: list[str]
    cached: bool = False
    response_time_ms: float = 0.0
    queries_used: list[str] | None = Field(None, description="For multi-query fusion")
    mode: str | None = Field(None, description="Named search strategy mode")
    engine_attempts: list[dict] = Field(default_factory=list, description="Strategy engine packs attempted")
    fallback_reason: str | None = Field(None, description="Why a later strategy pack was attempted")
    upstream_status: str = Field("ok", description="ok | degraded | error")
    upstream_errors: list[str] = Field(default_factory=list, description="SearXNG/backend errors")
    unresponsive_engines: list[str] = Field(default_factory=list, description="Engines SearXNG reported as unresponsive")


class SearchResponse(BaseModel):
    """Top-level search response."""
    results: list[SearchResult]
    meta: SearchMeta


class SearchStats(BaseModel):
    """Query statistics and engine performance."""
    total_queries: int
    queries_per_engine: dict[str, int]
    avg_results_per_engine: dict[str, float]


# ---------------------------------------------------------------------------
# Read (Kill Chain)
# ---------------------------------------------------------------------------

class TrustInfo(BaseModel):
    """Domain trust assessment."""
    domain: str = ""
    tier: str = Field("unknown", description="established | standard | new | suspicious | unknown")
    score: float = Field(0.5, description="Trust score 0.0–1.0")
    reasons: list[str] = Field(default_factory=list)
    https: bool = True
    lookalike_of: str | None = None


class ReadResponse(BaseModel):
    """Response from /read — extracted content via kill chain."""
    url: str
    content: str | None = Field(None, description="Extracted content text")
    strategy: str | None = Field(None, description="Strategy that succeeded")
    chars: int = Field(0, description="Content length in characters")
    cached: bool = Field(False, description="Whether content came from cache")
    strategies_tried: list[str] = Field(default_factory=list, description="All strategies attempted")
    error: str | None = Field(None, description="Error message if extraction failed")
    success: bool = Field(description="Whether content was extracted")
    trust: TrustInfo | None = Field(None, description="Domain trust assessment")


class BatchReadRequest(BaseModel):
    """Request body for /read/batch."""
    urls: list[str] = Field(..., min_length=1, max_length=20, description="URLs to extract (max 20)")
    max_chars: int | None = Field(None, description="Max chars per result")


class BatchReadResponse(BaseModel):
    """Response from /read/batch."""
    results: list[ReadResponse]
    total: int
    successful: int
    failed: int


class BrowserLink(BaseModel):
    """Link extracted from a rendered page."""

    text: str
    url: str


class BrowserFetchResponse(BaseModel):
    """Response from /providers/browser/fetch."""

    url: str
    final_url: str = ""
    title: str = ""
    content: str | None = Field(None, description="Extracted rendered text")
    chars: int = 0
    links: list[BrowserLink] = Field(default_factory=list)
    success: bool = False
    strategy: str = "browser-render"
    error: str | None = None
    challenge_detected: bool = False
    blocked_reason: str | None = None
    render_time_ms: float = 0.0
    trust: TrustInfo | None = Field(None, description="Domain trust assessment")


# ---------------------------------------------------------------------------
# News
# ---------------------------------------------------------------------------

class NewsResult(BaseModel):
    """A structured news result."""
    title: str
    url: str
    snippet: str
    source: str | None = Field(None, description="News source name")
    published: str | None = Field(None, description="Publication date/time")
    engines: list[str] = Field(default_factory=list)
    score: float = 0.0


class NewsResponse(BaseModel):
    """Top-level news response."""
    results: list[NewsResult]
    meta: SearchMeta


# ---------------------------------------------------------------------------
# Adapt (Evolver)
# ---------------------------------------------------------------------------

class AdaptReportRequest(BaseModel):
    """Report a fetch failure for pattern analysis."""
    url: str
    strategies_tried: list[str] = Field(default_factory=list)
    error: str | None = None


class AdaptStatsResponse(BaseModel):
    """Adaptation statistics."""
    period_days: int
    overall: dict
    strategies: dict
    domains: list[dict]
    chains: list[dict]


class EvolveResponse(BaseModel):
    """Evolution analysis results."""
    overall_health: str = Field(description="Health grade A-F")
    stats_summary: dict
    recommendations: list[dict]
    existing_adapters: list[str]
    actions_taken: list[str] = Field(default_factory=list, description="Auto-applied actions")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobResult(BaseModel):
    """A structured job search result."""
    title: str
    url: str
    snippet: str
    company: str | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    source: str | None = None


class JobSearchResponse(BaseModel):
    """Top-level job search response."""
    results: list[JobResult]
    meta: SearchMeta


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

class EngineInfo(BaseModel):
    """Info about an available search engine."""
    name: str
    shortcut: str
    enabled: bool
    categories: list[str] = Field(default_factory=list, description="Engine categories")


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    searxng_available: bool
    search_available: bool = False
    upstream_status: str = "unknown"
    upstream_errors: list[str] = Field(default_factory=list)
    unresponsive_engines: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
