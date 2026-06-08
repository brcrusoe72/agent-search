"""
Policy Source Library — curated URLs for geopolitical/policy research.

Bypasses search entirely for known high-value sources. When a query matches
a topic, returns direct URLs to fetch rather than hoping SearXNG finds them.
"""

from typing import List, Dict, Optional

# Each entry: topic keywords -> list of known high-quality source URLs
# Organized by institution and topic area

POLICY_SOURCES: Dict[str, List[Dict[str, str]]] = {
    # === DEFENSE STRATEGY ===
    "nds": [
        {"url": "https://www.csis.org/analysis/2026-national-defense-strategy-numbers-radical-changes-moderate-changes-and-some",
         "institution": "CSIS", "title": "2026 NDS by the Numbers", "date": "2026-01"},
        {"url": "https://www.cnas.org/press/in-the-news/experts-react-the-2026-national-defense-strategy",
         "institution": "CNAS", "title": "Experts React: 2026 NDS", "date": "2026-01"},
        {"url": "https://www.hscentre.org/latest-articles/2026-u-s-national-defense-strategy-still-china-taiwan/",
         "institution": "HSCentre", "title": "2026 NDS: Still About China", "date": "2026-02"},
        {"url": "https://www.heritage.org/defense/report/an-assessment-the-2026-national-defense-strategy",
         "institution": "Heritage", "title": "Assessment of the 2026 NDS", "date": "2026"},
        {"url": "https://www.rand.org/pubs/commentary/2026/01/the-2026-national-defense-strategy.html",
         "institution": "RAND", "title": "2026 NDS Commentary", "date": "2026-01"},
    ],

    # === NATO / EUROPEAN DEFENSE ===
    "nato_spending": [
        {"url": "https://gabelli.com/research/nato-spending-overview-a-structural-change-to-the-defense-industry/",
         "institution": "Gabelli Funds", "title": "NATO Spending Overview: Structural Change", "date": "2026-01"},
        {"url": "https://www.nato.int/nato_static_fl2014/assets/pdf/2024/7/pdf/240617-def-exp-2024-en.pdf",
         "institution": "NATO", "title": "Defence Expenditures of NATO Countries 2014-2024", "date": "2024-07"},
        {"url": "https://www.iiss.org/research-paper/2025/02/the-military-balance-2025/",
         "institution": "IISS", "title": "The Military Balance 2025", "date": "2025-02"},
        {"url": "https://www.sipri.org/databases/milex",
         "institution": "SIPRI", "title": "Military Expenditure Database", "date": "ongoing"},
    ],

    # === DOLLAR / DE-DOLLARIZATION ===
    "dollar_reserve": [
        {"url": "https://www.atlanticcouncil.org/programs/geoeconomics-center/dollar-dominance-monitor/",
         "institution": "Atlantic Council", "title": "Dollar Dominance Monitor", "date": "ongoing"},
        {"url": "https://www.jpmorgan.com/insights/global-research/currencies/de-dollarization",
         "institution": "J.P. Morgan", "title": "De-dollarization: End of Dollar Dominance?", "date": "2025-07"},
        {"url": "https://data.imf.org/regular.aspx?key=41175",
         "institution": "IMF", "title": "Currency Composition of Official Foreign Exchange Reserves (COFER)", "date": "ongoing"},
        {"url": "https://www.bis.org/statistics/rpfx22.htm",
         "institution": "BIS", "title": "Triennial Central Bank Survey of FX", "date": "2022"},
    ],

    # === BRICS / ALTERNATIVE FINANCE ===
    "brics_payment": [
        {"url": "https://asiatimes.com/2026/01/brics-laying-first-tracks-for-new-global-payment-system/",
         "institution": "Asia Times", "title": "BRICS Laying First Tracks for New Payment System", "date": "2026-01"},
        {"url": "https://www.atlanticcouncil.org/programs/geoeconomics-center/dollar-dominance-monitor/",
         "institution": "Atlantic Council", "title": "Dollar Dominance Monitor (BRICS section)", "date": "ongoing"},
        {"url": "https://www.bis.org/about/bisih/topics/cbdc/mcbdc_bridge.htm",
         "institution": "BIS", "title": "Project mBridge", "date": "ongoing"},
    ],

    # === IRAN / OPERATION EPIC FURY ===
    "iran_epic_fury": [
        {"url": "https://www.csis.org/analysis/operation-epic-fury-and-remnants-irans-nuclear-program",
         "institution": "CSIS", "title": "Epic Fury and Iran's Nuclear Program", "date": "2026-02"},
        {"url": "https://www.csis.org/analysis/37-billion-estimated-cost-epic-furys-first-100-hours",
         "institution": "CSIS", "title": "$3.7B: Cost of Epic Fury's First 100 Hours", "date": "2026-03"},
        {"url": "https://www.fddaction.org/secure-line-readout/2026/03/02/operation-epic-fury-battle-damage-assessment-and-strategic-outlook/",
         "institution": "FDD", "title": "Epic Fury: Battle Damage Assessment", "date": "2026-03"},
        {"url": "https://www.hudson.org/missile-defense/operation-epic-fury-situation-report-battlefield-effects-strategic-outcomes-can-kasapoglu",
         "institution": "Hudson Institute", "title": "Epic Fury Situation Report", "date": "2026-03"},
        {"url": "https://carnegieendowment.org/emissary/2026/03/gulf-states-iran-war-security",
         "institution": "Carnegie", "title": "Gulf Monarchies Caught Between Iran and US", "date": "2026-03"},
        {"url": "https://www.brookings.edu/articles/can-irans-regime-survive-the-war/",
         "institution": "Brookings", "title": "Can Iran's Regime Survive the War?", "date": "2026-03"},
    ],

    # === HORMUZ / ENERGY ===
    "hormuz_energy": [
        {"url": "https://www.cnbc.com/2026/03/18/hormuz-bottleneck-vessel-tanker-tracker-shipping-strait-of-hormuz.html",
         "institution": "CNBC", "title": "Hormuz Traffic: Who's Moving and Who's Not", "date": "2026-03"},
        {"url": "https://www.eia.gov/todayinenergy/detail.php?id=62481",
         "institution": "EIA", "title": "Strait of Hormuz Oil Flow Data", "date": "ongoing"},
        {"url": "https://www.iea.org/topics/oil",
         "institution": "IEA", "title": "Oil Market Analysis", "date": "ongoing"},
    ],

    # === AUKUS ===
    "aukus": [
        {"url": "https://www.csis.org/analysis/aukus-inflection-seizing-opportunity-deliver-deterrence",
         "institution": "CSIS", "title": "The AUKUS Inflection", "date": "2025-08"},
        {"url": "https://crsreports.congress.gov/product/pdf/IF/IF12113",
         "institution": "CRS", "title": "AUKUS and Indo-Pacific Security", "date": "2025-03"},
        {"url": "https://thediplomat.com/2026/03/the-iran-war-is-now-impacting-aukus/",
         "institution": "The Diplomat", "title": "Iran War Impacting AUKUS", "date": "2026-03"},
        {"url": "https://www.aspi.org.au/report/aukus",
         "institution": "ASPI", "title": "AUKUS Analysis", "date": "ongoing"},
    ],

    # === QUAD ===
    "quad": [
        {"url": "https://www.cnas.org/publications/reports/quad",
         "institution": "CNAS", "title": "Quad: The Next Phase", "date": "2025-06"},
        {"url": "https://www.state.gov/the-quad/",
         "institution": "State Department", "title": "The Quad (Official)", "date": "ongoing"},
        {"url": "https://www.ussc.edu.au/the-future-of-the-quad-in-the-age-of-trump",
         "institution": "USSC", "title": "Future of the Quad in the Age of Trump", "date": "2026-02"},
    ],

    # === VENEZUELA ===
    "venezuela_operation": [
        {"url": "https://www.brookings.edu/articles/making-sense-of-the-us-military-operation-in-venezuela/",
         "institution": "Brookings", "title": "Making Sense of the Venezuela Operation", "date": "2026-01"},
        {"url": "https://www.brookings.edu/articles/the-global-implications-of-the-us-military-operation-in-venezuela/",
         "institution": "Brookings", "title": "Global Implications of Venezuela Operation", "date": "2026-01"},
        {"url": "https://www.cfr.org/articles/guide-trumps-second-term-military-strikes-and-actions",
         "institution": "CFR", "title": "Guide to Trump's Military Actions", "date": "2026-03"},
    ],

    # === CHINA / BELT AND ROAD ===
    "china_bri_latam": [
        {"url": "https://www.csis.org/analysis/tracking-chinas-digital-silk-road",
         "institution": "CSIS", "title": "Tracking China's Digital Silk Road", "date": "ongoing"},
        {"url": "https://greenfdc.org/",
         "institution": "Green Finance & Development Center", "title": "BRI Monitor", "date": "ongoing"},
        {"url": "https://www.cfr.org/tracker/china-overseas-lending",
         "institution": "CFR", "title": "China's Overseas Lending Tracker", "date": "ongoing"},
    ],

    # === TARIFFS / TRADE ===
    "tariffs_trade": [
        {"url": "https://www.skadden.com/insights/publications/2026/02/the-supreme-court-ends-ieepa-tariffs",
         "institution": "Skadden", "title": "Supreme Court Ends IEEPA Tariffs", "date": "2026-02"},
        {"url": "https://taxpolicycenter.org/taxvox/how-supreme-courts-ieepa-ruling-and-new-section-122-tariffs-reshape-costs-across-industries",
         "institution": "Tax Policy Center", "title": "IEEPA Ruling and Section 122 Tariffs", "date": "2026-03"},
        {"url": "https://taxfoundation.org/research/all/federal/trump-tariffs-trade-war/",
         "institution": "Tax Foundation", "title": "Tariff Tracker: Trump Tariffs", "date": "ongoing"},
        {"url": "https://www.piie.com/research/piie-charts/us-tariff-tracker",
         "institution": "Peterson Institute", "title": "US Tariff Tracker", "date": "ongoing"},
    ],

    # === SEMICONDUCTORS / CHIPS ===
    "chips_semiconductors": [
        {"url": "https://www.nist.gov/news-events/news/2026/01/department-commerces-chips-program-announces-letter-intent-usa-rare-earth",
         "institution": "NIST", "title": "CHIPS + USA Rare Earth LOI", "date": "2026-01"},
        {"url": "https://www.nist.gov/chips",
         "institution": "NIST", "title": "CHIPS Program Office", "date": "ongoing"},
        {"url": "https://www.semiconductors.org/",
         "institution": "SIA", "title": "Semiconductor Industry Association", "date": "ongoing"},
    ],

    # === MUNICH SECURITY CONFERENCE ===
    "munich_security": [
        {"url": "https://securityconference.org/en/publications/",
         "institution": "MSC", "title": "Munich Security Conference Publications", "date": "ongoing"},
        {"url": "https://dgap.org/en/research/publications?topic=Munich+Security+Conference",
         "institution": "DGAP", "title": "German Council on Foreign Relations - MSC Coverage", "date": "ongoing"},
    ],

    # === FIVE EYES / INTELLIGENCE ===
    "five_eyes": [
        {"url": "https://www.aspi.org.au/report/five-eyes-intelligence-oversight-and-review-council",
         "institution": "ASPI", "title": "Five Eyes Intelligence Oversight", "date": "ongoing"},
        {"url": "https://www.csis.org/analysis/five-eyes-intelligence-alliance",
         "institution": "CSIS", "title": "Five Eyes Intelligence Alliance", "date": "ongoing"},
        {"url": "https://crsreports.congress.gov/product/pdf/IF/IF11987",
         "institution": "CRS", "title": "Intelligence Community Overview", "date": "ongoing"},
        {"url": "https://www.chathamhouse.org/publications/the-future-of-five-eyes",
         "institution": "Chatham House", "title": "The Future of Five Eyes", "date": "ongoing"},
    ],

    # === ALLIANCE DYNAMICS ===
    "transatlantic_alliance": [
        {"url": "https://www.gmfus.org/news/us-national-defense-strategy",
         "institution": "GMF", "title": "US National Defense Strategy Analysis", "date": "2026-01"},
        {"url": "https://www.ui.se/globalassets/ui.se-eng/publications/ui-publications/2026/ui-commentary-no.-2-march-2026-pdf.pdf",
         "institution": "Swedish Institute (UI)", "title": "Trump's Foreign Policy Divides Far-Right", "date": "2026-03"},
        {"url": "https://www.chathamhouse.org/publications",
         "institution": "Chatham House", "title": "Publications", "date": "ongoing"},
        {"url": "https://rusi.org/explore-our-research",
         "institution": "RUSI", "title": "Research Publications", "date": "ongoing"},
    ],
}

