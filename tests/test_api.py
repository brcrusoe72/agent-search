"""AgentSearch API tests.

Default tests are self-contained and do not require Docker, SearXNG, network
access, or a running localhost service. Live integration tests can be enabled
with:

    AGENTSEARCH_INTEGRATION=1 pytest tests -v
"""
from __future__ import annotations

import asyncio
import os
import socket

import httpx
import pytest

from app import killchain
from app import main
from app.cache import Cache
from app.database import QueryDatabase
from adapters import medium as medium_adapter
from adapters.safe_fetch import safe_requests_get


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("fake upstream error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


class FakeSearxngClient:
    async def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        if url.endswith("/healthz"):
            return FakeResponse({})
        if url.endswith("/config"):
            return FakeResponse({
                "engines": [
                    {"name": "duckduckgo", "shortcut": "ddg", "enabled": True, "categories": ["general"]},
                    {"name": "brave", "shortcut": "br", "enabled": True, "categories": ["general"]},
                ]
            })
        if url.endswith("/search"):
            q = (params or {}).get("q", "query")
            return FakeResponse({
                "results": [
                    {
                        "title": f"{q} result one",
                        "url": "https://example.com/one",
                        "content": "First result snippet",
                        "engines": ["duckduckgo"],
                    },
                    {
                        "title": f"{q} result two",
                        "url": "https://example.org/two",
                        "content": "Second result snippet",
                        "engines": ["brave"],
                    },
                ]
            })
        return FakeResponse({})


class DomainFakeSearxngClient:
    async def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        if url.endswith("/search"):
            return FakeResponse({
                "results": [
                    {
                        "title": "root domain",
                        "url": "https://example.com/one",
                        "content": "Root domain snippet",
                        "engines": ["duckduckgo"],
                    },
                    {
                        "title": "subdomain",
                        "url": "https://docs.example.com/two",
                        "content": "Subdomain snippet",
                        "engines": ["brave"],
                    },
                    {
                        "title": "lookalike domain",
                        "url": "https://notexample.com/three",
                        "content": "Lookalike snippet",
                        "engines": ["brave"],
                    },
                    {
                        "title": "path mention",
                        "url": "https://other.test/articles/example.com",
                        "content": "Path mention snippet",
                        "engines": ["duckduckgo"],
                    },
                ]
            })
        return FakeResponse({})


class AppClient:
    """Small sync wrapper around ASGITransport for self-contained API tests."""

    def get(self, path: str, **kwargs) -> httpx.Response:
        return asyncio.run(self._request("GET", path, **kwargs))

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        transport = httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as async_client:
            return await async_client.request(method, path, **kwargs)


@pytest.fixture(autouse=True)
def isolated_app_state(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(main, "_AUTH_TOKEN", "")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(main, "http_client", FakeSearxngClient())
    monkeypatch.setattr(main, "content_cache", None)
    monkeypatch.setattr(main, "evolver", None)
    monkeypatch.setattr(main, "cache", Cache(ttl=3600))
    monkeypatch.setattr(main, "query_db", QueryDatabase(str(tmp_path / "query_log.db")))
    main._rate_store.clear()
    main._global_timestamps.clear()
    yield


@pytest.fixture
def client() -> AppClient:
    return AppClient()


def test_lifespan_preserves_injected_http_client() -> None:
    injected_client = main.http_client

    async def run_lifespan() -> None:
        async with main.lifespan(main.app):
            assert main.http_client is injected_client

    asyncio.run(run_lifespan())


def test_health_endpoint(client: AppClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["searxng_available"] is True
    assert data["version"] == "2.0.0"


def test_engines_endpoint(client: AppClient) -> None:
    response = client.get("/engines")
    assert response.status_code == 200
    engines = response.json()
    assert engines[0]["name"] == "duckduckgo"
    assert engines[0]["enabled"] is True


def test_search_returns_deduplicated_results(client: AppClient) -> None:
    response = client.get("/search", params={"q": "manufacturing OEE", "count": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["meta"]["query"] == "manufacturing OEE"
    assert data["meta"]["total"] == 2
    assert len(data["results"]) == 2
    assert data["results"][0]["url"].startswith("https://")
    assert "duckduckgo" in data["meta"]["engines_used"]


def test_search_cache_distinguishes_filters_and_fetch() -> None:
    local_cache = Cache(ttl=3600)
    local_cache.set("same query", "", 10, {"results": ["unfiltered"]})

    assert local_cache.get("same query", "", 10) == {"results": ["unfiltered"]}
    assert local_cache.get("same query", "", 10, domain="example.com") is None
    assert local_cache.get("same query", "", 10, exclude_domains="example.com") is None
    assert local_cache.get("same query", "", 10, fetch=True) is None


def test_search_endpoint_cache_distinguishes_domain_filter(client: AppClient) -> None:
    unfiltered = client.get("/search", params={"q": "cache domain", "count": 2})
    assert unfiltered.status_code == 200
    assert unfiltered.json()["meta"]["total"] == 2

    filtered = client.get("/search", params={"q": "cache domain", "count": 2, "domain": "example.org"})
    assert filtered.status_code == 200
    data = filtered.json()
    assert data["meta"]["cached"] is False
    assert data["meta"]["total"] == 1
    assert data["results"][0]["url"] == "https://example.org/two"


def test_search_domain_filter_matches_hostname_only(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", DomainFakeSearxngClient())

    response = client.get("/search", params={"q": "domain filter", "count": 10, "domain": "example.com"})

    assert response.status_code == 200
    urls = [r["url"] for r in response.json()["results"]]
    assert urls == ["https://example.com/one", "https://docs.example.com/two"]


def test_search_exclude_domains_matches_hostname_only(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", DomainFakeSearxngClient())

    response = client.get("/search", params={"q": "domain exclude", "count": 10, "exclude_domains": "example.com"})

    assert response.status_code == 200
    urls = [r["url"] for r in response.json()["results"]]
    assert urls == ["https://notexample.com/three", "https://other.test/articles/example.com"]


def test_query_database_async_methods_use_bounded_sqlite(tmp_path) -> None:
    db = QueryDatabase(str(tmp_path / "query_log.db"))

    async def run_queries() -> dict:
        await db.log_query("bounded sqlite", ["duckduckgo", "brave"], 2, 12.5)
        return await db.get_stats()

    stats = asyncio.run(run_queries())

    assert stats["total_queries"] == 1
    assert stats["queries_per_engine"] == {"brave": 1, "duckduckgo": 1}
    assert stats["avg_results_per_engine"] == {"brave": 2.0, "duckduckgo": 2.0}


def test_empty_query_returns_400(client: AppClient) -> None:
    response = client.get("/search", params={"q": ""})
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]


def test_bearer_auth_when_enabled(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "_AUTH_TOKEN", "secret")

    unauthorized = client.get("/search", params={"q": "python"})
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/search",
        params={"q": "python"},
        headers={"Authorization": "Bearer secret"},
    )
    assert authorized.status_code == 200


def test_plain_http_url_safety_check_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr(killchain.socket, "getaddrinfo", fake_getaddrinfo)

    assert killchain.is_safe_url("http://example.com/page", verbose=True) is True


def test_content_type_helpers_match_hostnames_not_substrings() -> None:
    assert medium_adapter.can_handle("https://medium.com/@user/story")
    assert medium_adapter.can_handle("https://team.medium.com/story")
    assert medium_adapter.can_handle("https://towardsdatascience.com/story")
    assert not medium_adapter.can_handle("https://notmedium.com/story")
    assert not medium_adapter.can_handle("https://medium.com.evil.example/story")

    assert killchain._is_medium("https://medium.com/@user/story")
    assert killchain._is_medium("https://team.medium.com/story")
    assert not killchain._is_medium("https://notmedium.com/story")
    assert not killchain._is_medium("https://medium.com.evil.example/story")

    assert killchain._is_youtube("https://www.youtube.com/watch?v=1")
    assert killchain._is_youtube("https://youtu.be/video")
    assert not killchain._is_youtube("https://notyoutube.com/watch")
    assert not killchain._is_youtube("https://youtube.com.evil.example/watch")


def test_direct_strategy_blocks_unsafe_redirect_before_following(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []

    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    async def run_strategy() -> str | None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as async_client:
            return await killchain.strategy_direct(async_client, "https://safe.example/start")

    monkeypatch.setattr(killchain.socket, "getaddrinfo", fake_getaddrinfo)

    assert asyncio.run(run_strategy()) is None
    assert requested_urls == ["https://safe.example/start"]


def test_adapter_safe_requests_get_blocks_unsafe_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []

    class RedirectResponse:
        status_code = 302
        headers = {"Location": "http://127.0.0.1/private"}

        def close(self) -> None:
            pass

    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    def fake_get(url: str, **kwargs) -> RedirectResponse:
        requested_urls.append(url)
        return RedirectResponse()

    monkeypatch.setattr(killchain.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("adapters.safe_fetch.requests.get", fake_get)

    with pytest.raises(killchain.UnsafeRedirectError):
        safe_requests_get("https://safe.example/start", timeout=15)

    assert requested_urls == ["https://safe.example/start"]


def test_wayback_cdx_uses_encoded_params(monkeypatch: pytest.MonkeyPatch) -> None:
    target_url = "https://target.example/article?a=1&b=2#section"
    seen_cdx_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_cdx_params
        if request.url.path == "/cdx/search/cdx":
            seen_cdx_params = dict(request.url.params)
            return httpx.Response(
                200,
                json=[
                    ["urlkey", "timestamp"],
                    ["target.example/article", "20200101000000"],
                ],
            )
        return httpx.Response(
            200,
            text="<html><body><main><p>" + ("Useful archived text. " * 40) + "</p></main></body></html>",
        )

    async def run_strategy() -> str | None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as async_client:
            return await killchain.strategy_wayback(async_client, target_url)

    monkeypatch.setattr(killchain, "is_safe_url", lambda url, verbose=False: True)

    result = asyncio.run(run_strategy())

    assert result is not None
    assert seen_cdx_params["url"] == target_url
    assert seen_cdx_params["output"] == "json"
    assert "b" not in seen_cdx_params


@pytest.mark.skipif(os.getenv("AGENTSEARCH_INTEGRATION") != "1", reason="Set AGENTSEARCH_INTEGRATION=1 for live localhost tests")
def test_live_localhost_health() -> None:
    import requests

    headers = {}
    token = os.getenv("AGENT_SEARCH_TOKEN") or os.getenv("AGENTSEARCH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get("http://localhost:3939/health", headers=headers, timeout=10)
    assert response.status_code == 200
    assert response.json()["status"] in {"healthy", "degraded"}
