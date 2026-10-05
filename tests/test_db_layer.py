"""Tests for the shopifyseo.db database abstraction layer.

Tests placeholder translation, Row compatibility, backend detection,
exception mapping, and helpers.
"""
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from shopifyseo.db import (
    Backend,
    DictRow,
    IntegrityError,
    LockError,
    OperationalError,
    connect,
    connect_sqlite,
    get_backend,
    insert_returning_id,
    is_postgres,
    is_sqlite,
    map_sqlite_exception,
    parse_database_url,
    table_columns,
    table_exists,
    translate_placeholders,
    write_tx,
)


class TestParseDatabeUrl:
    """Tests for parse_database_url()."""

    def test_empty_returns_sqlite(self):
        backend, url = parse_database_url("")
        assert backend == Backend.SQLITE
        assert url == ""

    def test_none_reads_env_unset(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            backend, url = parse_database_url(None)
            assert backend == Backend.SQLITE
            assert url == ""

    def test_postgresql_url(self):
        url = "postgresql://user:pass@localhost:5432/db"
        backend, conn_str = parse_database_url(url)
        assert backend == Backend.POSTGRES
        assert conn_str == url

    def test_postgres_url(self):
        url = "postgres://user:pass@localhost:5432/db"
        backend, conn_str = parse_database_url(url)
        assert backend == Backend.POSTGRES
        assert conn_str == url

    def test_file_url(self):
        url = "file:/path/to/db.sqlite3"
        backend, conn_str = parse_database_url(url)
        assert backend == Backend.SQLITE
        assert conn_str == url

    def test_sqlite3_extension(self):
        url = "/path/to/mydb.sqlite3"
        backend, conn_str = parse_database_url(url)
        assert backend == Backend.SQLITE
        assert conn_str == url

    def test_db_extension(self):
        url = "/path/to/mydb.db"
        backend, conn_str = parse_database_url(url)
        assert backend == Backend.SQLITE
        assert conn_str == url

    def test_case_insensitive_postgres(self):
        backend, _ = parse_database_url("POSTGRESQL://user@host/db")
        assert backend == Backend.POSTGRES


class TestBackendHelpers:
    """Tests for is_sqlite() and is_postgres()."""

    def test_is_sqlite_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            assert is_sqlite() is True
            assert is_postgres() is False

    def test_is_postgres_when_set(self):
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/db"}):
            assert is_postgres() is True
            assert is_sqlite() is False


class TestTranslatePlaceholders:
    """Tests for translate_placeholders()."""

    def test_basic_replacement(self):
        sql = "SELECT * FROM users WHERE id = ? AND name = ?"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM users WHERE id = %s AND name = %s"

    def test_no_change_for_sqlite(self):
        sql = "SELECT * FROM users WHERE id = ?"
        result = translate_placeholders(sql, to_postgres=False)
        assert result == sql

    def test_preserves_single_quoted_strings(self):
        sql = "SELECT * FROM users WHERE name = '?' AND id = ?"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM users WHERE name = '?' AND id = %s"

    def test_preserves_double_quoted_strings(self):
        sql = 'SELECT * FROM users WHERE name = "?" AND id = ?'
        result = translate_placeholders(sql, to_postgres=True)
        assert result == 'SELECT * FROM users WHERE name = "?" AND id = %s'

    def test_escaped_quotes_in_string(self):
        sql = r"SELECT * FROM users WHERE name = 'don\'t?' AND id = ?"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == r"SELECT * FROM users WHERE name = 'don\'t?' AND id = %s"

    def test_double_question_mark(self):
        sql = "SELECT * FROM data WHERE pattern = ?? AND id = ?"
        result = translate_placeholders(sql, to_postgres=True)
        # ?? -> single ? (escaped placeholder)
        assert result == "SELECT * FROM data WHERE pattern = ? AND id = %s"

    def test_complex_query(self):
        sql = """
            INSERT INTO logs (message, data)
            VALUES (?, '{"key": "value?"}')
            WHERE type = ?
        """
        result = translate_placeholders(sql, to_postgres=True)
        assert "VALUES (%s, '{\"key\": \"value?\"}'" in result
        assert "WHERE type = %s" in result

    def test_multiple_strings_with_placeholders(self):
        sql = "SELECT '?' AS q1, ? AS val, '??' AS q2, ? AS val2"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT '?' AS q1, %s AS val, '??' AS q2, %s AS val2"

    def test_empty_sql(self):
        assert translate_placeholders("", to_postgres=True) == ""

    def test_no_placeholders(self):
        sql = "SELECT * FROM users"
        assert translate_placeholders(sql, to_postgres=True) == sql


class TestDictRow:
    """Tests for DictRow sqlite3.Row compatibility."""

    def test_key_access(self):
        row = DictRow({"id": 1, "name": "Alice"})
        assert row["id"] == 1
        assert row["name"] == "Alice"

    def test_index_access(self):
        row = DictRow({"id": 1, "name": "Alice"}, keys=["id", "name"])
        assert row[0] == 1
        assert row[1] == "Alice"

    def test_negative_index(self):
        row = DictRow({"id": 1, "name": "Alice"}, keys=["id", "name"])
        assert row[-1] == "Alice"
        assert row[-2] == 1

    def test_dict_conversion(self):
        data = {"id": 1, "name": "Alice"}
        row = DictRow(data)
        assert dict(row) == data

    def test_keys_method(self):
        row = DictRow({"id": 1, "name": "Alice"}, keys=["id", "name"])
        assert row.keys() == ("id", "name")

    def test_len(self):
        row = DictRow({"id": 1, "name": "Alice", "email": "a@b.com"})
        assert len(row) == 3

    def test_contains(self):
        row = DictRow({"id": 1, "name": "Alice"})
        assert "id" in row
        assert "name" in row
        assert "missing" not in row

    def test_iter(self):
        row = DictRow({"id": 1, "name": "Alice"}, keys=["id", "name"])
        values = list(row)
        assert values == [1, "Alice"]

    def test_get_with_default(self):
        row = DictRow({"id": 1})
        assert row.get("id") == 1
        assert row.get("missing") is None
        assert row.get("missing", "default") == "default"

    def test_index_out_of_range(self):
        row = DictRow({"id": 1}, keys=["id"])
        with pytest.raises(IndexError):
            _ = row[5]


class TestExceptionMapping:
    """Tests for exception mapping."""

    def test_map_integrity_error(self):
        exc = sqlite3.IntegrityError("UNIQUE constraint failed")
        mapped = map_sqlite_exception(exc)
        assert isinstance(mapped, IntegrityError)
        assert "UNIQUE constraint failed" in str(mapped)

    def test_map_locked_error(self):
        exc = sqlite3.OperationalError("database is locked")
        mapped = map_sqlite_exception(exc)
        assert isinstance(mapped, LockError)

    def test_map_busy_error(self):
        exc = sqlite3.OperationalError("database is busy")
        mapped = map_sqlite_exception(exc)
        assert isinstance(mapped, LockError)

    def test_map_general_operational_error(self):
        exc = sqlite3.OperationalError("no such table: foo")
        mapped = map_sqlite_exception(exc)
        assert isinstance(mapped, OperationalError)
        assert not isinstance(mapped, LockError)


class TestConnectSqlite:
    """Tests for SQLite connection with default configuration."""

    def test_connect_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                assert db_path.exists()
                assert isinstance(conn, sqlite3.Connection)
            finally:
                conn.close()

    def test_connect_sqlite_row_factory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                conn.execute("INSERT INTO t VALUES (1, 'Alice')")
                row = conn.execute("SELECT * FROM t").fetchone()
                # Should have sqlite3.Row interface
                assert row["id"] == 1
                assert row["name"] == "Alice"
                assert row[0] == 1
            finally:
                conn.close()

    def test_connect_busy_timeout_applied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                # PRAGMA returns 30000 (ms) if set correctly
                result = conn.execute("PRAGMA busy_timeout").fetchone()
                assert result[0] == 30000
            finally:
                conn.close()

    def test_connect_wal_mode_applied(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                result = conn.execute("PRAGMA journal_mode").fetchone()
                assert result[0].lower() == "wal"
            finally:
                conn.close()


class TestConnectPortable:
    """Tests for the portable connect() function."""

    def test_default_is_sqlite(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            with tempfile.TemporaryDirectory() as tmpdir:
                db_path = Path(tmpdir) / "test.sqlite3"
                conn = connect(path=db_path)
                try:
                    assert isinstance(conn, sqlite3.Connection)
                finally:
                    conn.close()

    def test_explicit_sqlite_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect(url=str(db_path))
            try:
                assert isinstance(conn, sqlite3.Connection)
            finally:
                conn.close()


class TestHelpers:
    """Tests for helper functions."""

    def test_table_exists_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                conn.execute("CREATE TABLE users (id INTEGER)")
                assert table_exists(conn, "users", backend=Backend.SQLITE) is True
            finally:
                conn.close()

    def test_table_exists_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                assert table_exists(conn, "nonexistent", backend=Backend.SQLITE) is False
            finally:
                conn.close()

    def test_table_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                conn.execute("CREATE TABLE users (id INTEGER, name TEXT, email TEXT)")
                cols = table_columns(conn, "users", backend=Backend.SQLITE)
                assert cols == {"id", "name", "email"}
            finally:
                conn.close()

    def test_insert_returning_id_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
                row_id = insert_returning_id(
                    conn,
                    "INSERT INTO users (name) VALUES (?)",
                    ("Alice",),
                    backend=Backend.SQLITE,
                )
                assert row_id == 1
                row_id2 = insert_returning_id(
                    conn,
                    "INSERT INTO users (name) VALUES (?)",
                    ("Bob",),
                    backend=Backend.SQLITE,
                )
                assert row_id2 == 2
            finally:
                conn.close()

    def test_write_tx_commits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path, wal_mode=False)
            try:
                conn.execute("CREATE TABLE t (id INTEGER)")
                conn.commit()
                with write_tx(conn, backend=Backend.SQLITE):
                    conn.execute("INSERT INTO t VALUES (1)")
                # Should be committed
                row = conn.execute("SELECT * FROM t").fetchone()
                assert row[0] == 1
            finally:
                conn.close()

    def test_write_tx_rollbacks_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path, wal_mode=False)
            try:
                conn.execute("CREATE TABLE t (id INTEGER)")
                conn.commit()
                try:
                    with write_tx(conn, backend=Backend.SQLITE):
                        conn.execute("INSERT INTO t VALUES (1)")
                        raise ValueError("test error")
                except ValueError:
                    pass
                # Should be rolled back
                row = conn.execute("SELECT * FROM t").fetchone()
                assert row is None
            finally:
                conn.close()


class TestSqlitePathUnchanged:
    """Tests that SQLite path matches exact same configuration as before."""

    def test_default_path_uses_same_pragmas(self):
        """Verify the SQLite path applies same PRAGMAs as backend/app/db.py."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                # Check busy_timeout
                result = conn.execute("PRAGMA busy_timeout").fetchone()
                assert result[0] == 30000, "busy_timeout should be 30000ms"

                # Check journal_mode
                result = conn.execute("PRAGMA journal_mode").fetchone()
                assert result[0].lower() == "wal", "journal_mode should be WAL"

                # Check synchronous
                result = conn.execute("PRAGMA synchronous").fetchone()
                # NORMAL = 1
                assert result[0] in (1, "normal"), "synchronous should be NORMAL"

                # Check row_factory
                conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                conn.execute("INSERT INTO t VALUES (1, 'test')")
                row = conn.execute("SELECT * FROM t").fetchone()
                assert row["id"] == 1, "row_factory should allow dict-like access"
                assert row[0] == 1, "row_factory should allow index access"
            finally:
                conn.close()


# Optional PostgreSQL tests - skipped unless TEST_DATABASE_URL is set
@pytest.fixture
def pg_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith(("postgresql://", "postgres://")):
        pytest.skip("TEST_DATABASE_URL not set to a PostgreSQL URL")
    return url


class TestPostgresOptional:
    """PostgreSQL tests - skipped unless TEST_DATABASE_URL is set."""

    def test_parse_test_database_url(self, pg_url):
        backend, conn_str = parse_database_url(pg_url)
        assert backend == Backend.POSTGRES
        assert conn_str == pg_url

    def test_connect_postgres(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            # Should be able to execute a simple query
            row = conn.execute("SELECT 1 AS val").fetchone()
            assert row["val"] == 1
            assert row[0] == 1
        finally:
            conn.close()

    def test_translate_placeholders_for_postgres(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            sql = translate_placeholders("SELECT %s AS val", to_postgres=True)
            row = conn.execute(sql, (42,)).fetchone()
            assert row[0] == 42
        finally:
            conn.close()

    def test_table_exists_postgres(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            # Should not raise, even if table doesn't exist
            exists = table_exists(conn, "nonexistent_table_xyz", backend=Backend.POSTGRES)
            assert exists is False
        finally:
            conn.close()
