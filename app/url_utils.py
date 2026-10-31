"""Shared hostname normalization and authority matching helpers."""

from __future__ import annotations

from urllib.parse import urlparse


def normalize_hostname(value: str) -> str:
    """Return a lower-case ASCII hostname without a trailing dot or ``www``."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    hostname = (parsed.hostname or "").strip().rstrip(".").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return ""


def hostname_matches(hostname: str, expected_domain: str) -> bool:
    """Match only an authority itself or one of its subdomains (R10)."""
    normalized_host = normalize_hostname(hostname)
    normalized_expected = normalize_hostname(expected_domain)
    if not normalized_host or not normalized_expected:
        return False
    return (
        normalized_host == normalized_expected
        or normalized_host.endswith(f".{normalized_expected}")
    )
