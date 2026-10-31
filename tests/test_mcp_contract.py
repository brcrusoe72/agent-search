from __future__ import annotations

import asyncio
import runpy
import sys
from pathlib import Path

import pytest


MCP_ROOT = Path(__file__).parents[1] / "mcp-server"
sys.path.insert(0, str(MCP_ROOT))


def _server_module():
    pytest.importorskip("mcp")
    from agent_search_mcp import server

    return server


def test_mcp_dependency_matches_verified_boundaries() -> None:
    pyproject = (MCP_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"mcp>=1.27.2,<2.3.0"' in pyproject


def test_mcp_transport_policy() -> None:
    server = _server_module()
    assert server.validate_transport("http://localhost:3939", "token") == "http://localhost:3939"
    assert server.validate_transport("https://search.example", "token") == "https://search.example"
    with pytest.raises(ValueError, match="plaintext HTTP"):
        server.validate_transport("http://search.example", "token")
    assert server.validate_transport(
        "http://search.example", "token", allow_insecure_http=True
    ) == "http://search.example"


def test_mcp_protocol_success_and_errors() -> None:
    _server_module()
    smoke = runpy.run_path(str(MCP_ROOT / "tests" / "protocol_smoke.py"))
    asyncio.run(asyncio.wait_for(smoke["verify_protocol"](), timeout=30))


def test_mcp_source_does_not_offer_cwd_token_file() -> None:
    source = (MCP_ROOT / "agent_search_mcp" / "server.py").read_text(encoding="utf-8")
    assert 'Path.cwd()' not in source

