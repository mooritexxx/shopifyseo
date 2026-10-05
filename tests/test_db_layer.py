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
    write_tx,
    BUSY_TIMEOUT_MS,
)
from shopifyseo.db.compat import _translate_placeholders


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
    """Tests for _translate_placeholders() - the private placeholder translation function.
    
    This function is internal and not exported from shopifyseo.db.
    Users should use shopifyseo.db.execute() which handles translation automatically.
    """

    def test_not_exported_from_public_api(self):
        """_translate_placeholders is private and not exported from shopifyseo.db."""
        import shopifyseo.db
        assert not hasattr(shopifyseo.db, "translate_placeholders")
        assert "_translate_placeholders" not in shopifyseo.db.__all__
        assert "translate_placeholders" not in shopifyseo.db.__all__

    def test_basic_replacement(self):
        sql = "SELECT * FROM users WHERE id = ? AND name = ?"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM users WHERE id = %s AND name = %s"

    def test_no_change_for_sqlite(self):
        sql = "SELECT * FROM users WHERE id = ?"
        result = _translate_placeholders(sql, to_postgres=False)
        assert result == sql

    def test_preserves_single_quoted_strings(self):
        sql = "SELECT * FROM users WHERE name = '?' AND id = ?"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM users WHERE name = '?' AND id = %s"

    def test_preserves_double_quoted_identifiers(self):
        sql = 'SELECT "?" AS col, ? AS val'
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == 'SELECT "?" AS col, %s AS val'

    def test_double_question_mark(self):
        sql = "SELECT * FROM data WHERE payload ?? 'key' AND id = ?"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM data WHERE payload ? 'key' AND id = %s"

    def test_escapes_percent_for_like_with_placeholders(self):
        sql = "SELECT * FROM t WHERE name LIKE 'a%' AND id = ?"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM t WHERE name LIKE 'a%%' AND id = %s"

    def test_escapes_percent_for_modulo_with_placeholders(self):
        sql = "SELECT 7 % 3 AS r, ? AS v"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT 7 %% 3 AS r, %s AS v"

    def test_no_escape_percent_without_placeholders(self):
        sql = "SELECT 7 % 3 AS r, 'a%' AS v"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == sql

    def test_explicit_escape_percent_flag(self):
        sql = "SELECT 7 % 3 AS r"
        result = _translate_placeholders(sql, to_postgres=True, escape_percent=True)
        assert result == "SELECT 7 %% 3 AS r"

    def test_explicit_no_escape_percent_flag(self):
        sql = "SELECT 7 % 3 AS r, ? AS v"
        result = _translate_placeholders(sql, to_postgres=True, escape_percent=False)
        assert result == "SELECT 7 % 3 AS r, %s AS v"

    def test_preserves_single_line_comment(self):
        sql = "SELECT ? AS b -- why?"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT %s AS b -- why?"

    def test_preserves_multi_line_comment(self):
        sql = "SELECT /* is it? */ ? AS b"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT /* is it? */ %s AS b"

    def test_backslash_not_escape_in_standard_string(self):
        sql = r"SELECT 'a\', ? AS b, 'c'"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == r"SELECT 'a\', %s AS b, 'c'"

    def test_else_with_string_not_misread_as_e_string(self):
        sql = r"SELECT CASE WHEN 1=1 THEN 'a' ELSE'\' END, ? AS b"
        result = _translate_placeholders(sql, to_postgres=True)
        assert "%s" in result

    def test_escaped_quotes_in_string(self):
        sql = "SELECT 'don''t?', ? AS val"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT 'don''t?', %s AS val"

    def test_mixed_like_patterns(self):
        sql = "SELECT * FROM t WHERE name LIKE 'how%' AND title LIKE '%' || ? || '%'"
        result = _translate_placeholders(sql, to_postgres=True)
        assert result == "SELECT * FROM t WHERE name LIKE 'how%%' AND title LIKE '%%' || %s || '%%'"

    def test_empty_sql(self):
        assert _translate_placeholders("", to_postgres=True) == ""

    def test_no_placeholders(self):
        sql = "SELECT * FROM users"
        assert _translate_placeholders(sql, to_postgres=True) == sql


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
        row = DictRow({"id": 1, "name": "Alice"}, keys=["id", "name"])
        d = dict(row.items())
        assert d == {"id": 1, "name": "Alice"}

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

    def test_duplicate_column_names(self):
        row = DictRow.from_values(("id", "id"), (1, 2))
        assert tuple(row) == (1, 2)
        assert row[0] == 1
        assert row[1] == 2
        assert row["id"] == 1
        assert row.keys() == ("id", "id")


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
    """Tests that existing backend/app/db.py paths work unchanged.
    
    Uses a temp DB to avoid touching the repo's live database.
    """

    def test_open_db_connection_returns_sqlite3_row(self):
        import importlib
        import shopifyseo.dashboard_store
        import backend.app.db
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            original_path = shopifyseo.dashboard_store.DB_PATH
            try:
                shopifyseo.dashboard_store.DB_PATH = str(db_path)
                importlib.reload(backend.app.db)
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
            finally:
                shopifyseo.dashboard_store.DB_PATH = original_path
                importlib.reload(backend.app.db)

    def test_db_conn_yields_sqlite3_row(self):
        import importlib
        import shopifyseo.dashboard_store
        import backend.app.db
        
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.sqlite3"
            original_path = shopifyseo.dashboard_store.DB_PATH
            try:
                shopifyseo.dashboard_store.DB_PATH = str(db_path)
                importlib.reload(backend.app.db)
                from backend.app.db import db_conn
                with db_conn() as conn:
                    assert conn.row_factory is sqlite3.Row
                    conn.execute("CREATE TABLE IF NOT EXISTS _test_db_layer (x INTEGER)")
                    conn.execute("INSERT INTO _test_db_layer VALUES (1)")
                    row = conn.execute("SELECT x FROM _test_db_layer").fetchone()
                    assert row["x"] == 1
                    assert row[0] == 1
                    conn.execute("DROP TABLE _test_db_layer")
            finally:
                shopifyseo.dashboard_store.DB_PATH = original_path
                importlib.reload(backend.app.db)


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
        row = pg_conn.execute("SELECT 1 AS \"Total\"").fetchone()
        assert row["total"] == 1
        assert row["TOTAL"] == 1
        assert row["Total"] == 1

    def test_row_factory_duplicate_columns(self, pg_conn):
        row = pg_conn.execute("SELECT 1 AS id, 2 AS id").fetchone()
        assert tuple(row) == (1, 2)
        assert row[0] == 1
        assert row[1] == 2
        assert row["id"] == 1


