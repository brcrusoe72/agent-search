"""AgentSearch v2.0 — Unified Information Tool

Search, read, extract, and adapt. One API for all information needs.

Endpoints:
  /search          — Multi-engine web search (deduplicated, scored)
  /search/extract  — Search + extract content via kill chain
  /search/deep     — Multi-query fusion search
  /search/jobs     — Job board search
  /search/stats    — Query statistics
  /read            — Kill chain content extraction (single URL)
  /read/batch      — Kill chain batch extraction (multiple URLs)
  /news            — Structured multi-source news
  /adapt/report    — Report fetch failures
  /adapt/stats     — View adaptation metrics
  /adapt/evolve    — Trigger self-improvement analysis
  /health          — Health check
  /engines         — List available search engines
"""

from __future__ import annotations

import os
import re
import time
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware

from app.cache import Cache
from app.content_cache import ContentCache
from app.dedup import deduplicate, deduplicate_with_scoring
from app.killchain import kill_chain, kill_chain_batch
from app.query_expansion import generate_query_variations
from app.evolver import Evolver
from app.database import query_db
from app.models import (
    AdaptReportRequest,
    AdaptStatsResponse,
    BatchReadRequest,
    BatchReadResponse,
    EngineInfo,
    EvolveResponse,
    HealthResponse,
    JobResult,
    JobSearchResponse,
    NewsResponse,
    NewsResult,
    ReadResponse,
    SearchMeta,
    SearchResponse,
    SearchResult,
    SearchStats,
)

import logging

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
VERSION = "2.0.0"

# Request logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agentsearch")

cache = Cache(ttl=CACHE_TTL)
content_cache: ContentCache | None = None
evolver: Evolver | None = None
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage async HTTP client and cache lifecycle."""
    global http_client, content_cache, evolver
    http_client = httpx.AsyncClient(timeout=30.0)
    content_cache = ContentCache()
    evolver = Evolver()

    # Periodic cache eviction (every hour)
    async def _evict_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                count = await content_cache.evict_expired()
                if count > 0:
                    logger.info(f"Evicted {count} expired cache entries")
            except Exception:
                pass

    evict_task = asyncio.create_task(_evict_loop())

    yield

    evict_task.cancel()
    await http_client.aclose()


app = FastAPI(
    title="AgentSearch",
    description="Unified information tool. Search, read, extract, adapt.",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Rate Limiting ---

_rate_store: dict[str, list[float]] = defaultdict(list)
_RATE_MAX_IPS = 500
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT", "60"))


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Per-IP rate limiter with memory bounds and request logging."""
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    path = request.url.path
    query = str(request.query_params) if request.query_params else ""
    logger.info(f"{client_ip} {request.method} {path} {query}")

    if len(_rate_store) > _RATE_MAX_IPS:
        stale = [ip for ip, ts in _rate_store.items() if not ts or now - max(ts) > 120]
        for ip in stale:
            del _rate_store[ip]
        if len(_rate_store) > _RATE_MAX_IPS:
            sorted_ips = sorted(_rate_store, key=lambda ip: max(_rate_store[ip]) if _rate_store[ip] else 0)
            for ip in sorted_ips[: len(sorted_ips) // 2]:
                del _rate_store[ip]

    _rate_store[client_ip] = [t for t in _rate_store[client_ip] if now - t < 60]
    if len(_rate_store[client_ip]) >= RATE_LIMIT_RPM:
        logger.warning(f"Rate limited: {client_ip}")
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    _rate_store[client_ip].append(now)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _query_searxng(
    query: str,
    count: int = 10,
    engines: str | None = None,
    categories: str | None = None,
) -> list[dict]:
    """Query SearXNG and return raw results."""
    assert http_client is not None
    params: dict = {"q": query, "format": "json", "pageno": 1}
    if engines:
        params["engines"] = engines
    if categories:
        params["categories"] = categories
    resp = await http_client.get(f"{SEARXNG_URL}/search", params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])[:count * 3]


# =========================================================================
# SEARCH ENDPOINTS
# =========================================================================

