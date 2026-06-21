"""AgentSearch v2.0 — Unified Information Tool

Search, read, extract, and adapt. One API for all information needs.

Endpoints:
  /search          — Multi-engine web search (deduplicated, scored)
  /search/strategy — Named engine strategy search
  /search/extract  — Search + extract content via kill chain
  /search/deep     — Multi-query fusion search
  /search/jobs     — Job board search
  /search/policy   — Policy/regulatory search
  /search/sources  — Primary source discovery
  /search/sources/institutions — List source registry institutions
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
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncGenerator
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.cache import Cache
from app.content_cache import ContentCache
from app.dedup import deduplicate_with_scoring
from app.killchain import kill_chain, kill_chain_batch
from app.query_expansion import generate_query_variations
from app.evolver import Evolver
from app.source_tracer import trace_sources, get_institution_registry
from app.database import query_db
from app.search_providers import provider_by_name
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
    TrustInfo,
)

import logging

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://searxng:8080")
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
VERSION = "2.0.0"
HEALTHCHECK_QUERY = os.getenv("HEALTHCHECK_QUERY", "python programming language")
HEALTHCHECK_ENGINES = os.getenv("HEALTHCHECK_ENGINES", "github")
HEALTHCHECK_SEARCH_TIMEOUT = float(os.getenv("HEALTHCHECK_SEARCH_TIMEOUT", "15"))
HEALTHCHECK_CACHE_TTL = float(os.getenv("HEALTHCHECK_CACHE_TTL", "15"))
ENGINE_CATALOG_CACHE_TTL = float(os.getenv("ENGINE_CATALOG_CACHE_TTL", "300"))

# Request logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("agentsearch")


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


cache = Cache(ttl=CACHE_TTL)
content_cache: ContentCache | None = None
evolver: Evolver | None = None
http_client: httpx.AsyncClient | None = None
_health_cache: tuple[float, HealthResponse] | None = None
_engine_catalog_cache: tuple[float, dict[str, "EngineDescriptor"]] | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage async HTTP client and cache lifecycle."""
    global http_client, content_cache, evolver
    created_http_client = http_client is None
    if http_client is None:
        http_client = httpx.AsyncClient(timeout=30.0)
    if content_cache is None:
        content_cache = ContentCache()
    if evolver is None:
        evolver = Evolver()

    # Periodic cache eviction (every hour)
    async def _evict_loop():
        while True:
            await asyncio.sleep(3600)
            try:
                assert content_cache is not None
                count = await content_cache.evict_expired()
                if count > 0:
                    logger.info("Evicted %s expired cache entries", count)
            except Exception as exc:
                logger.debug("Cache eviction failed: %s", exc)

    evict_task = asyncio.create_task(_evict_loop())

    yield

    evict_task.cancel()
    if created_http_client and hasattr(http_client, "aclose"):
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

# Global circuit breaker — hard cap on total requests per minute across ALL IPs
# Prevents runaway automation from overwhelming SearXNG or external sources
_GLOBAL_RATE_LIMIT = int(os.getenv("GLOBAL_RATE_LIMIT", "300"))  # 300 req/min global
_global_timestamps: list[float] = []


# Bearer token auth — set AGENT_SEARCH_TOKEN env var to enable
_AUTH_TOKEN = os.getenv("AGENT_SEARCH_TOKEN", "")


@app.middleware("http")
async def auth_middleware(request, call_next):
    """Bearer token auth. Skips /health for monitoring."""
    if _AUTH_TOKEN and request.url.path != "/health":
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") or not secrets.compare_digest(auth_header[7:], _AUTH_TOKEN):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Per-IP + global rate limiter with memory bounds and request logging."""
    global _global_timestamps
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    path = request.url.path
    query = str(request.query_params) if request.query_params else ""
    logger.info(
        "%s %s %s %s",
        _safe_log_value(client_ip),
        _safe_log_value(request.method),
        _safe_log_value(path),
        _safe_log_value(query),
    )

    # Skip rate limiting for health check
    if path == "/health":
        return await call_next(request)

    # Global circuit breaker — protect SearXNG and external sources
    _global_timestamps = [t for t in _global_timestamps if now - t < 60]
    if len(_global_timestamps) >= _GLOBAL_RATE_LIMIT:
        logger.warning("GLOBAL rate limit hit (%s/min)", _GLOBAL_RATE_LIMIT)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"detail": "Global rate limit exceeded — try again shortly"},
        )
    _global_timestamps.append(now)

    # Per-IP rate limiting
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
        logger.warning("Rate limited: %s", _safe_log_value(client_ip))
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    _rate_store[client_ip].append(now)
    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class SearxngQueryResponse:
    """SearXNG response plus diagnostics that should survive API shaping."""

    results: list[dict]
    upstream_status: str = "ok"
    upstream_errors: list[str] | None = None
    unresponsive_engines: list[str] | None = None
    response_time_ms: float = 0.0

    def __post_init__(self) -> None:
        if self.upstream_errors is None:
            self.upstream_errors = []
        if self.unresponsive_engines is None:
            self.unresponsive_engines = []


