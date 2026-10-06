"""PostgreSQL production runtime compat (placeholders, types, executemany).

Applied only by ``connect_postgres``. SQLite connections are never wrapped.
Does not install rollback-on-error or autocommit — those stay testdb-only.

``tests/db_support.adapt_postgres_test_connection`` calls
``apply_postgres_runtime_compat`` so testdb and production cannot diverge on
the shared pieces.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .compat import _translate_placeholders
from .timestamps import rewrite_current_timestamp_for_postgres

_RUNTIME_FLAG = "_shopifyseo_pg_runtime"

# PostgreSQL INT8 / NUMERIC OIDs.
_INT8_OID = 20
_NUMERIC_OID = 1700

_RuntimeCursor: type | None = None
_BoolAsIntDumper: type | None = None
_NumericAsFloatLoader: type | None = None


def _bool_as_int_dumper() -> type:
    """Dump Python ``bool`` as integer 0/1 (SQLite INTEGER / PG BIGINT)."""
    global _BoolAsIntDumper
    if _BoolAsIntDumper is None:
        from psycopg.adapt import Dumper

        class BoolAsIntDumper(Dumper):
            oid = _INT8_OID

            def dump(self, obj: bool) -> bytes:
                return b"1" if obj else b"0"

        _BoolAsIntDumper = BoolAsIntDumper
    return _BoolAsIntDumper


def _numeric_as_float_loader() -> type:
    """Load PG NUMERIC (SUM/AVG of bigint) as float so json.dumps works."""
    global _NumericAsFloatLoader
    if _NumericAsFloatLoader is None:
        from psycopg.adapt import Loader

        class NumericAsFloatLoader(Loader):
            def load(self, data: Any) -> float | None:
                if data is None:
                    return None
                if isinstance(data, memoryview):
                    data = data.tobytes()
                if isinstance(data, bytes):
                    text = data.decode("ascii")
                else:
                    text = str(data)
                return float(Decimal(text))

        _NumericAsFloatLoader = NumericAsFloatLoader
    return _NumericAsFloatLoader


def is_postgres_runtime(conn: Any) -> bool:
    """True when ``apply_postgres_runtime_compat`` has been installed on ``conn``."""
    return bool(getattr(conn, _RUNTIME_FLAG, False))


def _rewrite_postgres_sql(query: str, *, params: Any = None) -> str:
    """CURRENT_TIMESTAMP rewrite then ``?`` → ``%s``.

    This is the only placeholder translator on a production PG connection.
    ``escape_percent`` follows psycopg: ``%`` is processed only when a params
    sequence is passed (including empty ``()`` / ``[]``).
    """
    query = rewrite_current_timestamp_for_postgres(query)
    return _translate_placeholders(
        query, to_postgres=True, escape_percent=params is not None
    )


def postgres_runtime_cursor_factory() -> type:
    """psycopg Cursor that rewrites CURRENT_TIMESTAMP and ``?`` placeholders."""
    global _RuntimeCursor
    if _RuntimeCursor is None:
        import psycopg

        class PostgresRuntimeCursor(psycopg.Cursor):
            def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
                if isinstance(query, str):
                    query = _rewrite_postgres_sql(query, params=params)
                if params is None and not kwargs:
                    return super().execute(query)
                return super().execute(query, params, **kwargs)

            def executemany(self, query: Any, params_seq: Any, **kwargs: Any) -> Any:
                if isinstance(query, str):
                    query = _rewrite_postgres_sql(query, params=params_seq)
                return super().executemany(query, params_seq, **kwargs)

        _RuntimeCursor = PostgresRuntimeCursor
    return _RuntimeCursor


def _connection_executemany(conn: Any, query: Any, params_seq: Any, **kwargs: Any) -> Any:
    """psycopg Connection has no executemany; Cursor does."""
    cur = conn.cursor()
    return cur.executemany(query, params_seq, **kwargs)


def apply_postgres_runtime_compat(conn: Any) -> Any:
    """Install production PG compat on a psycopg connection. Idempotent.

    Adds: bool→int dumper, NUMERIC→float loader, Connection.executemany,
    and the runtime cursor factory (if not already set). Does not change
    autocommit or install rollback-on-error.
    """
    if getattr(conn, _RUNTIME_FLAG, False):
        return conn

    factory = postgres_runtime_cursor_factory()
    if getattr(conn, "cursor_factory", None) is not factory:
        conn.cursor_factory = factory

    conn.adapters.register_dumper(bool, _bool_as_int_dumper())
    conn.adapters.register_loader("numeric", _numeric_as_float_loader())
    conn.adapters.register_loader(_NUMERIC_OID, _numeric_as_float_loader())

    if not callable(getattr(conn, "executemany", None)):
        # Bind as a method-like callable that uses this connection.
        def executemany(query: Any, params_seq: Any, **kwargs: Any) -> Any:
            return _connection_executemany(conn, query, params_seq, **kwargs)

        conn.executemany = executemany

    setattr(conn, _RUNTIME_FLAG, True)
    return conn
