"""PostgreSQL production-runtime smoke: no testdb adapter, DATABASE_URL set.

Skipped unless DATABASE_URL is a postgres URL (backend-postgres extra job).
Builds schema from the SQLite bootstrap DDL (rewritten) plus cutover post-load
indexes/triggers, seeds a handful of rows, then drives FastAPI TestClient
routes and in-process sync_products / run_job. A socket audit hook allows
only the Postgres host.
"""
from __future__ import annotations

import json
import os
import sqlite3
import struct
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import pytest

_URL = os.environ.get("DATABASE_URL", "").strip()
_IS_PG = _URL.lower().startswith(("postgresql://", "postgres://"))

pytestmark = pytest.mark.skipif(
    not _IS_PG,
    reason="PG runtime smoke requires DATABASE_URL postgres (backend-postgres extra step)",
)

ROOT = Path(__file__).resolve().parents[1]


def _pg_host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 5432
    return host, port


def _install_pg_only_socket_guard(url: str) -> None:
    host, port = _pg_host_port(url)
    allowed_hosts = {host, "127.0.0.1", "localhost", "::1", None, ""}

    def _hook(event, args):
        if event == "socket.connect":
            addr = args[1] if len(args) > 1 else None
            if isinstance(addr, tuple) and len(addr) >= 2:
                dest_host, dest_port = addr[0], addr[1]
                if dest_host in allowed_hosts and int(dest_port) == int(port):
                    return
                raise PermissionError(f"pg-runtime-smoke blocked socket.connect {addr!r}")
        elif event == "socket.getaddrinfo":
            lookup = args[0] if args else None
            if isinstance(lookup, bytes):
                lookup = lookup.decode()
            if lookup not in allowed_hosts:
                raise PermissionError(f"pg-runtime-smoke blocked DNS {lookup!r}")

    sys.addaudithook(_hook)


