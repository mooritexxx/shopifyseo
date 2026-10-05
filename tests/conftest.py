"""Shared pytest fixtures. Dual-backend DB fixtures are plan 7a.

See ``tests/README.md`` and ``tests/db_support.py``.
"""
from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Callable

import pytest

from shopifyseo.db import Backend
from db_support import TestDatabase, make_testdb, postgres_test_url, resolve_backend


def pytest_report_header(config: pytest.Config) -> list[str]:
    if postgres_test_url():
        return ["shopifyseo test database: PostgreSQL (TEST_DATABASE_URL)"]
    return ["shopifyseo test database: SQLite (TEST_DATABASE_URL unset)"]


@pytest.fixture(scope="session")
def db_backend() -> Backend:
    """SQLite unless ``TEST_DATABASE_URL`` is a ``postgresql://`` / ``postgres://`` URL."""
    return resolve_backend()


@pytest.fixture
def testdb(tmp_path) -> Iterator[TestDatabase]:
    """Per-test isolated database (SQLite temp file or Postgres schema)."""
    with make_testdb(tmp_path) as db:
        yield db


@pytest.fixture(scope="module")
def testdb_module(tmp_path_factory) -> Iterator[TestDatabase]:
    """Module-scoped isolated database for tests that share a schema/file."""
    tmp = tmp_path_factory.mktemp("db")
    with make_testdb(tmp) as db:
        yield db


@pytest.fixture
def db_conn(testdb: TestDatabase) -> Iterator[Any]:
    """Per-test connection via ``shopifyseo.db.get_connection``. Closed on teardown."""
    conn = testdb.connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="module")
def db_conn_module(testdb_module: TestDatabase) -> Iterator[Any]:
    """Module-scoped connection via ``shopifyseo.db.get_connection``."""
    conn = testdb_module.connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def db_connect(testdb: TestDatabase) -> Iterator[Callable[..., Any]]:
    """Factory: extra connections to the same isolated test database.

    All connections opened through the factory are closed after the test.
    """
    opened: list[Any] = []

    def _connect(**kwargs: Any) -> Any:
        conn = testdb.connect(**kwargs)
        opened.append(conn)
        return conn

    try:
        yield _connect
    finally:
        for conn in opened:
            try:
                conn.close()
            except Exception:
                pass


@pytest.fixture
def pg_url() -> str:
    """Postgres-only URL. Skips unless ``TEST_DATABASE_URL`` is a Postgres URL.

    Existing ``tests/test_db_layer.py`` Postgres tests use this (public schema,
    not the isolated ``testdb`` schema) so two-connection visibility checks
    keep working without a ``search_path`` handshake.
    """
    url = postgres_test_url()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set to a PostgreSQL URL")
    return url


@pytest.fixture
def pg_conn(pg_url: str) -> Iterator[Any]:
    """Postgres-only connection on the public schema via production ``get_connection``."""
    from shopifyseo.db import get_connection

    conn = get_connection(url=pg_url)
    try:
        yield conn
    finally:
        conn.close()
