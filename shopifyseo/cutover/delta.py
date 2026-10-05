"""Reverse-export Postgres rows newer than a cutover mark onto a SQLite *copy*.

CUTOVER-PLAN §5: never write the live catalog file by default. Apply the
delta to a copy, then an operator can swap files after review.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shopifyseo.db import execute

from .catalog import DELTA_TABLES, TIMESTAMP_COLUMNS

LIVE_SQLITE_NAMES = frozenset({"shopify_catalog.sqlite3"})


class LiveSqliteRefused(RuntimeError):
    """Raised when a path resolves to the live catalog and live writes are off."""


def refuse_live_sqlite(path: str | Path, *, allow_live: bool = False) -> Path:
    """Resolve ``path`` and refuse the live catalog unless ``allow_live``."""
    resolved = Path(path).expanduser().resolve()
    if allow_live:
        return resolved
    live_env = os.environ.get("SHOPIFY_CATALOG_DB_PATH", "").strip()
    if live_env and Path(live_env).expanduser().resolve() == resolved:
        raise LiveSqliteRefused(
            f"refusing to write live SQLite {resolved} "
            "(SHOPIFY_CATALOG_DB_PATH). Pass a copy and omit --allow-live."
        )
    if resolved.name in LIVE_SQLITE_NAMES:
        raise LiveSqliteRefused(
            f"refusing to write live-looking SQLite {resolved}. "
            "Copy the file first; --allow-live is required to override."
        )
    return resolved


def write_cutover_mark(
    path: str | Path,
    *,
    cutover_at: datetime | None = None,
    sqlite_backup: str | None = None,
    postgres_target: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the mark file consumed by ``pg_to_sqlite_delta``."""
    when = cutover_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    payload = {
        "cutover_at": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cutover_epoch": int(when.timestamp()),
        "sqlite_backup": sqlite_backup,
        "postgres_target": postgres_target,
    }
    if extra:
        payload.update(extra)
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def load_cutover_mark(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _row_mapping(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return dict(row)
    keys = row.keys() if hasattr(row, "keys") else None
    if keys is not None:
        return {key: row[key] for key in keys}
    raise TypeError(f"cannot map row of type {type(row)!r}")


@dataclass
class TableDelta:
    table: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped: str | None = None


def export_pg_delta(
    pg_conn: Any,
    mark: dict[str, Any],
    *,
    tables: list[str] | None = None,
) -> list[TableDelta]:
    """Export Postgres rows newer than the cutover mark."""
    epoch = int(mark["cutover_epoch"])
    iso = str(mark.get("cutover_at") or "")
    wanted = set(tables) if tables else set(TIMESTAMP_COLUMNS)
    out: list[TableDelta] = []
    for table, (column, kind) in TIMESTAMP_COLUMNS.items():
        if table not in wanted:
            continue
        exists = execute(
            pg_conn,
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table,),
        ).fetchone()
        if not exists:
            out.append(TableDelta(table=table, skipped="table missing on postgres"))
            continue
        qtable, qcol = _quote_ident(table), _quote_ident(column)
        sql = f"SELECT * FROM {qtable} WHERE {qcol} IS NOT NULL AND {qcol} > ?"
        param: Any = epoch if kind == "epoch" else iso
        rows = [_row_mapping(row) for row in execute(pg_conn, sql, (param,)).fetchall()]
        out.append(TableDelta(table=table, rows=rows))
    return out


def apply_delta_to_sqlite_copy(
    sqlite_path: str | Path,
    deltas: list[TableDelta],
    *,
    allow_live: bool = False,
) -> dict[str, int]:
    """UPSERT exported rows into a SQLite copy. Never the live file by default."""
    dest = refuse_live_sqlite(sqlite_path, allow_live=allow_live)
    conn = sqlite3.connect(str(dest))
    conn.row_factory = sqlite3.Row
    applied: dict[str, int] = {}
    try:
        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        for delta in deltas:
            if delta.skipped or not delta.rows:
                continue
            if delta.table not in existing:
                continue
            pk = DELTA_TABLES.get(delta.table)
            if not pk:
                continue
            cols = list(delta.rows[0].keys())
            qtable = _quote_ident(delta.table)
            qcols = ", ".join(_quote_ident(c) for c in cols)
            placeholders = ", ".join("?" for _ in cols)
            conflict = ", ".join(_quote_ident(c) for c in pk)
            updates = ", ".join(
                f"{_quote_ident(c)} = excluded.{_quote_ident(c)}" for c in cols if c not in pk
            )
            sql = (
                f"INSERT INTO {qtable} ({qcols}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict}) DO UPDATE SET {updates}"
                if updates
                else f"INSERT INTO {qtable} ({qcols}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict}) DO NOTHING"
            )
            n = 0
            for row in delta.rows:
                conn.execute(sql, [row.get(c) for c in cols])
                n += 1
            applied[delta.table] = n
        conn.commit()
    finally:
        conn.close()
    return applied
