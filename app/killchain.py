"""
killchain.py — Escalating Content Extraction

Multi-strategy content extraction engine. Tries every approach from
fast/cheap to slow/aggressive until one succeeds.

Strategy order:
  1. Direct fetch + smart content selectors
  2. Readability scoring (paragraph density vs link density)
  3. User-agent rotation (Chrome/Safari/Firefox/Edge)
  4. Wayback Machine (CDX API → latest snapshot)
  5. Google Cache (webcache.googleusercontent.com)
  6. Search-about fallback (find coverage on other sites)
  7. Custom adapters (pluggable Python modules from disk)
  8. PDF extraction (pdfplumber)
  9. YouTube transcript (yt-dlp)

Security:
  - SSRF protection (private IPs, DNS rebinding, scheme validation)
  - Prompt injection detection and redaction
  - Content length caps
  - Paywall detection
  - Blocked domains and suspicious TLDs

Ported from CEO's fetch.py and upgraded to async httpx.
"""

from __future__ import annotations

import asyncio
import gc
import importlib.util
import ipaddress
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.content_cache import ContentCache
from app.scrubber import scrub_content

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ADAPTERS_DIR = Path(os.getenv("ADAPTERS_DIR", "/app/adapters"))

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
]

CONTENT_SELECTORS = [
    "main", "article", "[role=main]", ".content", "#content",
    ".post-content", ".entry-content", ".article-body", ".post-body",
    ".story-body", ".td-post-content", ".blog-content", ".page-content",
    "#article-body", ".article__body", ".article-text",
]

GARBAGE_TAGS = [
    "script", "style", "nav", "footer", "header", "aside",
    "iframe", "noscript", "svg", "form",
]

GARBAGE_CLASSES = [
    ".sidebar", ".comments", ".related", ".advertisement", ".ad",
    ".social-share", ".newsletter-signup", ".cookie-banner",
    ".popup", ".modal", ".nav-menu", ".breadcrumb",
]

PAYWALL_SIGNALS = [
    "subscribe to read", "premium content", "paywall",
    "sign up to continue", "members only", "login to view",
    "create a free account", "already a subscriber",
    "this content is for subscribers", "unlock this article",
    "start your free trial", "exclusive content",
]

MIN_USEFUL_CHARS = 300
MAX_CONTENT_CHARS = 15_000

BLOCKED_FETCH_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "buff.ly", "is.gd", "v.gd", "short.io",
}

SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",
    ".buzz", ".top", ".xyz", ".work", ".click",
}


# Note: Detailed injection/exfiltration/impersonation patterns are in scrubber.py
# The scrubber handles 70+ patterns, encoding detection, semantic analysis, etc.

FETCH_TIMEOUT = 15.0
WAYBACK_TIMEOUT = 20.0
YT_DLP_TIMEOUT = 60

import logging
logger = logging.getLogger("agentsearch.killchain")


# ---------------------------------------------------------------------------
# Security: SSRF Protection
# ---------------------------------------------------------------------------

