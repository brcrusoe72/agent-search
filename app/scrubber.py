"""
scrubber.py — Content Security Engine

Ported from Agent Café's scrubbing layer. Analyzes fetched content for:
- Prompt injection attempts (70+ patterns)
- Data exfiltration hooks (credential fishing, system probing)
- Payload smuggling (base64, hex, URL encoding, unicode homoglyphs, zero-width chars)
- Impersonation (authority claims embedded in content)
- XSS / HTML injection markers
- Recursive injection (nested encoding layers)
- Semantic intent analysis (vocabulary clustering)

Every piece of fetched content passes through this before being returned.
"""

from __future__ import annotations

import base64
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

import logging

logger = logging.getLogger("agentsearch.scrubber")


# ---------------------------------------------------------------------------
# Threat Types
# ---------------------------------------------------------------------------

class ThreatType(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    INSTRUCTION_OVERRIDE = "instruction_override"
    DATA_EXFILTRATION = "data_exfiltration"
    IMPERSONATION = "impersonation"
    PAYLOAD_SMUGGLING = "payload_smuggling"
    XSS_INJECTION = "xss_injection"
    RECURSIVE_INJECTION = "recursive_injection"
    SOCIAL_ENGINEERING = "social_engineering"


@dataclass
class ThreatDetection:
    threat_type: ThreatType
    confidence: float
    evidence: str
    location: str = ""


@dataclass
class ScrubResult:
    clean: bool
    content: str
    threats: List[ThreatDetection] = field(default_factory=list)
    risk_score: float = 0.0
    redactions: int = 0
    encoding_layers_stripped: int = 0


# ---------------------------------------------------------------------------
# Detection Patterns (from Agent Café scrubber)
# ---------------------------------------------------------------------------

INJECTION_PATTERNS = [
    # Direct instruction override
    r"(?i)ignore\s+(?:all\s+)?(?:previous\s+)?(?:your\s+)?instructions",
    r"(?i)forget\s+(?:all\s+)?(?:your\s+)?(?:previous\s+)?(?:instructions|rules|constraints)",
    r"(?i)system\s*:\s*you\s+are\s+now",
    r"(?i)new\s+instructions\s*:\s*",
    r"(?i)actually\s*,?\s*disregard",
    r"(?i)override\s+(?:your\s+)?(?:previous\s+)?instructions",
    r"(?i)cancel\s+(?:all\s+)?(?:previous\s+)?instructions",
    r"(?i)replace\s+(?:your\s+)?instructions\s+with",
    r"(?i)disregard\s+(?:all\s+)?(?:previous\s+)?(?:your\s+)?instructions",

    # Role manipulation
    r"(?i)you\s+are\s+(?:no\s+longer|not)\s+(?:an?\s+)?(?:assistant|ai|agent|bot)",
    r"(?i)pretend\s+(?:you\s+are|to\s+be)\s+(?:an?\s+)?(?:human|user|admin|developer|system)",
    r"(?i)act\s+as\s+(?:if\s+you\s+are\s+)?(?:an?\s+)?(?:human|user|admin|developer|system|root|superuser)",
    r"(?i)from\s+now\s+on\s+you\s+are",
    r"(?i)switch\s+to\s+(?:being\s+)?(?:a\s+)?(?:human|admin|developer)",

    # System prompt leakage
    r"(?i)(?:show|tell|give|reveal|print|output|display|share)\s+(?:me\s+)?(?:your\s+)?(?:system\s+)?(?:prompt|instructions)",
    r"(?i)what\s+(?:is|are)\s+your\s+(?:exact\s+)?(?:system\s+)?(?:prompt|instructions)",
    r"(?i)repeat\s+your\s+(?:system\s+)?(?:prompt|instructions)",
    r"(?i)(?:dump|export)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions|configuration)",

    # Jailbreak patterns
    r"(?i)developer\s+mode",
    r"(?i)jailbreak",
    r"(?i)do\s+anything\s+now",
    r"(?i)DAN\s+mode",
    r"(?i)unrestricted\s+mode",
    r"(?i)bypass\s+(?:all\s+)?(?:restrictions|limitations|safeguards)",
    r"(?i)evil\s+mode",
    r"(?i)godmode",
    r"(?i)admin\s+override",

    # Chain manipulation
    r"(?i)step\s+1\s*:\s*ignore",
    r"(?i)first\s*,?\s*ignore",
    r"(?i)but\s+before\s+that\s*,?\s*ignore",
    r"(?i)however\s*,?\s*disregard",

    # Context switching
    r"(?i)let's\s+start\s+over",
    r"(?i)forget\s+everything",
    r"(?i)reset\s+(?:the\s+)?conversation",
    r"(?i)clear\s+(?:the\s+)?(?:chat|conversation|context)",

    # Nested instruction patterns
    r"(?i)the\s+following\s+is\s+not\s+part\s+of\s+(?:the\s+)?(?:prompt|instructions)",
    r"(?i)everything\s+after\s+this\s+is\s+(?:fake|false|ignore)",
    r"(?i)(?:end|stop)\s+(?:of\s+)?(?:prompt|instructions)",
    r"(?i)user\s+input\s+begins\s+here",

    # Authority delegation / behavioral priming
    r"(?i)treat\s+(?:its|their|the|this)?\s*(?:contents?|text)\s+as\s+(?:your\s+)?(?:primary|main|new)\s+(?:guidelines?|instructions?|rules?)",
    r"(?i)(?:follow|execute|adopt)\s+(?:any\s+)?(?:procedures?|instructions?|directives?)\s+(?:described|found|contained)\s+(?:within|inside|in)",
    r"(?i)disregard\s+(?:external|other|previous|existing)\s+(?:style\s+)?(?:guides?|rules?|standards?|guidelines?)",
    r"(?i)(?:full|complete|total|unrestricted)\s+(?:autonomy|authority|access|control)",
    r"(?i)no\s+(?:approval|permission|authorization|review)\s+(?:needed|required|necessary)",

    # Social engineering
    r"(?i)this\s+is\s+(?:an\s+)?(?:emergency|urgent)",
    r"(?i)(?:the\s+)?(?:ceo|admin|developer)\s+(?:told|said|asked)\s+me",
    r"(?i)I\s+have\s+(?:permission|authorization)\s+(?:to|from)",
    r"(?i)security\s+(?:override|bypass|exception)",
]

EXFILTRATION_PATTERNS = [
    r"(?i)(?:show|tell|give|reveal|share|provide)\s+(?:me\s+)?(?:your|the)\s+(?:api|auth|access|secret|key|token)",
    r"(?i)(?:send|email|give)\s+me\s+(?:your|the)\s+(?:credentials|api\s+key|access\s+token)",
    r"(?i)environment\s+variables?",
    r"(?i)database\s+(?:connection|credentials|password)",
    r"(?i)(?:\.env|config\.json|settings\.py|secrets\.json)",
    r"(?i)/etc/passwd",
    r"(?i)/etc/shadow",
    r"(?i)operator\s+(?:key|token|password|credentials)",
    r"(?i)admin\s+(?:key|token|password|credentials|access)",
    r"(?i)private[\s_-]*key",
    r"(?i)signing[\s_-]*key",
]

IMPERSONATION_PATTERNS = [
    r"(?i)(?:this\s+is|i\s+am)\s+(?:the\s+)?(?:system|admin|operator)",
    r"(?i)message\s+from\s+(?:the\s+)?(?:system|admin|operator|platform)",
    r"(?i)authorized\s+by\s+(?:the\s+)?(?:system|admin|operator)",
    r"(?i)on\s+behalf\s+of\s+(?:the\s+)?(?:system|admin|operator|platform)",
    r"(?i)System\.execute\s*\(",
    r"(?i)rm\s+-rf\s+/",
]

XSS_PATTERNS = [
    r"<script[\s>]",
    r"(?i)javascript\s*:",
    r"(?i)onerror\s*=",
    r"(?i)onload\s*=",
    r"(?i)document\.cookie",
    r"(?i)\.innerHTML\s*=",
    r"(?i)eval\s*\(",
    r"(?i)window\.location",
]

# Patterns too broad for redaction — file paths/filenames, not injection vectors.
# Detected (flagged as threats) but NOT redacted from content.
_NO_REDACT_PATTERNS: frozenset[str] = frozenset({
    r"(?i)/etc/passwd",
    r"(?i)/etc/shadow",
    r"(?i)(?:\.env|config\.json|settings\.py|secrets\.json)",
})

# XSS detection patterns match opening tags/attributes; redaction must match
# the whole construct (script block, event-handler value, etc.).
_XSS_REDACTION_PATTERNS: list[tuple[str, str]] = [
    (r"<script[^>]*>[\s\S]*?</script>", "[REDACTED]"),
    (r"(?i)javascript\s*:[^\s\"']*", "[REDACTED]"),
    (r"(?i)on(?:error|load|click)\s*=[^\s>]*", "[REDACTED]"),
    (r"(?i)document\.cookie", "[REDACTED]"),
    (r"(?i)\.innerHTML\s*=[^\n;]*", "[REDACTED]"),
    (r"(?i)eval\s*\([^)]*\)", "[REDACTED]"),
    (r"(?i)window\.location\s*=[^\n;]*", "[REDACTED]"),
]

# Semantic intent vocabularies
INTENT_VOCABULARIES = {
    "override": {"ignore", "disregard", "bypass", "override", "cancel", "replace", "substitute", "forget"},
    "authority": {"admin", "system", "operator", "developer", "root", "sudo", "administrator", "superuser"},
    "extraction": {"show", "tell", "give", "reveal", "dump", "export", "provide", "share", "display"},
    "secrets": {"key", "token", "password", "secret", "credential", "private", "confidential", "signing"},
}

# Zero-width and invisible characters
INVISIBLE_CHARS = '\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff'
RTL_CHARS = '\u202a\u202b\u202c\u202d\u202e'

# Cyrillic homoglyph map
CONFUSABLES = {
    'а': 'a', 'е': 'e', 'о': 'o', 'с': 'c', 'р': 'p',
    'х': 'x', 'у': 'y', 'А': 'A', 'Е': 'E', 'О': 'O',
    'С': 'C', 'Р': 'P', 'Х': 'X', 'У': 'Y', 'Т': 'T',
    'М': 'M', 'Н': 'H', 'К': 'K', 'В': 'B', 'і': 'i',
}


# ---------------------------------------------------------------------------
# Scrubber Engine
# ---------------------------------------------------------------------------

class ContentScrubber:
    """
    Multi-stage content scrubber for fetched web content.

    Pipeline:
      1. Encoding detection + normalization (unicode, base64, hex, URL encoding)
      2. Pattern-based threat detection (injection, exfiltration, impersonation, XSS)
      3. Semantic intent analysis (vocabulary clustering)
      4. Risk scoring
      5. Content cleaning (targeted redaction)
    """

    def scrub(self, content: str) -> ScrubResult:
        """Run full scrubbing pipeline on content."""
        if not content:
            return ScrubResult(clean=True, content=content)

        threats: list[ThreatDetection] = []

        # Stage 1: Encoding normalization
        normalized, encoding_threats, layers = self._normalize_encoding(content)
        threats.extend(encoding_threats)

        # Stage 2: Pattern-based detection
        threats.extend(self._detect_injections(normalized))
        threats.extend(self._detect_exfiltration(normalized))
        threats.extend(self._detect_impersonation(normalized))
        threats.extend(self._detect_xss(normalized))

        # Stage 3: Semantic intent analysis
        threats.extend(self._semantic_analysis(normalized))

        # Stage 4: Risk scoring
        risk_score = self._calculate_risk(threats)

        # Stage 5: Content cleaning
        cleaned, redactions = self._clean_content(normalized, threats, risk_score)

        return ScrubResult(
            clean=(risk_score < 0.5),
            content=cleaned,
            threats=threats,
            risk_score=risk_score,
            redactions=redactions,
            encoding_layers_stripped=layers,
        )

    # --- Stage 1: Encoding Normalization ---

    def _normalize_encoding(self, text: str) -> tuple[str, list[ThreatDetection], int]:
        """Detect and strip encoding tricks."""
        threats = []
        current = text
        layers = 0

        # Unicode homoglyph normalization
        normalized = unicodedata.normalize('NFKD', current)
        cyrillic_found = any(c in current for c in CONFUSABLES)
        if cyrillic_found:
            for cyrillic, latin in CONFUSABLES.items():
                normalized = normalized.replace(cyrillic, latin)
            if normalized != current:
                threats.append(ThreatDetection(
                    threat_type=ThreatType.PAYLOAD_SMUGGLING,
                    confidence=0.7,
                    evidence="Unicode homoglyph characters detected and normalized",
                    location="unicode",
                ))
                current = normalized
                layers += 1

        # Zero-width character stripping
        stripped = ''.join(c for c in current if c not in INVISIBLE_CHARS)
        if stripped != current:
            removed = len(current) - len(stripped)
            threats.append(ThreatDetection(
                threat_type=ThreatType.PAYLOAD_SMUGGLING,
                confidence=0.8,
                evidence=f"{removed} zero-width/invisible characters stripped",
                location="zero_width",
            ))
            current = stripped
            layers += 1

        # RTL/LTR override stripping
        stripped = ''.join(c for c in current if c not in RTL_CHARS)
        if stripped != current:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PAYLOAD_SMUGGLING,
                confidence=0.8,
                evidence="RTL/LTR override characters stripped",
                location="rtl_override",
            ))
            current = stripped
            layers += 1

        # Base64 fragment detection (embedded encoded payloads)
        b64_fragments = re.findall(r'[A-Za-z0-9+/]{24,}={0,2}', current)
        for frag in b64_fragments:
            try:
                decoded = base64.b64decode(frag).decode('utf-8')
                if len(decoded) >= 10:
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.PAYLOAD_SMUGGLING,
                        confidence=0.8,
                        evidence=f"Base64 fragment decoded: '{decoded[:60]}...'",
                        location="base64_fragment",
                    ))
                    current = current.replace(frag, decoded)
                    layers += 1
            except Exception as exc:
                logger.debug("Base64 fragment decode failed: %s", exc)

        # URL encoding
        if '%' in current:
            try:
                decoded = urllib.parse.unquote(current)
                if decoded != current:
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.PAYLOAD_SMUGGLING,
                        confidence=0.5,
                        evidence="URL-encoded content normalized",
                        location="url_encoding",
                    ))
                    current = decoded
                    layers += 1
            except Exception as exc:
                logger.debug("URL decoding failed: %s", exc)

        # Flag recursive encoding
        if layers > 2:
            threats.append(ThreatDetection(
                threat_type=ThreatType.RECURSIVE_INJECTION,
                confidence=1.0,
                evidence=f"{layers} encoding layers detected — likely adversarial",
                location="nested_encoding",
            ))

        return current, threats, layers

    # --- Stage 2: Pattern Detection ---

    def _detect_injections(self, text: str) -> list[ThreatDetection]:
        threats = []
        for pattern in INJECTION_PATTERNS:
            try:
                if re.search(pattern, text):
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.PROMPT_INJECTION,
                        confidence=0.9,
                        evidence=f"Injection pattern: {pattern[:60]}",
                        location="content",
                    ))
            except re.error as exc:
                logger.debug("Invalid injection pattern skipped: %s", exc)
        return threats

    def _detect_exfiltration(self, text: str) -> list[ThreatDetection]:
        threats = []
        for pattern in EXFILTRATION_PATTERNS:
            try:
                if re.search(pattern, text):
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.DATA_EXFILTRATION,
                        confidence=0.85,
                        evidence=f"Exfiltration pattern: {pattern[:60]}",
                        location="content",
                    ))
            except re.error as exc:
                logger.debug("Invalid exfiltration pattern skipped: %s", exc)
        return threats

    def _detect_impersonation(self, text: str) -> list[ThreatDetection]:
        threats = []
        for pattern in IMPERSONATION_PATTERNS:
            try:
                if re.search(pattern, text):
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.IMPERSONATION,
                        confidence=0.8,
                        evidence=f"Impersonation pattern: {pattern[:60]}",
                        location="content",
                    ))
            except re.error as exc:
                logger.debug("Invalid impersonation pattern skipped: %s", exc)
        return threats

    def _detect_xss(self, text: str) -> list[ThreatDetection]:
        threats = []
        for pattern in XSS_PATTERNS:
            try:
                if re.search(pattern, text):
                    threats.append(ThreatDetection(
                        threat_type=ThreatType.XSS_INJECTION,
                        confidence=0.9,
                        evidence=f"XSS pattern: {pattern[:60]}",
                        location="content",
                    ))
            except re.error as exc:
                logger.debug("Invalid XSS pattern skipped: %s", exc)
        return threats

    # --- Stage 3: Semantic Analysis ---

    def _semantic_analysis(self, text: str) -> list[ThreatDetection]:
        """Detect attacks via vocabulary intent clustering."""
        threats = []
        words = set(re.findall(r'\w+', text.lower()))

        intent_scores = {}
        for category, vocabulary in INTENT_VOCABULARIES.items():
            overlap = len(words & vocabulary)
            if overlap > 0:
                intent_scores[category] = overlap / len(vocabulary)

        # Authority + Override = classic jailbreak
        if (intent_scores.get("authority", 0) > 0.2 and
                intent_scores.get("override", 0) > 0.2):
            threats.append(ThreatDetection(
                threat_type=ThreatType.INSTRUCTION_OVERRIDE,
                confidence=0.85,
                evidence=f"Authority + override intent (auth={intent_scores['authority']:.2f}, override={intent_scores['override']:.2f})",
                location="semantic",
            ))

        # Extraction + Secrets = data exfiltration
        if (intent_scores.get("extraction", 0) > 0.2 and
                intent_scores.get("secrets", 0) > 0.15):
            threats.append(ThreatDetection(
                threat_type=ThreatType.DATA_EXFILTRATION,
                confidence=0.9,
                evidence=f"Extraction + secrets intent (extract={intent_scores['extraction']:.2f}, secrets={intent_scores['secrets']:.2f})",
                location="semantic",
            ))

        # 3+ intent categories with strong overlap = sophisticated attack
        strong = {k: v for k, v in intent_scores.items() if v > 0.2}
        if len(strong) >= 3:
            threats.append(ThreatDetection(
                threat_type=ThreatType.PROMPT_INJECTION,
                confidence=0.7,
                evidence=f"Multi-intent attack: {list(strong.keys())}",
                location="semantic",
            ))

        return threats

    # --- Stage 4: Risk Scoring ---

    WEIGHTS = {
        ThreatType.PROMPT_INJECTION: 1.0,
        ThreatType.INSTRUCTION_OVERRIDE: 1.0,
        ThreatType.DATA_EXFILTRATION: 0.9,
        ThreatType.IMPERSONATION: 0.8,
        ThreatType.PAYLOAD_SMUGGLING: 0.9,
        ThreatType.XSS_INJECTION: 0.9,
        ThreatType.RECURSIVE_INJECTION: 1.0,
        ThreatType.SOCIAL_ENGINEERING: 0.7,
    }

    def _calculate_risk(self, threats: list[ThreatDetection]) -> float:
        if not threats:
            return 0.0

        total = sum(
            t.confidence * self.WEIGHTS.get(t.threat_type, 0.5)
            for t in threats
        )
        max_possible = len(threats) * max(self.WEIGHTS.values())
        risk = min(total / max_possible, 1.0) if max_possible > 0 else 0.0

        # Boost for multiple threat types (sophisticated attack)
        unique_types = len(set(t.threat_type for t in threats))
        if unique_types > 2:
            risk = min(risk * 1.3, 1.0)

        return round(risk, 3)

    # --- Stage 5: Content Cleaning ---

    def _clean_content(
        self, text: str, threats: list[ThreatDetection], risk_score: float
    ) -> tuple[str, int]:
        """Redact detected threat patterns from content.

        Redaction uses the same pattern lists as detection (no drift).
        File-path patterns in _NO_REDACT_PATTERNS are detected but not redacted.
        XSS uses redaction-specific patterns from _XSS_REDACTION_PATTERNS.
        High-risk content (>0.8) is truncated AFTER redaction, not before.
        """
        if not threats:
            return text, 0

        cleaned = text
        redactions = 0

        # Redact injection, exfiltration, impersonation patterns from detection lists
        for pattern in INJECTION_PATTERNS + EXFILTRATION_PATTERNS + IMPERSONATION_PATTERNS:
            if pattern in _NO_REDACT_PATTERNS:
                continue
            try:
                cleaned, count = re.subn(pattern, "[REDACTED]", cleaned)
                redactions += count
            except re.error as exc:
                logger.debug("Redaction pattern failed: %s", exc)

        # Redact XSS using redaction-specific patterns
        for pattern, replacement in _XSS_REDACTION_PATTERNS:
            try:
                cleaned, count = re.subn(pattern, replacement, cleaned)
                redactions += count
            except re.error as exc:
                logger.debug("XSS redaction pattern failed: %s", exc)

        # High risk: prepend warning and truncate AFTER redaction
        if risk_score > 0.8:
            header = (
                f"[⚠️ CONTENT FLAGGED: {len(threats)} threats detected "
                f"(risk={risk_score:.2f}). Treating as potentially adversarial.]\n\n"
            )
            cleaned = header + cleaned[:3000]

        return cleaned, redactions


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_scrubber: Optional[ContentScrubber] = None


def get_scrubber() -> ContentScrubber:
    global _scrubber
    if _scrubber is None:
        _scrubber = ContentScrubber()
    return _scrubber


def scrub_content(content: str) -> ScrubResult:
    """Scrub fetched content. Main entry point."""
    return get_scrubber().scrub(content)
