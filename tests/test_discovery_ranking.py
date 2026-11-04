"""Regressions for paper identity, source-order bias and exact documentation pages."""
import asyncio

import httpx
import pytest

from app.dedup import deduplicate_with_scoring
from app.scholarly import arxiv_query, rank_academic
from app.search_providers import ArxivProvider, CrossrefProvider, OpenAlexProvider, SemanticScholarProvider
from app import main
from app.search_providers import ProviderResponse
from tests.test_api import AppClient, isolated_app_state  # noqa: F401


def paper(title, url, engine, **metadata):
    return dict(title=title, url=url, engines=[engine], content="", **metadata)


def test_arxiv_title_is_quoted_and_every_keyword_scoped():
    query = arxiv_query('Attention Is All You Need')["search_query"]
    assert 'ti:"Attention Is All You Need"' in query
    keywords = arxiv_query('Attention Is All You Need', title_only=False)["search_query"]
    assert keywords == 'all:"Attention" AND all:"All" AND all:"You" AND all:"Need"'
    injected = arxiv_query('attention" OR cat:cs.AI)')["search_query"]
    assert 'cat:cs.AI' not in injected
    assert injected.count('"') % 2 == 0
    assert arxiv_query('!!!') == {"search_query": 'ti:""'}


@pytest.mark.parametrize("query,identifier", [
    ("arXiv:1706.03762v7", "1706.03762v7"),
    ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762"),
    ("hep-th/9901001", "hep-th/9901001"),
])
def test_arxiv_identifier_lookup(query, identifier):
    assert arxiv_query(query) == {"id_list": identifier}


def test_paper_identity_merges_arxiv_versions_and_doi_but_not_title_collisions():
    rows = [paper("Attention Is All You Need", "https://arxiv.org/abs/1706.03762v7", "arxiv"),
            paper("Attention Is All You Need", "https://doi.org/10.48550/arXiv.1706.03762", "openalex"),
            paper("Attention Is All You Need", "https://arxiv.org/pdf/1706.03762.pdf", "semantic scholar"),
            paper("Attention Is All You Need", "https://doi.org/10.1234/different-work", "crossref")]
    results = rank_academic(rows, query="Attention Is All You Need")
    assert len(results) == 2
    assert set(results[0].engines) == {"arxiv", "openalex", "semantic scholar"}
    assert results[1].engines == ["crossref"]


def test_distinct_legacy_arxiv_categories_do_not_merge():
    rows = [paper("Same title", "https://arxiv.org/abs/hep-th/9901001", "arxiv"),
            paper("Same title", "https://arxiv.org/abs/hep-ph/9901001", "arxiv")]
    assert len(rank_academic(rows, query="Same title")) == 2


def test_original_title_and_author_beat_extensions_and_unrelated_high_citations():
    rows = [paper("Random Forests: Finding Quasars", "https://doi.org/10.1234/quasars", "arxiv",
                  authors=["Leo Breiman"], citation_count=63),
            paper("Something unrelated", "https://doi.org/10.1234/unrelated", "crossref",
                  citation_count=10**9, content_override="Random Forests Leo Breiman"),
            paper("Random Forests", "https://doi.org/10.1023/a:1010933404324", "openalex",
                  authors=["Leo Breiman"], citation_count=125000)]
    rows[1]["content"] = rows[1].pop("content_override")
    results = rank_academic(rows, query="Random Forests Leo Breiman")
    assert results[0].url.endswith("1010933404324")
    assert results[-1].title == "Something unrelated"


def test_provider_order_does_not_penalize_later_results():
    rows = [paper("Attention Is All You Need", "https://doi.org/10.1234/other", "crossref"),
            paper("Attention Is All You Need", "https://arxiv.org/abs/1706.03762", "openalex", citation_count=100000)]
    forward = rank_academic(rows, query="Attention Is All You Need")
    reverse = rank_academic(list(reversed(rows)), query="Attention Is All You Need")
    assert [(r.url, r.score) for r in forward] == [(r.url, r.score) for r in reverse]


def test_same_doi_across_provider_urls_merges():
    rows = [paper("Random Forests", "https://link.springer.com/article/10.1023/A:1010933404324", "crossref",
                  doi="10.1023/A:1010933404324"),
            paper("Random Forests", "https://doi.org/10.1023/a:1010933404324", "openalex")]
    assert len(rank_academic(rows, query="Random Forests")) == 1


def test_exact_title_beats_reordered_words_even_without_citations():
    rows = [paper("Is Attention All You Need?", "https://doi.org/10.1234/other", "crossref", citation_count=100),
            paper("Attention Is All You Need", "https://arxiv.org/abs/1706.03762", "arxiv")]
    assert rank_academic(rows, query="Attention Is All You Need")[0].engines == ["arxiv"]


@pytest.mark.parametrize("query", ["1706.03762", "arXiv:1706.03762v7", "https://arxiv.org/abs/1706.03762",
                                  "10.48550/arxiv.1706.03762", "https://doi.org/10.48550/arxiv.1706.03762"])
