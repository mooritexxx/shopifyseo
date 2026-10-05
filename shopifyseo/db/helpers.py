"""Database helper functions for portable operations."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Generator, Sequence

from .backend import Backend, get_backend
from .compat import _translate_placeholders


def _resolve_backend(backend: Backend | None) -> Backend:
    return get_backend() if backend is None else backend


def group_concat(
    expr: str,
    *,
    distinct: bool = False,
    separator: str = ",",
    backend: Backend | None = None,
) -> str:
    """SQL fragment that concatenates grouped strings on both backends.

    SQLite: ``GROUP_CONCAT`` (default separator ``,``).
    PostgreSQL: ``string_agg`` with the same separator.
    """
    if "'" in separator:
        raise ValueError("group_concat separator cannot contain a single quote")
    if _resolve_backend(backend) == Backend.POSTGRES:
        inner = f"DISTINCT {expr}" if distinct else expr
        return f"string_agg({inner}, '{separator}')"
    if distinct and separator == ",":
        return f"GROUP_CONCAT(DISTINCT {expr})"
    if distinct:
        return f"GROUP_CONCAT(DISTINCT {expr}, '{separator}')"
    if separator == ",":
        return f"GROUP_CONCAT({expr})"
    return f"GROUP_CONCAT({expr}, '{separator}')"


def like_ci(left: str, right: str, *, backend: Backend | None = None) -> str:
    """Case-insensitive LIKE. SQLite keeps ``LIKE`` (ASCII CI); Postgres uses ``ILIKE``."""
    op = "ILIKE" if _resolve_backend(backend) == Backend.POSTGRES else "LIKE"
    return f"{left} {op} {right}"


def order_ci(expr: str, *, backend: Backend | None = None) -> str:
    """Case-insensitive ORDER BY key. SQLite keeps ``COLLATE NOCASE``; Postgres uses ``LOWER()``."""
    if _resolve_backend(backend) == Backend.POSTGRES:
        return f"LOWER({expr})"
    return f"{expr} COLLATE NOCASE"


def order_inserted(*, id_column: str = "id", backend: Backend | None = None) -> str:
    """ORDER BY key that prefers insertion order.

    SQLite: ``rowid`` (monotonic; same tie-break as main).
    PostgreSQL: ``id_column`` (default ``id``). INTEGER / IDENTITY PKs match
    insertion order. TEXT uuid PKs (``rank_jobs.id``) have no monotonic
    insertion column without a schema migration — the uuid is then
    lexicographic, not last-inserted. Call sites must document that residual.
    """
    if _resolve_backend(backend) == Backend.POSTGRES:
        return id_column
    return "rowid"


def on_conflict_do_nothing(target: str | None = None) -> str:
    """Portable UPSERT ignore clause. ``target`` is the unique index/constraint column list."""
    if target:
        return f"ON CONFLICT({target}) DO NOTHING"
    return "ON CONFLICT DO NOTHING"


def on_conflict_do_update(target: str, columns: Sequence[str]) -> str:
    """Portable UPSERT update clause assigning each column from ``excluded``."""
    if not columns:
        raise ValueError("on_conflict_do_update requires at least one column")
    assignments = ", ".join(f"{column} = excluded.{column}" for column in columns)
    return f"ON CONFLICT({target}) DO UPDATE SET {assignments}"


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
        sql = _translate_placeholders(sql, to_postgres=True, escape_percent=True)
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
    """Check if a table exists (visible via search_path on Postgres).
    
    On PostgreSQL, filters by relkind='r' (regular table) and current_schema()
    to avoid matching views, sequences, or tables in other schemas.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute(
            """
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND c.relkind = 'r'
              AND (
                  n.nspname = current_schema()
                  OR starts_with(n.nspname, 'pg_temp')
              )
            """,
            (table,),
        ).fetchone()
        return row is not None
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


def table_ddl(conn: Any, table: str, *, backend: Backend | None = None) -> str | None:
    """Get the CREATE TABLE DDL for a table, or None if it doesn't exist.

    On SQLite: returns the sql column from sqlite_master.
    On PostgreSQL: reconstructs DDL from pg_catalog (simplified version).
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute("SELECT to_regclass(%s)::oid", (table,)).fetchone()
        if row is None or row[0] is None:
            return None
        oid = row[0]
        cols_rows = conn.execute(
            """
            SELECT attname, format_type(atttypid, atttypmod) AS dtype,
                   attnotnull, pg_get_expr(adbin, adrelid) AS default_expr
            FROM pg_attribute
            LEFT JOIN pg_attrdef ON adrelid = attrelid AND adnum = attnum
            WHERE attrelid = %s AND attnum > 0 AND NOT attisdropped
            ORDER BY attnum
            """,
            (oid,),
        ).fetchall()
        if not cols_rows:
            return None
        col_defs = []
        for r in cols_rows:
            col_def = f'"{r[0]}" {r[1]}'
            if r[2]:
                col_def += " NOT NULL"
            if r[3]:
                col_def += f" DEFAULT {r[3]}"
            col_defs.append(col_def)
        return f'CREATE TABLE "{table}" ({", ".join(col_defs)})'
    else:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row[0] if row else None


def index_exists(conn: Any, index_name: str, *, backend: Backend | None = None) -> bool:
    """Check if an index exists.
    
    On PostgreSQL, filters by relkind='i' (index) and current_schema()
    to avoid matching indexes in other schemas.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute(
            """
            SELECT 1 FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND c.relkind = 'i'
              AND (
                  n.nspname = current_schema()
                  OR starts_with(n.nspname, 'pg_temp')
              )
            """,
            (index_name,),
        ).fetchone()
        return row is not None
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
        ).fetchone()
        return row is not None


def foreign_keys_enabled(conn: Any, *, backend: Backend | None = None) -> bool:
    """Check if foreign key constraints are enabled.

    On PostgreSQL: always returns True (foreign keys are always enforced).
    On SQLite: checks PRAGMA foreign_keys setting.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        return True
    else:
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        return bool(row and row[0])


def set_foreign_keys(conn: Any, enabled: bool, *, backend: Backend | None = None) -> None:
    """Enable or disable foreign key constraints.

    On PostgreSQL: This is a no-op (use session_replication_role or
    ALTER TABLE ... DISABLE TRIGGER for bulk loads).
    On SQLite: Sets PRAGMA foreign_keys.
    """
    if backend is None:
        backend = get_backend()

    if backend != Backend.POSTGRES:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if enabled else 'OFF'}")


def journal_mode(conn: Any, *, backend: Backend | None = None) -> str:
    """Get the current journal/logging mode.

    On PostgreSQL: Returns the wal_level setting.
    On SQLite: Returns the journal_mode PRAGMA value.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute("SHOW wal_level").fetchone()
        return row[0] if row else "unknown"
    else:
        row = conn.execute("PRAGMA journal_mode").fetchone()
        return row[0] if row else "unknown"


def busy_timeout(conn: Any, *, backend: Backend | None = None) -> int:
    """Get the current busy/lock timeout in milliseconds.

    On PostgreSQL: Reads lock_timeout from pg_settings (unit is always ms),
    supporting values like '2s', '1min', '500ms', etc.
    On SQLite: Returns busy_timeout PRAGMA value.
    """
    if backend is None:
        backend = get_backend()

    if backend == Backend.POSTGRES:
        row = conn.execute(
            "SELECT setting FROM pg_settings WHERE name = 'lock_timeout'"
        ).fetchone()
        if not row:
            return 0
        return int(row[0])
    else:
        row = conn.execute("PRAGMA busy_timeout").fetchone()
        return row[0] if row else 0