def _sqlite_schema_statements() -> list[str]:
    """Create the live SQLite schema, then return CREATE statements."""
    saved = os.environ.pop("DATABASE_URL", None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schema.sqlite3"
            conn = sqlite3.connect(path)
            try:
                from backend.app.services.team_tasks import ensure_schema as ensure_tasks
                from shopifyseo.dashboard_store import ensure_dashboard_schema

                ensure_dashboard_schema(conn)
                ensure_tasks(conn)
                conn.commit()
                rows = conn.execute(
                    """
                    SELECT type, sql FROM sqlite_master
                    WHERE sql IS NOT NULL
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY CASE type
                        WHEN 'table' THEN 0
                        WHEN 'index' THEN 1
                        WHEN 'trigger' THEN 2
                        ELSE 3
                    END, name
                    """
                ).fetchall()
            finally:
                conn.close()
    finally:
        if saved is not None:
            os.environ["DATABASE_URL"] = saved
    statements = []
    for typ, sql in rows:
        if typ == "trigger" and "RAISE" in sql.upper():
            continue
        statements.append(sql)
    return statements


def _apply_sqlite_schema_to_pg(pg_conn) -> None:
    from shopifyseo.cutover.sql import apply_sql_file
    from tests.db_support import rewrite_sqlite_ddl_for_postgres

    for sql in _sqlite_schema_statements():
        rewritten = rewrite_sqlite_ddl_for_postgres(sql)
        pg_conn.execute(rewritten)
    pg_conn.commit()
    apply_sql_file(pg_conn, ROOT / "scripts" / "pg_cutover" / "post_load_constraints.sql")


def _embed_blob(*values: float) -> bytes:
    return struct.pack(f"{len(values)}f", *values)


def _seed(pg_conn) -> dict[str, object]:
    now = "2026-10-05 18:00:00"
    embed = _embed_blob(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8)
    embed_b = _embed_blob(0.11, 0.21, 0.31, 0.41, 0.51, 0.61, 0.71, 0.81)

    pg_conn.execute(
        """
        INSERT INTO products (
            shopify_id, title, handle, tags_json, options_json, raw_json, synced_at,
            status, description_html
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gid://shopify/Product/1",
            "Smoke Product",
            "smoke-product",
            "[]",
            "[]",
            "{}",
            now,
            "ACTIVE",
            "<p>body</p>",
        ),
    )
    pg_conn.execute(
        """
        INSERT INTO collections (
            shopify_id, title, handle, raw_json, synced_at, description_html
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("gid://shopify/Collection/1", "Smoke Collection", "smoke-collection", "{}", now, "<p>c</p>"),
    )
    pg_conn.execute(
        """
        INSERT INTO pages (
            shopify_id, title, handle, raw_json, synced_at, body
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("gid://shopify/Page/1", "Smoke Page", "smoke-page", "{}", now, "<p>p</p>"),
    )
    pg_conn.execute(
        """
        INSERT INTO blogs (
            shopify_id, title, handle, tags_json, raw_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("gid://shopify/Blog/1", "Smoke Blog", "smoke-blog", "[]", "{}", now),
    )
    pg_conn.execute(
        """
        INSERT INTO blog_articles (
            shopify_id, blog_shopify_id, blog_handle, title, handle,
            tags_json, raw_json, synced_at, is_published, body
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gid://shopify/Article/1",
            "gid://shopify/Blog/1",
            "smoke-blog",
            "Smoke Article",
            "smoke-article",
            "[]",
            "{}",
            now,
            1,
            "<p>a</p>",
        ),
    )
    pg_conn.execute(
        """
        INSERT INTO gsc_query_rows (
            object_type, object_handle, url, query, clicks, impressions, ctr, position
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "product",
            "smoke-product",
            "https://example.com/products/smoke-product",
            "smoke keyword",
            10,
            100,
            0.1,
            4.2,
        ),
    )
    pg_conn.execute(
        "INSERT INTO tracked_keywords (term, active) VALUES (?, ?)",
        ("smoke rank term", 1),
    )
    pg_conn.execute(
        """
        INSERT INTO link_suggestions (
            source_type, source_handle, target_type, target_handle, kind,
            score, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("product", "smoke-product", "collection", "smoke-collection", "phrase_wrap", 0.5, "suggested", 1),
    )
    task_json = json.dumps(
        {
            "id": 1,
            "owner": "salar",
            "status": "todo",
            "priority": "normal",
            "version": 1,
            "created_at": now,
            "last_log_at": now,
            "completed_at": None,
            "title": "Smoke task",
            "proof": "",
        }
    )
    pg_conn.execute(
        """
        INSERT INTO team_tasks (
            owner, status, priority, version, created_at, last_log_at, data_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("salar", "todo", "normal", 1, now, now, task_json),
    )
    pg_conn.execute(
        """
        INSERT INTO article_ideas (
            suggested_title, brief, primary_keyword, supporting_keywords, created_at, status
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("Smoke idea", "brief", "smoke keyword", "[]", int(time.time()), "idea"),
    )
    idea_id = pg_conn.execute("SELECT id FROM article_ideas LIMIT 1").fetchone()[0]
    task_id = pg_conn.execute("SELECT id FROM team_tasks LIMIT 1").fetchone()[0]
    sug_id = pg_conn.execute("SELECT id FROM link_suggestions LIMIT 1").fetchone()[0]
    kw_id = pg_conn.execute("SELECT id FROM tracked_keywords LIMIT 1").fetchone()[0]

    for obj_type, handle, blob in (
        ("product", "smoke-product", embed),
        ("collection", "smoke-collection", embed_b),
        ("page", "smoke-page", embed),
        ("blog_article", "smoke-blog/smoke-article", embed_b),
        ("article_idea", str(idea_id), embed),
        ("keyword", "smoke keyword", embed_b),
        ("competitor_page", "example.com:https://example.com/x", embed),
        ("gsc_queries", "product:smoke-product", embed_b),
    ):
        pg_conn.execute(
            """
            INSERT INTO embeddings (
                object_type, object_handle, chunk_index, text_hash, model_version,
                embedding, source_text_preview, token_count, updated_at
            ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)
            """,
            (obj_type, handle, "hash", "v1", blob, handle, 8, now),
        )

    pg_conn.execute(
        """
        INSERT INTO google_api_cache (
            cache_key, cache_type, object_type, object_handle, url, strategy,
            payload_json, fetched_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "summary-seed",
            "gsc_summary",
            None,
            None,
            None,
            None,
            json.dumps({"rows": [], "_cache": {"exists": True}}),
            int(time.time()),
            int(time.time()) + 86400,
        ),
    )
    pg_conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, ?)",
        (
            "target_keywords",
            json.dumps(
                {
                    "items": [
                        {
                            "keyword": "smoke keyword",
                            "status": "approved",
                            "content_type": "product",
                            "intent": "transactional",
                        }
                    ]
                }
            ),
        ),
    )
    pg_conn.commit()
    return {
        "idea_id": int(idea_id),
        "task_id": int(task_id),
        "suggestion_id": int(sug_id),
        "keyword_id": int(kw_id),
    }


@pytest.fixture(scope="module")
def smoke_ids():
    if "runtime" not in _URL and "test" not in _URL:
        pytest.skip("refusing to wipe a non-test DATABASE_URL")
    from shopifyseo.db import connect_postgres

    conn = connect_postgres(_URL)
    try:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
        conn.commit()
        _apply_sqlite_schema_to_pg(conn)
        ids = _seed(conn)
    finally:
        conn.close()

    from backend.app import db as app_db

    app_db._bootstrapped_paths.clear()
    _install_pg_only_socket_guard(_URL)
    yield ids
    app_db._bootstrapped_paths.clear()


@pytest.fixture(scope="module")
def client(smoke_ids):
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_production_connection_class(smoke_ids):
    import psycopg

    from backend.app import db as app_db
    from shopifyseo.db import apply_postgres_runtime_compat
    from shopifyseo.db.pg_runtime import _RUNTIME_FLAG

    app_db._bootstrapped_paths.clear()
    conn = app_db.open_db_connection()
    try:
        assert isinstance(conn, psycopg.Connection)
        assert type(conn) is psycopg.Connection
        assert getattr(conn, _RUNTIME_FLAG, False)
        apply_postgres_runtime_compat(conn)
        assert hasattr(conn, "executemany")
    finally:
        conn.close()

    with app_db.db_conn() as conn2:
        assert isinstance(conn2, psycopg.Connection)
        app_db._bootstrap_once(conn2, app_db.DB_PATH)


WEB_HEADERS = {
    "sec-fetch-site": "same-origin",
    "sec-fetch-mode": "cors",
    "x-task-web": "1",
}


def _assert_not_5xx(response, label: str) -> None:
    assert response.status_code < 500, f"{label} -> {response.status_code}: {response.text[:400]}"


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("GET", "/api/products/smoke-product", {}),
        ("GET", "/api/collections/smoke-collection", {}),
        ("GET", "/api/pages/smoke-page", {}),
        ("GET", "/api/articles/smoke-blog/smoke-article", {}),
        ("GET", "/api/summary", {}),
        ("GET", "/api/web/tasks", {"headers": WEB_HEADERS}),
        ("GET", "/api/internal-links/summary", {}),
        ("GET", "/api/internal-links/suggestions", {}),
        ("GET", "/api/keywords/target", {}),
        ("GET", "/api/rankings/", {}),
        ("GET", "/api/opportunities", {}),
        ("GET", "/api/embeddings/similar/product/smoke-product", {}),
        ("GET", "/api/embeddings/semantic-keywords/product/smoke-product", {}),
        ("GET", "/api/embeddings/competitive-gaps/product/smoke-product", {}),
        ("GET", "/api/embeddings/cannibalization", {"params": {"threshold": 0.01}}),
        ("GET", "/api/embeddings/status", {}),
        ("GET", "/api/settings", {}),
    ],
)
def test_route_smoke_get(client, smoke_ids, method, path, kwargs):
    if "{idea}" in path:
        path = path.replace("{idea}", str(smoke_ids["idea_id"]))
    response = client.request(method, path, **kwargs)
    _assert_not_5xx(response, f"{method} {path}")


