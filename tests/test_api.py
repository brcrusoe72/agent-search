"""AgentSearch API tests.

Default tests are self-contained and do not require Docker, SearXNG, network
access, or a running localhost service. Live integration tests can be enabled
with:

    AGENTSEARCH_INTEGRATION=1 pytest tests -v
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app import main
from app.cache import Cache
from app.database import QueryDatabase


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("fake upstream error", request=None, response=None)

    def json(self) -> dict:
        return self._payload


class FakeSearxngClient:
    async def get(self, url: str, params: dict | None = None, timeout: float | None = None) -> FakeResponse:
        if url.endswith("/healthz"):
            return FakeResponse({})
        if url.endswith("/config"):
            return FakeResponse({
                "engines": [
                    {"name": "duckduckgo", "shortcut": "ddg", "enabled": True, "categories": ["general"]},
                    {"name": "brave", "shortcut": "br", "enabled": True, "categories": ["general"]},
                ]
            })
        if url.endswith("/search"):
            q = (params or {}).get("q", "query")
            return FakeResponse({
                "results": [
                    {
                        "title": f"{q} result one",
                        "url": "https://example.com/one",
                        "content": "First result snippet",
                        "engines": ["duckduckgo"],
                    },
                    {
                        "title": f"{q} result two",
                        "url": "https://example.org/two",
                        "content": "Second result snippet",
                        "engines": ["brave"],
                    },
                ]
            })
        return FakeResponse({})


@pytest.fixture(autouse=True)
def isolated_app_state(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(main, "_AUTH_TOKEN", "")
    monkeypatch.setattr(main, "http_client", FakeSearxngClient())
    monkeypatch.setattr(main, "content_cache", None)
    monkeypatch.setattr(main, "evolver", None)
    monkeypatch.setattr(main, "cache", Cache(ttl=3600))
    monkeypatch.setattr(main, "query_db", QueryDatabase(str(tmp_path / "query_log.db")))
    main._rate_store.clear()
    main._global_timestamps.clear()
    yield


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["searxng_available"] is True
    assert data["version"] == "2.0.0"


def test_engines_endpoint(client: TestClient) -> None:
    response = client.get("/engines")
    assert response.status_code == 200
    engines = response.json()
    assert engines[0]["name"] == "duckduckgo"
    assert engines[0]["enabled"] is True


def test_search_returns_deduplicated_results(client: TestClient) -> None:
    response = client.get("/search", params={"q": "manufacturing OEE", "count": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["meta"]["query"] == "manufacturing OEE"
    assert data["meta"]["total"] == 2
    assert len(data["results"]) == 2
    assert data["results"][0]["url"].startswith("https://")
    assert "duckduckgo" in data["meta"]["engines_used"]


def test_empty_query_returns_400(client: TestClient) -> None:
    response = client.get("/search", params={"q": ""})
    assert response.status_code == 400
    assert "cannot be empty" in response.json()["detail"]


def test_bearer_auth_when_enabled(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    monkeypatch.setattr(main, "_AUTH_TOKEN", "secret")

    unauthorized = client.get("/search", params={"q": "python"})
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/search",
        params={"q": "python"},
        headers={"Authorization": "Bearer secret"},
    )
    assert authorized.status_code == 200


@pytest.mark.skipif(os.getenv("AGENTSEARCH_INTEGRATION") != "1", reason="Set AGENTSEARCH_INTEGRATION=1 for live localhost tests")
def test_live_localhost_health() -> None:
    import requests

    headers = {}
    token = os.getenv("AGENT_SEARCH_TOKEN") or os.getenv("AGENTSEARCH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get("http://localhost:3939/health", headers=headers, timeout=10)
    assert response.status_code == 200
    assert response.json()["status"] in {"healthy", "degraded"}
