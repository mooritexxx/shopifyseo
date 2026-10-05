"""Unified database connection factory.

Provides connect() and open_connection() that return the appropriate
connection type based on DATABASE_URL, with consistent configuration.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .backend import Backend, parse_database_url
from .compat import DictRow

if TYPE_CHECKING:
    pass


BUSY_TIMEOUT_MS = 30000


def _configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    row_factory: bool = True,
    wal_mode: bool = True,
    busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    text_factory: bool = True,
) -> sqlite3.Connection:
    """Apply standard SQLite configuration.

    Matches the existing configuration used across the codebase:
    - row_factory = sqlite3.Row (dict-like row access)
    - busy_timeout = 30000ms
    - journal_mode = WAL
    - synchronous = NORMAL
    - Custom text_factory for UTF-8 with replacement
    """
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
            return DictRow(dict(zip(columns, values)), columns)

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
    """Open a SQLite connection with standard configuration.

    If path is None, uses the default catalog path from environment.
    """
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


def connect_postgres(url: str, *, row_factory: bool = True) -> Any:
    """Open a PostgreSQL connection via psycopg.

    Configures DictRow row factory for sqlite3.Row compatibility.
    """
    try:
        import psycopg
    except ImportError as e:
        raise ImportError(
            "psycopg is required for PostgreSQL connections. "
            "Install it with: pip install 'psycopg[binary]'"
        ) from e

    conn = psycopg.connect(url, autocommit=False)
    if row_factory:
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

    If url is provided, it determines the backend:
    - postgresql:// or postgres://: PostgreSQL via psycopg
    - Otherwise: SQLite (path from url, or from `path` param, or default)

    If url is None, reads DATABASE_URL from environment.
    For SQLite, if no explicit path is given, uses the default catalog path.

    Returns a connection object with:
    - sqlite3.Row-compatible row factory (key/index access, dict(), .keys())
    - Standard configuration (WAL mode, busy timeout, etc. for SQLite)
    """
    backend, conn_str = parse_database_url(url)

    if backend == Backend.POSTGRES:
        return connect_postgres(conn_str, row_factory=row_factory)
    else:
        sqlite_path = conn_str if conn_str else path
        return connect_sqlite(
            sqlite_path,
            timeout=timeout,
            row_factory=row_factory,
            wal_mode=wal_mode,
            busy_timeout_ms=busy_timeout_ms,
            text_factory=text_factory,
        )


def get_backend_for_connection(conn: Any) -> Backend:
    """Determine the backend type for an existing connection."""
    if isinstance(conn, sqlite3.Connection):
        return Backend.SQLITE
    try:
        import psycopg
        if isinstance(conn, psycopg.Connection):
            return Backend.POSTGRES
    except ImportError:
        pass
    return Backend.SQLITE