@app.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
    engines: str | None = Query(None, description="Comma-separated engine names"),
    domain: str | None = Query(None, description="Filter results to this domain"),
    exclude_domains: str | None = Query(None, description="Comma-separated domains to exclude"),
    fetch: bool = Query(False, description="Fetch page content via kill chain"),
) -> SearchResponse:
    """Search the web and return deduplicated, scored results."""
    start = time.time()

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")
    if len(q) > 500:
        raise HTTPException(status_code=400, detail="Query too long (max 500 chars)")

    cached_resp = cache.get(q, engines or "", count)
    if cached_resp is not None:
        cached_resp.meta.cached = True
        cached_resp.meta.response_time_ms = round((time.time() - start) * 1000, 1)
        return cached_resp

    try:
        raw = await _query_searxng(q, count * 2, engines)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"SearXNG error: {e}")

    results = deduplicate_with_scoring(raw)

    if domain:
        results = [r for r in results if domain.lower() in r.url.lower()]
    if exclude_domains:
        excluded = [d.strip().lower() for d in exclude_domains.split(",")]
        results = [r for r in results if not any(d in r.url.lower() for d in excluded)]

    results = results[:count]

    # Fetch content via kill chain if requested
    if fetch and results:
        assert http_client is not None
        kc_results = await kill_chain_batch(
            http_client,
            [r.url for r in results],
            searxng_url=SEARXNG_URL,
            content_cache=content_cache,
            max_concurrent=5,
        )
        for result, kc in zip(results, kc_results):
            result.content = kc.content
            # Log each fetch
            if content_cache:
                await content_cache.log_fetch(
                    kc.url, kc.strategy, kc.success, kc.chars,
                    kc.strategies_tried, kc.error,
                )

    engines_used = list({e for r in results for e in r.engines})
    elapsed = round((time.time() - start) * 1000, 1)

    await query_db.log_query(q, engines_used, len(results), elapsed)

    response = SearchResponse(
        results=results,
        meta=SearchMeta(
            query=q,
            total=len(results),
            engines_used=engines_used,
            cached=False,
            response_time_ms=elapsed,
        ),
    )

    cache.set(q, engines or "", count, response)
    return response


@app.get("/search/extract", response_model=SearchResponse)
async def search_extract(
    q: str = Query(..., description="Search query"),
    count: int = Query(5, ge=1, le=20, description="Number of results"),
    engines: str | None = Query(None, description="Comma-separated engine names"),
) -> SearchResponse:
    """Search and extract content from top results via kill chain."""
    return await search(q=q, count=count, engines=engines, domain=None, exclude_domains=None, fetch=True)