# Domain quality scores for result ranking
DOMAIN_QUALITY = {
    # Tier 1: Gold standard think tanks and government sources
    "csis.org": 1.0, "rand.org": 1.0, "cfr.org": 1.0, "crsreports.congress.gov": 1.0,
    "congress.gov": 1.0, "state.gov": 1.0, "defense.gov": 1.0, "nist.gov": 1.0,
    "whitehouse.gov": 0.95, "treasury.gov": 0.95, "eia.gov": 0.95,

    # Tier 2: Major think tanks
    "brookings.edu": 0.95, "carnegieendowment.org": 0.95, "cnas.org": 0.95,
    "heritage.org": 0.9, "aei.org": 0.9, "atlanticcouncil.org": 0.9,
    "gmfus.org": 0.9, "stimson.org": 0.9, "sipri.org": 0.95,

    # Tier 3: International policy institutions
    "chathamhouse.org": 0.9, "iiss.org": 0.9, "rusi.org": 0.9,
    "securityconference.org": 0.9, "lowy.org": 0.9, "aspi.org.au": 0.9,
    "iea.org": 0.9, "imf.org": 0.9, "bis.org": 0.9,

    # Tier 4: Quality journalism and defense-specific
    "foreignaffairs.com": 0.85, "foreignpolicy.com": 0.85,
    "warontherocks.com": 0.85, "thediplomat.com": 0.85,
    "reuters.com": 0.85, "apnews.com": 0.85, "bbc.com": 0.8,
    "ft.com": 0.85, "economist.com": 0.85, "asia.nikkei.com": 0.85,
    "scmp.com": 0.8, "aljazeera.com": 0.75,

    # Tier 5: Academic
    "arxiv.org": 0.8, "jstor.org": 0.8, "ssrn.com": 0.8,

    # Negative signals (penalize)
    "wikipedia.org": 0.3, "merriam-webster.com": 0.0, "dictionary.com": 0.0,
    "cambridge.org/dictionary": 0.0, "tripadvisor.com": 0.0,
    "lonely planet.com": 0.0, "munich.travel": 0.0,
    "fivem.net": 0.0, "daz3d.com": 0.0,
    "timeanddate.com": 0.1, "zhihu.com": 0.0,
}


