from __future__ import annotations

import json
import math
import socket
import asyncio
from pathlib import Path

import httpx
import pytest

import app.evolver as evolver_module
from app.content_cache import ContentCache
from app.dedup import deduplicate_with_scoring
from app.evolver import Evolver
from app.source_library import get_domain_score


FIXTURES = Path(__file__).parent / "fixtures" / "security"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_untrusted_evolver_reports_never_apply_blocklist(tmp_path, monkeypatch) -> None:
    source = _fixture("evolver_poison.json")
    expected = _fixture("evolver_poison.expected.json")
    database_path = tmp_path / "content_cache.db"
    ContentCache(str(database_path))
    blocklist_path = tmp_path / "blocked_domains.txt"
    monkeypatch.setattr(evolver_module, "DYNAMIC_BLOCKLIST_PATH", blocklist_path)
    evolver = Evolver(str(database_path))

    for report in source["reports"]:
        evolver.report_failure(
            report["url"], report["strategies_tried"], report["error"]
        )

    result = evolver.evolve(7)

    assert len(source["reports"]) == expected["feedback_recorded"]
    assert bool(result["recommendations"]) is expected["recommendation_may_be_reported"]
    assert result["actions_taken"] == expected["actions_taken"]
    assert blocklist_path.exists() is expected["blocklist_created"]


def test_policy_domain_scoring_rejects_spoofed_hostnames() -> None:
    source = _fixture("hostname_spoof.json")
    expected = _fixture("hostname_spoof.expected.json")

    for case in source["cases"]:
        host = case["hostname"]
        score = get_domain_score(f"https://{host}/report")
        matched = score != 0.5
        assert matched is expected[host], host


def test_query_bearing_urls_remain_distinct_and_missing_engines_are_safe() -> None:
    source = _fixture("ranking_edges.json")
    expected = _fixture("ranking_edges.expected.json")

    results = deduplicate_with_scoring(source["results"])

    assert len(results) == expected["result_count"]
    assert {result.url for result in results} == set(expected["urls"])
    assert all(math.isfinite(result.score) for result in results)
    assert [result.position for result in results] == list(range(1, len(results) + 1))


def test_ssrf_fixture_fails_closed_for_every_non_global_answer() -> None:
    from app.fetch_security import validate_public_url

    source = _fixture("ssrf_targets.json")
    expected = _fixture("ssrf_targets.expected.json")

    for target in source["targets"]:
        if target["name"] == "rebind":
            continue

        def resolver(host, port, family=0, socktype=0, *, _target=target):
            if _target.get("dns_error"):
                raise socket.gaierror("fixture DNS failure")
            return [
                (
                    socket.AF_INET6 if ":" in address else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (address, port or 443),
                )
                for address in _target["answers"]
            ]

        try:
            validate_public_url(target["url"], resolver=resolver)
            allowed = True
        except ValueError:
            allowed = False
        assert allowed is expected[target["name"]]["allowed"], target["name"]


def test_safe_network_backend_revalidates_and_blocks_rebinding() -> None:
    from app.fetch_security import SafeAsyncNetworkBackend

    class RecordingBackend:
        def __init__(self) -> None:
            self.hosts: list[str] = []

        async def connect_tcp(self, host, port, **kwargs):
            self.hosts.append(host)
            return object()

        async def connect_unix_socket(self, path, **kwargs):
            raise AssertionError("unexpected unix socket")

    answers = iter(["8.8.8.8", "127.0.0.1"])

    def resolver(host, port, family=0, socktype=0):
        address = next(answers)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    backend = RecordingBackend()
    safe_backend = SafeAsyncNetworkBackend(backend=backend, resolver=resolver)

    asyncio.run(safe_backend.connect_tcp("rebind.example", 443))
    with pytest.raises(ValueError):
        asyncio.run(safe_backend.connect_tcp("rebind.example", 443))
    assert backend.hosts == ["8.8.8.8"]


def test_limited_http_fetch_rejects_actual_body_overflow() -> None:
    from app.fetch_security import ResponseTooLarge, safe_httpx_get

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 64, request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ResponseTooLarge):
                await safe_httpx_get(
                    client,
                    "https://example.com/article",
                    validator=lambda url: True,
                    max_bytes=32,
                )

    asyncio.run(run())


def test_direct_fetch_and_cache_preserve_provenance(tmp_path, monkeypatch) -> None:
    from app import killchain

    source = _fixture("direct_fetch.json")
    expected = _fixture("direct_fetch.expected.json")
    html = "<html><main>" + (source["content"] * 8) + "</main></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    async def run():
        cache = ContentCache(str(tmp_path / "cache.db"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            fresh = await killchain.kill_chain(client, source["requested_url"], content_cache=cache)
            cached = await killchain.kill_chain(client, source["requested_url"], content_cache=cache)
            return fresh, cached

    monkeypatch.setattr(killchain, "is_safe_url", lambda url, verbose=False: True)
    fresh, cached = asyncio.run(run())

    for result in (fresh, cached):
        assert result.url == expected["url"]
        assert result.fetched_url == expected["fetched_url"]
        assert result.derived_from is expected["derived_from"]
        assert result.trust.domain == expected["trust_domain"]
        assert result.requested_trust.domain == expected["requested_trust_domain"]
    assert cached.cached is expected["cache_preserves_provenance"]


def test_alternate_source_uses_actual_source_trust_and_provenance(monkeypatch) -> None:
    from app import killchain
    from app.killchain import FetchedContent

    source = _fixture("derived_content.json")
    expected = _fixture("derived_content.expected.json")

    async def no_content(*args, **kwargs):
        return None

    async def alternate(*args, **kwargs):
        return FetchedContent(source["content"] * 8, source["fetched_url"])

    for name in (
        "strategy_direct",
        "strategy_readability",
        "strategy_ua_rotation",
        "strategy_browser_render",
        "strategy_wayback",
        "strategy_google_cache",
    ):
        monkeypatch.setattr(killchain, name, no_content)
    monkeypatch.setattr(killchain, "strategy_search_about", alternate)
    monkeypatch.setattr(killchain, "is_safe_url", lambda url, verbose=False: True)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
            return await killchain.kill_chain(client, source["requested_url"])

    result = asyncio.run(run())

    assert result.url == expected["url"]
    assert result.fetched_url == expected["fetched_url"]
    assert result.derived_from == expected["derived_from"]
    assert result.trust.domain == expected["trust_domain"]
    assert result.requested_trust.domain == expected["requested_trust_domain"]
