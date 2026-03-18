# AgentSearch v2.0 — Unified Information Tool

## The Problem

AgentSearch (944 LOC) is a thin wrapper around SearXNG. CEO's fetch.py (546 LOC) is a
battle-tested content extraction engine with SSRF protection, prompt injection filtering,
paywall bypass via archive cascades, and pluggable adapters. CEO's evolver.py (482 LOC)
is a self-improvement system that analyzes performance, detects failing patterns, and
auto-tunes configuration.

These systems don't know about each other. AgentSearch can find pages but can't read them
well. CEO can read pages brilliantly but only CEO benefits.

## The Solution

Roll CEO's kill chain and evolver into AgentSearch so every system — CEO, market-intel,
my direct use, any future system — gets robust content extraction and self-improvement
through one API.

## Architecture

```
AgentSearch v2.0 (localhost:3939)
├── /search          — find things (existing, improved)
├── /search/deep     — deep research (real query expansion)
├── /search/extract  — search + read combined (uses /read backend)
├── /read            — NEW: kill chain content extraction
│   ├── Strategy 1: Direct fetch + smart content selectors
│   ├── Strategy 2: Readability scoring (paragraph density)
│   ├── Strategy 3: UA rotation (Chrome/Safari/Firefox/Edge)
│   ├── Strategy 4: Wayback Machine (CDX API → snapshot)
│   ├── Strategy 5: Google Cache
│   ├── Strategy 6: Search-about fallback (find coverage elsewhere)
│   ├── Strategy 7: Custom adapters (pluggable, disk-loaded)
│   ├── Strategy 8: PDF extraction (pdfplumber)
│   └── Strategy 9: YouTube transcript (yt-dlp)
├── /read/batch      — NEW: read multiple URLs concurrently
├── /news            — NEW: structured multi-source news
├── /adapt           — NEW: evolver endpoint (self-improvement)
│   ├── POST /adapt/report   — report a fetch failure
│   ├── GET  /adapt/stats    — view adaptation metrics
│   └── POST /adapt/evolve   — trigger self-improvement cycle
├── /health          — health check (existing)
├── /engines         — engine list (existing)
└── /search/stats    — query stats (existing)
```

## Build Plan (7 phases, each independently testable)

### Phase 1: Port Kill Chain → `/read` endpoint
**Files:** `app/killchain.py` (new), `app/main.py` (add route)
- Port all 9 strategies from CEO's fetch.py
- Port SSRF protection (`_is_safe_url`)
- Port prompt injection sanitization (`_sanitize_content`)
- Port paywall detection (`_is_paywalled`)
- Add content caching (fetched content cached by URL, separate from search cache)
- Add response model: `ReadResponse` with url, content, strategy_used, chars, cached
- Make it async (CEO's version is sync requests — upgrade to httpx)
- Add `/read/batch` for concurrent multi-URL extraction
- **Dependencies:** beautifulsoup4, pdfplumber, yt-dlp (system)

### Phase 2: Upgrade `/search/extract` to use kill chain
**Files:** `app/main.py`
- Replace basic `fetch_multiple_contents` with kill chain backend
- `/search/extract` now returns genuinely extracted content, not shallow HTTP GETs

### Phase 3: Real query expansion for `/search/deep`
**Files:** `app/query_expansion.py` (rewrite)
- Current implementation is trivial synonym matching
- New: semantic reformulation — rephrase as question, add domain context,
  generate opposing viewpoint query, broaden/narrow scope
- No LLM needed — rule-based but much smarter than current

### Phase 4: `/news` endpoint
**Files:** `app/main.py`, `app/models.py`
- Dedicated news search using SearXNG news engines:
  Google News, Bing News, Reuters, Yahoo News, DuckDuckGo News,
  Brave News, Startpage News, Qwant News, Wikinews
- Structured output: title, source, date, url, snippet, category
- Deduplication across news engines
- Optional topic filtering

### Phase 5: Adapter system → `/adapt`
**Files:** `app/adapters/` (new dir), `app/evolver.py` (new), `app/main.py`
- Port CEO's adapter loading system (dynamic Python module loading from disk)
- Port obstacle tracking (which URLs/domains fail, why, how often)
- `POST /adapt/report` — any consumer reports "I couldn't read this URL"
- `GET /adapt/stats` — view fetch success rates by domain, strategy effectiveness
- `POST /adapt/evolve` — analyze patterns, tune blocked domains, suggest new adapters
- Store obstacle data in SQLite alongside query logs

### Phase 6: Content caching layer
**Files:** `app/content_cache.py` (new), integrated into killchain
- SQLite-backed content cache (URL → extracted text, strategy, timestamp)
- Configurable TTL (default 24h for articles, 1h for news)
- Cache hit/miss stats exposed in `/adapt/stats`
- Prevents re-fetching the same article across CEO runs, my queries, etc.

### Phase 7: Docker + deploy
- Update `requirements.txt` with new deps
- Update `Dockerfile` to install yt-dlp system package
- Volume mount for adapters directory (persist across rebuilds)
- Volume mount for SQLite data
- Rebuild and test
- Update TOOLS.md

## Dependency Changes

```
# New in requirements.txt
pdfplumber==0.11.0
lxml==5.1.0

# System packages in Dockerfile  
# yt-dlp (pip install, not apt)
```

## What Gets Removed From CEO

After AgentSearch v2.0 ships:
- CEO's `fetch.py` becomes a thin wrapper: `requests.get("localhost:3939/read?url=...")`
- CEO's adapters directory migrates to AgentSearch
- CEO's evolver pattern-checking integrates with `/adapt/stats`
- CEO keeps its wolf-model hunting logic, just delegates fetching

## Security Carried Forward

All of these from CEO's fetch.py, preserved:
- SSRF protection (scheme, localhost, private IP, DNS rebinding)
- Blocked domains list (shorteners, suspicious TLDs)
- Prompt injection pattern matching + redaction
- Content length caps (15K chars)
- Paywall detection heuristic
- Safe URL validation before every network request

## Success Criteria

- `curl localhost:3939/read?url=<paywalled-article>` returns content
- `curl localhost:3939/read/batch` handles 10 URLs concurrently in <30s
- `curl localhost:3939/news?q=AI` returns structured news from 8+ sources
- `curl localhost:3939/search/deep?q=...` generates 3-4 genuinely different queries
- `/adapt/stats` shows strategy effectiveness breakdown
- All existing endpoints still work unchanged
- CEO pipeline runs using AgentSearch `/read` instead of local fetch.py