class TestPostgresTranslation:
    """PostgreSQL placeholder translation tests with real execution."""

    def test_basic_placeholder(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders("SELECT ? AS val", to_postgres=True),
            (42,)
        ).fetchone()
        assert row[0] == 42

    def test_like_with_percent_and_params(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders("SELECT 'abc' LIKE 'a%' AS m, ? AS v", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["m"] is True
        assert row["v"] == "test"

    def test_modulo_with_percent_and_params(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders("SELECT 7 % 3 AS r, ? AS v", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["r"] == 1
        assert row["v"] == "test"

    def test_like_no_params(self, pg_conn):
        sql = _translate_placeholders("SELECT 'a%' LIKE 'a%' AS m", to_postgres=True)
        row = pg_conn.execute(sql).fetchone()
        assert row["m"] is True

    def test_modulo_no_params(self, pg_conn):
        sql = _translate_placeholders("SELECT 7 % 3 AS r", to_postgres=True)
        row = pg_conn.execute(sql).fetchone()
        assert row["r"] == 1

    def test_like_empty_params(self, pg_conn):
        sql = _translate_placeholders("SELECT 'a%' LIKE 'a%' AS m", to_postgres=True, escape_percent=True)
        row = pg_conn.execute(sql, ()).fetchone()
        assert row["m"] is True

    def test_modulo_empty_params(self, pg_conn):
        sql = _translate_placeholders("SELECT 7 % 3 AS r", to_postgres=True, escape_percent=True)
        row = pg_conn.execute(sql, ()).fetchone()
        assert row["r"] == 1

    def test_single_line_comment(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders("SELECT ? AS b -- why?", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["b"] == "test"

    def test_multi_line_comment(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders("SELECT /* is it? */ ? AS b", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["b"] == "test"

    def test_backslash_in_standard_string(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders(r"SELECT 'a\', ? AS b, 'c' AS c", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["b"] == "test"

    def test_double_question_jsonb(self, pg_conn):
        row = pg_conn.execute(
            _translate_placeholders("SELECT '{\"a\":1}'::jsonb ?? 'a' AS has_key, ? AS v", to_postgres=True),
            ("test",)
        ).fetchone()
        assert row["has_key"] is True
        assert row["v"] == "test"


class TestPostgresWriteTx:
    """PostgreSQL write_tx tests."""

    def test_write_tx_commits_visible_to_second_connection(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn1 = connect_postgres(pg_url)
        table_name = "_test_write_tx_visibility"
        try:
            conn1.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn1.commit()
            conn1.execute(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY, val TEXT)")
            conn1.commit()
            conn1.execute("SELECT 1")
            with write_tx(conn1, backend=Backend.POSTGRES):
                conn1.execute(f"INSERT INTO {table_name} (val) VALUES ('test')")
            conn2 = connect_postgres(pg_url)
            try:
                row = conn2.execute(f"SELECT val FROM {table_name}").fetchone()
                assert row is not None, "Write should be visible to second connection"
                assert row["val"] == "test"
            finally:
                conn2.close()
        finally:
            conn1.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn1.commit()
            conn1.close()

    def test_write_tx_rollbacks_on_error(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_write_tx_rollback"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY, val TEXT)")
            conn.commit()
            try:
                with write_tx(conn, backend=Backend.POSTGRES):
                    conn.execute(f"INSERT INTO {table_name} (val) VALUES ('test')")
                    raise ValueError("test error")
            except ValueError:
                pass
            row = conn.execute(f"SELECT val FROM {table_name}").fetchone()
            assert row is None
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()


class TestPostgresInsertReturningId:
    """PostgreSQL insert_returning_id tests."""

    def test_uses_returning(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_insert_returning"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY, name TEXT)")
            conn.commit()
            row_id = insert_returning_id(
                conn,
                f"INSERT INTO {table_name} (name) VALUES (?)",
                ("Alice",),
                backend=Backend.POSTGRES,
            )
            conn.commit()
            assert row_id == 1
            row_id2 = insert_returning_id(
                conn,
                f"INSERT INTO {table_name} (name) VALUES (?)",
                ("Bob",),
                backend=Backend.POSTGRES,
            )
            conn.commit()
            assert row_id2 == 2
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()


class TestPostgresTableHelpers:
    """PostgreSQL table helper tests."""

    def test_table_exists_temp_table(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE test_exists_temp (id INTEGER)")
        exists = table_exists(pg_conn, "test_exists_temp", backend=Backend.POSTGRES)
        assert exists is True
        exists = table_exists(pg_conn, "nonexistent_xyz_abc", backend=Backend.POSTGRES)
        assert exists is False

    def test_table_exists_real_table(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_table_exists_real"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id INTEGER)")
            conn.commit()
            exists = table_exists(conn, table_name, backend=Backend.POSTGRES)
            assert exists is True
            exists = table_exists(conn, "nonexistent_xyz_abc", backend=Backend.POSTGRES)
            assert exists is False
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()

    def test_table_columns_temp_table(self, pg_conn):
        pg_conn.execute("CREATE TEMP TABLE test_cols_temp (id INTEGER, name TEXT, val REAL)")
        cols = table_columns(pg_conn, "test_cols_temp", backend=Backend.POSTGRES)
        assert cols == {"id", "name", "val"}

    def test_table_columns_real_table(self, pg_url):
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_table_columns_real"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id INTEGER, name TEXT, val REAL)")
            conn.commit()
            cols = table_columns(conn, table_name, backend=Backend.POSTGRES)
            assert cols == {"id", "name", "val"}
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()

    def test_table_columns_nonexistent(self, pg_conn):
        cols = table_columns(pg_conn, "nonexistent_table_xyz", backend=Backend.POSTGRES)
        assert cols == set()


class TestPostgresPercentMatching:
    """Test that % handling matches SQLite for various param scenarios."""

    def test_modulo_matches_sqlite_no_params(self, pg_conn):
        sql = "SELECT 7 % 3 AS r"
        pg_result = pg_conn.execute(_translate_placeholders(sql, to_postgres=True)).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql).fetchone()
                assert pg_result["r"] == sqlite_result["r"]
            finally:
                sqlite_conn.close()

    def test_like_matches_sqlite_no_params(self, pg_conn):
        sql = "SELECT 'abc' LIKE 'a%' AS m"
        pg_result = pg_conn.execute(_translate_placeholders(sql, to_postgres=True)).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql).fetchone()
                assert pg_result["m"] == sqlite_result["m"]
            finally:
                sqlite_conn.close()

    def test_modulo_matches_sqlite_empty_params(self, pg_conn):
        sql = "SELECT 7 % 3 AS r"
        pg_result = pg_conn.execute(_translate_placeholders(sql, to_postgres=True, escape_percent=True), ()).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql, ()).fetchone()
                assert pg_result["r"] == sqlite_result["r"]
            finally:
                sqlite_conn.close()

    def test_modulo_matches_sqlite_with_params(self, pg_conn):
        sql = "SELECT 7 % 3 AS r, ? AS v"
        pg_result = pg_conn.execute(
            _translate_placeholders(sql, to_postgres=True), ("x",)
        ).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql, ("x",)).fetchone()
                assert pg_result["r"] == sqlite_result["r"]
                assert pg_result["v"] == sqlite_result["v"]
            finally:
                sqlite_conn.close()

    def test_like_matches_sqlite_empty_params(self, pg_conn):
        sql = "SELECT 'abc' LIKE 'a%' AS m"
        pg_result = pg_conn.execute(_translate_placeholders(sql, to_postgres=True, escape_percent=True), ()).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql, ()).fetchone()
                assert pg_result["m"] == sqlite_result["m"]
            finally:
                sqlite_conn.close()

    def test_modulo_matches_sqlite_empty_list_params(self, pg_conn):
        sql = "SELECT 7 % 3 AS r"
        pg_result = pg_conn.execute(_translate_placeholders(sql, to_postgres=True, escape_percent=True), []).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql, []).fetchone()
                assert pg_result["r"] == sqlite_result["r"]
            finally:
                sqlite_conn.close()

    def test_like_matches_sqlite_empty_list_params(self, pg_conn):
        sql = "SELECT 'abc' LIKE 'a%' AS m"
        pg_result = pg_conn.execute(_translate_placeholders(sql, to_postgres=True, escape_percent=True), []).fetchone()
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                sqlite_result = sqlite_conn.execute(sql, []).fetchone()
                assert pg_result["m"] == sqlite_result["m"]
            finally:
                sqlite_conn.close()


class TestInsertReturningIdPercentLiteral:
    """Regression test: insert_returning_id with literal % in value."""

    def test_insert_percent_literal_postgres(self, pg_conn):
        """Literal '50%' via insert_returning_id with empty params reads back exactly."""
        pg_conn.execute("DROP TABLE IF EXISTS percent_test")
        pg_conn.execute("CREATE TABLE percent_test (id SERIAL PRIMARY KEY, val TEXT)")
        pg_conn.commit()

        row_id = insert_returning_id(
            pg_conn,
            "INSERT INTO percent_test (val) VALUES ('50%')",
            backend=Backend.POSTGRES,
        )
        pg_conn.commit()

        row = pg_conn.execute("SELECT val FROM percent_test WHERE id = %s", (row_id,)).fetchone()
        assert row["val"] == "50%"

        pg_conn.execute("DROP TABLE percent_test")
        pg_conn.commit()


# ============================================================================
# PR2: Execute wrapper and get_connection tests
# ============================================================================

from shopifyseo.db import (
    execute,
    executemany,
    get_connection,
    table_ddl,
    index_exists,
    foreign_keys_enabled,
    set_foreign_keys,
    journal_mode,
    busy_timeout,
    resync_sequence,
    resync_all_sequences,
    ensure_identity,
    create_identity_column_ddl,
    IDENTITY_COLUMNS,
    get_sequence_name,
    ResyncResult,
)


class TestExecuteWrapperSqlite:
    """Tests for execute() wrapper on SQLite."""

    def test_execute_no_params(self):
        """execute() with params=None works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                conn.execute("INSERT INTO t VALUES (1, 'test')")
                row = execute(conn, "SELECT * FROM t", backend=Backend.SQLITE).fetchone()
                assert row["id"] == 1
                assert row["name"] == "test"
            finally:
                conn.close()

    def test_execute_empty_tuple_params(self):
        """execute() with params=() works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE t (val TEXT)")
                conn.execute("INSERT INTO t VALUES ('50%')")
                row = execute(conn, "SELECT val FROM t", (), backend=Backend.SQLITE).fetchone()
                assert row["val"] == "50%"
            finally:
                conn.close()

    def test_execute_empty_list_params(self):
        """execute() with params=[] works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE t (val TEXT)")
                conn.execute("INSERT INTO t VALUES ('50%')")
                row = execute(conn, "SELECT val FROM t", [], backend=Backend.SQLITE).fetchone()
                assert row["val"] == "50%"
            finally:
                conn.close()

    def test_execute_with_params(self):
        """execute() with actual params works correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                execute(conn, "INSERT INTO t VALUES (?, ?)", (1, "Alice"), backend=Backend.SQLITE)
                row = execute(conn, "SELECT * FROM t WHERE id = ?", (1,), backend=Backend.SQLITE).fetchone()
                assert row["name"] == "Alice"
            finally:
                conn.close()

    def test_execute_percent_literal_no_params(self):
        """SELECT with % literal and no params works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 7 % 3 AS r", backend=Backend.SQLITE).fetchone()
                assert row["r"] == 1
            finally:
                conn.close()

    def test_execute_like_percent_no_params(self):
        """SELECT with LIKE % and no params works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 'abc' LIKE 'a%' AS m", backend=Backend.SQLITE).fetchone()
                assert row["m"] == 1
            finally:
                conn.close()


class TestExecuteWrapperPostgres:
    """Tests for execute() wrapper on PostgreSQL."""

    def test_execute_no_params(self, pg_conn):
        """execute() with params=None works correctly."""
        row = execute(pg_conn, "SELECT 1 AS val", backend=Backend.POSTGRES).fetchone()
        assert row["val"] == 1

    def test_execute_empty_tuple_params(self, pg_conn):
        """execute() with params=() and % literal works."""
        row = execute(pg_conn, "SELECT 7 % 3 AS r", (), backend=Backend.POSTGRES).fetchone()
        assert row["r"] == 1

    def test_execute_empty_list_params(self, pg_conn):
        """execute() with params=[] and % literal works."""
        row = execute(pg_conn, "SELECT 7 % 3 AS r", [], backend=Backend.POSTGRES).fetchone()
        assert row["r"] == 1

    def test_execute_with_params(self, pg_conn):
        """execute() with actual params works correctly."""
        row = execute(pg_conn, "SELECT ? AS val", (42,), backend=Backend.POSTGRES).fetchone()
        assert row["val"] == 42

    def test_execute_like_empty_tuple(self, pg_conn):
        """LIKE with % and empty tuple params works."""
        row = execute(pg_conn, "SELECT 'abc' LIKE 'a%' AS m", (), backend=Backend.POSTGRES).fetchone()
        assert row["m"] is True

    def test_execute_like_empty_list(self, pg_conn):
        """LIKE with % and empty list params works."""
        row = execute(pg_conn, "SELECT 'abc' LIKE 'a%' AS m", [], backend=Backend.POSTGRES).fetchone()
        assert row["m"] is True

    def test_execute_like_no_params(self, pg_conn):
        """LIKE with % and no params (None) works."""
        row = execute(pg_conn, "SELECT 'abc' LIKE 'a%' AS m", backend=Backend.POSTGRES).fetchone()
        assert row["m"] is True

    def test_execute_modulo_with_placeholder(self, pg_conn):
        """Modulo % with placeholder param works."""
        row = execute(pg_conn, "SELECT 7 % 3 AS r, ? AS v", ("x",), backend=Backend.POSTGRES).fetchone()
        assert row["r"] == 1
        assert row["v"] == "x"


class TestExecutemany:
    """Tests for executemany() function."""

    def test_executemany_sqlite(self):
        """executemany() works on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                executemany(conn, "INSERT INTO t VALUES (?, ?)", [(1, "a"), (2, "b")], backend=Backend.SQLITE)
                rows = conn.execute("SELECT * FROM t ORDER BY id").fetchall()
                assert len(rows) == 2
                assert rows[0]["name"] == "a"
                assert rows[1]["name"] == "b"
            finally:
                conn.close()

    def test_executemany_postgres(self, pg_conn):
        """executemany() works on PostgreSQL."""
        pg_conn.execute("DROP TABLE IF EXISTS test_executemany")
        pg_conn.execute("CREATE TABLE test_executemany (id INTEGER, name TEXT)")
        pg_conn.commit()
        try:
            executemany(pg_conn, "INSERT INTO test_executemany VALUES (?, ?)", [(1, "a"), (2, "b")], backend=Backend.POSTGRES)
            pg_conn.commit()
            rows = pg_conn.execute("SELECT * FROM test_executemany ORDER BY id").fetchall()
            assert len(rows) == 2
            assert rows[0]["name"] == "a"
            assert rows[1]["name"] == "b"
        finally:
            pg_conn.execute("DROP TABLE IF EXISTS test_executemany")
            pg_conn.commit()


class TestGetConnection:
    """Tests for get_connection() function."""

    def test_get_connection_default_sqlite(self):
        """get_connection() returns SQLite when DATABASE_URL not set."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            with tempfile.TemporaryDirectory() as tmpdir:
                conn = get_connection(path=Path(tmpdir) / "test.db")
                try:
                    assert isinstance(conn, sqlite3.Connection)
                    conn.execute("CREATE TABLE t (id INTEGER)")
                    row = conn.execute("SELECT 1 AS val").fetchone()
                    assert row["val"] == 1
                finally:
                    conn.close()

    def test_get_connection_postgres_url(self, pg_url):
        """get_connection() with a postgresql:// URL returns a psycopg connection."""
        import psycopg

        conn = get_connection(url=pg_url)
        try:
            assert isinstance(conn, psycopg.Connection)
            assert not isinstance(conn, sqlite3.Connection)
            row = conn.execute("SELECT 1 AS val").fetchone()
            assert row["val"] == 1
        finally:
            conn.close()

    def test_get_connection_postgres_creates_no_file(self, pg_url):
        """get_connection() with a postgresql:// URL creates nothing on disk."""
        import psycopg

        with tempfile.TemporaryDirectory() as tmpdir:
            check_dir = Path(tmpdir)
            decoy = check_dir / "should_not_exist.db"
            cwd_before = {p.name for p in Path.cwd().iterdir()}
            conn = get_connection(url=pg_url, path=decoy)
            try:
                assert isinstance(conn, psycopg.Connection)
                row = conn.execute("SELECT 1 AS val").fetchone()
                assert row["val"] == 1
            finally:
                conn.close()
            assert not decoy.exists()
            assert list(check_dir.iterdir()) == []
            cwd_after = {p.name for p in Path.cwd().iterdir()}
            leaked = [
                name
                for name in (cwd_after - cwd_before)
                if "postgresql" in name.lower() or name.endswith(".db")
            ]
            assert leaked == [], f"Unexpected files created from PG URL: {leaked}"


class TestTableDdl:
    """Tests for table_ddl() function."""

    def test_table_ddl_sqlite(self):
        """table_ddl() returns CREATE TABLE for SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
                ddl = table_ddl(conn, "users", backend=Backend.SQLITE)
                assert ddl is not None
                assert "CREATE TABLE" in ddl
                assert "users" in ddl
            finally:
                conn.close()

    def test_table_ddl_nonexistent_sqlite(self):
        """table_ddl() returns None for nonexistent table."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                ddl = table_ddl(conn, "nonexistent", backend=Backend.SQLITE)
                assert ddl is None
            finally:
                conn.close()

    def test_table_ddl_postgres(self, pg_url):
        """table_ddl() returns DDL for PostgreSQL."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_table_ddl"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.commit()
            ddl = table_ddl(conn, table_name, backend=Backend.POSTGRES)
            assert ddl is not None
            assert "CREATE TABLE" in ddl
            assert "id" in ddl
            assert "name" in ddl
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()


class TestIndexExists:
    """Tests for index_exists() function."""

    def test_index_exists_sqlite(self):
        """index_exists() correctly detects SQLite indexes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                conn.execute("CREATE TABLE users (id INTEGER, name TEXT)")
                conn.execute("CREATE INDEX idx_users_name ON users(name)")
                assert index_exists(conn, "idx_users_name", backend=Backend.SQLITE) is True
                assert index_exists(conn, "nonexistent_idx", backend=Backend.SQLITE) is False
            finally:
                conn.close()

    def test_index_exists_postgres(self, pg_url):
        """index_exists() correctly detects PostgreSQL indexes."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_idx_exists"
        idx_name = "_test_idx_exists_name"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id INTEGER, name TEXT)")
            conn.execute(f"CREATE INDEX {idx_name} ON {table_name}(name)")
            conn.commit()
            assert index_exists(conn, idx_name, backend=Backend.POSTGRES) is True
            assert index_exists(conn, "nonexistent_idx_xyz", backend=Backend.POSTGRES) is False
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()


class TestForeignKeysHelpers:
    """Tests for foreign_keys_enabled() and set_foreign_keys()."""

    def test_foreign_keys_enabled_sqlite(self):
        """foreign_keys_enabled() checks SQLite setting."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                set_foreign_keys(conn, True, backend=Backend.SQLITE)
                assert foreign_keys_enabled(conn, backend=Backend.SQLITE) is True
                set_foreign_keys(conn, False, backend=Backend.SQLITE)
                assert foreign_keys_enabled(conn, backend=Backend.SQLITE) is False
            finally:
                conn.close()

    def test_foreign_keys_always_true_postgres(self, pg_conn):
        """foreign_keys_enabled() always returns True on PostgreSQL."""
        assert foreign_keys_enabled(pg_conn, backend=Backend.POSTGRES) is True


class TestJournalMode:
    """Tests for journal_mode() function."""

    def test_journal_mode_sqlite_wal(self):
        """journal_mode() returns WAL on SQLite with WAL enabled."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db", wal_mode=True)
            try:
                mode = journal_mode(conn, backend=Backend.SQLITE)
                assert mode.lower() == "wal"
            finally:
                conn.close()

    def test_journal_mode_postgres(self, pg_conn):
        """journal_mode() returns wal_level on PostgreSQL."""
        mode = journal_mode(pg_conn, backend=Backend.POSTGRES)
        assert mode in ("replica", "logical", "minimal")


class TestBusyTimeout:
    """Tests for busy_timeout() function."""

    def test_busy_timeout_sqlite(self):
        """busy_timeout() returns the configured timeout on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db", busy_timeout_ms=5000)
            try:
                timeout = busy_timeout(conn, backend=Backend.SQLITE)
                assert timeout == 5000
            finally:
                conn.close()

    def test_busy_timeout_sqlite_default_30000(self):
        """busy_timeout() returns default 30000ms on SQLite when not specified."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                timeout = busy_timeout(conn, backend=Backend.SQLITE)
                assert timeout == 30000
            finally:
                conn.close()

    def test_get_connection_sqlite_busy_timeout_default_30000(self):
        """get_connection() on SQLite applies PRAGMA busy_timeout = 30000."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            with tempfile.TemporaryDirectory() as tmpdir:
                conn = get_connection(path=Path(tmpdir) / "test.db")
                try:
                    row = conn.execute("PRAGMA busy_timeout").fetchone()
                    assert row[0] == 30000
                    assert busy_timeout(conn, backend=Backend.SQLITE) == 30000
                finally:
                    conn.close()

    def test_get_connection_create_parents_false_does_not_mkdir(self):
        """create_parents=False leaves a missing parent directory uncreated."""
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            with tempfile.TemporaryDirectory() as tmpdir:
                missing = Path(tmpdir) / "nope" / "test.db"
                with pytest.raises((sqlite3.OperationalError, OSError)):
                    get_connection(
                        path=missing,
                        create_parents=False,
                        wal_mode=False,
                        busy_timeout_ms=0,
                    )
                assert not missing.parent.exists()

    def test_busy_timeout_postgres(self, pg_conn):
        """busy_timeout() returns lock_timeout on PostgreSQL."""
        timeout = busy_timeout(pg_conn, backend=Backend.POSTGRES)
        assert isinstance(timeout, int)

    def test_busy_timeout_postgres_maps_2s_to_2000(self, pg_url):
        """busy_timeout() maps PG '2s' to 2000ms using pg_settings."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute("SET lock_timeout = '2s'")
            timeout = busy_timeout(conn, backend=Backend.POSTGRES)
            assert timeout == 2000
        finally:
            conn.close()

    def test_busy_timeout_postgres_maps_1min_to_60000(self, pg_url):
        """busy_timeout() maps PG '1min' to 60000ms using pg_settings."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute("SET lock_timeout = '1min'")
            timeout = busy_timeout(conn, backend=Backend.POSTGRES)
            assert timeout == 60000
        finally:
            conn.close()


class TestIdentityColumnHelpers:
    """Tests for identity column DDL and sequence helpers."""

    def test_create_identity_column_ddl_sqlite(self):
        """create_identity_column_ddl() returns INTEGER PRIMARY KEY for SQLite."""
        ddl = create_identity_column_ddl("test_table", "id", backend=Backend.SQLITE)
        assert ddl == "id INTEGER PRIMARY KEY"

    def test_create_identity_column_ddl_postgres(self):
        """create_identity_column_ddl() returns GENERATED BY DEFAULT for PostgreSQL."""
        ddl = create_identity_column_ddl("test_table", "id", backend=Backend.POSTGRES)
        assert "GENERATED BY DEFAULT AS IDENTITY" in ddl
        assert "PRIMARY KEY" in ddl
        assert "BIGINT" in ddl

    def test_get_sequence_name_sqlite_returns_none(self):
        """get_sequence_name() returns None on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                result = get_sequence_name(conn, "users", "id", backend=Backend.SQLITE)
                assert result is None
            finally:
                conn.close()

    def test_get_sequence_name_postgres(self, pg_url):
        """get_sequence_name() uses pg_get_serial_sequence on PostgreSQL."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_get_seq_name"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY, name TEXT)")
            conn.commit()
            seq_name = get_sequence_name(conn, table_name, "id", backend=Backend.POSTGRES)
            assert seq_name is not None
            assert table_name in seq_name
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()

    def test_resync_sequence_sqlite_noop(self):
        """resync_sequence() is a no-op on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                result = resync_sequence(conn, "test", "id", backend=Backend.SQLITE)
                assert result.new_value is None
                assert result.error is None
            finally:
                conn.close()

    def test_resync_sequence_postgres(self, pg_url):
        """resync_sequence() updates sequence on PostgreSQL."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        table_name = "_test_resync_seq"
        try:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.execute(f"CREATE TABLE {table_name} (id SERIAL PRIMARY KEY, name TEXT)")
            conn.commit()
            conn.execute(f"INSERT INTO {table_name} (id, name) VALUES (100, 'manual')")
            conn.commit()
            result = resync_sequence(conn, table_name, "id", backend=Backend.POSTGRES)
            assert result.error is None
            assert result.new_value == 101
            conn.execute(f"INSERT INTO {table_name} (name) VALUES ('auto')")
            conn.commit()
            row = conn.execute(f"SELECT id FROM {table_name} WHERE name = 'auto'").fetchone()
            assert row["id"] == 101
        finally:
            conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            conn.commit()
            conn.close()

    def test_resync_sequence_postgres_reserved_word_table(self, pg_url):
        """resync_sequence() works with reserved word table names."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute('DROP TABLE IF EXISTS "order"')
            conn.commit()
            conn.execute('CREATE TABLE "order" (id SERIAL PRIMARY KEY, name TEXT)')
            conn.commit()
            conn.execute('INSERT INTO "order" (id, name) VALUES (50, \'manual\')')
            conn.commit()
            result = resync_sequence(conn, "order", "id", backend=Backend.POSTGRES)
            assert result.error is None
            assert result.new_value == 51
        finally:
            conn.execute('DROP TABLE IF EXISTS "order"')
            conn.commit()
            conn.close()

    def test_resync_all_sequences_sqlite_empty(self):
        """resync_all_sequences() returns empty list on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                results = resync_all_sequences(conn, backend=Backend.SQLITE)
                assert results == []
            finally:
                conn.close()

    def test_resync_all_sequences_postgres_discovers_tables(self, pg_url):
        """resync_all_sequences() discovers tables from catalog."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute("DROP TABLE IF EXISTS _test_resync_all_a")
            conn.execute("DROP TABLE IF EXISTS _test_resync_all_b")
            conn.commit()
            conn.execute("CREATE TABLE _test_resync_all_a (id SERIAL PRIMARY KEY, name TEXT)")
            conn.execute("CREATE TABLE _test_resync_all_b (id SERIAL PRIMARY KEY, val INT)")
            conn.commit()
            conn.execute("INSERT INTO _test_resync_all_a (id, name) VALUES (10, 'a')")
            conn.execute("INSERT INTO _test_resync_all_b (id, val) VALUES (20, 1)")
            conn.commit()
            results = resync_all_sequences(conn, backend=Backend.POSTGRES)
            result_map = {r.table: r for r in results}
            assert "_test_resync_all_a" in result_map
            assert "_test_resync_all_b" in result_map
            assert result_map["_test_resync_all_a"].new_value == 11
            assert result_map["_test_resync_all_b"].new_value == 21
        finally:
            conn.execute("DROP TABLE IF EXISTS _test_resync_all_a")
            conn.execute("DROP TABLE IF EXISTS _test_resync_all_b")
            conn.commit()
            conn.close()

    def test_resync_all_sequences_skips_composite_pk(self, pg_url):
        """resync_all_sequences() skips composite PKs like cluster_keywords (int + text)."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute("DROP TABLE IF EXISTS _test_composite_pk")
            conn.commit()
            conn.execute("""
                CREATE TABLE _test_composite_pk (
                    cluster_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    PRIMARY KEY (cluster_id, keyword)
                )
            """)
            conn.commit()
            results = resync_all_sequences(conn, backend=Backend.POSTGRES)
            table_names = [r.table for r in results]
            assert "_test_composite_pk" not in table_names
        finally:
            conn.execute("DROP TABLE IF EXISTS _test_composite_pk")
            conn.commit()
            conn.close()

    def test_resync_mixed_tables_savepoint_and_max_plus_one(self, pg_url):
        """Real-PG coverage: mixed sequences, composite PK, reserved name, max+1.

        A composite-PK table in the explicit list must not abort the
        transaction; the following valid table is still resynced.
        """
        from shopifyseo.db import connect_postgres
        from psycopg import sql

        conn = connect_postgres(pg_url)
        tables = {
            "with_seq": "_test_mixed_with_seq",
            "no_seq": "_test_mixed_no_seq",
            "composite": "_test_mixed_composite",
        }
        reserved = "order"
        try:
            for name in tables.values():
                conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(reserved)))
            conn.commit()

            conn.execute(
                f"CREATE TABLE {tables['with_seq']} (id SERIAL PRIMARY KEY, name TEXT)"
            )
            conn.execute(
                f"CREATE TABLE {tables['no_seq']} (id INTEGER PRIMARY KEY, name TEXT)"
            )
            conn.execute(f"""
                CREATE TABLE {tables['composite']} (
                    cluster_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    PRIMARY KEY (cluster_id, keyword)
                )
            """)
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (id SERIAL PRIMARY KEY, name TEXT)"
                ).format(sql.Identifier(reserved))
            )
            conn.commit()

            conn.execute(
                f"INSERT INTO {tables['with_seq']} (id, name) VALUES (10, 'a')"
            )
            conn.execute(
                f"INSERT INTO {tables['no_seq']} (id, name) VALUES (20, 'b')"
            )
            conn.execute(
                f"INSERT INTO {tables['composite']} (cluster_id, keyword) VALUES (1, 'kw')"
            )
            conn.execute(
                sql.SQL("INSERT INTO {} (id, name) VALUES (30, 'c')").format(
                    sql.Identifier(reserved)
                )
            )
            conn.commit()

            # Composite PK first in the explicit list, then a valid table.
            listed = resync_all_sequences(
                conn,
                tables=[
                    (tables["composite"], "id"),
                    (tables["with_seq"], "id"),
                ],
                backend=Backend.POSTGRES,
            )
            listed_map = {r.table: r for r in listed}
            assert listed_map[tables["composite"]].error is not None
            assert listed_map[tables["with_seq"]].error is None
            assert listed_map[tables["with_seq"]].new_value == 11
            still_ok = conn.execute("SELECT 1 AS ok").fetchone()
            assert still_ok["ok"] == 1

            discovered = resync_all_sequences(conn, backend=Backend.POSTGRES)
            discovered_map = {r.table: r for r in discovered}
            assert tables["composite"] not in discovered_map
            assert tables["with_seq"] in discovered_map
            assert reserved in discovered_map
            assert tables["no_seq"] in discovered_map
            assert discovered_map[tables["with_seq"]].error is None
            assert discovered_map[tables["with_seq"]].new_value == 11
            assert discovered_map[reserved].error is None
            assert discovered_map[reserved].new_value == 31
            assert discovered_map[tables["no_seq"]].error is not None

            ensured = ensure_identity(conn, tables["no_seq"], "id", backend=Backend.POSTGRES)
            assert ensured.error is None
            assert ensured.new_value == 21

            conn.execute(f"INSERT INTO {tables['with_seq']} (name) VALUES ('auto')")
            conn.execute(f"INSERT INTO {tables['no_seq']} (name) VALUES ('auto')")
            conn.execute(
                sql.SQL("INSERT INTO {} (name) VALUES ('auto')").format(
                    sql.Identifier(reserved)
                )
            )
            conn.commit()

            with_seq_id = conn.execute(
                f"SELECT id FROM {tables['with_seq']} WHERE name = 'auto'"
            ).fetchone()["id"]
            no_seq_id = conn.execute(
                f"SELECT id FROM {tables['no_seq']} WHERE name = 'auto'"
            ).fetchone()["id"]
            reserved_id = conn.execute(
                sql.SQL("SELECT id FROM {} WHERE name = 'auto'").format(
                    sql.Identifier(reserved)
                )
            ).fetchone()["id"]
            assert with_seq_id == 11
            assert no_seq_id == 21
            assert reserved_id == 31
        finally:
            for name in tables.values():
                conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(reserved)))
            conn.commit()
            conn.close()

    def test_ensure_identity_sqlite_noop(self):
        """ensure_identity() is a no-op on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                result = ensure_identity(conn, "test", "id", backend=Backend.SQLITE)
                assert result.new_value is None
                assert result.error is None
            finally:
                conn.close()

    def test_ensure_identity_postgres_adds_identity(self, pg_url):
        """ensure_identity() adds IDENTITY and resyncs for columns without sequence."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute("DROP TABLE IF EXISTS _test_ensure_identity")
            conn.commit()
            conn.execute("""
                CREATE TABLE _test_ensure_identity (
                    id INTEGER PRIMARY KEY,
                    name TEXT
                )
            """)
            conn.commit()
            conn.execute("INSERT INTO _test_ensure_identity (id, name) VALUES (10, 'a')")
            conn.commit()
            result = ensure_identity(conn, "_test_ensure_identity", "id", backend=Backend.POSTGRES)
            conn.commit()
            assert result.error is None
            assert result.new_value == 11
            conn.execute("INSERT INTO _test_ensure_identity (name) VALUES ('auto')")
            conn.commit()
            row = conn.execute("SELECT id FROM _test_ensure_identity WHERE name = 'auto'").fetchone()
            assert row["id"] == 11
        finally:
            conn.execute("DROP TABLE IF EXISTS _test_ensure_identity")
            conn.commit()
            conn.close()

    def test_ensure_identity_postgres_idempotent(self, pg_url):
        """ensure_identity() is idempotent - safe to call multiple times."""
        from shopifyseo.db import connect_postgres
        conn = connect_postgres(pg_url)
        try:
            conn.execute("DROP TABLE IF EXISTS _test_ensure_idem")
            conn.commit()
            conn.execute("CREATE TABLE _test_ensure_idem (id SERIAL PRIMARY KEY, name TEXT)")
            conn.commit()
            conn.execute("INSERT INTO _test_ensure_idem (id, name) VALUES (5, 'a')")
            conn.commit()
            result1 = ensure_identity(conn, "_test_ensure_idem", "id", backend=Backend.POSTGRES)
            conn.commit()
            result2 = ensure_identity(conn, "_test_ensure_idem", "id", backend=Backend.POSTGRES)
            conn.commit()
            assert result1.error is None
            assert result2.error is None
            assert result1.new_value == 6
        finally:
            conn.execute("DROP TABLE IF EXISTS _test_ensure_idem")
            conn.commit()
            conn.close()

    def test_resync_sequence_postgres_mixed_case_table(self, pg_url):
        """resync_sequence() works with a mixed-case table name like Order."""
        from shopifyseo.db import connect_postgres
        from psycopg import sql

        conn = connect_postgres(pg_url)
        table = "Order"
        try:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))
            conn.commit()
            conn.execute(
                sql.SQL(
                    "CREATE TABLE {} (id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name TEXT)"
                ).format(sql.Identifier(table))
            )
            conn.commit()
            conn.execute(
                sql.SQL("INSERT INTO {} (id, name) VALUES (7, 'manual')").format(
                    sql.Identifier(table)
                )
            )
            conn.commit()
            result = resync_sequence(conn, table, "id", backend=Backend.POSTGRES)
            assert result.error is None
            assert result.new_value == 8
            seq_name = get_sequence_name(conn, table, "id", backend=Backend.POSTGRES)
            assert seq_name is not None
            conn.execute(
                sql.SQL("INSERT INTO {} (name) VALUES ('auto')").format(sql.Identifier(table))
            )
            conn.commit()
            row = conn.execute(
                sql.SQL("SELECT id FROM {} WHERE name = 'auto'").format(sql.Identifier(table))
            ).fetchone()
            assert row["id"] == 8
        finally:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table)))
            conn.commit()
            conn.close()

    def test_identity_helpers_on_autocommit_connection(self, pg_url):
        """resync/ensure work on get_connection(autocommit=True) via conn.transaction()."""
        conn = get_connection(url=pg_url, autocommit=True)
        try:
            assert conn.autocommit is True
            conn.execute("DROP TABLE IF EXISTS _test_ac_resync")
            conn.execute("DROP TABLE IF EXISTS _test_ac_ensure")
            conn.execute(
                "CREATE TABLE _test_ac_resync ("
                "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, name TEXT)"
            )
            conn.execute("INSERT INTO _test_ac_resync (id, name) VALUES (40, 'manual')")
            result = resync_sequence(conn, "_test_ac_resync", "id", backend=Backend.POSTGRES)
            assert result.error is None
            assert result.new_value == 41

            conn.execute(
                "CREATE TABLE _test_ac_ensure (id INTEGER PRIMARY KEY, name TEXT)"
            )
            conn.execute("INSERT INTO _test_ac_ensure (id, name) VALUES (3, 'x')")
            ensured = ensure_identity(conn, "_test_ac_ensure", "id", backend=Backend.POSTGRES)
            assert ensured.error is None
            assert ensured.new_value == 4

            all_results = resync_all_sequences(
                conn,
                tables=[("_test_ac_resync", "id"), ("_test_ac_ensure", "id")],
                backend=Backend.POSTGRES,
            )
            assert all(r.error is None for r in all_results)
            assert {r.table: r.new_value for r in all_results} == {
                "_test_ac_resync": 41,
                "_test_ac_ensure": 4,
            }
        finally:
            conn.execute("DROP TABLE IF EXISTS _test_ac_resync")
            conn.execute("DROP TABLE IF EXISTS _test_ac_ensure")
            conn.close()


