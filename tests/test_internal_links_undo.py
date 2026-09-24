"""Tests for undoing applied link suggestions."""

import hashlib
import sqlite3

import pytest

from shopifyseo.internal_links.apply import (
    undo_suggestion,
    remove_link_from_html,
    check_link_present_in_body,
)
from shopifyseo.dashboard_store import _migrate_link_suggestions_check_constraint


def _hash_body(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def test_remove_link_from_html_removes_single_link():
    html = '<p>Check out our <a href="https://s.com/collections/tanks">ceramic tanks</a> for more.</p>'
    result = remove_link_from_html(html, "https://s.com/collections/tanks")
    assert result == '<p>Check out our ceramic tanks for more.</p>'


def test_remove_link_from_html_keeps_other_links():
    html = (
        '<p>See <a href="https://s.com/collections/tanks">tanks</a> and '
        '<a href="https://s.com/products/vape">vapes</a>.</p>'
    )
    result = remove_link_from_html(html, "https://s.com/collections/tanks")
    assert '<a href="https://s.com/products/vape">vapes</a>' in result
    assert "tanks" in result
    assert '<a href="https://s.com/collections/tanks">' not in result


def test_remove_link_from_html_returns_none_when_not_found():
    html = '<p>No links here.</p>'
    result = remove_link_from_html(html, "https://s.com/collections/tanks")
    assert result is None


def test_remove_link_matches_by_path():
    html = '<p><a href="/collections/tanks">tanks</a></p>'
    result = remove_link_from_html(html, "https://s.com/collections/tanks")
    assert result == '<p>tanks</p>'


def test_check_link_present_in_body():
    html = '<p><a href="https://s.com/collections/tanks">tanks</a></p>'
    assert check_link_present_in_body(html, "https://s.com/collections/tanks") is True
    assert check_link_present_in_body(html, "/collections/tanks") is True
    assert check_link_present_in_body(html, "https://s.com/collections/vapes") is False


def _conn_with_applied() -> sqlite3.Connection:
    """Create a DB with an applied suggestion."""
    # Body WITH the link (after apply)
    body = '<p>Love <a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>.</p>'
    body_hash = _hash_body(body)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]',
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
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
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    # Applied suggestion
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, status, created_at, applied_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 'applied', 1, 1000)",
        (body_hash,),
    )
    # Internal link edge
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'ceramic tanks', 'https://s.com/collections/ceramic-tanks')"
    )
    conn.commit()
    return conn


def test_undo_removes_link_and_updates_state():
    conn = _conn_with_applied()
    pushed = {}

    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}

    sid = conn.execute("SELECT id FROM link_suggestions WHERE status = 'applied'").fetchone()["id"]
    result = undo_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push)
    
    assert result["status"] == "undone"
    assert "ceramic tanks" in pushed["body"]  # text preserved
    assert '<a href="https://s.com/collections/ceramic-tanks">' not in pushed["body"]  # link removed
    
    # Check DB state
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "undone"
    
    # Internal link edge should be removed
    edge_count = conn.execute(
        "SELECT COUNT(*) FROM internal_links WHERE source_handle = 'news/post' AND target_handle = 'ceramic-tanks'"
    ).fetchone()[0]
    assert edge_count == 0
    
    # Local body should be updated
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert '<a href="https://s.com/collections/ceramic-tanks">' not in body


def test_undo_keeps_other_links():
    """Undo should only remove the specific link, not all links."""
    body = (
        '<p>Love <a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a> '
        'and <a href="https://s.com/products/vape-kit">vape kits</a>.</p>'
    )
    body_hash = _hash_body(body)
    
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT);
        CREATE TABLE collections (handle TEXT, title TEXT);
        CREATE TABLE products (handle TEXT, title TEXT, status TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, target_type TEXT, 
            target_handle TEXT, anchor_text TEXT, href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, target_type TEXT,
            target_handle TEXT, kind TEXT, anchor_phrase TEXT, ai_anchor_html TEXT, source_body_hash TEXT,
            score REAL DEFAULT 0, status TEXT DEFAULT 'suggested', created_at INTEGER, applied_at INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('vape-kit', 'Vape Kit', 'ACTIVE')")
    # Two applied suggestions
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, status, created_at, applied_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 'applied', 1, 1000)",
        (body_hash,),
    )
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, status, created_at, applied_at) VALUES "
        "('blog_article', 'news/post', 'product', 'vape-kit', 'phrase_wrap', 'vape kits', ?, 0.9, 'applied', 2, 1001)",
        (body_hash,),
    )
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'ceramic tanks', 'https://s.com/collections/ceramic-tanks')"
    )
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'product', 'vape-kit', 'vape kits', 'https://s.com/products/vape-kit')"
    )
    conn.commit()

    pushed = {}

    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}

    # Undo just the ceramic-tanks link
    sid = conn.execute("SELECT id FROM link_suggestions WHERE target_handle = 'ceramic-tanks'").fetchone()["id"]
    result = undo_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push)
    
    assert result["status"] == "undone"
    # ceramic-tanks link removed
    assert '<a href="https://s.com/collections/ceramic-tanks">' not in pushed["body"]
    # vape-kit link preserved
    assert '<a href="https://s.com/products/vape-kit">vape kits</a>' in pushed["body"]


