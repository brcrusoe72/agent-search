from __future__ import annotations

import asyncio
import inspect
import json
import socket
from pathlib import Path

import pytest

from app import browser_renderer, browser_service, fetch_proxy


FIXTURES = Path(__file__).parent / "fixtures" / "security"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_browser_launch_keeps_chromium_sandbox_enabled() -> None:
    source = inspect.getsource(browser_renderer.render_browser_page_local)
    assert "--no-sandbox" not in source
    assert "--disable-setuid-sandbox" not in source
    assert "chromium_sandbox=True" in source


def test_browser_worker_enforces_global_capacity(monkeypatch) -> None:
    fixture = _fixture("browser_limits.json")
    expected = _fixture("browser_limits.expected.json")
    active = 0
    maximum = 0

    async def fake_render(url: str, **kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return browser_renderer.BrowserRenderResult(url=url, final_url=url, success=True)

    monkeypatch.setattr(browser_service, "MAX_CONCURRENT", fixture["max_concurrent"])
    monkeypatch.setattr(browser_service, "_capacity", asyncio.Semaphore(fixture["max_concurrent"]))
    monkeypatch.setattr(browser_service, "render_browser_page_local", fake_render)

    async def run() -> None:
        await asyncio.gather(*[
            browser_service.render(browser_service.RenderRequest(url=row["url"]))
            for row in fixture["requests"]
            if row["safe"]
        ])

    asyncio.run(run())
    assert maximum == expected["maximum_observed_concurrency"]


def test_validating_proxy_rejects_mixed_dns_answers(monkeypatch) -> None:
    def mixed_answers(*args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ]

    monkeypatch.setattr(fetch_proxy.socket, "getaddrinfo", mixed_answers)
    with pytest.raises(ValueError):
        fetch_proxy._resolve_direct("mixed.example", 443)


def test_validating_proxy_has_cheap_local_liveness() -> None:
    async def run() -> bytes:
        class MemoryWriter:
            def __init__(self) -> None:
                self.body = bytearray()
                self.closed = False

            def write(self, data: bytes) -> None:
                self.body.extend(data)

            async def drain(self) -> None:
                return None

            def is_closing(self) -> bool:
                return self.closed

            def close(self) -> None:
                self.closed = True

        reader = asyncio.StreamReader()
        reader.feed_data(b"GET /live HTTP/1.1\r\nHost: localhost\r\n\r\n")
        reader.feed_eof()
        writer = MemoryWriter()
        await fetch_proxy.handle_client(reader, writer)  # type: ignore[arg-type]
        return bytes(writer.body)

    response = asyncio.run(run())
    assert response.startswith(b"HTTP/1.1 200 OK")
    assert response.endswith(b'{"status":"alive"}')


def test_browser_redirect_recalculates_actual_trust(monkeypatch) -> None:
    source = inspect.getsource(browser_renderer.render_browser_page_local)
    assert "evaluate_trust(final_url" in source
    assert "requested_trust=requested_trust" in source


def test_deployment_routes_browser_egress_through_validating_proxy() -> None:
    direct = (Path(__file__).parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    private = (Path(__file__).parents[1] / "examples" / "compose.private.yml").read_text(encoding="utf-8")

    assert "BROWSER_PROXY_URL=http://fetch-proxy:8888" in direct
    assert "NETWORK_ROUTE=direct" in direct
    assert "fetch-internal:" in direct
    assert "internal: true" in direct
    assert "BROWSER_PROXY_URL=http://fetch-proxy-private:8888" in private
    assert "FETCH_PROXY_TOR=tor:9050" in private
    assert "NETWORK_ROUTE=tor" in private
    assert "ALL_PROXY=" not in private
