#!/usr/bin/env python3
"""AgentSearch MCP Tool Server — wraps localhost:3939 as MCP tools."""

import argparse
import json
import sys

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

BASE_URL = "http://localhost:3939"


def make_server(base_url: str) -> Server:
    server = Server("agent-search")
    timeout = httpx.Timeout(120, connect=10)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="search",
                description="Web search across 93+ engines (Google, Bing, Brave, DDG, etc). Returns ranked results with titles, URLs, and snippets.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                        "fetch": {"type": "boolean", "description": "Also extract page content from top results", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="deep_search",
                description="Multi-query fusion search. Generates 3-5 query variations and merges results for broader coverage.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="read_url",
                description="Extract readable content from any URL using a 9-strategy kill chain (direct, readability, UA rotation, Wayback, Google Cache, etc).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to extract content from"},
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="read_batch",
                description="Batch extract content from multiple URLs concurrently (max 20).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "urls": {"type": "array", "items": {"type": "string"}, "description": "List of URLs to extract (max 20)"},
                    },
                    "required": ["urls"],
                },
            ),
            Tool(
                name="news",
                description="Structured news search from 9+ news engines (Google News, Bing News, Reuters, Yahoo, Brave, etc).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "News topic to search"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="search_jobs",
                description="Search job boards for job listings.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Job search query"},
                    },
                    "required": ["query"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
                if name == "search":
                    params = {"q": arguments["query"], "count": arguments.get("count", 10)}
                    if arguments.get("fetch"):
                        params["fetch"] = "true"
                    r = await client.get("/search", params=params)

                elif name == "deep_search":
                    r = await client.get("/search/deep", params={"q": arguments["query"], "count": arguments.get("count", 10)})

                elif name == "read_url":
                    r = await client.get("/read", params={"url": arguments["url"]})

                elif name == "read_batch":
                    r = await client.post("/read/batch", json={"urls": arguments["urls"]})

                elif name == "news":
                    r = await client.get("/news", params={"q": arguments["query"], "count": arguments.get("count", 10)})

                elif name == "search_jobs":
                    r = await client.get("/search/jobs", params={"q": arguments["query"]})

                else:
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]

                r.raise_for_status()
                return [TextContent(type="text", text=json.dumps(r.json(), indent=2))]

        except httpx.ConnectError:
            return [TextContent(type="text", text=f"Error: Cannot connect to AgentSearch at {base_url}. Is it running?")]
        except httpx.TimeoutException:
            return [TextContent(type="text", text=f"Error: Request to AgentSearch timed out.")]
        except httpx.HTTPStatusError as e:
            return [TextContent(type="text", text=f"Error: AgentSearch returned HTTP {e.response.status_code}: {e.response.text[:500]}")]
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]

    return server


async def main(base_url: str):
    server = make_server(base_url)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    parser = argparse.ArgumentParser(description="AgentSearch MCP Server")
    parser.add_argument("--port", type=int, default=3939, help="AgentSearch port (default: 3939)")
    parser.add_argument("--host", default="localhost", help="AgentSearch host (default: localhost)")
    args = parser.parse_args()
    asyncio.run(main(f"http://{args.host}:{args.port}"))
