"""No-key direct search providers used by AgentSearch strategy modes."""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx


DEFAULT_PROVIDER_HEADERS = {
    "Accept": "application/json, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
    "User-Agent": "AgentSearch/2.0 (+https://github.com/brcrusoe72/agent-search)",
}


@dataclass
class ProviderResponse:
    """Provider results plus diagnostics preserved into API metadata."""

    results: list[dict]
    provider: str
    upstream_status: str = "ok"
    upstream_errors: list[str] = field(default_factory=list)
    unresponsive_engines: list[str] = field(default_factory=list)
    response_time_ms: float = 0.0


class SearchProvider:
    """Base class for direct providers that do not require paid API access."""

    name: str = ""
    engine_name: str = ""
    timeout: float = 15.0

    async def search(self, client: httpx.AsyncClient, query: str, count: int) -> ProviderResponse:
        start = time.monotonic()
        try:
            results = await self._search(client, query, count)
            status = "ok"
            errors: list[str] = []
        except Exception as exc:
            results = []
            status = "error"
            errors = [f"{self.engine_name}: {_safe_text(exc)}"]
        return ProviderResponse(
            results=results,
            provider=self.name,
            upstream_status=status,
            upstream_errors=errors,
            unresponsive_engines=[self.engine_name] if status == "error" else [],
            response_time_ms=round((time.monotonic() - start) * 1000, 1),
        )

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        raise NotImplementedError

    def _result(self, title: object, url: object, snippet: object = "") -> dict:
        title_text = _safe_text(title)
        url_text = _safe_text(url)
        snippet_text = _clean_snippet(_safe_text(snippet))
        if not title_text:
            title_text = url_text
        return {
            "title": title_text,
            "url": url_text,
            "content": snippet_text,
            "engines": [self.engine_name],
        }


def provider_by_name(name: str) -> SearchProvider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown provider: {name}") from exc


def _safe_text(value: object, limit: int = 1000) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _clean_snippet(value: str) -> str:
    value = unescape(value)
    value = re.sub(r"<[^>]+>", " ", value)
    return _safe_text(value, limit=2000)


def _first(value: object) -> object:
    if isinstance(value, list) and value:
        return value[0]
    return value


def _json_items(data: Any, *path: str) -> list:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return []
        current = current.get(key)
    return current if isinstance(current, list) else []


def _abstract_from_openalex_index(index: object) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, values in index.items():
        if not isinstance(values, list):
            continue
        for position in values:
            if isinstance(position, int):
                positions.append((position, str(word)))
    return " ".join(word for _, word in sorted(positions))


def _doi_url(doi: object) -> str:
    doi_text = _safe_text(doi)
    if not doi_text:
        return ""
    if doi_text.startswith("http://") or doi_text.startswith("https://"):
        return doi_text
    return f"https://doi.org/{doi_text}"


class MDNProvider(SearchProvider):
    name = "mdn"
    engine_name = "mdn"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://developer.mozilla.org/api/v1/search",
            params={"q": query, "locale": "en-US"},
            headers=DEFAULT_PROVIDER_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        documents = data.get("documents") or data.get("results") or []
        if not isinstance(documents, list):
            return []

        results: list[dict] = []
        for item in documents[: count * 3]:
            if not isinstance(item, dict):
                continue
            raw_url = item.get("mdn_url") or item.get("url") or item.get("path") or item.get("slug")
            if not raw_url:
                continue
            url = str(raw_url)
            if url.startswith("/"):
                url = urljoin("https://developer.mozilla.org", url)
            elif not url.startswith(("http://", "https://")):
                url = urljoin("https://developer.mozilla.org/en-US/docs/", url)
            results.append(self._result(
                item.get("title") or item.get("slug") or url,
                url,
                item.get("summary") or item.get("excerpt") or item.get("description") or "",
            ))
        return results


