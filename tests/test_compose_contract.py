from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def _compose(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_direct_api_and_browser_have_no_direct_egress_network() -> None:
    compose = _compose("docker-compose.yml")
    services = compose["services"]

    assert compose["networks"]["fetch-internal"]["internal"] is True
    assert compose["networks"]["fetch-internal"]["ipam"]["config"][0]["subnet"] == "172.29.0.0/24"
    assert set(services["api"]["networks"]) == {"fetch-internal"}
    assert set(services["browser"]["networks"]) == {"fetch-internal"}
    assert services["api"]["networks"]["fetch-internal"]["ipv4_address"] == "172.29.0.23"
    assert services["browser"]["networks"]["fetch-internal"]["ipv4_address"] == "172.29.0.22"
    assert services["api"]["dns"] == ["172.29.0.10"]
    assert services["browser"]["dns"] == ["172.29.0.10"]
    assert "seccomp=./examples/chromium-seccomp.json" in services["browser"]["security_opt"]
    assert services["browser"]["cap_add"] == ["SYS_CHROOT"]
    assert set(services["dns"]["networks"]) == {"agent-search", "fetch-internal"}
    assert services["dns"]["networks"]["fetch-internal"]["ipv4_address"] == "172.29.0.10"
    assert services["dns"]["cap_add"] == ["NET_BIND_SERVICE"]
    assert set(services["fetch-proxy"]["networks"]) == {"agent-search", "fetch-internal"}
    assert set(services["api-frontend"]["networks"]) == {"agent-search", "fetch-internal"}
    assert services["api-frontend"]["ports"] == ["127.0.0.1:3939:3939"]
    assert services["browser"]["environment"] == [
        "BROWSER_LOCAL_RENDER_ENABLED=1",
        "BROWSER_PROXY_URL=http://fetch-proxy:8888",
        "BROWSER_MAX_CONCURRENT=2",
        "HOME=/tmp/browser-home",
        "XDG_CACHE_HOME=/tmp/browser-cache",
        "XDG_CONFIG_HOME=/tmp/browser-config",
    ]


def test_private_api_and_browser_only_join_internal_tor_network() -> None:
    compose = _compose("examples/compose.private.yml")
    services = compose["services"]

    assert compose["networks"]["tor-internal"]["internal"] is True
    assert set(services["api-private"]["networks"]) == {"tor-internal"}
    assert set(services["browser-private"]["networks"]) == {"tor-internal"}
    assert services["api-private"]["networks"]["tor-internal"]["ipv4_address"] == "172.31.0.33"
    assert services["browser-private"]["networks"]["tor-internal"]["ipv4_address"] == "172.31.0.32"
    assert services["api-private"]["dns"] == ["172.31.0.10"]
    assert services["browser-private"]["dns"] == ["172.31.0.10"]
    assert "seccomp=./examples/chromium-seccomp.json" in services["browser-private"]["security_opt"]
    assert services["browser-private"]["cap_add"] == ["SYS_CHROOT"]
    assert set(services["fetch-proxy-private"]["networks"]) == {"tor-internal"}
    assert "FETCH_PROXY_TOR=tor:9050" in services["fetch-proxy-private"]["environment"]


def test_runtime_containers_are_nonroot_and_resource_bounded() -> None:
    direct = _compose("docker-compose.yml")["services"]
    private = _compose("examples/compose.private.yml")["services"]

    for name in ("api", "browser", "fetch-proxy", "api-frontend", "dns"):
        service = direct[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["pids_limit"] > 0
        assert service["mem_limit"]
        assert service["cpus"] > 0
    for name in ("api-private", "browser-private", "fetch-proxy-private"):
        service = private[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["pids_limit"] > 0
        assert service["mem_limit"]
        assert service["cpus"] > 0

    assert "USER agentsearch" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER browser" in (ROOT / "Dockerfile.browser").read_text(encoding="utf-8")
    assert "USER fetchproxy" in (ROOT / "Dockerfile.proxy").read_text(encoding="utf-8")
