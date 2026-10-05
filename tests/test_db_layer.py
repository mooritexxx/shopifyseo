"""Tests for the shopifyseo.db database abstraction layer."""
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from unittest import mock

import pytest

from shopifyseo.db import (
    Backend,
    DictRow,
    InvalidDatabaseURL,
    connect,
    connect_sqlite,
    get_backend,
    insert_returning_id,
    is_postgres,
    is_sqlite,
    parse_database_url,
    table_columns,
    table_exists,
    translate_placeholders,
    write_tx,
    BUSY_TIMEOUT_MS,
)


class TestParseDatabaseUrl:
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

    def test_sqlite_triple_slash_absolute(self):
        backend, path = parse_database_url("sqlite:////abs/path.db")
        assert backend == Backend.SQLITE
        assert path == "/abs/path.db"

    def test_sqlite_triple_slash_relative(self):
        backend, path = parse_database_url("sqlite:///./relative.db")
        assert backend == Backend.SQLITE
        assert path == "./relative.db"

    def test_file_url(self):
        backend, path = parse_database_url("file:/path/to/db.sqlite3")
        assert backend == Backend.SQLITE
        assert path == "/path/to/db.sqlite3"

    def test_bare_path_with_slash(self):
        backend, path = parse_database_url("/path/to/mydb.sqlite3")
        assert backend == Backend.SQLITE
        assert path == "/path/to/mydb.sqlite3"

    def test_bare_path_sqlite3_extension(self):
        backend, path = parse_database_url("mydb.sqlite3")
        assert backend == Backend.SQLITE
        assert path == "mydb.sqlite3"

    def test_case_insensitive_postgres(self):
        backend, _ = parse_database_url("POSTGRESQL://user@host/db")
        assert backend == Backend.POSTGRES

    def test_rejects_mysql(self):
        with pytest.raises(InvalidDatabaseURL, match="Unsupported.*mysql"):
            parse_database_url("mysql://user@host/db")

    def test_rejects_bare_postgresql(self):
        with pytest.raises(InvalidDatabaseURL, match="Cannot parse"):
            parse_database_url("postgresql")

    def test_rejects_postgresql_plus_driver(self):
        with pytest.raises(InvalidDatabaseURL, match="Unsupported.*postgresql\\+psycopg"):
            parse_database_url("postgresql+psycopg://user@host/db")

    def test_rejects_sqlite_without_triple_slash(self):
        with pytest.raises(InvalidDatabaseURL, match="Invalid sqlite URL"):
            parse_database_url("sqlite:relative.db")

    def test_rejects_sqlite_triple_slash_empty(self):
        with pytest.raises(InvalidDatabaseURL, match="no path"):
            parse_database_url("sqlite:///")

    def test_rejects_unknown_scheme(self):
        with pytest.raises(InvalidDatabaseURL, match="Unsupported.*oracle"):
            parse_database_url("oracle://user@host/db")


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

    def test_preserves_double_quoted_identifiers(self):
        sql = 'SELECT "?" AS col, ? AS val'
        result = translate_placeholders(sql, to_postgres=True)
        assert result == 'SELECT "?" AS col, %s AS val'

    def test_double_question_mark(self):
        sql = "SELECT * FROM data WHERE payload ?? 'key' AND id = ?"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM data WHERE payload ? 'key' AND id = %s"

    def test_escapes_percent_for_like(self):
        sql = "SELECT * FROM t WHERE name LIKE 'a%' AND id = ?"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM t WHERE name LIKE 'a%%' AND id = %s"

    def test_escapes_percent_for_modulo(self):
        sql = "SELECT 7 % 3 AS r, ? AS v"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT 7 %% 3 AS r, %s AS v"

    def test_preserves_single_line_comment(self):
        sql = "SELECT ? AS b -- why?"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT %s AS b -- why?"

    def test_preserves_multi_line_comment(self):
        sql = "SELECT /* is it? */ ? AS b"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT /* is it? */ %s AS b"

    def test_backslash_not_escape_in_standard_string(self):
        sql = r"SELECT 'a\', ? AS b, 'c'"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == r"SELECT 'a\', %s AS b, 'c'"

    def test_e_string_with_backslash_escape(self):
        sql = r"SELECT E'a\'b', ? AS val"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == r"SELECT E'a\'b', %s AS val"

    def test_escaped_quotes_in_string(self):
        sql = "SELECT 'don''t?', ? AS val"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT 'don''t?', %s AS val"

    def test_mixed_like_patterns(self):
        sql = "SELECT * FROM t WHERE name LIKE 'how%' AND title LIKE '%' || ? || '%'"
        result = translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM t WHERE name LIKE 'how%%' AND title LIKE '%%' || %s || '%%'"

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

    def test_key_access_case_insensitive(self):
        row = DictRow({"Total": 100, "Name": "Alice"}, keys=["Total", "Name"])
        assert row["total"] == 100
        assert row["TOTAL"] == 100
        assert row["Total"] == 100
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

    def test_contains_case_insensitive(self):
        row = DictRow({"ID": 1, "Name": "Alice"}, keys=["ID", "Name"])
        assert "id" in row
        assert "ID" in row
        assert "name" in row
        assert "Name" in row
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

    def test_get_case_insensitive(self):
        row = DictRow({"Total": 100}, keys=["Total"])
        assert row.get("total") == 100
        assert row.get("TOTAL") == 100

    def test_index_out_of_range(self):
        row = DictRow({"id": 1}, keys=["id"])
        with pytest.raises(IndexError):
            _ = row[5]


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
                result = conn.execute("PRAGMA busy_timeout").fetchone()
                assert result[0] == BUSY_TIMEOUT_MS
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