def test_identifier_match_beats_highly_cited_unrelated_paper(query):
    rows = [paper("Unrelated", "https://doi.org/10.1234/other", "crossref", citation_count=1000000),
            paper("Attention Is All You Need", "https://arxiv.org/abs/1706.03762v7", "arxiv")]
    assert rank_academic(rows, query=query)[0].engines == ["arxiv"]


@pytest.mark.parametrize("filters", [{"domain": "arxiv.org"}, {"exclude_domains": "doi.org"}])
def test_academic_api_retains_eligible_url_before_identity_merge(monkeypatch, filters):
    class Provider:
        def __init__(self, name):
            self.name = name
        async def search(self, client, query, count):
            rows = ([paper("Attention Is All You Need", "https://arxiv.org/abs/1706.03762", "arxiv")]
                    if self.name == "arxiv" else
                    [paper("Attention Is All You Need", "https://doi.org/10.48550/arxiv.1706.03762", self.name)])
            return ProviderResponse(results=rows, provider=self.name)
    monkeypatch.setattr(main, "provider_by_name", Provider)
    monkeypatch.setattr(main, "http_client", object())
    response = AppClient().get('/search', params={"q": "Attention Is All You Need", "mode": "academic", "count": 1, **filters})
    assert response.status_code == 200
    assert response.json()["results"][0]["url"] == "https://arxiv.org/abs/1706.03762"


def test_exact_documentation_page_beats_home_search_listing_and_mirror():
    rows = [paper("SQLite Documentation", "https://sqlite.org/index.html", "bing"),
            paper("Search SQLite Documentation", "https://sqlite.org/search?q=write+ahead+logging", "bing"),
            paper("Write-Ahead Logging - SQLite", "https://sqlite.org/wal.html", "duckduckgo"),
            paper("Write-Ahead Logging - SQLite documentation", "https://sqlite.org.evil.com/wal.html", "bing")]
    for row in rows:
        row["content"] = "SQLite write ahead logging documentation"
    results = deduplicate_with_scoring(rows, query="SQLite write ahead logging documentation")
    assert results[0].url == "https://sqlite.org/wal.html"
    assert len(results) == 4


@pytest.mark.parametrize("provider,payload", [
    (CrossrefProvider(), {"message": {"items": [{"title": ["Random Forests"], "DOI": "10.1023/a:1010933404324",
        "author": [{"given": "Leo", "family": "Breiman"}], "is-referenced-by-count": 42}]}}),
    (OpenAlexProvider(), {"results": [{"display_name": "Random Forests", "doi": "https://doi.org/10.1023/a:1010933404324",
        "authorships": [{"author": {"display_name": "Leo Breiman"}}], "cited_by_count": 42}]}),
    (SemanticScholarProvider(), {"data": [{"title": "Random Forests", "url": "https://www.semanticscholar.org/paper/abc",
        "externalIds": {"DOI": "10.1023/a:1010933404324"}, "authors": [{"name": "Leo Breiman"}], "citationCount": 42}]}),
])
def test_provider_preserves_paper_metadata(provider, payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
            response = await provider.search(client, "Random Forests Leo Breiman", 3)
        assert response.upstream_status == "ok"
        assert response.results[0]["authors"] == ["Leo Breiman"]
        assert response.results[0]["doi"].endswith("10.1023/a:1010933404324")
        assert response.results[0]["citation_count"] == 42
    asyncio.run(run())


def test_arxiv_preserves_authors_and_identifier():
    xml = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A paper</title><id>https://arxiv.org/abs/1706.03762</id><author><name>Ashish Vaswani</name></author></entry></feed>'
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=xml))) as client:
            response = await ArxivProvider().search(client, "A paper", 3)
        assert response.results[0]["authors"] == ["Ashish Vaswani"]
        assert response.results[0]["url"].endswith("1706.03762")
    asyncio.run(run())


@pytest.mark.parametrize("query,first_status,first_body,expected_calls", [
    ("transformer attention", 200, '<feed xmlns="http://www.w3.org/2005/Atom"/>', 2),
    ("1706.03762", 200, '<feed xmlns="http://www.w3.org/2005/Atom"/>', 1),
    ("transformer attention", 429, 'rate limited', 1),
])
def test_arxiv_keyword_fallback_is_bounded_and_does_not_retry_rate_limits(monkeypatch, query, first_status, first_body, expected_calls):
    calls, pauses = [], []
    async def pause(seconds):
        pauses.append(seconds)
    monkeypatch.setattr('app.search_providers.asyncio.sleep', pause)
    def handle(request):
        calls.append(dict(request.url.params))
        return httpx.Response(first_status if len(calls) == 1 else 200,
                              text=first_body if len(calls) == 1 else '<feed xmlns="http://www.w3.org/2005/Atom"/>')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            response = await ArxivProvider().search(client, query, 3)
        assert len(calls) == expected_calls
        assert pauses == ([3] if expected_calls == 2 else [])
        if expected_calls == 2:
            assert calls[0]["search_query"] == 'ti:"transformer attention"'
            assert calls[1]["search_query"] == 'all:"transformer" AND all:"attention"'
        if first_status == 429:
            assert response.upstream_status == "error"
    asyncio.run(run())
