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
