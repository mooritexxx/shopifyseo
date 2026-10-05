"""Plan 7a: dual-backend fixture behaviour (SQLite default, Postgres when URL set)."""
from __future__ import annotations

import os
import sqlite3

import pytest

from shopifyseo.db import Backend, NOW_TEXT_PATTERN, PG_NOW_TEXT_SQL, get_connection, table_columns, table_exists
from db_support import make_testdb, postgres_test_url, resolve_backend, rewrite_sqlite_ddl_for_postgres


class TestPostgresTestUrlHelper:
    def test_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert postgres_test_url() is None
        assert resolve_backend() is Backend.SQLITE

    def test_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_DATABASE_URL", "  ")
        assert postgres_test_url() is None

    def test_postgres_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "postgresql://shopifyseo@127.0.0.1:5433/shopifyseo_test"
        monkeypatch.setenv("TEST_DATABASE_URL", url)
        assert postgres_test_url() == url
        assert resolve_backend() is Backend.POSTGRES

    def test_postgres_scheme_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        url = "postgres://shopifyseo@127.0.0.1:5433/shopifyseo_test"
        monkeypatch.setenv("TEST_DATABASE_URL", url)
        assert postgres_test_url() == url

    def test_ignores_sqlite_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_DATABASE_URL", "sqlite:///tmp/x.sqlite3")
        assert postgres_test_url() is None
        assert resolve_backend() is Backend.SQLITE


