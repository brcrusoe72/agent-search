"""Bounded browser rendering and extraction.

This module is intentionally a renderer/extractor, not a bot-evasion layer. It
does not solve CAPTCHAs, use persistent profiles, log in, or try to bypass
search-engine blocking. It loads a safe URL in a temporary browser context,
extracts readable text and links, and reports challenge/block pages as failures.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from app.domain_trust import TrustResult, evaluate_trust
from app.fetch_policy import (
    MAX_CONTENT_CHARS,
    MIN_USEFUL_CHARS,
    USER_AGENTS,
    clean_html,
    is_safe_url,
    sanitize_content,
)


BROWSER_RENDER_ENABLED = os.getenv("BROWSER_RENDER_ENABLED", "1").lower() not in {"0", "false", "no", "off"}
BROWSER_RENDER_TIMEOUT_MS = int(os.getenv("BROWSER_RENDER_TIMEOUT_MS", "15000"))
BROWSER_RENDER_MAX_LINKS = int(os.getenv("BROWSER_RENDER_MAX_LINKS", "50"))
BROWSER_RENDER_BLOCKED_DOMAINS = {
    domain.strip().lower()
    for domain in os.getenv(
        "BROWSER_RENDER_BLOCKED_DOMAINS",
        ",".join([
            "google.com",
            "bing.com",
            "search.yahoo.com",
            "startpage.com",
            "duckduckgo.com",
        ]),
    ).split(",")
    if domain.strip()
}
BROWSER_BLOCKED_RESOURCE_TYPES = {
    item.strip().lower()
    for item in os.getenv("BROWSER_BLOCKED_RESOURCE_TYPES", "image,media,font").split(",")
    if item.strip()
}

CHALLENGE_SIGNALS = [
    "captcha",
    "checking your browser",
    "client challenge",
    "verify you are human",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "enable javascript and cookies",
    "cloudflare ray id",
    "too many requests",
]


@dataclass
class BrowserLink:
    """A link extracted from a rendered page."""

    text: str
    url: str


@dataclass
class BrowserRenderResult:
    """Result from a bounded browser render attempt."""

    url: str
    final_url: str = ""
    title: str = ""
    content: str | None = None
    chars: int = 0
    links: list[BrowserLink] = field(default_factory=list)
    success: bool = False
    strategy: str = "browser-render"
    error: str | None = None
    challenge_detected: bool = False
    blocked_reason: str | None = None
    render_time_ms: float = 0.0
    trust: TrustResult | None = None


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_matches(hostname: str, domain: str) -> bool:
    hostname = hostname.lower().removeprefix("www.")
    domain = domain.lower().removeprefix("www.")
    return hostname == domain or hostname.endswith("." + domain)


def _blocked_browser_domain(url: str) -> str | None:
    hostname = _hostname(url)
    for domain in BROWSER_RENDER_BLOCKED_DOMAINS:
        if _host_matches(hostname, domain):
            return f"Browser rendering disabled for search-engine domain '{domain}'"
    return None


def _default_chromium_path() -> str | None:
    explicit = os.getenv("BROWSER_CHROMIUM_PATH", "").strip()
    if explicit:
        return explicit
    for candidate in [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]:
        if Path(candidate).exists():
            return candidate
    return None


def detect_browser_challenge(title: str, text: str, html: str) -> bool:
    haystack = f"{title}\n{text[:5000]}\n{html[:5000]}".lower()
    return any(signal in haystack for signal in CHALLENGE_SIGNALS)


def _clean_link_text(value: object, limit: int = 140) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text[:limit]


def _extract_links(raw_links: list[dict[str, Any]], max_links: int) -> list[BrowserLink]:
    links: list[BrowserLink] = []
    seen: set[str] = set()
    for item in raw_links:
        href = str(item.get("href") or "").strip()
        if not href or href in seen:
            continue
        if not href.startswith(("http://", "https://")):
            continue
        if not is_safe_url(href):
            continue
        text = _clean_link_text(item.get("text")) or href
        links.append(BrowserLink(text=text, url=href))
        seen.add(href)
        if len(links) >= max_links:
            break
    return links


def extract_rendered_content(html: str, body_text: str, max_chars: int) -> str | None:
    content = clean_html(html)
    if not content and body_text:
        soup = BeautifulSoup(body_text, "html.parser")
        content = soup.get_text(separator="\n", strip=True) or body_text
    if not content:
        return None
    content = sanitize_content(content)[:max_chars]
    if len(content.strip()) < MIN_USEFUL_CHARS:
        return None
    return content


async def render_browser_page(
    url: str,
    *,
    max_chars: int | None = None,
    max_links: int | None = None,
    timeout_ms: int | None = None,
) -> BrowserRenderResult:
    """Render a URL in an ephemeral browser context and extract text/links."""
    start = time.monotonic()
    effective_max_chars = max_chars or MAX_CONTENT_CHARS
    effective_max_links = max_links if max_links is not None else BROWSER_RENDER_MAX_LINKS
    effective_timeout_ms = timeout_ms or BROWSER_RENDER_TIMEOUT_MS
    trust = evaluate_trust(url, check_whois=False)

    def _result(**kwargs: Any) -> BrowserRenderResult:
        result = BrowserRenderResult(url=url, trust=trust, **kwargs)
        result.render_time_ms = round((time.monotonic() - start) * 1000, 1)
        return result

    if not BROWSER_RENDER_ENABLED:
        return _result(error="Browser rendering is disabled", blocked_reason="disabled")
    if not is_safe_url(url, verbose=True):
        return _result(error="URL blocked by safety check", blocked_reason="unsafe_url")
    blocked_reason = _blocked_browser_domain(url)
    if blocked_reason:
        return _result(error=blocked_reason, blocked_reason=blocked_reason)

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except Exception as exc:
        return _result(error=f"Playwright unavailable: {type(exc).__name__}: {exc}")

    browser = None
    context = None
    try:
        async with async_playwright() as pw:
            executable_path = _default_chromium_path()
            browser = await pw.chromium.launch(
                headless=True,
                executable_path=executable_path,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-extensions",
                    "--disable-background-networking",
                ],
            )
            context = await browser.new_context(
                user_agent=USER_AGENTS[0],
                viewport={"width": 1365, "height": 768},
                locale="en-US",
                timezone_id="UTC",
                ignore_https_errors=False,
            )
            page = await context.new_page()

            safety_cache: dict[str, bool] = {}

            async def _route(route: Any) -> None:
                request = route.request
                if request.resource_type in BROWSER_BLOCKED_RESOURCE_TYPES:
                    await route.abort()
                    return
                request_url = request.url
                safe = safety_cache.get(request_url)
                if safe is None:
                    safe = is_safe_url(request_url)
                    safety_cache[request_url] = safe
                if not safe:
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", _route)
            response = await page.goto(url, wait_until="domcontentloaded", timeout=effective_timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(3000, effective_timeout_ms))
            except PlaywrightTimeoutError:
                # Network-idle is best effort; DOM content is enough for extraction.
                pass

            final_url = page.url
            if not is_safe_url(final_url, verbose=True):
                return _result(
                    final_url=final_url,
                    error="Rendered navigation ended at an unsafe URL",
                    blocked_reason="unsafe_final_url",
                )
            final_blocked_reason = _blocked_browser_domain(final_url)
            if final_blocked_reason:
                return _result(
                    final_url=final_url,
                    error=final_blocked_reason,
                    blocked_reason=final_blocked_reason,
                )

            title = await page.title()
            html = await page.content()
            try:
                body_text = await page.locator("body").inner_text(timeout=1000)
            except Exception:
                body_text = ""

            status = response.status if response is not None else None
            if status is not None and status >= 400:
                return _result(
                    final_url=final_url,
                    title=title,
                    error=f"Rendered navigation returned HTTP {status}",
                    blocked_reason="http_error",
                )

            challenge_detected = detect_browser_challenge(title, body_text, html)
            if challenge_detected:
                return _result(
                    final_url=final_url,
                    title=title,
                    error="Challenge or block page detected; browser renderer will not bypass it",
                    challenge_detected=True,
                )

            raw_links = await page.evaluate(
                """(maxLinks) => Array.from(document.links).slice(0, maxLinks * 3).map((a) => ({
                    text: (a.innerText || a.textContent || '').trim(),
                    href: a.href || ''
                }))""",
                effective_max_links,
            )
            links = _extract_links(raw_links if isinstance(raw_links, list) else [], effective_max_links)
            content = extract_rendered_content(html, body_text, effective_max_chars)
            if not content:
                return _result(
                    final_url=final_url,
                    title=title,
                    links=links,
                    error=f"Rendered page produced no useful content (status={status})",
                )

            return _result(
                final_url=final_url,
                title=title,
                content=content,
                chars=len(content),
                links=links,
                success=True,
            )
    except Exception as exc:
        return _result(error=f"{type(exc).__name__}: {exc}")
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception:
                # Playwright may already close the context after navigation-level failures.
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                # Browser teardown should not override the render result.
                pass
