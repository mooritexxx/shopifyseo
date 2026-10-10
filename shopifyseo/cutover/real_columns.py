"""SQLite REAL-affinity columns for the pgloader double-precision CAST.

Used by cutover tests so a new REAL / FLOAT / DOUBLE column cannot land as
Postgres 4-byte ``real``. Does not open the live catalog.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from shopifyseo.db import connect_sqlite, execute

# pgloader SQLite default: ``type real to real`` / ``type float to float``.
# These source type names must all be overridden to double precision.
PGLOADER_FLOAT_SOURCE_TYPES = (
    "real",
    "float",
    "double",
    "double precision",
)

_TYPE_HEAD = re.compile(r"\s+", re.ASCII)


def sqlite_column_affinity(declared: str) -> str:
    """SQLite column affinity from a declared type (https://sqlite.org/datatype3.html)."""
    u = (declared or "").upper()
    if "INT" in u:
        return "INTEGER"
    if "CHAR" in u or "CLOB" in u or "TEXT" in u:
        return "TEXT"
    if "BLOB" in u:
        return "BLOB"
    if "REAL" in u or "FLOA" in u or "DOUB" in u:
        return "REAL"
    return "NUMERIC"


def pgloader_source_type(declared: str) -> str:
    """Declared type stripped to the name pgloader's CAST ``type …`` matches."""
    head = (declared or "").split("(", 1)[0]
    return _TYPE_HEAD.sub(" ", head).strip().lower()


def bootstrap_sqlite_schema(conn: Any) -> None:
    """Apply the same CREATE/ALTER path the live SQLite catalog uses."""
    from backend.app.services.team_tasks import ensure_schema as ensure_tasks
    from shopifyseo.dashboard_store import ensure_dashboard_schema

    ensure_dashboard_schema(conn)
    ensure_tasks(conn)
    conn.commit()


def iter_schema_typed_columns(
    conn: Any,
) -> list[tuple[str, str, str]]:
    """Return ``(table, column, declared_type)`` for every user table column."""
    tables = [
        row[0]
        for row in execute(
            conn,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
        )
    ]
    out: list[tuple[str, str, str]] = []
    for table in tables:
        for _cid, name, decl, *_rest in execute(conn, f"PRAGMA table_info({table})"):
            out.append((table, name, decl or ""))
    return out


def iter_real_affinity_columns(
    conn: Any,
) -> list[tuple[str, str, str]]:
    """Columns whose SQLite affinity is REAL (IEEE-754 double)."""
    return [
        (table, name, decl)
        for table, name, decl in iter_schema_typed_columns(conn)
        if sqlite_column_affinity(decl) == "REAL"
    ]


def iter_numeric_affinity_columns(
    conn: Any,
) -> list[tuple[str, str, str]]:
    """NUMERIC-affinity columns (DECIMAL/NUMERIC/BOOLEAN/DATE/untyped)."""
    return [
        (table, name, decl)
        for table, name, decl in iter_schema_typed_columns(conn)
        if sqlite_column_affinity(decl) == "NUMERIC"
    ]


def real_affinity_columns_from_repo_schema() -> list[tuple[str, str, str]]:
    """Bootstrap a throwaway SQLite file and list REAL-affinity columns.

    Never opens the live catalog or any path under ``/home/box``.
    """
    saved = os.environ.pop("DATABASE_URL", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sqlite3"
            conn = connect_sqlite(path, wal_mode=False)
            try:
                bootstrap_sqlite_schema(conn)
                return iter_real_affinity_columns(conn)
            finally:
                conn.close()
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved
