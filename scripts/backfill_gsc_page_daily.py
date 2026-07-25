#!/usr/bin/env python3
"""Load the daily per-page Search Console history Google still retains (~16 months).

Regular syncs refresh only a short trailing window, so without this the trend columns
and charts stay empty until enough days accumulate. Google keeps the history, so it can
simply be fetched once.

    PYTHONPATH=. python scripts/backfill_gsc_page_daily.py [--days 480]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Runnable directly, without needing PYTHONPATH set.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shopifyseo.dashboard_actions._sync import GSC_BACKFILL_MAX_DAYS, backfill_gsc_page_daily
from shopifyseo.dashboard_store import DB_PATH, bootstrap_runtime_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=GSC_BACKFILL_MAX_DAYS,
        help=f"How many days back to pull (default {GSC_BACKFILL_MAX_DAYS}; Google retains ~16 months)",
    )
    parser.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    args = parser.parse_args()

    bootstrap_runtime_settings()
    result = backfill_gsc_page_daily(args.db, days=args.days)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