@dataclass(frozen=True)
class EngineDescriptor:
    """Enabled SearXNG engine metadata used to validate engine requests."""

    name: str
    shortcut: str
    categories: list[str]


@dataclass(frozen=True)
class EngineSelection:
    """Resolved engine request safe to pass to SearXNG."""

    value: str
    engines: list[EngineDescriptor]


@dataclass(frozen=True)
class SearchStrategyPack:
    """A strategy source: either a SearXNG engine pack or a direct provider."""

    source: str
    engines: tuple[str, ...] = ()
    provider: str | None = None
    categories: str | None = None
    label: str | None = None


def _searxng_pack(*engines: str, categories: str | None = None) -> SearchStrategyPack:
    return SearchStrategyPack(source="searxng", engines=engines, categories=categories)


def _provider_pack(provider: str, label: str | None = None) -> SearchStrategyPack:
    return SearchStrategyPack(source="provider", provider=provider, label=label or provider)


SEARCH_STRATEGY_MODES: dict[str, tuple[SearchStrategyPack, ...]] = {
    "general": (
        _searxng_pack("bing"),
        _searxng_pack("duckduckgo", "brave", "google", "startpage"),
    ),
    "code": (
        _provider_pack("github", "github"),
        _provider_pack("mdn", "mdn"),
        _provider_pack("docker_hub", "docker hub"),
    ),
    "academic": (
        _provider_pack("arxiv", "arxiv"),
        _provider_pack("crossref", "crossref"),
        _provider_pack("openalex", "openalex"),
        _provider_pack("semantic_scholar", "semantic scholar"),
    ),
    "news": (
        _searxng_pack("reuters", "yahoo news", "bing news"),
    ),
    "private": (
        _provider_pack("github", "github"),
        _provider_pack("mdn", "mdn"),
        _provider_pack("docker_hub", "docker hub"),
        _provider_pack("arxiv", "arxiv"),
        _provider_pack("crossref", "crossref"),
        _provider_pack("semantic_scholar", "semantic scholar"),
    ),
}


def _append_unique(values: list[str], value: object) -> None:
    text = _safe_log_value(value, limit=500).strip()
    if text and text not in values:
        values.append(text)


def _format_upstream_error(value: object) -> str:
    if isinstance(value, dict):
        engine = value.get("engine") or value.get("name") or value.get("engine_name")
        error = value.get("error") or value.get("exception") or value.get("message") or value.get("reason")
        if engine and error:
            return f"{engine}: {error}"
        if engine:
            return str(engine)
        if error:
            return str(error)
    if isinstance(value, (list, tuple)):
        parts = [str(part) for part in value if part not in (None, "")]
        return ": ".join(parts)
    return str(value)


def _normalize_upstream_diagnostics(data: dict) -> tuple[list[str], list[str]]:
    unresponsive_engines: list[str] = []
    upstream_errors: list[str] = []

    raw_unresponsive = data.get("unresponsive_engines") or []
    if not isinstance(raw_unresponsive, list):
        raw_unresponsive = [raw_unresponsive]

    for item in raw_unresponsive:
        if isinstance(item, dict):
            engine = item.get("engine") or item.get("name") or item.get("engine_name")
        elif isinstance(item, (list, tuple)) and item:
            engine = item[0]
        else:
            engine = item
        _append_unique(unresponsive_engines, engine)
        _append_unique(upstream_errors, _format_upstream_error(item))

    raw_errors = data.get("errors") or data.get("error") or []
    if isinstance(raw_errors, (str, dict)):
        raw_errors = [raw_errors]
    if isinstance(raw_errors, list):
        for item in raw_errors:
            _append_unique(upstream_errors, _format_upstream_error(item))

    return unresponsive_engines, upstream_errors


