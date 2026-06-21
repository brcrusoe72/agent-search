# AgentSearch MCP Server 🔍

<!-- mcp-name: io.github.brcrusoe72/agent-search -->

[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org)

MCP tool server that gives AI agents access to the engines enabled in your connected SearXNG-backed AgentSearch instance. Self-hosted. No third-party search API keys required; optional local bearer auth is supported.

Built on [AgentSearch](https://github.com/brcrusoe72/agent-search), which wraps SearXNG with multi-engine fusion, kill-chain content extraction, browser-rendered extraction, news aggregation, and job search.

## Why AgentSearch?

| Feature | AgentSearch | Other search MCP servers |
|---------|-------------|-------------------------|
| Search engines | SearXNG-backed; run `/engines` for the live list | Usually 1-3 |
| Content extraction | Kill chain plus browser-render extraction | Basic fetch or none |
| Multi-query fusion | ✓ (generates 3-5 query variations) | ✗ |
| News aggregation | News engines enabled in SearXNG | ✗ |
| Job search | Dedicated job board search | ✗ |
| Third-party search API keys required | None (self-hosted) | Often required |
| Self-improving | Evolver tracks success rates by domain/strategy | ✗ |

## Tools

| Tool | Description |
|------|-------------|
| `health` | Check API, SearXNG, and live search health |
| `engines` | List configured SearXNG engines |
| `providers_health` | Summarize provider health from recorded live attempts |
| `providers_stats` | Return rolling provider/SearXNG attempt telemetry |
| `search` | SearXNG-backed web search with engine/mode, domain, exclusion, and extraction controls |
| `search_strategy` | Named mode search: general, code, academic, news, private, reference, community |
| `search_extract` | Search and extract readable content from top results |
| `deep_search` | Multi-query fusion — generates 3-5 query variations and merges results |
| `policy_search` | Policy/geopolitical search with source-library and domain-quality ranking |
| `source_search` | Trace primary sources across curated institutions |
| `source_institutions` | List curated source registry institutions |
| `read_url` | Extract content from any URL using the kill chain |
| `browser_fetch` | Render a safe target URL in an ephemeral browser context and extract text/links |
| `read_batch` | Batch extract content from up to 20 URLs concurrently |
| `news` | Structured news search using enabled or explicitly selected SearXNG news engines |
| `search_jobs` | Job board search with location and salary filters |

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

# Token-protected AgentSearch
AGENT_SEARCH_TOKEN="change-me" python server.py
python server.py --token "change-me"
```

The MCP server loads a bearer token from `--token`, `AGENT_SEARCH_TOKEN`, `AGENTSEARCH_TOKEN`, `./credentials/agent-search-token.txt`, `~/.openclaw/workspace/credentials/agent-search-token.txt`, or `~/.config/agent-search/token`.

## Search engines

Engine availability comes from the AgentSearch API and its connected SearXNG instance. Run this against the API to inspect the live catalog:

```bash
curl "http://localhost:3939/engines"
```

The bundled AgentSearch SearXNG config explicitly enables a focused engine set, and `use_default_settings: true` can expose additional engines from the installed SearXNG catalog. Avoid hard-coding a fixed engine count in clients.

Strategy modes also include direct no-key providers for GitHub, MDN, Docker Hub, PyPI, Wikipedia, Wikidata, Hacker News, arXiv, Crossref, OpenAlex, and Semantic Scholar. Reddit is treated as best-effort because anonymous requests are often blocked. Use `providers_health` and `providers_stats` to inspect which providers are actually returning rows in the current process.

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

When extracting content from a URL, AgentSearch tries escalating strategies in sequence:

1. **Direct fetch** — simple HTTP GET
2. **Readability extraction** — strip boilerplate, extract article
3. **User-Agent rotation** — try different browser signatures
4. **Browser render** — render JS-heavy target pages in an ephemeral context
5. **Wayback Machine** — fetch cached version from Internet Archive
6. **Google Cache** — fetch Google's cached copy
7. **Search-about** — find the content via search engines
8. **Custom adapters** — site-specific extractors
9. **PDF extraction** — for PDF URLs
10. **YouTube transcript** — for YouTube URLs

Each strategy is tried until one succeeds. The Evolver system tracks success rates by domain and strategy, learning which approaches work for which sites.

The `browser_fetch` MCP tool exposes the browser renderer directly. It extracts rendered text and links from safe target pages, and reports CAPTCHA/challenge pages instead of trying to bypass them.

## Architecture

```
Claude / Cursor / any MCP client
        ↓ (stdio, JSON-RPC)
  AgentSearch MCP Server
        ↓ (HTTP)
  AgentSearch API (localhost:3939)
        ↓
  SearXNG-backed engine catalog
```

## License

The MCP server is AGPL-3.0 — see [LICENSE](LICENSE). The root AgentSearch API and Docker stack are MIT licensed.

## Links

- [AgentSearch](https://github.com/brcrusoe72/agent-search) — the search API this wraps
- [MCP Protocol](https://modelcontextprotocol.io) — the Model Context Protocol spec
- [Agent Café](https://thecafe.dev) — trust infrastructure for AI agents
