#!/usr/bin/env python3
"""One-off: Labs ``serp_competitors`` for seed keywords in the dashboard SQLite DB.

Usage (repo root, same DB as the app uses by default)::

    PYTHONPATH=. python scripts/run_serp_competitors_from_seeds.py

Requires ``dataforseo_api_login`` / ``dataforseo_api_password`` in service settings.
"""

from __future__ import annotations

import json
import sys

from shopifyseo.dashboard_google import get_service_setting
from shopifyseo.market_context import get_primary_country_code

from backend.app.db import open_db_connection
from backend.app.services.keyword_research.dataforseo_client import call_serp_competitors


def main() -> int:
    conn = open_db_connection()
    try:
        raw = get_service_setting(conn, "seed_keywords", "[]")
        try:
            seeds = json.loads(raw)
        except json.JSONDecodeError:
            print("seed_keywords setting is not valid JSON.", file=sys.stderr)
            return 1
        if not isinstance(seeds, list) or not seeds:
            print("No seed keywords in DB.", file=sys.stderr)
            return 1
        keywords = []
        for s in seeds:
            if isinstance(s, dict) and s.get("keyword"):
                keywords.append(str(s["keyword"]))
            elif isinstance(s, str):
                keywords.append(s)
        login = (get_service_setting(conn, "dataforseo_api_login", "") or "").strip()
        password = (get_service_setting(conn, "dataforseo_api_password", "") or "").strip()
        if not login or not password:
            print("Configure DataForSEO login/password in Settings.", file=sys.stderr)
            return 1
        cc = (get_primary_country_code(conn) or "CA").strip().upper()
        if len(cc) != 2:
            cc = "CA"
    finally:
        conn.close()

    print(f"Using {len(keywords)} seed keywords (Labs max 200 per request).")
    print(f"Market: country_iso={cc!r}\n")

    rows, cost = call_serp_competitors(login, password, keywords, country_iso=cc, limit=50)
    print(f"API cost (reported): ${cost:.4f}\n")
    print(f"{'#':<4} {'domain':<42} {'etv':>12} {'kw hits':>8} {'rating':>7} {'vis':>6}")
    print("-" * 85)
    for i, r in enumerate(rows, 1):
        print(
            f"{i:<4} {r['domain']:<42} {int(r['etv']):>12,} "
            f"{r['keywords_count']:>8} {r['rating']:>7} {r['visibility']:>6.2f}"
        )
    want = "180smoke.ca"
    hit = next((r for r in rows if r["domain"] == want or r["domain"].endswith("180smoke.ca")), None)
    print()
    if hit:
        print(f"Found {want!r}: etv={int(hit['etv']):,}, keywords_count={hit['keywords_count']}")
    else:
        print(f"{want!r} not in top {len(rows)} rows for this keyword batch + limit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
