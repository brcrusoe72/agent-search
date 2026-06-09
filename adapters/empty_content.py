"""Adapter for pages that return HTML but with empty/minimal content.

Uses readability-style scoring to find the real content block.
"""

import logging

from bs4 import BeautifulSoup

from adapters.safe_fetch import safe_requests_get

logger = logging.getLogger("agentsearch.adapters.empty_content")

MIN_CHARS = 300
MAX_CHARS = 15000


def fetch_content(url: str) -> str | None:
    """Extract content from pages with poor HTML structure."""
    try:
        r = safe_requests_get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AgentSearch/2.0)"},
        )
        if not r.ok:
            return None

        # Try multiple parsers
        for parser in ["html.parser", "lxml", "html5lib"]:
            try:
                soup = BeautifulSoup(r.text, parser)
            except Exception as exc:
                logger.debug("Parser %s failed: %s", parser, exc)
                continue

            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()

            # Score all text-containing blocks
            candidates = []
            for el in soup.find_all(["div", "article", "section", "main", "td", "p"]):
                text = el.get_text(strip=True)
                if len(text) < MIN_CHARS:
                    continue
                p_count = len(el.find_all("p"))
                score = len(text) + (p_count * 50)
                candidates.append((score, text))

            if candidates:
                candidates.sort(reverse=True)
                return candidates[0][1][:MAX_CHARS]

        return None
    except Exception as exc:
        logger.debug("Empty-content adapter failed: %s", exc)
        return None
