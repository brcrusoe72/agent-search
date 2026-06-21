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
import sqlite3
import sys
import time
import types

import httpx
import pytest

from app import killchain
from app import main
from app import browser_renderer
from app.cache import Cache
from app.content_cache import ContentCache
from app.database import QueryDatabase
from app.dedup import deduplicate_with_scoring
from adapters import medium as medium_adapter
from adapters.safe_fetch import safe_requests_get


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, text: str | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text or ""

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("fake upstream error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


class FakeSearxngClient:
    async def get(self, url: str, params: dict | None = None, timeout: float | None = None, **kwargs) -> FakeResponse:
        if url.endswith("/healthz"):
            return FakeResponse({})
        if url.endswith("/config"):
            return FakeResponse({
                "engines": [
                    {"name": "duckduckgo", "shortcut": "ddg", "enabled": True, "categories": ["general"]},
                    {"name": "brave", "shortcut": "br", "enabled": True, "categories": ["general"]},
                    {"name": "github", "shortcut": "gh", "enabled": True, "categories": ["it", "repos"]},
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
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def get(self, url: str, params: dict | None = None, timeout: float | None = None, **kwargs) -> FakeResponse:
        if url.endswith("/search"):
            self.queries.append((params or {}).get("q", ""))
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


class UnresponsiveFakeSearxngClient:
    async def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        if url.endswith("/healthz"):
            return FakeResponse({})
        if url.endswith("/config"):
            return FakeResponse({
                "engines": [
                    {"name": "github", "shortcut": "gh", "enabled": True, "categories": ["it", "repos"]},
                ]
            })
        if url.endswith("/search"):
            return FakeResponse({
                "results": [],
                "unresponsive_engines": [
                    ["duckduckgo", "timeout"],
                    {"engine": "brave", "error": "blocked"},
                ],
                "errors": ["all engines failed"],
            })
        return FakeResponse({})


class EngineAwareFakeSearxngClient:
    def __init__(self) -> None:
        self.search_params: list[dict] = []
        self.mdn_params: list[dict] = []
        self.github_params: list[dict] = []
        self.docker_hub_params: list[dict] = []
        self.pypi_packages: list[str] = []
        self.wikipedia_params: list[dict] = []
        self.wikidata_params: list[dict] = []
        self.hackernews_params: list[dict] = []
        self.reddit_params: list[dict] = []
        self.arxiv_params: list[dict] = []
        self.crossref_params: list[dict] = []
        self.openalex_params: list[dict] = []
        self.semantic_scholar_params: list[dict] = []

    async def get(self, url: str, params: dict | None = None, timeout: float | None = None, **kwargs) -> FakeResponse:
        url = str(url)
        if url.startswith("https://developer.mozilla.org/api/v1/search"):
            self.mdn_params.append(dict(params or {}))
            return FakeResponse({
                "documents": [
                    {
                        "title": "Fetch API - Web APIs | MDN",
                        "mdn_url": "/en-US/docs/Web/API/Fetch_API",
                        "summary": "The Fetch API provides an interface for fetching resources.",
                    }
                ]
            })
        if url.startswith("https://api.github.com/search/repositories"):
            self.github_params.append(dict(params or {}))
            return FakeResponse({
                "items": [
                    {
                        "full_name": "python/cpython",
                        "html_url": "https://github.com/python/cpython?utm_source=test",
                        "description": "The Python programming language",
                        "stargazers_count": 65000,
                    }
                ]
            })
        if url.startswith("https://hub.docker.com/v2/search/repositories/"):
            self.docker_hub_params.append(dict(params or {}))
            return FakeResponse({
                "results": [
                    {
                        "repo_name": "library/python",
                        "short_description": "Python Docker image",
                        "pull_count": 1000000,
                    }
                ]
            })
        if url.startswith("https://pypi.org/pypi/") and url.endswith("/json"):
            package = url.removeprefix("https://pypi.org/pypi/").removesuffix("/json")
            self.pypi_packages.append(package)
            return FakeResponse({
                "info": {
                    "name": package,
                    "package_url": f"https://pypi.org/project/{package}/",
                    "summary": f"{package} Python package",
                    "version": "1.0.0",
                }
            })
        if url.startswith("https://en.wikipedia.org/w/api.php"):
            self.wikipedia_params.append(dict(params or {}))
            return FakeResponse({
                "query": {
                    "search": [
                        {
                            "title": "Python (programming language)",
                            "pageid": 23862,
                            "snippet": "Python is a programming language.",
                        }
                    ]
                }
            })
        if url.startswith("https://www.wikidata.org/w/api.php"):
            self.wikidata_params.append(dict(params or {}))
            return FakeResponse({
                "search": [
                    {
                        "id": "Q28865",
                        "label": "Python",
                        "description": "programming language",
                        "concepturi": "https://www.wikidata.org/wiki/Q28865",
                    }
                ]
            })
        if url.startswith("https://hn.algolia.com/api/v1/search"):
            self.hackernews_params.append(dict(params or {}))
            return FakeResponse({
                "hits": [
                    {
                        "title": "Python async discussion",
                        "url": "https://news.ycombinator.com/item?id=1",
                        "objectID": "1",
                        "points": 100,
                        "num_comments": 42,
                    }
                ]
            })
        if url.startswith("https://www.reddit.com/search.json"):
            self.reddit_params.append(dict(params or {}))
            return FakeResponse({
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Python discussion",
                                "permalink": "/r/Python/comments/1/example/",
                                "subreddit_name_prefixed": "r/Python",
                                "selftext": "Python community discussion",
                                "score": 123,
                            }
                        }
                    ]
                }
            })
        if url.startswith("https://export.arxiv.org/api/query"):
            self.arxiv_params.append(dict(params or {}))
            return FakeResponse({}, text="""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>https://arxiv.org/abs/1706.03762</id>
    <title>Attention Is All You Need</title>
    <summary>Transformer architecture paper</summary>
  </entry>
</feed>""")
        if url.startswith("https://api.crossref.org/works"):
            self.crossref_params.append(dict(params or {}))
            return FakeResponse({
                "message": {
                    "items": [
                        {
                            "title": ["Attention is all you need"],
                            "DOI": "10.5555/attention",
                            "container-title": ["NeurIPS"],
                            "abstract": "Transformer paper",
                        }
                    ]
                }
            })
        if url.startswith("https://api.openalex.org/works"):
            self.openalex_params.append(dict(params or {}))
            return FakeResponse({
                "results": [
                    {
                        "display_name": "Attention Is All You Need",
                        "doi": "https://doi.org/10.5555/openalex",
                        "abstract_inverted_index": {"Transformer": [0], "paper": [1]},
                    }
                ]
            })
        if url.startswith("https://api.semanticscholar.org/graph/v1/paper/search"):
            self.semantic_scholar_params.append(dict(params or {}))
            return FakeResponse({
                "data": [
                    {
                        "title": "Attention Is All You Need",
                        "url": "https://www.semanticscholar.org/paper/example",
                        "abstract": "Transformer paper",
                        "citationCount": 100000,
                    }
                ]
            })
        if url.endswith("/config"):
            return FakeResponse({
                "engines": [
                    {"name": "duckduckgo", "shortcut": "ddg", "enabled": True, "categories": ["general", "web"]},
                    {"name": "brave", "shortcut": "br", "enabled": True, "categories": ["general", "web"]},
                    {"name": "google", "shortcut": "g", "enabled": True, "categories": ["general", "web"]},
                    {"name": "startpage", "shortcut": "sp", "enabled": True, "categories": ["general", "web"]},
                    {"name": "github", "shortcut": "gh", "enabled": True, "categories": ["it", "repos"]},
                    {"name": "docker hub", "shortcut": "dh", "enabled": True, "categories": ["it"]},
                    {"name": "arxiv", "shortcut": "ax", "enabled": True, "categories": ["science"]},
                    {"name": "crossref", "shortcut": "cr", "enabled": True, "categories": ["science"]},
                    {"name": "openalex", "shortcut": "oa", "enabled": True, "categories": ["science"]},
                    {"name": "semantic scholar", "shortcut": "ss", "enabled": True, "categories": ["science"]},
                    {"name": "reuters", "shortcut": "reu", "enabled": True, "categories": ["news"]},
                    {"name": "yahoo news", "shortcut": "yn", "enabled": True, "categories": ["news"]},
                    {"name": "bing news", "shortcut": "bn", "enabled": True, "categories": ["news"]},
                    {"name": "duckduckgo news", "shortcut": "ddn", "enabled": True, "categories": ["news"]},
                    {"name": "wikinews", "shortcut": "wn", "enabled": True, "categories": ["news"]},
                    {"name": "bing", "shortcut": "b", "enabled": True, "categories": ["general", "web"]},
                    {"name": "disabled", "shortcut": "off", "enabled": False, "categories": ["general"]},
                ]
            })
        if url.endswith("/search"):
            search_params = params or {}
            self.search_params.append(dict(search_params))
            engine = search_params.get("engines")
            if engine == "bing" and "fallback" in search_params.get("q", ""):
                return FakeResponse({"results": []})

            engine_names = [item.strip() for item in (engine or "bing").split(",") if item.strip()]
            results = []
            for item in engine_names:
                if item == "github":
                    results.append({
                        "title": "python/cpython",
                        "url": "https://github.com/python/cpython",
                        "content": "The Python programming language",
                        "engines": ["github"],
                    })
                elif item == "docker hub":
                    results.append({
                        "title": "python - Docker Official Image",
                        "url": "https://hub.docker.com/_/python",
                        "content": "Python Docker image",
                        "engines": ["docker hub"],
                    })
                elif item == "arxiv":
                    results.append({
                        "title": "Attention Is All You Need",
                        "url": "https://arxiv.org/abs/1706.03762",
                        "content": "Transformer architecture paper",
                        "engines": ["arxiv"],
                    })
                elif item == "reuters":
                    results.append({
                        "title": "Reuters technology news",
                        "url": "https://www.reuters.com/technology/",
                        "content": "Reuters news result",
                        "engines": ["reuters"],
                    })
                else:
                    slug = item.replace(" ", "-")
                    results.append({
                        "title": f"{item} result",
                        "url": f"https://example.com/{slug}",
                        "content": f"Example {item} result",
                        "engines": [item],
                    })
            return FakeResponse({"results": results})
        return FakeResponse({})


