from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


MCP_ROOT = Path(__file__).parents[1] / "mcp-server"
sys.path.insert(0, str(MCP_ROOT))


def _server_module():
    pytest.importorskip("mcp")
    from agent_search_mcp import server

    return server


def test_mcp_dependency_remains_on_verified_127_line() -> None:
    pyproject = (MCP_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"mcp>=1.27.2,<1.28.0"' in pyproject


def test_mcp_transport_policy() -> None:
    server = _server_module()
    assert server.validate_transport("http://localhost:3939", "token") == "http://localhost:3939"
    assert server.validate_transport("https://search.example", "token") == "https://search.example"
    with pytest.raises(ValueError, match="plaintext HTTP"):
        server.validate_transport("http://search.example", "token")
    assert server.validate_transport(
        "http://search.example", "token", allow_insecure_http=True
    ) == "http://search.example"


def test_mcp_unknown_tool_is_protocol_error() -> None:
    server_module = _server_module()
    from mcp.types import CallToolRequest, CallToolRequestParams

    server = server_module.make_server("http://localhost:3939")
    handler = server.request_handlers[CallToolRequest]
    request = CallToolRequest(params=CallToolRequestParams(name="unknown", arguments={}))
    response = asyncio.run(handler(request)).root

    assert response.isError is True
    assert response.content


def test_mcp_connection_failure_is_protocol_error(monkeypatch) -> None:
    server_module = _server_module()
    from mcp.types import CallToolRequest, CallToolRequestParams

    class FailedClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise server_module.httpx.ConnectError("offline")

    monkeypatch.setattr(server_module.httpx, "AsyncClient", lambda **kwargs: FailedClient())
    server = server_module.make_server("http://localhost:3939")
    handler = server.request_handlers[CallToolRequest]
    request = CallToolRequest(params=CallToolRequestParams(name="health", arguments={}))
    response = asyncio.run(handler(request)).root

    assert response.isError is True
    assert "Cannot connect" in response.content[0].text


def test_mcp_source_does_not_offer_cwd_token_file() -> None:
    source = (MCP_ROOT / "agent_search_mcp" / "server.py").read_text(encoding="utf-8")
    assert 'Path.cwd()' not in source
