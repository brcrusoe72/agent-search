"""Exercise AgentSearch MCP discovery and invocation over stdio."""

from __future__ import annotations

import asyncio
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


EXPECTED_TOOLS = {
    "health",
    "search",
    "search_strategy",
    "deep_search",
    "read_url",
    "read_batch",
}


async def verify_protocol() -> None:
    """Initialize a real subprocess, discover tools, and invoke one tool."""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_search_mcp", "--host", "127.0.0.1", "--port", "9"],
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            missing = EXPECTED_TOOLS - names
            assert not missing, f"missing MCP tools: {sorted(missing)}"

            result = await session.call_tool("health", {})
            assert len(result.content) == 1
            assert getattr(result.content[0], "text", "").startswith(
                "Error: Cannot connect to AgentSearch"
            )


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(verify_protocol(), timeout=30))
