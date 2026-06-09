"""
evolver.py — Self-Improvement Engine for AgentSearch

Analyzes fetch patterns to identify:
- Domains that consistently fail (→ block or build adapters)
- Strategies that never work (→ skip for speed)
- Strategy ordering that could be optimized
- Missing adapter opportunities

Reports failures, tracks patterns, and provides recommendations.
Does NOT use LLM calls — pure data analysis.
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import logging
import os

logger = logging.getLogger("agentsearch.evolver")

ADAPTERS_DIR = Path(os.getenv("ADAPTERS_DIR", "adapters"))
DYNAMIC_BLOCKLIST_PATH = Path(os.getenv("DATA_DIR", "data")) / "blocked_domains.txt"


def _safe_log_value(value: object, limit: int = 200) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text[:limit]


class Evolver:
    """Analyzes fetch patterns and recommends improvements."""

    def __init__(self, db_path: str | None = None):
        data_dir = Path(os.getenv("DATA_DIR", "data"))
        self.db_path = db_path or str(data_dir / "content_cache.db")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def report_failure(
        self,
        url: str,
        strategies_tried: list[str],
        error: Optional[str] = None,
    ) -> dict:
        """Report a fetch failure for pattern analysis."""
        domain = urlparse(url).netloc
        now = time.time()

        with self._conn() as conn:
            conn.execute("""
                INSERT INTO fetch_log
                (url, domain, strategy, success, chars, strategies_tried, error, timestamp)
                VALUES (?, ?, ?, 0, 0, ?, ?, ?)
            """, (url, domain, None, ",".join(strategies_tried), error, now))
            conn.commit()

        return {"recorded": True, "domain": domain, "strategies_tried": strategies_tried}

    def get_adaptation_stats(self, days: int = 7) -> dict:
        """Get comprehensive adaptation statistics."""
        cutoff = time.time() - (days * 86400)

        with self._conn() as conn:
            # Overall success rate
            total = conn.execute(
                "SELECT COUNT(*) FROM fetch_log WHERE timestamp > ?", (cutoff,)
            ).fetchone()[0]
            successes = conn.execute(
                "SELECT COUNT(*) FROM fetch_log WHERE success = 1 AND timestamp > ?",
                (cutoff,),
            ).fetchone()[0]

            # Strategy effectiveness
            strategy_data = {}
            for row in conn.execute("""
                SELECT strategy,
                       COUNT(*) as total,
                       SUM(success) as wins,
                       AVG(chars) as avg_chars,
                       AVG(CASE WHEN success = 1 THEN chars ELSE NULL END) as avg_success_chars
                FROM fetch_log
                WHERE strategy IS NOT NULL AND timestamp > ?
                GROUP BY strategy
                ORDER BY total DESC
            """, (cutoff,)):
                strategy_data[row["strategy"]] = {
                    "total": row["total"],
                    "wins": row["wins"] or 0,
                    "win_rate": (row["wins"] or 0) / row["total"],
                    "avg_chars": round(row["avg_chars"] or 0),
                    "avg_success_chars": round(row["avg_success_chars"] or 0),
                }

            # Domain health
            domain_data = []
            for row in conn.execute("""
                SELECT domain,
                       COUNT(*) as total,
                       SUM(success) as wins,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as fails,
                       GROUP_CONCAT(DISTINCT strategy) as strategies_that_worked
                FROM fetch_log
                WHERE timestamp > ?
                GROUP BY domain
                HAVING total >= 2
                ORDER BY fails DESC
                LIMIT 30
            """, (cutoff,)):
                domain_data.append({
                    "domain": row["domain"],
                    "total": row["total"],
                    "wins": row["wins"] or 0,
                    "fails": row["fails"] or 0,
                    "success_rate": (row["wins"] or 0) / row["total"],
                    "working_strategies": (row["strategies_that_worked"] or "").split(","),
                })

            # Strategy chain analysis — which strategies are tried before success?
            chain_data = []
            for row in conn.execute("""
                SELECT strategies_tried, strategy as winning_strategy, COUNT(*) as count
                FROM fetch_log
                WHERE success = 1 AND strategies_tried IS NOT NULL AND timestamp > ?
                GROUP BY strategies_tried, strategy
                ORDER BY count DESC
                LIMIT 20
            """, (cutoff,)):
                chain_data.append({
                    "chain": row["strategies_tried"],
                    "winner": row["winning_strategy"],
                    "count": row["count"],
                })

        return {
            "period_days": days,
            "overall": {
                "total_fetches": total,
                "successes": successes,
                "success_rate": successes / total if total > 0 else 0,
            },
            "strategies": strategy_data,
            "domains": domain_data,
            "chains": chain_data,
        }

    def evolve(self, days: int = 7) -> dict:
        """
        Analyze patterns and generate concrete recommendations.
        No LLM — pure data analysis with heuristic rules.
        """
        stats = self.get_adaptation_stats(days)
        recommendations = []

        # 1. Identify domains to block (>5 attempts, 0% success)
        hopeless_domains = [
            d for d in stats["domains"]
            if d["total"] >= 5 and d["success_rate"] == 0
        ]
        if hopeless_domains:
            recommendations.append({
                "type": "block_domains",
                "priority": "high",
                "description": f"{len(hopeless_domains)} domains have 0% success rate with 5+ attempts",
                "domains": [d["domain"] for d in hopeless_domains],
                "action": "Add to BLOCKED_FETCH_DOMAINS in killchain.py",
            })

        # 2. Identify strategies that never win (waste of time)
        dead_strategies = [
            name for name, data in stats["strategies"].items()
            if data["total"] >= 10 and data["win_rate"] == 0
        ]
        if dead_strategies:
            recommendations.append({
                "type": "disable_strategies",
                "priority": "medium",
                "description": f"Strategies with 0% win rate over {days}d: {', '.join(dead_strategies)}",
                "strategies": dead_strategies,
                "action": "Consider disabling or deprioritizing these strategies",
            })

        # 3. Identify strategy reordering opportunities
        # If wayback or google-cache win frequently, move them up
        for name, data in stats["strategies"].items():
            if name in ("wayback", "google-cache") and data["win_rate"] > 0.3:
                recommendations.append({
                    "type": "reorder_strategy",
                    "priority": "medium",
                    "description": f"'{name}' has {data['win_rate']:.0%} success rate — consider promoting it in the chain",
                    "strategy": name,
                    "win_rate": data["win_rate"],
                })

        # 4. Identify domains that need custom adapters
        adapter_candidates = [
            d for d in stats["domains"]
            if d["total"] >= 3 and 0 < d["success_rate"] < 0.3
        ]
        if adapter_candidates:
            recommendations.append({
                "type": "build_adapters",
                "priority": "medium",
                "description": f"{len(adapter_candidates)} domains have low success rates — may need custom adapters",
                "domains": [
                    {"domain": d["domain"], "success_rate": d["success_rate"], "working_strategies": d["working_strategies"]}
                    for d in adapter_candidates[:10]
                ],
                "action": "Build custom adapters targeting common failure patterns",
            })

        # 5. Check if overall success rate is declining
        recent_stats = self.get_adaptation_stats(days=1)
        if (stats["overall"]["success_rate"] > 0 and
                recent_stats["overall"]["total_fetches"] >= 5 and
                recent_stats["overall"]["success_rate"] < stats["overall"]["success_rate"] * 0.7):
            recommendations.append({
                "type": "regression_alert",
                "priority": "high",
                "description": (
                    f"Success rate dropped: {stats['overall']['success_rate']:.0%} "
                    f"({days}d avg) → {recent_stats['overall']['success_rate']:.0%} (last 24h)"
                ),
                "action": "Investigate recent failures for common patterns",
            })

        # 6. List available adapters vs potential needs
        existing_adapters = []
        if ADAPTERS_DIR.exists():
            existing_adapters = [
                f.stem for f in ADAPTERS_DIR.glob("*.py")
                if f.stem != "__init__"
            ]

        # Auto-apply safe recommendations
        actions_taken = self.auto_apply(recommendations)

        return {
            "analyzed_at": time.time(),
            "period_days": days,
            "overall_health": self._health_grade(stats),
            "stats_summary": {
                "total_fetches": stats["overall"]["total_fetches"],
                "success_rate": stats["overall"]["success_rate"],
                "active_strategies": len(stats["strategies"]),
                "problematic_domains": len([d for d in stats["domains"] if d["success_rate"] < 0.5]),
            },
            "recommendations": recommendations,
            "existing_adapters": existing_adapters,
            "actions_taken": actions_taken,
        }

    def auto_apply(self, recommendations: list[dict]) -> list[str]:
        """Auto-apply safe recommendations. Returns list of actions taken."""
        actions = []

        for rec in recommendations:
            if rec["type"] == "block_domains" and rec["priority"] == "high":
                # Auto-block domains with 0% success rate
                existing = set()
                try:
                    if DYNAMIC_BLOCKLIST_PATH.exists():
                        existing = {
                            line.strip().lower()
                            for line in DYNAMIC_BLOCKLIST_PATH.read_text().splitlines()
                            if line.strip() and not line.startswith("#")
                        }
                except Exception as exc:
                    logger.debug("Failed to read dynamic blocklist: %s", exc)

                new_domains = [d for d in rec["domains"] if d.lower() not in existing]
                if new_domains:
                    try:
                        with open(DYNAMIC_BLOCKLIST_PATH, "a") as f:
                            f.write(f"# Auto-blocked {time.strftime('%Y-%m-%d %H:%M')}\n")
                            for d in new_domains:
                                f.write(f"{d}\n")
                        actions.append(f"Blocked {len(new_domains)} domains: {', '.join(new_domains)}")
                        safe_domains = [_safe_log_value(domain) for domain in new_domains]
                        logger.info("Evolver auto-blocked domains: %s", safe_domains)
                    except Exception as e:
                        logger.warning("Failed to write dynamic blocklist: %s", e)

        return actions

    def _health_grade(self, stats: dict) -> str:
        """Grade overall fetch health A-F."""
        rate = stats["overall"]["success_rate"]
        if rate >= 0.85:
            return "A"
        elif rate >= 0.70:
            return "B"
        elif rate >= 0.55:
            return "C"
        elif rate >= 0.40:
            return "D"
        else:
            return "F"
