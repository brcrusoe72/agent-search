"""Query expansion for multi-query fusion search.

Generates genuinely different query reformulations to surface results
that a single query would miss. No LLM needed — rule-based but smart.

Strategies:
  1. Original query (always included)
  2. Question form (turns statements into questions)
  3. Synonym/concept expansion (broader terminology)
  4. Opposing viewpoint (find counterarguments)
  5. Domain narrowing (add specificity)
  6. Acronym/full-name expansion
"""

from __future__ import annotations

import re
from typing import List


# Common concept expansions (not just synonyms — related framings)
CONCEPT_MAP = {
    # Tech
    "ai": ["artificial intelligence", "machine learning", "deep learning"],
    "ml": ["machine learning", "statistical learning"],
    "llm": ["large language model", "foundation model", "GPT", "Claude"],
    "api": ["interface", "integration", "SDK", "endpoint"],
    "saas": ["software as a service", "cloud software", "subscription software"],
    "devops": ["deployment automation", "CI/CD", "infrastructure as code"],
    "kubernetes": ["k8s", "container orchestration"],
    "docker": ["containerization", "container runtime"],
    "database": ["data store", "persistence layer"],
    "microservices": ["service-oriented architecture", "distributed systems"],
    "startup": ["early-stage company", "venture-backed"],
    "vc": ["venture capital", "startup funding", "investor"],
    "ipo": ["initial public offering", "going public"],
    # Business
    "roi": ["return on investment", "cost-benefit", "payback period"],
    "kpi": ["key performance indicator", "metric", "benchmark"],
    "b2b": ["business to business", "enterprise sales"],
    "b2c": ["business to consumer", "consumer market"],
    "revenue": ["income", "sales", "top line"],
    "profit": ["margin", "bottom line", "earnings"],
    "strategy": ["approach", "framework", "playbook"],
    "market": ["industry", "sector", "vertical"],
    # Manufacturing
    "manufacturing": ["production", "fabrication", "industrial"],
    "supply chain": ["logistics", "procurement", "sourcing"],
    "quality control": ["QC", "inspection", "defect detection"],
    "lean": ["lean manufacturing", "waste reduction", "kaizen"],
    "six sigma": ["process improvement", "DMAIC", "statistical process control"],
    # Finance
    "stock": ["equity", "shares", "securities"],
    "bond": ["fixed income", "debt instrument", "treasury"],
    "crypto": ["cryptocurrency", "digital assets", "blockchain"],
    "inflation": ["price increases", "purchasing power", "CPI"],
    "interest rate": ["fed funds rate", "monetary policy", "yield"],
}

# Question prefixes by query type
QUESTION_PREFIXES = [
    "what is", "how does", "why is", "what are the benefits of",
    "what are the risks of", "how to", "what is the future of",
]

# Opposing viewpoint triggers
OPPOSITION_TRIGGERS = {
    "best": "worst problems with",
    "benefits": "risks drawbacks of",
    "advantages": "disadvantages limitations of",
    "why": "why not criticism of",
    "success": "failure case study",
    "growing": "declining stagnating",
    "popular": "overrated criticism",
    "recommended": "alternatives to avoid",
    "safe": "risks dangers of",
    "cheap": "hidden costs of",
    "easy": "challenges difficulties of",
    "fast": "slow problems with",
    "good": "problems criticism of",
    "pros": "cons drawbacks",
}


def generate_query_variations(original_query: str) -> List[str]:
    """Generate 3-5 genuinely different query reformulations."""
    variations = [original_query]
    query_lower = original_query.strip().lower()
    words = query_lower.split()

    # Strategy 1: Question form
    question = _to_question(query_lower, words)
    if question and question.lower() != query_lower:
        variations.append(question)

    # Strategy 2: Concept expansion
    expanded = _expand_concepts(original_query, words)
    if expanded and expanded.lower() != query_lower:
        variations.append(expanded)

    # Strategy 3: Opposing viewpoint
    opposing = _opposing_viewpoint(original_query, query_lower, words)
    if opposing and opposing.lower() != query_lower:
        variations.append(opposing)

    # Strategy 4: Domain narrowing or broadening
    scoped = _adjust_scope(original_query, query_lower, words)
    if scoped and scoped.lower() != query_lower:
        variations.append(scoped)

    # Deduplicate (case-insensitive)
    seen = set()
    unique = []
    for v in variations:
        key = v.strip().lower()
        if key not in seen:
            seen.add(key)
            unique.append(v)

    return unique[:5]


def _to_question(query: str, words: list[str]) -> str | None:
    """Turn a statement into a question form."""
    # Already a question
    if query.endswith("?") or words[0] in ("how", "what", "why", "when", "where", "who", "which", "is", "are", "can", "do", "does"):
        return None

    # Action queries → "how to" form
    action_words = {"install", "setup", "configure", "build", "create", "deploy",
                    "fix", "solve", "debug", "optimize", "improve", "migrate"}
    if words[0] in action_words or (len(words) > 1 and words[1] in action_words):
        return f"how to {query}"

    # Noun phrases → "what is" form
    if len(words) <= 4:
        return f"what is {query} and how does it work"

    # Longer queries → "why" form for opinions/analysis
    return f"why {query}"


def _expand_concepts(original: str, words: list[str]) -> str | None:
    """Replace terms with broader/alternative concepts."""
    result = original
    expanded = False

    for word in words:
        if word in CONCEPT_MAP:
            # Pick the first alternative that's different
            alternatives = CONCEPT_MAP[word]
            replacement = alternatives[0]
            result = re.sub(r'\b' + re.escape(word) + r'\b', replacement, result, count=1, flags=re.IGNORECASE)
            expanded = True
            break

    # Also try multi-word matches
    if not expanded:
        for phrase, alternatives in CONCEPT_MAP.items():
            if " " in phrase and phrase in original.lower():
                result = result.lower().replace(phrase, alternatives[0], 1)
                expanded = True
                break

    return result if expanded else None


def _opposing_viewpoint(original: str, query_lower: str, words: list[str]) -> str | None:
    """Generate a query for the opposing viewpoint."""
    for trigger, opposition in OPPOSITION_TRIGGERS.items():
        if trigger in words:
            # Replace the trigger word with its opposition
            new_query = query_lower.replace(trigger, opposition, 1)
            return new_query

    # No trigger found — add generic opposition framing
    if len(words) >= 2:
        return f"criticism problems with {original}"

    return None


def _adjust_scope(original: str, query_lower: str, words: list[str]) -> str | None:
    """Narrow or broaden the query scope."""
    # Short queries → narrow with context
    if len(words) <= 2:
        return f"{original} in 2026 latest developments"

    # Long queries → broaden by removing qualifiers
    qualifiers = {"latest", "best", "top", "new", "recent", "current", "modern",
                  "2024", "2025", "2026", "today", "now", "ultimate", "complete",
                  "comprehensive", "definitive", "essential"}
    narrowed_words = [w for w in words if w not in qualifiers]
    if len(narrowed_words) < len(words) and len(narrowed_words) >= 2:
        return " ".join(narrowed_words)

    # Add "expert analysis" or "research paper" for depth
    if not any(w in words for w in ["research", "study", "analysis", "paper", "academic"]):
        return f"{original} research analysis"

    return None
