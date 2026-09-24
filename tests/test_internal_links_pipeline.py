"""Tests for the link suggestion pipeline: suppression, scoring, caps."""

import sqlite3

from shopifyseo.internal_links.pipeline import generate_link_suggestions


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0, seo_title TEXT, seo_description TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            anchor_text TEXT,
            href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
            anchor_phrase TEXT,
            ai_anchor_html TEXT,
            source_body_hash TEXT,
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('suggested', 'applied', 'dismissed', 'undone')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE TABLE keyword_page_map (
            keyword TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_handle TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gsc',
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0,
            gsc_position REAL,
            is_primary INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (keyword, object_type, object_handle)
        );
        CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, content_type TEXT,
            primary_keyword TEXT, content_brief TEXT, total_volume INTEGER DEFAULT 0,
            avg_difficulty REAL DEFAULT 0, avg_opportunity REAL DEFAULT 0, priority_score REAL DEFAULT 0,
            match_type TEXT, match_handle TEXT, match_title TEXT, generated_at TEXT
        );
        CREATE TABLE cluster_keywords (cluster_id INTEGER, keyword TEXT, PRIMARY KEY (cluster_id, keyword));
        """
    )
    return conn


def _seed(conn):
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('news', 'post', 'Post', '<p>All about ceramic tanks and more.</p>', 100)"
    )
    conn.execute(
        "INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')"
    )
    conn.execute(
        "INSERT INTO products (handle, title, status) VALUES ('widget', 'Widget Pro', 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO products (handle, title, status) VALUES ('hidden', 'Hidden', 'DRAFT')"
    )
    conn.commit()


def _fake_related(conn, object_type, handle, top_k=10, type_quotas=None):
    if (object_type, handle) == ("blog_article", "news/post"):
        return [
            {"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9},
            {"object_type": "product", "object_handle": "widget", "score": 0.8},
            {"object_type": "product", "object_handle": "hidden", "score": 0.95},
            {"object_type": "product", "object_handle": "low-sim", "score": 0.2},
        ]
    return []


def test_generates_phrase_wrap_and_ai_woven_and_suppresses():
    conn = _conn()
    _seed(conn)
    n = generate_link_suggestions(conn, related_fn=_fake_related)
    rows = conn.execute(
        "SELECT * FROM link_suggestions ORDER BY score DESC"
    ).fetchall()
    handles = {(r["target_type"], r["target_handle"]) for r in rows}
    # draft product and below-threshold target suppressed
    assert ("product", "hidden") not in handles
    assert ("product", "low-sim") not in handles
    assert n == len(rows) == 2
    by_target = {r["target_handle"]: r for r in rows}
    # 'Ceramic Tanks' title appears in body text -> phrase_wrap
    assert by_target["ceramic-tanks"]["kind"] == "phrase_wrap"
    assert by_target["ceramic-tanks"]["anchor_phrase"] == "ceramic tanks"
    # 'Widget Pro' does not appear -> ai_woven placeholder
    assert by_target["widget"]["kind"] == "ai_woven"
    assert by_target["widget"]["anchor_phrase"] is None


def test_existing_link_and_dismissed_are_suppressed_and_rerun_is_stable():
    conn = _conn()
    _seed(conn)
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', '/collections/ceramic-tanks')"
    )
    # Pass rebuild_graph=False to keep the manually seeded internal_link
    generate_link_suggestions(conn, related_fn=_fake_related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle, status FROM link_suggestions").fetchall()
    assert [r["target_handle"] for r in rows] == ["widget"]
    conn.execute("UPDATE link_suggestions SET status = 'dismissed' WHERE target_handle = 'widget'")
    conn.commit()
    # Second run: dismissed suggestion should remain with its status, not be re-created
    generate_link_suggestions(conn, related_fn=_fake_related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle, status FROM link_suggestions").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "dismissed"


def test_traffic_weighted_scoring_orders_high_traffic_sources_first():
    conn = _conn()
    _seed(conn)
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('news', 'quiet', 'Quiet', '<p>ceramic tanks here too</p>', 0)"
    )
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if object_type == "blog_article":
            return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
        return []

    generate_link_suggestions(conn, related_fn=related)
    rows = conn.execute(
        "SELECT source_handle, score FROM link_suggestions ORDER BY score DESC"
    ).fetchall()
    assert rows[0]["source_handle"] == "news/post"  # 100 clicks beats 0 clicks
    assert rows[0]["score"] > rows[1]["score"]


def test_rebuild_replaces_stale_suggested_rows_with_fresh_hashes():
    """Rebuild should delete pending suggestions with stale hashes and re-insert fresh ones.

    Regression test for bug: INSERT OR IGNORE left stale source_body_hash values
    when body content changed, causing Apply to fail even after Rebuild + Generate.
    """
    import hashlib

    def _hash_body(body: str) -> str:
        return hashlib.sha256((body or "").encode("utf-8")).hexdigest()

    conn = _conn()
    old_body = "<p>Old body content about ceramic tanks.</p>"
    new_body = "<p>New body content about ceramic tanks.</p>"
    old_hash = _hash_body(old_body)
    new_hash = _hash_body(new_body)

    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('news', 'post', 'Post', ?, 100)",
        (old_body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 1)",
        (old_hash,),
    )
    conn.commit()

    conn.execute("UPDATE blog_articles SET body = ? WHERE handle = 'post'", (new_body,))
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if (object_type, handle) == ("blog_article", "news/post"):
            return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
        return []

    generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)

    row = conn.execute(
        "SELECT source_body_hash FROM link_suggestions WHERE source_handle = 'news/post'"
    ).fetchone()
    assert row["source_body_hash"] == new_hash, "Rebuild should update hash to current body"
    assert row["source_body_hash"] != old_hash, "Old stale hash should be replaced"


def test_rebuild_preserves_applied_and_dismissed_suggestions():
    """Rebuild should only delete 'suggested' rows, preserving 'applied' and 'dismissed'."""
    conn = _conn()
    _seed(conn)

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if (object_type, handle) == ("blog_article", "news/post"):
            return [
                {"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9},
                {"object_type": "product", "object_handle": "widget", "score": 0.8},
            ]
        return []

    generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
    conn.execute("UPDATE link_suggestions SET status = 'applied' WHERE target_handle = 'ceramic-tanks'")
    conn.execute("UPDATE link_suggestions SET status = 'dismissed' WHERE target_handle = 'widget'")
    conn.commit()

    generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)

    applied = conn.execute(
        "SELECT status FROM link_suggestions WHERE target_handle = 'ceramic-tanks'"
    ).fetchone()
    dismissed = conn.execute(
        "SELECT status FROM link_suggestions WHERE target_handle = 'widget'"
    ).fetchone()
    assert applied["status"] == "applied", "Applied suggestions should be preserved"
    assert dismissed["status"] == "dismissed", "Dismissed suggestions should be preserved"


def test_progress_tracks_error_on_failure():
    """Pipeline should track error state on failure so the UI can display it.
    
    Regression test for the bug where the pipeline's finally block always set
    progress to idle, even on failure, causing the UI to toast "Link rebuild
    complete" when the rebuild actually failed.
    """
    import pytest
    from unittest.mock import patch
    from shopifyseo.internal_links.pipeline import internal_link_sync_progress, _set_progress
    
    conn = _conn()
    _seed(conn)
    
    # Reset progress state
    _set_progress(running=False, stage="idle", done=0, total=0, error=None, finished_at=None)
    
    # Patch rebuild_internal_link_graph to raise an error (this error propagates)
    with patch("shopifyseo.internal_links.pipeline.rebuild_internal_link_graph") as mock_rebuild:
        mock_rebuild.side_effect = RuntimeError("Intentional graph rebuild failure")
        
        # Pipeline should propagate the error
        with pytest.raises(RuntimeError, match="Intentional graph rebuild failure"):
            generate_link_suggestions(conn, rebuild_graph=True)
    
    # Progress should show error state
    progress = internal_link_sync_progress()
    assert progress["running"] is False
    assert progress["error"] is not None
    assert "Intentional graph rebuild failure" in progress["error"]
    assert progress["finished_at"] is not None


def test_progress_clears_error_on_success():
    """Pipeline should clear error state on successful completion."""
    from shopifyseo.internal_links.pipeline import internal_link_sync_progress, _set_progress
    
    conn = _conn()
    _seed(conn)
    
    # Set a fake prior error
    _set_progress(error="Previous failure")
    
    # Run pipeline successfully
    generate_link_suggestions(conn, related_fn=_fake_related)
    
    # Error should be cleared
    progress = internal_link_sync_progress()
    assert progress["error"] is None
    assert progress["running"] is False


def test_db_lock_retry_logic():
    """_run_with_db_lock_retry should retry on database locked errors."""
    import sqlite3
    from shopifyseo.internal_links.pipeline import _run_with_db_lock_retry
    
    call_count = [0]
    
    def flaky_fn():
        call_count[0] += 1
        if call_count[0] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "success"
    
    result = _run_with_db_lock_retry(flaky_fn, max_retries=5)
    assert result == "success"
    assert call_count[0] == 3


def test_db_lock_retry_exhaustion_raises():
    """_run_with_db_lock_retry should raise after exhausting retries."""
    import pytest
    import sqlite3
    from shopifyseo.internal_links.pipeline import _run_with_db_lock_retry
    
    call_count = [0]
    
    def always_locked():
        call_count[0] += 1
        raise sqlite3.OperationalError("database is locked")
    
    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        _run_with_db_lock_retry(always_locked, max_retries=3)
    
    assert call_count[0] == 3
