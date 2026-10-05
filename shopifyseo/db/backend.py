"""Database backend detection and configuration."""
from __future__ import annotations

import os
from enum import Enum


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


def is_postgres() -> bool:
    """True if PostgreSQL is the active backend."""
    return get_backend() == Backend.POSTGRES


def is_sqlite() -> bool:
    """True if SQLite is the active backend."""
    return get_backend() == Backend.SQLITE
