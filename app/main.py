"""AgentSearch — Free, self-hosted search API for AI agents.

Wraps SearXNG to provide clean, structured JSON search results.
Zero API keys. One command to deploy.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.cache import Cache
from app.dedup import deduplicate
from app.models import (
    EngineInfo,
    HealthResponse,
    JobResult,
    JobSearchResponse,
    SearchMeta,
    SearchResponse,
)

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
VERSION = "1.0.0"

cache = Cache(ttl=CACHE_TTL)
http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage async HTTP client lifecycle."""
    global http_client
    http_client = httpx.AsyncClient(timeout=30.0)
    yield
    await http_client.aclose()


app = FastAPI(
    title="AgentSearch",
    description="Free, self-hosted search API for AI agents. Wraps SearXNG for clean JSON results.",
    version=VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _query_searxng(
    query: str,
    count: int = 10,
    engines: str | None = None,
) -> list[dict]:
    """Query SearXNG and return raw results."""
    assert http_client is not None
    params: dict = {
        "q": query,
        "format": "json",
        "pageno": 1,
    }
    if engines:
        params["engines"] = engines
    resp = await http_client.get(f"{SEARXNG_URL}/search", params=params)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])[:count * 3]  # fetch extra for dedup


@app.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
    engines: str | None = Query(None, description="Comma-separated engine names (e.g. google,bing)"),
    domain: str | None = Query(None, description="Filter results to this domain"),
    exclude_domains: str | None = Query(None, description="Comma-separated domains to exclude"),
) -> SearchResponse:
    """Search the web and return deduplicated, scored results."""
    start = time.time()

    # Check cache
    cached = cache.get(q, engines or "", count)
    if cached is not None:
        cached.meta.cached = True
        cached.meta.response_time_ms = round((time.time() - start) * 1000, 1)
        return cached

    try:
        raw = await _query_searxng(q, count, engines)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"SearXNG error: {e}")

    results = deduplicate(raw)

    # Domain filtering
    if domain:
        results = [r for r in results if domain.lower() in r.url.lower()]
    if exclude_domains:
        excluded = [d.strip().lower() for d in exclude_domains.split(",")]
        results = [r for r in results if not any(d in r.url.lower() for d in excluded)]

    # Trim to requested count and re-number positions
    results = results[:count]
    for i, r in enumerate(results):
        r.position = i + 1

    engines_used = list({e for r in results for e in r.engines})
    elapsed = round((time.time() - start) * 1000, 1)

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
    location: str | None = Query(None, description="Job location (e.g. remote, NYC)"),
    salary_min: int | None = Query(None, description="Minimum salary filter"),
) -> JobSearchResponse:
    """Search for jobs across multiple job boards."""
    start = time.time()

    job_sites = ["site:linkedin.com/jobs", "site:indeed.com", "site:glassdoor.com", "site:ziprecruiter.com"]
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


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — also verifies SearXNG connectivity."""
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
