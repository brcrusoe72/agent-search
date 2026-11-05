# AgentSearch

Self-hosted search API for AI agents. Layered content extraction with isolated browser rendering. Optional Tor-anonymized stack. No third-party search API keys, no per-query fees, no vendor lock-in. Optional local bearer auth is supported.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![PyPI](https://img.shields.io/pypi/v/agentsearch-client)](https://pypi.org/project/agentsearch-client/)

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
./scripts/prepare-searxng.sh
docker compose up -d
curl "http://localhost:3939/search?q=distributed+consensus+algorithms"
```

You now have a deduplicated, multi-engine search API running on `:3939`.

To enable auth on the standard stack, set a token and recreate the API so it reads the new environment. Keep the token in your private deployment environment for subsequent Compose commands. Pass it on all endpoints except `/health`, `/live`, and `/ready`:

```bash
export AGENT_SEARCH_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker compose up -d --no-deps api
curl -H "Authorization: Bearer $AGENT_SEARCH_TOKEN" \
  "http://localhost:3939/search?q=distributed+consensus+algorithms"
```

For an existing private stack, use the same Compose files and project name used at startup, and recreate both `api` and `api-private` with the token set. Setting a client token alone does not enable server authentication.

Prefer not to use Docker for the API server?

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
./scripts/install-native.sh
./scripts/run-native.sh
```

Native mode requires Python 3.11+ and a reachable SearXNG instance with JSON output enabled. It stores AgentSearch state in `./data`. See [Native Install](docs/native-install.md).

Existing Docker installations: follow [the upgrade procedure](docs/upgrading.md) for data ownership, network changes, and migration of existing SearXNG settings.

## Verify

Run the self-contained test suite:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt pytest requests build wheel "setuptools>=68"
pip install -e sdk
# MCP has a separate Pydantic requirement; keep it isolated.
python -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -e mcp-server pytest
.venv-mcp/bin/python -m pytest tests/test_mcp_contract.py -q
.venv-mcp/bin/python -m pip check
./scripts/prepare-searxng.sh
pytest tests -q
python -m compileall app adapters mcp-server/agent_search_mcp scripts sdk -q
python -m pip check
```

The self-contained tests mock SearXNG and do not require Docker or a running local service. Validate the container configuration and pinned engine templates separately with Docker available:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml -f examples/compose.private.yml config --quiet
docker build -t agent-search-api:test .
python scripts/check_searxng_config.py
```

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

For a small, on-demand live pilot, use the eight fixed cases covering general,
code, academic, and news search. Set `AGENT_SEARCH_TOKEN` when auth is enabled:

```bash
python scripts/check_search_pilot.py benchmark --label YOUR_DEPLOYED_COMMIT-direct --output /tmp/search-pilot.json
python scripts/check_search_pilot.py reliability --seconds 300 --label YOUR_DEPLOYED_COMMIT-direct --output /tmp/search-reliability.json
```

Use `--base-url http://127.0.0.1:3940` and a separate output/label for Tor.
The benchmark makes eight search requests and records cache state, timing,
upstream diagnostics, and the first three results. Academic targets use paper
identifiers; news needs human relevance and publication-date review. A successful
script exit means the requests completed, not that the relevance targets passed.

The reliability pilot probes readiness every 30 seconds for five minutes, then
warms one query and sends seven requests at concurrency 1, 2, and 4. It checks
that those requests use cached, consistent results. This measures a short health
window and cached API handling, not multi-day uptime or uncached search capacity.
The runner allows at most 12 benchmark cases and ten minutes per reliability run;
it creates no schedule. Reports can contain queries and third-party result text.

---

## What it does

AgentSearch wraps [SearXNG](https://github.com/searxng/searxng) with a FastAPI layer that adds the pieces agents usually need on top of raw search: deduplication, cross-engine scoring, content extraction, query expansion, domain trust scoring, prompt injection scrubbing, and failure-pattern analysis.

**Standard stack** — `docker compose up` gives you search on `:3939`.

**Private stack** — `docker compose -f docker-compose.yml -f examples/compose.private.yml up` adds an instance on `:3940` that routes search and content-fetch traffic through Tor with Snowflake transport. Encrypted DNS via CoreDNS → Cloudflare DoT. Network-level isolation — the private SearXNG instance runs on an internal-only Docker network with no route to the internet except through Tor.

## Search engines

AgentSearch delegates engine support to the connected SearXNG instance. The authoritative list for a running stack is:

```bash
curl "http://localhost:3939/engines"
```

The bundled settings configure broad web, scholarly, news, reference, and developer sources using SearXNG 2026.9.5 (`searxng/searxng@sha256:55e1fa15a63ff04e79e213e6aa2837549877b0c6d60757cdb633ae9111cb5fea`). Both templates are checked against the pinned image in CI. Engine registration does not guarantee live availability: Google, Brave, Reuters, and other sources can still return blocks or rate limits. Inspect `/engines` and response diagnostics for the current instance. Existing runtime files need the [configuration upgrade steps](docs/upgrading.md#searxng-202695-configuration); changing the image alone does not migrate them.

Run `./scripts/prepare-searxng.sh` to create ignored local runtime files at `searxng/settings.yml` and `searxng/settings.tor.yml` with generated SearXNG instance secrets. Do not commit those generated files.

Because SearXNG is configured with `use_default_settings: true`, your live instance may expose additional enabled engines from the installed SearXNG catalog. Use the `engines=` query parameter to request specific engines, and use `/engines` to verify what is available in that deployment.

Strategy modes can also use direct no-key providers for vertical search. These do not require paid search APIs: GitHub repository search, MDN search, Docker Hub search, PyPI package metadata, Wikipedia, Wikidata, Hacker News, arXiv, Crossref, OpenAlex, and Semantic Scholar are called directly when a mode selects them. SearXNG remains the broad-web provider for Google/Bing/Brave/DuckDuckGo-style engines, but Google, Startpage, Yahoo, and Reddit are best-effort explicit sources rather than defaults because they are commonly blocked or empty.

## Why not just use SearXNG directly?

SearXNG finds pages. AgentSearch fetches and reads them, scores and deduplicates the results, caches them, scrubs prompt injections, detects paywalls, and falls back through several extraction strategies when the first one fails — in one API call. It also logs fetch outcomes so you can see which strategies and domains are actually working.

You operate the containers, storage, and network connection. AgentSearch does not charge per query; hosting and maintenance still have costs. The evolver analyzes recorded failures and returns recommendations; it does not automatically modify the running system.

## Related projects

For narrower tasks, these separate projects in the same GitHub account may be useful:

- **[scholar](https://github.com/brcrusoe72/scholar-cli)**: scholarly search.
- **[agent-read](https://github.com/brcrusoe72/agent-read)**: URL-to-text extraction.
- **[agent-web](https://github.com/brcrusoe72/agent-web)**: browser interaction.

See each repository for installation instructions and documentation.

## Endpoints

### Search

| Endpoint | Method | What it does |
|---|---|---|
| `/search` | GET | Multi-engine web search with deduplication and scoring |
| `/search/strategy` | GET | Named search modes: general, browser, code, academic, news, private, reference, community |
| `/search/deep` | GET | Server-side query expansion — runs variations in parallel, fuses results |
| `/search/extract` | GET | Search + inline content extraction in one call |
| `/search/jobs` | GET | Job search across LinkedIn, Indeed, Glassdoor, ZipRecruiter |
| `/search/policy` | GET | Policy and regulatory document search |
| `/search/sources` | GET | Source discovery with institutional filtering |
| `/search/sources/institutions` | GET | List source registry institutions |
| `/search/stats` | GET | Query statistics and cache metrics |
| `/news` | GET | Structured multi-source news with upstream diagnostics and explicit engine overrides |

### Content extraction

| Endpoint | Method | What it does |
|---|---|---|
| `/read` | GET | Escalating extraction for any URL |
| `/read/batch` | POST | Concurrent multi-URL extraction in one request |
| `/providers/browser/fetch` | GET | Ephemeral browser render/extract for JS-rendered target pages |

The extraction chain tries applicable strategies until one succeeds. PDF, YouTube, and site-specific handlers may run before the general fallbacks below:

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

The bundled extraction paths validate destinations and redirects, bound fetched content, and apply content scrubbing and paywall detection. Scrubbing is heuristic: returned text remains untrusted. In the Docker stack, browser rendering runs as a dedicated non-root service on an internal-only network, uses an ephemeral sandboxed context, and can reach targets only through the validating fetch proxy. It reports CAPTCHA/challenge pages instead of trying to bypass them. Custom adapters execute local Python code and must be reviewed before installation.

### Adaptation

| Endpoint | Method | What it does |
|---|---|---|
| `/adapt/report` | POST | Report a fetch failure for a URL |
| `/adapt/stats` | GET | View adaptation metrics and failure patterns |
| `/adapt/evolve` | POST | Analyze failures and return recommendations (read-only) |

### Infrastructure

| Endpoint | Method | What it does |
|---|---|---|
| `/health` | GET | Health check (API + SearXNG status) |
| `/live` | GET | Cheap process liveness check; never calls upstream search |
| `/ready` | GET | Bounded SearXNG readiness check; returns 503 when unusable |
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

Modes validate or call only their declared sources instead of falling back silently: `general` queries DuckDuckGo, Brave, and Bing together, then tries browser discovery when results are insufficient, followed by direct reference/community providers if needed; `code` uses direct GitHub, MDN, Docker Hub, and PyPI providers; `academic` uses direct arXiv, Crossref, OpenAlex, and Semantic Scholar providers; `news` uses Reuters, Bing News, and DuckDuckGo News through SearXNG; `reference` uses direct Wikipedia and Wikidata providers; `community` uses direct Hacker News; `private` avoids broad general web engines.

Ordinary `/search` and `mode=general` balance query-term coverage with engine agreement, source priors, and upstream position. Documentation queries prioritize matching page titles and relevant hostnames over homepages and search listings; this is a ranking signal, not proof that a site is official. Scores are ranking heuristics, not probabilities of correctness. Query matching improves some irrelevant-result failures but does not establish that a result answers the question. Upstream blocks remain visible even when another engine supplies results.

Academic search tries a quoted title query for arXiv, then one explicit keyword
query if the title lookup is empty, with a three-second pause between calls.
An arXiv ID uses a direct identifier lookup. Crossref uses bibliographic queries.
Ranking uses titles and authors, with small capped citation and provider-agreement
signals. Provider call order does not give earlier sources a ranking advantage,
and provider diversity cannot displace a more relevant paper. Matching DOI and
arXiv identifiers merge duplicate records; identical titles alone do not.
Author names remain visible in result snippets. Provider rate limits can still
reduce coverage; inspect `meta.engine_attempts` before interpreting an empty result.

In `mode=code`, explicit Python package or PyPI lookups preserve exact package
matches ahead of provider diversity. Other code and academic results still need
relevance review. Empty responses caused by upstream errors are not cached for
the normal result TTL, so the next request can observe recovery.

Browser discovery uses the existing sandboxed Chromium worker to read public
DuckDuckGo search pages, then Bing if more results are needed. It needs no paid
API, subscription, or API key. It is automatically available when
`BROWSER_SERVICE_URL` is configured; set `BROWSER_DISCOVERY_ENABLED=0` on the API
to disable it. Ordinary `/search` and `mode=general` invoke it when the initial
web results are fewer than requested or SearXNG is unavailable. Explicit
`engines=` requests and specialized/private modes keep their declared sources.
Use `mode=browser` to search through the browser directly, independently of
SearXNG:

```bash
curl "http://localhost:3939/search?q=SQLite+write+ahead+logging+documentation&mode=browser&count=3"
```

Each browser search visits at most two search pages (up to 20 results per page),
with a bounded worker deadline and a shared browser concurrency limit. It
extracts organic result cards, decodes tracking URLs, validates destinations,
and applies domain exclusions before ranking. The browser keeps its sandbox,
validating proxy and network isolation. Generic page rendering still blocks
search-engine URLs; only the dedicated search path can visit its two fixed
search endpoints. It does not log in or solve challenges. Blocks, empty pages,
timeouts and temporary cooldowns remain visible in `meta.engine_attempts` under
`browser_attempts`; SearXNG errors remain visible even after successful recovery.
There are no vendor query credits, but external sites may still restrict traffic.
The normal result cache prevents repeated successful searches; empty failures
are not cached for the normal result TTL. This feature does not build a local
web index or crawl every result page. Use `fetch=true` to extract returned pages.

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

Server-side query variation + parallel execution + result fusion. It can find additional results, but query expansion does not guarantee better relevance.

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
`AGENTSEARCH_TOKEN`, `~/.openclaw/workspace/credentials/agent-search-token.txt`,
or `~/.config/agent-search/token`. CWD credential files are never loaded.
Remote bearer-token connections require HTTPS unless the SDK caller explicitly
sets `allow_insecure_http=True`.

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
python3 -m venv .venv-mcp
.venv-mcp/bin/python -m pip install -e ./mcp-server
.venv-mcp/bin/agent-search-mcp
```

For authenticated instances, set `AGENT_SEARCH_TOKEN` or run:

```bash
.venv-mcp/bin/agent-search-mcp --token "change-me"
```

Add to Claude Desktop config:

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/absolute/path/to/agent-search/.venv-mcp/bin/agent-search-mcp"
    }
  }
}
```

Install from the repository root. The separate environment applies the MCP package's declared dependency bounds without changing the API environment. Use the absolute executable path in your client config; on Windows it is `.venv-mcp\Scripts\agent-search-mcp.exe`. See [`mcp-server/README.md`](mcp-server/README.md) for details.

## Private stack (Tor + encrypted DNS)

The optional private stack routes search and content-fetch traffic through Tor:

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
- The bundled configuration uses encrypted DNS and Tor with Snowflake transport. Network observers can still see connection metadata; this is not a guarantee of anonymity.
- The private SearXNG instance lives on an internal-only Docker network with no internet route except through Tor.
- Port 3939 = direct; port 3940 = Tor-routed and typically slower. Providers still receive your query text, and AgentSearch stores local query/fetch records and cached content.

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

### Key modules

| Module | What it does |
|---|---|
| `killchain.py` | Escalating content extraction and browser-render fallback |
| `browser_renderer.py` | Ephemeral browser rendering, extraction, and challenge detection |
| `main.py` | FastAPI endpoints, auth, and rate limiting |
| `source_tracer.py` | Source provenance tracking and citation chains |
| `scrubber.py` | Heuristic prompt injection detection and content sanitization |
| `source_library.py` | Curated institutional source registry |
| `domain_trust.py` | Heuristic domain trust scoring |
| `evolver.py` | Failure analysis and read-only recommendations |
| `content_cache.py` | URL-keyed content cache with TTL |
| `query_expansion.py` | Server-side query variation and fusion |

Plus: 5 pluggable adapters (Cloudflare bypass, Medium, 403 handler, parse error recovery, empty content fallback), MCP server, Python SDK, test suite.

## Integration case study

The historical [research-agent walkthrough](case-studies/wolf.md) describes an authentication repair and an integration pattern. Its reported outcomes are specific to that system; the underlying agent and evaluation data are not included here. Treat it as an integration example, not a reproducible benchmark for the current release.

## Configuration

Environment variables (set in `docker-compose.yml` or `.env`):

| Variable | Default | Description |
|---|---|---|
| `SEARXNG_URL` | `http://searxng:8080` | SearXNG instance URL |
| `SEARXNG_IMAGE` | pinned SearXNG digest (2026.9.5) | SearXNG container image; override only after a live engine comparison on the candidate |
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
- The MCP package supports `mcp>=1.27.2,<2.3.0`. CI tests both 1.27.2 and 2.2.0 in isolated environments, including real stdio discovery, successful invocation, and error semantics. Upgrade deliberately and run the package/CI checks before publishing.
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
