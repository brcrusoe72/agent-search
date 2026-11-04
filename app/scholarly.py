"""Bounded scholarly queries and relevance-first ranking, independent of call order."""
from __future__ import annotations

import math
import re
from urllib.parse import unquote, urlparse

from app.dedup import _normalize_engines, _normalize_url, clean_result_url
from app.models import SearchResult

_ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?"
_STOP = {"a", "an", "the", "and", "or", "of", "to", "in", "for", "on", "with", "is", "are"}


def words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", text.casefold())) - _STOP


def arxiv_query(query: str, *, title_only: bool = True) -> dict[str, str]:
    """Treat input as text, not executable arXiv query syntax; preserve exact IDs."""
    identifier = query.strip().removeprefix("arXiv:").removeprefix("arxiv:")
    parsed = urlparse(identifier)
    if parsed.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        identifier = re.sub(r"^/(?:abs|pdf)/", "", parsed.path).removesuffix(".pdf")
    if re.fullmatch(_ARXIV_ID, identifier):
        return {"id_list": identifier}
    tokens = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:32]
    phrase = " ".join(tokens)
    terms = [token for token in tokens if token.casefold() not in _STOP] or tokens
    conjunction = " AND ".join(f'all:"{token}"' for token in terms)
    return {"search_query": f'ti:"{phrase}"' if title_only or not tokens else conjunction}


def _identities(row: dict) -> set[str]:
    url = str(row.get("url") or "")
    identities = {"url:" + _normalize_url(url)}
    doi = str(row.get("doi") or "").strip()
    parsed = urlparse(url)
    if not doi and parsed.hostname in {"doi.org", "dx.doi.org"}:
        doi = unquote(parsed.path.lstrip("/"))
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I).casefold()
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        identities.add("doi:" + doi)
    arxiv = str(row.get("arxiv_id") or "")
    if not arxiv and parsed.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
        arxiv = re.sub(r"^/(?:abs|pdf)/", "", parsed.path).removesuffix(".pdf")
    if doi.startswith("10.48550/arxiv."):
        arxiv = doi.removeprefix("10.48550/arxiv.")
    if re.fullmatch(_ARXIV_ID, arxiv, flags=re.I):
        identities.add("arxiv:" + re.sub(r"v\d+$", "", arxiv.casefold()))
    return identities


def rank_academic(raw_results: list[dict], *, query: str) -> list[SearchResult]:
    """Merge verified identifiers, never titles; citations are only a small prior.

    Provider metadata stays internal. Scores are heuristics, not confidence.
    Each provider gets its own position counter, so sequential calls are fair.
    """
    groups: list[dict] = []
    positions: dict[tuple[str, ...], int] = {}
    for row in raw_results:
        if not row.get("url"):
            continue
        engines = _normalize_engines(row.get("engines"))
        provider = tuple(engines)
        position = positions.get(provider, 0)
        positions[provider] = position + 1
        identities = _identities(row)
        matches = [group for group in groups if group["ids"] & identities]
        group = {"ids": identities, "rows": [row], "engines": list(engines), "position": position}
        for previous in matches:
            group["ids"] |= previous["ids"]
            group["rows"] += previous["rows"]
            group["engines"] += [e for e in previous["engines"] if e not in group["engines"]]
            group["position"] = min(group["position"], previous["position"])
            groups.remove(previous)
        groups.append(group)

    terms = words(query)
    results = []
    for group in groups:
        def relevance(row):
            title_text = str(row.get("title") or "")
            title = words(title_text)
            authors = words(" ".join(row.get("authors") or []))
            coverage = len(terms & (title | authors)) / max(len(terms), 1)
            precision = len(terms & title) / max(len(title), 1)
            title_phrase = " ".join(re.findall(r"[^\W_]+", title_text.casefold()))
            query_phrase = " ".join(re.findall(r"[^\W_]+", query.casefold()))
            exact_phrase = bool(title_phrase and f" {title_phrase} " in f" {query_phrase} ")
            return .6 * coverage + .25 * precision + .15 * exact_phrase

        row = max(group["rows"], key=relevance)
        citations = max((r.get("citation_count", 0) for r in group["rows"]
                         if isinstance(r.get("citation_count", 0), int)), default=0)
        score = (.85 * relevance(row) + .08 * min(math.log10(1 + max(0, citations)) / 5, 1)
                 + .05 * min(len(group["engines"]) / 4, 1) + .02 / (1 + group["position"]))
        results.append(SearchResult(title=row.get("title", ""), url=clean_result_url(row["url"]),
                                    snippet=row.get("content", row.get("snippet", "")),
                                    engines=group["engines"], score=round(score, 4), position=0))
    results.sort(key=lambda result: result.score, reverse=True)
    for i, result in enumerate(results, 1):
        result.position = i
    return results
