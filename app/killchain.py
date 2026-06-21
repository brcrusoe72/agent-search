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
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.content_cache import ContentCache
from app.scrubber import scrub_content
from app.domain_trust import evaluate_trust, format_trust_tag, TrustResult

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ADAPTERS_DIR = Path(os.getenv("ADAPTERS_DIR", "adapters"))

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

MAX_CONTENT_CHARS = 15_000

BLOCKED_FETCH_DOMAINS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly",
    "buff.ly", "is.gd", "v.gd", "short.io",
    "media.defense.gov",  # 0% success rate — DoD site blocks all scraping
    "www.iiss.org",  # 0% success rate — aggressive Cloudflare + paywall (auto-blocked 2026-04-02)
    "saisreview.sais.jhu.edu",  # 0% success rate — academic paywall (auto-blocked 2026-04-02)
    "barrywehmiller.wd1.myworkdayjobs.com",  # 0% success rate — Workday JS app (auto-blocked 2026-04-02)
}

# Dynamic blocklist — loaded from file, written by evolver auto-apply
DYNAMIC_BLOCKLIST_PATH = Path(os.getenv("DATA_DIR", "data")) / "blocked_domains.txt"


def _load_dynamic_blocklist() -> set[str]:
    """Load additional blocked domains from evolver-managed file."""
    try:
        if DYNAMIC_BLOCKLIST_PATH.exists():
            domains = set()
            for line in DYNAMIC_BLOCKLIST_PATH.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    domains.add(line.lower())
            return domains
    except Exception as exc:
        logger.debug("Failed to load dynamic blocklist: %s", exc)
    return set()


# Merge static + dynamic blocklist
def get_blocked_domains() -> set[str]:
    """Return the full set of blocked domains (static + dynamic)."""
    return BLOCKED_FETCH_DOMAINS | _load_dynamic_blocklist()

SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",
    ".buzz", ".top", ".xyz", ".work", ".click",
}


# Note: Detailed injection/exfiltration/impersonation patterns are in scrubber.py
# The scrubber handles 70+ patterns, encoding detection, semantic analysis, etc.

FETCH_TIMEOUT = 15.0
MIN_USEFUL_CHARS = 200  # Content shorter than this is probably garbage
WAYBACK_TIMEOUT = 20.0
GOOGLE_CACHE_TIMEOUT = 5.0  # Google's public cache is effectively dead; fail fast
YT_DLP_TIMEOUT = 90
MAX_REDIRECTS = 5

import logging
logger = logging.getLogger("agentsearch.killchain")


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_matches(hostname: str, domain: str) -> bool:
    hostname = hostname.lower().removeprefix("www.")
    domain = domain.lower().removeprefix("www.")
    return hostname == domain or hostname.endswith("." + domain)


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
            logger.warning("BLOCKED: non-HTTP scheme '%s'", _safe_log_value(parsed.scheme))
        return False

    hostname = parsed.hostname
    if not hostname:
        if verbose:
            logger.warning("BLOCKED: no hostname in URL")
        return False

    # Warn on plain HTTP (content could be tampered via MITM)
    if parsed.scheme == "http" and verbose:
        logger.warning("DEGRADED: plain HTTP URL (no TLS) - %s", _safe_log_value(hostname))

    hostname_lower = hostname.lower()

    if hostname_lower in ("localhost", "localhost.localdomain", "127.0.0.1", "::1", "0.0.0.0"):
        if verbose:
            logger.warning("BLOCKED: localhost/loopback")
        return False

    all_blocked = get_blocked_domains()
    for blocked in all_blocked:
        if hostname_lower == blocked or hostname_lower.endswith("." + blocked):
            if verbose:
                logger.warning("BLOCKED: blocked domain '%s'", _safe_log_value(blocked))
            return False

    for tld in SUSPICIOUS_TLDS:
        if hostname_lower.endswith(tld):
            if verbose:
                logger.warning("BLOCKED: suspicious TLD '%s'", _safe_log_value(tld))
            return False

    # IP address check — block private/reserved ranges
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local:
            if verbose:
                logger.warning("BLOCKED: private/reserved IP %s", addr)
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
                            logger.warning(
                                "BLOCKED: '%s' resolves to private IP %s",
                                _safe_log_value(hostname),
                                addr,
                            )
                        return False
                except ValueError:
                    continue
        except (socket.gaierror, OSError) as exc:
            if verbose:
                logger.debug(
                    "DNS resolution failed for '%s': %s",
                    _safe_log_value(hostname),
                    exc,
                )

    return True


