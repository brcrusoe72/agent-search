# Case Study: Agentic Research Wolf

> *Same agent. Same model. Same prompts. The only thing that changed was the search infrastructure underneath. Frameworks per hunt went from 0 to 17.*

This is a real-world walkthrough of how a single autonomous research agent uses every endpoint AgentSearch exposes — and the empirical before/after numbers from the day the agent was wired in correctly.

The agent ("the wolf") is part of an internal research system that runs hunts on questions like *"What is Ethon's industrial AI platform doing?"* It scans existing knowledge, identifies gaps, searches the web, scores results, fetches content, extracts structured frameworks, and synthesizes a territorial update. It runs unattended, multiple rounds per hunt, with a budget cap.

It is the kind of agent AgentSearch was built for.

---

## The five-phase pipeline

```
SEED ─▶ DISAMBIGUATE ─▶ TERRITORY ─▶ ┌─ STALK ─▶ BITE ─▶ KILL ─┐ ─▶ FEED
                                     │                          │
                                     └────── reformulate ───────┘
                                          (round 2..N if zero yield)
```

| Phase | What it does | AgentSearch endpoint |
|---|---|---|
| **SEED** | Detect URL in question, fetch the canonical entity profile | `/read` |
| **DISAMBIGUATE** | Resolve ambiguous bare-name questions ("Ethon" → company, eagle, butterfly?) | (LLM only — no search) |
| **TERRITORY** | Identify gaps and seed search queries grounded in the entity profile | (LLM only) |
| **STALK** (round 1) | Multi-engine discovery + parallel academic + content extraction | `/search`, `/search/extract` |
| **STALK** (round 2+) | Server-side query-variation fusion when round 1 produced zero high-value yield | `/search/deep` |
| **STALK** (optional) | Recent news / press for company-type entities | `/news` |
| **BITE** | Quick 1KB-content commit/abandon decision with mismatch detection | `/read` (small slice) |
| **KILL** | Full-text extraction from committed sources | `/read`, `/read/batch` |
| **FEED** | LLM synthesis: gaps closed, organs found, next-hunt recommendations | (LLM only) |

The wolf hits AgentSearch dozens of times per hunt. Every endpoint pulls its weight.

---

## Why each endpoint matters

### `/search` — round-1 discovery

Multi-engine deduplication is the difference between *"6 of 8 SearXNG engines silently 401'd"* and *"Bing + DuckDuckGo + Startpage + Brave all responded, deduplicated to 16 unique results in 0.3s."* The wolf doesn't have to write engine-fallback logic — it just gets the merged ranking with cross-engine agreement scores.

### `/search/deep` — round-2 reformulation

When round 1 produced zero high-value frameworks, the wolf used to reformulate queries client-side via an LLM call ("generate 5 new queries from a different angle"). That works, but it costs ~$0.01 per reformulation and the LLM doesn't know which queries are server-side cheap.

`/search/deep` does query variation **server-side**, runs all variations in parallel, and fuses the results. On the test hunt, it surfaced a Siemens partnership signal as result #1 that flat `/search` missed entirely. No equivalent exists in OpenAI's `web_search` tool.

### `/search/extract` — content in the discovery call

The bite phase needs ~1KB per result to decide commit-or-abandon. Without `/search/extract`, that's `n` round-trips to `/read` after `/search`. With `/search/extract`, content arrives inline. Round-1 latency drops 60%+.

### `/news` — recent-events channel

Generic web search returns evergreen pages. For a research question about *a company* — funding rounds, partnerships, product launches — recent-events density is much higher in news engines (Google News, Bing News). On the test hunt, `/news` surfaced a 2026 Industrial AI Summit press release that named the target alongside its competitors. `/search` didn't.

### `/read` — the 9-strategy kill chain

The wolf used to maintain a hand-rolled `kill_chain()` function: direct fetch → readability → UA rotation → Wayback → Google Cache → … 200+ lines of fragile edge-case handling. `/read` replaces all of it with a single authenticated GET. PDF binary detection, Wikipedia-coverage detection, paywall handling — all server-side. The wolf's `content_adapter.py` collapsed from 482 lines of strategy fallbacks to a 30-line wrapper.

### `/read/batch` — parallel multi-source acquisition

When the wolf commits 5 sources for full extraction, `/read/batch` fetches all 5 concurrently in one request. No client-side `ThreadPoolExecutor`, no per-request connection setup, no rate-limit dance with the server. One POST, one response, content for everything that succeeded.

---

## Before / after — empirical

The wolf was running with a hand-rolled SearXNG client that hit `localhost:3939/search` without auth. Three of four engines silently returned `401 Unauthorized`. Only OpenAlex (a separate academic API, no auth) responded. Every hunt on a low-profile entity returned zero frameworks because OpenAlex's last-resort title-search was matching name collisions ("Ethon" → blueberry dormancy paper, HR well-being literature review, animal-behaviour information-theory paper).

After wiring in `Authorization: Bearer ${AGENTSEARCH_TOKEN}`:

