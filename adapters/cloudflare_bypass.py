"""
Adapter: Cloudflare-protected sites.

Detects Cloudflare "Just a moment" / "Checking your browser" challenges
and falls back to alternative extraction strategies.
"""

import httpx
import logging
from typing import Optional, Dict

from adapters.safe_fetch import safe_httpx_get

logger = logging.getLogger(__name__)


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


# Domains known to use aggressive Cloudflare protection
CLOUDFLARE_DOMAINS = {
    "iiss.org",
    "securityconference.org",
    "foreignaffairs.com",
    "ft.com",
    "economist.com",
    "wsj.com",
}

# Alternative user agents that sometimes bypass basic CF challenges
BYPASS_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "curl/8.7.1",
]


def can_handle(url: str, error: Optional[str] = None, content: Optional[str] = None) -> bool:
    """Check if this adapter should handle the URL/error."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lower().replace("www.", "")

    # Known CF domains
    if domain in CLOUDFLARE_DOMAINS:
        return True

    # Detect CF challenge in error or content
    cf_signals = [
        "just a moment",
        "checking your browser",
        "cloudflare",
        "cf-ray",
        "challenge-platform",
        "enable javascript and cookies",
    ]

    check_text = (error or "") + (content or "")
    if any(sig in check_text.lower() for sig in cf_signals):
        return True

    return False


async def extract(url: str, timeout: int = 30) -> Optional[Dict]:
    """
    Try to extract content from a Cloudflare-protected site.

    Strategy chain:
    1. Try with different user agents
    2. Try Google Cache version
    3. Try Wayback Machine
    4. Try search-about (find the content discussed elsewhere)
    """
    # Strategy 1: Try with bypass user agents
    for ua in BYPASS_AGENTS:
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Cache-Control": "no-cache",
                }
            ) as client:
                resp = await safe_httpx_get(client, url)
                if resp.status_code == 200:
                    text = resp.text
                    # Check if we got real content, not a challenge page
                    if not _is_challenge_page(text):
                        return {
                            "content": text,
                            "strategy": "cloudflare-bypass-ua",
                            "success": True,
                        }
        except Exception as e:
            logger.debug("CF bypass attempt failed with UA %s: %s", _safe_log_value(ua[:30]), e)
            continue

    # Strategy 2: Google Cache
    try:
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await safe_httpx_get(client, cache_url)
            if resp.status_code == 200 and not _is_challenge_page(resp.text):
                return {
                    "content": resp.text,
                    "strategy": "cloudflare-google-cache",
                    "success": True,
                }
    except Exception as e:
        logger.debug("Google Cache fallback failed: %s", e)

    # Strategy 3: Wayback Machine
    try:
        wb_url = f"https://web.archive.org/web/2/{url}"
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await safe_httpx_get(client, wb_url)
            if resp.status_code == 200 and not _is_challenge_page(resp.text):
                return {
                    "content": resp.text,
                    "strategy": "cloudflare-wayback",
                    "success": True,
                }
    except Exception as e:
        logger.debug("Wayback fallback failed: %s", e)

    return None


def _is_challenge_page(html: str) -> bool:
    """Detect if HTML is a Cloudflare challenge page, not real content."""
    signals = [
        "just a moment",
        "checking your browser",
        "challenge-platform",
        "cf-chl-",
        "enable javascript and cookies to continue",
        '<title>Just a moment...</title>',
    ]
    html_lower = html.lower()
    # If page is very short and has CF signals, it's a challenge
    if len(html) < 5000 and any(s in html_lower for s in signals):
        return True
    # Even on longer pages, if the title is the CF challenge
    if '<title>just a moment' in html_lower:
        return True
    return False