def _upstream_metadata(*responses: SearxngQueryResponse) -> dict:
    unresponsive_engines: list[str] = []
    upstream_errors: list[str] = []
    statuses = [response.upstream_status for response in responses]

    for response in responses:
        for engine in response.unresponsive_engines or []:
            _append_unique(unresponsive_engines, engine)
        for error in response.upstream_errors or []:
            _append_unique(upstream_errors, error)

    if statuses and all(status == "error" for status in statuses):
        upstream_status = "error"
    elif any(status in {"degraded", "error"} for status in statuses):
        upstream_status = "degraded"
    else:
        upstream_status = "ok"

    return {
        "upstream_status": upstream_status,
        "upstream_errors": upstream_errors,
        "unresponsive_engines": unresponsive_engines,
    }


def _upstream_error_response(exc: Exception) -> SearxngQueryResponse:
    return SearxngQueryResponse(
        results=[],
        upstream_status="error",
        upstream_errors=[f"SearXNG error: {_safe_log_value(exc)}"],
    )


def _engine_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _split_engine_list(value: str) -> list[str]:
    return [engine.strip() for engine in value.split(",") if engine.strip()]


async def _enabled_engine_catalog() -> dict[str, EngineDescriptor]:
    """Return enabled engines keyed by normalized name and shortcut."""
    global _engine_catalog_cache
    assert http_client is not None
    now = time.time()
    if _engine_catalog_cache and now - _engine_catalog_cache[0] < ENGINE_CATALOG_CACHE_TTL:
        return _engine_catalog_cache[1]

    resp = await http_client.get(f"{SEARXNG_URL}/config", timeout=5.0)
    resp.raise_for_status()
    data = resp.json()

    catalog: dict[str, EngineDescriptor] = {}
    for engine in data.get("engines", []):
        if not engine.get("enabled", False):
            continue
        name = str(engine.get("name", "")).strip()
        if not name:
            continue
        descriptor = EngineDescriptor(
            name=name,
            shortcut=str(engine.get("shortcut", "")).strip(),
            categories=[str(category).lower() for category in engine.get("categories", [])],
        )
        catalog[_engine_key(name)] = descriptor
        if descriptor.shortcut:
            catalog[_engine_key(descriptor.shortcut)] = descriptor

    _engine_catalog_cache = (now, catalog)
    return catalog


async def _resolve_engine_selection(engines: str | None) -> EngineSelection | None:
    """Validate explicit engine requests so SearXNG cannot fall back silently."""
    if not engines:
        return None

    requested = _split_engine_list(engines)
    if not requested:
        raise HTTPException(status_code=400, detail="No valid engine names provided")

    try:
        catalog = await _enabled_engine_catalog()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not validate engine list: {exc}") from exc

    invalid: list[str] = []
    selected: list[EngineDescriptor] = []
    seen: set[str] = set()
    for item in requested:
        descriptor = catalog.get(_engine_key(item))
        if descriptor is None:
            invalid.append(item)
            continue
        if descriptor.name not in seen:
            selected.append(descriptor)
            seen.add(descriptor.name)

    if invalid:
        preview = sorted({descriptor.name for descriptor in catalog.values()})[:25]
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unknown or disabled search engine(s)",
                "invalid_engines": invalid,
                "hint": "Use /engines to inspect enabled engine names and shortcuts",
                "enabled_engine_examples": preview,
            },
        )

    return EngineSelection(
        value=",".join(descriptor.name for descriptor in selected),
        engines=selected,
    )


def _supports_site_search(selection: EngineSelection | None) -> bool:
    if selection is None:
        return True
    site_categories = {"general", "web"}
    return all(site_categories.intersection(engine.categories) for engine in selection.engines)