class TestConnectValidation:
    """Tests that connect() validates URLs properly."""

    def test_rejects_mysql_url(self):
        with pytest.raises(InvalidDatabaseURL, match="Unsupported"):
            connect(url="mysql://localhost/db")

    def test_does_not_create_directory_for_bad_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_path = Path(tmpdir) / "mysql:" / "localhost"
            with pytest.raises(InvalidDatabaseURL):
                connect(url="mysql://localhost/db")
            assert not bad_path.exists()


class TestWriteTxSqlite:
    """Tests for write_tx() on SQLite."""

    def test_write_tx_issues_begin_immediate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path, wal_mode=False)
            conn.isolation_level = None
            try:
                conn.execute("CREATE TABLE t (id INTEGER)")
                blocked = []

                def try_write():
                    conn2 = sqlite3.connect(db_path, timeout=0.1)
                    conn2.isolation_level = None
                    try:
                        conn2.execute("BEGIN IMMEDIATE")
                        blocked.append(False)
                    except sqlite3.OperationalError as e:
                        if "locked" in str(e).lower() or "busy" in str(e).lower():
                            blocked.append(True)
                        else:
                            raise
                    finally:
                        conn2.close()

                with write_tx(conn, backend=Backend.SQLITE):
                    t = threading.Thread(target=try_write)
                    t.start()
                    t.join(timeout=1)

                assert blocked == [True], "Second writer should be blocked by BEGIN IMMEDIATE"
            finally:
                conn.close()

    def test_write_tx_commits_on_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path, wal_mode=False)
            conn.isolation_level = None
            try:
                conn.execute("CREATE TABLE t (id INTEGER)")
                with write_tx(conn, backend=Backend.SQLITE):
                    conn.execute("INSERT INTO t VALUES (1)")
                row = conn.execute("SELECT * FROM t").fetchone()
                assert row[0] == 1
            finally:
                conn.close()

    def test_write_tx_rollbacks_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path, wal_mode=False)
            conn.isolation_level = None
            try:
                conn.execute("CREATE TABLE t (id INTEGER)")
                try:
                    with write_tx(conn, backend=Backend.SQLITE):
                        conn.execute("INSERT INTO t VALUES (1)")
                        raise ValueError("test error")
                except ValueError:
                    pass
                row = conn.execute("SELECT * FROM t").fetchone()
                assert row is None
            finally:
                conn.close()


class TestInsertReturningIdSqlite:
    """Tests for insert_returning_id() on SQLite."""

    def test_returns_lastrowid(self):
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


class TestTableHelpers:
    """Tests for table_exists() and table_columns()."""

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

    def test_table_columns_quoted_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            conn = connect_sqlite(db_path)
            try:
                conn.execute('CREATE TABLE "user""table" (id INTEGER)')
                cols = table_columns(conn, 'user"table', backend=Backend.SQLITE)
                assert cols == {"id"}
            finally:
                conn.close()


class TestExistingDbPath:
    """Tests that existing backend/app/db.py paths work unchanged."""

    def test_open_db_connection_returns_sqlite3_row(self):
        from backend.app.db import open_db_connection
        conn = open_db_connection()
        try:
            assert conn.row_factory is sqlite3.Row
            result = conn.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == 30000
            result = conn.execute("PRAGMA journal_mode").fetchone()
            assert result[0].lower() == "wal"
            result = conn.execute("PRAGMA synchronous").fetchone()
            assert result[0] in (1, "normal")
        finally:
            conn.close()

    def test_db_conn_yields_sqlite3_row(self):
        from backend.app.db import db_conn
        with db_conn() as conn:
            assert conn.row_factory is sqlite3.Row
            conn.execute("CREATE TABLE IF NOT EXISTS _test_db_layer (x INTEGER)")
            conn.execute("INSERT INTO _test_db_layer VALUES (1)")
            row = conn.execute("SELECT x FROM _test_db_layer").fetchone()
            assert row["x"] == 1
            assert row[0] == 1
            conn.execute("DROP TABLE _test_db_layer")


