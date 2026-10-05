"""Database backend detection and configuration.

The backend is selected based on the DATABASE_URL environment variable:
- Unset or empty: SQLite (default path)
- file:// or .sqlite3 path: SQLite (explicit path)
- postgresql:// or postgres://: PostgreSQL via psycopg (v3)
"""
from __future__ import annotations

import os
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class Backend(Enum):
    SQLITE = "sqlite"
    POSTGRES = "postgres"


def parse_database_url(url: str | None = None) -> tuple[Backend, str]:
    """Parse DATABASE_URL and return (backend, connection_string).

    Returns (SQLITE, "") for unset/empty URLs (use default path).
    """
    if url is None:
        url = os.environ.get("DATABASE_URL", "")
    url = url.strip()
    if not url:
        return Backend.SQLITE, ""
    lower = url.lower()
    if lower.startswith(("postgresql://", "postgres://")):
        return Backend.POSTGRES, url
    if lower.startswith("file:") or lower.endswith(".sqlite3") or lower.endswith(".db"):
        return Backend.SQLITE, url
    if "/" not in url and not url.startswith(("postgresql", "postgres")):
        return Backend.SQLITE, url
    return Backend.SQLITE, url


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