async def _query_searxng(
    query: str,
    count: int = 10,
    engines: str | None = None,
    categories: str | None = None,
    timeout: float | None = None,
) -> SearxngQueryResponse:
    """Query SearXNG and return raw results."""
    assert http_client is not None
    start = time.monotonic()
    params: dict = {"q": query, "format": "json", "pageno": 1}
    if engines:
        params["engines"] = engines
    if categories:
        params["categories"] = categories
    request_kwargs = {"params": params}
    if timeout is not None:
        request_kwargs["timeout"] = timeout
    resp = await http_client.get(f"{SEARXNG_URL}/search", **request_kwargs)
    resp.raise_for_status()
    data = resp.json()
    unresponsive_engines, upstream_errors = _normalize_upstream_diagnostics(data)
    upstream_status = "degraded" if unresponsive_engines or upstream_errors else "ok"
    return SearxngQueryResponse(
        results=data.get("results", [])[:count * 3],
        upstream_status=upstream_status,
        upstream_errors=upstream_errors,
        unresponsive_engines=unresponsive_engines,
        response_time_ms=round((time.monotonic() - start) * 1000, 1),
    )


def _normalize_domain_filter(value: str) -> str:
    """Return a hostname from a domain filter value."""
    raw = value.strip().lower().strip(".")
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = (parsed.hostname or raw).lower().strip(".")
    return host


def _result_hostname(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower().strip(".")


def _hostname_matches(hostname: str, domain: str) -> bool:
    if not hostname or not domain:
        return False
    return hostname == domain or hostname.endswith(f".{domain}")


def _domain_scoped_query(query: str, domain: str) -> str:
    site_clause = f"site:{domain}"
    if re.search(rf"(?i)(^|\s){re.escape(site_clause)}(\s|$)", query):
        return query
    return f"{site_clause} {query}"


def _normalize_search_mode(mode: str) -> str:
    normalized = mode.strip().lower()
    if normalized not in SEARCH_STRATEGY_MODES:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Unknown search strategy mode",
                "mode": mode,
                "available_modes": sorted(SEARCH_STRATEGY_MODES),
            },
        )
    return normalized


def _filter_search_results(
    results: list[SearchResult],
    domain: str | None = None,
    exclude_domains: str | None = None,
) -> list[SearchResult]:
    if domain:
        include_domain = _normalize_domain_filter(domain)
        results = [r for r in results if _hostname_matches(_result_hostname(r.url), include_domain)]
    if exclude_domains:
        excluded = [_normalize_domain_filter(d) for d in exclude_domains.split(",")]
        excluded = [d for d in excluded if d]
        results = [
            r for r in results
            if not any(_hostname_matches(_result_hostname(r.url), d) for d in excluded)
        ]
    return results


async def _attach_content(results: list[SearchResult]) -> None:
    if not results:
        return
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
        if content_cache:
            await content_cache.log_fetch(
                kc.url, kc.strategy, kc.success, kc.chars,
                kc.strategies_tried, kc.error,
            )


