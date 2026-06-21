"""SDK behavior tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from agentsearch.client import AgentSearch  # noqa: E402


def test_sdk_loads_token_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SEARCH_TOKEN", "env-token")

    client = AgentSearch()

    assert client.token == "env-token"
    assert client._headers()["Authorization"] == "Bearer env-token"
    client.close()


def test_sdk_loads_token_from_local_credentials_file(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_SEARCH_TOKEN", raising=False)
    monkeypatch.delenv("AGENTSEARCH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)

    token_file = tmp_path / "credentials" / "agent-search-token.txt"
    token_file.parent.mkdir()
    token_file.write_text("file-token\n")

    client = AgentSearch()

    assert client.token == "file-token"
    assert client._headers()["Authorization"] == "Bearer file-token"
    client.close()


def test_sdk_browser_fetch_parses_response(monkeypatch) -> None:
    client = AgentSearch(token="token")

    def fake_get(path, params):
        assert path == "/providers/browser/fetch"
        assert params["url"] == "https://example.com/app"
        return {
            "url": "https://example.com/app",
            "final_url": "https://example.com/app",
            "title": "Rendered App",
            "content": "Rendered content",
            "chars": 16,
            "links": [{"text": "Docs", "url": "https://example.com/docs"}],
            "success": True,
            "strategy": "browser-render",
            "challenge_detected": False,
            "render_time_ms": 12.3,
        }

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.browser_fetch("https://example.com/app", max_links=5)

    assert result.success is True
    assert result.title == "Rendered App"
    assert result.links[0].url == "https://example.com/docs"
    client.close()
