"""AgentSearch — Python client for the AgentSearch API.

Usage::

    from agentsearch import AgentSearch

    client = AgentSearch("http://localhost:3939")
    results = client.search("python async patterns", count=5)

Or use module-level convenience functions::

    import agentsearch
    agentsearch.configure("http://localhost:3939")
    results = agentsearch.search("python async patterns")
"""

from .client import AgentSearch, AgentSearchError
from .models import (
    BatchReadResponse,
    BrowserFetchResponse,
    BrowserLink,
    HealthResponse,
    JobResult,
    JobSearchResponse,
    NewsResponse,
    NewsResult,
    ReadResult,
    SearchMeta,
    SearchResponse,
    SearchResult,
)

__version__ = "0.1.0"
__all__ = [
    "AgentSearch",
    "AgentSearchError",
    "SearchResult",
    "SearchResponse",
    "SearchMeta",
    "ReadResult",
    "BatchReadResponse",
    "BrowserLink",
    "BrowserFetchResponse",
    "NewsResult",
    "NewsResponse",
    "JobResult",
    "JobSearchResponse",
    "HealthResponse",
    "configure",
    "search",
    "search_strategy",
    "deep_search",
    "read",
    "read_batch",
    "browser_fetch",
    "news",
    "search_jobs",
]

# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

_default_client: AgentSearch | None = None


def _get_client() -> AgentSearch:
    global _default_client
    if _default_client is None:
        _default_client = AgentSearch()
    return _default_client


def configure(base_url: str = "http://localhost:3939", timeout: float = 30.0) -> None:
    """Configure the default AgentSearch instance for module-level functions.

    Args:
        base_url: Base URL of the AgentSearch instance.
        timeout: Request timeout in seconds.
    """
    global _default_client
    if _default_client is not None:
        _default_client.close()
    _default_client = AgentSearch(base_url, timeout=timeout)


def search(query: str, **kwargs) -> SearchResponse:  # type: ignore[no-untyped-def]
    """Search the web using the default client. See :meth:`AgentSearch.search`."""
    return _get_client().search(query, **kwargs)


def search_strategy(query: str, **kwargs) -> SearchResponse:  # type: ignore[no-untyped-def]
    """Search with a named engine strategy. See :meth:`AgentSearch.search_strategy`."""
    return _get_client().search_strategy(query, **kwargs)


def deep_search(query: str, **kwargs) -> SearchResponse:  # type: ignore[no-untyped-def]
    """Multi-query fusion search. See :meth:`AgentSearch.deep_search`."""
    return _get_client().deep_search(query, **kwargs)


def read(url: str, **kwargs) -> ReadResult:  # type: ignore[no-untyped-def]
    """Extract content from a URL. See :meth:`AgentSearch.read`."""
    return _get_client().read(url, **kwargs)


def read_batch(urls: list, **kwargs) -> BatchReadResponse:  # type: ignore[no-untyped-def]
    """Extract content from multiple URLs. See :meth:`AgentSearch.read_batch`."""
    return _get_client().read_batch(urls, **kwargs)


def browser_fetch(url: str, **kwargs) -> BrowserFetchResponse:  # type: ignore[no-untyped-def]
    """Browser-render a URL. See :meth:`AgentSearch.browser_fetch`."""
    return _get_client().browser_fetch(url, **kwargs)


def news(query: str, **kwargs) -> NewsResponse:  # type: ignore[no-untyped-def]
    """Search news. See :meth:`AgentSearch.news`."""
    return _get_client().news(query, **kwargs)


def search_jobs(query: str, **kwargs) -> JobSearchResponse:  # type: ignore[no-untyped-def]
    """Search jobs. See :meth:`AgentSearch.search_jobs`."""
    return _get_client().search_jobs(query, **kwargs)