class UnsafeRedirectError(ValueError):
    """Raised when a redirect target fails the URL safety policy."""


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_redirects: int = MAX_REDIRECTS,
    **kwargs,
) -> httpx.Response:
    """GET a URL while validating every redirect hop before following it."""
    current_url = url
    request_kwargs = dict(kwargs)
    request_kwargs.pop("follow_redirects", None)
    for _ in range(max_redirects + 1):
        if not is_safe_url(current_url, verbose=True):
            raise UnsafeRedirectError(f"Unsafe URL blocked: {current_url}")

        response = await client.get(current_url, follow_redirects=False, **request_kwargs)
        if not response.is_redirect:
            return response

        location = response.headers.get("location")
        if not location:
            return response

        next_url = urljoin(str(response.url or current_url), location)
        if not is_safe_url(next_url, verbose=True):
            raise UnsafeRedirectError(f"Unsafe redirect blocked: {current_url} -> {next_url}")
        current_url = next_url

    raise UnsafeRedirectError(f"Too many redirects for URL: {url}")


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
    hostname = _hostname(url)
    return _host_matches(hostname, "youtube.com") or _host_matches(hostname, "youtu.be")


def _is_pdf_url(url: str) -> bool:
    return url.lower().rstrip("/").endswith(".pdf")


def _is_medium(url: str) -> bool:
    """Check if URL is a Medium article."""
    domain = _hostname(url)
    medium_domains = {
        "medium.com", "towardsdatascience.com", "betterprogramming.pub",
        "levelup.gitconnected.com", "javascript.plainenglish.io",
        "python.plainenglish.io", "blog.devgenius.io", "infosecwriteups.com",
    }
    return any(_host_matches(domain, medium_domain) for medium_domain in medium_domains)


def _load_medium_adapter():
    """Load the Medium adapter if available."""
    path = ADAPTERS_DIR / "medium.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("adapter_medium", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "fetch_content", None)
    except Exception as e:
        logger.warning("Failed to load medium adapter: %s", e)
        return None


