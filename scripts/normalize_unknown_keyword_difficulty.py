#!/usr/bin/env python3
"""One-off: convert stored keyword difficulty 0 to NULL.

DataForSEO sends ``keyword_difficulty: 0`` when it has not computed a difficulty
rather than omitting the field (verified against both ``keyword_overview/live``
and ``bulk_keyword_difficulty/live``). Ingest now normalizes that to NULL, but
rows written before that change still hold 0 and read as "trivially easy"
everywhere downstream.

Updates, in one transaction:
  * ``keyword_metrics.difficulty``
  * ``competitor_keyword_gaps.difficulty``
  * the ``target_keywords`` JSON blob in ``service_settings`` — required, or the
    next ``sync_keyword_metrics_to_db`` would write the zeros straight back
  * ``clusters.avg_difficulty``, recomputed excluding unknowns

Touches no other ``service_settings`` key, so API credentials are left alone.

Usage:  python3 scripts/normalize_unknown_keyword_difficulty.py [--db PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

TARGET_KEY = "target_keywords"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="shopify_catalog.sqlite3")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    km_zeros = cur.execute("SELECT COUNT(*) FROM keyword_metrics WHERE difficulty = 0").fetchone()[0]
    gap_zeros = cur.execute(
        "SELECT COUNT(*) FROM competitor_keyword_gaps WHERE difficulty = 0"
    ).fetchone()[0]

    row = cur.execute("SELECT value FROM service_settings WHERE key = ?", (TARGET_KEY,)).fetchone()
    blob_zeros = 0
    blob = None
    if row and row["value"]:
        blob = json.loads(row["value"])
        blob_zeros = sum(1 for i in blob.get("items", []) if i.get("difficulty") == 0)

    print(f"keyword_metrics.difficulty = 0        : {km_zeros}")
    print(f"competitor_keyword_gaps.difficulty = 0: {gap_zeros}")
    print(f"target_keywords JSON items with 0     : {blob_zeros}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    cur.execute("BEGIN")
    try:
        cur.execute("UPDATE keyword_metrics SET difficulty = NULL WHERE difficulty = 0")
        cur.execute("UPDATE competitor_keyword_gaps SET difficulty = NULL WHERE difficulty = 0")

        if blob is not None and blob_zeros:
            for item in blob.get("items", []):
                if item.get("difficulty") == 0:
                    item["difficulty"] = None
            cur.execute(
                "UPDATE service_settings SET value = ? WHERE key = ?",
                (json.dumps(blob), TARGET_KEY),
            )

        # Recompute cluster averages over known difficulties only. `avg_difficulty`
        # is NOT NULL, so clusters with no known KD keep 0.0 as the sentinel — the
        # UI renders 0 as "—" rather than as a difficulty.
        cur.execute(
            """
            UPDATE clusters SET avg_difficulty = COALESCE((
                SELECT ROUND(AVG(km.difficulty), 1)
                FROM cluster_keywords ck
                JOIN keyword_metrics km ON LOWER(km.keyword) = LOWER(ck.keyword)
                WHERE ck.cluster_id = clusters.id AND km.difficulty IS NOT NULL
            ), 0.0)
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    print("\nAfter:")
    for label, sql in [
        ("keyword_metrics zeros", "SELECT COUNT(*) FROM keyword_metrics WHERE difficulty = 0"),
        ("keyword_metrics NULL", "SELECT COUNT(*) FROM keyword_metrics WHERE difficulty IS NULL"),
        ("keyword_metrics real KD", "SELECT COUNT(*) FROM keyword_metrics WHERE difficulty > 0"),
        ("gaps zeros", "SELECT COUNT(*) FROM competitor_keyword_gaps WHERE difficulty = 0"),
        ("clusters avg 0 (unknown)", "SELECT COUNT(*) FROM clusters WHERE avg_difficulty = 0"),
        ("clusters with avg > 0", "SELECT COUNT(*) FROM clusters WHERE avg_difficulty > 0"),
    ]:
        print(f"  {label:<26}: {cur.execute(sql).fetchone()[0]}")

    creds = cur.execute(
        "SELECT COUNT(*) FROM service_settings WHERE key LIKE '%api_login%' OR key LIKE '%api_password%'"
    ).fetchone()[0]
    print(f"  credential keys intact     : {creds}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
