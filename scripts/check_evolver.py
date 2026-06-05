#!/usr/bin/env python3
"""
Parse and report AgentSearch evolver stats.
Usage: python3 check_evolver.py [--days N] [--apply]
"""

import sys
import json
import os
import argparse
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = os.getenv("AGENT_SEARCH_URL", "http://localhost:3939").rstrip("/")


def load_token():
    token = (os.environ.get("AGENT_SEARCH_TOKEN") or os.environ.get("AGENTSEARCH_TOKEN") or "").strip()
    if token:
        return token
    for path in [
        Path.cwd() / "credentials/agent-search-token.txt",
        Path.home() / ".openclaw/workspace/credentials/agent-search-token.txt",
        Path.home() / ".config/agent-search/token",
    ]:
        try:
            if path.exists():
                token = path.read_text().strip()
                if token:
                    return token
        except Exception:
            continue
    return None


def fetch(path, token, method="GET"):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} from {url}")
        return None
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None


def parse_evolver(data):
    """Parse evolver stats and return a structured report."""
    if not data:
        return None

    overall = data.get("overall", {})
    strategies = data.get("strategies", {})
    domains = data.get("domains", [])
    period = data.get("period_days", 7)

    total = overall.get("total_fetches", 0)
    successes = overall.get("successes", 0)
    success_rate = overall.get("success_rate", 0)

    # Grade
    if success_rate >= 0.95:
        grade = "A"
    elif success_rate >= 0.85:
        grade = "B"
    elif success_rate >= 0.70:
        grade = "C"
    elif success_rate >= 0.50:
        grade = "D"
    else:
        grade = "F"

    # Failing domains (>= 3 attempts, 0% success)
    failing = [d for d in domains if d.get("total", 0) >= 3 and d.get("success_rate", 1) == 0]
    struggling = [d for d in domains if 0 < d.get("success_rate", 1) < 0.5 and d.get("total", 0) >= 3]

    # Best strategies
    best = sorted(
        [(s, v) for s, v in strategies.items() if v.get("total", 0) >= 3],
        key=lambda x: x[1].get("win_rate", 0),
        reverse=True
    )

    return {
        "period_days": period,
        "total_fetches": total,
        "successes": successes,
        "success_rate": success_rate,
        "health_grade": grade,
        "failing_domains": failing,
        "struggling_domains": struggling,
        "strategies": best,
    }


def print_report(r):
    print(f"\n{'='*50}")
    print(f"  AgentSearch Evolver Report ({r['period_days']}d)")
    print(f"{'='*50}")
    print(f"  Health:   {r['health_grade']}")
    print(f"  Fetches:  {r['total_fetches']} total, {r['successes']} succeeded")
    print(f"  Rate:     {r['success_rate']:.1%}")

    print(f"\n  Strategies ({len(r['strategies'])} active):")
    for name, stats in r['strategies'][:6]:
        wr = stats.get('win_rate', 0)
        cnt = stats.get('total', 0)
        avg = stats.get('avg_chars', 0)
        bar = '█' * int(wr * 10) + '░' * (10 - int(wr * 10))
        print(f"    {name:<25} {bar} {wr:.0%}  ({cnt} uses, avg {avg:,} chars)")

    if r['failing_domains']:
        print(f"\n  ⚠️  Consistently failing ({len(r['failing_domains'])} domains):")
        for d in r['failing_domains'][:8]:
            print(f"    {d['domain']:<35} {d['total']} attempts, 0% success")
        print(f"    → Consider adding to blocked_domains.txt")

    if r['struggling_domains']:
        print(f"\n  ⚡ Struggling ({len(r['struggling_domains'])} domains):")
        for d in r['struggling_domains'][:5]:
            working = ', '.join(d.get('working_strategies', []) or ['none'])
            print(f"    {d['domain']:<35} {d['success_rate']:.0%}  (works via: {working})")

    print(f"\n{'='*50}\n")


def main():
    parser = argparse.ArgumentParser(description="Check AgentSearch evolver stats")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="Run evolve recommendations")
    args = parser.parse_args()

    token = load_token()
    if not token:
        print("Warning: no auth token found, trying unauthenticated")

    # Health check first
    health = fetch("/health", token)
    if not health or health.get("status") != "healthy":
        print(f"ERROR: AgentSearch not healthy: {health}")
        sys.exit(1)

    # Fetch stats
    data = fetch(f"/adapt/stats?days={args.days}", token)
    if not data:
        print("ERROR: Could not fetch evolver stats")
        sys.exit(1)

    report = parse_evolver(data)
    print_report(report)

    # Run evolve if requested
    if args.apply:
        print("Running evolve recommendations...")
        evolve = fetch(f"/adapt/evolve?days={args.days}", token, method="POST")
        if evolve:
            print(f"  Grade: {evolve.get('overall_health', '?')}")
            recs = evolve.get("recommendations", [])
            if recs:
                print(f"  Recommendations ({len(recs)}):")
                for r in recs:
                    print(f"    [{r.get('priority','?').upper()}] {r.get('description','')}")
            else:
                print("  No recommendations — system is healthy")
        print()

    return report


if __name__ == "__main__":
    main()
