"""Exercise real HTTP decoding and recovery without contacting any provider."""
import asyncio
import json

import httpx
import pytest

from app import main
from app.search_providers import GitHubProvider
from tests.test_api import AppClient, isolated_app_state  # noqa: F401


GOOD = {"results": [{"url": "https://example.org/recovered", "title": "Recovered", "content": "Useful content", "engines": ["bing"]}]}
CATALOG = {"engines": [{"name": name, "enabled": True, "categories": ["general", "news"]}
                       for name in ("duckduckgo", "brave", "bing", "reuters", "bing news", "duckduckgo news")]}


def failure(request, kind):
    if isinstance(kind, int):
        return httpx.Response(kind, json={"error": "temporary upstream failure"})
    if kind == "timeout":
        raise httpx.ReadTimeout("upstream timeout", request=request)
    if kind == "connection":
        raise httpx.ConnectError("upstream unavailable", request=request)
    if kind == "invalid-json":
        return httpx.Response(200, text="not JSON")
    payloads = {"array": [], "null-results": {"results": None}, "bad-row": {"results": [None]},
                "bad-field": {"results": [{"url": 123, "title": None}]}}
    return httpx.Response(200, json=payloads[kind])


@pytest.mark.parametrize("kind", [401, 402, 429, 500, "timeout", "connection", "invalid-json", "array", "null-results", "bad-row", "bad-field"])
def test_web_search_reports_upstream_failure_then_recovers(monkeypatch, kind):
    attempts = []
    def handle(request):
        attempts.append(request)
        return failure(request, kind) if len(attempts) == 1 else httpx.Response(200, json=GOOD)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
            monkeypatch.setattr(main, "http_client", upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://testserver") as client:
                first = await client.get("/search", params={"q": "recovery"})
                assert first.status_code == 502, first.text
                second = await client.get("/search", params={"q": "recovery"})
                assert second.status_code == 200
                assert second.json()["results"][0]["url"] == GOOD["results"][0]["url"]
                assert not second.json()["meta"]["cached"]
    asyncio.run(run())


@pytest.mark.parametrize("kind", [401, 402, 429, 500, "timeout", "connection", "invalid-json"])
def test_direct_provider_reports_failure_then_recovers(kind):
    attempts = []
    def handle(request):
        attempts.append(request)
        return (failure(request, kind) if len(attempts) == 1 else httpx.Response(200, json={"items": [
            {"full_name": "example/recovered", "html_url": "https://github.com/example/recovered"}]}))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            provider = GitHubProvider()
            first = await provider.search(client, "recovery", 3)
            assert first.results == [] and first.upstream_status == "error" and first.upstream_errors
            second = await provider.search(client, "recovery", 3)
            assert len(second.results) == 1 and second.upstream_status == "ok"
    asyncio.run(run())


@pytest.mark.parametrize("mode", [None, "news"])
@pytest.mark.parametrize("kind", ["degraded-empty", "http-error"])
def test_empty_failed_search_does_not_poison_cache(monkeypatch, mode, kind):
    searches = []
    def handle(request):
        if request.url.path == "/config":
            return httpx.Response(200, json=CATALOG)
        searches.append(request)
        if len(searches) == 1:
            if kind == "http-error":
                return httpx.Response(503, json={})
            return httpx.Response(200, json={"results": [], "unresponsive_engines": [["bing", "timeout"]]})
        return httpx.Response(200, json=GOOD)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
            monkeypatch.setattr(main, "http_client", upstream)
            params = {"q": "temporary outage"}
            if mode:
                params["mode"] = mode
            client = AppClient()
            # Use the async method to keep MockTransport lifecycle in this event loop.
            first = await client._request("GET", "/search", params=params)
            assert first.status_code in (200, 502)
            if first.status_code == 200:
                assert first.json()["meta"]["upstream_status"] in ("error", "degraded")
            second = await client._request("GET", "/search", params=params)
            assert second.status_code == 200
            assert second.json()["results"], "A temporary failure was cached instead of retried"
            assert second.json()["meta"]["cached"] is False
            third = await client._request("GET", "/search", params=params)
            assert third.json()["meta"]["cached"] is True
            assert len(searches) == 2, "Successful results should still be cached"
    asyncio.run(run())


@pytest.mark.parametrize("payload", [None, [], {"engines": None}, {"engines": [None]}, {"engines": [{"enabled": True, "name": "bing", "categories": None}]}])
def test_malformed_catalog_is_a_controlled_gateway_error(monkeypatch, payload):
    def handle(request):
        assert request.url.path == "/config", "Must not search with unvalidated engines"
        return httpx.Response(200, content=b"null" if payload is None else json.dumps(payload).encode())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as upstream:
            monkeypatch.setattr(main, "http_client", upstream)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False), base_url="http://testserver") as client:
                response = await client.get("/search", params={"q": "test", "engines": "bing"})
                assert response.status_code == 502, response.text
    asyncio.run(run())


@pytest.mark.parametrize("package", ["requests", "httpx", "numpy"])
def test_exact_python_package_survives_provider_diversity(monkeypatch, package):
    from tests.test_api import EngineAwareFakeSearxngClient
    monkeypatch.setattr(main, "http_client", EngineAwareFakeSearxngClient())
    response = AppClient().get("/search", params={"q": package + " Python package", "mode": "code", "count": 3})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["url"] == f"https://pypi.org/project/{package}/"
    assert len(results) == 3
    assert [r["position"] for r in results] == [1, 2, 3]
