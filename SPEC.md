# SPEC: AgentSearch security and integration repair

Status: ACCEPTED
Author: Brian Crusoe (interviewed by Codex)
Date: 2026-08-06

## 1. Job to be done

Repair the current AgentSearch repository in bounded batches so that its public
API, extraction chain, browser renderer, Docker/Tor deployments, Python SDK,
and MCP server can be used without the confirmed control-plane poisoning,
SSRF, provenance, sandboxing, hostname-spoofing, deduplication, ranking, and
MCP compatibility failures. Each batch must pass focused tests before it is
combined with the previous batches. The completed repair must then pass the
whole regression suite and live end-to-end tests against both the direct Docker
stack and the Tor/private stack. Existing API routes and fields remain
compatible; security metadata is added, not hidden.

## 2. Inputs

The repair consumes the real interfaces and data shapes already present in the
repository.

| Field | Type | Where it comes from | Example value | Required |
|---|---|---|---|---|
| `url` | URL string | `/read`, `/providers/browser/fetch`, `/adapt/report` | `https://reuters.com/original` | yes |
| `strategies_tried` | list of strings | `/adapt/report`, fetch log | `["direct", "readability"]` | no |
| `error` | string or null | `/adapt/report`, fetch log | `client supplied` | no |
| `q` | string | search endpoints | `distributed consensus algorithms` | yes for search |
| `engines` | string or list | SearXNG/provider result | `[]` or missing | no from upstream |
| `requested_url` | URL string | extraction request | `https://reuters.com/original` | yes |
| `fetched_url` | URL string | successful extraction strategy | `https://coverage.example/story` | yes on success |
| `AGENT_SEARCH_TOKEN` | secret string | environment/credential file | `redacted-token` | no |
| outbound DNS answers | IPv4/IPv6 addresses | resolver/fetch gateway | `100.64.0.1`, `127.0.0.1`, public address | yes for network fetch |
| deployment route | enum | direct or Tor Compose stack | `direct`, `tor` | yes in live tests |

**Sample rows**

```json
{"url":"https://example.com/page","strategies_tried":["direct"],"error":"client supplied"}
{"url":"https://youtube.com/watch?v=AAA","title":"Video A","engines":["bing"]}
{"url":"https://youtube.com/watch?v=BBB","title":"Video B","engines":["bing"]}
{"url":"https://example.com/a","title":"Missing engines result"}
{"url":"https://rand.org.evil.example/report","title":"Spoofed institution","engines":["bing"]}
```

## 3. Output artifact

The output is a dedicated Git branch, `repair/security-integration`, containing
the implementation, focused regression fixtures, deployment changes, and live
integration tests. It is consumed by repository maintainers and by existing
API, SDK, and MCP users.

For extraction, the backward-compatible response shape is extended as follows:

```json
{
  "url": "https://reuters.com/original",
  "fetched_url": "https://coverage.example/story",
  "derived_from": "https://reuters.com/original",
  "content": "Content fetched from the coverage source...",
  "strategy": "search-about",
  "success": true,
  "trust": {
    "domain": "coverage.example",
    "tier": "standard",
    "score": 0.5,
    "reasons": [],
    "https": true,
    "lookalike_of": null
  },
  "requested_trust": {
    "domain": "reuters.com",
    "tier": "established",
    "score": 1.0,
    "reasons": [],
    "https": true,
    "lookalike_of": null
  }
}
```

For a direct extraction, `url` and `fetched_url` are equal and `derived_from`
is null. Cached results preserve the same provenance fields.

## 4. Rules

- R1. `/adapt/report` is untrusted feedback and can never directly or
  indirectly mutate a runtime blocklist.
- R2. `/adapt/evolve` remains at its existing route and response shape but is
  analysis-only; it never writes files or changes fetch behavior.
- R3. Only authenticated administrative code using verified server-observed
  fetch-attempt evidence may apply a blocklist change. No public auto-apply
  path is added in this repair.
- R4. A URL is fetchable only when its scheme is HTTP(S), DNS resolution
  succeeds, and every resolved IPv4/IPv6 address is globally routable.
- R5. URL validation fails closed for DNS errors, mixed public/non-global
  answers, loopback, private, link-local, reserved, unspecified, multicast,
  and CGNAT addresses.
- R6. Every redirect is revalidated. The network connection must be bound to
  the validated resolution or enforced by the shared outbound fetch proxy so a
  second unconstrained DNS lookup cannot change the destination.
- R7. All ordinary HTML and adapter fetches enforce both declared
  `Content-Length` and actual streamed/decompressed byte limits before parsing.
- R8. Extraction strategies return structured provenance. `url` remains the
  requested URL, `fetched_url` is the actual content source, `derived_from` is
  set for alternate-source content, `trust` describes `fetched_url`, and
  `requested_trust` describes the request target.
- R9. Alternate-source content is never cached or labeled as if it came from
  the requested origin. Cache rows retain all provenance fields.
- R10. Hostname authority matching uses normalized parsed hostnames and matches
  only an exact domain or its subdomain; substrings such as
  `rand.org.evil.example` never match `rand.org`.
- R11. URL deduplication removes known tracking parameters but retains sorted
  meaningful query parameters, so distinct YouTube videos and item IDs remain
  distinct.
- R12. Ranking accepts missing, empty, string, or list engine metadata and
  never divides by zero.