@app.get("/search/deep", response_model=SearchResponse)
async def search_deep(
    q: str = Query(..., description="Search query"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
) -> SearchResponse:
    """Multi-query fusion search with intelligent query variations."""
    start = time.time()

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")

    query_variations = generate_query_variations(q)

    async def _search_one(query: str) -> list[dict]:
        try:
            return await _query_searxng(query, count)
        except httpx.HTTPError:
            return []

    all_raw = await asyncio.gather(*[_search_one(v) for v in query_variations])

    combined = []
    for result_set in all_raw:
        if isinstance(result_set, list):
            combined.extend(result_set)

    results = deduplicate_with_scoring(combined)[:count]

    engines_used = list({e for r in results for e in r.engines})
    elapsed = round((time.time() - start) * 1000, 1)

    await query_db.log_query(q, engines_used, len(results), elapsed)

    return SearchResponse(
        results=results,
        meta=SearchMeta(
            query=q,
            total=len(results),
            engines_used=engines_used,
            cached=False,
            response_time_ms=elapsed,
            queries_used=query_variations,
        ),
    )


@app.get("/search/stats", response_model=SearchStats)
async def search_stats() -> SearchStats:
    """Get search query statistics and engine performance."""
    stats = await query_db.get_stats()
    return SearchStats(
        total_queries=stats["total_queries"],
        queries_per_engine=stats["queries_per_engine"],
        avg_results_per_engine=stats["avg_results_per_engine"],
    )


# =========================================================================
# READ ENDPOINTS (Kill Chain)
# =========================================================================

@app.get("/read", response_model=ReadResponse)
async def read_url(
    url: str = Query(..., description="URL to extract content from"),
    max_chars: int | None = Query(None, description="Max content length"),
    skip_cache: bool = Query(False, description="Bypass content cache"),
) -> ReadResponse:
    """Extract readable content from a URL using the kill chain.

    Tries 9 strategies in escalating order: direct fetch, readability,
    UA rotation, Wayback Machine, Google Cache, search-about, custom
    adapters, PDF extraction, and YouTube transcripts.
    """
    assert http_client is not None
    start = time.time()

    result = await kill_chain(
        http_client,
        url,
        searxng_url=SEARXNG_URL,
        content_cache=content_cache,
        max_chars=max_chars,
        skip_cache=skip_cache,
    )

    # Log the fetch
    if content_cache:
        await content_cache.log_fetch(
            result.url, result.strategy, result.success,
            result.chars, result.strategies_tried, result.error,
        )

    return ReadResponse(
        url=result.url,
        content=result.content,
        strategy=result.strategy,
        chars=result.chars,
        cached=result.cached,
        strategies_tried=result.strategies_tried,
        error=result.error,
        success=result.success,
    )


@app.post("/read/batch", response_model=BatchReadResponse)
async def read_batch(body: BatchReadRequest) -> BatchReadResponse:
    """Extract content from multiple URLs concurrently via kill chain."""
    assert http_client is not None

    results = await kill_chain_batch(
        http_client,
        body.urls,
        searxng_url=SEARXNG_URL,
        content_cache=content_cache,
        max_chars=body.max_chars,
        max_concurrent=5,
    )

    # Log all fetches
    if content_cache:
        for r in results:
            await content_cache.log_fetch(
                r.url, r.strategy, r.success,
                r.chars, r.strategies_tried, r.error,
            )

    read_responses = [
        ReadResponse(
            url=r.url,
            content=r.content,
            strategy=r.strategy,
            chars=r.chars,
            cached=r.cached,
            strategies_tried=r.strategies_tried,
            error=r.error,
            success=r.success,
        )
        for r in results
    ]

    return BatchReadResponse(
        results=read_responses,
        total=len(results),
        successful=sum(1 for r in results if r.success),
        failed=sum(1 for r in results if not r.success),
    )


# =========================================================================
# NEWS ENDPOINT
# =========================================================================

NEWS_ENGINES = "google news,bing news,duckduckgo news,yahoo news,brave.news,startpage news,qwant news,wikinews,reuters"


@app.get("/news", response_model=NewsResponse)
async def news(
    q: str = Query(..., description="News search query"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
    engines: str | None = Query(None, description="Override news engines"),
) -> NewsResponse:
    """Search news across multiple news engines with deduplication."""
    start = time.time()

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")

    news_engines = engines or NEWS_ENGINES

    try:
        raw = await _query_searxng(q, count * 3, engines=news_engines, categories="news")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"SearXNG error: {e}")

    # Deduplicate and build news results
    seen_urls: set[str] = set()
    results: list[NewsResult] = []

    for r in raw:
        url = r.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # Extract source from engine or URL
        source = None
        raw_engines = r.get("engines", [])
        if isinstance(raw_engines, str):
            raw_engines = [raw_engines]

        # Try to get source from the result metadata
        source = r.get("source", None)
        if not source:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc.replace("www.", "")
            source = domain

        results.append(
            NewsResult(
                title=r.get("title", ""),
                url=url,
                snippet=r.get("content", r.get("snippet", "")),
                source=source,
                published=r.get("publishedDate", r.get("published_date", None)),
                engines=raw_engines,
                score=len(raw_engines) / 3.0,  # Simple multi-engine score
            )
        )

    # Sort by engine agreement, then recency
    results.sort(key=lambda x: x.score, reverse=True)
    results = results[:count]

    engines_used = list({e for r in results for eng in r.engines for e in ([eng] if isinstance(eng, str) else eng)})
    elapsed = round((time.time() - start) * 1000, 1)

    return NewsResponse(
        results=results,
        meta=SearchMeta(
            query=q,
            total=len(results),
            engines_used=engines_used,
            cached=False,
            response_time_ms=elapsed,
        ),
    )


# =========================================================================
# ADAPT ENDPOINTS (Evolver)
# =========================================================================

@app.post("/adapt/report")
async def adapt_report(body: AdaptReportRequest) -> dict:
    """Report a fetch failure for pattern analysis."""
    assert evolver is not None
    return evolver.report_failure(body.url, body.strategies_tried, body.error)


@app.get("/adapt/stats", response_model=AdaptStatsResponse)
async def adapt_stats(
    days: int = Query(7, ge=1, le=90, description="Analysis period in days"),
) -> AdaptStatsResponse:
    """View adaptation statistics — strategy effectiveness, failing domains, etc."""
    assert evolver is not None
    stats = evolver.get_adaptation_stats(days)
    return AdaptStatsResponse(**stats)


@app.post("/adapt/evolve", response_model=EvolveResponse)
async def adapt_evolve(
    days: int = Query(7, ge=1, le=90, description="Analysis period in days"),
) -> EvolveResponse:
    """Trigger self-improvement analysis. Returns recommendations."""
    assert evolver is not None
    result = evolver.evolve(days)
    return EvolveResponse(
        overall_health=result["overall_health"],
        stats_summary=result["stats_summary"],
        recommendations=result["recommendations"],
        existing_adapters=result["existing_adapters"],
    )


# =========================================================================
# JOBS ENDPOINT
# =========================================================================

def _parse_salary(text: str) -> tuple[int | None, int | None]:
    """Try to extract salary range from text."""
    patterns = [
        r"\$(\d{2,3})[,.]?(\d{3})[\s\-–]+\$?(\d{2,3})[,.]?(\d{3})",
        r"\$(\d{2,3})k[\s\-–]+\$?(\d{2,3})k",
        r"(\d{2,3})[,.]?(\d{3})\s*(?:to|-|–)\s*(\d{2,3})[,.]?(\d{3})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            groups = m.groups()
            if len(groups) == 4:
                lo = int(groups[0]) * 1000 + int(groups[1])
                hi = int(groups[2]) * 1000 + int(groups[3])
                return lo, hi
            elif len(groups) == 2:
                return int(groups[0]) * 1000, int(groups[1]) * 1000
    return None, None


@app.get("/search/jobs", response_model=JobSearchResponse)
async def search_jobs(
    q: str = Query(..., description="Job search query"),
    location: str | None = Query(None, description="Job location"),
    salary_min: int | None = Query(None, description="Minimum salary filter"),
) -> JobSearchResponse:
    """Search for jobs across multiple job boards."""
    start = time.time()

    job_sites = [
        "site:linkedin.com/jobs",
        "site:indeed.com",
        "site:glassdoor.com",
        "site:ziprecruiter.com",
    ]
    location_str = f" {location}" if location else ""
    all_raw: list[dict] = []

    for site in job_sites:
        query = f"{q}{location_str} {site}"
        try:
            raw = await _query_searxng(query, count=10)
            all_raw.extend(raw)
        except httpx.HTTPError:
            continue

    results: list[JobResult] = []
    seen_urls: set[str] = set()

    for r in all_raw:
        url = r.get("url", "")
        if url in seen_urls or not url:
            continue
        seen_urls.add(url)

        snippet = r.get("content", r.get("snippet", ""))
        sal_min, sal_max = _parse_salary(snippet + " " + r.get("title", ""))

        if salary_min and sal_max and sal_max < salary_min:
            continue

        source = None
        for s in ["linkedin", "indeed", "glassdoor", "ziprecruiter"]:
            if s in url.lower():
                source = s.capitalize()
                break

        results.append(
            JobResult(
                title=r.get("title", ""),
                url=url,
                snippet=snippet,
                company=None,
                location=location,
                salary_min=sal_min,
                salary_max=sal_max,
                source=source,
            )
        )

    elapsed = round((time.time() - start) * 1000, 1)

    return JobSearchResponse(
        results=results,
        meta=SearchMeta(
            query=q,
            total=len(results),
            engines_used=["google", "bing", "duckduckgo"],
            response_time_ms=elapsed,
        ),
    )


# =========================================================================
# SYSTEM ENDPOINTS
# =========================================================================

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — verifies SearXNG connectivity."""
    assert http_client is not None
    try:
        resp = await http_client.get(f"{SEARXNG_URL}/healthz", timeout=5.0)
        searxng_ok = resp.status_code == 200
    except Exception:
        searxng_ok = False

    return HealthResponse(
        status="healthy" if searxng_ok else "degraded",
        searxng_available=searxng_ok,
        version=VERSION,
    )


@app.get("/engines", response_model=list[EngineInfo])
async def engines() -> list[EngineInfo]:
    """List available search engines from SearXNG."""
    assert http_client is not None
    try:
        resp = await http_client.get(f"{SEARXNG_URL}/config", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        return [
            EngineInfo(
                name=e.get("name", ""),
                shortcut=e.get("shortcut", ""),
                enabled=e.get("enabled", False),
            )
            for e in data.get("engines", [])
        ]
    except Exception:
        raise HTTPException(status_code=502, detail="Could not fetch engine list from SearXNG")
