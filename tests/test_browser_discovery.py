"""Browser discovery behavior: real API routing, parsing, restrictions and failures."""
import asyncio
import base64
from urllib.parse import quote

import httpx
import pytest
from pydantic import ValidationError

from app import browser_renderer, browser_search, browser_service, main, search_providers
from tests.test_api import isolated_app_state  # noqa: F401


GOOD = {"success": True, "search_results": [
    {"title": "Write-Ahead Logging - SQLite", "url": "https://sqlite.org/wal.html", "content": "SQLite write ahead logging documentation"}
]}
CATALOG = {"engines": [{"name": x, "enabled": True, "categories": ["general"]}
                       for x in ("duckduckgo", "bing", "brave")]}


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("BROWSER_SERVICE_URL", "http://browser:3941")
    monkeypatch.setenv("BROWSER_DISCOVERY_ENABLED", "1")
    browser_service._search_cooldowns.clear()


def test_query_encoding_cannot_change_search_origin():
    url = browser_search.search_url("duckduckgo", "a &url=http://127.0.0.1/#something")
    assert url.startswith("https://html.duckduckgo.com/html/?q=a+")
    assert browser_search.search_navigation_allowed(url, "duckduckgo")
    for unsafe in ["https://html.duckduckgo.com.evil.test/html/", "https://html.duckduckgo.com@127.0.0.1/html/",
                   "http://html.duckduckgo.com/html/", "https://html.duckduckgo.com:8080/html/",
                   "https://html.duckduckgo.com/l/?uddg=http://127.0.0.1"]:
        assert not browser_search.search_navigation_allowed(unsafe, "duckduckgo")


@pytest.mark.parametrize("provider", ["duckduckgo", "bing"])
def test_tracking_links_decode_to_validated_destinations(monkeypatch, provider):
    checked = []
    def safe(url):
        checked.append(url)
        return url == "https://sqlite.org/wal.html"
    monkeypatch.setattr(browser_search, "is_safe_url", safe)
    def wrapped(url):
        if provider == "duckduckgo":
            return "//duckduckgo.com/l/?uddg=" + quote(url, safe="")
        return "https://www.bing.com/ck/a?u=a1" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    assert browser_search.result_url(wrapped("https://sqlite.org/wal.html"), provider) == "https://sqlite.org/wal.html"
    assert browser_search.result_url(wrapped("http://127.0.0.1/private"), provider) is None
    assert checked == ["https://sqlite.org/wal.html", "http://127.0.0.1/private"]
    assert browser_search.result_url(wrapped("file:///etc/passwd"), provider) is None


def test_parser_ignores_navigation_ads_and_duplicate_results(monkeypatch):
    monkeypatch.setattr(browser_search, "is_safe_url", lambda u: True)
    html = '''<a href="https://example.org/navigation">Menu</a>
      <div class="result result--ad"><a class="result__a" href="https://ad.example/ad">Ad</a></div>
      <div class="result"><a class="result__a" href="https://sqlite.org/wal.html">Write-Ahead Logging</a><a class="result__snippet">Original documentation</a></div>
      <div class="result"><a class="result__a" href="https://sqlite.org/wal.html">Duplicate</a></div>'''
    rows = browser_search.extract_search_results(html, "duckduckgo", 10)
    assert rows == [{"title": "Write-Ahead Logging", "url": "https://sqlite.org/wal.html",
                     "content": "Original documentation", "engines": ["browser:duckduckgo"]}]
    bing = '<li class="b_algo"><h2><a href="https://sqlite.org/wal.html">WAL</a></h2><div class="b_caption"><p>Details</p></div></li>'
    assert browser_search.extract_search_results(bing, "bing", 3)[0]["content"] == "Details"


@pytest.mark.parametrize("url", ['https://learn.microsoft.com/en-us/dotnet/', 'https://support.microsoft.com/windows',
                                 'https://blogs.bing.com/search/example', 'https://duckduckgo.com/duckduckgo-help-pages/'])