def match_topic(query: str) -> Optional[str]:
    """Match a search query to a topic in the source library."""
    q = query.lower()

    topic_keywords = {
        "nds": ["national defense strategy", "nds 2026", "defense strategy"],
        "nato_spending": ["nato", "defence expenditure", "defense spending", "burden sharing", "european defense"],
        "dollar_reserve": ["dollar", "reserve currency", "de-dollarization", "dedollarization", "cofer"],
        "brics_payment": ["brics", "cbdc", "cips", "mbridge", "yuan settlement", "alternative payment"],
        "iran_epic_fury": ["epic fury", "iran strike", "iran war", "khamenei"],
        "hormuz_energy": ["hormuz", "strait", "oil transit", "energy disruption"],
        "aukus": ["aukus", "submarine", "pillar i", "pillar ii"],
        "quad": ["quad", "quadrilateral"],
        "venezuela_operation": ["venezuela", "maduro", "absolute resolve"],
        "china_bri_latam": ["belt and road", "bri", "latin america", "chinese port", "chinese infrastructure"],
        "tariffs_trade": ["tariff", "ieepa", "section 122", "trade war", "trade act"],
        "chips_semiconductors": ["chips act", "semiconductor", "rare earth", "chip"],
        "munich_security": ["munich security", "msc 2026"],
        "five_eyes": ["five eyes", "fvey", "intelligence sharing", "intelligence cooperation"],
        "transatlantic_alliance": ["transatlantic", "far-right", "european allies", "alliance fracture"],
    }

    for topic, keywords in topic_keywords.items():
        if any(kw in q for kw in keywords):
            return topic
    return None


