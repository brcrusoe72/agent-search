"""AgentSearch Python client."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import (
    BatchReadResponse,
    BrowserFetchResponse,
    BrowserLink,
    HealthResponse,
    JobResult,
    JobSearchResponse,
    NewsResponse,
    NewsResult,
    ReadResult,
    SearchMeta,
    SearchResponse,
    SearchResult,
)


class AgentSearchError(Exception):
    """Base exception for AgentSearch client errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class AgentSearch:
    """Python client for the AgentSearch API.

    Args:
        base_url: Base URL of the AgentSearch instance (e.g. ``"http://localhost:3939"``).
        timeout: Request timeout in seconds.

    Example::

        from agentsearch import AgentSearch

        client = AgentSearch("http://localhost:3939")
        results = client.search("python async patterns", count=5)
        for r in results.results:
            print(r.title, r.url)
    """

    def __init__(self, base_url: str = "http://localhost:3939", timeout: float = 30.0, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = token or self._load_token()
        self._session: Any = None

        # Try to use httpx for connection pooling
        try:
            import httpx  # type: ignore

            self._session = httpx.Client(base_url=self.base_url, timeout=timeout, headers=self._headers())
            self._backend = "httpx"
        except ImportError:
            self._backend = "urllib"

    def _load_token(self) -> str | None:
        token = (os.environ.get("AGENT_SEARCH_TOKEN") or os.environ.get("AGENTSEARCH_TOKEN") or "").strip()
        if token:
            return token
        for path in [
            Path.cwd() / "credentials/agent-search-token.txt",
            Path.home() / ".openclaw/workspace/credentials/agent-search-token.txt",
            Path.home() / ".config/agent-search/token",
        ]:
            try:
                if path.exists():
                    token = path.read_text().strip()
                    if token:
                        return token
            except Exception:
                continue
        return None

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> "AgentSearch":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal HTTP
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Make a GET request and return parsed JSON."""
        if self._backend == "httpx":
            resp = self._session.get(path, params=params)
            if resp.status_code >= 400:
                detail = None
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                raise AgentSearchError(
                    f"HTTP {resp.status_code}: {detail or resp.text[:200]}",
                    status_code=resp.status_code,
                    detail=detail,
                )
            return resp.json()

        # urllib fallback
        url = self.base_url + path
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urlencode(filtered, doseq=True)
        req = Request(url, headers=self._headers())
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            detail = None
            try:
                body = json.loads(e.read())
                detail = body.get("detail")
            except Exception:
                detail = None
            raise AgentSearchError(
                f"HTTP {e.code}: {detail or str(e)}",
                status_code=e.code,
                detail=detail,
            ) from e
        except URLError as e:
            raise AgentSearchError(f"Connection error: {e}") from e

    def _post(self, path: str, body: Any) -> Any:
        """Make a POST request with JSON body and return parsed JSON."""
        if self._backend == "httpx":
            resp = self._session.post(path, json=body)
            if resp.status_code >= 400:
                detail = None
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                raise AgentSearchError(
                    f"HTTP {resp.status_code}: {detail or resp.text[:200]}",
                    status_code=resp.status_code,
                    detail=detail,
                )
            return resp.json()

        # urllib fallback
        url = self.base_url + path
        data = json.dumps(body).encode()
        req = Request(url, data=data, headers={
            **self._headers(),
            "Content-Type": "application/json",
        })
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            detail = None
            try:
                body_resp = json.loads(e.read())
                detail = body_resp.get("detail")
            except Exception:
                detail = None
            raise AgentSearchError(
                f"HTTP {e.code}: {detail or str(e)}",
                status_code=e.code,
                detail=detail,
            ) from e
        except URLError as e:
            raise AgentSearchError(f"Connection error: {e}") from e

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_meta(data: Dict[str, Any]) -> SearchMeta:
        m = data.get("meta", {})
        return SearchMeta(
            query=m.get("query", ""),
            total=m.get("total", 0),
            engines_used=m.get("engines_used", []),
            cached=m.get("cached", False),
            response_time_ms=m.get("response_time_ms", 0.0),
            queries_used=m.get("queries_used"),
            mode=m.get("mode"),
            engine_attempts=m.get("engine_attempts", []),
            fallback_reason=m.get("fallback_reason"),
            upstream_status=m.get("upstream_status", "ok"),
            upstream_errors=m.get("upstream_errors", []),
            unresponsive_engines=m.get("unresponsive_engines", []),
        )

    @staticmethod
    def _parse_search_result(d: Dict[str, Any]) -> SearchResult:
        return SearchResult(
            title=d.get("title", ""),
            url=d.get("url", ""),
            snippet=d.get("snippet", ""),
            engines=d.get("engines", []),
            score=d.get("score", 0.0),
            position=d.get("position", 0),
            content=d.get("content"),
        )

    @staticmethod
    def _parse_read_result(d: Dict[str, Any]) -> ReadResult:
        return ReadResult(
            url=d.get("url", ""),
            content=d.get("content"),
            strategy=d.get("strategy"),
            chars=d.get("chars", 0),
            cached=d.get("cached", False),
            strategies_tried=d.get("strategies_tried", []),
            error=d.get("error"),
            success=d.get("success", False),
        )

    @staticmethod
    def _parse_browser_fetch_response(d: Dict[str, Any]) -> BrowserFetchResponse:
        return BrowserFetchResponse(
            url=d.get("url", ""),
            final_url=d.get("final_url", ""),
            title=d.get("title", ""),
            content=d.get("content"),
            chars=d.get("chars", 0),
            links=[
                BrowserLink(text=item.get("text", ""), url=item.get("url", ""))
                for item in d.get("links", [])
                if isinstance(item, dict)
            ],
            success=d.get("success", False),
            strategy=d.get("strategy", "browser-render"),
            error=d.get("error"),
            challenge_detected=d.get("challenge_detected", False),
            blocked_reason=d.get("blocked_reason"),
            render_time_ms=d.get("render_time_ms", 0.0),
        )

    @staticmethod
    def _parse_news_result(d: Dict[str, Any]) -> NewsResult:
        return NewsResult(
            title=d.get("title", ""),
            url=d.get("url", ""),
            snippet=d.get("snippet", ""),
            source=d.get("source"),
            published=d.get("published"),
            engines=d.get("engines", []),
            score=d.get("score", 0.0),
        )

    @staticmethod
    def _parse_job_result(d: Dict[str, Any]) -> JobResult:
        return JobResult(
            title=d.get("title", ""),
            url=d.get("url", ""),
            snippet=d.get("snippet", ""),
            company=d.get("company"),
            location=d.get("location"),
            salary_min=d.get("salary_min"),
            salary_max=d.get("salary_max"),
            source=d.get("source"),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        count: int = 10,
        engines: Optional[str] = None,
        mode: Optional[str] = None,
        domain: Optional[str] = None,
        exclude_domains: Optional[str] = None,
        fetch: bool = False,
    ) -> SearchResponse:
        """Search the web.

        Args:
            query: Search query string.
            count: Number of results (1-50).
            engines: Comma-separated engine names.
            mode: Named search strategy: general, code, academic, news, private, reference, or community.
            domain: Filter results to this domain.
            exclude_domains: Comma-separated domains to exclude.
            fetch: If True, also extract page content via kill chain.

        Returns:
            SearchResponse with results and metadata.
        """
        params: Dict[str, Any] = {"q": query, "count": count}
        if engines:
            params["engines"] = engines
        if mode:
            params["mode"] = mode
        if domain:
            params["domain"] = domain
        if exclude_domains:
            params["exclude_domains"] = exclude_domains
        if fetch:
            params["fetch"] = "true"

        data = self._get("/search", params)
        return SearchResponse(
            results=[self._parse_search_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def search_strategy(
        self,
        query: str,
        *,
        mode: str = "general",
        count: int = 10,
        domain: Optional[str] = None,
        exclude_domains: Optional[str] = None,
        fetch: bool = False,
    ) -> SearchResponse:
        """Search with a named engine strategy."""
        params: Dict[str, Any] = {"q": query, "mode": mode, "count": count}
        if domain:
            params["domain"] = domain
        if exclude_domains:
            params["exclude_domains"] = exclude_domains
        if fetch:
            params["fetch"] = "true"

        data = self._get("/search/strategy", params)
        return SearchResponse(
            results=[self._parse_search_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def search_extract(
        self,
        query: str,
        *,
        count: int = 5,
        engines: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> SearchResponse:
        """Search and extract content from top results.

        Args:
            query: Search query string.
            count: Number of results (1-20).
            engines: Comma-separated engine names.
            mode: Named search strategy: general, code, academic, news, private, reference, or community.

        Returns:
            SearchResponse with results including extracted content.
        """
        params: Dict[str, Any] = {"q": query, "count": count}
        if engines:
            params["engines"] = engines
        if mode:
            params["mode"] = mode

        data = self._get("/search/extract", params)
        return SearchResponse(
            results=[self._parse_search_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def deep_search(
        self,
        query: str,
        *,
        count: int = 10,
    ) -> SearchResponse:
        """Multi-query fusion search with intelligent query variations.

        Args:
            query: Search query string.
            count: Number of results (1-50).

        Returns:
            SearchResponse with fused, deduplicated results.
        """
        data = self._get("/search/deep", {"q": query, "count": count})
        return SearchResponse(
            results=[self._parse_search_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def search_policy(
        self,
        query: str,
        *,
        count: int = 10,
        fetch: bool = False,
    ) -> SearchResponse:
        """Policy-optimized search with domain quality ranking.

        Args:
            query: Policy/geopolitical search query.
            count: Number of results (1-50).
            fetch: If True, extract page content.

        Returns:
            SearchResponse with policy-ranked results.
        """
        params: Dict[str, Any] = {"q": query, "count": count}
        if fetch:
            params["fetch"] = "true"

        data = self._get("/search/policy", params)
        return SearchResponse(
            results=[self._parse_search_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def read(
        self,
        url: str,
        *,
        max_chars: Optional[int] = None,
        skip_cache: bool = False,
    ) -> ReadResult:
        """Extract readable content from a URL using the kill chain.

        Tries escalating strategies: direct fetch, readability, UA rotation,
        browser rendering, Wayback Machine, Google Cache, search-about,
        custom adapters, PDF extraction, and YouTube transcripts.

        Args:
            url: URL to extract content from.
            max_chars: Maximum content length.
            skip_cache: Bypass content cache.

        Returns:
            ReadResult with extracted content and metadata.
        """
        params: Dict[str, Any] = {"url": url}
        if max_chars is not None:
            params["max_chars"] = max_chars
        if skip_cache:
            params["skip_cache"] = "true"

        data = self._get("/read", params)
        return self._parse_read_result(data)

    def read_batch(
        self,
        urls: List[str],
        *,
        max_chars: Optional[int] = None,
    ) -> BatchReadResponse:
        """Extract content from multiple URLs concurrently.

        Args:
            urls: List of URLs to extract (max 20).
            max_chars: Maximum chars per result.

        Returns:
            BatchReadResponse with individual results and summary counts.
        """
        body: Dict[str, Any] = {"urls": urls}
        if max_chars is not None:
            body["max_chars"] = max_chars

        data = self._post("/read/batch", body)
        return BatchReadResponse(
            results=[self._parse_read_result(r) for r in data.get("results", [])],
            total=data.get("total", 0),
            successful=data.get("successful", 0),
            failed=data.get("failed", 0),
        )

    def browser_fetch(
        self,
        url: str,
        *,
        max_chars: Optional[int] = None,
        max_links: Optional[int] = None,
        timeout_ms: Optional[int] = None,
    ) -> BrowserFetchResponse:
        """Render a URL in an ephemeral browser context and extract text/links.

        This endpoint is for JS-rendered target pages. It reports CAPTCHA or
        challenge pages as blocked instead of trying to bypass them.
        """
        params: Dict[str, Any] = {"url": url}
        if max_chars is not None:
            params["max_chars"] = max_chars
        if max_links is not None:
            params["max_links"] = max_links
        if timeout_ms is not None:
            params["timeout_ms"] = timeout_ms

        data = self._get("/providers/browser/fetch", params)
        return self._parse_browser_fetch_response(data)

    def news(
        self,
        query: str,
        *,
        count: int = 10,
        engines: Optional[str] = None,
    ) -> NewsResponse:
        """Search news across multiple news engines.

        Args:
            query: News search query.
            count: Number of results (1-50).
            engines: Override default news engines.

        Returns:
            NewsResponse with news results and metadata.
        """
        params: Dict[str, Any] = {"q": query, "count": count}
        if engines:
            params["engines"] = engines

        data = self._get("/news", params)
        return NewsResponse(
            results=[self._parse_news_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def search_jobs(
        self,
        query: str,
        *,
        location: Optional[str] = None,
        salary_min: Optional[int] = None,
    ) -> JobSearchResponse:
        """Search for jobs across multiple job boards.

        Args:
            query: Job title / keywords.
            location: Location filter.
            salary_min: Minimum salary filter.

        Returns:
            JobSearchResponse with job results.
        """
        params: Dict[str, Any] = {"q": query}
        if location:
            params["location"] = location
        if salary_min is not None:
            params["salary_min"] = salary_min

        data = self._get("/search/jobs", params)
        return JobSearchResponse(
            results=[self._parse_job_result(r) for r in data.get("results", [])],
            meta=self._parse_meta(data),
        )

    def health(self) -> HealthResponse:
        """Check API health status.

        Returns:
            HealthResponse with status and version info.
        """
        data = self._get("/health")
        return HealthResponse(
            status=data.get("status", ""),
            searxng_available=data.get("searxng_available", False),
            search_available=data.get("search_available", False),
            upstream_status=data.get("upstream_status", "unknown"),
            upstream_errors=data.get("upstream_errors", []),
            unresponsive_engines=data.get("unresponsive_engines", []),
            version=data.get("version", ""),
        )

    def providers_stats(self) -> Dict[str, Any]:
        """Return rolling provider and SearXNG attempt telemetry."""
        return self._get("/providers/stats")

    def providers_health(self) -> Dict[str, Any]:
        """Return provider health summarized from recorded live attempts."""
        return self._get("/providers/health")
