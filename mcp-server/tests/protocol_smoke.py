"""Verify both MCP SDK generations over real stdio without external services."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXPECTED_TOOLS = {
    "health", "engines", "providers_stats", "providers_health", "search",
    "search_strategy", "search_extract", "deep_search", "policy_search",
    "source_search", "source_institutions", "read_url", "browser_fetch",
    "read_batch", "news", "search_jobs",
}

# Only the HTTP boundary is replaced: the actual server factory, SDK request
# handlers, stdio transport, and client serialization all run. Do not call main()
# here, because it intentionally discovers the user's local bearer credentials.
SERVER_SCRIPT = r'''
import asyncio
from agent_search_mcp import server as module

class OfflineClient:
    def __init__(self, **kwargs):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    async def get(self, path, **kwargs):
        if path == "/providers/stats":
            raise module.httpx.ConnectError("offline")
        if path == "/providers/health":
            raise module.httpx.ReadTimeout("offline timeout")
        request = module.httpx.Request("GET", "http://localhost:3939" + path)
        if path == "/engines":
            return module.httpx.Response(503, text="offline unavailable", request=request)
        return module.httpx.Response(200, json={"status": "ok"}, request=request)

module.httpx.AsyncClient = OfflineClient

async def serve():
    server = module.make_server("http://localhost:3939")
    async with module.stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())

asyncio.run(serve())
'''


async def verify_protocol() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", SERVER_SCRIPT],
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == EXPECTED_TOOLS

            result = await session.call_tool("health", {})
            assert not result.model_dump(by_alias=True).get("isError", False)
            assert len(result.content) == 1
            assert json.loads(result.content[0].text) == {"status": "ok"}

            for name, expected in (
                ("unknown", ""),
                ("providers_stats", "Cannot connect"),
                ("providers_health", "timed out"),
                ("engines", "HTTP 503"),
            ):
                result = await session.call_tool(name, {})
                assert result.model_dump(by_alias=True).get("isError") is True, (name, result)
                assert result.content, name
                assert expected in result.content[0].text, (name, result)

            # Required arguments must not silently produce a successful call.
            result = await session.call_tool("search", {})
            assert result.model_dump(by_alias=True).get("isError") is True


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(verify_protocol(), timeout=30))
