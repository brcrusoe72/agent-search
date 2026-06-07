"""Optional live Docker smoke tests for a running AgentSearch stack.

These tests are skipped by default. Enable them with:

    AGENTSEARCH_DOCKER_INTEGRATION=1 pytest tests/test_live_docker.py -q

If the stack uses bearer auth, set AGENT_SEARCH_TOKEN or AGENTSEARCH_TOKEN.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import requests


pytestmark = pytest.mark.skipif(
    os.getenv("AGENTSEARCH_DOCKER_INTEGRATION") != "1",
    reason="Set AGENTSEARCH_DOCKER_INTEGRATION=1 for live Docker smoke tests",
)


PUBLIC_BASE_URL = os.getenv("AGENTSEARCH_PUBLIC_URL", "http://127.0.0.1:3939")
PRIVATE_BASE_URL = os.getenv("AGENTSEARCH_PRIVATE_URL", "http://127.0.0.1:3940")
TIMEOUT = float(os.getenv("AGENTSEARCH_DOCKER_TIMEOUT", "30"))


def _load_token() -> str | None:
    env_token = os.getenv("AGENT_SEARCH_TOKEN") or os.getenv("AGENTSEARCH_TOKEN")
    if env_token:
        return env_token.strip()

    candidates = [
        Path.cwd() / "credentials" / "agent-search-token.txt",
        Path.home() / ".openclaw" / "workspace" / "credentials" / "agent-search-token.txt",
        Path.home() / ".config" / "agent-search" / "token",
    ]
    for path in candidates:
        try:
            if path.exists():
                token = path.read_text(encoding="utf-8").strip()
                if token:
                    return token
        except OSError:
            continue
    return None


def _headers(required: bool = False) -> dict[str, str]:
    token = _load_token()
    if required and not token:
        pytest.skip("Set AGENT_SEARCH_TOKEN or AGENTSEARCH_TOKEN for authenticated live checks")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get_json(base_url: str, path: str, *, headers: dict[str, str] | None = None, **params) -> dict | list:
    response = requests.get(
        f"{base_url.rstrip('/')}{path}",
        params=params or None,
        headers=headers or {},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def test_public_health() -> None:
    data = _get_json(PUBLIC_BASE_URL, "/health")
    assert data["status"] in {"healthy", "degraded"}
    assert "searxng_available" in data


def test_public_engines_authenticated() -> None:
    data = _get_json(PUBLIC_BASE_URL, "/engines", headers=_headers(required=True))
    assert isinstance(data, list)
    assert data
    assert any(engine.get("enabled") for engine in data)


def test_public_search_authenticated() -> None:
    data = _get_json(
        PUBLIC_BASE_URL,
        "/search",
        headers=_headers(required=True),
        q="agent search",
        count=1,
    )
    assert isinstance(data.get("results"), list)
    assert "meta" in data


def test_private_health() -> None:
    data = _get_json(PRIVATE_BASE_URL, "/health")
    assert data["status"] in {"healthy", "degraded"}
    assert "searxng_available" in data


def test_private_search_authenticated() -> None:
    data = _get_json(
        PRIVATE_BASE_URL,
        "/search",
        headers=_headers(required=True),
        q="agent search",
        count=1,
    )
    assert isinstance(data.get("results"), list)
    assert "meta" in data
