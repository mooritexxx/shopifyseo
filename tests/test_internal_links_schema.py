"""Schema tests for internal link graph and suggestion tables."""

import sqlite3

from shopifyseo.dashboard_store import ensure_dashboard_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    return conn


def test_internal_links_table_exists_with_unique_edge():
    conn = _conn()
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'product', 'widget', 'widget', '/products/widget')"
    )
    # Same edge again must be ignorable via OR IGNORE
    conn.execute(
        "INSERT OR IGNORE INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'product', 'widget', 'widget', '/products/widget')"
    )
    rows = conn.execute("SELECT COUNT(*) AS c FROM internal_links").fetchone()
    assert rows["c"] == 1


def test_link_suggestions_unique_pair_and_status_default():
    conn = _conn()
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, score, created_at) "
        "VALUES ('blog_article', 'news/post', 'collection', 'vapes', 'phrase_wrap', 1.5, 123)"
    )
    row = conn.execute("SELECT status, kind FROM link_suggestions").fetchone()
    assert row["status"] == "suggested"
    conn.execute(
        "INSERT OR IGNORE INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, score, created_at) "
        "VALUES ('blog_article', 'news/post', 'collection', 'vapes', 'ai_woven', 9.9, 456)"
    )
    assert conn.execute("SELECT COUNT(*) AS c FROM link_suggestions").fetchone()["c"] == 1