def is_safe_url(url: str, verbose: bool = False) -> bool:
    """
    Validate a URL is safe to fetch. Blocks:
    - Non-HTTP(S) schemes
    - Localhost / loopback addresses
    - Private/reserved IP ranges (RFC 1918, link-local, etc.)
    - Blocked domains and suspicious TLDs
    - DNS rebinding (resolves hostname and checks resolved IP)
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        if verbose:
            logger.warning(f"BLOCKED: non-HTTP scheme '{parsed.scheme}'")
        return False

    hostname = parsed.hostname
    if not hostname:
        if verbose:
            logger.warning("BLOCKED: no hostname in URL")
        return False

    hostname_lower = hostname.lower()

    if hostname_lower in ("localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"):
        if verbose:
            logger.warning("BLOCKED: localhost/loopback")
        return False

    for blocked in BLOCKED_FETCH_DOMAINS:
        if hostname_lower == blocked or hostname_lower.endswith("." + blocked):
            if verbose:
                logger.warning(f"BLOCKED: blocked domain '{blocked}'")
            return False

    for tld in SUSPICIOUS_TLDS:
        if hostname_lower.endswith(tld):
            if verbose:
                logger.warning(f"BLOCKED: suspicious TLD '{tld}'")
            return False

    # IP address check — block private/reserved ranges
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local:
            if verbose:
                logger.warning(f"BLOCKED: private/reserved IP {addr}")
            return False
    except ValueError:
        # Not an IP literal — resolve DNS and check resolved IP
        try:
            resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            for family, _, _, _, sockaddr in resolved:
                ip_str = sockaddr[0]
                try:
                    addr = ipaddress.ip_address(ip_str)
                    if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local:
                        if verbose:
                            logger.warning(f"BLOCKED: '{hostname}' resolves to private IP {addr}")
                        return False
                except ValueError:
                    continue
        except (socket.gaierror, OSError):
            pass

    return True


# ---------------------------------------------------------------------------
# Content Sanitization (delegates to scrubber)
# ---------------------------------------------------------------------------

def sanitize_content(text: str) -> str:
    """Run fetched content through the full scrubbing pipeline.

    Uses the Agent Café-derived scrubber: encoding normalization,
    70+ injection patterns, exfiltration detection, semantic intent
    analysis, XSS filtering, and targeted content cleaning.
    """
    if not text:
        return text

    result = scrub_content(text)

    if result.threats:
        threat_types = {t.threat_type.value for t in result.threats}
        logger.info(
            f"Scrubber: {len(result.threats)} threats detected "
            f"(types={threat_types}, risk={result.risk_score:.2f}, "
            f"redactions={result.redactions})"
        )

    return result.content


# ---------------------------------------------------------------------------
# HTML Processing
# ---------------------------------------------------------------------------

def clean_html(html: str, parser: str = "html.parser") -> Optional[str]:
    """Extract readable text from HTML with smart content detection."""
    soup = BeautifulSoup(html, parser)

    # Remove garbage tags
    for tag in soup(GARBAGE_TAGS):
        tag.decompose()

    # Remove garbage classes
    for selector in GARBAGE_CLASSES:
        for el in soup.select(selector):
            el.decompose()

    # Try targeted content extraction first
    for selector in CONTENT_SELECTORS:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) >= MIN_USEFUL_CHARS:
                return text[:MAX_CONTENT_CHARS]

    # Fallback: full body text
    text = soup.get_text(separator="\n", strip=True)
    return text[:MAX_CONTENT_CHARS] if len(text) >= MIN_USEFUL_CHARS else None


def is_paywalled(text: str) -> bool:
    """Heuristic: does this look like a paywall stub?"""
    if len(text) > 2000:
        return False
    lower = text.lower()
    return any(sig in lower for sig in PAYWALL_SIGNALS)


def _is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def _is_pdf_url(url: str) -> bool:
    return url.lower().rstrip("/").endswith(".pdf")


# ---------------------------------------------------------------------------
# Adapter Loading
# ---------------------------------------------------------------------------

def _load_adapter(name: str):
    """Dynamically load an adapter from the adapters directory."""
    path = ADAPTERS_DIR / f"{name}.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(f"adapter_{name}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "fetch_content", None)
    except Exception as e:
        logger.warning(f"Failed to load adapter '{name}': {e}")
        return None


# ---------------------------------------------------------------------------
# Strategies (each returns str | None)
# ---------------------------------------------------------------------------

async def strategy_direct(client: httpx.AsyncClient, url: str, ua_index: int = 0) -> Optional[str]:
    """Strategy 1: Direct GET + BeautifulSoup."""
    try:
        r = await client.get(
            url,
            timeout=FETCH_TIMEOUT,
            headers={
                "User-Agent": USER_AGENTS[ua_index % len(USER_AGENTS)],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        )
        if r.status_code in (403, 429):
            return None
        if "pdf" in r.headers.get("content-type", "").lower():
            return None  # Handled by pdf strategy
        r.raise_for_status()
        text = clean_html(r.text)
        if text and not is_paywalled(text):
            return text
        return None
    except Exception:
        return None


async def strategy_readability(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Strategy 2: Readability-style extraction — score blocks by paragraph density."""
    try:
        r = await client.get(
            url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENTS[0]},
            follow_redirects=True,
        )
        if not r.is_success:
            return None

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(GARBAGE_TAGS):
            tag.decompose()
        for selector in GARBAGE_CLASSES:
            for el in soup.select(selector):
                el.decompose()

        candidates = []
        for tag in soup.find_all(["div", "article", "section", "main"]):
            text = tag.get_text(strip=True)
            if len(text) < MIN_USEFUL_CHARS:
                continue
            p_count = len(tag.find_all("p"))
            link_density = len(tag.find_all("a")) / max(p_count, 1)
            score = len(text) + (p_count * 100) - (link_density * 200)
            candidates.append((score, text))

        if candidates:
            candidates.sort(reverse=True)
            text = candidates[0][1]
            if not is_paywalled(text):
                return text[:MAX_CONTENT_CHARS]
        return None
    except Exception:
        return None


