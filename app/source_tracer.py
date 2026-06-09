"""
source_tracer.py — Primary Source Discovery Engine

Instead of fighting paywalls, go upstream. Find the think tanks,
government agencies, and research institutions that produce the
primary data journalists wrap narratives around.

Given a topic → identifies relevant institutions → searches their
publications directly → returns primary sources with content.

The FT writes about it. We read what the FT read.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("agentsearch.source_tracer")


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


# ---------------------------------------------------------------------------
# Institution Registry
# ---------------------------------------------------------------------------
# Each institution: name, domain, search/publications URL pattern,
# topic tags, quality tier (1=gold, 2=strong, 3=solid)

INSTITUTIONS = [
    # === US THINK TANKS (Tier 1) ===
    {
        "id": "csis", "name": "Center for Strategic & International Studies",
        "domain": "csis.org",
        "search": "https://www.csis.org/search?search_fulltext={q}",
        "pubs": "https://www.csis.org/analysis",
        "topics": ["defense", "geopolitics", "china", "trade", "technology", "energy", "security", "indo-pacific", "nato", "russia", "middle-east", "africa"],
        "tier": 1,
    },
    {
        "id": "rand", "name": "RAND Corporation",
        "domain": "rand.org",
        "search": "https://www.rand.org/search.html?query={q}",
        "pubs": "https://www.rand.org/pubs.html",
        "topics": ["defense", "security", "technology", "health", "education", "labor", "infrastructure"],
        "tier": 1,
    },
    {
        "id": "brookings", "name": "Brookings Institution",
        "domain": "brookings.edu",
        "search": "https://www.brookings.edu/search/?s={q}",
        "pubs": "https://www.brookings.edu/articles/",
        "topics": ["economy", "governance", "foreign-policy", "trade", "technology", "health", "education", "climate"],
        "tier": 1,
    },
    {
        "id": "cfr", "name": "Council on Foreign Relations",
        "domain": "cfr.org",
        "search": "https://www.cfr.org/search?keyword={q}",
        "pubs": "https://www.cfr.org/publications",
        "topics": ["geopolitics", "foreign-policy", "defense", "trade", "china", "russia", "middle-east", "energy"],
        "tier": 1,
    },
    {
        "id": "carnegie", "name": "Carnegie Endowment for International Peace",
        "domain": "carnegieendowment.org",
        "search": "https://carnegieendowment.org/search?query={q}",
        "pubs": "https://carnegieendowment.org/publications",
        "topics": ["geopolitics", "democracy", "nuclear", "technology", "china", "russia", "middle-east", "south-asia"],
        "tier": 1,
    },
    {
        "id": "cnas", "name": "Center for a New American Security",
        "domain": "cnas.org",
        "search": "https://www.cnas.org/search?query={q}",
        "pubs": "https://www.cnas.org/publications/reports",
        "topics": ["defense", "indo-pacific", "technology", "energy", "middle-east", "security"],
        "tier": 1,
    },
    {
        "id": "heritage", "name": "Heritage Foundation",
        "domain": "heritage.org",
        "search": "https://www.heritage.org/search?contains={q}",
        "pubs": "https://www.heritage.org/defense",
        "topics": ["defense", "economy", "trade", "governance", "budget"],
        "tier": 2,
    },
    {
        "id": "aei", "name": "American Enterprise Institute",
        "domain": "aei.org",
        "search": "https://www.aei.org/search/?s={q}",
        "pubs": "https://www.aei.org/research/",
        "topics": ["economy", "trade", "defense", "technology", "health", "foreign-policy"],
        "tier": 2,
    },
    {
        "id": "atlantic_council", "name": "Atlantic Council",
        "domain": "atlanticcouncil.org",
        "search": "https://www.atlanticcouncil.org/?s={q}",
        "pubs": "https://www.atlanticcouncil.org/research/",
        "topics": ["geopolitics", "nato", "energy", "technology", "china", "russia", "economy", "digital"],
        "tier": 1,
    },
    {
        "id": "piie", "name": "Peterson Institute for International Economics",
        "domain": "piie.com",
        "search": "https://www.piie.com/search?keyword={q}",
        "pubs": "https://www.piie.com/research",
        "topics": ["trade", "economy", "tariffs", "currency", "globalization", "sanctions"],
        "tier": 1,
    },
    {
        "id": "stimson", "name": "Stimson Center",
        "domain": "stimson.org",
        "search": "https://www.stimson.org/?s={q}",
        "topics": ["security", "nuclear", "arms-control", "south-asia", "environment"],
        "tier": 2,
    },
    {
        "id": "wilson", "name": "Wilson Center",
        "domain": "wilsoncenter.org",
        "search": "https://www.wilsoncenter.org/search?search_api_fulltext={q}",
        "topics": ["geopolitics", "russia", "china", "latin-america", "history", "science"],
        "tier": 2,
    },

    # === EUROPEAN / INTERNATIONAL THINK TANKS ===
    {
        "id": "chatham_house", "name": "Chatham House",
        "domain": "chathamhouse.org",
        "search": "https://www.chathamhouse.org/search?search_api_fulltext={q}",
        "pubs": "https://www.chathamhouse.org/publications",
        "topics": ["geopolitics", "trade", "energy", "climate", "russia", "china", "middle-east", "africa"],
        "tier": 1,
    },
    {
        "id": "rusi", "name": "Royal United Services Institute",
        "domain": "rusi.org",
        "search": "https://rusi.org/explore-our-research?search={q}",
        "topics": ["defense", "security", "intelligence", "nuclear", "cyber"],
        "tier": 1,
    },
    {
        "id": "sipri", "name": "Stockholm International Peace Research Institute",
        "domain": "sipri.org",
        "search": "https://www.sipri.org/search?keys={q}",
        "pubs": "https://www.sipri.org/publications",
        "topics": ["defense", "arms", "nuclear", "conflict", "military-spending"],
        "tier": 1,
    },
    {
        "id": "ecfr", "name": "European Council on Foreign Relations",
        "domain": "ecfr.eu",
        "search": "https://ecfr.eu/?s={q}",
        "topics": ["geopolitics", "europe", "china", "russia", "middle-east", "defense"],
        "tier": 2,
    },
    {
        "id": "lowy", "name": "Lowy Institute",
        "domain": "lowyinstitute.org",
        "search": "https://www.lowyinstitute.org/search?query={q}",
        "topics": ["indo-pacific", "china", "australia", "trade", "defense", "diplomacy"],
        "tier": 2,
    },
    {
        "id": "aspi", "name": "Australian Strategic Policy Institute",
        "domain": "aspi.org.au",
        "search": "https://www.aspi.org.au/search?q={q}",
        "topics": ["indo-pacific", "defense", "china", "cyber", "technology", "aukus"],
        "tier": 2,
    },
    {
        "id": "bruegel", "name": "Bruegel",
        "domain": "bruegel.org",
        "search": "https://www.bruegel.org/search?search_api_fulltext={q}",
        "topics": ["economy", "trade", "europe", "energy", "digital", "climate"],
        "tier": 2,
    },

    # === US GOVERNMENT SOURCES ===
    {
        "id": "crs", "name": "Congressional Research Service",
        "domain": "crsreports.congress.gov",
        "search": "https://crsreports.congress.gov/search/#/?termsToSearch={q}&orderBy=Relevance",
        "topics": ["defense", "trade", "economy", "technology", "foreign-policy", "budget", "energy", "health"],
        "tier": 1,
    },
    {
        "id": "gao", "name": "Government Accountability Office",
        "domain": "gao.gov",
        "search": "https://www.gao.gov/search?keyword={q}",
        "pubs": "https://www.gao.gov/reports-testimonies",
        "topics": ["defense", "budget", "technology", "health", "infrastructure", "audit"],
        "tier": 1,
    },
    {
        "id": "cbo", "name": "Congressional Budget Office",
        "domain": "cbo.gov",
        "search": "https://www.cbo.gov/search/results?search={q}",
        "topics": ["budget", "economy", "defense", "health", "tax", "spending"],
        "tier": 1,
    },
    {
        "id": "bls", "name": "Bureau of Labor Statistics",
        "domain": "bls.gov",
        "search": "https://search.bls.gov/search?query={q}&utf8=%E2%9C%93",
        "topics": ["labor", "employment", "wages", "inflation", "cpi", "manufacturing"],
        "tier": 1,
    },
    {
        "id": "census", "name": "US Census Bureau",
        "domain": "census.gov",
        "search": "https://www.census.gov/search-results.html?searchType=web&q={q}",
        "topics": ["trade", "economy", "demographics", "manufacturing", "housing"],
        "tier": 1,
    },
    {
        "id": "eia", "name": "Energy Information Administration",
        "domain": "eia.gov",
        "search": "https://www.eia.gov/search/?q={q}",
        "topics": ["energy", "oil", "gas", "electricity", "renewables", "commodities"],
        "tier": 1,
    },
    {
        "id": "fed", "name": "Federal Reserve",
        "domain": "federalreserve.gov",
        "search": "https://www.federalreserve.gov/search.htm?text={q}",
        "topics": ["economy", "monetary-policy", "banking", "inflation", "employment", "financial-stability"],
        "tier": 1,
    },
    {
        "id": "ustr", "name": "US Trade Representative",
        "domain": "ustr.gov",
        "search": "https://ustr.gov/?s={q}",
        "topics": ["trade", "tariffs", "wto", "china", "nafta", "usmca"],
        "tier": 1,
    },
    {
        "id": "treasury", "name": "US Treasury",
        "domain": "treasury.gov",
        "search": "https://home.treasury.gov/search#?query={q}",
        "topics": ["economy", "sanctions", "debt", "currency", "tax", "financial-regulation"],
        "tier": 1,
    },

    # === INTERNATIONAL ORGANIZATIONS ===
    {
        "id": "imf", "name": "International Monetary Fund",
        "domain": "imf.org",
        "search": "https://www.imf.org/en/Search#q={q}&sort=relevancy",
        "pubs": "https://www.imf.org/en/Publications",
        "topics": ["economy", "currency", "debt", "trade", "developing", "financial-stability"],
        "tier": 1,
    },
    {
        "id": "worldbank", "name": "World Bank",
        "domain": "worldbank.org",
        "search": "https://www.worldbank.org/en/search?q={q}",
        "topics": ["development", "economy", "infrastructure", "climate", "poverty", "trade", "health"],
        "tier": 1,
    },
    {
        "id": "wto", "name": "World Trade Organization",
        "domain": "wto.org",
        "search": "https://www.wto.org/english/search_e/search_e.htm?search={q}",
        "topics": ["trade", "tariffs", "disputes", "globalization"],
        "tier": 1,
    },
    {
        "id": "iea", "name": "International Energy Agency",
        "domain": "iea.org",
        "search": "https://www.iea.org/search?q={q}",
        "topics": ["energy", "oil", "gas", "renewables", "climate", "electricity"],
        "tier": 1,
    },
    {
        "id": "bis", "name": "Bank for International Settlements",
        "domain": "bis.org",
        "search": "https://www.bis.org/search/?q={q}",
        "topics": ["banking", "currency", "cbdc", "financial-regulation", "monetary-policy"],
        "tier": 1,
    },
    {
        "id": "oecd", "name": "OECD",
        "domain": "oecd.org",
        "search": "https://www.oecd.org/en/search.html?q={q}",
        "topics": ["economy", "trade", "tax", "education", "labor", "technology", "governance"],
        "tier": 1,
    },
    {
        "id": "un", "name": "United Nations",
        "domain": "un.org",
        "search": "https://www.un.org/en/search/?query={q}",
        "topics": ["geopolitics", "development", "climate", "human-rights", "peacekeeping", "security"],
        "tier": 2,
    },

    # === DATA / TRACKERS ===
    {
        "id": "aiddata", "name": "AidData (William & Mary)",
        "domain": "aiddata.org",
        "search": "https://www.aiddata.org/search?q={q}",
        "topics": ["china", "development", "bri", "lending", "infrastructure"],
        "tier": 2,
    },
    {
        "id": "tax_foundation", "name": "Tax Foundation",
        "domain": "taxfoundation.org",
        "search": "https://taxfoundation.org/?s={q}",
        "topics": ["tax", "tariffs", "trade", "economy", "budget"],
        "tier": 2,
    },
    {
        "id": "fred", "name": "Federal Reserve Economic Data (FRED)",
        "domain": "fred.stlouisfed.org",
        "search": "https://fred.stlouisfed.org/searchresults/?st={q}",
        "topics": ["economy", "inflation", "employment", "gdp", "interest-rates", "manufacturing"],
        "tier": 1,
    },

    # === ACADEMIC / RESEARCH ===
    {
        "id": "arxiv", "name": "arXiv",
        "domain": "arxiv.org",
        "search": "https://arxiv.org/search/?query={q}&searchtype=all",
        "topics": ["technology", "ai", "physics", "math", "computer-science"],
        "tier": 1,
    },
    {
        "id": "ssrn", "name": "SSRN",
        "domain": "ssrn.com",
        "search": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words={q}",
        "topics": ["economy", "law", "finance", "governance", "trade"],
        "tier": 2,
    },
    {
        "id": "nber", "name": "National Bureau of Economic Research",
        "domain": "nber.org",
        "search": "https://www.nber.org/search?fullText={q}",
        "topics": ["economy", "labor", "trade", "health", "finance", "tax"],
        "tier": 1,
    },

    # === DEFENSE-SPECIFIC ===
    {
        "id": "usni", "name": "US Naval Institute",
        "domain": "usni.org",
        "search": "https://news.usni.org/?s={q}",
        "topics": ["defense", "navy", "maritime", "indo-pacific", "shipbuilding"],
        "tier": 2,
    },
    {
        "id": "war_on_rocks", "name": "War on the Rocks",
        "domain": "warontherocks.com",
        "search": "https://warontherocks.com/?s={q}",
        "topics": ["defense", "security", "strategy", "military", "geopolitics"],
        "tier": 2,
    },
    {
        "id": "defense_one", "name": "Defense One",
        "domain": "defenseone.com",
        "search": "https://www.defenseone.com/search/?q={q}",
        "topics": ["defense", "military", "technology", "budget", "procurement"],
        "tier": 2,
    },

    # === QUALITY JOURNALISM (free or mostly free) ===
    {
        "id": "reuters", "name": "Reuters",
        "domain": "reuters.com",
        "search": "https://www.reuters.com/search/news?query={q}",
        "topics": ["geopolitics", "economy", "trade", "energy", "technology", "markets"],
        "tier": 2,
    },
    {
        "id": "ap", "name": "Associated Press",
        "domain": "apnews.com",
        "search": "https://apnews.com/search?q={q}",
        "topics": ["geopolitics", "economy", "trade", "security"],
        "tier": 2,
    },
    {
        "id": "diplomat", "name": "The Diplomat",
        "domain": "thediplomat.com",
        "search": "https://thediplomat.com/?s={q}",
        "topics": ["indo-pacific", "china", "japan", "korea", "asean", "defense", "trade"],
        "tier": 2,
    },
    {
        "id": "foreign_affairs", "name": "Foreign Affairs",
        "domain": "foreignaffairs.com",
        "search": "https://www.foreignaffairs.com/search?query={q}",
        "topics": ["geopolitics", "foreign-policy", "security", "economy", "trade"],
        "tier": 1,
    },
    {
        "id": "foreign_policy", "name": "Foreign Policy",
        "domain": "foreignpolicy.com",
        "search": "https://foreignpolicy.com/?s={q}",
        "topics": ["geopolitics", "foreign-policy", "security", "trade", "technology"],
        "tier": 2,
    },
]

# ---------------------------------------------------------------------------
# Topic Mapping — keyword → topic tags
# ---------------------------------------------------------------------------

TOPIC_KEYWORDS: dict[str, list[str]] = {
    "defense": ["defense", "military", "pentagon", "dod", "armed forces", "weapons", "procurement", "nds", "army", "navy", "air force", "marines"],
    "geopolitics": ["geopolitical", "geopolitics", "international relations", "foreign policy", "diplomacy", "great power", "world order"],
    "china": ["china", "chinese", "beijing", "xi jinping", "prc", "ccp", "belt and road", "bri", "south china sea", "taiwan"],
    "russia": ["russia", "russian", "moscow", "putin", "kremlin", "ukraine"],
    "middle-east": ["middle east", "iran", "saudi", "israel", "gaza", "iraq", "syria", "hormuz", "gulf"],
    "indo-pacific": ["indo-pacific", "indo pacific", "pacific", "asean", "quad", "aukus", "australia", "japan", "korea", "philippines"],
    "trade": ["trade", "tariff", "import", "export", "wto", "free trade", "trade war", "protectionism", "usmca", "nafta"],
    "economy": ["economy", "economic", "gdp", "recession", "growth", "inflation", "monetary", "fiscal", "debt", "deficit"],
    "energy": ["energy", "oil", "gas", "petroleum", "opec", "renewable", "solar", "wind", "nuclear energy", "lng", "pipeline"],
    "technology": ["technology", "tech", "ai", "artificial intelligence", "cyber", "semiconductor", "chip", "quantum", "5g", "digital"],
    "security": ["security", "intelligence", "terrorism", "counterterrorism", "homeland", "cyber security", "espionage"],
    "nuclear": ["nuclear", "nonproliferation", "warhead", "icbm", "arms control", "new start", "uranium", "enrichment"],
    "climate": ["climate", "emissions", "carbon", "paris agreement", "net zero", "green", "sustainability"],
    "sanctions": ["sanctions", "ofac", "embargo", "financial restriction", "asset freeze"],
    "currency": ["currency", "dollar", "yuan", "euro", "dedollarization", "de-dollarization", "reserve currency", "cbdc", "forex"],
    "labor": ["labor", "employment", "jobs", "wages", "workforce", "unemployment", "manufacturing jobs"],
    "manufacturing": ["manufacturing", "factory", "production", "industrial", "oee", "lean", "supply chain", "reshoring"],
    "health": ["health", "healthcare", "pharma", "pandemic", "fda", "medicare", "medicaid"],
    "infrastructure": ["infrastructure", "transportation", "bridges", "roads", "ports", "broadband"],
    "budget": ["budget", "spending", "appropriations", "continuing resolution", "debt ceiling", "fiscal"],
    "tax": ["tax", "taxation", "corporate tax", "income tax", "tax reform", "irs"],
    "africa": ["africa", "african", "sahel", "sub-saharan", "nigeria", "ethiopia", "kenya"],
    "latin-america": ["latin america", "south america", "brazil", "mexico", "venezuela", "argentina", "colombia"],
    "nato": ["nato", "alliance", "article 5", "burden sharing", "european defense"],
    "ai": ["ai", "artificial intelligence", "machine learning", "llm", "foundation model", "deep learning", "gpt", "claude", "openai", "anthropic"],
    "cyber": ["cyber", "cybersecurity", "hacking", "ransomware", "zero day", "apt"],
    "arms": ["arms", "weapons", "missile", "hypersonic", "drone", "uav", "munitions", "f-35", "patriot"],
    "maritime": ["maritime", "naval", "shipping", "strait", "piracy", "submarine", "carrier", "fleet"],
    "space": ["space", "satellite", "orbit", "launch", "starlink", "gps", "anti-satellite"],
    "financial-stability": ["financial stability", "banking crisis", "systemic risk", "too big to fail", "stress test"],
}


def _extract_topics(query: str) -> set[str]:
    """Extract topic tags from a query string."""
    q = query.lower()
    matched = set()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            matched.add(topic)
    return matched


def find_institutions(query: str, max_results: int = 15) -> list[dict]:
    """Find institutions relevant to a query, ranked by relevance and tier."""
    topics = _extract_topics(query)

    if not topics:
        # No topic match — return tier 1 general sources
        return [i for i in INSTITUTIONS if i["tier"] == 1][:max_results]

    scored = []
    for inst in INSTITUTIONS:
        inst_topics = set(inst["topics"])
        overlap = topics & inst_topics
        if overlap:
            # Score: overlap count * tier bonus
            tier_bonus = {1: 3, 2: 2, 3: 1}.get(inst["tier"], 1)
            score = len(overlap) * tier_bonus
            scored.append((score, inst))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [inst for _, inst in scored[:max_results]]


async def trace_sources(
    query: str,
    searxng_url: str = "http://searxng:8080",
    max_institutions: int = 10,
    max_results: int = 15,
) -> dict:
    """
    Primary source discovery. Given a query:
    1. Identify relevant institutions
    2. Search within those institutions via SearXNG site: queries
    3. Return ranked primary sources

    This is the "go upstream" function — find what the journalists read.
    """
    institutions = find_institutions(query, max_results=max_institutions)
    topics = _extract_topics(query)

    if not institutions:
        return {
            "query": query,
            "topics": list(topics),
            "institutions": [],
            "sources": [],
            "strategy": "no_match",
        }

    # Build site-scoped searches for top institutions
    async def _search_institution(client: httpx.AsyncClient, inst: dict) -> list[dict]:
        """Search within a specific institution's domain."""
        site_query = f"site:{inst['domain']} {query}"
        try:
            resp = await client.get(
                f"{searxng_url}/search",
                params={"q": site_query, "format": "json", "pageno": 1},
                timeout=10.0,
            )
            if not resp.is_success:
                return []
            results = resp.json().get("results", [])
            return [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", r.get("snippet", "")),
                    "institution": inst["name"],
                    "institution_id": inst["id"],
                    "domain": inst["domain"],
                    "tier": inst["tier"],
                    "published": r.get("publishedDate", None),
                }
                for r in results[:5]
            ]
        except Exception as e:
            logger.debug("Search failed for %s: %s", _safe_log_value(inst["id"]), e)
            return []

    # Also do a broad search limited to all primary-source domains
    async def _search_broad(client: httpx.AsyncClient) -> list[dict]:
        """Broad search filtered to primary source domains."""
        # Add analytical terms to avoid homepages
        enhanced = f"{query} report analysis assessment publication"
        try:
            resp = await client.get(
                f"{searxng_url}/search",
                params={"q": enhanced, "format": "json", "pageno": 1},
                timeout=10.0,
            )
            if not resp.is_success:
                return []
            results = resp.json().get("results", [])

            # Filter to known institution domains
            filtered = []
            for r in results:
                url = r.get("url", "")
                domain = urlparse(url).netloc.lower().replace("www.", "")
                # Check if domain matches any known institution
                matching_inst = None
                for inst in INSTITUTIONS:
                    if inst["domain"] in domain or domain.endswith("." + inst["domain"]):
                        matching_inst = inst
                        break
                if matching_inst:
                    filtered.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("content", r.get("snippet", "")),
                        "institution": matching_inst["name"],
                        "institution_id": matching_inst["id"],
                        "domain": matching_inst["domain"],
                        "tier": matching_inst["tier"],
                        "published": r.get("publishedDate", None),
                    })
            return filtered
        except Exception:
            return []

    # Execute all searches concurrently
    async with httpx.AsyncClient(timeout=15.0) as client:
        tasks = [_search_institution(client, inst) for inst in institutions[:8]]
        tasks.append(_search_broad(client))
        all_results = await asyncio.gather(*tasks)

    # Flatten and deduplicate
    seen_urls = set()
    sources = []
    for result_set in all_results:
        for r in result_set:
            url = r["url"]
            if url not in seen_urls:
                seen_urls.add(url)
                sources.append(r)

    # Rank: tier 1 first, then tier 2, preserve original order within tier
    sources = [s for _, s in sorted(enumerate(sources), key=lambda x: (x[1]["tier"], x[0]))]

    return {
        "query": query,
        "topics": sorted(topics),
        "institutions_searched": [
            {"id": i["id"], "name": i["name"], "tier": i["tier"]}
            for i in institutions[:8]
        ],
        "sources": sources[:max_results],
        "total_found": len(sources),
    }


def get_institution_registry() -> list[dict]:
    """Return the full institution registry for inspection."""
    return [
        {
            "id": i["id"],
            "name": i["name"],
            "domain": i["domain"],
            "topics": i["topics"],
            "tier": i["tier"],
            "search_url": i.get("search", ""),
            "publications_url": i.get("pubs", ""),
        }
        for i in INSTITUTIONS
    ]
