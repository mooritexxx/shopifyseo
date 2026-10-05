"""Common database exceptions mapped across backends.

Maps SQLite and psycopg exceptions to a common hierarchy so callers can
catch portable error types regardless of which backend is active.
"""
from __future__ import annotations


class DatabaseError(Exception):
    """Base class for all database errors."""

    pass


class IntegrityError(DatabaseError):
    """Constraint violation (unique, foreign key, check, etc.)."""

    pass


class OperationalError(DatabaseError):
    """Database operational error (connection failed, locked, syntax, etc.)."""

    pass


class LockError(OperationalError):
    """Database is locked or busy (SQLite SQLITE_BUSY, PG deadlock/lock timeout)."""

    pass


class ProgrammingError(DatabaseError):
    """Programming error (invalid SQL, wrong param count, etc.)."""

    pass


def map_sqlite_exception(exc: Exception) -> Exception:
    """Map a sqlite3 exception to the common exception hierarchy."""
    import sqlite3

    if isinstance(exc, sqlite3.IntegrityError):
        return IntegrityError(str(exc))
    if isinstance(exc, sqlite3.OperationalError):
        msg = str(exc).lower()
        if "database is locked" in msg or "database is busy" in msg:
            return LockError(str(exc))
        return OperationalError(str(exc))
    if isinstance(exc, sqlite3.ProgrammingError):
        return ProgrammingError(str(exc))
    if isinstance(exc, sqlite3.DatabaseError):
        return DatabaseError(str(exc))
    return exc


def map_psycopg_exception(exc: Exception) -> Exception:
    """Map a psycopg exception to the common exception hierarchy."""
    try:
        import psycopg
    except ImportError:
        return exc

    if isinstance(exc, psycopg.IntegrityError):
        return IntegrityError(str(exc))
    if isinstance(exc, psycopg.OperationalError):
        msg = str(exc).lower()
        sqlstate = getattr(exc, "sqlstate", None) or ""
        if sqlstate in ("40001", "40P01", "55P03") or "deadlock" in msg or "lock" in msg:
            return LockError(str(exc))
        return OperationalError(str(exc))
    if isinstance(exc, psycopg.ProgrammingError):
        return ProgrammingError(str(exc))
    if isinstance(exc, psycopg.DatabaseError):
        return DatabaseError(str(exc))
    return exc


def map_exception(exc: Exception, backend: str = "sqlite") -> Exception:
    """Map a backend-specific exception to the common hierarchy."""
    if backend == "postgres":
        return map_psycopg_exception(exc)
    return map_sqlite_exception(exc)
