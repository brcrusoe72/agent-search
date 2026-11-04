from __future__ import annotations

import asyncio

import httpx

from app.search_providers import YoucomProvider, provider_by_name, providers_catalog


class FakeYoucomResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("fake upstream error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


class FakeYoucomClient:
    """Records HTTP calls so tests can assert the exact request contract."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self._status_code = status_code
        self.calls: list[dict] = []

    async def get(self, url: str, params: dict | None = None, headers: dict | None = None,
                  timeout: float | None = None, **kwargs) -> FakeYoucomResponse:
        self.calls.append({"method": "GET", "url": url, "params": params, "headers": headers})
        return FakeYoucomResponse(self._payload, self._status_code)

    async def post(self, url: str, params: dict | None = None, headers: dict | None = None,
                   json: dict | None = None, timeout: float | None = None, **kwargs) -> FakeYoucomResponse:
        self.calls.append({"method": "POST", "url": url, "params": params, "headers": headers, "json": json})
        return FakeYoucomResponse(self._payload, self._status_code)


def _keyless_payload() -> dict:
    return {
        "results": {
            "web": [
                {
                    "title": "You.com keyless search",
                    "url": "https://you.com/blog/keyless",
                    "snippets": ["Free tier, no key needed."],
                    "description": "Agents API free tier",
                },
                {"title": "", "url": "not a url", "snippets": ["dropped"]},
                {"title": "Missing URL", "url": "", "snippets": ["dropped too"]},
            ]
        }
    }


def test_youcom_registered_in_catalog() -> None:
    names = {item["name"] for item in providers_catalog()}
    assert "youcom" in names
    assert provider_by_name("youcom").engine_name == "youcom"


def test_youcom_keyless_parses_nested_web_results_and_drops_invalid_urls() -> None:
    client = FakeYoucomClient(_keyless_payload())

    response = asyncio.run(YoucomProvider().search(client, "free web search", 5))

    assert response.provider == "youcom"
    assert response.upstream_status == "ok"
    assert [r["url"] for r in response.results] == ["https://you.com/blog/keyless"]
    result = response.results[0]
    assert result["title"] == "You.com keyless search"
    assert result["content"] == "Free tier, no key needed."
    assert result["engines"] == ["youcom"]


def test_youcom_keyless_request_contract() -> None:
    client = FakeYoucomClient(_keyless_payload())

    asyncio.run(YoucomProvider().search(client, "free web search", 5))

    call = client.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.you.com/v1/agents/search"
    assert call["params"]["query"] == "free web search"
    assert call["params"]["max_results"] >= 1
    assert "youdotcom-integration/brcrusoe72-agent-search" in call["headers"]["User-Agent"]
    assert "AgentSearch" in call["headers"]["User-Agent"]


def test_youcom_tolerates_flat_response_shape() -> None:
    payload = {"results": [{"title": "Flat result", "url": "https://example.com/flat", "snippets": ["flat shape"]}]}
    client = FakeYoucomClient(payload)

    response = asyncio.run(YoucomProvider().search(client, "flat query", 3))

    call = client.calls[0]
    assert call["method"] == "GET"
    assert response.upstream_status == "ok"
    assert [r["url"] for r in response.results] == ["https://example.com/flat"]


def test_youcom_reports_upstream_errors_instead_of_empty_success() -> None:
    client = FakeYoucomClient({"results": {"web": []}}, status_code=503)

    response = asyncio.run(YoucomProvider().search(client, "anything", 3))

    assert response.upstream_status == "error"
    assert response.results == []
    assert response.unresponsive_engines == ["youcom"]
    assert response.upstream_errors and "youcom" in response.upstream_errors[0]
