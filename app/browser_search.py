"""Fixed public search-page adapters; no paid APIs or arbitrary navigation targets."""
from __future__ import annotations

import base64
import binascii
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from app.fetch_policy import is_safe_url


SEARCH_PAGES = {
    "duckduckgo": "https://html.duckduckgo.com/html/",
    "bing": "https://www.bing.com/search",
}


def search_challenge(title: str, text: str, html: str) -> bool:
    """Recognize challenge pages without rejecting searches about CAPTCHAs."""
    soup = BeautifulSoup(html, "html.parser")
    if soup.select_one('#captcha, #b_captcha, form[action*="anomaly"], iframe[src*="recaptcha"]'):
        return True
    if title.strip().lower().startswith(("access denied", "just a moment", "attention required")):
        return True
    return (not soup.select_one(".result__a, li.b_algo h2 a")
            and any(signal in text[:5000].lower() for signal in
                    ("verify you are human", "unusual traffic", "select all squares", "complete the following challenge")))


def search_url(provider: str, query: str) -> str:
    # The public API caps user text at 500; allow room for its site: clause.
    if provider not in SEARCH_PAGES or not query.strip() or len(query) > 1000:
        raise ValueError("Invalid browser search provider or query")
    return SEARCH_PAGES[provider] + "?" + urlencode({"q": query})


def search_navigation_allowed(url: str, provider: str) -> bool:
    """Only the selected search origin/path can be a discovery document."""
    try:
        target, expected = urlparse(url), urlparse(SEARCH_PAGES[provider])
        return (target.scheme == "https" and target.hostname == expected.hostname
                and target.port in (None, 443) and not target.username and not target.password
                and target.path.rstrip("/") == expected.path.rstrip("/"))
    except (KeyError, ValueError):
        return False


def result_url(href: str, provider: str) -> str | None:
    """Decode known tracking wrappers, then validate the actual destination."""
    if len(href) > 8192:
        return None
    try:
        url = urljoin(SEARCH_PAGES[provider], href)
        parsed = urlparse(url)
        if parsed.hostname in {"duckduckgo.com", "html.duckduckgo.com"} and parsed.path == "/l/":
            url = parse_qs(parsed.query).get("uddg", [""])[0]
        elif parsed.hostname in {"bing.com", "www.bing.com"} and parsed.path == "/ck/a":
            value = parse_qs(parsed.query).get("u", [""])[0]
            if not value.startswith("a1"):
                return None
            encoded = value[2:]
            url = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True).decode("utf-8")
        target = urlparse(url)
        host = (target.hostname or "").lower()
        if any(host == domain or host.endswith("." + domain)
               for domain in ("bing.com", "duckduckgo.com", "microsoft.com")):
            return None
        if len(url) > 4096 or target.scheme not in {"http", "https"} or target.username or target.password:
            return None
        return url if is_safe_url(url) else None
    except (ValueError, KeyError, UnicodeError, binascii.Error):
        return None


def extract_search_results(html: str, provider: str, count: int) -> list[dict]:
    """Extract organic result cards only; never mistake menus or ads for hits."""
    soup = BeautifulSoup(html, "html.parser")
    selector = ".result" if provider == "duckduckgo" else "li.b_algo"
    rows, seen = [], set()
    for card in soup.select(selector)[:40]:
        if "result--ad" in card.get("class", []):
            continue
        anchor = card.select_one("a.result__a" if provider == "duckduckgo" else "h2 a")
        if not anchor:
            continue
        url = result_url(str(anchor.get("href", "")), provider)
        title = " ".join(anchor.get_text(" ", strip=True).split())[:500]
        if not url or not title or url in seen:
            continue
        snippet = card.select_one(".result__snippet" if provider == "duckduckgo" else ".b_caption p")
        rows.append({"title": title, "url": url, "content": snippet.get_text(" ", strip=True)[:1500] if snippet else "",
                     "engines": ["browser:" + provider]})
        seen.add(url)
        if len(rows) >= max(1, min(count, 20)):
            break
    return rows
