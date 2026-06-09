"""Content extraction utilities for fetching readable text from web pages.

⚠️  DEPRECATED: This module is a legacy shim. All content extraction should
go through the kill chain (killchain.py), which routes content through the
full scrubbing pipeline (scrubber.py).

This module now wraps its output through the scrubber to prevent any future
caller from bypassing content security.
"""

import asyncio
from typing import Optional
import httpx
from bs4 import BeautifulSoup, Comment

from app.killchain import _safe_get
from app.scrubber import scrub_content

import logging
logger = logging.getLogger("agentsearch.content_extractor")


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


def extract_readable_text(html: str) -> str:
    """Extract readable text from HTML, removing scripts, styles, and HTML tags.
    
    Output is passed through the content scrubber for injection detection.
    """
    if not html:
        return ""
    
    try:
        soup = BeautifulSoup(html, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Remove comments
        comments = soup.findAll(text=lambda text: isinstance(text, Comment))
        for comment in comments:
            comment.extract()
        
        # Get text
        text = soup.get_text()
        
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        text = text[:5000]  # Truncate to 5000 chars
        
        # Route through scrubber
        result = scrub_content(text)
        if result.threats:
            threat_types = {t.threat_type.value for t in result.threats}
            logger.warning(
                f"content_extractor scrubber: {len(result.threats)} threats "
                f"(types={threat_types}, risk={result.risk_score:.2f})"
            )
        return result.content
        
    except Exception as exc:
        logger.debug("Readable text extraction failed: %s", exc)
        return ""


async def fetch_page_content(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Fetch and extract content from a single URL with timeout.
    
    ⚠️  DEPRECATED: Use killchain.kill_chain() instead.
    """
    logger.warning(
        "DEPRECATED: fetch_page_content called for %s. Use kill_chain() instead.",
        _safe_log_value(url),
    )
    try:
        response = await _safe_get(
            client,
            url,
            timeout=10.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; AgentSearch/2.0; +https://github.com/brcrusoe72/agent-search)'
            },
        )
        
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' in content_type:
                return extract_readable_text(response.text)
                
    except Exception as exc:
        logger.debug("Deprecated fetch_page_content failed: %s", exc)
    
    return None


async def fetch_multiple_contents(client: httpx.AsyncClient, urls: list[str]) -> dict[str, str]:
    """Fetch content from multiple URLs concurrently.
    
    ⚠️  DEPRECATED: Use killchain.kill_chain_batch() instead.
    """
    logger.warning("DEPRECATED: fetch_multiple_contents called. Use kill_chain_batch() instead.")
    if not urls:
        return {}
    
    async def fetch_one(url: str) -> tuple[str, str]:
        content = await fetch_page_content(client, url)
        return url, content or ""
    
    tasks = [fetch_one(url) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    content_map = {}
    for result in results:
        if isinstance(result, tuple) and len(result) == 2:
            url, content = result
            content_map[url] = content
        
    return content_map
