#!/usr/bin/env python3
"""Export Postgres rows newer than a cutover mark onto a SQLite *copy*.

CUTOVER-PLAN §5 rollback helper. Never writes the live catalog by default.
Does not set DATABASE_URL and does not start uvicorn.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shopifyseo.cutover.delta import (  # noqa: E402
    LiveSqliteRefused,
    apply_delta_to_sqlite_copy,
    export_pg_delta,
    load_cutover_mark,
    refuse_live_sqlite,
)
from shopifyseo.db import connect_postgres  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mark",
        required=True,
        help="cutover_mark.json written by scripts/pg_cutover.sh",
    )
    parser.add_argument(
        "--sqlite-copy",
        required=True,
        help="Destination SQLite file (a copy, not the live catalog)",
    )
    parser.add_argument(
        "--create-copy-from",
        help="Copy this SQLite file to --sqlite-copy before applying (still not live)",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("CUTOVER_DATABASE_URL") or os.environ.get("PG_CUTOVER_URL"),
        help="Postgres URL (CUTOVER_DATABASE_URL). Not DATABASE_URL.",
    )
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="Dangerous: allow writing a path that looks like the live catalog",
    )
    args = parser.parse_args(argv)
    if not args.postgres_url:
        print("error: pass --postgres-url or set CUTOVER_DATABASE_URL", file=sys.stderr)
        return 2
    try:
        dest = refuse_live_sqlite(args.sqlite_copy, allow_live=args.allow_live)
    except LiveSqliteRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.create_copy_from:
        src = Path(args.create_copy_from).expanduser()
        if not src.is_file():
            print(f"error: --create-copy-from not found: {src}", file=sys.stderr)
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(f"copied {src} -> {dest}")
    elif not dest.is_file():
        print(f"error: --sqlite-copy does not exist: {dest}", file=sys.stderr)
        return 2
    mark = load_cutover_mark(args.mark)
    conn = connect_postgres(args.postgres_url)
    try:
        deltas = export_pg_delta(conn, mark)
    finally:
        conn.close()
    applied = apply_delta_to_sqlite_copy(dest, deltas, allow_live=args.allow_live)
    total_rows = 0
    for item in deltas:
        if item.skipped:
            print(f"SKIP {item.table}: {item.skipped}")
            continue
        n = applied.get(item.table, 0)
        total_rows += n
        print(f"OK  {item.table}: {len(item.rows)} exported, {n} applied")
    print(f"applied {total_rows} rows to {dest}")
    print("live uvicorn / DATABASE_URL were not changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
