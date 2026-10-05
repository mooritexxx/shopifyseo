"""Dual-backend test database helpers (PostgreSQL plan 7a).

``TEST_DATABASE_URL`` selects the backend for the shared fixtures in
``tests/conftest.py``:

- Unset / empty / non-postgres: SQLite temp file (today's smoke default).
- ``postgresql://`` or ``postgres://``: open via ``shopifyseo.db.get_connection``
  (same path as production ``connect()``), so session ``timezone=UTC`` and
  CURRENT_TIMESTAMP → naive UTC text from plan 6 still apply.

Plan 7b–d should migrate ``sqlite3.connect`` call sites onto ``testdb`` /
``db_conn`` rather than adding a second fixture stack.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shopifyseo.db import Backend, get_connection

_PG_PREFIXES = ("postgresql://", "postgres://")


def postgres_test_url() -> str | None:
    """Return ``TEST_DATABASE_URL`` when it is a Postgres URL, else ``None``."""
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if url.lower().startswith(_PG_PREFIXES):
        return url
    return None


def resolve_backend() -> Backend:
    """Backend selected by ``TEST_DATABASE_URL`` (SQLite unless Postgres URL)."""
    return Backend.POSTGRES if postgres_test_url() else Backend.SQLITE


def _pg_ident(name: str) -> str:
    """Quote a known-safe SQL identifier (hex schema names from this module)."""
    if not name.isidentifier() or not name.isascii():
        raise ValueError(f"refusing to use {name!r} as a Postgres identifier")
    return name


@dataclass
class TestDatabase:
    """Isolated database for one test (or one module, if the fixture is module-scoped)."""

    backend: Backend
    url: str | None = None
    path: Path | None = None
    schema: str | None = None

    @property
    def is_postgres(self) -> bool:
        return self.backend == Backend.POSTGRES

    def connect(self, **kwargs: Any) -> Any:
        """Open a connection through ``shopifyseo.db.get_connection``.

        On Postgres, sets ``search_path`` to this test's schema so DDL does not
        leak into ``public``. Session ``timezone=UTC`` comes from the production
        connect path (libpq options), not from this SET.
        """
        if self.backend == Backend.POSTGRES:
            if not self.url:
                raise RuntimeError("postgres TestDatabase is missing url")
            conn = get_connection(url=self.url, **kwargs)
            if self.schema:
                ident = _pg_ident(self.schema)
                conn.execute(f"SET search_path TO {ident}, public")
            return conn
        path = kwargs.pop("path", None) or self.path
        return get_connection(path=path, **kwargs)


def _create_postgres_schema(url: str) -> str:
    schema = f"t{uuid.uuid4().hex}"
    admin = get_connection(url=url)
    try:
        admin.execute(f"CREATE SCHEMA {_pg_ident(schema)}")
        admin.commit()
    finally:
        admin.close()
    return schema


def _drop_postgres_schema(url: str, schema: str) -> None:
    admin = get_connection(url=url)
    try:
        admin.execute(f"DROP SCHEMA IF EXISTS {_pg_ident(schema)} CASCADE")
        admin.commit()
    finally:
        admin.close()


@contextmanager
def make_testdb(tmp_path: Path) -> Iterator[TestDatabase]:
    """Yield an isolated ``TestDatabase`` and tear it down.

    SQLite: one temp file under ``tmp_path``. Postgres: a unique schema in the
    database named by ``TEST_DATABASE_URL`` (CI service or local port 5433).
    """
    url = postgres_test_url()
    if url:
        schema = _create_postgres_schema(url)
        db = TestDatabase(backend=Backend.POSTGRES, url=url, schema=schema)
        try:
            yield db
        finally:
            _drop_postgres_schema(url, schema)
        return

    path = tmp_path / "test.sqlite3"
    yield TestDatabase(backend=Backend.SQLITE, path=path)
