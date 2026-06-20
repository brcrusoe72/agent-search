"""Tests for domain_trust typosquat / lookalike detection.

Regression guard for the "brc" vs "bbc" false positive: a long, unrelated
subdomain label (e.g. "brcrusoe72.github.io") must not be flagged as a typosquat
of a short brand just because its first few characters happen to be close.
The brand-length-prefix heuristic used to fire on the first len(brand) chars of
any label, matching "brc" against "bbc" at Levenshtein distance 1.
"""

import pytest

from app.domain_trust import detect_lookalike, evaluate_trust


@pytest.mark.parametrize(
    "domain, expected",
    [
        # --- Must be CLEAN (None): the regression and other false-positive shapes ---
        ("brcrusoe72.github.io", None),   # the bug: "brc" prefix vs "bbc" brand
        ("brave.com", None),              # starts ~"bbc"-ish, unrelated
        ("crusoeresearch.io", None),      # unrelated long label
        ("microsoft.com", None),          # the real brand itself is not a squat
        ("github.com", None),             # real brand itself
        ("apple.com", None),              # real brand itself

        # --- Must still be CAUGHT: genuine typosquats ---
        ("bbc-news.xyz", "bbc"),          # short brand embedded + suffix
        ("bbcc.com", "bbc"),              # short brand, one extra char
        ("g00gle-news.com", "google"),    # digit-substituted prefix + suffix
        ("paypa1.com", "paypal"),         # char-swap of a longer brand
        ("amaz0n-login.tk", "amazon"),    # brand + separator + suffix
        ("netflixx.github.io", "netflix"),  # impersonation ON a hosting platform
    ],
)
def test_detect_lookalike(domain, expected):
    assert detect_lookalike(domain) == expected


def test_github_pages_is_not_suspicious():
    """A user's GitHub Pages site should resolve to a usable tier, never blocked."""
    result = evaluate_trust("https://brcrusoe72.github.io/trueaicost/", check_whois=False)
    assert result.lookalike_of is None
    assert result.tier in ("standard", "established")
    assert result.score >= 0.5


def test_real_typosquat_on_platform_is_still_flagged():
    """Platform recognition must not disable impersonation detection on subdomains."""
    result = evaluate_trust("https://netflixx.github.io/", check_whois=False)
    assert result.lookalike_of == "netflix"
    assert result.tier == "suspicious"


def test_known_platform_skips_whois_and_floors_score():
    """github.io and friends get a standard floor without a per-subdomain WHOIS."""
    result = evaluate_trust("https://someproject.gitlab.io/", check_whois=True)
    assert result.lookalike_of is None
    assert result.score >= 0.6
    assert result.domain_age_days is None  # WHOIS skipped for platform hosts
