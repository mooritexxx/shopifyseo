"""Common database exceptions mapped across backends."""
from __future__ import annotations

import sqlite3
from typing import Final

# PostgreSQL SQLSTATEs retried as lock/contention (see run_with_db_lock_retry).
PG_LOCK_SQLSTATES: Final[frozenset[str]] = frozenset({
    "40001",  # serialization_failure
    "40P01",  # deadlock_detected
    "55P03",  # lock_not_available
})

# Missing-table on Postgres (UndefinedTable). SQLite raises OperationalError
# for the same condition ("no such table"), so classify 42P01 as operational.
PG_UNDEFINED_TABLE_SQLSTATE: Final[str] = "42P01"

_INTEGRITY_NAMES: Final[frozenset[str]] = frozenset({
    "IntegrityError",
    "UniqueViolation",
    "ForeignKeyViolation",
    "CheckViolation",
    "NotNullViolation",
    "ExclusionViolation",
})

_LOCK_NAMES: Final[frozenset[str]] = frozenset({
    "LockError",
    "DeadlockDetected",
    "SerializationFailure",
    "LockNotAvailable",
})

_OPERATIONAL_NAMES: Final[frozenset[str]] = frozenset({
    "OperationalError",
    "UndefinedTable",
    "QueryCanceled",
})

_PROGRAMMING_NAMES: Final[frozenset[str]] = frozenset({
    "ProgrammingError",
    "SyntaxError",
    "UndefinedColumn",
    "UndefinedFunction",
    "UndefinedObject",
})


class DatabaseError(Exception):
    """Base class for all database errors."""
    pass


class IntegrityError(DatabaseError):
    """Constraint violation (unique, foreign key, check, etc.)."""
    pass


class OperationalError(DatabaseError):
    """Database operational error (connection failed, syntax, etc.)."""
    pass


class LockError(OperationalError):
    """Database is locked or busy."""
    pass


class ProgrammingError(DatabaseError):
    """Programming error (invalid SQL, wrong param count, etc.)."""
    pass


def _sqlstate(exc: BaseException) -> str | None:
    for obj in (exc, getattr(exc, "__cause__", None)):
        if obj is None:
            continue
        state = getattr(obj, "sqlstate", None)
        if state:
            return str(state)
    return None


def _is_sqlite_lock_message(message: str) -> bool:
    # Match the existing sqlite_retry predicate exactly so SQLite retry
    # behaviour stays identical to main when DATABASE_URL is unset.
    return "database is locked" in message.lower()


def _classify(exc: BaseException) -> type[DatabaseError] | None:
    """Return the mapped exception class, or None if ``exc`` is not a DB error."""
    if isinstance(exc, DatabaseError):
        return type(exc)

    if isinstance(exc, sqlite3.IntegrityError):
        return IntegrityError
    if isinstance(exc, sqlite3.OperationalError):
        return LockError if _is_sqlite_lock_message(str(exc)) else OperationalError
    if isinstance(exc, sqlite3.ProgrammingError):
        return ProgrammingError
    if isinstance(exc, sqlite3.DatabaseError):
        return DatabaseError

    state = _sqlstate(exc)
    if state in PG_LOCK_SQLSTATES:
        return LockError
    if state == PG_UNDEFINED_TABLE_SQLSTATE:
        return OperationalError
    if state and state.startswith("23"):
        return IntegrityError

    name = type(exc).__name__
    if name in _LOCK_NAMES:
        return LockError
    if name in _INTEGRITY_NAMES:
        return IntegrityError
    if name in _OPERATIONAL_NAMES:
        return OperationalError
    if name in _PROGRAMMING_NAMES:
        return ProgrammingError

    module = getattr(type(exc), "__module__", "") or ""
    if "psycopg" in module and name.endswith("Error"):
        return DatabaseError
    return None


def map_exception(exc: BaseException) -> BaseException:
    """Map a driver exception onto the common hierarchy.

    Already-mapped errors and non-database exceptions are returned unchanged.
    The original is not raised; callers that need a raise should use
    ``raise map_exception(exc) from exc``.
    """
    cls = _classify(exc)
    if cls is None or isinstance(exc, DatabaseError):
        return exc
    mapped = cls(str(exc))
    if hasattr(exc, "sqlstate"):
        mapped.sqlstate = exc.sqlstate  # type: ignore[attr-defined]
    return mapped


def is_integrity_error(exc: BaseException) -> bool:
    """True for mapped IntegrityError and driver unique/FK/check failures."""
    return _classify(exc) is IntegrityError


def is_lock_error(exc: BaseException) -> bool:
    """True for LockError, SQLite 'database is locked', and PG lock SQLSTATEs."""
    if isinstance(exc, LockError):
        return True
    if isinstance(exc, sqlite3.OperationalError) and _is_sqlite_lock_message(str(exc)):
        return True
    if _classify(exc) is LockError:
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return is_lock_error(cause)
    return False


def is_operational_error(exc: BaseException) -> bool:
    """True for mapped OperationalError (including LockError) and driver equivalents."""
    cls = _classify(exc)
    return cls is not None and issubclass(cls, OperationalError)