async def _search_strategy_impl(
    q: str,
    count: int,
    mode: str,
    domain: str | None = None,
    exclude_domains: str | None = None,
    fetch: bool = False,
) -> SearchResponse:
    """Run a named strategy without silently falling through to default engines."""
    start = time.time()

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")
    if len(q) > 500:
        raise HTTPException(status_code=400, detail="Query too long (max 500 chars)")

    normalized_mode = _normalize_search_mode(mode)
    cache_domain = domain or ""
    cache_exclude_domains = exclude_domains or ""
    cache_engines = f"mode:{normalized_mode}"
    cached_resp = cache.get(q, cache_engines, count, cache_domain, cache_exclude_domains, fetch)
    if cached_resp is not None:
        cached_resp.meta.cached = True
        cached_resp.meta.response_time_ms = round((time.time() - start) * 1000, 1)
        return cached_resp

    combined_raw: list[dict] = []
    upstream_responses: list[SearxngQueryResponse] = []
    engine_attempts: list[dict] = []
    queries_used: list[str] = []
    fallback_reason: str | None = None

    for index, pack in enumerate(SEARCH_STRATEGY_MODES[normalized_mode]):
        engine_selection = None
        if pack.source == "searxng":
            engine_selection = await _resolve_engine_selection(",".join(pack.engines))
        query_text = q
        if domain and pack.source == "searxng":
            include_domain = _normalize_domain_filter(domain)
            if include_domain and _supports_site_search(engine_selection):
                query_text = _domain_scoped_query(q, include_domain)
        if query_text != q:
            _append_unique(queries_used, query_text)

        provider_name: str | None = None
        if pack.source == "provider":
            if not pack.provider:
                raise HTTPException(status_code=500, detail=f"Strategy pack '{pack.label or pack.source}' has no provider")
            assert http_client is not None
            provider = provider_by_name(pack.provider)
            upstream = await provider.search(http_client, query_text, count * 2)
            provider_name = upstream.provider
        elif pack.source == "searxng":
            if engine_selection is None:
                raise HTTPException(status_code=500, detail=f"Strategy pack '{pack.label or pack.source}' has no engines")
            try:
                upstream = await _query_searxng(
                    query_text,
                    count * 2,
                    engine_selection.value,
                    categories=pack.categories,
                )
            except httpx.HTTPError as exc:
                upstream = _upstream_error_response(exc)
            provider_name = "searxng"
        else:
            raise HTTPException(status_code=500, detail=f"Unknown strategy source: {pack.source}")

        upstream_responses.append(upstream)
        combined_raw.extend(upstream.results)
        current_results = _filter_search_results(
            deduplicate_with_scoring(combined_raw),
            domain=domain,
            exclude_domains=exclude_domains,
        )
        engine_names = (
            [engine.name for engine in engine_selection.engines]
            if engine_selection
            else [pack.label or pack.source]
        )
        engine_attempts.append({
            "mode": normalized_mode,
            "source": pack.source,
            "provider": provider_name,
            "engines": engine_names,
            "engine_query": engine_selection.value if engine_selection else None,
            "categories": pack.categories,
            "query": query_text,
            "raw_results": len(upstream.results),
            "combined_total": len(current_results),
            "response_time_ms": getattr(upstream, "response_time_ms", 0.0),
            "upstream_status": upstream.upstream_status,
            "upstream_errors": upstream.upstream_errors or [],
            "unresponsive_engines": upstream.unresponsive_engines or [],
        })

        if normalized_mode == "general" and len(current_results) >= count:
            break
        if normalized_mode == "general" and index < len(SEARCH_STRATEGY_MODES[normalized_mode]) - 1:
            fallback_reason = "no_results" if not upstream.results else "insufficient_results"

    results = _filter_search_results(
        deduplicate_with_scoring(combined_raw),
        domain=domain,
        exclude_domains=exclude_domains,
    )[:count]

    if fetch:
        await _attach_content(results)

    engines_used = list({e for r in results for e in r.engines})
    elapsed = round((time.time() - start) * 1000, 1)

    await query_db.log_query(f"{q} mode:{normalized_mode}", engines_used, len(results), elapsed)

    response = SearchResponse(
        results=results,
        meta=SearchMeta(
            query=q,
            total=len(results),
            engines_used=engines_used,
            cached=False,
            response_time_ms=elapsed,
            queries_used=queries_used or None,
            mode=normalized_mode,
            engine_attempts=engine_attempts,
            fallback_reason=fallback_reason,
            **_upstream_metadata(*upstream_responses),
        ),
    )

    cache.set(q, cache_engines, count, response, cache_domain, cache_exclude_domains, fetch)
    return response


# =========================================================================
# SEARCH ENDPOINTS
# =========================================================================

