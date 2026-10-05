"""Database backend detection and configuration."""
from __future__ import annotations

import os
import sqlite3
from enum import Enum
from typing import Any


class Backend(Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


class InvalidDatabaseURL(ValueError):
    """Raised when DATABASE_URL has an unsupported or malformed scheme."""
    pass


def parse_database_url(url: str | None = None) -> tuple[Backend, str]:
    """Parse DATABASE_URL and return (backend, connection_string).

    Supported formats:
    - Unset, empty, or whitespace: SQLite with default path (returns ("", ""))
    - postgresql://... or postgres://...: PostgreSQL
    - sqlite:///path or file:path: SQLite with explicit path
    - Bare filesystem path (contains / or ends with .sqlite3/.db): SQLite

    Raises InvalidDatabaseURL for unknown schemes.
    """
    if url is None:
        url = os.environ.get("DATABASE_URL", "")
    url = url.strip()
    if not url:
        return Backend.SQLITE, ""

    lower = url.lower()

    if lower.startswith("postgresql://") or lower.startswith("postgres://"):
        return Backend.POSTGRES, url

    if lower.startswith("sqlite:///"):
        path = url[10:]
        if not path:
            raise InvalidDatabaseURL("sqlite:/// URL has no path")
        return Backend.SQLITE, path

    if lower.startswith("sqlite:"):
        raise InvalidDatabaseURL(
            f"Invalid sqlite URL format: {url!r}. Use sqlite:///path for absolute "
            "or sqlite:///./path for relative paths."
        )

    if lower.startswith("file:"):
        path = url[5:]
        if not path:
            raise InvalidDatabaseURL("file: URL has no path")
        return Backend.SQLITE, path

    if "://" in url:
        scheme = url.split("://", 1)[0]
        raise InvalidDatabaseURL(f"Unsupported database scheme: {scheme!r}")

    if "/" in url or url.endswith((".sqlite3", ".db")):
        return Backend.SQLITE, url

    raise InvalidDatabaseURL(f"Cannot parse DATABASE_URL: {url!r}")


def get_backend() -> Backend:
    """Return the active backend based on DATABASE_URL."""
    backend, _ = parse_database_url()
    return backend


def backend_for_connection(conn: Any, *, backend: Backend | None = None) -> Backend:
    """Return the backend for ``conn``, ignoring ``DATABASE_URL`` when possible.

    Plan 7a keeps ``DATABASE_URL`` unset in the Postgres CI job so leftover
    ``sqlite3.connect`` tests still open temp files. Converted tests open a
    real Postgres connection via ``TEST_DATABASE_URL`` / ``testdb``; helpers
    that only called ``get_backend()`` would then take the SQLite path
    (PRAGMA, ``lastrowid``) on a psycopg connection. Prefer the live
    connection type, then ``get_backend()``.
    """
    if backend is not None:
        return backend
    if isinstance(conn, sqlite3.Connection):
        return Backend.SQLITE
    module = type(conn).__module__
    if module == "psycopg" or module.startswith("psycopg."):
        return Backend.POSTGRES
    return get_backend()


def is_postgres() -> bool:
    """True if PostgreSQL is the active backend."""
    return get_backend() == Backend.POSTGRES


def is_sqlite() -> bool:
    """True if SQLite is the active backend."""
    return get_backend() == Backend.SQLITE
