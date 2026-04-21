# 🔍 AgentSearch

Self-hosted SearXNG-backed search API and MCP server for AI agents. Drop-in alternative to Tavily/Exa — without the per-query bill or the API key.

[![PyPI](https://img.shields.io/pypi/v/agentsearch-client)](https://pypi.org/project/agentsearch-client/) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

AgentSearch wraps [SearXNG](https://github.com/searxng/searxng) (an open-source meta-search engine) with a clean FastAPI layer that returns structured JSON. Built for LLM agents, RAG pipelines, and anyone tired of paying per-query for search APIs.

## Why?

| | AgentSearch | Brave API | Google CSE | SerpAPI |
|---|---|---|---|---|
| **Cost** | Free forever | $0.005/query | $5/1K queries | $50/mo |
| **API Key** | None | Required | Required | Required |
| **Setup** | `docker compose up` | Sign up + wait | Console + billing | Sign up + pay |
| **Engines** | 6+ (configurable) | Brave only | Google only | Google only |
| **Self-hosted** | ✅ | ❌ | ❌ | ❌ |
| **Rate limits** | You control | 1 req/sec free | 100/day free | 100/mo free |
| **Deduplication** | Built-in | ❌ | ❌ | ❌ |

## Quickstart

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
docker compose up -d
```

That's it. Search at `http://localhost:3939`.

## API

### `GET /search`

General web search with deduplication and multi-engine scoring.

```bash
curl "http://localhost:3939/search?q=python+async+patterns&count=5"
```

```json
{
  "results": [
    {
      "title": "Async IO in Python: A Complete Walkthrough",
      "url": "https://realpython.com/async-io-python/",
      "snippet": "A comprehensive guide to async/await in Python 3...",
      "engines": ["google", "bing", "duckduckgo"],
      "score": 1.0,
      "position": 1
    }
  ],
  "meta": {
    "query": "python async patterns",
    "total": 5,
    "engines_used": ["google", "bing", "duckduckgo"],
    "cached": false,
    "response_time_ms": 842.3
  }
}
```

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | *required* | Search query |
| `count` | int | 10 | Results to return (1-50) |
| `engines` | string | all | Comma-separated engines (`google,bing`) |
| `domain` | string | — | Filter to specific domain |
| `exclude_domains` | string | — | Comma-separated domains to exclude |

### `GET /search/jobs`

Job search across LinkedIn, Indeed, Glassdoor, and ZipRecruiter.

```bash
curl "http://localhost:3939/search/jobs?q=senior+python+engineer&location=remote&salary_min=150000"
```

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | *required* | Job title / keywords |
| `location` | string | — | Location filter |
| `salary_min` | int | — | Minimum salary filter |

### `GET /health`

```bash
curl http://localhost:3939/health
```

### `GET /engines`

List all available search engines and their status.

```bash
curl http://localhost:3939/engines
```

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Your Agent  │────▶│  AgentSearch API  │────▶│   SearXNG    │
│  (any LLM)  │◀────│  :3939 (FastAPI)  │◀────│   :8080      │
└─────────────┘     └──────────────────┘     └──────┬──────┘
                     • Deduplication                  │
                     • Scoring                   ┌────┴────┐
                     • Caching                   │ Google  │
                     • Rate limiting             │ Bing    │
                                                 │ DDG     │
                                                 │ Brave   │
                                                 │ Start.. │
                                                 └─────────┘
```

## Integration Examples

### Python (requests)

```python
import requests

resp = requests.get("http://localhost:3939/search", params={"q": "latest AI news", "count": 5})
results = resp.json()["results"]
for r in results:
    print(f"{r['title']}: {r['url']}")
```

### LangChain Tool

```python
from langchain.tools import tool
import requests

@tool
def web_search(query: str) -> str:
    """Search the web using AgentSearch."""
    resp = requests.get("http://localhost:3939/search", params={"q": query, "count": 5})
    results = resp.json()["results"]
    return "\n".join(f"- {r['title']}: {r['url']}\n  {r['snippet']}" for r in results)
```

### OpenClaw (TOOLS.md)

```
## Search
- **AgentSearch**: `http://localhost:3939/search?q=QUERY` via web_fetch — free, self-hosted, no rate limits
```

### curl (one-liner)

```bash
curl -s "http://localhost:3939/search?q=your+query" | jq '.results[:3]'
```

## Configuration

Environment variables (set in `docker-compose.yml`):

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG instance URL |
| `CACHE_TTL` | `3600` | Cache duration in seconds |
| `RATE_LIMIT` | `30` | Max requests per minute |

### Adding/removing search engines

Edit `searxng/settings.yml` and restart:

```bash
docker compose restart searxng
```

## Development

```bash
# Run locally (needs SearXNG running separately)
pip install -r requirements.txt
SEARXNG_URL=http://localhost:8080 uvicorn app.main:app --reload --port 3939
```

## Contributing

1. Fork it
2. Create your branch (`git checkout -b feature/better-dedup`)
3. Commit (`git commit -am 'Improve dedup algorithm'`)
4. Push (`git push origin feature/better-dedup`)
5. Open a PR

## Python SDK

```bash
pip install agentsearch-client
```

```python
from agentsearch import AgentSearch

client = AgentSearch()  # defaults to localhost:3939
results = client.search("manufacturing OEE best practices")
for r in results:
    print(f"{r.title} — {r.url}")
```

## MCP Server

Use AgentSearch as an [MCP](https://modelcontextprotocol.io) tool server — gives any MCP-compatible client (Claude Desktop, Cursor, etc.) access to all 6 tools over stdio.

```bash
pip install mcp httpx
python mcp-server/server.py
```

Add to Claude Desktop config:
```json
{
  "mcpServers": {
    "agent-search": {
      "command": "python",
      "args": ["/path/to/mcp-server/server.py"]
    }
  }
}
```

See [`mcp-server/README.md`](mcp-server/README.md) for full setup.

## Related Projects

- **[Operations Intelligence Analyzer](https://github.com/brcrusoe72/operations-intelligence-analyzer)** — AI-powered OEE analysis ([live demo](https://oee.trueaicost.com))
- **[Agent Café](https://github.com/brcrusoe72/agent-cafe)** — AI agent marketplace ([live at thecafe.dev](https://thecafe.dev))
- **[Manufacturing Analyst Pro](https://github.com/brcrusoe72/manufacturing-analyst-pro)** — MES data analysis CLI
- **[AI True Cost Calculator](https://trueaicost.com)** — Know what your AI project really costs

## License

MIT — do whatever you want with it.