def test_article_idea_cannibalization_check(client, smoke_ids):
    response = client.get(f"/api/article-ideas/{smoke_ids['idea_id']}/cannibalization-check")
    _assert_not_5xx(response, "cannibalization-check")


def test_gsc_crossref_decimal(client, smoke_ids):
    response = client.post("/api/keywords/target/gsc-crossref")
    _assert_not_5xx(response, "gsc-crossref")
    body = response.json()
    assert body.get("ok") is True


def test_rankings_add_delete_keyword(client, smoke_ids):
    added = client.post("/api/rankings/keywords", json={"term": "smoke extra term"})
    _assert_not_5xx(added, "add keyword")
    kid = added.json()["data"]["id"]
    deleted = client.delete(f"/api/rankings/keywords/{kid}")
    _assert_not_5xx(deleted, "delete keyword")


def test_settings_post(client, smoke_ids):
    current = client.get("/api/settings")
    _assert_not_5xx(current, "settings GET")
    payload = {"store_name": "Smoke Store"}
    response = client.post("/api/settings", json=payload)
    _assert_not_5xx(response, "settings POST")


def test_web_task_detail(client, smoke_ids):
    response = client.get(f"/api/web/tasks/{smoke_ids['task_id']}", headers=WEB_HEADERS)
    _assert_not_5xx(response, "web task detail")


