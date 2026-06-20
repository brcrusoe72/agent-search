"""
domain_trust.py — Domain Reputation & Authenticity Engine

Defends against page impersonation / spoofed content by scoring domain
trustworthiness. Every piece of returned content gets tagged with a
trust tier so consuming agents can weight accordingly.

Signals used:
  1. Known-good allowlist (major news, think tanks, gov, edu, etc.)
  2. Suspicious TLD check
  3. Domain age via WHOIS (new domains = lower trust)
  4. Lookalike / typosquat detection (Levenshtein against known brands)
  5. HTTPS enforcement
  6. Content provenance tagging

Trust tiers:
  - "established"  — known-good domain or well-aged + reputable TLD
  - "standard"     — HTTPS, reasonable age, no red flags
  - "new"          — domain < 180 days old
  - "suspicious"   — bad TLD, no HTTPS, or lookalike detected
  - "unknown"      — couldn't determine (WHOIS failed, etc.)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger("agentsearch.domain_trust")


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


# ---------------------------------------------------------------------------
# Known-Good Domains (authoritative sources)
# ---------------------------------------------------------------------------

ESTABLISHED_DOMAINS: set[str] = {
    # Major news
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "npr.org",
    "nytimes.com", "washingtonpost.com", "wsj.com", "economist.com",
    "ft.com", "theguardian.com", "bloomberg.com", "cnbc.com", "cnn.com",
    "aljazeera.com", "dw.com", "france24.com",
    # Think tanks & policy
    "csis.org", "rand.org", "brookings.edu", "cfr.org", "carnegieendowment.org",
    "chathamhouse.org", "iiss.org", "piie.com", "atlanticcouncil.org",
    "heritage.org", "aei.org", "cato.org", "newamerica.org",
    # Government
    "state.gov", "defense.gov", "whitehouse.gov", "congress.gov",
    "gao.gov", "cbo.gov", "cia.gov", "fbi.gov", "nasa.gov",
    "gov.uk", "europa.eu", "un.org", "nato.int", "who.int",
    # Academic
    "nature.com", "science.org", "thelancet.com", "nejm.org",
    "arxiv.org", "ssrn.com", "jstor.org", "pubmed.ncbi.nlm.nih.gov",
    # Tech
    "github.com", "stackoverflow.com", "wikipedia.org", "wikimedia.org",
    "developer.mozilla.org", "docs.python.org",
    # Reference
    "archive.org", "law.cornell.edu", "courtlistener.com",
}

# TLDs that are generally trustworthy
TRUSTED_TLDS = {".gov", ".edu", ".mil", ".int", ".museum"}

# Suspicious TLDs (already in killchain but duplicated here for scoring)
SUSPICIOUS_TLDS = {
    ".tk", ".ml", ".ga", ".cf", ".gq",
    ".buzz", ".top", ".xyz", ".work", ".click",
    ".loan", ".win", ".racing", ".review", ".stream",
    ".download", ".bid", ".trade", ".date", ".faith",
    ".party", ".science", ".cricket", ".accountant",
}

# Known brands for typosquat detection
KNOWN_BRANDS = [
    "google", "amazon", "microsoft", "apple", "facebook", "twitter",
    "github", "wikipedia", "reuters", "bloomberg", "nytimes",
    "washingtonpost", "cnn", "bbc", "netflix", "paypal", "chase",
    "wellsfargo", "bankofamerica", "linkedin", "openai", "anthropic",
]

# Legitimate hosting platforms that serve user/project pages on subdomains.
# These are not impersonation risks in themselves, and per-subdomain WHOIS is
# meaningless (the registrable domain is the platform, not the user), so we give
# them a standard floor and skip the age check. Brand/typosquat detection still
# runs on the subdomain, so an actual impersonation (e.g. "netflixx.github.io")
# is still caught.
PLATFORM_DOMAINS = {
    "github.io", "gitlab.io", "pages.dev", "netlify.app", "vercel.app",
    "readthedocs.io", "gitbook.io", "sourceforge.net",
}


# ---------------------------------------------------------------------------
# Trust Result
# ---------------------------------------------------------------------------

@dataclass
class TrustResult:
    domain: str
    tier: str              # established | standard | new | suspicious | unknown
    score: float           # 0.0 (untrusted) to 1.0 (fully trusted)
    reasons: list[str]
    https: bool
    domain_age_days: Optional[int] = None
    lookalike_of: Optional[str] = None


# ---------------------------------------------------------------------------
# Typosquat / Lookalike Detection
# ---------------------------------------------------------------------------

def _levenshtein(s1: str, s2: str) -> int:
    """Simple Levenshtein distance."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


def detect_lookalike(domain: str) -> Optional[str]:
    """Check if domain looks like a typosquat of a known brand.

    Returns the brand name if a lookalike is detected, None otherwise.
    """
    # Extract the registrable part (e.g. "g00gle" from "g00gle-news.xyz")
    parts = domain.replace("www.", "").split(".")
    if len(parts) < 2:
        return None
    base = parts[0]  # e.g. "g00gle-news"

    # Also check without hyphens
    base_clean = base.replace("-", "").replace("_", "")

    for brand in KNOWN_BRANDS:
        # Exact match = not a lookalike, it IS the brand
        if base == brand or base_clean == brand:
            return None

        # Check Levenshtein distance
        dist = min(_levenshtein(base, brand), _levenshtein(base_clean, brand))

        # Short brands need closer matches
        threshold = 1 if len(brand) <= 5 else 2

        if 0 < dist <= threshold:
            return brand

        # Check for brand embedded with extra chars (e.g. "google-verify")
        if brand in base and len(base) - len(brand) <= 8:
            return brand

        # Check if a brand-length prefix is a near-match (e.g. "g00gle-news" → "g00gle" vs "google").
        # Guard against the "brc" vs "bbc" false positive: a long, unrelated label must not match a
        # short brand just because its first few characters happen to be close. Only run this when the
        # brand is distinctive enough AND the label is either marginally longer than the brand or
        # splits the brand off with a separator — the actual shapes a typosquat takes.
        if len(brand) >= 4 and len(base_clean) > len(brand):
            looks_like_squat = ("-" in base) or ("_" in base) or (len(base_clean) - len(brand) <= 2)
            if looks_like_squat:
                prefix = base_clean[:len(brand)]
                prefix_dist = _levenshtein(prefix, brand)
                if 0 < prefix_dist <= threshold:
                    return brand

    return None