- R13. Existing routes and existing response fields remain available. New
  security/provenance fields are additive, except that unsafe mutation by
  `/adapt/evolve` is removed.
- R14. Chromium rendering runs in a dedicated non-root service with its
  browser sandbox enabled, strict CPU/memory/PID limits, a read-only root
  filesystem where practical, and no unconstrained direct egress.
- R15. The existing browser endpoint remains stable and the API enforces a
  bounded global browser queue and timeout.
- R16. The direct stack labels its network route `direct`; the private stack
  labels it `tor`. Live tests verify the private stack observes a Tor exit and
  cannot perform a deliberate direct-egress probe.
- R17. Liveness is cheap and does not call an upstream search service.
  Readiness may check dependencies, is bounded/cached, and returns HTTP 503
  when unusable. Compatibility for `/health` is retained.
- R18. SDK read and browser models preserve `fetched_url`, `derived_from`,
  `trust`, and `requested_trust`.
- R19. SDK and MCP clients refuse to send a bearer token over non-loopback
  plaintext HTTP unless the caller explicitly opts into insecure transport.
- R20. SDK and MCP credential discovery does not implicitly load a token from
  the current working directory.
- R21. MCP remains on the verified `>=1.27.2,<1.28.0` line for this repair.
  MCP 2.x is not admitted until a separate migration changes the server API.
- R22. MCP failures are returned as protocol-level tool errors rather than
  ordinary successful text.
- R23. Each batch must pass its focused fixtures and all previously completed
  batch fixtures before the next batch begins.
- R24. The final gate runs the complete unit/regression suite and live
  end-to-end direct and Tor/private Docker tests.
- R25. No release is declared successful if any required security fixture,
  direct-stack test, Tor routing assertion, or regression test fails.

## 5. Fixtures

Expected outputs in these files become the human-approved answer key when this
spec is accepted. They must not be weakened merely to make implementation pass.

| ID | Scenario | Input file | Expected output file | Rules exercised |
|---|---|---|---|---|
| F1 | Direct safe extraction with provenance | `tests/fixtures/security/direct_fetch.json` | `tests/fixtures/security/direct_fetch.expected.json` | R4-R9, R13 |
| F2 | Five fabricated adaptation failures | `tests/fixtures/security/evolver_poison.json` | `tests/fixtures/security/evolver_poison.expected.json` | R1-R3 |
| F3 | CGNAT, private, mixed-answer, DNS-failure and rebinding targets | `tests/fixtures/security/ssrf_targets.json` | `tests/fixtures/security/ssrf_targets.expected.json` | R4-R7 |
| F4 | Search-about returns content from a different domain | `tests/fixtures/security/derived_content.json` | `tests/fixtures/security/derived_content.expected.json` | R8-R9, R13, R18 |
| F5 | Institution and quality-domain spoof hostnames | `tests/fixtures/security/hostname_spoof.json` | `tests/fixtures/security/hostname_spoof.expected.json` | R10 |
| F6 | Distinct query-bearing URLs and missing engine metadata | `tests/fixtures/security/ranking_edges.json` | `tests/fixtures/security/ranking_edges.expected.json` | R11-R12 |
| F7 | Browser request under concurrency and hostile navigation | `tests/fixtures/security/browser_limits.json` | `tests/fixtures/security/browser_limits.expected.json` | R14-R15 |
| F8 | SDK/MCP token and provenance contracts | `tests/fixtures/security/client_contracts.json` | `tests/fixtures/security/client_contracts.expected.json` | R18-R22 |
| F9 | Live direct stack | existing live query plus browser/read probes | successful direct-route report | R16-R17, R23-R25 |
| F10 | Live Tor/private stack | existing live query plus exit/direct-egress probes | successful Tor-route report and denied direct probe | R16-R17, R23-R25 |

## 6. Out of scope

- MCP 2.x migration.
- General ranking-tuning or search-quality benchmarking beyond the confirmed
  zero-engine crash and hostname/query correctness defects.
- Rewriting or refreshing the curated geopolitical policy-source library.
- Query-expansion wording and removal of the hardcoded year.
- New search endpoints or broader SDK endpoint parity.
- Publishing releases, PyPI packages, or GHCR images.
- Redesigning the user-facing API.
- Replacing SearXNG or changing its engine catalog.
- Automatic blocklist application of any kind.

## 7. Open questions

None. The repair uses the confirmed decisions above. If live integration
reveals a platform constraint that conflicts with a rule, the spec returns to
the user for an explicit amendment.

## 8. Done condition

The maintainer can start the repaired direct and Tor/private stacks, run one
real search and one real extraction through each, observe correct route and
provenance metadata, and complete the rigorous integration command without
manual database/blocklist repair or any failed required test.

## 9. Constraints

- Work occurs on `repair/security-integration`, based on current `master`.
- The existing `/home/brcxrusoe36/agent-search` checkout and its
  `docs/link-siblings` branch remain untouched.
- Python support remains API 3.11+, SDK 3.9+, and MCP 3.10+.
- Existing API route compatibility is preserved as defined in R13.
- The direct and private Docker Compose forms remain supported.
- Tor/private egress must fail closed rather than silently fall back to direct
  internet access.
- Security tests must be deterministic; live-network assertions are isolated
  in the explicit integration phase.
- New dependencies are avoided unless they are required to enforce a rule and
  cannot be implemented safely with an existing dependency or the standard
  library.