def test_search_owner_documentation_is_not_discarded_as_navigation(monkeypatch, url):
    monkeypatch.setattr(browser_search, "is_safe_url", lambda u: True)
    for provider in ['bing', 'duckduckgo']:
        assert browser_search.result_url(url, provider) == url
    assert browser_search.result_url('https://www.bing.com/search?q=next', 'bing') is None
    assert browser_search.result_url('https://html.duckduckgo.com/html/?q=next', 'duckduckgo') is None


def test_search_about_captcha_is_not_itself_a_challenge():
    assert not browser_search.search_challenge("CAPTCHA research - Search", "captcha verify you are human",
                                              '<div class="result"><a class="result__a">CAPTCHA paper</a></div>')
    assert browser_search.search_challenge("Search", "", '<form action="/anomaly.js">Pick ducks</form>')
    assert browser_search.search_challenge("Search", "Please verify you are human", "<p>Challenge</p>")


@pytest.mark.parametrize("fields", [{"provider": "google", "query": "test"}, {"provider": "bing", "query": "   "},
                                  {"provider": "bing", "query": "test", "count": 1000}])
def test_worker_rejects_unbounded_or_unknown_searches(fields):
    with pytest.raises(ValidationError):
        browser_service.SearchRequest(**fields)


def test_generic_renderer_still_blocks_search_pages(monkeypatch):
    monkeypatch.setattr(browser_renderer, "is_safe_url", lambda *args, **kwargs: True)
    async def run():
        result = await browser_renderer.render_browser_page_local("https://www.bing.com/search?q=test")
        assert not result.success and "search-engine domain" in result.error
    asyncio.run(run())


def test_worker_cools_down_after_challenge_without_hiding_reason(monkeypatch, enabled):
    calls = []
    async def blocked(url, **kwargs):
        calls.append((url, kwargs))
        return browser_renderer.BrowserRenderResult(url=url, error="Challenge detected", challenge_detected=True)
    monkeypatch.setattr(browser_service, "render_browser_page_local", blocked)
    async def run():
        request = browser_service.SearchRequest(provider="duckduckgo", query="SQLite")
        first = await browser_service.search(request)
        second = await browser_service.search(request)
        assert first["challenge_detected"]
        assert second["blocked_reason"] == "cooldown"
        assert len(calls) == 1
        assert calls[0][1]["_search_provider"] == "duckduckgo"
    asyncio.run(run())


def test_search_and_render_share_worker_capacity(monkeypatch, enabled):
    active, maximum = 0, 0
    async def render(url, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(.01)
        active -= 1
        return browser_renderer.BrowserRenderResult(url=url, success=True)
    monkeypatch.setattr(browser_service, "_capacity", asyncio.Semaphore(1))
    monkeypatch.setattr(browser_service, "render_browser_page_local", render)
    async def run():
        await asyncio.gather(browser_service.render(browser_service.RenderRequest(url="https://example.org/")),
                             browser_service.search(browser_service.SearchRequest(provider="bing", query="test")))
    asyncio.run(run())
    assert maximum == 1


def test_empty_query_result_does_not_cool_down_other_queries(monkeypatch, enabled):
    calls = []
    async def empty(url, **kwargs):
        calls.append(url)
        return browser_renderer.BrowserRenderResult(url=url, error="No results", blocked_reason="no_results")
    monkeypatch.setattr(browser_service, "render_browser_page_local", empty)
    async def run():
        for query in ["first query", "different query"]:
            await browser_service.search(browser_service.SearchRequest(provider="bing", query=query))
    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize("mode", [None, "browser"])
def test_complete_failure_is_not_cached_and_can_recover(monkeypatch, enabled, mode):
    calls = []
    async def browser(provider, query, count):
        calls.append(provider)
        return {"success": False, "search_results": [], "error": "Blocked"} if len(calls) <= 2 else GOOD
    monkeypatch.setattr(search_providers, "browser_search_request", browser)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))) as client:
            monkeypatch.setattr(main, "http_client", client)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as api:
                params = {"q": "SQLite WAL", "count": 1, **({"mode": mode} if mode else {})}
                first = await api.get('/search', params=params)
                second = await api.get('/search', params=params)
        assert first.status_code == (200 if mode else 502)
        assert second.status_code == 200
        assert second.json()["results"] and not second.json()["meta"]["cached"]
        assert len(calls) == 3
    asyncio.run(run())