class TestIdentityColumnsConstant:
    """IDENTITY_COLUMNS lists the five tables that need identity on Postgres."""

    def test_identity_columns_are_the_five_integer_pk_tables(self):
        assert IDENTITY_COLUMNS == {
            "rank_checks": "id",
            "rank_requests": "id",
            "robots_snapshots": "id",
            "seo_opportunity_tasks": "id",
            "tracked_keywords": "id",
        }

    def test_seo_change_events_task_id_must_not_get_identity(self):
        assert "seo_change_events" not in IDENTITY_COLUMNS


class TestPercentLiteralRegression:
    """Regression tests for % literal handling with different param scenarios.

    These tests verify that % in SQL (modulo, LIKE patterns) works correctly
    with params=None, params=(), and params=[].
    """

    def test_modulo_params_none_sqlite(self):
        """% modulo with params=None on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 7 % 3 AS r", None, backend=Backend.SQLITE).fetchone()
                assert row["r"] == 1
            finally:
                conn.close()

    def test_modulo_params_empty_tuple_sqlite(self):
        """% modulo with params=() on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 7 % 3 AS r", (), backend=Backend.SQLITE).fetchone()
                assert row["r"] == 1
            finally:
                conn.close()

    def test_modulo_params_empty_list_sqlite(self):
        """% modulo with params=[] on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 7 % 3 AS r", [], backend=Backend.SQLITE).fetchone()
                assert row["r"] == 1
            finally:
                conn.close()

    def test_modulo_params_none_postgres(self, pg_conn):
        """% modulo with params=None on PostgreSQL."""
        row = execute(pg_conn, "SELECT 7 % 3 AS r", None, backend=Backend.POSTGRES).fetchone()
        assert row["r"] == 1

    def test_modulo_params_empty_tuple_postgres(self, pg_conn):
        """% modulo with params=() on PostgreSQL."""
        row = execute(pg_conn, "SELECT 7 % 3 AS r", (), backend=Backend.POSTGRES).fetchone()
        assert row["r"] == 1

    def test_modulo_params_empty_list_postgres(self, pg_conn):
        """% modulo with params=[] on PostgreSQL."""
        row = execute(pg_conn, "SELECT 7 % 3 AS r", [], backend=Backend.POSTGRES).fetchone()
        assert row["r"] == 1

    def test_like_params_none_sqlite(self):
        """LIKE % with params=None on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 'abc' LIKE 'a%' AS m", None, backend=Backend.SQLITE).fetchone()
                assert row["m"] == 1
            finally:
                conn.close()

    def test_like_params_empty_tuple_sqlite(self):
        """LIKE % with params=() on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 'abc' LIKE 'a%' AS m", (), backend=Backend.SQLITE).fetchone()
                assert row["m"] == 1
            finally:
                conn.close()

    def test_like_params_empty_list_sqlite(self):
        """LIKE % with params=[] on SQLite."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn = connect_sqlite(Path(tmpdir) / "test.db")
            try:
                row = execute(conn, "SELECT 'abc' LIKE 'a%' AS m", [], backend=Backend.SQLITE).fetchone()
                assert row["m"] == 1
            finally:
                conn.close()

    def test_like_params_none_postgres(self, pg_conn):
        """LIKE % with params=None on PostgreSQL."""
        row = execute(pg_conn, "SELECT 'abc' LIKE 'a%' AS m", None, backend=Backend.POSTGRES).fetchone()
        assert row["m"] is True

    def test_like_params_empty_tuple_postgres(self, pg_conn):
        """LIKE % with params=() on PostgreSQL."""
        row = execute(pg_conn, "SELECT 'abc' LIKE 'a%' AS m", (), backend=Backend.POSTGRES).fetchone()
        assert row["m"] is True

    def test_like_params_empty_list_postgres(self, pg_conn):
        """LIKE % with params=[] on PostgreSQL."""
        row = execute(pg_conn, "SELECT 'abc' LIKE 'a%' AS m", [], backend=Backend.POSTGRES).fetchone()
        assert row["m"] is True

    def test_literal_percent_in_string_postgres(self, pg_conn):
        """Literal '50%' in INSERT with params works."""
        pg_conn.execute("DROP TABLE IF EXISTS percent_lit_test")
        pg_conn.execute("CREATE TABLE percent_lit_test (id SERIAL PRIMARY KEY, val TEXT)")
        pg_conn.commit()
        execute(pg_conn, "INSERT INTO percent_lit_test (val) VALUES (?)", ("50%",), backend=Backend.POSTGRES)
        pg_conn.commit()
        row = pg_conn.execute("SELECT val FROM percent_lit_test").fetchone()
        assert row["val"] == "50%"
        pg_conn.execute("DROP TABLE percent_lit_test")
        pg_conn.commit()
