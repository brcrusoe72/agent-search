#!/usr/bin/env python3
"""AgentSearch MCP Tool Server — wraps localhost:3939 as MCP tools."""

import argparse
import json
import os
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

BASE_URL = "http://localhost:3939"


def load_token() -> str | None:
    """Load AgentSearch bearer token from env or common local credential files."""
    env_token = os.getenv("AGENT_SEARCH_TOKEN") or os.getenv("AGENTSEARCH_TOKEN")
    if env_token:
        return env_token.strip()

    candidates = [
        Path.cwd() / "credentials" / "agent-search-token.txt",
        Path.home() / ".openclaw" / "workspace" / "credentials" / "agent-search-token.txt",
        Path.home() / ".config" / "agent-search" / "token",
    ]
    for path in candidates:
        try:
            if path.exists():
                token = path.read_text(encoding="utf-8").strip()
                if token:
                    return token
        except OSError:
            continue
    return None


def make_server(base_url: str, token: str | None = None) -> Server:
    server = Server("agent-search")
    timeout = httpx.Timeout(120, connect=10)
    headers = {"Authorization": f"Bearer {token}"} if token else None

    def _add_optional(params: dict, arguments: dict, *names: str) -> dict:
        for name in names:
            value = arguments.get(name)
            if value is not None and value != "":
                params[name] = value
        return params

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="health",
                description="Check AgentSearch, SearXNG, and live search health.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="engines",
                description="List engines configured in the connected SearXNG instance.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="providers_stats",
                description="Return rolling provider and SearXNG attempt telemetry from AgentSearch.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="providers_health",
                description="Summarize provider health from recorded live AgentSearch attempts.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="search",
                description="SearXNG-backed web search with optional engine, strategy mode, domain, exclusion, and fetch controls.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                        "engines": {"type": "string", "description": "Comma-separated engine names"},
                        "mode": {"type": "string", "description": "Named strategy: general, code, academic, news, private, reference, or community"},
                        "domain": {"type": "string", "description": "Restrict results to this domain"},
                        "exclude_domains": {"type": "string", "description": "Comma-separated domains to exclude"},
                        "fetch": {"type": "boolean", "description": "Also extract page content from top results", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="search_strategy",
                description="Search with a named engine strategy. Modes: general, code, academic, news, private, reference, community.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "mode": {"type": "string", "description": "Named strategy (default general)", "default": "general"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                        "domain": {"type": "string", "description": "Restrict results to this domain"},
                        "exclude_domains": {"type": "string", "description": "Comma-separated domains to exclude"},
                        "fetch": {"type": "boolean", "description": "Also extract page content from top results", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="search_extract",
                description="Search and extract readable page content from top results.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "count": {"type": "integer", "description": "Number of results (default 5)", "default": 5},
                        "engines": {"type": "string", "description": "Comma-separated engine names"},
                        "mode": {"type": "string", "description": "Named strategy: general, code, academic, news, private, reference, or community"},
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
                name="policy_search",
                description="Policy/geopolitical search with source-library boost, junk filtering, and domain-quality ranking.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Policy/geopolitical search query"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                        "fetch": {"type": "boolean", "description": "Extract page content", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="source_search",
                description="Trace primary sources across curated government, research, and policy institutions.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Topic to trace"},
                        "count": {"type": "integer", "description": "Maximum results (default 15)", "default": 15},
                        "fetch": {"type": "boolean", "description": "Extract source page content", "default": False},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="source_institutions",
                description="List curated source registry institutions, optionally filtered by topic tag.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "Optional topic tag filter"},
                    },
                },
            ),
            Tool(
                name="read_url",
                description="Extract readable content from any URL using the kill chain, including direct fetch, readability, UA rotation, browser render, Wayback, cache, adapters, PDF, and YouTube.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to extract content from"},
                        "max_chars": {"type": "integer", "description": "Maximum content length"},
                        "skip_cache": {"type": "boolean", "description": "Bypass content cache", "default": False},
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="browser_fetch",
                description="Render a safe URL in an ephemeral browser context and extract readable text/links. Reports challenges instead of bypassing them.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to render and extract"},
                        "max_chars": {"type": "integer", "description": "Maximum extracted content length"},
                        "max_links": {"type": "integer", "description": "Maximum rendered links to return"},
                        "timeout_ms": {"type": "integer", "description": "Browser render timeout in milliseconds"},
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
                        "max_chars": {"type": "integer", "description": "Maximum chars per result"},
                    },
                    "required": ["urls"],
                },
            ),
            Tool(
                name="news",
                description="Structured news search using the news engines enabled in the connected SearXNG instance.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "News topic to search"},
                        "count": {"type": "integer", "description": "Number of results (default 10)", "default": 10},
                        "engines": {"type": "string", "description": "Override comma-separated news engines"},
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
                        "location": {"type": "string", "description": "Job location"},
                        "salary_min": {"type": "integer", "description": "Minimum salary filter"},
                    },
                    "required": ["query"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=timeout, headers=headers) as client:
                if name == "health":
                    r = await client.get("/health")

                elif name == "engines":
                    r = await client.get("/engines")

                elif name == "providers_stats":
                    r = await client.get("/providers/stats")

                elif name == "providers_health":
                    r = await client.get("/providers/health")

                elif name == "search":
                    params = {"q": arguments["query"], "count": arguments.get("count", 10)}
                    _add_optional(params, arguments, "engines", "mode", "domain", "exclude_domains")
                    if arguments.get("fetch"):
                        params["fetch"] = "true"
                    r = await client.get("/search", params=params)

                elif name == "search_strategy":
                    params = {
                        "q": arguments["query"],
                        "mode": arguments.get("mode", "general"),
                        "count": arguments.get("count", 10),
                    }
                    _add_optional(params, arguments, "domain", "exclude_domains")
                    if arguments.get("fetch"):
                        params["fetch"] = "true"
                    r = await client.get("/search/strategy", params=params)

                elif name == "search_extract":
                    params = {"q": arguments["query"], "count": arguments.get("count", 5)}
                    _add_optional(params, arguments, "engines", "mode")
                    r = await client.get("/search/extract", params=params)

                elif name == "deep_search":
                    r = await client.get("/search/deep", params={"q": arguments["query"], "count": arguments.get("count", 10)})

                elif name == "policy_search":
                    params = {"q": arguments["query"], "count": arguments.get("count", 10)}
                    if arguments.get("fetch"):
                        params["fetch"] = "true"
                    r = await client.get("/search/policy", params=params)

                elif name == "source_search":
                    params = {"q": arguments["query"], "count": arguments.get("count", 15)}
                    if arguments.get("fetch"):
                        params["fetch"] = "true"
                    r = await client.get("/search/sources", params=params)

                elif name == "source_institutions":
                    params = {}
                    _add_optional(params, arguments, "topic")
                    r = await client.get("/search/sources/institutions", params=params or None)

                elif name == "read_url":
                    params = {"url": arguments["url"]}
                    _add_optional(params, arguments, "max_chars")
                    if arguments.get("skip_cache"):
                        params["skip_cache"] = "true"
                    r = await client.get("/read", params=params)

                elif name == "browser_fetch":
                    params = {"url": arguments["url"]}
                    _add_optional(params, arguments, "max_chars", "max_links", "timeout_ms")
                    r = await client.get("/providers/browser/fetch", params=params)

                elif name == "read_batch":
                    body = {"urls": arguments["urls"]}
                    _add_optional(body, arguments, "max_chars")
                    r = await client.post("/read/batch", json=body)

                elif name == "news":
                    params = {"q": arguments["query"], "count": arguments.get("count", 10)}
                    _add_optional(params, arguments, "engines")
                    r = await client.get("/news", params=params)

                elif name == "search_jobs":
                    params = {"q": arguments["query"]}
                    _add_optional(params, arguments, "location", "salary_min")
                    r = await client.get("/search/jobs", params=params)

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


async def main(base_url: str, token: str | None = None):
    server = make_server(base_url, token=token or load_token())
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def cli() -> None:
    import asyncio
    parser = argparse.ArgumentParser(description="AgentSearch MCP Server")
    parser.add_argument("--port", type=int, default=3939, help="AgentSearch port (default: 3939)")
    parser.add_argument("--host", default="localhost", help="AgentSearch host (default: localhost)")
    parser.add_argument("--token", default=None, help="AgentSearch bearer token (default: env or local credential file)")
    args = parser.parse_args()
    asyncio.run(main(f"http://{args.host}:{args.port}", token=args.token))


if __name__ == "__main__":
    cli()