async def strategy_ua_rotation(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Strategy 3: Try all user agents."""
    for i in range(1, len(USER_AGENTS)):  # Skip 0, already tried
        result = await strategy_direct(client, url, ua_index=i)
        if result:
            return result
    return None


async def strategy_wayback(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Strategy 4: Wayback Machine via CDX API."""
    try:
        cdx_url = f"https://web.archive.org/cdx/search/cdx?url={url}&output=json&limit=1&sort=reverse"
        r = await client.get(cdx_url, timeout=FETCH_TIMEOUT)
        if r.is_success:
            data = r.json()
            if len(data) > 1:  # First row is headers
                timestamp = data[1][1]
                snapshot_url = f"https://web.archive.org/web/{timestamp}/{url}"
                r2 = await client.get(
                    snapshot_url,
                    timeout=WAYBACK_TIMEOUT,
                    headers={"User-Agent": USER_AGENTS[0]},
                    follow_redirects=True,
                )
                if r2.is_success:
                    text = clean_html(r2.text)
                    if text:
                        return text
        return None
    except Exception:
        return None


async def strategy_google_cache(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Strategy 5: Google Cache."""
    try:
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
        r = await client.get(
            cache_url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENTS[0]},
            follow_redirects=True,
        )
        if r.is_success:
            text = clean_html(r.text)
            if text:
                return text
        return None
    except Exception:
        return None


async def strategy_search_about(
    client: httpx.AsyncClient, url: str, searxng_url: str
) -> Optional[str]:
    """Strategy 6: Can't read the page? Search for coverage elsewhere."""
    try:
        domain = urlparse(url).netloc
        path = urlparse(url).path.strip("/").replace("/", " ")
        query = f"{domain} {path}"[:80]

        r = await client.get(
            f"{searxng_url}/search",
            params={"q": query, "format": "json", "pageno": 1},
            timeout=FETCH_TIMEOUT,
        )
        if not r.is_success:
            return None

        results = r.json().get("results", [])
        for result in results[:3]:
            result_url = result.get("url", "")
            result_domain = urlparse(result_url).netloc
            if result_domain != domain and is_safe_url(result_url):
                content = await strategy_direct(client, result_url)
                if content:
                    return f"[Via coverage of {url}]\n\n{content}"
        return None
    except Exception:
        return None


def strategy_adapter_sync(url: str, obstacle_type: str) -> Optional[str]:
    """Strategy 7: Custom adapter from disk (sync — run in executor)."""
    adapter = _load_adapter(obstacle_type)
    if adapter:
        try:
            result = adapter(url)
            if result and len(result) >= MIN_USEFUL_CHARS:
                return result[:MAX_CONTENT_CHARS]
        except Exception:
            pass
    return None


async def strategy_adapter(url: str, obstacle_type: str) -> Optional[str]:
    """Strategy 7 wrapper: run sync adapter in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy_adapter_sync, url, obstacle_type)


def strategy_pdf_sync(url: str) -> Optional[str]:
    """Strategy 8: PDF extraction (sync — run in executor)."""
    try:
        import pdfplumber
    except ImportError:
        return None

    try:
        import requests as req
        r = req.get(url, timeout=30, headers={"User-Agent": USER_AGENTS[0]})
        r.raise_for_status()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(r.content)
            tmp_path = Path(tmp.name)
        parts = []
        with pdfplumber.open(tmp_path) as pdf:
            for page in pdf.pages[:50]:  # Cap at 50 pages
                t = page.extract_text()
                if t:
                    parts.append(t)
        tmp_path.unlink(missing_ok=True)
        text = "\n".join(parts)
        return text[:MAX_CONTENT_CHARS] if len(text) >= MIN_USEFUL_CHARS else None
    except Exception:
        return None


async def strategy_pdf(url: str) -> Optional[str]:
    """Strategy 8 wrapper."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy_pdf_sync, url)


def strategy_youtube_sync(url: str) -> Optional[str]:
    """Strategy 9: YouTube transcript via yt-dlp (sync)."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "subs")
            cmd = [
                "yt-dlp", "--write-auto-sub", "--sub-lang", "en",
                "--sub-format", "vtt", "--skip-download", "-o", out, url,
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=YT_DLP_TIMEOUT)
            vtt = Path(tmp) / "subs.en.vtt"
            if vtt.exists():
                raw = vtt.read_text()
                lines, seen = [], set()
                for line in raw.split("\n"):
                    line = line.strip()
                    if not line or line.startswith("WEBVTT") or re.match(r"^\d{2}:\d{2}", line) or "<" in line:
                        continue
                    clean = re.sub(r"<[^>]+>", "", line).strip()
                    if clean and clean not in seen:
                        seen.add(clean)
                        lines.append(clean)
                transcript = " ".join(lines)
                if len(transcript) >= MIN_USEFUL_CHARS:
                    return transcript[:MAX_CONTENT_CHARS]
        return None
    except Exception:
        return None


async def strategy_youtube(url: str) -> Optional[str]:
    """Strategy 9 wrapper."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy_youtube_sync, url)


# ---------------------------------------------------------------------------
# The Kill Chain
# ---------------------------------------------------------------------------

class KillChainResult:
    """Result of a kill chain extraction."""

    def __init__(
        self,
        url: str,
        content: Optional[str],
        strategy: Optional[str],
        chars: int,
        cached: bool,
        strategies_tried: list[str],
        error: Optional[str] = None,
    ):
        self.url = url
        self.content = content
        self.strategy = strategy
        self.chars = chars
        self.cached = cached
        self.strategies_tried = strategies_tried
        self.error = error

    @property
    def success(self) -> bool:
        return self.content is not None


async def kill_chain(
    client: httpx.AsyncClient,
    url: str,
    searxng_url: str = "http://searxng:8080",
    content_cache: Optional[ContentCache] = None,
    max_chars: Optional[int] = None,
    skip_cache: bool = False,
) -> KillChainResult:
    """
    Escalating content extraction. Tries every strategy until one works.
    Returns KillChainResult with content, strategy used, and metadata.
    """
    strategies_tried = []
    effective_max = max_chars or MAX_CONTENT_CHARS

    # Safety check
    if not is_safe_url(url, verbose=True):
        return KillChainResult(
            url=url, content=None, strategy=None, chars=0,
            cached=False, strategies_tried=[], error="URL blocked by safety check",
        )

    # Check cache
    if content_cache and not skip_cache:
        cached_content = await content_cache.get(url)
        if cached_content is not None:
            return KillChainResult(
                url=url, content=cached_content[:effective_max],
                strategy="cache", chars=len(cached_content),
                cached=True, strategies_tried=["cache"],
            )

    # YouTube gets its own path
    if _is_youtube(url):
        strategies_tried.append("youtube")
        content = await strategy_youtube(url)
        if content:
            content = sanitize_content(content)[:effective_max]
            if content_cache:
                await content_cache.set(url, content, "youtube")
            return KillChainResult(
                url=url, content=content, strategy="youtube",
                chars=len(content), cached=False, strategies_tried=strategies_tried,
            )
        return KillChainResult(
            url=url, content=None, strategy=None, chars=0,
            cached=False, strategies_tried=strategies_tried,
            error="YouTube transcript extraction failed",
        )

    # PDF gets its own path
    if _is_pdf_url(url):
        strategies_tried.append("pdf")
        content = await strategy_pdf(url)
        if content:
            content = sanitize_content(content)[:effective_max]
            if content_cache:
                await content_cache.set(url, content, "pdf")
            return KillChainResult(
                url=url, content=content, strategy="pdf",
                chars=len(content), cached=False, strategies_tried=strategies_tried,
            )
        return KillChainResult(
            url=url, content=None, strategy=None, chars=0,
            cached=False, strategies_tried=strategies_tried,
            error="PDF extraction failed",
        )

    # Web content: escalating strategies
    web_strategies = [
        ("direct", lambda: strategy_direct(client, url)),
        ("readability", lambda: strategy_readability(client, url)),
        ("ua-rotate", lambda: strategy_ua_rotation(client, url)),
        ("wayback", lambda: strategy_wayback(client, url)),
        ("google-cache", lambda: strategy_google_cache(client, url)),
        ("search-about", lambda: strategy_search_about(client, url, searxng_url)),
        ("adapter-403", lambda: strategy_adapter(url, "403_forbidden")),
        ("adapter-empty", lambda: strategy_adapter(url, "empty_content")),
        ("adapter-parse", lambda: strategy_adapter(url, "parse_error")),
    ]

    for name, strategy_fn in web_strategies:
        strategies_tried.append(name)
        try:
            content = await strategy_fn()
            if content:
                # Check for accidental PDF content
                if content.strip().startswith("%PDF"):
                    pdf_content = await strategy_pdf(url)
                    if pdf_content:
                        content = pdf_content

                content = sanitize_content(content)[:effective_max]
                logger.info(f"[{name}] {len(content):,} chars from {urlparse(url).netloc}")

                if content_cache:
                    await content_cache.set(url, content, name)

                return KillChainResult(
                    url=url, content=content, strategy=name,
                    chars=len(content), cached=False,
                    strategies_tried=strategies_tried,
                )
        except Exception as e:
            logger.debug(f"Strategy {name} failed for {url}: {e}")
        finally:
            gc.collect()

    logger.info(f"All strategies exhausted for {urlparse(url).netloc}")
    return KillChainResult(
        url=url, content=None, strategy=None, chars=0,
        cached=False, strategies_tried=strategies_tried,
        error="All extraction strategies failed",
    )


async def kill_chain_batch(
    client: httpx.AsyncClient,
    urls: list[str],
    searxng_url: str = "http://searxng:8080",
    content_cache: Optional[ContentCache] = None,
    max_chars: Optional[int] = None,
    max_concurrent: int = 5,
) -> list[KillChainResult]:
    """Run kill chain on multiple URLs with concurrency limit."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _limited(url: str) -> KillChainResult:
        async with semaphore:
            return await kill_chain(
                client, url, searxng_url, content_cache, max_chars,
            )

    return await asyncio.gather(*[_limited(u) for u in urls])
