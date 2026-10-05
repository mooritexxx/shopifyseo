"""Database helper functions for portable operations.

Provides:
- insert_returning_id: Get inserted row ID portably (RETURNING vs lastrowid)
- write_tx: Write transaction context manager (BEGIN IMMEDIATE on SQLite, BEGIN on PG)
- table_exists: Check if a table exists
- table_columns: Get column names for a table
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Generator

from .backend import Backend, get_backend
from .compat import translate_placeholders

if TYPE_CHECKING:
    pass


def insert_returning_id(
    conn: Any,
    sql: str,
    params: tuple[Any, ...] = (),
    *,
    id_column: str = "id",
    backend: Backend | None = None,
) -> int | None:
    """Execute an INSERT and return the inserted row's ID.

    On SQLite, uses cursor.lastrowid.
    On PostgreSQL, appends RETURNING id_column and fetches the result.

    The SQL should NOT include RETURNING clause; this function adds it for PG.
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

    On SQLite: BEGIN IMMEDIATE to acquire write lock immediately and avoid
               SQLITE_BUSY on commit.
    On PostgreSQL: Standard BEGIN (PG handles locking differently).

    Usage:
        with write_tx(conn) as txn:
            txn.execute("INSERT ...")
            txn.execute("UPDATE ...")
        # Auto-commits on exit, rollback on exception
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        conn.execute("BEGIN")
    else:
        conn.execute("BEGIN IMMEDIATE")

    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def table_exists(conn: Any, table: str, *, backend: Backend | None = None) -> bool:
    """Check if a table exists in the database.

    On SQLite: Queries sqlite_master.
    On PostgreSQL: Queries information_schema.tables.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        sql = """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = %s
        """
        row = conn.execute(sql, (table,)).fetchone()
    else:
        sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?"
        row = conn.execute(sql, (table,)).fetchone()

    return row is not None


def table_columns(conn: Any, table: str, *, backend: Backend | None = None) -> set[str]:
    """Get the set of column names for a table.

    On SQLite: Uses PRAGMA table_info.
    On PostgreSQL: Queries information_schema.columns.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        sql = """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
        """
        rows = conn.execute(sql, (table,)).fetchall()
        return {row[0] for row in rows}
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {row[1] if isinstance(row, tuple) else row["name"] for row in rows}
