# AgentSearch MCP Server 🔍

<!-- mcp-name: io.github.brcrusoe72/agent-search -->

[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)

MCP tool server that gives AI agents access to **93+ search engines** (Google, Bing, Brave, DuckDuckGo, Startpage, and more) through a single interface. Self-hosted. No API keys required.

Built on [AgentSearch](https://github.com/brcrusoe72/agent-search), which wraps SearXNG with multi-engine fusion, a 9-strategy content extraction kill chain, news aggregation, and job search.

## Why AgentSearch?

| Feature | AgentSearch | Other search MCP servers |
|---------|-------------|-------------------------|
| Search engines | 93+ (Google, Bing, Brave, DDG, Startpage...) | Usually 1-3 |
| Content extraction | 9-strategy kill chain (handles paywalls, Cloudflare, etc.) | Basic fetch or none |
| Multi-query fusion | ✓ (generates 3-5 query variations) | ✗ |
| News aggregation | 9+ dedicated news engines | ✗ |
| Job search | Dedicated job board search | ✗ |
| API keys required | None (self-hosted) | Often required |
| Self-improving | Evolver tracks success rates by domain/strategy | ✗ |

## Tools

| Tool | Description |
|------|-------------|
| `search` | Web search across 93+ engines with optional content extraction |
| `deep_search` | Multi-query fusion — generates 3-5 query variations and merges results |
| `read_url` | Extract content from any URL using a 9-strategy kill chain |
| `read_batch` | Batch extract content from up to 20 URLs concurrently |
| `news` | Structured news search from 9+ news engines |
| `search_jobs` | Job board search across multiple job sites |

## Prerequisites

[AgentSearch](https://github.com/brcrusoe72/agent-search) must be running (default: `http://localhost:3939`). AgentSearch requires a SearXNG instance (Docker setup included in the repo).

## Install

```bash
pip install mcp httpx
```

## Usage

```bash
# Default (AgentSearch at localhost:3939)
python server.py

# Custom host/port
python server.py --host 192.168.1.10 --port 4000
```

## Connect from Claude Desktop

Add to your Claude Desktop config:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "python",
      "args": ["/path/to/server.py"]
    }
  }
}
```

## Connect from Cursor

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "python",
      "args": ["/path/to/server.py"]
    }
  }
}
```

## Connect from any MCP client

The server uses **stdio transport**. Launch `python server.py` as a subprocess and communicate via stdin/stdout using the MCP JSON-RPC protocol.

## Example Output

```
> search("multi-hop agent delegation OAuth")

[
  {
    "title": "Agent Authorization in Multi-Party Systems",
    "url": "https://example.com/...",
    "snippet": "OAuth 2.0 token exchange (RFC 8693) breaks beyond 2 hops..."
  },
  ...
]
```

```
> read_url("https://example.com/article")

"# Article Title\n\nFull extracted content in markdown..."
```

## How the Kill Chain Works

When extracting content from a URL, AgentSearch tries up to 9 strategies in sequence:

1. **Direct fetch** — simple HTTP GET
2. **Readability extraction** — strip boilerplate, extract article
3. **User-Agent rotation** — try different browser signatures
4. **Wayback Machine** — fetch cached version from Internet Archive
5. **Google Cache** — fetch Google's cached copy
6. **Search-about** — find the content via search engines
7. **Custom adapters** — site-specific extractors
8. **PDF extraction** — for PDF URLs
9. **YouTube transcript** — for YouTube URLs

Each strategy is tried until one succeeds. The Evolver system tracks success rates by domain and strategy, learning which approaches work for which sites.

## Architecture

```
Claude / Cursor / any MCP client
        ↓ (stdio, JSON-RPC)
  AgentSearch MCP Server
        ↓ (HTTP)
  AgentSearch API (localhost:3939)
        ↓
  SearXNG (93+ engines)
```

## License

AGPL-3.0 — see [LICENSE](LICENSE).

## Links

- [AgentSearch](https://github.com/brcrusoe72/agent-search) — the search API this wraps
- [MCP Protocol](https://modelcontextprotocol.io) — the Model Context Protocol spec
- [Agent Café](https://thecafe.dev) — trust infrastructure for AI agents