@app.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., description="Search query"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
    engines: str | None = Query(None, description="Comma-separated engine names"),
    mode: str | None = Query(None, description="Named strategy: general, code, academic, news, or private"),
    domain: str | None = Query(None, description="Filter results to this domain"),
    exclude_domains: str | None = Query(None, description="Comma-separated domains to exclude"),
    fetch: bool = Query(False, description="Fetch page content via kill chain"),
) -> SearchResponse:
    """Search the web and return deduplicated, scored results."""
    start = time.time()

    if mode:
        if engines:
            raise HTTPException(status_code=400, detail="Use either 'mode' or 'engines', not both")
        return await _search_strategy_impl(
            q=q,
            count=count,
            mode=mode,
            domain=domain,
            exclude_domains=exclude_domains,
            fetch=fetch,
        )

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")
    if len(q) > 500:
        raise HTTPException(status_code=400, detail="Query too long (max 500 chars)")

    engine_selection = await _resolve_engine_selection(engines)
    resolved_engines = engine_selection.value if engine_selection else ""
    cache_domain = domain or ""
    cache_exclude_domains = exclude_domains or ""
    cached_resp = cache.get(q, resolved_engines, count, cache_domain, cache_exclude_domains, fetch)
    if cached_resp is not None:
        cached_resp.meta.cached = True
        cached_resp.meta.response_time_ms = round((time.time() - start) * 1000, 1)
        return cached_resp

    query_text = q
    if domain:
        include_domain = _normalize_domain_filter(domain)
        if include_domain and _supports_site_search(engine_selection):
            query_text = _domain_scoped_query(q, include_domain)

    try:
        upstream = await _query_searxng(query_text, count * 2, resolved_engines or None)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"SearXNG error: {e}")

    results = deduplicate_with_scoring(upstream.results)
    results = _filter_search_results(results, domain=domain, exclude_domains=exclude_domains)[:count]

    # Fetch content via kill chain if requested
    if fetch and results:
        await _attach_content(results)

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
            queries_used=[query_text] if query_text != q else None,
            **_upstream_metadata(upstream),
        ),
    )

    cache.set(q, resolved_engines, count, response, cache_domain, cache_exclude_domains, fetch)
    return response


@app.get("/search/strategy", response_model=SearchResponse)
async def search_strategy(
    q: str = Query(..., description="Search query"),
    mode: str = Query("general", description="Named strategy: general, code, academic, news, or private"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
    domain: str | None = Query(None, description="Filter results to this domain"),
    exclude_domains: str | None = Query(None, description="Comma-separated domains to exclude"),
    fetch: bool = Query(False, description="Fetch page content via kill chain"),
) -> SearchResponse:
    """Search using a named strategy with visible engine-pack attempts."""
    return await _search_strategy_impl(
        q=q,
        count=count,
        mode=mode,
        domain=domain,
        exclude_domains=exclude_domains,
        fetch=fetch,
    )


@app.get("/search/extract", response_model=SearchResponse)
async def search_extract(
    q: str = Query(..., description="Search query"),
    count: int = Query(5, ge=1, le=20, description="Number of results"),
    engines: str | None = Query(None, description="Comma-separated engine names"),
    mode: str | None = Query(None, description="Named strategy: general, code, academic, news, or private"),
) -> SearchResponse:
    """Search and extract content from top results via kill chain."""
    return await search(q=q, count=count, engines=engines, mode=mode, domain=None, exclude_domains=None, fetch=True)


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

    async def _search_one(query: str) -> SearxngQueryResponse:
        try:
            return await _query_searxng(query, count)
        except httpx.HTTPError as exc:
            return _upstream_error_response(exc)

    all_raw = await asyncio.gather(*[_search_one(v) for v in query_variations])

    combined = []
    for upstream in all_raw:
        combined.extend(upstream.results)

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
            **_upstream_metadata(*all_raw),
        ),
    )


