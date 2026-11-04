"""SDK behavior tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk"))

from agentsearch.client import AgentSearch, AgentSearchError  # noqa: E402


def test_sdk_loads_token_from_environment(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_SEARCH_TOKEN", "env-token")

    client = AgentSearch()

    assert client.token == "env-token"
    assert client._headers()["Authorization"] == "Bearer env-token"
    client.close()


def test_sdk_does_not_load_token_from_current_working_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_SEARCH_TOKEN", raising=False)
    monkeypatch.delenv("AGENTSEARCH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

    token_file = tmp_path / "credentials" / "agent-search-token.txt"
    token_file.parent.mkdir()
    token_file.write_text("file-token\n")

    client = AgentSearch()

    assert client.token is None
    assert "Authorization" not in client._headers()
    client.close()


def test_sdk_blocks_remote_plaintext_bearer_token() -> None:
    import pytest

    with pytest.raises(AgentSearchError, match="plaintext HTTP"):
        AgentSearch("http://search.example", token="secret")


def test_sdk_allows_explicit_remote_plaintext_opt_in() -> None:
    client = AgentSearch("http://search.example", token="secret", allow_insecure_http=True)
    assert client.token == "secret"
    client.close()


def test_sdk_browser_fetch_parses_response(monkeypatch) -> None:
    client = AgentSearch(token="token")

    def fake_get(path, params):
        assert path == "/providers/browser/fetch"
        assert params["url"] == "https://example.com/app"
        return {
            "url": "https://example.com/app",
            "fetched_url": "https://cdn.example.net/rendered",
            "derived_from": "https://example.com/app",
            "final_url": "https://example.com/app",
            "title": "Rendered App",
            "content": "Rendered content",
            "chars": 16,
            "links": [{"text": "Docs", "url": "https://example.com/docs"}],
            "success": True,
            "strategy": "browser-render",
            "challenge_detected": False,
            "render_time_ms": 12.3,
            "trust": {"domain": "cdn.example.net", "tier": "standard", "score": 0.5},
            "requested_trust": {"domain": "example.com", "tier": "standard", "score": 0.6},
        }

    monkeypatch.setattr(client, "_get", fake_get)

    result = client.browser_fetch("https://example.com/app", max_links=5)

    assert result.success is True
    assert result.title == "Rendered App"
    assert result.links[0].url == "https://example.com/docs"
    assert result.fetched_url == "https://cdn.example.net/rendered"
    assert result.derived_from == "https://example.com/app"
    assert result.trust and result.trust.domain == "cdn.example.net"
    assert result.requested_trust and result.requested_trust.domain == "example.com"
    client.close()


def test_sdk_read_preserves_provenance_and_trust() -> None:
    result = AgentSearch._parse_read_result({
        "url": "https://reuters.com/original",
        "fetched_url": "https://coverage.example/story",
        "derived_from": "https://reuters.com/original",
        "success": True,
        "trust": {"domain": "coverage.example", "tier": "standard", "score": 0.5},
        "requested_trust": {"domain": "reuters.com", "tier": "established", "score": 1.0},
    })
    assert result.fetched_url == "https://coverage.example/story"
    assert result.derived_from == result.url
    assert result.trust and result.trust.domain == "coverage.example"
    assert result.requested_trust and result.requested_trust.domain == "reuters.com"
