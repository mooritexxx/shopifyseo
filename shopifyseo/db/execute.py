"""Execute wrapper for unified query execution across SQLite and PostgreSQL.

The wrapper ensures that:
1. Placeholders are translated from ? to %s for PostgreSQL
2. % literals are escaped when params are provided (even empty () or [])
3. get_connection() is the single entry point for app connection sites
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Sequence

from .backend import Backend, get_backend, parse_database_url
from .compat import _translate_placeholders


def _translate_sql(sql: str, params: Sequence[Any] | None, backend: Backend) -> str:
    """Translate SQL for the target backend with correct escape_percent handling.

    The key insight: psycopg processes % whenever a params sequence is passed,
    even if that sequence is empty () or []. So we must escape % to %% when
    params is not None, regardless of whether the SQL has ? placeholders.
    """
    if backend != Backend.POSTGRES:
        return sql
    return _translate_placeholders(sql, to_postgres=True, escape_percent=params is not None)


def execute(
    conn: Any,
    sql: str,
    params: Sequence[Any] | None = None,
    *,
    backend: Backend | None = None,
) -> Any:
    """Execute a query on the connection with proper placeholder and % handling.

    Args:
        conn: Database connection (sqlite3.Connection or psycopg connection)
        sql: SQL query with ? placeholders
        params: Query parameters. None means no params tuple is passed to execute().
                An empty tuple () or list [] IS passed (and triggers % escaping on Postgres).
        backend: Override backend detection (for testing)

    Returns:
        Cursor object from the execution
    """
    if backend is None:
        backend = get_backend()

    translated_sql = _translate_sql(sql, params, backend)

    if params is None:
        return conn.execute(translated_sql)
    return conn.execute(translated_sql, params)


def executemany(
    conn: Any,
    sql: str,
    params_seq: Sequence[Sequence[Any]],
    *,
    backend: Backend | None = None,
) -> Any:
    """Execute a query multiple times with different parameter sets.

    Args:
        conn: Database connection
        sql: SQL query with ? placeholders
        params_seq: Sequence of parameter tuples
        backend: Override backend detection

    Returns:
        Cursor object from the execution
    """
    if backend is None:
        backend = get_backend()

    translated_sql = _translate_sql(sql, (), backend) if backend == Backend.POSTGRES else sql

    if backend == Backend.POSTGRES:
        cursor = conn.cursor()
        cursor.executemany(translated_sql, params_seq)
        return cursor
    return conn.executemany(translated_sql, params_seq)


def get_connection(
    *,
    path: str | Path | None = None,
    url: str | None = None,
    timeout: float = 10.0,
    row_factory: bool = True,
    wal_mode: bool = True,
    busy_timeout_ms: int | None = None,
    text_factory: bool = True,
    autocommit: bool = False,
    create_parents: bool = True,
) -> Any:
    """Get a database connection based on DATABASE_URL or default SQLite.

    This is the single entry point for all connection sites. It routes to:
    - PostgreSQL when DATABASE_URL is set to a postgres:// URL
    - SQLite otherwise (default path or explicit path)

    Args:
        path: Explicit SQLite path (ignored when DATABASE_URL is PostgreSQL)
        url: Explicit database URL (overrides DATABASE_URL env var)
        timeout: SQLite connection timeout (seconds; also seeds PRAGMA busy_timeout)
        row_factory: Enable dict-like row access
        wal_mode: Enable WAL + synchronous=NORMAL on SQLite
        busy_timeout_ms: SQLite busy timeout. None uses the connect-module
            default (30000). 0 skips the PRAGMA so sqlite3.connect(timeout=)
            remains the observable busy_timeout.
        text_factory: Enable UTF-8 text decoding on SQLite
        autocommit: Enable autocommit (PostgreSQL autocommit; SQLite isolation_level=None)
        create_parents: Create the SQLite file's parent directory (sites that
            never mkdir must pass False)

    Returns:
        sqlite3.Connection for SQLite, psycopg connection for PostgreSQL
    """
    from .connect import BUSY_TIMEOUT_MS, connect_postgres, connect_sqlite

    if busy_timeout_ms is None:
        busy_timeout_ms = BUSY_TIMEOUT_MS

    backend, conn_str = parse_database_url(url)

    if backend == Backend.POSTGRES:
        return connect_postgres(conn_str, autocommit=autocommit)

    sqlite_path = conn_str if conn_str else path
    conn = connect_sqlite(
        sqlite_path,
        timeout=timeout,
        row_factory=row_factory,
        wal_mode=wal_mode,
        busy_timeout_ms=busy_timeout_ms,
        text_factory=text_factory,
        create_parents=create_parents,
    )
    if autocommit:
        conn.isolation_level = None
    return conn