async def _strategy_medium(url: str) -> Optional[str]:
    """Run the Medium adapter in a thread pool."""
    adapter = _load_medium_adapter()
    if not adapter:
        return None
    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, adapter, url)
    except Exception as e:
        logger.debug("Medium adapter failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Adapter Loading
# ---------------------------------------------------------------------------

def _load_cloudflare_adapter():
    """Load the Cloudflare bypass adapter if available."""
    path = ADAPTERS_DIR / "cloudflare_bypass.py"
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("adapter_cloudflare", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod  # returns module with can_handle() and extract()
    except Exception as e:
        logger.warning("Failed to load cloudflare adapter: %s", e)
        return None


async def _strategy_cloudflare(cf_mod, url: str) -> Optional[str]:
    """Run the Cloudflare bypass adapter's async extract."""
    try:
        result = await cf_mod.extract(url, timeout=30)
        if result and result.get("success") and result.get("content"):
            content = result["content"]
            strategy = result.get("strategy", "cloudflare-bypass")
            logger.info(
                "[%s] Cloudflare bypass succeeded for %s",
                _safe_log_value(strategy),
                _safe_log_value(_hostname(url)),
            )
            # Parse HTML to text if needed
            if "<html" in content.lower()[:500]:
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(content, "html.parser")
                    for tag in soup(["script", "style", "nav", "footer", "header"]):
                        tag.decompose()
                    content = soup.get_text(separator="\n", strip=True)
                except Exception as exc:
                    logger.debug("Cloudflare HTML cleanup failed: %s", exc)
            return content
    except Exception as e:
        logger.debug("Cloudflare bypass failed: %s", e)
    return None


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
        logger.warning("Failed to load adapter '%s': %s", _safe_log_value(name), e)
        return None


# ---------------------------------------------------------------------------
# Strategies (each returns str | None)
# ---------------------------------------------------------------------------

async def strategy_direct(client: httpx.AsyncClient, url: str, ua_index: int = 0) -> Optional[str]:
    """Strategy 1: Direct GET + BeautifulSoup."""
    try:
        r = await _safe_get(
            client,
            url,
            timeout=FETCH_TIMEOUT,
            headers={
                "User-Agent": USER_AGENTS[ua_index % len(USER_AGENTS)],
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
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
    except Exception as exc:
        logger.debug("Direct strategy failed for %s: %s", _safe_log_value(_hostname(url)), exc)
        return None


async def strategy_readability(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Strategy 2: Readability-style extraction — score blocks by paragraph density."""
    try:
        r = await _safe_get(
            client,
            url,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": USER_AGENTS[0]},
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
    except Exception as exc:
        logger.debug("Readability strategy failed for %s: %s", _safe_log_value(_hostname(url)), exc)
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
        r = await client.get(
            "https://web.archive.org/cdx/search/cdx",
            params={"url": url, "output": "json", "limit": "1", "sort": "reverse"},
            timeout=FETCH_TIMEOUT,
        )
        if r.is_success:
            data = r.json()
            if len(data) > 1:  # First row is headers
                timestamp = data[1][1]
                snapshot_url = f"https://web.archive.org/web/{timestamp}/{url}"
                r2 = await _safe_get(
                    client,
                    snapshot_url,
                    timeout=WAYBACK_TIMEOUT,
                    headers={"User-Agent": USER_AGENTS[0]},
                )
                if r2.is_success:
                    text = clean_html(r2.text)
                    if text:
                        return text
        return None
    except Exception as exc:
        logger.debug("Wayback strategy failed for %s: %s", _safe_log_value(_hostname(url)), exc)
        return None


async def strategy_google_cache(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Strategy 5: Google Cache.

    Google cache pages wrap original content in a div. We need to strip
    Google's header/banner and extract the actual cached page content.
    """
    try:
        cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
        r = await _safe_get(
            client,
            cache_url,
            timeout=GOOGLE_CACHE_TIMEOUT,
            headers={"User-Agent": USER_AGENTS[0]},
        )
        if r.is_success:
            html = r.text
            soup = BeautifulSoup(html, "html.parser")

            # Google Cache wraps content — remove Google's own header div
            for div in soup.find_all("div", style=lambda s: s and "background-color" in (s or "")):
                # Google's cache banner has inline background-color styles
                if "cache" in div.get_text().lower() or "google" in div.get_text().lower():
                    div.decompose()

            # Also remove Google's top bar and any script/style
            for tag in soup(GARBAGE_TAGS):
                tag.decompose()

            # Try targeted content selectors on the cached page
            for selector in CONTENT_SELECTORS:
                el = soup.select_one(selector)
                if el:
                    text = el.get_text(separator="\n", strip=True)
                    if len(text) >= MIN_USEFUL_CHARS:
                        return text[:MAX_CONTENT_CHARS]

            # Fallback: full body text
            text = soup.get_text(separator="\n", strip=True)
            if text and len(text) >= MIN_USEFUL_CHARS:
                return text[:MAX_CONTENT_CHARS]
        return None
    except Exception as exc:
        logger.debug("Google cache strategy failed for %s: %s", _safe_log_value(_hostname(url)), exc)
        return None


async def strategy_search_about(
    client: httpx.AsyncClient, url: str, searxng_url: str
) -> Optional[str]:
    """Strategy 6: Can't read the page? Search for coverage elsewhere."""
    try:
        domain = _hostname(url)
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
            result_domain = _hostname(result_url)
            if result_domain != domain and is_safe_url(result_url):
                content = await strategy_direct(client, result_url)
                if content:
                    return f"[Via coverage of {url}]\n\n{content}"
        return None
    except Exception as exc:
        logger.debug("Search-about strategy failed for %s: %s", _safe_log_value(_hostname(url)), exc)
        return None


def strategy_adapter_sync(url: str, obstacle_type: str) -> Optional[str]:
    """Strategy 7: Custom adapter from disk (sync — run in executor)."""
    adapter = _load_adapter(obstacle_type)
    if adapter:
        try:
            result = adapter(url)
            if result and len(result) >= MIN_USEFUL_CHARS:
                return result[:MAX_CONTENT_CHARS]
        except Exception as exc:
            logger.debug("Adapter '%s' failed for %s: %s", obstacle_type, _safe_log_value(_hostname(url)), exc)
    return None


async def strategy_adapter(url: str, obstacle_type: str) -> Optional[str]:
    """Strategy 7 wrapper: run sync adapter in thread pool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy_adapter_sync, url, obstacle_type)


MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB max PDF download
PDF_PARSE_TIMEOUT = 60  # seconds


def strategy_pdf_sync(url: str) -> Optional[str]:
    """Strategy 8: PDF extraction with size limits and subprocess isolation."""
    try:
        import requests as req

        # Stream download with size check
        current_url = url
        r = None
        for _ in range(MAX_REDIRECTS + 1):
            if not is_safe_url(current_url, verbose=True):
                raise UnsafeRedirectError(f"Unsafe URL blocked: {current_url}")

            r = req.get(
                current_url,
                timeout=30,
                headers={"User-Agent": USER_AGENTS[0]},
                stream=True,
                allow_redirects=False,
            )
            if not 300 <= r.status_code < 400:
                break

            location = r.headers.get("Location")
            r.close()
            if not location:
                break

            next_url = urljoin(current_url, location)
            if not is_safe_url(next_url, verbose=True):
                raise UnsafeRedirectError(f"Unsafe redirect blocked: {current_url} -> {next_url}")
            current_url = next_url
        else:
            raise UnsafeRedirectError(f"Too many redirects for URL: {url}")

        assert r is not None
        r.raise_for_status()

        # Check Content-Length header first
        content_length = r.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_PDF_BYTES:
            logger.warning(
                "PDF too large (%s bytes): %s",
                int(content_length),
                _safe_log_value(url),
            )
            return None

        # Download with running size check
        chunks = []
        total = 0
        for chunk in r.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                logger.warning(
                    "PDF exceeded %s bytes during download: %s",
                    MAX_PDF_BYTES,
                    _safe_log_value(url),
                )
                return None
            chunks.append(chunk)

        pdf_bytes = b"".join(chunks)
        del chunks

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        # Run pdfplumber in a subprocess with resource limits
        extract_script = (
            "import sys, json, pdfplumber, resource; "
            "resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024, 512*1024*1024)); "  # 512MB RAM cap
            "pdf = pdfplumber.open(sys.argv[1]); "
            "parts = []; "
            "[parts.append(p.extract_text()) for p in pdf.pages[:50] if p.extract_text()]; "
            "print(json.dumps('\\n'.join(parts)))"
        )
        try:
            result = subprocess.run(
                ["python3", "-c", extract_script, tmp_path],
                capture_output=True, text=True,
                timeout=PDF_PARSE_TIMEOUT,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json
                text = json.loads(result.stdout.strip())
                return text[:MAX_CONTENT_CHARS] if len(text) >= MIN_USEFUL_CHARS else None
            else:
                logger.debug("PDF subprocess failed: %s", _safe_log_value(result.stderr[:200]))
                return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)
    except Exception as e:
        logger.debug("PDF extraction failed: %s", e)
        return None


async def strategy_pdf(url: str) -> Optional[str]:
    """Strategy 8 wrapper."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy_pdf_sync, url)


def strategy_youtube_sync(url: str) -> Optional[str]:
    """Strategy 9: YouTube transcript via yt-dlp with resource limits (sync)."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "subs")
            cmd = [
                "yt-dlp", "--write-auto-sub", "--sub-lang", "en",
                "--sub-format", "vtt", "--skip-download",
                "--no-playlist",  # Never expand playlists
                "--max-downloads", "1",  # Single video only
                "-o", out, url,
            ]
            subprocess.run(
                cmd, capture_output=True, text=True, timeout=YT_DLP_TIMEOUT,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
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
    except Exception as exc:
        logger.debug("YouTube transcript extraction failed for %s: %s", _safe_log_value(url), exc)
        return None


async def strategy_youtube(url: str) -> Optional[str]:
    """Strategy 9 wrapper."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, strategy_youtube_sync, url)


async def strategy_browser_render(url: str, max_chars: int | None = None) -> Optional[str]:
    """Browser-rendered extraction for JS-rendered pages.

    This renderer does not solve challenges or bypass blocked search engines.
    It returns content only when an ephemeral browser context can render useful
    page text without hitting a CAPTCHA/challenge page.
    """
    try:
        from app.browser_renderer import render_browser_page

        result = await render_browser_page(url, max_chars=max_chars or MAX_CONTENT_CHARS)
        if result.success and result.content:
            return result.content
        if result.challenge_detected:
            logger.info("Browser render detected challenge for %s", _safe_log_value(_hostname(url)))
        elif result.error:
            logger.debug("Browser render failed for %s: %s", _safe_log_value(_hostname(url)), result.error)
        return None
    except Exception as exc:
        logger.debug("Browser render strategy failed for %s: %s", _safe_log_value(_hostname(url)), exc)
        return None


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
        trust: Optional[TrustResult] = None,
    ):
        self.url = url
        self.content = content
        self.strategy = strategy
        self.chars = chars
        self.cached = cached
        self.strategies_tried = strategies_tried
        self.error = error
        self.trust = trust

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

    # Domain trust evaluation (skip WHOIS for speed — batch callers can re-check)
    trust = evaluate_trust(url, check_whois=False)

    # Block suspicious domains outright — but never block trusted TLDs (.gov/.edu/.mil)
    # The typosquat detector can false-positive on legitimate government domains (e.g. congress.gov → cnn)
    _SAFE_TLDS = (".gov", ".edu", ".mil", ".int")
    _is_trusted_tld = any(trust.domain.endswith(t) for t in _SAFE_TLDS)
    if trust.lookalike_of and not _is_trusted_tld:
        logger.warning(
            "Blocked typosquat: %s (lookalike of %s)",
            _safe_log_value(trust.domain),
            _safe_log_value(trust.lookalike_of),
        )
        return KillChainResult(
            url=url, content=None, strategy=None, chars=0,
            cached=False, strategies_tried=[],
            error=f"Blocked: possible typosquat of '{trust.lookalike_of}'",
            trust=trust,
        )

    # Tag content with provenance
    def _tag_content(content: str) -> str:
        """Prepend trust tag to content so consuming agents see provenance."""
        if trust.tier in ("suspicious", "new"):
            return format_trust_tag(trust) + "\n\n" + content
        return content

    # Check cache
    if content_cache and not skip_cache:
        cached_content = await content_cache.get(url)
        if cached_content is not None:
            return KillChainResult(
                url=url, content=cached_content[:effective_max],
                strategy="cache", chars=len(cached_content),
                cached=True, strategies_tried=["cache"],
                trust=trust,
            )

    # Medium gets its own path — dedicated adapter with paywall bypass
    if _is_medium(url):
        strategies_tried.append("medium-adapter")
        content = await _strategy_medium(url)
        if content:
            content = sanitize_content(content)[:effective_max]
            if content_cache:
                await content_cache.set(url, content, "medium-adapter")
            return KillChainResult(
                url=url, content=content, strategy="medium-adapter",
                chars=len(content), cached=False, strategies_tried=strategies_tried,
                trust=trust,
            )
        # Fall through to normal chain if medium adapter fails

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
                trust=trust,
            )
        return KillChainResult(
            url=url, content=None, strategy=None, chars=0,
            cached=False, strategies_tried=strategies_tried,
            error="YouTube transcript extraction failed",
            trust=trust,
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
                trust=trust,
            )
        return KillChainResult(
            url=url, content=None, strategy=None, chars=0,
            cached=False, strategies_tried=strategies_tried,
            error="PDF extraction failed",
            trust=trust,
        )

    # Check if URL is a known Cloudflare-protected domain — try CF adapter early
    _cf_adapter = None
    try:
        _cf_mod = _load_cloudflare_adapter()
        if _cf_mod and _cf_mod.can_handle(url):
            _cf_adapter = _cf_mod
    except Exception as exc:
        logger.debug("Cloudflare adapter detection failed for %s: %s", _safe_log_value(_hostname(url)), exc)

    # Web content: escalating strategies
    web_strategies = [
        ("direct", lambda: strategy_direct(client, url)),
        ("readability", lambda: strategy_readability(client, url)),
        ("ua-rotate", lambda: strategy_ua_rotation(client, url)),
        ("browser-render", lambda: strategy_browser_render(url, effective_max)),
    ]

    # If Cloudflare detected for this domain, insert CF adapter before wayback/cache
    if _cf_adapter:
        web_strategies.append(("cloudflare-bypass", lambda: _strategy_cloudflare(_cf_adapter, url)))

    web_strategies.extend([
        ("wayback", lambda: strategy_wayback(client, url)),
        ("google-cache", lambda: strategy_google_cache(client, url)),
        ("search-about", lambda: strategy_search_about(client, url, searxng_url)),
        ("adapter-403", lambda: strategy_adapter(url, "403_forbidden")),
        ("adapter-empty", lambda: strategy_adapter(url, "empty_content")),
        ("adapter-parse", lambda: strategy_adapter(url, "parse_error")),
    ])

    for name, strategy_fn in web_strategies:
        strategies_tried.append(name)
        try:
            content = await strategy_fn()
            if content:
                # Cloudflare detected mid-chain: if direct/readability/ua-rotate got a challenge
                # page, try the CF adapter before giving up on this strategy
                if not _cf_adapter and name in ("direct", "readability", "ua-rotate"):
                    try:
                        _cf_mod = _load_cloudflare_adapter()
                        if _cf_mod and _cf_mod.can_handle(url, content=content):
                            _cf_adapter = _cf_mod
                            # Insert CF strategy right after current position
                            logger.info(
                                "Cloudflare challenge detected on %s, engaging bypass",
                                _safe_log_value(_hostname(url)),
                            )
                            cf_content = await _strategy_cloudflare(_cf_adapter, url)
                            if cf_content:
                                content = cf_content
                                name = "cloudflare-bypass"
                            else:
                                continue  # Skip this challenge page
                    except Exception as exc:
                        logger.debug("Cloudflare challenge handling failed: %s", exc)

                # Check for accidental PDF content
                if content.strip().startswith("%PDF"):
                    pdf_content = await strategy_pdf(url)
                    if pdf_content:
                        content = pdf_content

                content = sanitize_content(content)[:effective_max]

                # Skip garbage — too short to be real content
                if len(content.strip()) < MIN_USEFUL_CHARS:
                    logger.debug(
                        "[%s] Only %s chars - below minimum, skipping",
                        _safe_log_value(name),
                        len(content.strip()),
                    )
                    continue

                content = _tag_content(content)
                logger.info(
                    "[%s] %s chars from %s [trust=%s]",
                    _safe_log_value(name),
                    f"{len(content):,}",
                    _safe_log_value(_hostname(url)),
                    _safe_log_value(trust.tier),
                )

                if content_cache:
                    await content_cache.set(url, content, name)

                return KillChainResult(
                    url=url, content=content, strategy=name,
                    chars=len(content), cached=False,
                    strategies_tried=strategies_tried,
                    trust=trust,
                )
        except Exception as e:
            logger.debug(
                "Strategy %s failed for %s: %s",
                _safe_log_value(name),
                _safe_log_value(url),
                e,
            )
        finally:
            gc.collect()

    logger.info("All strategies exhausted for %s", _safe_log_value(_hostname(url)))
    return KillChainResult(
        url=url, content=None, strategy=None, chars=0,
        cached=False, strategies_tried=strategies_tried,
        error="All extraction strategies failed",
        trust=trust,
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