@app.get("/search/policy", response_model=SearchResponse)
async def search_policy(
    q: str = Query(..., description="Policy/geopolitical search query"),
    count: int = Query(10, ge=1, le=50, description="Number of results"),
    fetch: bool = Query(False, description="Fetch page content via kill chain"),
) -> SearchResponse:
    """Policy-optimized search: domain quality ranking, junk filtering, source library.

    Enhances standard search with:
    1. Source library lookup — returns curated URLs for known topics
    2. Query enhancement — appends analytical terms to avoid homepages
    3. Domain quality re-ranking — boosts think tanks, penalizes junk
    4. Junk filtering — removes dictionaries, tourism, gaming results
    """
    from app.source_library import get_sources, rank_results, filter_junk

    start = time.time()

    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Query parameter 'q' cannot be empty")

    # Step 1: Check source library for curated URLs
    library_sources = get_sources(q)
    library_results = []
    if library_sources:
        for src in library_sources[:5]:  # Top 5 curated sources
            library_results.append(SearchResult(
                title=f"[Library] {src.get('institution', '')}: {src.get('title', '')}",
                url=src["url"],
                snippet=f"Curated source from {src.get('institution', '')}. {src.get('date', '')}",
                engines=["source_library"],
                score=1.0,
                position=0,
                content=None,
            ))

    # Step 2: Enhanced search queries
    enhanced_queries = [q]
    q_lower = q.lower()
    # Add analytical terms if not already present
    if not any(term in q_lower for term in ["analysis", "assessment", "report", "implications", "strategy"]):
        enhanced_queries.append(f"{q} analysis assessment")
    # Add think tank search
    enhanced_queries.append(f"{q} CSIS RAND Brookings CFR")

    # Step 3: Run searches
    async def _search_one(query: str) -> SearxngQueryResponse:
        try:
            return await _query_searxng(query, count * 2)
        except httpx.HTTPError as exc:
            return _upstream_error_response(exc)

    all_raw = await asyncio.gather(*[_search_one(v) for v in enhanced_queries])

    combined = []
    for upstream in all_raw:
        combined.extend(upstream.results)

    results = deduplicate_with_scoring(combined)

    # Step 4: Filter junk results
    results_dicts = [{"url": r.url, "title": r.title, "score": r.score, "snippet": r.snippet, "engines": r.engines} for r in results]
    results_dicts = filter_junk(results_dicts)

    # Step 5: Re-rank by domain quality
    results_dicts = rank_results(results_dicts)

    # Convert back to SearchResult objects
    search_results = []
    for rd in results_dicts[:count]:
        search_results.append(SearchResult(
            title=rd.get("title", ""),
            url=rd.get("url", ""),
            snippet=rd.get("snippet", ""),
            engines=rd.get("engines", []),
            score=rd.get("policy_score", rd.get("score", 0.5)),
            position=0,
            content=None,
        ))

    # Prepend library results (they always come first)
    all_results = library_results + search_results
    all_results = all_results[:count]

    # Fetch content if requested
    if fetch and all_results:
        assert http_client is not None
        kc_results = await kill_chain_batch(
            http_client,
            [r.url for r in all_results],
            searxng_url=SEARXNG_URL,
            content_cache=content_cache,
            max_concurrent=5,
        )
        for result, kc in zip(all_results, kc_results):
            result.content = kc.content
            if content_cache:
                await content_cache.log_fetch(
                    kc.url, kc.strategy, kc.success, kc.chars,
                    kc.strategies_tried, kc.error,
                )

    engines_used = list({e for r in all_results for e in r.engines})
    elapsed = round((time.time() - start) * 1000, 1)

    await query_db.log_query(q, engines_used, len(all_results), elapsed)

    return SearchResponse(
        results=all_results,
        meta=SearchMeta(
            query=q,
            total=len(all_results),
            engines_used=engines_used,
            cached=False,
            response_time_ms=elapsed,
            queries_used=enhanced_queries,
            **_upstream_metadata(*all_raw),
        ),
    )


# =========================================================================
# SOURCE TRACER ENDPOINTS
# =========================================================================

@app.get("/search/sources")
async def search_sources(
    q: str = Query(..., description="Topic to trace primary sources for"),
    count: int = Query(15, ge=1, le=30, description="Max results"),
    fetch: bool = Query(False, description="Also extract content via kill chain"),
) -> dict:
    """Trace primary sources for a topic.

    Instead of fighting paywalls, go upstream. Find the think tanks,
    government agencies, and research institutions that produce the
    data that journalists wrap narratives around.

    Returns ranked sources from ~50 curated institutions.
    """
    start = time.time()

    result = await trace_sources(
        query=q,
        searxng_url=SEARXNG_URL,
        max_results=count,
    )

    # Optionally fetch content from sources
    if fetch and result["sources"]:
        assert http_client is not None
        kc_results = await kill_chain_batch(
            http_client,
            [s["url"] for s in result["sources"]],
            searxng_url=SEARXNG_URL,
            content_cache=content_cache,
            max_concurrent=5,
        )
        for source, kc in zip(result["sources"], kc_results):
            source["content"] = kc.content
            source["content_chars"] = kc.chars
            source["content_strategy"] = kc.strategy
            if content_cache:
                await content_cache.log_fetch(
                    kc.url, kc.strategy, kc.success, kc.chars,
                    kc.strategies_tried, kc.error,
                )

    result["response_time_ms"] = round((time.time() - start) * 1000, 1)
    return result


