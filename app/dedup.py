"""Result deduplication and scoring across multiple search engines."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.models import SearchResult


# Domain authority scores
DOMAIN_AUTHORITY = {
    'arxiv.org': 0.9,
    'wikipedia.org': 0.9,
    'github.com': 0.8,
    'stackoverflow.com': 0.8,
    'docs.python.org': 0.8,
    'developer.mozilla.org': 0.8,
    'medium.com': 0.5,
    'reddit.com': 0.4,
    'quora.com': 0.3,
}


TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "mkt_tok",
    "msclkid",
    "ref",
    "ref_src",
    "spm",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
    "ved",
}


def clean_result_url(url: str) -> str:
    """Remove common tracking parameters while preserving useful query params."""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS and not key.lower().startswith("utm_")
    ]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup comparison (strip trailing slash, www, fragments)."""
    parsed = urlparse(clean_result_url(url))
    host = parsed.hostname or ""
    host = re.sub(r"^www\.", "", host)
    path = parsed.path.rstrip("/")
    return f"{host}{path}"


def deduplicate(raw_results: list[dict]) -> list[SearchResult]:
    """Deduplicate results by URL, scoring by engine count.

    Results that appear in more engines get higher scores.
    Snippets are merged for richer context.
    """
    seen: dict[str, dict] = {}

    for r in raw_results:
        url = clean_result_url(r.get("url", ""))
        if not url:
            continue

        norm = _normalize_url(url)
        engines = r.get("engines", [])
        if isinstance(engines, str):
            engines = [engines]

        if norm in seen:
            existing = seen[norm]
            # Merge engines
            for e in engines:
                if e not in existing["engines"]:
                    existing["engines"].append(e)
            # Keep longer snippet
            snippet = r.get("content", r.get("snippet", ""))
            if len(snippet) > len(existing["snippet"]):
                existing["snippet"] = snippet
        else:
            seen[norm] = {
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("content", r.get("snippet", "")),
                "engines": list(engines),
            }

    # Score by engine count and sort
    results: list[SearchResult] = []
    sorted_items = sorted(seen.values(), key=lambda x: len(x["engines"]), reverse=True)

    for i, item in enumerate(sorted_items):
        results.append(
            SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item["snippet"],
                engines=item["engines"],
                score=round(len(item["engines"]) / max(len(set().union(*(r.get("engines", []) if isinstance(r.get("engines"), list) else [r.get("engines", "")] for r in raw_results))), 1), 2),
                position=i + 1,
            )
        )

    return results


def deduplicate_with_scoring(raw_results: list[dict]) -> list[SearchResult]:
    """Enhanced deduplication with domain authority and position scoring."""
    seen: dict[str, dict] = {}
    
    for i, r in enumerate(raw_results):
        url = clean_result_url(r.get("url", ""))
        if not url:
            continue

        norm = _normalize_url(url)
        engines = r.get("engines", [])
        if isinstance(engines, str):
            engines = [engines]

        if norm in seen:
            existing = seen[norm]
            # Merge engines
            for e in engines:
                if e not in existing["engines"]:
                    existing["engines"].append(e)
            # Keep longer snippet
            snippet = r.get("content", r.get("snippet", ""))
            if len(snippet) > len(existing["snippet"]):
                existing["snippet"] = snippet
            # Keep best position
            existing["best_position"] = min(existing.get("best_position", i), i)
        else:
            seen[norm] = {
                "title": r.get("title", ""),
                "url": url,
                "snippet": r.get("content", r.get("snippet", "")),
                "engines": list(engines),
                "best_position": i,
            }

    # Calculate enhanced scores
    results: list[SearchResult] = []
    
    for item in seen.values():
        # Engine agreement score (0-1)
        engine_score = len(item["engines"]) / len(set().union(*(r.get("engines", []) if isinstance(r.get("engines"), list) else [r.get("engines", "")] for r in raw_results if r.get("engines"))))
        
        # Domain authority score (0-1)
        domain = urlparse(item["url"]).netloc.lower()
        domain_score = DOMAIN_AUTHORITY.get(domain, 0.2)  # Default low score for unknown domains
        
        # Position score (0-1, higher for better positions)
        position_score = 1.0 / (1.0 + item["best_position"] / 10.0)
        
        # Combined score
        final_score = (engine_score * 0.4) + (domain_score * 0.3) + (position_score * 0.3)
        
        results.append(
            SearchResult(
                title=item["title"],
                url=item["url"],
                snippet=item["snippet"],
                engines=item["engines"],
                score=round(final_score, 3),
                position=0,  # Will be set after sorting
            )
        )
    
    # Sort by score and set positions
    results.sort(key=lambda x: x.score, reverse=True)
    for i, result in enumerate(results):
        result.position = i + 1
    
    return results
