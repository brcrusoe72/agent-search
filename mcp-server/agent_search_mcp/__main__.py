"""Entry point for `python -m agent_search_mcp`."""

import asyncio
import argparse
from agent_search_mcp.server import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AgentSearch MCP Server")
    parser.add_argument("--port", type=int, default=3939, help="AgentSearch port (default: 3939)")
    parser.add_argument("--host", default="localhost", help="AgentSearch host (default: localhost)")
    args = parser.parse_args()

    asyncio.run(main(f"http://{args.host}:{args.port}"))