class TestDbConnFixture:
    def test_backend_matches_env(self, db_backend: Backend) -> None:
        url = os.environ.get("TEST_DATABASE_URL", "").strip().lower()
        if url.startswith(("postgresql://", "postgres://")):
            assert db_backend is Backend.POSTGRES
        else:
            assert db_backend is Backend.SQLITE

    def test_row_access(self, db_conn) -> None:
        row = db_conn.execute("SELECT 1 AS val").fetchone()
        assert row["val"] == 1
        assert row[0] == 1

    def test_opens_via_get_connection(self, testdb, db_conn) -> None:
        if testdb.is_postgres:
            import psycopg

            assert isinstance(db_conn, psycopg.Connection)
            assert not isinstance(db_conn, sqlite3.Connection)
        else:
            assert isinstance(db_conn, sqlite3.Connection)
            assert testdb.path is not None
            assert testdb.path.exists()

    def test_sqlite_default_is_temp_file(self, testdb) -> None:
        if testdb.is_postgres:
            pytest.skip("TEST_DATABASE_URL points at Postgres")
        assert testdb.path is not None
        conn = testdb.connect()
        try:
            conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        assert testdb.path.is_file()

    def test_postgres_timezone_utc(self, db_conn, testdb) -> None:
        if not testdb.is_postgres:
            pytest.skip("SHOW timezone is Postgres-only")
        row = db_conn.execute("SHOW timezone").fetchone()
        assert str(row[0]).upper() == "UTC"

    def test_current_timestamp_naive_utc_text(self, db_conn, testdb) -> None:
        db_conn.execute(
            "CREATE TABLE ts_probe (id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        db_conn.execute("INSERT INTO ts_probe (id) VALUES (1)")
        db_conn.commit()
        row = db_conn.execute("SELECT ts FROM ts_probe WHERE id = 1").fetchone()
        assert NOW_TEXT_PATTERN.match(str(row["ts"]))

    def test_make_testdb_isolates_sequential_databases(self, tmp_path) -> None:
        first = tmp_path / "one"
        second = tmp_path / "two"
        first.mkdir()
        second.mkdir()
        with make_testdb(first) as db1:
            conn = db1.connect()
            try:
                conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")
                conn.execute("INSERT INTO items (id, val) VALUES (1, 'a')")
                conn.commit()
                assert table_exists(conn, "items", backend=db1.backend) is True
            finally:
                conn.close()
        with make_testdb(second) as db2:
            conn = db2.connect()
            try:
                assert table_exists(conn, "items", backend=db2.backend) is False
            finally:
                conn.close()

    def test_db_connect_factory_second_connection(self, testdb, db_connect) -> None:
        conn1 = db_connect()
        conn1.execute("CREATE TABLE vis (id INTEGER PRIMARY KEY, val TEXT)")
        conn1.execute("INSERT INTO vis (id, val) VALUES (1, 'ok')")
        conn1.commit()
        conn2 = db_connect()
        row = conn2.execute("SELECT val FROM vis WHERE id = 1").fetchone()
        assert row["val"] == "ok"
        if testdb.is_postgres:
            assert testdb.schema is not None
            search = conn2.execute("SHOW search_path").fetchone()[0]
            assert testdb.schema in str(search)

    def test_factory_matches_production_get_connection(self, testdb) -> None:
        if testdb.is_postgres:
            conn = get_connection(url=testdb.url)
            try:
                row = conn.execute("SHOW timezone").fetchone()
                assert str(row[0]).upper() == "UTC"
            finally:
                conn.close()
        else:
            conn = get_connection(path=testdb.path)
            try:
                assert isinstance(conn, sqlite3.Connection)
            finally:
                conn.close()

    def test_module_scoped_fixture_reuses_schema(self, testdb_module, db_conn_module) -> None:
        db_conn_module.execute(
            "CREATE TABLE IF NOT EXISTS module_probe (id INTEGER PRIMARY KEY)"
        )
        db_conn_module.commit()
        assert table_exists(
            db_conn_module, "module_probe", backend=testdb_module.backend
        ) is True

    def test_question_placeholders_and_table_columns_follow_connection(self, testdb, db_conn) -> None:
        """Plan 7b: leftover ``?`` SQL and ``table_columns(conn)`` must work with DATABASE_URL unset."""
        db_conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, val TEXT)")
        db_conn.execute("INSERT INTO items (id, val) VALUES (?, ?)", (1, "ok"))
        db_conn.commit()
        row = db_conn.execute("SELECT val FROM items WHERE id = ?", (1,)).fetchone()
        assert row["val"] == "ok"
        cols = table_columns(db_conn, "items")
        assert "id" in cols and "val" in cols

    def test_executescript_and_autoincrement_on_testdb(self, testdb, db_conn) -> None:
        db_conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS seq_probe (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL
            );
            INSERT OR IGNORE INTO seq_probe (name) VALUES ('a');
            """
        )
        row = db_conn.execute("SELECT name FROM seq_probe WHERE id = ?", (1,)).fetchone()
        assert row["name"] == "a"

    def test_executemany_question_placeholders(self, testdb, db_conn) -> None:
        db_conn.execute("CREATE TABLE many_probe (id INTEGER PRIMARY KEY, val TEXT)")
        db_conn.executemany(
            "INSERT INTO many_probe (id, val) VALUES (?, ?)",
            [(1, "a"), (2, "b")],
        )
        db_conn.commit()
        rows = db_conn.execute("SELECT val FROM many_probe ORDER BY id").fetchall()
        assert [r["val"] for r in rows] == ["a", "b"]

    def test_datetime_now_rewrites_to_plan6_now_text(self) -> None:
        out = rewrite_sqlite_ddl_for_postgres(
            "INSERT INTO t (ts) VALUES (datetime('now'))"
        )
        assert "datetime" not in out.lower()
        assert "CURRENT_TIMESTAMP" not in out
        assert PG_NOW_TEXT_SQL in out

    def test_round_two_arg_rewrites_to_numeric(self) -> None:
        out = rewrite_sqlite_ddl_for_postgres("SELECT ROUND(AVG(score), 2) FROM t")
        assert "::numeric" in out.lower()
        assert "ROUND(" in out.upper() or "round(" in out

    def test_last_insert_rowid_rewrites_to_lastval(self) -> None:
        out = rewrite_sqlite_ddl_for_postgres("SELECT last_insert_rowid()")
        assert "last_insert_rowid" not in out.lower()
        assert "lastval()" in out.lower()

    def test_sqlite_raise_trigger_is_skipped_on_postgres(self, testdb, db_conn) -> None:
        db_conn.executescript(
            """
            CREATE TABLE ev (id INTEGER PRIMARY KEY, actor TEXT);
            CREATE TRIGGER IF NOT EXISTS ev_no_update BEFORE UPDATE ON ev
            BEGIN SELECT RAISE(ABORT, 'Task history is append-only'); END;
            INSERT INTO ev (actor) VALUES ('a');
            """
        )
        if testdb.is_postgres:
            db_conn.execute("UPDATE ev SET actor = 'b'")
            db_conn.commit()
            assert db_conn.execute("SELECT actor FROM ev").fetchone()[0] == "b"
        else:
            with pytest.raises(Exception, match="append-only"):
                db_conn.execute("UPDATE ev SET actor = 'b'")

    def test_last_insert_rowid_after_identity_insert(self, testdb, db_conn) -> None:
        db_conn.execute(
            "CREATE TABLE id_probe (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
        )
        db_conn.execute("INSERT INTO id_probe (name) VALUES (?)", ("a",))
        db_conn.commit()
        row = db_conn.execute("SELECT last_insert_rowid()").fetchone()
        assert int(row[0]) == 1

    def test_insert_or_replace_and_datetime_now(self, testdb, db_conn) -> None:
        db_conn.execute("CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        db_conn.execute(
            "INSERT OR REPLACE INTO service_settings (key, value) VALUES ('k', 'v1')"
        )
        db_conn.execute(
            "INSERT OR REPLACE INTO service_settings (key, value) VALUES ('k', 'v2')"
        )
        db_conn.execute(
            "INSERT INTO service_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("ts", "1"),
        )
        db_conn.commit()
        assert db_conn.execute("SELECT value FROM service_settings WHERE key = 'k'").fetchone()[0] == "v2"
        ts = db_conn.execute("SELECT updated_at FROM service_settings WHERE key = 'ts'").fetchone()[0]
        assert NOW_TEXT_PATTERN.match(ts)

    def test_real_scores_round_trip(self, testdb, db_conn) -> None:
        db_conn.execute("CREATE TABLE score_probe (id INTEGER PRIMARY KEY, score REAL)")
        db_conn.executemany(
            "INSERT INTO score_probe (id, score) VALUES (?, ?)",
            [(1, 100), (2, 99), (3, 99)],
        )
        db_conn.commit()
        rows = db_conn.execute(
            "SELECT id FROM score_probe ORDER BY score DESC, id ASC"
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3]

    def test_blob_and_wide_integer_ddl(self, testdb, db_conn) -> None:
        db_conn.execute(
            "CREATE TABLE cache_probe (id INTEGER PRIMARY KEY, blob BLOB, expires_at INTEGER)"
        )
        db_conn.execute(
            "INSERT INTO cache_probe (id, blob, expires_at) VALUES (?, ?, ?)",
            (1, b"\\x00\\x01", 9_999_999_999),
        )
        db_conn.commit()
        row = db_conn.execute("SELECT blob, expires_at FROM cache_probe WHERE id = ?", (1,)).fetchone()
        assert bytes(row["blob"]) == b"\\x00\\x01"
        assert int(row["expires_at"]) == 9_999_999_999

    def test_pragma_table_info_reports_columns(self, testdb, db_conn) -> None:
        db_conn.execute("CREATE TABLE pragma_probe (handle TEXT PRIMARY KEY, online_store_url TEXT)")
        rows = db_conn.execute("PRAGMA table_info(pragma_probe)").fetchall()
        names = {r[1] if isinstance(r, (list, tuple)) else r["name"] for r in rows}
        assert names == {"handle", "online_store_url"}

    def test_failed_statement_does_not_poison_connection(self, testdb, db_conn) -> None:
        db_conn.execute("CREATE TABLE ok_probe (id INTEGER PRIMARY KEY)")
        try:
            db_conn.execute("INSERT INTO ok_probe (no_such_col) VALUES (1)")
        except Exception:
            pass
        db_conn.execute("INSERT INTO ok_probe (id) VALUES (?)", (1,))
        db_conn.commit()
        assert db_conn.execute("SELECT COUNT(*) FROM ok_probe").fetchone()[0] == 1


@pytest.mark.postgres
class TestPgvectorCiImage:
    """Runs only when TEST_DATABASE_URL is set (CI backend-postgres / local :5433)."""

    def test_vector_extension_present(self, pg_conn) -> None:
        row = pg_conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        assert row is not None, (
            "pgvector extension missing — CI must use pgvector/pgvector:pg17 "
            "(or equivalent PG17 + vector) and CREATE EXTENSION vector"
        )
        assert str(row[0])
