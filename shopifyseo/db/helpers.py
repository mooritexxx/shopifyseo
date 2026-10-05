"""Database helper functions for portable operations."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Any, Generator

from .backend import Backend, get_backend
from .compat import translate_placeholders


def insert_returning_id(
    conn: Any,
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    id_column: str = "id",
    backend: Backend | None = None,
) -> int | None:
    """Execute an INSERT and return the inserted row's ID.

    On SQLite: uses cursor.lastrowid.
    On PostgreSQL: appends RETURNING and fetches the result.
    The SQL should NOT include RETURNING clause.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        sql = sql.rstrip().rstrip(";")
        sql = f"{sql} RETURNING {id_column}"
        sql = translate_placeholders(sql, to_postgres=True)
        cursor = conn.execute(sql, params)
        row = cursor.fetchone()
        return row[0] if row else None
    else:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


@contextmanager
def write_tx(conn: Any, *, backend: Backend | None = None) -> Generator[Any, None, None]:
    """Context manager for a write transaction.

    On SQLite: BEGIN IMMEDIATE to acquire write lock immediately.
    On PostgreSQL: Uses psycopg's transaction() context manager.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        with conn.transaction():
            yield conn
    else:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def table_exists(conn: Any, table: str, *, backend: Backend | None = None) -> bool:
    """Check if a table exists in the database."""
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def table_columns(conn: Any, table: str, *, backend: Backend | None = None) -> set[str]:
    """Get the set of column names for a table."""
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        ).fetchall()
        return {row[0] for row in rows}
    else:
        quoted = table.replace('"', '""')
        rows = conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
        return {row[1] if isinstance(row, tuple) else row["name"] for row in rows}
