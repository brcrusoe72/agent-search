"""Optional live Docker smoke tests for a running AgentSearch stack.

These tests are skipped by default. Enable them with:

    AGENTSEARCH_DOCKER_INTEGRATION=1 pytest tests/test_live_docker.py -q

If the stack uses bearer auth, set AGENT_SEARCH_TOKEN or AGENTSEARCH_TOKEN.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest
import requests


pytestmark = pytest.mark.skipif(
    os.getenv("AGENTSEARCH_DOCKER_INTEGRATION") != "1",
    reason="Set AGENTSEARCH_DOCKER_INTEGRATION=1 for live Docker smoke tests",
)


PUBLIC_BASE_URL = os.getenv("AGENTSEARCH_PUBLIC_URL", "http://127.0.0.1:3939")
PRIVATE_BASE_URL = os.getenv("AGENTSEARCH_PRIVATE_URL", "http://127.0.0.1:3940")
TIMEOUT = float(os.getenv("AGENTSEARCH_DOCKER_TIMEOUT", "60"))
LIVE_QUERY = os.getenv("AGENTSEARCH_LIVE_QUERY", "python programming language")
LIVE_ENGINES = os.getenv("AGENTSEARCH_LIVE_ENGINES", "github")
LIVE_READ_URL = os.getenv("AGENTSEARCH_LIVE_READ_URL", "https://www.iana.org/help/example-domains")


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


def _get_response(base_url: str, path: str, *, headers: dict[str, str] | None = None, **params) -> requests.Response:
    return requests.get(
        f"{base_url.rstrip('/')}{path}",
        params=params or None,
        headers=headers or {},
        timeout=TIMEOUT,
    )


def _assert_no_direct_egress(compose_files: list[str], services: tuple[str, ...]) -> None:
    for service in services:
        command = [
            "docker", "compose", *compose_files,
            "exec", "-T", service, "python", "-c",
            "import socket; socket.create_connection(('1.1.1.1',443),5)",
        ]
        completed = subprocess.run(
            command,
            cwd=Path(__file__).parents[1],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert completed.returncode != 0, f"{service} unexpectedly established direct public egress"


def test_public_health() -> None:
    data = _get_json(PUBLIC_BASE_URL, "/health")
    assert data["status"] in {"healthy", "degraded"}, data
    assert data["searxng_available"] is True
    assert data["search_available"] is True
    assert data.get("upstream_status") in {"ok", "degraded"}
    if data["status"] == "degraded":
        assert data.get("upstream_errors") or data.get("unresponsive_engines")


def test_public_liveness_reports_direct_route() -> None:
    data = _get_json(PUBLIC_BASE_URL, "/live")
    assert data == {"status": "alive", "version": "2.0.0", "network_route": "direct"}


def test_public_workers_have_no_direct_egress() -> None:
    _assert_no_direct_egress([], ("api", "browser"))


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
        q=LIVE_QUERY,
        count=3,
        engines=LIVE_ENGINES,
    )
    assert data.get("meta", {}).get("upstream_status") in {"ok", "degraded"}, data.get("meta")
    assert data.get("results"), data
    assert data["meta"]["total"] >= 1
    assert data["meta"]["engines_used"]


def test_public_strategy_code_authenticated() -> None:
    data = _get_json(
        PUBLIC_BASE_URL,
        "/search/strategy",
        headers=_headers(required=True),
        q=LIVE_QUERY,
        count=3,
        mode="code",
    )
    meta = data.get("meta", {})
    assert meta.get("mode") == "code", meta
    assert meta.get("upstream_status") in {"ok", "degraded", "error"}, meta
    assert data.get("results"), data
    assert meta["total"] >= 1
    attempt_engines = [engine for attempt in meta.get("engine_attempts", []) for engine in attempt.get("engines", [])]
    assert {"github", "mdn", "docker hub"}.issubset(set(attempt_engines)), meta
    assert "bing" not in attempt_engines
    assert "bing" not in meta.get("engines_used", [])


def test_public_invalid_engine_does_not_fallback_authenticated() -> None:
    response = _get_response(
        PUBLIC_BASE_URL,
        "/search",
        headers=_headers(required=True),
        q=LIVE_QUERY,
        count=3,
        engines="notarealengine",
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Unknown or disabled search engine(s)"
    assert detail["invalid_engines"] == ["notarealengine"]


def test_public_read_preserves_live_provenance_authenticated() -> None:
    data = _get_json(PUBLIC_BASE_URL, "/read", headers=_headers(required=True), url=LIVE_READ_URL)
    assert data["success"] is True, data
    assert data["url"] == LIVE_READ_URL
    assert data["fetched_url"].startswith("https://")
    assert data["chars"] >= 200
    assert data["trust"]["domain"]
    assert data["requested_trust"]["domain"]


def test_public_browser_worker_live_authenticated() -> None:
    data = _get_json(
        PUBLIC_BASE_URL,
        "/providers/browser/fetch",
        headers=_headers(required=True),
        url=LIVE_READ_URL,
        max_chars=5000,
        max_links=10,
        timeout_ms=30000,
    )
    assert data["success"] is True, data
    assert data["fetched_url"].startswith("https://")
    assert data["chars"] >= 200
    assert data["trust"]["domain"]
    assert data["requested_trust"]["domain"]


def test_private_health() -> None:
    data = _get_json(PRIVATE_BASE_URL, "/health")
    assert data["status"] in {"healthy", "degraded"}, data
    assert data["searxng_available"] is True
    assert data["search_available"] is True
    assert data.get("upstream_status") in {"ok", "degraded"}
    if data["status"] == "degraded":
        assert data.get("upstream_errors") or data.get("unresponsive_engines")


def test_private_liveness_reports_tor_route() -> None:
    data = _get_json(PRIVATE_BASE_URL, "/live")
    assert data == {"status": "alive", "version": "2.0.0", "network_route": "tor"}


def test_private_proxy_observes_tor_exit() -> None:
    command = [
        "docker", "compose", "-f", "docker-compose.yml", "-f", "examples/compose.private.yml",
        "exec", "-T", "api-private", "python", "-c",
        (
            "import json,urllib.request; "
            "data=json.load(urllib.request.urlopen('https://check.torproject.org/api/ip',timeout=60)); "
            "print(json.dumps(data))"
        ),
    ]
    completed = subprocess.run(command, cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=TIMEOUT + 30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1])["IsTor"] is True


def test_private_workers_have_no_direct_egress() -> None:
    _assert_no_direct_egress(
        ["-f", "docker-compose.yml", "-f", "examples/compose.private.yml"],
        ("api-private", "browser-private"),
    )


def test_private_search_authenticated() -> None:
    data = _get_json(
        PRIVATE_BASE_URL,
        "/search",
        headers=_headers(required=True),
        q=LIVE_QUERY,
        count=3,
        engines=LIVE_ENGINES,
    )
    assert data.get("meta", {}).get("upstream_status") in {"ok", "degraded"}, data.get("meta")
    assert data.get("results"), data
    assert data["meta"]["total"] >= 1
    assert data["meta"]["engines_used"]


def test_private_strategy_private_authenticated() -> None:
    data = _get_json(
        PRIVATE_BASE_URL,
        "/search/strategy",
        headers=_headers(required=True),
        q=LIVE_QUERY,
        count=3,
        mode="private",
    )
    meta = data.get("meta", {})
    assert meta.get("mode") == "private", meta
    assert meta.get("upstream_status") in {"ok", "degraded", "error"}, meta
    assert data.get("results"), data
    assert meta["total"] >= 1
    attempt_engines = [engine for attempt in meta.get("engine_attempts", []) for engine in attempt.get("engines", [])]
    assert "github" in attempt_engines
    assert "docker hub" in attempt_engines
    assert "bing" not in attempt_engines
    assert "bing" not in meta.get("engines_used", [])


def test_private_invalid_engine_does_not_fallback_authenticated() -> None:
    response = _get_response(
        PRIVATE_BASE_URL,
        "/search",
        headers=_headers(required=True),
        q=LIVE_QUERY,
        count=3,
        engines="notarealengine",
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Unknown or disabled search engine(s)"
    assert detail["invalid_engines"] == ["notarealengine"]


def test_private_read_uses_live_tor_fetch_path_authenticated() -> None:
    data = _get_json(PRIVATE_BASE_URL, "/read", headers=_headers(required=True), url=LIVE_READ_URL)
    assert data["success"] is True, data
    assert data["url"] == LIVE_READ_URL
    assert data["fetched_url"].startswith("https://")
    assert data["chars"] >= 200


def test_private_browser_worker_uses_live_tor_path_authenticated() -> None:
    data = _get_json(
        PRIVATE_BASE_URL,
        "/providers/browser/fetch",
        headers=_headers(required=True),
        url=LIVE_READ_URL,
        max_chars=5000,
        max_links=10,
        timeout_ms=45000,
    )
    assert data["success"] is True, data
    assert data["fetched_url"].startswith("https://")
    assert data["chars"] >= 200
