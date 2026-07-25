#!/usr/bin/env python3
"""Measure the URL Inspection rate Google actually sustains, before changing the sync.

The sync self-limits to 55 inspections/minute while Google documents 600/minute. Rather
than trust the documented figure, this drives real inspections at a target rate and
reports what was actually achieved, including every error status.

It aborts early if the error rate climbs, so a bad setting costs a handful of requests
rather than the whole run.

COST: every request spends one of the ~2000 daily URL Inspection quota for the property.
Nothing is written to the catalog — results are discarded after being counted.

    python scripts/probe_index_inspection_rate.py --count 300 --rate 200 --workers 10
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

import requests  # noqa: E402

import shopifyseo.dashboard_google as dg  # noqa: E402
from shopifyseo.dashboard_actions._rpm_limiter import PerMinuteRateLimiter  # noqa: E402
from shopifyseo.dashboard_http import HttpRequestError  # noqa: E402
from shopifyseo.dashboard_store import DB_PATH, bootstrap_runtime_settings  # noqa: E402
from shopifyseo.sqlite_utf8 import configure_sqlite_text_decode  # noqa: E402


# Plain session with connection pooling but NO automatic retries, so throttling shows up
# as-is instead of being retried away.
_RAW = requests.Session()
_RAW.mount("https://", requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=0))


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    configure_sqlite_text_decode(conn)
    return conn


def _targets(conn: sqlite3.Connection, count: int, offset: int = 0) -> list[str]:
    """Real catalog URLs — the same ones a sync would inspect."""
    import shopifyseo.dashboard_queries as dq

    need = count + offset
    urls: list[str] = []
    for row in dq.fetch_all_products(conn):
        urls.append(dq.object_url("product", row["handle"]))
        if len(urls) >= need:
            break
    if len(urls) < need:
        for row in dq.fetch_all_collections(conn):
            urls.append(dq.object_url("collection", row["handle"]))
            if len(urls) >= need:
                break
    return urls[offset : offset + count]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=300, help="How many URLs to inspect (spends daily quota)")
    ap.add_argument("--rate", type=int, default=200, help="Target inspections per minute")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--offset", type=int, default=0, help="Skip N URLs, so repeat runs use fresh ones")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--abort-error-pct", type=float, default=25.0, help="Stop if errors exceed this %% after 40 calls")
    args = ap.parse_args()

    bootstrap_runtime_settings()
    conn = _connect(args.db)
    try:
        site_url = (dg.get_service_setting(conn, "search_console_site") or "").strip()
        if not site_url:
            site_url = dg.preferred_site_url(conn, dg.get_search_console_sites(conn))
        if not site_url:
            print("No Search Console property selected.")
            return 1
        access_token = dg.get_google_access_token(conn)
        lang = "en-US"
        urls = _targets(conn, args.count, args.offset)
    finally:
        conn.close()

    if not urls:
        print("No catalog URLs found.")
        return 1

    print(f"property : {site_url}")
    print(f"plan     : {len(urls)} inspections, target {args.rate}/min, {args.workers} workers")
    print(f"quota    : this spends ~{len(urls)} of the ~2000 daily URL Inspection allowance\n")

    limiter = PerMinuteRateLimiter(args.rate)
    lock = threading.Lock()
    statuses: Counter = Counter()
    latencies: list[float] = []
    sample_errors: list[str] = []
    first_error_at: list[float] = []
    aborted = threading.Event()
    done = 0
    started = time.monotonic()

    def _inspect(url: str) -> None:
        nonlocal done
        if aborted.is_set():
            return
        limiter.acquire()
        if aborted.is_set():
            return
        t0 = time.monotonic()
        label = "ok"
        try:
            # Deliberately NOT dg.google_api_post: that path auto-retries 429/5xx with
            # backoff, which would both hide the throttling we are trying to measure and
            # distort the achieved rate. This posts once and reports exactly what Google said.
            resp = _RAW.post(
                "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={"inspectionUrl": url, "siteUrl": site_url, "languageCode": lang},
                timeout=60,
            )
            if resp.status_code != 200:
                label = f"HTTP {resp.status_code}"
                with lock:
                    if len(sample_errors) < 3:
                        sample_errors.append(resp.text[:300])
        except HttpRequestError as exc:
            label = f"HTTP {exc.status}" if exc.status else "connection error"
        except Exception as exc:  # noqa: BLE001
            label = f"{type(exc).__name__}"
        dt = time.monotonic() - t0
        with lock:
            done += 1
            statuses[label] += 1
            latencies.append(dt)
            if label != "ok" and not first_error_at:
                first_error_at.append(time.monotonic() - started)
            n = done
            errs = sum(v for k, v in statuses.items() if k != "ok")
            if n >= 40 and (errs / n) * 100 > args.abort_error_pct and not aborted.is_set():
                aborted.set()
                print(f"\n!! aborting: {errs}/{n} errors ({errs / n * 100:.0f}%) exceeded the {args.abort_error_pct}% threshold")
            if n % 25 == 0:
                el = time.monotonic() - started
                print(f"  {n:4d}/{len(urls)}  achieved {n / el * 60:6.1f}/min  errors={errs}")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(_inspect, u) for u in urls]
        for f in as_completed(futures):
            f.result()

    elapsed = time.monotonic() - started
    errs = sum(v for k, v in statuses.items() if k != "ok")
    result = {
        "requested": len(urls),
        "completed": done,
        "aborted_early": aborted.is_set(),
        "target_rate_per_min": args.rate,
        "achieved_rate_per_min": round(done / elapsed * 60, 1) if elapsed else 0,
        "elapsed_seconds": round(elapsed, 1),
        "error_count": errs,
        "error_pct": round(errs / done * 100, 1) if done else 0,
        "status_breakdown": dict(statuses),
        "latency_ms": {
            "median": round(statistics.median(latencies) * 1000) if latencies else None,
            "p95": round(sorted(latencies)[int(len(latencies) * 0.95)] * 1000) if len(latencies) > 20 else None,
            "max": round(max(latencies) * 1000) if latencies else None,
        },
        "seconds_to_first_error": round(first_error_at[0], 1) if first_error_at else None,
        "sample_error_bodies": sample_errors,
    }
    print("\n" + json.dumps(result, indent=2))

    ok = errs == 0 and not aborted.is_set()
    print(
        f"\nVERDICT: {args.rate}/min "
        + ("SUSTAINED cleanly." if ok else "did NOT hold cleanly — see status_breakdown above.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
