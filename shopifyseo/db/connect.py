"""Unified database connection factory."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .backend import Backend, parse_database_url
from .compat import DictRow

BUSY_TIMEOUT_MS = 30000


def _configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    row_factory: bool = True,
    wal_mode: bool = True,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    text_factory: bool = True,
) -> sqlite3.Connection:
    """Apply standard SQLite configuration matching existing codebase."""
    if row_factory:
        conn.row_factory = sqlite3.Row
    if busy_timeout_ms:
        conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    if wal_mode:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    if text_factory:
        from ..sqlite_utf8 import configure_sqlite_text_decode
        configure_sqlite_text_decode(conn)
    return conn


def _make_postgres_row_factory():
    """Create a row factory for psycopg that produces DictRow objects."""
    def row_factory(cursor):
        if cursor.description is None:
            return None
        columns = tuple(col.name for col in cursor.description)

        def make_row(values):
            return DictRow.from_values(columns, tuple(values))
        return make_row
    return row_factory


def connect_sqlite(
    path: str | Path | None = None,
    *,
    timeout: float = 10.0,
    row_factory: bool = True,
    wal_mode: bool = True,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    text_factory: bool = True,
) -> sqlite3.Connection:
    """Open a SQLite connection with standard configuration."""
    if path is None:
        from ..shopify_catalog_sync import DEFAULT_DB_PATH
        path = DEFAULT_DB_PATH
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    return _configure_sqlite_connection(
        conn,
        row_factory=row_factory,
        wal_mode=wal_mode,
        busy_timeout_ms=busy_timeout_ms,
        text_factory=text_factory,
    )


def connect_postgres(url: str, *, autocommit: bool = False) -> Any:
    """Open a PostgreSQL connection via psycopg with DictRow factory."""
    try:
        import psycopg
    except ImportError as e:
        raise ImportError(
            "psycopg required for PostgreSQL. Install with: pip install 'psycopg[binary]'"
        ) from e
    conn = psycopg.connect(url, autocommit=autocommit)
    conn.row_factory = _make_postgres_row_factory()
    return conn


def connect(
    url: str | None = None,
    *,
    path: str | Path | None = None,
    timeout: float = 10.0,
    row_factory: bool = True,
    wal_mode: bool = True,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    text_factory: bool = True,
) -> Any:
    """Open a database connection based on DATABASE_URL or explicit URL.

    For PostgreSQL: url must be postgresql:// or postgres://
    For SQLite: url can be sqlite:///path, file:path, or bare path.
    If url is None, reads DATABASE_URL from environment.
    """
    backend, conn_str = parse_database_url(url)

    if backend == Backend.POSTGRES:
        return connect_postgres(conn_str)

    sqlite_path = conn_str if conn_str else path
    return connect_sqlite(
        sqlite_path,
        timeout=timeout,
        row_factory=row_factory,
        wal_mode=wal_mode,
        busy_timeout_ms=busy_timeout_ms,
        text_factory=text_factory,
    )
