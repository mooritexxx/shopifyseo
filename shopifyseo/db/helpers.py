"""Database helper functions for portable operations."""
from __future__ import annotations

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
        cursor = conn.execute(sql, params if params else ())
        row = cursor.fetchone()
        return row[0] if row else None
    else:
        cursor = conn.execute(sql, params)
        return cursor.lastrowid


@contextmanager
def write_tx(conn: Any, *, backend: Backend | None = None) -> Generator[Any, None, None]:
    """Context manager for a write transaction.

    On SQLite: BEGIN IMMEDIATE to acquire write lock immediately.
    On PostgreSQL: Commits any pending transaction first, then uses an explicit
    transaction block. This matches SQLite behavior where BEGIN raises if a
    transaction is already open.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        if conn.info.transaction_status != conn.info.transaction_status.__class__.IDLE:
            conn.commit()
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
    """Check if a table exists (visible via search_path on Postgres)."""
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            (table,),
        ).fetchone()
        return row[0] if row else False
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None


def table_columns(conn: Any, table: str, *, backend: Backend | None = None) -> set[str]:
    """Get the set of column names for a table (visible via search_path on Postgres)."""
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute("SELECT to_regclass(%s)::oid", (table,)).fetchone()
        if row is None or row[0] is None:
            return set()
        oid = row[0]
        rows = conn.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped",
            (oid,),
        ).fetchall()
        return {r[0] for r in rows}
    else:
        quoted = table.replace('"', '""')
        rows = conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
        return {row[1] if isinstance(row, tuple) else row["name"] for row in rows}
