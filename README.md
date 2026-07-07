# AgentSearch

Self-hosted search API for AI agents. 17 endpoints. Layered content extraction with optional browser rendering. Optional Tor-anonymized stack. No third-party search API keys, no per-query fees, no vendor lock-in. Optional local bearer auth is supported.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![PyPI](https://img.shields.io/pypi/v/agentsearch-client)](https://pypi.org/project/agentsearch-client/)

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
./scripts/prepare-searxng.sh
docker compose up -d
curl "http://localhost:3939/search?q=distributed+consensus+algorithms"
```

You now have a deduplicated, multi-engine search API running on `:3939`.

If you enable auth, pass the token on all non-health endpoints:

```bash
export AGENT_SEARCH_TOKEN="change-me"
curl -H "Authorization: Bearer $AGENT_SEARCH_TOKEN" \
  "http://localhost:3939/search?q=distributed+consensus+algorithms"
```

Prefer not to use Docker for the API server?

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
./scripts/install-native.sh
./scripts/run-native.sh
```

Native mode requires Python 3.11+ and a reachable SearXNG instance with JSON output enabled. It stores AgentSearch state in `./data`. See [Native Install](docs/native-install.md).

## Verify

Run the self-contained test suite:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest requests
pip install -e sdk -e mcp-server
./scripts/prepare-searxng.sh
pytest tests -q
python -m compileall app adapters mcp-server/agent_search_mcp scripts sdk -q
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f examples/compose.private.yml config --quiet
docker build -t agent-search-api:test .
```

Those tests mock SearXNG, so they do not require Docker or a running local service.

Run the optional live localhost check:

```bash
AGENTSEARCH_INTEGRATION=1 pytest tests -q
```

If your local instance requires auth:

```bash
AGENT_SEARCH_TOKEN="change-me" AGENTSEARCH_INTEGRATION=1 pytest tests -q
```

Run the optional Docker smoke tests against a running direct/private stack:

```bash
AGENT_SEARCH_TOKEN="change-me" \
AGENTSEARCH_DOCKER_INTEGRATION=1 \
pytest tests/test_live_docker.py -q
```

---

## What it does

AgentSearch wraps [SearXNG](https://github.com/searxng/searxng) with a FastAPI layer that adds the pieces agents usually need on top of raw search: deduplication, cross-engine scoring, content extraction, query expansion, domain trust scoring, prompt injection scrubbing, and failure-pattern analysis.

**Standard stack** — `docker compose up` gives you search on `:3939`.

**Private stack** — `docker compose -f docker-compose.yml -f examples/compose.private.yml up` adds an anonymized instance on `:3940` that routes all traffic through Tor with Snowflake obfuscation. Encrypted DNS via CoreDNS → Cloudflare DoT. Network-level isolation — the private SearXNG instance runs on an internal-only Docker network with no route to the internet except through Tor.

## Search engines

AgentSearch delegates engine support to the connected SearXNG instance. The authoritative list for a running stack is:

```bash
curl "http://localhost:3939/engines"
```

The bundled `searxng/settings.example.yml` explicitly enables 25 engines, including best-effort Google/Startpage/Yahoo entries plus Brave, Bing, DuckDuckGo, Google Scholar, Semantic Scholar, arXiv, Crossref, OpenAlex, PubMed, Bing News, Reuters, Wikinews, Wikipedia, Wikidata, Hugging Face, Reddit, Hacker News, Stack Overflow, GitHub, Docker Hub, and Lobsters.

Run `./scripts/prepare-searxng.sh` to create ignored local runtime files at `searxng/settings.yml` and `searxng/settings.tor.yml` with generated SearXNG instance secrets. Do not commit those generated files.

Because SearXNG is configured with `use_default_settings: true`, your live instance may expose additional enabled engines from the installed SearXNG catalog. Use the `engines=` query parameter to request specific engines, and use `/engines` to verify what is available in that deployment.

Strategy modes can also use direct no-key providers for vertical search. These do not require paid search APIs: GitHub repository search, MDN search, Docker Hub search, PyPI package metadata, Wikipedia, Wikidata, Hacker News, arXiv, Crossref, OpenAlex, and Semantic Scholar are called directly when a mode selects them. SearXNG remains the broad-web provider for Google/Bing/Brave/DuckDuckGo-style engines, but Google, Startpage, Yahoo, and Reddit are best-effort explicit sources rather than defaults because they are commonly blocked or empty.

## Why not just use SearXNG directly?

SearXNG finds pages. AgentSearch fetches and reads them, scores and deduplicates the results, caches them, scrubs prompt injections, detects paywalls, and falls back through several extraction strategies when the first one fails — in one API call. It also logs fetch outcomes so you can see which strategies and domains are actually working.

| | AgentSearch | Tavily | Exa | SerpAPI | Raw SearXNG |
|---|---|---|---|---|---|
| **Cost** | Free | $0.005/query | $0.001/query | $50/mo | Free |
| **Self-hosted** | ✅ | ❌ | ❌ | ❌ | ✅ |
| **Content extraction** | Multi-strategy + browser renderer | Basic | Basic | ❌ | ❌ |
| **Deduplication** | Cross-engine | ❌ | ❌ | ❌ | ❌ |
| **Prompt injection scrubbing** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Adaptive (failure analysis)** | ✅ (evolver) | ❌ | ❌ | ❌ | ❌ |
| **Tor anonymization** | Optional | ❌ | ❌ | ❌ | Manual |

## Endpoints

### Search

| Endpoint | Method | What it does |
|---|---|---|
| `/search` | GET | Multi-engine web search with deduplication and scoring |
| `/search/strategy` | GET | Named search modes: general, code, academic, news, private, reference, community |
| `/search/deep` | GET | Server-side query expansion — runs variations in parallel, fuses results |
| `/search/extract` | GET | Search + inline content extraction in one call |
| `/search/jobs` | GET | Job search across LinkedIn, Indeed, Glassdoor, ZipRecruiter |
| `/search/policy` | GET | Policy and regulatory document search |
| `/search/sources` | GET | Source discovery with institutional filtering |
| `/search/sources/institutions` | GET | List source registry institutions |
| `/search/stats` | GET | Query statistics and cache metrics |
| `/news` | GET | Structured multi-source news with reliable defaults and explicit engine overrides |

### Content extraction

| Endpoint | Method | What it does |
|---|---|---|
| `/read` | GET | Escalating extraction for any URL |
| `/read/batch` | POST | Concurrent multi-URL extraction in one request |
| `/providers/browser/fetch` | GET | Ephemeral browser render/extract for JS-rendered target pages |

The extraction chain escalates through strategies until one succeeds:

1. Direct fetch + smart content selectors
2. Readability scoring (paragraph density vs link density)
3. User-agent rotation (Chrome/Safari/Firefox/Edge signatures)
4. Browser render/extract for JS-rendered target pages
5. Wayback Machine (CDX API → latest snapshot)
6. Google Cache
7. Search-about fallback (find coverage elsewhere)
8. Custom adapters (pluggable Python modules from disk)
9. PDF extraction (pdfplumber)
10. YouTube transcript (yt-dlp)

Every request gets SSRF protection, prompt injection detection, paywall detection, and content length caps automatically. The browser renderer uses an ephemeral context, blocks high-cost resource types by default, and reports CAPTCHA/challenge pages instead of trying to bypass them. It is for rendering target pages, not for scraping blocked search-result pages.

### Adaptation

| Endpoint | Method | What it does |
|---|---|---|
| `/adapt/report` | POST | Report a fetch failure for a URL |
| `/adapt/stats` | GET | View adaptation metrics and failure patterns |
| `/adapt/evolve` | POST | Trigger an adaptation cycle — analyzes failures, tunes config |

### Infrastructure

| Endpoint | Method | What it does |
|---|---|---|
| `/health` | GET | Health check (API + SearXNG status) |
| `/engines` | GET | List available search engines and their status |
| `/providers/health` | GET | Summarize provider health from recorded live attempts |
| `/providers/stats` | GET | Rolling provider/SearXNG attempt telemetry |

## Quick examples

### Search with content extraction

```bash
curl "http://localhost:3939/search/extract?q=python+async+patterns&count=3"
```

Returns search results with extracted content inline — no second round-trip to `/read`.

### Strategy search

```bash
curl "http://localhost:3939/search/strategy?q=fetch+api&mode=code&count=5"
curl "http://localhost:3939/search?q=AI+regulation&mode=academic&count=5"
curl "http://localhost:3939/search?q=Python&mode=reference&count=5"
```

Modes validate or call only their declared sources instead of falling back silently: `general` tries Bing first, then uses DuckDuckGo/Brave and direct reference/community providers only when more coverage is needed; `code` uses direct GitHub, MDN, Docker Hub, and PyPI providers; `academic` uses direct arXiv, Crossref, OpenAlex, and Semantic Scholar providers; `news` uses Reuters, Bing News, DuckDuckGo News, and Wikinews through SearXNG; `reference` uses direct Wikipedia and Wikidata providers; `community` uses direct Hacker News; `private` avoids broad general web engines.

Each strategy response includes `meta.engine_attempts` with source/provider, query, raw result count, latency, and upstream errors so blocked or empty providers stay visible.

Provider telemetry is available without running another probe:

```bash
curl "http://localhost:3939/providers/health"
curl "http://localhost:3939/providers/stats"
```

Telemetry is in-memory and reflects live attempts since the API process started. It tracks attempts, successes, empty-result rate, errors, latency, last error, and last success per direct provider or SearXNG pack.

### Deep search (query expansion)

```bash
curl "http://localhost:3939/search/deep?q=ethon+industrial+ai+platform&count=10"
```

Server-side query variation + parallel execution + result fusion. Surfaces results that flat `/search` misses.

### Read a URL (escalating extraction)

```bash
curl "http://localhost:3939/read?url=https://example.com/paywalled-article"
```

```json
{
  "url": "https://example.com/paywalled-article",
  "content": "Full article text extracted via Wayback Machine...",
  "strategy": "wayback",
  "chars": 4821,
  "cached": false,
  "strategies_tried": ["direct", "readability", "ua_rotation", "wayback"]
}
```

### Browser render a JS page

```bash
curl "http://localhost:3939/providers/browser/fetch?url=https://example.com/app&max_links=20"
```

Returns rendered text, page title, final URL, extracted links, trust metadata, and `challenge_detected=true` if the page is a CAPTCHA or bot challenge.

### Batch read

```bash
curl -X POST "http://localhost:3939/read/batch" \
  -H "Content-Type: application/json" \
  -d '{"urls": ["https://a.com", "https://b.com", "https://c.com"]}'
```

### Python SDK

```bash
pip install agentsearch-client
```

```python
from agentsearch import AgentSearch

client = AgentSearch()  # defaults to localhost:3939
results = client.search("manufacturing OEE best practices", count=5)
for r in results.results:
    print(f"{r.title} — {r.url}")

# Content extraction
page = client.read("https://example.com/article")
print(page.content[:500])

# Browser-rendered extraction for JS-heavy target pages
rendered = client.browser_fetch("https://example.com/app", max_links=20)
print(rendered.title, rendered.links[:3])

# Batch read
pages = client.read_batch(["https://a.com", "https://b.com"])
print(f"{pages.successful}/{pages.total} succeeded")
```

For authenticated instances, pass `token=...` or use `AGENT_SEARCH_TOKEN`,
`AGENTSEARCH_TOKEN`, `credentials/agent-search-token.txt`, or
`~/.config/agent-search/token`.

### LangChain tool

```python
from langchain.tools import tool
import requests

@tool
def web_search(query: str) -> str:
    """Search the web using AgentSearch."""
    resp = requests.get("http://localhost:3939/search", params={"q": query, "count": 5})
    return "\n".join(
        f"- {r['title']}: {r['url']}\n  {r['snippet']}"
        for r in resp.json()["results"]
    )
```

### MCP server (Claude Desktop, Cursor, Windsurf)

```bash
pip install mcp httpx
python mcp-server/server.py
```

For authenticated instances, set `AGENT_SEARCH_TOKEN` or run:

```bash
python mcp-server/server.py --token "change-me"
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

See [`mcp-server/README.md`](mcp-server/README.md) for details.

## Private stack (Tor + encrypted DNS)

The optional private stack adds a fully anonymized search path:

```
┌──────────┐    ┌──────────────┐    ┌───────────────┐    ┌──────────┐
│  :3940   │───▶│ api-private  │───▶│ searxng-priv  │───▶│   Tor    │──▶ Internet
│ (agent)  │    │ (FastAPI)    │    │ (SearXNG)     │    │(Snowflake│
└──────────┘    └──────────────┘    └───────────────┘    │ + obfs4) │
                                                          └──────────┘
                 All containers use CoreDNS → Cloudflare DoT
                 tor-internal network: no direct egress possible
```

**What this gives you:**
- Your ISP sees TLS to Cloudflare (DNS) and WebRTC-looking traffic (Snowflake). Not search queries.
- The private SearXNG instance lives on an internal-only Docker network with no internet route except through Tor.
- Port 3939 = direct (fast), port 3940 = anonymized (slower, private).

**Setup:**

```bash
./scripts/prepare-searxng.sh
docker compose -f docker-compose.yml -f examples/compose.private.yml up -d --build
```

All private stack configs live in [`examples/`](examples/) — copy and customize as needed.

## Architecture

```
Port 3939 (direct)                    Port 3940 (Tor-anonymized)
     │                                      │
     ▼                                      ▼
┌─────────┐                          ┌─────────────┐
│   API   │                          │ api-private  │
│(FastAPI) │                          │  (FastAPI)   │
├─────────┤                          ├─────────────┤
│ dedup   │  ┌─────────────────┐     │ same code   │  ┌───────────────┐
│ scoring │  │    SearXNG      │     │ Tor egress  │  │ SearXNG-priv  │
│ cache   │──│ Google, Bing,   │     │ only        │──│ (tor-internal │
│ scrub   │  │ DDG, Brave,     │     └─────────────┘  │  network)     │
│ killchn │  │ /engines list   │                       └───────┬───────┘
│ trust   │  └─────────────────┘                               │
│ evolver │                                              ┌─────┴─────┐
└─────────┘                                              │    Tor    │
     │                                                   │ Snowflake │
     ▼                                                   │  + obfs4  │
┌─────────┐                                              └───────────┘
│ CoreDNS │──▶ Cloudflare DoT (encrypted DNS)
└─────────┘
```

### Key modules (6,700 LOC)

| Module | LOC | What it does |
|---|---|---|
| `killchain.py` | 1016 | Escalating content extraction and browser-render fallback |
| `browser_renderer.py` | 320 | Ephemeral browser rendering, extraction, and challenge detection |
| `main.py` | 920 | FastAPI app, 17 endpoints, auth, rate limiting |
| `source_tracer.py` | 620 | Source provenance tracking and citation chains |
| `scrubber.py` | 539 | Prompt injection detection and content sanitization |
| `source_library.py` | 310 | Curated institutional source registry |
| `domain_trust.py` | 311 | Domain trust scoring (TLD, age, reputation) |
| `evolver.py` | 301 | Adaptation engine — failure analysis → config tuning |
| `content_cache.py` | 241 | URL-keyed content cache with TTL |
| `query_expansion.py` | 201 | Server-side query variation and fusion |

Plus: 5 pluggable adapters (Cloudflare bypass, Medium, 403 handler, parse error recovery, empty content fallback), MCP server, Python SDK, test suite.

## Case study: 0 → 17 frameworks per hunt

A real autonomous research agent ("the wolf") uses every AgentSearch endpoint. Before AgentSearch was wired in correctly, the agent's hand-rolled SearXNG client silently 401'd on three of four engines. Every hunt on a low-profile entity returned **0 frameworks**.

After: **17 frameworks per hunt, 7/7 gaps closed.** Same agent, same model, same prompts. The difference was the search infrastructure underneath.

→ Full walkthrough: [`case-studies/wolf.md`](case-studies/wolf.md)

## Configuration

Environment variables (set in `docker-compose.yml` or `.env`):

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG instance URL |
| `SEARXNG_IMAGE` | pinned SearXNG digest | SearXNG container image; override only when intentionally upgrading |
| `PYTHON_BASE_IMAGE` | pinned Python digest | API Docker base image; override only when intentionally upgrading |
| `COREDNS_IMAGE` | pinned CoreDNS digest | Private-stack DNS image |
| `SOCAT_IMAGE` | pinned socat digest | Private-stack TCP forwarder image |
| `TOR_BASE_IMAGE` | pinned Debian digest | Private-stack Tor proxy base image |
| `CACHE_TTL` | `3600` | Cache duration in seconds |
| `RATE_LIMIT` | `60` | Max requests per minute |
| `SQLITE_TIMEOUT` | `1.0` | SQLite lock wait timeout in seconds for query stats |
| `FETCH_LOG_RETENTION_DAYS` | `30` | Delete fetch analytics rows older than this many days during SQLite maintenance |
| `QUERY_LOG_RETENTION_DAYS` | `30` | Delete query analytics rows older than this many days during SQLite maintenance |
| `SQLITE_MAINTENANCE_INTERVAL_SECONDS` | `3600` | Interval for expired content-cache cleanup, log retention, and `PRAGMA optimize` |
| `SQLITE_VACUUM_MIN_DELETED_ROWS` | `1000` | Run `VACUUM` only when a maintenance pass deletes at least this many rows; set `0` to disable threshold vacuum |
| `AGENT_SEARCH_TOKEN` | *(empty)* | Bearer token for auth (optional) |
| `ADAPTERS_DIR` | `/app/adapters` | Path to pluggable adapter modules |

## Limitations and security notes

- Search engines, news engines, rate limits, and failure modes depend on the connected SearXNG instance. `/engines` is the live source of truth.
- Bearer auth is a simple local API gate, not a multi-user authorization system. Treat `AGENT_SEARCH_TOKEN` as a shared service token.
- Rate limiting is in memory. It resets on restart and is per API process.
- Query statistics use local SQLite with WAL and a bounded lock timeout. For high-volume multi-worker deployments, move query logging to an external database or telemetry backend.
- The MCP package intentionally bounds its `mcp` dependency to the tested 1.27.x line. Upgrade deliberately and run the package/CI checks before publishing.
- Content extraction validates the starting URL and every redirect hop before fetching redirected content, but fetched third-party pages are still untrusted and are scrubbed before being returned.
- Google Cache is unreliable because public cache availability changes frequently.
- The Tor/private stack is intentionally slower than direct search.

## Development

```bash
pip install -r requirements.txt
SEARXNG_URL=http://localhost:8080 uvicorn app.main:app --reload --port 3939

# Run tests
pytest tests/
```

## Release and GitHub governance

- `Test` runs on every push and pull request.
- `CodeQL` runs on push, pull request, and a weekly schedule.
- Dependabot watches Python packages, Dockerfiles, and GitHub Actions.
- Version releases are created from semantic tags such as `v2.0.1` or by manually running the `Release` workflow with a tag input.
- Update [`CHANGELOG.md`](CHANGELOG.md) before creating a release tag.

## Contributing

1. Fork → branch → commit → PR.

Issues and PRs welcome. If you're building an agent that needs search, this is for you.

## License

The root AgentSearch API, SDK, Docker stack, and docs are MIT licensed. The MCP server under `mcp-server/` is AGPL-3.0 licensed; see [mcp-server/LICENSE](mcp-server/LICENSE).
