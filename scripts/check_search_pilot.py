"""Small, explicit live checks. Never scheduled and never a broad accuracy score."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlparse

import httpx

DEFAULT_CASES = Path(__file__).resolve().parents[1] / "tests/fixtures/search_pilot.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def latency(rows):
    values = sorted(row["seconds"] for row in rows)
    if not values:
        return {}
    return {"samples": len(values), "p50_seconds": values[math.ceil(len(values) * .5) - 1],
            "p95_seconds": values[math.ceil(len(values) * .95) - 1], "max_seconds": values[-1]}


def target_match(case, result):
    if "target" in case or "targets" in case:
        targets = case.get("targets") or [case["target"]]
        parsed = urlparse(result.get("url", ""))
        host = (parsed.hostname or "").lower()
        return any((host == target["host"] or host.endswith("." + target["host"]))
                   and target["path"].casefold() in parsed.path.casefold() for target in targets)
    return None  # News needs human relevance/freshness review, not a fake accuracy score.


def probe(base, path, token, params=None, timeout=60):
    start = time.monotonic()
    row = {"at": utc_now()}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", base + path, params=params,
                               headers={"Authorization": "Bearer " + token} if token else {}) as response:
                row["http_status"] = response.status_code
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 2_000_000:
                        raise ValueError("Response exceeds pilot size limit")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected JSON object")
                row["data"] = data
    except Exception as exc:
        # Never include URLs, headers, response bodies or credentials in errors.
        row["error"] = type(exc).__name__
    row["seconds"] = round(time.monotonic() - start, 4)
    return row


def benchmark(base, token, cases):
    rows = []
    for case in cases:
        row = probe(base, "/search", token, {"q": case["query"], "mode": case["mode"], "count": 3})
        data = row.pop("data", {})
        results = data.get("results", [])
        meta = data.get("meta", {})
        if not isinstance(results, list) or any(not isinstance(x, dict) for x in results) or not isinstance(meta, dict):
            row["error"] = "InvalidSearchShape"
            results, meta = [], {}
        row.update(id=case["id"], mode=case["mode"], query=case["query"],
                   cached=meta.get("cached"), upstream_status=meta.get("upstream_status"),
                   upstream_errors=meta.get("upstream_errors", []),
                   engine_attempts=meta.get("engine_attempts", []),
                   results=[{k: x.get(k) for k in ("title", "url", "snippet", "engines")} for x in results[:3]])
        row["target_in_top3"] = (any(target_match(case, r) for r in results[:3])
                                 if "topic_terms" not in case else None)
        if "topic_terms" in case:
            row["topic_matches"] = sum(any(term in (str(x.get("title", "")) + " " + str(x.get("snippet", ""))).casefold()
                                           for term in case["topic_terms"]) for x in results[:3])
            row["manual_review_required"] = "News relevance and publication dates"
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("id", "seconds", "cached", "target_in_top3", "upstream_status")}), flush=True)
    return {"kind": "benchmark", "cases": rows, "latency": latency(rows),
            "note": "Target matches and news topic checks are screening signals, not semantic accuracy."}


def reliability(base, token, seconds, interval):
    started = time.monotonic()
    checks = []
    next_probe = started
    while True:
        row = probe(base, "/ready", token, timeout=8)
        data = row.pop("data", {})
        row["ready"] = row.get("http_status") == 200 and data.get("searxng_available") is True
        checks.append(row)
        if time.monotonic() - started >= seconds:
            break
        next_probe = min(next_probe + interval, started + seconds)
        time.sleep(max(0, next_probe - time.monotonic()))
    # Seven requests, with concurrency 1, 2, then 4, to one warmed cache entry.
    params = {"q": "Python asyncio TaskGroup documentation", "mode": "general", "count": 3}
    warm = probe(base, "/search", token, params)
    warm_data = warm.pop("data", {})
    rows = []
    if warm.get("http_status") == 200 and warm_data.get("results"):
        expected_urls = [x["url"] for x in warm_data["results"]]
        for concurrency in (1, 2, 4):
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                batch = list(pool.map(lambda _: probe(base, "/search", token, params), range(concurrency)))
            for row in batch:
                data = row.pop("data", {})
                row.update(concurrency=concurrency, cached=data.get("meta", {}).get("cached"),
                           consistent=[x.get("url") for x in data.get("results", [])] == expected_urls)
                rows.append(row)
    else:
        warm["error"] = "WarmupFailed; cached load skipped"
    return {"kind": "reliability", "observation_seconds": round(time.monotonic() - started, 2),
            "readiness": checks, "warmup": warm, "cached_load": rows, "load_latency": latency(rows),
            "note": "Brief readiness and cached-request concurrency check; not multi-day uptime or uncached search throughput."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", choices=["benchmark", "reliability"])
    parser.add_argument("--base-url", default="http://127.0.0.1:3939")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", required=True, help="Source commit/image and route under test")
    args = parser.parse_args()
    url = urlparse(args.base_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        parser.error("Use a plain HTTP(S) API origin without credentials")
    token = os.environ.get("AGENT_SEARCH_TOKEN", "")
    if token and url.scheme != "https" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        parser.error("Remote bearer tokens require HTTPS")
    if not 0 <= args.seconds <= 600 or not 10 <= args.interval <= 60:
        parser.error("Keep this pilot within 0–600 seconds and 10–60 second intervals")
    output = {"started_at": utc_now(), "label": args.label, "base_url": args.base_url}
    if args.batch == "benchmark":
        raw = args.cases.read_bytes()
        cases = json.loads(raw)
        if not isinstance(cases, list) or not 1 <= len(cases) <= 12:
            parser.error("Use a fixed batch of 1–12 cases")
        output["fixture_sha256"] = hashlib.sha256(raw).hexdigest()
        output["result"] = benchmark(args.base_url.rstrip("/"), token, cases)
    else:
        output["result"] = reliability(args.base_url.rstrip("/"), token, args.seconds, args.interval)
    output["finished_at"] = utc_now()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print("Evidence saved to " + str(args.output), flush=True)
    # A completed measurement can contain failed targets; never silently claim it passed.
    result = output["result"]
    if args.batch == "benchmark":
        failed = any(r.get("error") or r.get("http_status") != 200 for r in result["cases"])
    else:
        failed = (any(not r["ready"] for r in result["readiness"]) or not result["cached_load"]
                  or any(r.get("http_status") != 200 or not r.get("cached") or not r.get("consistent") for r in result["cached_load"]))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
