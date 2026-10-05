"""Schema tests for internal link graph and suggestion tables."""

from shopifyseo.dashboard_store import ensure_dashboard_schema


def _conn(db_conn):
    ensure_dashboard_schema(db_conn)
    return db_conn


def test_internal_links_table_exists_with_unique_edge(db_conn):
    conn = _conn(db_conn)
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


def test_link_suggestions_unique_pair_and_status_default(db_conn):
    conn = _conn(db_conn)
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


def test_old_ai_responses_are_retired_once_without_deleting_suggestions(db_conn):
    from shopifyseo.internal_links.store import ensure_schema
    conn = db_conn
    conn.executescript("CREATE TABLE link_suggestions(id INTEGER PRIMARY KEY, kind TEXT, ai_anchor_html TEXT); INSERT INTO link_suggestions VALUES (1,'ai_woven','<p>Legacy whole body</p>');")
    ensure_schema(conn)
    row = conn.execute('SELECT * FROM link_suggestions').fetchone()
    assert row['id'] == 1 and row['ai_anchor_html'] is None and row['ai_edit_json'] is None
    conn.execute('UPDATE link_suggestions SET ai_edit_json=\'{"anchor_phrase":"phrase"}\'')
    ensure_schema(conn)
    assert conn.execute('SELECT ai_edit_json FROM link_suggestions').fetchone()[0]