| Metric | Before (silent 401s) | After (authenticated) |
|---|---|---|
| Web results per query | **0** | 8–16 |
| Frameworks extracted per hunt | **0** | **17** |
| Organs (contradictory/surprising findings) | 0 | 1 |
| Meat (high-value frameworks) | 0 | 11 |
| Bone (confirmatory) | 0 | 5 |
| Gap closure | **0 / 7** | **7 / 7** |
| Founder identified by name | no | yes (Julian Senoner, ETH Zurich PhD) |
| Architecture mapped | no | 3-layer (Data / Model / Application, UNS, causal AI) |
| Synthesis quality | "negative result" diagnostic | Observer grade B / Signal A / Reliability A |
| Cost per hunt | $0.18 (wasted) | $0.21 (productive) |

Same agent. Same model. Same prompts. Same budget. The difference was the search infrastructure underneath.

The "before" wasn't a different design — it was the same design talking to a search layer that silently refused to answer. Once authenticated, the agent's entire pipeline came alive.

---

## The integration — 200 LoC

Drop-in client. Token resolved from env, then credentials file, then config dir. All endpoints accessible:

```python
# tools/agentsearch_client.py — 200 lines, zero dependencies beyond `requests`

import os, requests
from pathlib import Path
from typing import Iterable

AGENTSEARCH_URL = os.environ.get("AGENTSEARCH_URL", "http://localhost:3939")

def _load_token() -> str | None:
    if (t := os.environ.get("AGENTSEARCH_TOKEN", "").strip()):
        return t
    for p in [Path.home() / ".config/agent-search/token",
              Path.home() / ".local/share/agent-search/token"]:
        if p.exists():
            t = p.read_text().strip()
            if t:
                return t
    return None

_TOKEN = _load_token()

def _headers() -> dict:
    h = {"Accept": "application/json"}
    if _TOKEN:
        h["Authorization"] = f"Bearer {_TOKEN}"
    return h

def search(q: str, count: int = 10) -> list[dict]:
    r = requests.get(f"{AGENTSEARCH_URL}/search",
                     params={"q": q[:160], "count": count},
                     headers=_headers(), timeout=20)
    return r.json().get("results", []) if r.ok else []

def search_deep(q: str, count: int = 10) -> list[dict]:
    r = requests.get(f"{AGENTSEARCH_URL}/search/deep",
                     params={"q": q[:160], "count": count},
                     headers=_headers(), timeout=30)
    return r.json().get("results", []) if r.ok else []

def search_extract(q: str, count: int = 5) -> list[dict]:
    r = requests.get(f"{AGENTSEARCH_URL}/search/extract",
                     params={"q": q[:160], "count": count},
                     headers=_headers(), timeout=30)
    return r.json().get("results", []) if r.ok else []

def news(q: str, count: int = 5) -> list[dict]:
    r = requests.get(f"{AGENTSEARCH_URL}/news",
                     params={"q": q[:160], "count": count},
                     headers=_headers(), timeout=20)
    return r.json().get("results", []) if r.ok else []

def read(url: str, max_chars: int = 15000) -> str | None:
    r = requests.get(f"{AGENTSEARCH_URL}/read",
                     params={"url": url, "max_chars": max_chars},
                     headers=_headers(), timeout=30)
    return (r.json().get("content") if r.ok else None)

def read_batch(urls: Iterable[str], max_chars: int = 8000) -> dict[str, str]:
    r = requests.post(f"{AGENTSEARCH_URL}/read/batch",
                      json={"urls": list(urls)[:20], "max_chars": max_chars},
                      headers={**_headers(), "Content-Type": "application/json"},
                      timeout=60)
    if not r.ok:
        return {}
    return {x["url"]: x.get("content","") for x in r.json().get("results", []) if x.get("content")}
```

Three other modules in the wolf delegate to this one. Everything else stayed the same.

---

## What this means if you're building a research agent

If your agent is silent-failing on web search, you don't need a smarter agent — you need a working search layer. The wolf was already doing the right things: gap analysis, mismatch detection, query reformulation, source-memory accumulation, negative-result synthesis. It looked like a flawed agent because the infrastructure was returning empty results to every call.

AgentSearch isn't a clever optimization. It's a working baseline. The endpoints compose. The auth is real. The kill chain handles the long tail. You get what you'd build if you had three months and didn't want to maintain it.

For agents in production, that's the bar.

---

## Reproducing this

The wolf's source isn't open (yet — it's part of a larger system), but the pattern is repeatable in any research agent:

1. **Wire all your search through one authenticated client.** No raw `requests.get()` calls scattered across modules.
2. **Use `/search` for round 1, `/search/deep` for reformulation rounds.** They're complementary, not redundant.
3. **Mismatch-detect at bite (1KB) phase, not kill phase.** Saves the kill-phase LLM cost on noise pages.
4. **Record dud domains per topic in a SQLite file.** Across hunts, the agent learns which domains are wasted spend for which topics.
5. **Always synthesize, even on zero yield.** A negative-result report tells the operator *why* the hunt failed and what to try next. Silence is worse than failure.

Five disciplines, one search backend. That's the whole recipe.
