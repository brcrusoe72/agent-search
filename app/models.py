"""Pydantic models for AgentSearch API request/response schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """A single deduplicated search result."""

    title: str
    url: str
    snippet: str
    engines: list[str] = Field(description="Engines that returned this result")
    score: float = Field(description="Relevance score (higher = more engines agreed)")
    position: int = Field(description="Position in final ranked list")


class SearchMeta(BaseModel):
    """Metadata about the search response."""

    query: str
    total: int
    engines_used: list[str]
    cached: bool = False
    response_time_ms: float = 0.0


class SearchResponse(BaseModel):
    """Top-level search response."""

    results: list[SearchResult]
    meta: SearchMeta


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


class EngineInfo(BaseModel):
    """Info about an available search engine."""

    name: str
    shortcut: str
    enabled: bool


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    searxng_available: bool
    version: str = "1.0.0"