# ---------------------------------------------------------------------------
# Domain Age Check (WHOIS)
# ---------------------------------------------------------------------------

_whois_cache: dict[str, Optional[int]] = {}


def _get_domain_age_days(domain: str) -> Optional[int]:
    """Get domain age in days via WHOIS. Returns None on failure."""
    if domain in _whois_cache:
        return _whois_cache[domain]

    try:
        import whois
        w = whois.whois(domain)
        creation = w.creation_date
        if isinstance(creation, list):
            creation = creation[0]
        if creation:
            import datetime
            age = (datetime.datetime.now() - creation).days
            _whois_cache[domain] = age
            return age
    except Exception as e:
        logger.debug("WHOIS lookup failed for %s: %s", _safe_log_value(domain), e)

    _whois_cache[domain] = None
    return None


# ---------------------------------------------------------------------------
# Main Trust Evaluation
# ---------------------------------------------------------------------------

def evaluate_trust(url: str, check_whois: bool = True) -> TrustResult:
    """Evaluate the trustworthiness of a URL's domain.

    Args:
        url: The URL to evaluate.
        check_whois: Whether to do a WHOIS lookup for domain age.
                     Set False for batch operations (slow).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    is_https = scheme == "https"

    reasons = []
    score = 0.5  # Start neutral

    if not hostname:
        return TrustResult(
            domain="", tier="suspicious", score=0.0,
            reasons=["No hostname in URL"], https=False,
        )

    # Strip www
    domain = hostname.removeprefix("www.")

    # 1. Check established allowlist
    if domain in ESTABLISHED_DOMAINS or any(domain.endswith("." + d) for d in ESTABLISHED_DOMAINS):
        reasons.append(f"Known-good domain: {domain}")
        score = 1.0

    # 2. Check trusted TLDs (.gov, .edu, .mil)
    for tld in TRUSTED_TLDS:
        if domain.endswith(tld):
            reasons.append(f"Trusted TLD: {tld}")
            score = max(score, 0.9)
            break

    # 3. Check suspicious TLDs
    for tld in SUSPICIOUS_TLDS:
        if domain.endswith(tld):
            reasons.append(f"Suspicious TLD: {tld}")
            score = min(score, 0.2)
            break

    # 3b. Known hosting platforms (GitHub Pages, etc.) — legitimate, but per-subdomain
    #     WHOIS is pointless. Give a standard floor and skip the age lookup below.
    is_platform = any(domain == d or domain.endswith("." + d) for d in PLATFORM_DOMAINS)
    if is_platform:
        reasons.append("Hosted on known platform")
        score = max(score, 0.6)

    # 4. HTTPS check
    if is_https:
        reasons.append("HTTPS")
        score = min(score + 0.05, 1.0)
    else:
        reasons.append("No HTTPS — content may be tampered")
        score = min(score, 0.3)

    # 5. Lookalike detection — skip for established domains and trusted TLDs
    _skip_lookalike = (
        domain in ESTABLISHED_DOMAINS
        or any(domain.endswith("." + d) for d in ESTABLISHED_DOMAINS)
        or any(domain.endswith(t) for t in TRUSTED_TLDS)
    )
    lookalike = None if _skip_lookalike else detect_lookalike(domain)
    if lookalike:
        reasons.append(f"Possible typosquat of '{lookalike}'")
        score = min(score, 0.1)

    # 6. Domain age (optional, slow)
    age_days = None
    if check_whois and score < 0.9 and not is_platform:  # Skip for known-good and platform hosts
        age_days = _get_domain_age_days(domain)
        if age_days is not None:
            if age_days < 30:
                reasons.append(f"Very new domain ({age_days} days)")
                score = min(score, 0.15)
            elif age_days < 180:
                reasons.append(f"New domain ({age_days} days)")
                score = min(score, 0.35)
            elif age_days > 365 * 3:
                reasons.append(f"Established domain ({age_days // 365}+ years)")
                score = min(score + 0.15, 1.0)

    # Determine tier
    if score >= 0.85:
        tier = "established"
    elif score >= 0.5:
        tier = "standard"
    elif score >= 0.3:
        tier = "new"
    elif score > 0.0:
        tier = "suspicious"
    else:
        tier = "unknown"

    return TrustResult(
        domain=domain,
        tier=tier,
        score=round(score, 2),
        reasons=reasons,
        https=is_https,
        domain_age_days=age_days,
        lookalike_of=lookalike,
    )


def format_trust_tag(result: TrustResult) -> str:
    """Format a trust result as a short tag to prepend to content."""
    icons = {
        "established": "🟢",
        "standard": "🔵",
        "new": "🟡",
        "suspicious": "🔴",
        "unknown": "⚪",
    }
    icon = icons.get(result.tier, "⚪")
    tag = f"[{icon} {result.tier} source: {result.domain}"
    if result.lookalike_of:
        tag += f" ⚠️ possible typosquat of '{result.lookalike_of}'"
    if not result.https:
        tag += " ⚠️ no HTTPS"
    tag += "]"
    return tag