class RaisingSearchFakeSearxngClient(EngineAwareFakeSearxngClient):
    async def get(self, url: str, params: dict | None = None, timeout: float | None = None, **kwargs) -> FakeResponse:
        if url.endswith("/search"):
            raise httpx.ReadTimeout("fake timeout")
        return await super().get(url, params=params, timeout=timeout, **kwargs)


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
    main._provider_attempt_stats.clear()
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
    assert data["search_available"] is True
    assert data["upstream_status"] == "ok"
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


def test_search_result_urls_strip_tracking_params() -> None:
    results = deduplicate_with_scoring([
        {
            "title": "tracked",
            "url": "https://example.com/page?utm_source=newsletter&id=123&fbclid=abc",
            "content": "Tracked result",
            "engines": ["test"],
        }
    ])

    assert results[0].url == "https://example.com/page?id=123"


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
    fake = DomainFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "domain filter", "count": 10, "domain": "example.com"})

    assert response.status_code == 200
    urls = [r["url"] for r in response.json()["results"]]
    assert urls == ["https://example.com/one", "https://docs.example.com/two"]
    assert fake.queries == ["site:example.com domain filter"]


def test_search_exclude_domains_matches_hostname_only(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", DomainFakeSearxngClient())

    response = client.get("/search", params={"q": "domain exclude", "count": 10, "exclude_domains": "example.com"})

    assert response.status_code == 200
    urls = [r["url"] for r in response.json()["results"]]
    assert urls == ["https://notexample.com/three", "https://other.test/articles/example.com"]


def test_search_rejects_unknown_engine_before_searxng_fallback(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "python", "engines": "notarealengine"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Unknown or disabled search engine(s)"
    assert detail["invalid_engines"] == ["notarealengine"]
    assert fake.search_params == []


def test_search_resolves_engine_shortcuts(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "python", "engines": "gh"})

    assert response.status_code == 200
    assert fake.search_params[0]["engines"] == "github"
    assert response.json()["meta"]["engines_used"] == ["github"]


def test_search_mode_rejects_explicit_engines(client: AppClient) -> None:
    response = client.get("/search", params={"q": "python", "mode": "code", "engines": "github"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Use either 'mode' or 'engines', not both"


def test_search_strategy_rejects_unknown_mode(client: AppClient) -> None:
    response = client.get("/search/strategy", params={"q": "python", "mode": "unknown"})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Unknown search strategy mode"
    assert "code" in detail["available_modes"]


def test_search_strategy_general_uses_bing_first(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "python", "count": 1, "mode": "general"})

    assert response.status_code == 200
    data = response.json()
    assert fake.search_params == [{"q": "python", "format": "json", "pageno": 1, "engines": "bing"}]
    assert data["meta"]["mode"] == "general"
    assert data["meta"]["engine_attempts"][0]["engines"] == ["bing"]
    assert data["meta"]["fallback_reason"] is None


def test_search_strategy_general_falls_back_to_non_bing_pack(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search/strategy", params={"q": "fallback query", "count": 1, "mode": "general"})

    assert response.status_code == 200
    data = response.json()
    assert [params["engines"] for params in fake.search_params] == [
        "bing",
        "duckduckgo,brave",
    ]
    assert data["meta"]["fallback_reason"] == "no_results"
    assert data["meta"]["engine_attempts"][1]["engines"] == ["duckduckgo", "brave"]
    assert "bing" not in data["meta"]["engines_used"]


def test_search_strategy_code_uses_direct_code_providers(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search/strategy", params={"q": "fetch api", "count": 10, "mode": "code"})

    assert response.status_code == 200
    data = response.json()
    assert fake.search_params == []
    assert fake.github_params[0]["q"] == "fetch api"
    assert fake.mdn_params == [{"q": "fetch api", "locale": "en-US"}]
    assert fake.docker_hub_params[0]["query"] == "fetch api"
    assert fake.pypi_packages == ["fetch"]
    assert [attempt["engines"] for attempt in data["meta"]["engine_attempts"]] == [
        ["github"],
        ["mdn"],
        ["docker hub"],
        ["pypi"],
    ]
    assert {"github", "mdn", "docker hub", "pypi"}.issubset(set(data["meta"]["engines_used"]))
    assert data["results"][0]["url"] == "https://github.com/python/cpython"
    assert data["meta"]["fallback_reason"] is None


def test_search_strategy_reference_uses_wikipedia_and_wikidata(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search/strategy", params={"q": "Python", "count": 10, "mode": "reference"})

    assert response.status_code == 200
    data = response.json()
    assert fake.search_params == []
    assert fake.wikipedia_params[0]["srsearch"] == "Python"
    assert fake.wikidata_params[0]["search"] == "Python"
    assert [attempt["engines"] for attempt in data["meta"]["engine_attempts"]] == [
        ["wikipedia"],
        ["wikidata"],
    ]
    assert {"wikipedia", "wikidata"}.issubset(set(data["meta"]["engines_used"]))


def test_search_strategy_community_uses_hackernews(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search/strategy", params={"q": "python async", "count": 10, "mode": "community"})

    assert response.status_code == 200
    data = response.json()
    assert fake.search_params == []
    assert fake.hackernews_params[0]["query"] == "python async"
    assert [attempt["engines"] for attempt in data["meta"]["engine_attempts"]] == [
        ["hackernews"],
    ]
    assert "hackernews" in data["meta"]["engines_used"]


def test_search_strategy_academic_uses_direct_providers(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search/strategy", params={"q": "transformer attention", "count": 10, "mode": "academic"})

    assert response.status_code == 200
    data = response.json()
    assert fake.search_params == []
    assert fake.arxiv_params[0]["search_query"] == "all:transformer attention"
    assert fake.crossref_params[0]["query"] == "transformer attention"
    assert fake.openalex_params[0]["search"] == "transformer attention"
    assert fake.semantic_scholar_params[0]["query"] == "transformer attention"
    assert [attempt["engines"] for attempt in data["meta"]["engine_attempts"]] == [
        ["arxiv"],
        ["crossref"],
        ["openalex"],
        ["semantic scholar"],
    ]
    assert {"arxiv", "crossref", "openalex", "semantic scholar"}.issubset(set(data["meta"]["engines_used"]))


def test_search_strategy_news_uses_requested_news_pack(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "semiconductors", "count": 3, "mode": "news"})

    assert response.status_code == 200
    assert fake.search_params[0]["engines"] == "reuters,bing news,duckduckgo news,wikinews"
    assert "categories" not in fake.search_params[0]
    meta = response.json()["meta"]
    assert meta["mode"] == "news"
    assert meta["engine_attempts"][0]["engines"] == ["reuters", "bing news", "duckduckgo news", "wikinews"]


def test_provider_stats_and_health_record_direct_attempts(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search/strategy", params={"q": "fetch api", "count": 10, "mode": "code"})
    assert response.status_code == 200

    stats = client.get("/providers/stats")
    assert stats.status_code == 200
    providers = {
        (row["source"], row["name"]): row
        for row in stats.json()["providers"]
    }
    for name in ["github", "mdn", "docker_hub", "pypi"]:
        row = providers[("provider", name)]
        assert row["attempts"] == 1
        assert row["successes"] == 1
        assert row["health"] == "healthy"

    health = client.get("/providers/health")
    assert health.status_code == 200
    data = health.json()
    assert data["status"] == "healthy"
    assert data["attempted"] >= 4


def test_search_records_searxng_error_attempt(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", RaisingSearchFakeSearxngClient())

    response = client.get("/search", params={"q": "timeout", "count": 1})
    assert response.status_code == 502

    stats = client.get("/providers/stats")
    assert stats.status_code == 200
    providers = {
        (row["source"], row["name"]): row
        for row in stats.json()["providers"]
    }
    row = providers[("searxng", "default")]
    assert row["attempts"] == 1
    assert row["errors"] == 1
    assert row["health"] == "error"
    assert "SearXNG error" in row["last_error"]


def test_news_records_searxng_error_attempt(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", RaisingSearchFakeSearxngClient())

    response = client.get("/news", params={"q": "timeout", "count": 1})
    assert response.status_code == 502

    stats = client.get("/providers/stats")
    assert stats.status_code == 200
    providers = {
        (row["source"], row["name"]): row
        for row in stats.json()["providers"]
    }
    row = providers[("searxng", main.NEWS_ENGINES)]
    assert row["attempts"] == 1
    assert row["errors"] == 1
    assert row["health"] == "error"
    assert "SearXNG error" in row["last_error"]


def test_browser_challenge_detection() -> None:
    assert browser_renderer.detect_browser_challenge("Client Challenge", "Verify you are human", "")
    assert browser_renderer.detect_browser_challenge("Access denied", "Cloudflare Ray ID: abc", "")
    assert not browser_renderer.detect_browser_challenge("Example Domain", "Ordinary article content", "")


def test_browser_extract_rendered_content() -> None:
    paragraph = "Rendered application text with enough useful content for extraction. " * 8
    html = f"<html><body><main><p>{paragraph}</p></main></body></html>"

    content = browser_renderer.extract_rendered_content(html, "", 1000)

    assert content is not None
    assert "Rendered application text" in content


def test_browser_fetch_endpoint(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    async def fake_render_browser_page(url: str, **kwargs):
        return browser_renderer.BrowserRenderResult(
            url=url,
            final_url=url,
            title="Rendered App",
            content="Rendered app content " * 20,
            chars=420,
            links=[
                browser_renderer.BrowserLink(text="Docs", url="https://example.com/docs"),
            ],
            success=True,
            render_time_ms=12.3,
        )

    monkeypatch.setattr(browser_renderer, "render_browser_page", fake_render_browser_page)

    response = client.get(
        "/providers/browser/fetch",
        params={"url": "https://example.com/app", "max_chars": 1000, "max_links": 5},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["strategy"] == "browser-render"
    assert data["title"] == "Rendered App"
    assert data["links"] == [{"text": "Docs", "url": "https://example.com/docs"}]


def test_browser_render_ignores_close_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLocator:
        async def inner_text(self, timeout: int) -> str:
            return "Rendered body content " * 20

    class FakePage:
        url = "https://example.com/app"

        async def route(self, pattern: str, handler) -> None:
            return None

        async def goto(self, url: str, wait_until: str, timeout: int):
            self.url = url
            return types.SimpleNamespace(status=200)

        async def wait_for_load_state(self, state: str, timeout: int) -> None:
            return None

        async def title(self) -> str:
            return "Example App"

        async def content(self) -> str:
            body = "Rendered body content " * 20
            return f"<html><body><main><p>{body}</p></main><a href='https://example.com/docs'>Docs</a></body></html>"

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator()

        async def evaluate(self, script: str, max_links: int):
            return [{"text": "Docs", "href": "https://example.com/docs"}]

    class FakeContext:
        async def new_page(self) -> FakePage:
            return FakePage()

        async def close(self) -> None:
            raise RuntimeError("context already closed")

    class FakeBrowser:
        async def new_context(self, **kwargs) -> FakeContext:
            return FakeContext()

        async def close(self) -> None:
            raise RuntimeError("browser already closed")

    class FakeChromium:
        async def launch(self, **kwargs) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakePlaywrightManager:
        async def __aenter__(self) -> FakePlaywright:
            return FakePlaywright()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.TimeoutError = TimeoutError
    fake_async_api.async_playwright = lambda: FakePlaywrightManager()
    fake_playwright = types.ModuleType("playwright")
    fake_playwright.async_api = fake_async_api

    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)
    monkeypatch.setattr(browser_renderer, "is_safe_url", lambda url, verbose=False: True)
    monkeypatch.setattr(browser_renderer, "_default_chromium_path", lambda: "/usr/bin/chromium")

    result = asyncio.run(browser_renderer.render_browser_page("https://example.com/app", max_links=5))

    assert result.success is True
    assert result.title == "Example App"
    assert result.content and "Rendered body content" in result.content
    assert result.links[0].url == "https://example.com/docs"


def test_browser_render_rejects_http_error_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeLocator:
        async def inner_text(self, timeout: int) -> str:
            return "Rendered error page content " * 20

    class FakePage:
        url = "https://example.com/missing"

        async def route(self, pattern: str, handler) -> None:
            return None

        async def goto(self, url: str, wait_until: str, timeout: int):
            self.url = url
            return types.SimpleNamespace(status=404)

        async def wait_for_load_state(self, state: str, timeout: int) -> None:
            return None

        async def title(self) -> str:
            return "Not Found"

        async def content(self) -> str:
            return "<html><body><main>Rendered error page content " * 20 + "</main></body></html>"

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator()

    class FakeContext:
        async def new_page(self) -> FakePage:
            return FakePage()

        async def close(self) -> None:
            return None

    class FakeBrowser:
        async def new_context(self, **kwargs) -> FakeContext:
            return FakeContext()

        async def close(self) -> None:
            return None

    class FakeChromium:
        async def launch(self, **kwargs) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakePlaywrightManager:
        async def __aenter__(self) -> FakePlaywright:
            return FakePlaywright()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    fake_async_api = types.ModuleType("playwright.async_api")
    fake_async_api.TimeoutError = TimeoutError
    fake_async_api.async_playwright = lambda: FakePlaywrightManager()
    fake_playwright = types.ModuleType("playwright")
    fake_playwright.async_api = fake_async_api

    monkeypatch.setitem(sys.modules, "playwright", fake_playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", fake_async_api)
    monkeypatch.setattr(browser_renderer, "is_safe_url", lambda url, verbose=False: True)
    monkeypatch.setattr(browser_renderer, "_default_chromium_path", lambda: "/usr/bin/chromium")

    result = asyncio.run(browser_renderer.render_browser_page("https://example.com/missing"))

    assert result.success is False
    assert result.content is None
    assert result.blocked_reason == "http_error"
    assert result.error == "Rendered navigation returned HTTP 404"


def test_strategy_coverage_preserves_successful_provider_rows() -> None:
    def result(title: str, url: str, engine: str, position: int) -> main.SearchResult:
        return main.SearchResult(
            title=title,
            url=url,
            snippet="snippet",
            engines=[engine],
            score=100 - position,
            position=position,
        )

    github_results = [
        result(f"github {index}", f"https://github.com/example/{index}", "github", index)
        for index in range(1, 8)
    ]
    docker_result = result("docker", "https://hub.docker.com/r/example/app", "docker hub", 8)
    pypi_result = result("pypi", "https://pypi.org/project/example/", "pypi", 9)

    selected = main._ensure_strategy_coverage(
        ranked_results=github_results,
        coverage_groups=[github_results, [docker_result], [pypi_result]],
        count=5,
    )

    assert selected[0].engines == ["github"]
    assert len(selected) == 5
    assert "docker hub" in {engine for item in selected for engine in item.engines}
    assert "pypi" in {engine for item in selected for engine in item.engines}
    assert [item.position for item in selected] == [1, 2, 3, 4, 5]


def test_domain_filter_does_not_site_scope_non_web_engines(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "python", "domain": "github.com", "engines": "github"})

    assert response.status_code == 200
    assert fake.search_params[0]["q"] == "python"
    assert response.json()["meta"]["total"] == 1


def test_domain_filter_site_scopes_web_engines(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    fake = EngineAwareFakeSearxngClient()
    monkeypatch.setattr(main, "http_client", fake)

    response = client.get("/search", params={"q": "python", "domain": "example.com", "engines": "bing"})

    assert response.status_code == 200
    assert fake.search_params[0]["q"] == "site:example.com python"
    assert response.json()["meta"]["total"] == 1


def test_search_preserves_upstream_diagnostics(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", UnresponsiveFakeSearxngClient())

    response = client.get("/search", params={"q": "backend outage", "count": 1})

    assert response.status_code == 200
    meta = response.json()["meta"]
    assert meta["total"] == 0
    assert meta["upstream_status"] == "degraded"
    assert meta["unresponsive_engines"] == ["duckduckgo", "brave"]
    assert "duckduckgo: timeout" in meta["upstream_errors"]
    assert "all engines failed" in meta["upstream_errors"]


def test_upstream_metadata_marks_partial_provider_failure_degraded() -> None:
    metadata = main._upstream_metadata(
        main.SearxngQueryResponse(results=[{"url": "https://example.com"}]),
        main.SearxngQueryResponse(results=[], upstream_status="error", upstream_errors=["semantic scholar: 429"]),
    )

    assert metadata["upstream_status"] == "degraded"
    assert metadata["upstream_errors"] == ["semantic scholar: 429"]


def test_health_degrades_when_search_probe_has_no_results(monkeypatch: pytest.MonkeyPatch, client: AppClient) -> None:
    monkeypatch.setattr(main, "http_client", UnresponsiveFakeSearxngClient())

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "degraded"
    assert data["searxng_available"] is True
    assert data["search_available"] is False
    assert data["upstream_status"] == "degraded"
    assert data["unresponsive_engines"] == ["duckduckgo", "brave"]


def test_query_database_async_methods_use_bounded_sqlite(tmp_path) -> None:
    db = QueryDatabase(str(tmp_path / "query_log.db"))

    async def run_queries() -> dict:
        await db.log_query("bounded sqlite", ["duckduckgo", "brave"], 2, 12.5)
        return await db.get_stats()

    stats = asyncio.run(run_queries())

    assert stats["total_queries"] == 1
    assert stats["queries_per_engine"] == {"brave": 1, "duckduckgo": 1}
    assert stats["avg_results_per_engine"] == {"brave": 2.0, "duckduckgo": 2.0}


def test_content_cache_maintenance_prunes_expired_cache_and_old_fetch_logs(tmp_path) -> None:
    cache_db = ContentCache(str(tmp_path / "content_cache.db"))
    now = time.time()
    old = now - 31 * 86400
    fresh = now - 5

    with sqlite3.connect(cache_db.db_path) as conn:
        conn.execute(
            """
            INSERT INTO content_cache
            (url_hash, url, content, strategy, chars, created_at, expires_at)
            VALUES ('expired', 'https://example.com/old', 'old', 'direct', 3, ?, ?)
            """,
            (old, old),
        )
        conn.execute(
            """
            INSERT INTO content_cache
            (url_hash, url, content, strategy, chars, created_at, expires_at)
            VALUES ('fresh', 'https://example.com/new', 'new', 'direct', 3, ?, ?)
            """,
            (fresh, now + 3600),
        )
        conn.execute(
            """
            INSERT INTO fetch_log
            (url, domain, strategy, success, chars, strategies_tried, error, timestamp)
            VALUES ('https://example.com/old-log', 'example.com', 'direct', 1, 3, 'direct', NULL, ?)
            """,
            (old,),
        )
        conn.execute(
            """
            INSERT INTO fetch_log
            (url, domain, strategy, success, chars, strategies_tried, error, timestamp)
            VALUES ('https://example.com/new-log', 'example.com', 'direct', 1, 3, 'direct', NULL, ?)
            """,
            (fresh,),
        )
        conn.commit()

    result = cache_db.maintain(fetch_log_retention_days=30, vacuum_min_deleted_rows=1)

    with sqlite3.connect(cache_db.db_path) as conn:
        cache_count = conn.execute("SELECT COUNT(*) FROM content_cache").fetchone()[0]
        fetch_count = conn.execute("SELECT COUNT(*) FROM fetch_log").fetchone()[0]

    assert result["expired_content_cache"] == 1
    assert result["old_fetch_logs"] == 1
    assert result["deleted_rows"] == 2
    assert result["vacuumed"] is True
    assert cache_count == 1
    assert fetch_count == 1


def test_query_database_maintenance_prunes_old_query_logs(tmp_path) -> None:
    db = QueryDatabase(str(tmp_path / "query_log.db"))
    now = time.time()
    old = now - 31 * 86400
    fresh = now - 5

    with sqlite3.connect(db.db_path) as conn:
        conn.execute(
            """
            INSERT INTO query_log (query, timestamp, engine, result_count, response_time_ms)
            VALUES ('old query', ?, 'bing', 1, 10.0)
            """,
            (old,),
        )
        conn.execute(
            """
            INSERT INTO query_log (query, timestamp, engine, result_count, response_time_ms)
            VALUES ('fresh query', ?, 'bing', 2, 20.0)
            """,
            (fresh,),
        )
        conn.commit()

    result = db.maintain(query_log_retention_days=30, vacuum_min_deleted_rows=1)
    stats = asyncio.run(db.get_stats())

    assert result["old_query_logs"] == 1
    assert result["deleted_rows"] == 1
    assert result["vacuumed"] is True
    assert stats["total_queries"] == 1
    assert stats["queries_per_engine"] == {"bing": 1}


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
