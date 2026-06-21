# agentsearch

Python client for the [AgentSearch](https://github.com/brcrusoe72/agent-search) API — self-hosted, multi-engine web search for AI agents and RAG pipelines.

**Zero dependencies required.** Uses `urllib` out of the box; install `httpx` for connection pooling.

## Install

```bash
pip install agentsearch-client

# Optional: better HTTP performance
pip install agentsearch-client[fast]
```

## Quick Start

```python
from agentsearch import AgentSearch

client = AgentSearch("http://localhost:3939")

# Search the web
results = client.search("python async patterns", count=5)
for r in results.results:
    print(f"{r.title}: {r.url}")
```

## API Reference

### `AgentSearch(base_url, timeout=30.0, token=None)`

Create a client instance. If `httpx` is installed, it uses connection pooling automatically.

```python
client = AgentSearch("http://localhost:3939", timeout=15.0)
```

For an authenticated AgentSearch instance, pass a token or set it locally:

```python
client = AgentSearch("http://localhost:3939", token="change-me")
```

If `token` is omitted, the client tries `AGENT_SEARCH_TOKEN`,
`AGENTSEARCH_TOKEN`, `credentials/agent-search-token.txt`, and
`~/.config/agent-search/token`.

Supports context manager:

```python
with AgentSearch("http://localhost:3939") as client:
    results = client.search("query")
```

---

### `client.search(query, *, count=10, engines=None, domain=None, exclude_domains=None, fetch=False)`

Multi-engine web search with deduplication and scoring.

```python
# Basic search
results = client.search("python async patterns", count=5)

# Search + extract page content
results = client.search("python async", count=5, fetch=True)
for r in results.results:
    print(r.content[:200] if r.content else "No content")

# Filter to specific domain
results = client.search("site reliability", domain="google.com")
```

---

### `client.search_strategy(query, *, mode="general", count=10, domain=None, exclude_domains=None, fetch=False)`

Named strategy search. Modes are `general`, `code`, `academic`, `news`, `private`, `reference`, and `community`. Code, academic, reference, and community modes use direct no-key providers where available, while broad web/news engines still run through the configured AgentSearch/SearXNG stack. Reddit remains best-effort and is not selected by default because live anonymous requests are often blocked.

```python
results = client.search_strategy("fetch api", mode="code", count=5)
print(results.meta.engine_attempts)
```

`client.search(...)` and `client.search_extract(...)` also accept `mode=...`.

---

### `client.search_extract(query, *, count=5, engines=None, mode=None)`

Search and automatically extract content from top results via the kill chain.

```python
results = client.search_extract("RAG pipeline best practices", count=3)
for r in results.results:
    print(f"{r.title}\n{r.content[:500]}\n")
```

---

### `client.deep_search(query, *, count=10)`

Multi-query fusion search — generates 3-5 query variations, merges and deduplicates.

```python
results = client.deep_search("manufacturing OEE best practices", count=10)
print(f"Queries used: {results.meta.queries_used}")
```

---

### `client.search_policy(query, *, count=10, fetch=False)`

Policy-optimized search with think tank boosting and junk filtering.

```python
results = client.search_policy("South China Sea maritime disputes", fetch=True)
```

---

### `client.read(url, *, max_chars=None, skip_cache=False)`

Extract readable content from any URL using escalating strategies (direct -> readability -> UA rotation -> browser render -> Wayback Machine -> Google Cache -> search-about -> custom adapters -> PDF -> YouTube).

```python
content = client.read("https://example.com/article")
print(content.content)
print(f"Strategy: {content.strategy}, Chars: {content.chars}")
```

---

### `client.browser_fetch(url, *, max_chars=None, max_links=None, timeout_ms=None)`

Render a safe target URL in an ephemeral browser context and extract readable text plus links. This is for JS-rendered pages; CAPTCHA/challenge pages are reported, not bypassed.

```python
rendered = client.browser_fetch("https://example.com/app", max_links=20)
print(rendered.title)
print(rendered.content)
print(rendered.links[:3])
```

---

### `client.read_batch(urls, *, max_chars=None)`

Extract content from multiple URLs concurrently (max 20).

```python
results = client.read_batch([
    "https://example.com/page1",
    "https://example.com/page2",
])
print(f"Success: {results.successful}/{results.total}")
```

---

### `client.news(query, *, count=10, engines=None)`

Search news across the default reliable news pack (Reuters, Bing News, DuckDuckGo News, Wikinews) or explicitly selected SearXNG news engines.

```python
articles = client.news("AI agents", count=10)
for a in articles.results:
    print(f"[{a.source}] {a.title} ({a.published})")
```

---

### `client.search_jobs(query, *, location=None, salary_min=None)`

Search jobs across LinkedIn, Indeed, Glassdoor, and ZipRecruiter.

```python
jobs = client.search_jobs("python engineer", location="Chicago", salary_min=120000)
for j in jobs.results:
    sal = f"${j.salary_min:,}-${j.salary_max:,}" if j.salary_min else "Not listed"
    print(f"{j.title} @ {j.source} — {sal}")
```

---

### `client.health()`

Check API health and SearXNG connectivity.

```python
h = client.health()
print(f"Status: {h.status}, Version: {h.version}")
```

### `client.providers_health()`

Summarize provider health from recorded live attempts.

```python
health = client.providers_health()
print(health["status"])
```

### `client.providers_stats()`

Return rolling in-memory telemetry for direct providers and SearXNG packs.

```python
stats = client.providers_stats()
for provider in stats["providers"]:
    print(provider["source"], provider["name"], provider["success_rate"])
```

---

### Module-level convenience functions

For quick scripts, use module-level functions with a default client:

```python
import agentsearch

agentsearch.configure("http://localhost:3939")

results = agentsearch.search("python")
content = agentsearch.read("https://example.com")
articles = agentsearch.news("AI")
jobs = agentsearch.search_jobs("data engineer")
```

## Error Handling

```python
from agentsearch import AgentSearch, AgentSearchError

client = AgentSearch("http://localhost:3939")

try:
    results = client.search("query")
except AgentSearchError as e:
    print(f"Error: {e}")
    print(f"Status code: {e.status_code}")
    print(f"Detail: {e.detail}")
```

## Why AgentSearch?

| | AgentSearch | Brave API | Google CSE | SerpAPI |
|---|---|---|---|---|
| **Cost** | Free forever | $0.005/query | $5/1K queries | $50/mo |
| **API Key** | None | Required | Required | Required |
| **Setup** | `docker compose up` | Sign up + wait | Console + billing | Sign up + pay |
| **Engines** | 6+ (configurable) | Brave only | Google only | Google only |
| **Self-hosted** | ✅ | ❌ | ❌ | ❌ |
| **Rate limits** | You control | 1 req/sec free | 100/day free | 100/mo free |
| **Deduplication** | Built-in | ❌ | ❌ | ❌ |

## Self-Hosted Server

This package is just the client. To run your own AgentSearch server:

```bash
git clone https://github.com/brcrusoe72/agent-search.git
cd agent-search
docker compose up -d
# API available at http://localhost:3939
```

See the [server repository](https://github.com/brcrusoe72/agent-search) for full setup instructions.

## License

MIT