class GitHubProvider(SearchProvider):
    name = "github"
    engine_name = "github"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "per_page": min(max(count * 3, 1), 50), "sort": "stars"},
            headers={
                **DEFAULT_PROVIDER_HEADERS,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in _json_items(resp.json(), "items")[: count * 3]:
            if not isinstance(item, dict):
                continue
            snippet = item.get("description") or ""
            stars = item.get("stargazers_count")
            if isinstance(stars, int):
                snippet = f"{snippet} Stars: {stars}".strip()
            results.append(self._result(item.get("full_name"), item.get("html_url"), snippet))
        return results


class DockerHubProvider(SearchProvider):
    name = "docker_hub"
    engine_name = "docker hub"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://hub.docker.com/v2/search/repositories/",
            params={"query": query, "page_size": min(max(count * 3, 1), 50)},
            headers=DEFAULT_PROVIDER_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in _json_items(resp.json(), "results")[: count * 3]:
            if not isinstance(item, dict):
                continue
            repo_name = _safe_text(item.get("repo_name") or item.get("name"))
            if not repo_name:
                continue
            url = f"https://hub.docker.com/r/{repo_name}"
            snippet = item.get("short_description") or item.get("description") or ""
            pulls = item.get("pull_count")
            if isinstance(pulls, int):
                snippet = f"{snippet} Pulls: {pulls}".strip()
            results.append(self._result(repo_name, url, snippet))
        return results


class ArxivProvider(SearchProvider):
    name = "arxiv"
    engine_name = "arxiv"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": min(max(count * 3, 1), 50),
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            headers={**DEFAULT_PROVIDER_HEADERS, "Accept": "application/atom+xml"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results: list[dict] = []
        for entry in root.findall("atom:entry", ns)[: count * 3]:
            title = entry.findtext("atom:title", default="", namespaces=ns)
            url = entry.findtext("atom:id", default="", namespaces=ns)
            summary = entry.findtext("atom:summary", default="", namespaces=ns)
            results.append(self._result(title, url, summary))
        return results


class CrossrefProvider(SearchProvider):
    name = "crossref"
    engine_name = "crossref"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://api.crossref.org/works",
            params={"query": query, "rows": min(max(count * 3, 1), 50)},
            headers=DEFAULT_PROVIDER_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in _json_items(resp.json(), "message", "items")[: count * 3]:
            if not isinstance(item, dict):
                continue
            url = item.get("URL") or _doi_url(item.get("DOI"))
            if not url:
                continue
            title = _first(item.get("title")) or item.get("DOI") or url
            container = _first(item.get("container-title")) or ""
            abstract = item.get("abstract") or ""
            snippet = f"{container}. {abstract}".strip(". ")
            results.append(self._result(title, url, snippet))
        return results


class OpenAlexProvider(SearchProvider):
    name = "openalex"
    engine_name = "openalex"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://api.openalex.org/works",
            params={"search": query, "per-page": min(max(count * 3, 1), 50)},
            headers=DEFAULT_PROVIDER_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in _json_items(resp.json(), "results")[: count * 3]:
            if not isinstance(item, dict):
                continue
            location = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
            url = item.get("doi") or location.get("landing_page_url") or item.get("id")
            if not url:
                continue
            snippet = _abstract_from_openalex_index(item.get("abstract_inverted_index"))
            results.append(self._result(item.get("display_name") or item.get("title"), url, snippet))
        return results


class SemanticScholarProvider(SearchProvider):
    name = "semantic_scholar"
    engine_name = "semantic scholar"

    async def _search(self, client: httpx.AsyncClient, query: str, count: int) -> list[dict]:
        resp = await client.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": query,
                "limit": min(max(count * 3, 1), 50),
                "fields": "title,url,abstract,authors,year,externalIds,citationCount,openAccessPdf",
            },
            headers=DEFAULT_PROVIDER_HEADERS,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results: list[dict] = []
        for item in _json_items(resp.json(), "data")[: count * 3]:
            if not isinstance(item, dict):
                continue
            external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
            open_pdf = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
            url = item.get("url") or open_pdf.get("url") or _doi_url(external.get("DOI"))
            if not url:
                continue
            citations = item.get("citationCount")
            snippet = item.get("abstract") or ""
            if isinstance(citations, int):
                snippet = f"{snippet} Citations: {citations}".strip()
            results.append(self._result(item.get("title"), url, snippet))
        return results


PROVIDERS: dict[str, SearchProvider] = {
    "mdn": MDNProvider(),
    "github": GitHubProvider(),
    "docker_hub": DockerHubProvider(),
    "arxiv": ArxivProvider(),
    "crossref": CrossrefProvider(),
    "openalex": OpenAlexProvider(),
    "semantic_scholar": SemanticScholarProvider(),
}
