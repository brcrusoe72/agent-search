"""
Adapter: medium.com — Paywall bypass for Medium articles.

Medium blocks scrapers and paywalls most content. This adapter tries:
1. Freedium mirror (medium paywall bypass)
2. Webcache / archive.org
3. Google AMP version
4. RSS/feed version via 12ft.io-style approach
"""

import logging

from bs4 import BeautifulSoup
from urllib.parse import urlparse, quote

from adapters.safe_fetch import safe_requests_get

logger = logging.getLogger("agentsearch.adapters.medium")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

MIN_CHARS = 300
MAX_CHARS = 15000

# Known Medium domains (medium.com + custom domains on Medium platform)
MEDIUM_DOMAINS = {
    "medium.com",
    "towardsdatascience.com",
    "betterprogramming.pub",
    "levelup.gitconnected.com",
    "javascript.plainenglish.io",
    "python.plainenglish.io",
    "blog.devgenius.io",
    "infosecwriteups.com",
    "hackernoon.com",
}


def can_handle(url: str) -> bool:
    """Check if this URL is a Medium article."""
    domain = urlparse(url).netloc.lower().replace("www.", "")
    if domain in MEDIUM_DOMAINS:
        return True
    # Check for medium.com subdomains
    if domain.endswith(".medium.com"):
        return True
    return False


def _extract_article(html: str) -> str | None:
    """Extract article text from Medium-style HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Medium article selectors
    selectors = [
        "article",
        '[role="main"]',
        ".postArticle-content",
        ".section-content",
        ".meteredContent",
        "main",
    ]

    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= MIN_CHARS:
                return text[:MAX_CHARS]

    # Fallback: all paragraphs
    paragraphs = soup.find_all("p")
    text = "\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20)
    if len(text) >= MIN_CHARS:
        return text[:MAX_CHARS]

    return None


def fetch_content(url: str) -> str | None:
    """Try multiple strategies to extract Medium article content."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com/",
    }

    # Strategy 1: Freedium mirror
    try:
        freedium_url = f"https://freedium.cfd/{url}"
        r = safe_requests_get(freedium_url, timeout=15, headers=headers)
        if r.status_code == 200:
            text = _extract_article(r.text)
            if text:
                return text
    except Exception as exc:
        logger.debug("Freedium strategy failed: %s", exc)

    # Strategy 2: Google cache
    try:
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{quote(url, safe='')}"
        r = safe_requests_get(cache_url, timeout=15, headers=headers)
        if r.status_code == 200:
            text = _extract_article(r.text)
            if text:
                return text
    except Exception as exc:
        logger.debug("Google cache strategy failed: %s", exc)

    # Strategy 3: archive.org latest
    try:
        wb_api = f"https://archive.org/wayback/available?url={quote(url, safe='')}"
        r = safe_requests_get(wb_api, timeout=10, headers=headers)
        if r.status_code == 200:
            data = r.json()
            snapshot = data.get("archived_snapshots", {}).get("closest", {})
            if snapshot.get("available"):
                wb_url = snapshot["url"]
                r2 = safe_requests_get(wb_url, timeout=15, headers=headers)
                if r2.status_code == 200:
                    text = _extract_article(r2.text)
                    if text:
                        return text
    except Exception as exc:
        logger.debug("Archive.org strategy failed: %s", exc)

    # Strategy 4: Direct with Googlebot UA (Medium sometimes allows this)
    try:
        bot_headers = {
            "User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)",
            "Accept": "text/html",
        }
        r = safe_requests_get(url, timeout=15, headers=bot_headers)
        if r.status_code == 200:
            text = _extract_article(r.text)
            if text:
                return text
    except Exception as exc:
        logger.debug("Googlebot strategy failed: %s", exc)

    return None