@app.get("/search/sources/institutions")
async def list_institutions(
    topic: str | None = Query(None, description="Filter by topic tag"),
) -> dict:
    """List all institutions in the source registry."""
    registry = get_institution_registry()
    if topic:
        registry = [i for i in registry if topic.lower() in i["topics"]]
    return {
        "total": len(registry),
        "institutions": registry,
    }


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

    trust_info = None
    if result.trust:
        trust_info = TrustInfo(
            domain=result.trust.domain, tier=result.trust.tier,
            score=result.trust.score, reasons=result.trust.reasons,
            https=result.trust.https, lookalike_of=result.trust.lookalike_of,
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
        trust=trust_info,
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
            trust=TrustInfo(
                domain=r.trust.domain, tier=r.trust.tier,
                score=r.trust.score, reasons=r.trust.reasons,
                https=r.trust.https, lookalike_of=r.trust.lookalike_of,
            ) if r.trust else None,
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

    engine_selection = await _resolve_engine_selection(engines) if engines else None
    news_engines = engine_selection.value if engine_selection else NEWS_ENGINES

    try:
        upstream = await _query_searxng(q, count * 3, engines=news_engines, categories="news")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"SearXNG error: {e}")

    # Deduplicate and build news results
    seen_urls: set[str] = set()
    results: list[NewsResult] = []

    for r in upstream.results:
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
            **_upstream_metadata(upstream),
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
    upstream_responses: list[SearxngQueryResponse] = []

    for site in job_sites:
        query = f"{q}{location_str} {site}"
        try:
            upstream = await _query_searxng(query, count=10)
        except httpx.HTTPError as exc:
            upstream = _upstream_error_response(exc)
        upstream_responses.append(upstream)
        all_raw.extend(upstream.results)

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
    engines_used = []
    for r in all_raw:
        raw_engines = r.get("engines", [])
        if isinstance(raw_engines, str):
            raw_engines = [raw_engines]
        for engine in raw_engines:
            _append_unique(engines_used, engine)

    return JobSearchResponse(
        results=results,
        meta=SearchMeta(
            query=q,
            total=len(results),
            engines_used=engines_used,
            response_time_ms=elapsed,
            **_upstream_metadata(*upstream_responses),
        ),
    )


# =========================================================================
# SYSTEM ENDPOINTS
# =========================================================================

@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — verifies SearXNG and live search connectivity."""
    global _health_cache
    assert http_client is not None
    now = time.time()
    if _health_cache and now - _health_cache[0] < HEALTHCHECK_CACHE_TTL:
        return _health_cache[1]

    searxng_ok = False
    search_available = False
    upstream = SearxngQueryResponse(results=[], upstream_status="unknown")

    try:
        resp = await http_client.get(f"{SEARXNG_URL}/healthz", timeout=5.0)
        searxng_ok = resp.status_code == 200
    except Exception as exc:
        upstream = _upstream_error_response(exc)

    if searxng_ok:
        try:
            engine_selection = await _resolve_engine_selection(HEALTHCHECK_ENGINES)
            upstream = await _query_searxng(
                HEALTHCHECK_QUERY,
                count=1,
                engines=engine_selection.value if engine_selection else None,
                timeout=HEALTHCHECK_SEARCH_TIMEOUT,
            )
            search_available = bool(upstream.results)
            if not search_available and upstream.upstream_status == "ok":
                upstream.upstream_status = "degraded"
                upstream.upstream_errors.append("Health search returned no results")
        except Exception as exc:
            upstream = _upstream_error_response(exc)

    upstream_meta = _upstream_metadata(upstream)
    status = "healthy" if searxng_ok and search_available and upstream_meta["upstream_status"] == "ok" else "degraded"

    response = HealthResponse(
        status=status,
        searxng_available=searxng_ok,
        search_available=search_available,
        version=VERSION,
        **upstream_meta,
    )
    _health_cache = (now, response)
    return response


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
                categories=e.get("categories", []),
            )
            for e in data.get("engines", [])
        ]
    except Exception:
        raise HTTPException(status_code=502, detail="Could not fetch engine list from SearXNG")