def test_sync_products_canned_nodes(smoke_ids):
    from shopifyseo.dashboard_store import DB_PATH
    from shopifyseo.shopify_catalog_sync.products import sync_products

    node = {
        "id": "gid://shopify/Product/99",
        "legacyResourceId": "99",
        "handle": "canned-sync-product",
        "title": "Canned",
        "vendor": "",
        "productType": "",
        "status": "ACTIVE",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-10-05T00:00:00Z",
        "publishedAt": "2026-01-01T00:00:00Z",
        "descriptionHtml": "",
        "seo": {"title": "", "description": ""},
        "tags": [],
        "totalInventory": 1,
        "tracksInventory": True,
        "onlineStoreUrl": "https://example.com/products/canned-sync-product",
        "variants": {"edges": []},
        "media": {"edges": []},
        "images": {"edges": []},
        "metafields": {"edges": []},
        "collections": {"edges": []},
        "options": [],
    }
    with mock.patch(
        "shopifyseo.shopify_catalog_sync.products.fetch_metaobjects_by_ids",
        return_value=[],
    ):
        result = sync_products(DB_PATH, 50, products=[node])
    assert result is not None


def test_run_job_python_bools(smoke_ids):
    import backend.app.services.rank_tracking as rt
    from backend.app import db as app_db

    fake_result = {
        "position": 7,
        "ranking_url": "https://example.com/x",
        "top1_domain": "a.ca",
        "top2_domain": "b.ca",
        "top3_domain": "c.ca",
        "pages_checked": 1,
        "checked_depth": 10,
        "status": "ok",
        "error": None,
        "coverage_complete": True,
        "cancelled": False,
    }

    def fake_check(term, max_pages, key, before):
        before()
        return dict(fake_result)

    conn = app_db.open_db_connection()
    try:
        kid = conn.execute("SELECT id FROM tracked_keywords ORDER BY id LIMIT 1").fetchone()[0]
        with mock.patch.object(rt, "remaining_credits", return_value=100000), mock.patch.object(
            rt, "check_term", side_effect=fake_check
        ):
            job = rt.start_job(conn, [kid], 1, "smoke-job", launch=False)
            terms = rt.keywords(conn, [kid])
            rt.run_job(job["job_id"], terms, 1, "fake-key")
            st = conn.execute(
                "SELECT status, error FROM rank_jobs WHERE id = ?",
                (job["job_id"],),
            ).fetchone()
        assert st["status"] == "complete", st["error"]
    finally:
        conn.close()
