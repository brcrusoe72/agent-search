"""Command-line runner for native AgentSearch installs."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AgentSearch API server.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "3939")))
    parser.add_argument(
        "--searxng-url",
        default=os.getenv("SEARXNG_URL", "http://localhost:8080"),
        help="Base URL of a running SearXNG instance.",
    )
    parser.add_argument(
        "--data-dir",
        default=os.getenv("DATA_DIR", str(Path.cwd() / "data")),
        help="Directory for AgentSearch SQLite cache/log state.",
    )
    parser.add_argument(
        "--adapters-dir",
        default=os.getenv("ADAPTERS_DIR", str(Path.cwd() / "adapters")),
        help="Directory containing AgentSearch extraction adapters.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("AGENT_SEARCH_TOKEN", ""),
        help="Optional bearer token required for non-/health endpoints.",
    )
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload for development.")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).expanduser().resolve()
    adapters_dir = Path(args.adapters_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    os.environ["SEARXNG_URL"] = args.searxng_url.rstrip("/")
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["ADAPTERS_DIR"] = str(adapters_dir)
    if args.token:
        os.environ["AGENT_SEARCH_TOKEN"] = args.token

    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
