#!/usr/bin/env python3
"""Compare table counts between the SQLite working copy and Postgres."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shopifyseo.cutover.verify import verify_counts  # noqa: E402
from shopifyseo.db import connect_postgres, connect_sqlite  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", required=True, help="SQLite working copy path")
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("CUTOVER_DATABASE_URL") or os.environ.get("PG_CUTOVER_URL"),
        help="Postgres URL (CUTOVER_DATABASE_URL). Not DATABASE_URL.",
    )
    args = parser.parse_args(argv)
    if not args.postgres_url:
        print("error: pass --postgres-url or set CUTOVER_DATABASE_URL", file=sys.stderr)
        return 2
    sqlite_conn = connect_sqlite(args.sqlite)
    pg_conn = connect_postgres(args.postgres_url)
    try:
        report = verify_counts(sqlite_conn, pg_conn)
    finally:
        sqlite_conn.close()
        pg_conn.close()
    for table, count in report.matches.items():
        print(f"OK  {table}: {count}")
    for miss in report.mismatches:
        print(
            f"FAIL {miss.table}: sqlite={miss.sqlite_count} postgres={miss.postgres_count}",
            file=sys.stderr,
        )
    for name in report.sqlite_only:
        print(f"FAIL sqlite-only table: {name}", file=sys.stderr)
    for name in report.postgres_only:
        print(f"FAIL postgres-only table: {name}", file=sys.stderr)
    if not report.ok:
        return 1
    print(f"counts match ({len(report.matches)} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
