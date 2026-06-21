"""Shared fetch safety and content extraction helpers."""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.scrubber import scrub_content


logger = logging.getLogger("agentsearch.fetch_policy")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

CONTENT_SELECTORS = [
    "main", "article", "[role=main]", ".content", "#content",
    ".post-content", ".entry-content", ".article-body", ".post-body",
    ".story-body", ".td-post-content", ".blog-content", ".page-content",
    "#article-body", ".article__body", ".article-text",
]

GARBAGE_TAGS = [
    "script", "style", "nav", "footer", "header", "aside",
    "iframe", "noscript", "svg", "form",
]

GARBAGE_CLASSES = [
    ".sidebar", ".comments", ".related", ".advertisement", ".ad",
    ".social-share", ".newsletter-signup", ".cookie-banner",
    ".popup", ".modal", ".nav-menu", ".breadcrumb",
]

MAX_CONTENT_CHARS = 15_000
MIN_USEFUL_CHARS = 200

BLOCKED_FETCH_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "buff.ly", "is.gd", "v.gd", "short.io",
    "media.defense.gov",
    "www.iiss.org",
    "saisreview.sais.jhu.edu",
    "barrywehmiller.wd1.myworkdayjobs.com",
}

DYNAMIC_BLOCKLIST_PATH = Path(os.getenv("DATA_DIR", "data")) / "blocked_domains.txt"

SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",
    ".buzz", ".top", ".xyz", ".work", ".click",
}


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


def _load_dynamic_blocklist() -> set[str]:
    try:
        if DYNAMIC_BLOCKLIST_PATH.exists():
            domains = set()
            for line in DYNAMIC_BLOCKLIST_PATH.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    domains.add(line.lower())
            return domains
    except Exception as exc:
        logger.debug("Failed to load dynamic blocklist: %s", exc)
    return set()


def get_blocked_domains() -> set[str]:
    """Return the full set of blocked domains."""
    return BLOCKED_FETCH_DOMAINS | _load_dynamic_blocklist()


def is_safe_url(url: str, verbose: bool = False) -> bool:
    """Validate a URL before fetching or rendering it."""
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        if verbose:
            logger.warning("BLOCKED: non-HTTP scheme '%s'", _safe_log_value(parsed.scheme))
        return False

    hostname = parsed.hostname
    if not hostname:
        if verbose:
            logger.warning("BLOCKED: no hostname in URL")
        return False

    if parsed.scheme == "http" and verbose:
        logger.warning("DEGRADED: plain HTTP URL (no TLS) - %s", _safe_log_value(hostname))

    hostname_lower = hostname.lower()
    if hostname_lower in ("localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"):
        if verbose:
            logger.warning("BLOCKED: localhost/loopback")
        return False

    for blocked in get_blocked_domains():
        if hostname_lower == blocked or hostname_lower.endswith("." + blocked):
            if verbose:
                logger.warning("BLOCKED: blocked domain '%s'", _safe_log_value(blocked))
            return False

    for tld in SUSPICIOUS_TLDS:
        if hostname_lower.endswith(tld):
            if verbose:
                logger.warning("BLOCKED: suspicious TLD '%s'", _safe_log_value(tld))
            return False

    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local:
            if verbose:
                logger.warning("BLOCKED: private/reserved IP %s", addr)
            return False
    except ValueError:
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for _, _, _, _, sockaddr in resolved:
                ip_str = sockaddr[0]
                try:
                    addr = ipaddress.ip_address(ip_str)
                    if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local:
                        if verbose:
                            logger.warning(
                                "BLOCKED: '%s' resolves to private IP %s",
                                _safe_log_value(hostname),
                                addr,
                            )
                        return False
                except ValueError:
                    continue
        except (socket.gaierror, OSError) as exc:
            if verbose:
                logger.debug("DNS resolution failed for '%s': %s", _safe_log_value(hostname), exc)

    return True


def sanitize_content(text: str) -> str:
    """Run fetched content through the scrubber pipeline."""
    if not text:
        return text

    result = scrub_content(text)
    if result.threats:
        threat_types = {threat.threat_type.value for threat in result.threats}
        logger.info(
            "Scrubber: %s threats detected (types=%s, risk=%.2f, redactions=%s)",
            len(result.threats),
            threat_types,
            result.risk_score,
            result.redactions,
        )
    return result.content


def clean_html(html: str, parser: str = "html.parser") -> Optional[str]:
    """Extract readable text from HTML with smart content detection."""
    soup = BeautifulSoup(html, parser)

    for tag in soup(GARBAGE_TAGS):
        tag.decompose()

    for selector in GARBAGE_CLASSES:
        for el in soup.select(selector):
            el.decompose()

    for selector in CONTENT_SELECTORS:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= MIN_USEFUL_CHARS:
                return text[:MAX_CONTENT_CHARS]

    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CONTENT_CHARS] if len(text) >= MIN_USEFUL_CHARS else None
