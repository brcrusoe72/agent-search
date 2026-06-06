"""Adapter for pages with malformed HTML that crash standard parsers."""

import re

from adapters.safe_fetch import safe_requests_get

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

        text = r.text

        # Strip all HTML tags via regex (last resort)
        text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)

        # Clean whitespace
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"(\n\s*){3,}", "\n\n", text)

        if len(text) >= MIN_CHARS:
            return text[:MAX_CHARS]

        return None
    except Exception:
        return None
