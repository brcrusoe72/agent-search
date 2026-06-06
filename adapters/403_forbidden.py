"""Adapter for 403/forbidden content — tries alternative access methods."""

from bs4 import BeautifulSoup

from adapters.safe_fetch import safe_requests_get

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
]

MIN_CHARS = 300
MAX_CHARS = 15000


def fetch_content(url: str) -> str | None:
    """Try to access 403'd content via alternative methods."""
    for ua in USER_AGENTS:
        try:
            r = safe_requests_get(
                url,
                timeout=15,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate",
                    "Cache-Control": "no-cache",
                    "Referer": "https://www.google.com/",
                },
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header"]):
                    tag.decompose()
                text = soup.get_text(separator="\n", strip=True)
                if len(text) >= MIN_CHARS:
                    return text[:MAX_CHARS]
        except Exception:
            continue
    return None
