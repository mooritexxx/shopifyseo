#!/usr/bin/env python3
"""Check mixed-type leftovers and report cluster_keywords orphans."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shopifyseo.cutover.verify import verify_values  # noqa: E402
from shopifyseo.db import connect_postgres, connect_sqlite  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("CUTOVER_DATABASE_URL") or os.environ.get("PG_CUTOVER_URL"),
        help="Postgres URL (CUTOVER_DATABASE_URL). Not DATABASE_URL.",
    )
    parser.add_argument("--sqlite", help="Optional SQLite working copy (extra notes)")
    parser.add_argument(
        "--fail-on-orphans",
        action="store_true",
        help="Treat cluster_keywords orphans as a hard failure (default: report only)",
    )
    args = parser.parse_args(argv)
    if not args.postgres_url:
        print("error: pass --postgres-url or set CUTOVER_DATABASE_URL", file=sys.stderr)
        return 2
    sqlite_conn = connect_sqlite(args.sqlite) if args.sqlite else None
    pg_conn = connect_postgres(args.postgres_url)
    try:
        report = verify_values(
            pg_conn, sqlite_conn=sqlite_conn, fail_on_orphans=args.fail_on_orphans
        )
    finally:
        pg_conn.close()
        if sqlite_conn is not None:
            sqlite_conn.close()
    for note in report.notes:
        print(f"NOTE {note}")
    for issue in report.issues:
        print(
            f"FAIL {issue.table}.{issue.column} {issue.kind} count={issue.count} {issue.detail}",
            file=sys.stderr,
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