# PostgreSQL tests - skipped unless TEST_DATABASE_URL is set
@pytest.fixture
def pg_url():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url or not url.startswith(("postgresql://", "postgres://")):
        pytest.skip("TEST_DATABASE_URL not set to a PostgreSQL URL")
    return url


@pytest.fixture
def pg_conn(pg_url):
    from shopifyseo.db import connect_postgres
    conn = connect_postgres(pg_url)
    yield conn
    conn.close()


class TestPostgresConnection:
    """PostgreSQL connection tests."""

    def test_connect_not_autocommit(self, pg_conn):
        assert pg_conn.autocommit is False

    def test_row_factory_produces_dictrow(self, pg_conn):
        row = pg_conn.execute("SELECT 1 AS val, 'test' AS name").fetchone()
        assert isinstance(row, DictRow)
        assert row["val"] == 1
        assert row["name"] == "test"
        assert row[0] == 1
        assert row[1] == "test"

    def test_row_factory_column_order(self, pg_conn):
        row = pg_conn.execute("SELECT 'a' AS first, 'b' AS second, 'c' AS third").fetchone()
        assert row.keys() == ("first", "second", "third")
        assert list(row) == ["a", "b", "c"]

    def test_row_factory_case_insensitive(self, pg_conn):
        row = pg_conn.execute("SELECT 1 AS Total").fetchone()
        assert row["total"] == 1
        assert row["TOTAL"] == 1
        assert row["Total"] == 1


class TestPostgresTranslation:
    """PostgreSQL placeholder translation tests with real execution."""

    def test_basic_placeholder(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders("SELECT ? AS val", to_postgres=True),
            (42,)
        ).fetchone()
        assert row[0] == 42

    def test_like_with_percent(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders("SELECT 'abc' LIKE 'a%' AS m, ? AS v", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["m"] is True
        assert row["v"] == "test"

    def test_modulo_with_percent(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders("SELECT 7 % 3 AS r, ? AS v", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["r"] == 1
        assert row["v"] == "test"

    def test_single_line_comment(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders("SELECT ? AS b -- why?", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["b"] == "test"

    def test_multi_line_comment(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders("SELECT /* is it? */ ? AS b", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["b"] == "test"

    def test_backslash_in_standard_string(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders(r"SELECT 'a\', ? AS b, 'c' AS c", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["b"] == "test"

    def test_double_question_jsonb(self, pg_conn):
        row = pg_conn.execute(
            translate_placeholders("SELECT '{\"a\":1}'::jsonb ?? 'a' AS has_key, ? AS v", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["has_key"] is True
        assert row["v"] == "test"


class TestPostgresWriteTx:
    """PostgreSQL write_tx tests."""

    def test_write_tx_commits(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE t (id SERIAL PRIMARY KEY, val TEXT)")
        with write_tx(pg_conn, backend=Backend.POSTGRES):
            pg_conn.execute("INSERT INTO t (val) VALUES ('test')")
        row = pg_conn.execute("SELECT val FROM t").fetchone()
        assert row["val"] == "test"

    def test_write_tx_rollbacks_on_error(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE t2 (id SERIAL PRIMARY KEY, val TEXT)")
        pg_conn.commit()
        try:
            with write_tx(pg_conn, backend=Backend.POSTGRES):
                pg_conn.execute("INSERT INTO t2 (val) VALUES ('test')")
                raise ValueError("test error")
        except ValueError:
            pass
        row = pg_conn.execute("SELECT val FROM t2").fetchone()
        assert row is None


class TestPostgresInsertReturningId:
    """PostgreSQL insert_returning_id tests."""

    def test_uses_returning(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE users (id SERIAL PRIMARY KEY, name TEXT)")
        row_id = insert_returning_id(
            pg_conn,
            "INSERT INTO users (name) VALUES (?)",
            ("Alice",),
            backend=Backend.POSTGRES,
        )
        assert row_id == 1
        row_id2 = insert_returning_id(
            pg_conn,
            "INSERT INTO users (name) VALUES (?)",
            ("Bob",),
            backend=Backend.POSTGRES,
        )
        assert row_id2 == 2


class TestPostgresTableHelpers:
    """PostgreSQL table helper tests."""

    def test_table_exists_public_schema(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE test_exists (id INTEGER)")
        exists = table_exists(pg_conn, "test_exists", backend=Backend.POSTGRES)
        assert exists is True
        exists = table_exists(pg_conn, "nonexistent_xyz", backend=Backend.POSTGRES)
        assert exists is False

    def test_table_columns(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE test_cols (id INTEGER, name TEXT, val REAL)")
        cols = table_columns(pg_conn, "test_cols", backend=Backend.POSTGRES)
        assert cols == {"id", "name", "val"}