def get_sources(query: str) -> List[Dict[str, str]]:
    """Get curated source URLs for a query topic."""
    topic = match_topic(query)
    if topic and topic in POLICY_SOURCES:
        return POLICY_SOURCES[topic]
    return []


def get_domain_score(url: str) -> float:
    """Get quality score for a URL's domain."""
    from urllib.parse import urlparse
    try:
        domain = urlparse(url).netloc.lower()
        # Strip www.
        if domain.startswith("www."):
            domain = domain[4:]
        # Check exact match first
        if domain in DOMAIN_QUALITY:
            return DOMAIN_QUALITY[domain]
        # Check if any key is a substring
        for key, score in DOMAIN_QUALITY.items():
            if key in domain:
                return score
        # Unknown domain: neutral score
        return 0.5
    except Exception:
        return 0.5


def rank_results(results: list) -> list:
    """Re-rank search results by domain quality."""
    for r in results:
        url = r.get("url", "")
        domain_score = get_domain_score(url)
        original_score = r.get("score", 0.5)
        # Blend: 40% original relevance, 60% domain quality
        r["policy_score"] = (original_score * 0.4) + (domain_score * 0.6)
        r["domain_quality"] = domain_score

    return sorted(results, key=lambda x: x.get("policy_score", 0), reverse=True)


def filter_junk(results: list) -> list:
    """Remove obviously irrelevant results (dictionaries, tourism, gaming)."""
    junk_domains = {
        "merriam-webster.com", "dictionary.com", "cambridge.org",
        "tripadvisor.com", "tripadvisor.co", "lonelyplanet.com", "munich.travel",
        "muenchen.de", "travel.usnews.com",
        "fivem.net", "cfx.re", "forum.cfx.re", "runtime.fivem.net",
        "daz3d.com", "zhihu.com", "timeanddate.com",
        "countryreports.org", "britannica.com/place",
        "wiktionary.org", "thefreedictionary.com",
        "oxfordlearnersdictionaries.com", "collinsdictionary.com",
        "servers.fivem.net", "docs.fivem.net",
    }
    filtered = []
    for r in results:
        url = r.get("url", "").lower()
        if not any(junk in url for junk in junk_domains):
            filtered.append(r)
    return filtered