def test_undo_rejects_non_applied():
    conn = _conn_with_applied()
    # Change status to suggested
    conn.execute("UPDATE link_suggestions SET status = 'suggested'")
    conn.commit()

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(ValueError, match="not applied"):
        undo_suggestion(conn, sid, base_url="https://s.com", push_fn=lambda *a: {})


def test_undo_handles_missing_link_gracefully():
    """Undo should succeed even if the link was manually removed from the body."""
    # Body WITHOUT the link (someone manually removed it)
    body = '<p>Love ceramic tanks.</p>'
    body_hash = _hash_body(body)
    
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT);
        CREATE TABLE collections (handle TEXT, title TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, target_type TEXT, 
            target_handle TEXT, anchor_text TEXT, href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, target_type TEXT,
            target_handle TEXT, kind TEXT, anchor_phrase TEXT, ai_anchor_html TEXT, source_body_hash TEXT,
            score REAL DEFAULT 0, status TEXT DEFAULT 'suggested', created_at INTEGER, applied_at INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, status, created_at, applied_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 'applied', 1, 1000)",
        (body_hash,),
    )
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'ceramic tanks', 'https://s.com/collections/ceramic-tanks')"
    )
    conn.commit()

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    result = undo_suggestion(conn, sid, base_url="https://s.com", push_fn=lambda *a: {})
    
    assert result["status"] == "undone"
    assert result.get("link_not_found") is True
    
    # Status should still be updated
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "undone"
    
    # Internal link edge should still be removed
    edge_count = conn.execute("SELECT COUNT(*) FROM internal_links").fetchone()[0]
    assert edge_count == 0


def _conn_with_old_check_constraint() -> sqlite3.Connection:
    """Create a DB with the OLD CHECK constraint (without 'undone').
    
    This simulates a production DB created before PR #24 added the 'undone' status.
    """
    body = '<p>Love <a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>.</p>'
    body_hash = _hash_body(body)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Use the OLD schema that doesn't include 'undone' in the CHECK
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]',
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
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
                CHECK (status IN ('suggested', 'applied', 'dismissed')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE INDEX idx_link_suggestions_status ON link_suggestions (status, score);
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, status, created_at, applied_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 'applied', 1, 1000)",
        (body_hash,),
    )
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'ceramic tanks', 'https://s.com/collections/ceramic-tanks')"
    )
    conn.commit()
    return conn


def test_undo_fails_on_old_check_constraint_without_migration():
    """Verify that undo fails on a DB with the old CHECK constraint (without migration)."""
    conn = _conn_with_old_check_constraint()
    
    def fake_push(source_type, row, new_body):
        return {"ok": True}
    
    sid = conn.execute("SELECT id FROM link_suggestions WHERE status = 'applied'").fetchone()["id"]
    
    # Undo should fail with CHECK constraint error
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        undo_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push)


def test_migration_adds_undone_to_check_constraint():
    """Test that _migrate_link_suggestions_check_constraint correctly updates the CHECK."""
    conn = _conn_with_old_check_constraint()
    
    # Verify old schema doesn't have 'undone'
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='link_suggestions'"
    ).fetchone()["sql"]
    assert "'undone'" not in table_sql
    
    # Run migration
    migrated = _migrate_link_suggestions_check_constraint(conn)
    assert migrated is True
    
    # Verify new schema has 'undone'
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='link_suggestions'"
    ).fetchone()["sql"]
    assert "'undone'" in table_sql
    
    # Verify data is preserved
    row = conn.execute("SELECT * FROM link_suggestions WHERE status = 'applied'").fetchone()
    assert row is not None
    assert row["source_handle"] == "news/post"
    assert row["target_handle"] == "ceramic-tanks"
    
    # Verify index still exists
    idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_link_suggestions_status'"
    ).fetchone()
    assert idx is not None


def test_undo_works_after_migration():
    """Test that undo works on an old DB after migration is applied."""
    conn = _conn_with_old_check_constraint()
    
    # Run migration
    _migrate_link_suggestions_check_constraint(conn)
    
    pushed = {}
    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}
    
    sid = conn.execute("SELECT id FROM link_suggestions WHERE status = 'applied'").fetchone()["id"]
    
    # Undo should now work
    result = undo_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push)
    
    assert result["status"] == "undone"
    assert "ceramic tanks" in pushed["body"]  # text preserved
    assert '<a href="https://s.com/collections/ceramic-tanks">' not in pushed["body"]  # link removed
    
    # Status should be 'undone'
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "undone"


def test_migration_is_idempotent():
    """Test that running migration multiple times is safe."""
    conn = _conn_with_old_check_constraint()
    
    # Run migration first time
    migrated1 = _migrate_link_suggestions_check_constraint(conn)
    assert migrated1 is True
    
    # Run migration second time - should be no-op
    migrated2 = _migrate_link_suggestions_check_constraint(conn)
    assert migrated2 is False
    
    # Data should still be there
    row = conn.execute("SELECT * FROM link_suggestions WHERE status = 'applied'").fetchone()
    assert row is not None


def test_migration_skips_when_table_missing():
    """Test that migration handles missing table gracefully."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # No link_suggestions table
    migrated = _migrate_link_suggestions_check_constraint(conn)
    assert migrated is False
