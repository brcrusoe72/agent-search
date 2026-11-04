"""Exercise PDF connection policy without contacting external hosts."""
import socket
import asyncio
import threading
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from app import killchain


@pytest.mark.parametrize("fetcher", ["pdf", "html"])
def test_dns_rebinding_cannot_reach_loopback(monkeypatch, fetcher):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"not a PDF")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = socket.getaddrinfo
    lookups = []

    def resolver(host, port, *args, **kwargs):
        if host == "rebind.example":
            lookups.append(host)
            host = "93.184.216.34" if len(lookups) == 1 else "127.0.0.1"
        return original(host, port, *args, **kwargs)

    for name in ("FETCH_PROXY_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                 "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    try:
        url = f"http://rebind.example:{server.server_port}/proof.pdf"
        if fetcher == "pdf":
            assert killchain.strategy_pdf_sync(url) is None
        else:
            from app import main
            monkeypatch.setenv("HTTP_PROXY", "http://unused.example:8888")
            monkeypatch.setenv("NO_PROXY", "*")
            monkeypatch.setattr(main, "http_client", None)

            async def fetch():
                async with main.lifespan(main.app):
                    with pytest.raises(ValueError):
                        await killchain._safe_get(main.http_client, url)

            asyncio.run(fetch())
        assert len(lookups) >= 2
        assert hits == [], "Download reached a private address after DNS rebinding"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("mode", ["success", "redirect", "declared_limit", "stream_limit"])
def test_pdf_download_limits_redirects_and_parser(monkeypatch, mode):
    urls = []
    parsed = []
    payload = b"%PDF-test-content"

    def respond(request):
        urls.append(str(request.url))
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        if mode == "declared_limit":
            return httpx.Response(200, headers={"content-length": "999"}, content=payload)
        if mode == "stream_limit":
            return httpx.Response(200, stream=httpx.ByteStream(b"x" * 33))
        return httpx.Response(200, content=payload)

    original_client = httpx.Client
    def client(**kwargs):
        kwargs.pop("transport", None)
        return original_client(transport=httpx.MockTransport(respond), **kwargs)

    def parse(command, **kwargs):
        assert command[0] == sys.executable
        parsed.append(Path(command[-1]).read_bytes())
        return SimpleNamespace(returncode=0, stdout=json.dumps("useful text " * 100))

    monkeypatch.delenv("FETCH_PROXY_URL", raising=False)
    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(killchain, "is_safe_url", lambda url, **kw: url.startswith("https://public.example/"))
    monkeypatch.setattr(killchain, "MAX_PDF_BYTES", 32)
    monkeypatch.setattr(killchain.subprocess, "run", parse)
    result = killchain.strategy_pdf_sync("https://public.example/document.pdf")
    assert urls == ["https://public.example/document.pdf"]
    if mode == "success":
        assert result == "useful text " * 100
        assert parsed == [payload]
    else:
        assert result is None
        assert parsed == []


def test_pdf_explicit_proxy_disables_ambient_proxy(monkeypatch):
    options = []
    monkeypatch.setenv("FETCH_PROXY_URL", "http://fetch-proxy:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://unexpected:8888")

    def client(**kwargs):
        options.append(kwargs)
        raise httpx.ConnectError("validating gateway unavailable")

    monkeypatch.setattr(httpx, "Client", client)
    assert killchain.strategy_pdf_sync("https://public.example/document.pdf") is None
    assert options == [{"trust_env": False, "timeout": 30, "proxy": "http://fetch-proxy:8888"}]


def test_api_routes_external_requests_through_explicit_proxy(monkeypatch):
    from app import main
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(main, "http_client", None)
    monkeypatch.setattr(main, "SEARXNG_URL", origin)
    monkeypatch.setenv("FETCH_PROXY_URL", origin)
    monkeypatch.setenv("HTTP_PROXY", "http://unexpected.invalid:1")
    monkeypatch.setenv("NO_PROXY", "*")

    async def fetch():
        async with main.lifespan(main.app):
            assert (await main.http_client.get(origin + "/search")).status_code == 200
            assert (await main.http_client.get("http://public.invalid/article")).status_code == 200

    try:
        asyncio.run(fetch())
        assert paths == ["/search", "http://public.invalid/article"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