def test_provider_tries_second_page_only_when_needed_and_preserves_failure(monkeypatch, enabled):
    calls = []
    async def search(provider, query, count):
        calls.append(provider)
        return ({"success": False, "search_results": [], "error": "Challenge", "blocked_reason": "challenge"}
                if provider == "duckduckgo" else GOOD)
    monkeypatch.setattr(search_providers, "browser_search_request", search)
    result = asyncio.run(search_providers.BrowserDiscoveryProvider().search(None, "SQLite WAL", 1))
    assert calls == ["duckduckgo", "bing"]
    assert result.results[0]["engines"] == ["browser:bing"]
    assert result.upstream_status == "degraded" and "Challenge" in result.upstream_errors[0]
    assert len(result.attempts) == 2


def test_domain_filtering_happens_before_provider_stop_decision(monkeypatch, enabled):
    calls = []
    async def search(provider, query, count):
        calls.append(provider)
        return ({"success": True, "search_results": [{**GOOD["search_results"][0], "url": "https://sqlite.org.evil.test/wal.html"}]}
                if provider == "duckduckgo" else GOOD)
    monkeypatch.setattr(search_providers, "browser_search_request", search)
    result = asyncio.run(search_providers.BrowserDiscoveryProvider().search(None, "SQLite WAL", 1, domain="sqlite.org"))
    assert calls == ["duckduckgo", "bing"] and len(result.results) == 1
    excluded = asyncio.run(search_providers.BrowserDiscoveryProvider().search(None, "SQLite WAL", 1,
                            domain="sqlite.org", exclude_domains="sqlite.org"))
    assert excluded.results == []


def test_browser_domain_filters_agree_with_api_for_explicit_www():
    matches = search_providers.BrowserDiscoveryProvider._matches
    assert matches('https://www.sqlite.org/wal.html', 'www.sqlite.org')
    assert not matches('https://sqlite.org/wal.html', 'www.sqlite.org')
    assert matches('https://www.sqlite.org/wal.html', 'https://sqlite.org/')
    assert not matches('https://sqlite.org.evil.test/wal.html', 'sqlite.org')


@pytest.mark.parametrize("mode", [None, "general", "browser"])
@pytest.mark.parametrize("failure", ["search", "catalog"])
def test_api_recovers_from_searx_outage_and_caches_success(monkeypatch, enabled, mode, failure):
    calls = []
    async def browser(provider, query, count):
        calls.append(provider)
        return GOOD
    def upstream(request):
        if request.url.path == "/config" and failure != "catalog":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(503, text="SearXNG unavailable")
    monkeypatch.setattr(search_providers, "browser_search_request", browser)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            monkeypatch.setattr(main, "http_client", client)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as api:
                params = {"q": "SQLite WAL", "count": 1, **({"mode": mode} if mode else {})}
                first = await api.get('/search', params=params)
                second = await api.get('/search', params=params)
        assert first.status_code == 200, first.text
        assert first.json()["results"][0]["url"] == "https://sqlite.org/wal.html"
        assert first.json()["meta"]["upstream_status"] == ("ok" if mode == "browser" else "degraded")
        assert second.json()["meta"]["cached"]
        assert calls == ["duckduckgo"]
    asyncio.run(run())


@pytest.mark.parametrize("explicit", [False, True])
def test_browser_not_used_for_sufficient_results_or_explicit_engines(monkeypatch, enabled, explicit):
    async def unexpected(*args, **kwargs):
        pytest.fail("Browser should not run")
    monkeypatch.setattr(search_providers, "browser_search_request", unexpected)
    def upstream(request):
        return httpx.Response(200, json=CATALOG if request.url.path == '/config' else
                              {"results": [{**GOOD['search_results'][0], "engines": ["bing"]}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            monkeypatch.setattr(main, "http_client", client)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://testserver") as api:
                response = await api.get('/search', params={"q": "SQLite", "count": 10 if explicit else 1,
                                                            **({"engines": "bing"} if explicit else {})})
        assert response.status_code == 200
    asyncio.run(run())
