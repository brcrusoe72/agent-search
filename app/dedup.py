"""Result deduplication and scoring across multiple search engines."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.models import SearchResult


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup comparison (strip trailing slash, www, fragments)."""
    parsed = urlparse(url)
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
        url = r.get("url", "")
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
