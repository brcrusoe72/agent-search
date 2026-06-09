"""Adapter for pages with malformed HTML that crash standard parsers."""

import logging
import re

from bs4 import BeautifulSoup

from adapters.safe_fetch import safe_requests_get

logger = logging.getLogger("agentsearch.adapters.parse_error")

MIN_CHARS = 300
MAX_CHARS = 15000


def fetch_content(url: str) -> str | None:
    """Extract text from malformed HTML using regex fallback."""
    try:
        r = safe_requests_get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AgentSearch/2.0)"},
        )
        if not r.ok:
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)

        # Clean whitespace
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(\n\s*){3,}", "\n\n", text)

        if len(text) >= MIN_CHARS:
            return text[:MAX_CHARS]

        return None
    except Exception as exc:
        logger.debug("Parse-error adapter failed: %s", exc)
        return None
