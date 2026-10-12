"""Postgres ShareLock: request-path schema must not wait on a link rebuild.

On production Postgres, ``CREATE INDEX IF NOT EXISTS`` takes ShareLock on the
table before it checks whether the index exists. ``get_article_draft_run``
(and other request helpers) call ``ensure_dashboard_schema`` on every
connection. A long ``generate_link_suggestions`` writer that sits idle in
transaction on ``link_suggestions`` then blocks those requests.

Cutover owns the PG indexes; ``_sqlite_ddl`` skips that DDL when
``DATABASE_URL`` is a postgres URL. Testdb keeps ``DATABASE_URL`` unset, so
the lock test forces the production gate with a monkeypatch.
"""
from __future__ import annotations

import threading

import pytest

from shopifyseo.dashboard_store import (
    create_article_draft_run,
    ensure_dashboard_schema,
    get_article_draft_run,
)
from shopifyseo.db import is_lock_error, is_operational_error

DASHBOARD_STORE_INDEX_NAMES = (
    "idx_gsc_query_dimension_lookup",
    "idx_gsc_page_daily_object",
    "idx_keyword_metrics_keyword_lower",
    "idx_keyword_page_map_keyword_lower",
    "idx_competitor_gaps_keyword_lower",
    "idx_keyword_page_map_object",
    "idx_embeddings_type",
    "idx_api_usage_log_created",
    "idx_internal_links_target",
    "idx_link_suggestions_status",
    "idx_link_suggestion_events_created",
    "idx_link_suggestion_events_suggestion",
)


def _index_names(conn, *, postgres: bool) -> set[str]:
    if postgres:
        rows = conn.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
        ).fetchall()
        return {row["indexname"] for row in rows}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
    ).fetchall()
    return {row["name"] for row in rows}


def test_sqlite_and_testdb_still_create_dashboard_indexes(testdb, db_conn):
    """SQLite (and testdb PG) bootstrap is unchanged: indexes are still created."""
    ensure_dashboard_schema(db_conn)
    names = _index_names(db_conn, postgres=testdb.is_postgres)
    missing = [name for name in DASHBOARD_STORE_INDEX_NAMES if name not in names]
    assert missing == []


def test_ensure_dashboard_schema_skips_index_ddl_on_postgres_runtime(db_conn, monkeypatch):
    """Production ``sqlite_runtime_ddl()`` is false; request-path index DDL is skipped."""
    from shopifyseo import dashboard_store as ds

    ensure_dashboard_schema(db_conn)
    monkeypatch.setattr(ds, "sqlite_runtime_ddl", lambda: False)
    seen: list[str] = []
    real_execute = db_conn.execute

    def spy(sql, *args, **kwargs):
        text = sql if isinstance(sql, str) else str(sql)
        if "CREATE INDEX" in text.upper():
            seen.append(text)
        return real_execute(sql, *args, **kwargs)

    db_conn.execute = spy
    ensure_dashboard_schema(db_conn)
    leaked = [sql for sql in seen if any(name in sql for name in DASHBOARD_STORE_INDEX_NAMES)]
    assert leaked == []


def test_get_article_draft_run_answers_while_rebuild_holds_link_suggestions(
    testdb, db_connect, monkeypatch
):
    """Request helper must answer while a rebuild-shaped writer holds link_suggestions."""
    if not testdb.is_postgres:
        pytest.skip("SQLite write lock is database-wide; this is a Postgres ShareLock race")

    from shopifyseo import dashboard_store as ds

    writer = testdb.connect(autocommit=False)
    reader = db_connect()
    control = db_connect()
    worker: threading.Thread | None = None
    try:
        ensure_dashboard_schema(writer)
        run_id = create_article_draft_run(writer, {"topic": "rebuild lock"})

        # Testdb keeps DATABASE_URL unset; force the production cutover gate.
        monkeypatch.setattr(ds, "sqlite_runtime_ddl", lambda: False)

        # Same shape as generate_link_suggestions: write, then sit idle in txn.
        writer.execute("DELETE FROM link_suggestions WHERE status = 'suggested' AND id < 0")

        control.execute("SET lock_timeout = '500ms'")
        blocked = False
        try:
            control.execute(
                "CREATE INDEX IF NOT EXISTS idx_link_suggestions_status "
                "ON link_suggestions (status, score)"
            )
        except Exception as exc:
            assert is_lock_error(exc) or is_operational_error(exc), repr(exc)
            blocked = True
        assert blocked, "CREATE INDEX IF NOT EXISTS should wait on the rebuild ShareLock"

        result: dict = {}

        def _read() -> None:
            try:
                result["run"] = get_article_draft_run(reader, run_id)
            except Exception as exc:
                result["error"] = exc

        worker = threading.Thread(target=_read)
        worker.start()
        worker.join(timeout=5)
        assert not worker.is_alive(), "get_article_draft_run blocked on the rebuild lock"
        assert "error" not in result, result.get("error")
        assert result["run"] is not None
        assert result["run"]["id"] == run_id
        assert result["run"]["request"]["topic"] == "rebuild lock"
    finally:
        try:
            writer.rollback()
        except Exception:
            pass
        writer.close()
        if worker is not None and worker.is_alive():
            worker.join(timeout=5)
