"""Content extraction utilities for fetching readable text from web pages."""

import re
import asyncio
from typing import Optional
import httpx
from bs4 import BeautifulSoup, Comment


def extract_readable_text(html: str) -> str:
    """Extract readable text from HTML, removing scripts, styles, and HTML tags."""
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
        
        return text[:5000]  # Truncate to 5000 chars
        
    except Exception:
        return ""


async def fetch_page_content(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Fetch and extract content from a single URL with timeout."""
    try:
        response = await client.get(
            url,
            timeout=10.0,
            headers={
                'User-Agent': 'Mozilla/5.0 (compatible; AgentSearch/1.1.0; +https://github.com/brcrusoe72/agent-search)'
            },
            follow_redirects=True
        )
        
        if response.status_code == 200:
            content_type = response.headers.get('content-type', '').lower()
            if 'text/html' in content_type:
                return extract_readable_text(response.text)
                
    except Exception:
        pass
    
    return None


async def fetch_multiple_contents(client: httpx.AsyncClient, urls: list[str]) -> dict[str, str]:
    """Fetch content from multiple URLs concurrently."""
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