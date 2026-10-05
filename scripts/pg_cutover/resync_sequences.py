#!/usr/bin/env python3
"""Ensure identity columns and setval(max+1) after pgloader."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shopifyseo.cutover.sequences import resync_cutover_identities  # noqa: E402
from shopifyseo.db import connect_postgres  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("CUTOVER_DATABASE_URL") or os.environ.get("PG_CUTOVER_URL"),
        help="Postgres URL (CUTOVER_DATABASE_URL). Not DATABASE_URL.",
    )
    args = parser.parse_args(argv)
    if not args.postgres_url:
        print("error: pass --postgres-url or set CUTOVER_DATABASE_URL", file=sys.stderr)
        return 2
    conn = connect_postgres(args.postgres_url)
    try:
        results = resync_cutover_identities(conn)
        if not getattr(conn, "autocommit", False):
            conn.commit()
    finally:
        conn.close()
    rc = 0
    for item in results:
        if item.error and not item.error.startswith("skipped"):
            print(f"FAIL {item.table}.{item.column}: {item.error}", file=sys.stderr)
            rc = 1
        elif item.error:
            print(f"SKIP {item.table}.{item.column}: {item.error}")
        else:
            print(f"OK  {item.table}.{item.column} -> {item.new_value}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
