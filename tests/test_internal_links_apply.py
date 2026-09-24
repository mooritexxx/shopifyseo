"""Tests for wrapping anchors and applying suggestions."""

import hashlib
import sqlite3

import pytest

from shopifyseo.internal_links.apply import apply_suggestion, wrap_phrase_in_html


def _hash_body(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


def test_wrap_phrase_wraps_first_eligible_occurrence_only():
    html = '<h2>ceramic tanks</h2><p><a href="/x">ceramic tanks</a> Love ceramic tanks. More ceramic tanks.</p>'
    out = wrap_phrase_in_html(html, "ceramic tanks", "https://s.com/collections/ceramic-tanks")
    assert out.count('<a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>') == 1
    # heading and existing link untouched
    assert "<h2>ceramic tanks</h2>" in out
    assert '<a href="/x">ceramic tanks</a>' in out
    # second plain occurrence untouched
    assert out.endswith("More ceramic tanks.</p>")


def test_wrap_phrase_returns_none_when_absent():
    assert wrap_phrase_in_html("<p>nothing here</p>", "ceramic tanks", "u") is None


def _conn() -> sqlite3.Connection:
    body = "<p>Love ceramic tanks.</p>"
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
                CHECK (status IN ('suggested', 'applied', 'dismissed')),
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
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 1)",
        (body_hash,),
    )
    conn.commit()
    return conn


def test_apply_phrase_wrap_pushes_and_updates_state():
    conn = _conn()
    pushed = {}
    sanitized = []

    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}

    def fake_sanitize(body):
        sanitized.append(body)
        return body

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    # Use skip_shopify_check since we're using a mock push function
    result = apply_suggestion(
        conn, sid, base_url="https://s.com", push_fn=fake_push, sanitize_fn=fake_sanitize, skip_shopify_check=True
    )
    assert result["status"] == "applied"
    assert '<a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>' in pushed["body"]
    assert len(sanitized) == 1, "sanitize_fn should have been called"
    row = conn.execute("SELECT status, applied_at FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "applied" and row["applied_at"]
    # local body updated and graph row inserted
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert 'href="https://s.com/collections/ceramic-tanks"' in body
    assert conn.execute(
        "SELECT COUNT(*) FROM internal_links WHERE source_handle = 'news/post' AND target_handle = 'ceramic-tanks'"
    ).fetchone()[0] == 1


def test_apply_failure_leaves_status_suggested():
    conn = _conn()

    def failing_push(source_type, row, new_body):
        raise RuntimeError("shopify down")

    def fake_sanitize(body):
        return body

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(RuntimeError):
        # Use skip_shopify_check since we're testing the push failure path
        apply_suggestion(
            conn, sid, base_url="https://s.com", push_fn=failing_push, sanitize_fn=fake_sanitize, skip_shopify_check=True
        )
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "suggested"
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert "<a " not in body  # local untouched


def test_apply_rejects_stale_body():
    """Apply should reject if source body changed since suggestion was created."""
    body_old = "<p>Love ceramic tanks.</p>"
    body_new = "<p>Updated body content.</p>"
    hash_old = _hash_body(body_old)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY,
            source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT,
            anchor_text TEXT, href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY,
            source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT,
            kind TEXT, anchor_phrase TEXT, ai_anchor_html TEXT, source_body_hash TEXT,
            score REAL DEFAULT 0, status TEXT DEFAULT 'suggested', created_at INTEGER, applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE TABLE collections (handle TEXT, title TEXT);
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body_new,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 1)",
        (hash_old,),
    )
    conn.commit()

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(ValueError, match="Source body changed"):
        # Use skip_shopify_check since we're testing the stale body check path
        apply_suggestion(
            conn, sid, base_url="https://s.com", push_fn=lambda *a: {}, sanitize_fn=lambda b: b, skip_shopify_check=True
        )


def test_apply_rejects_ghost_source_collection():
    """Apply should reject if the source collection no longer exists in Shopify (ghost)."""
    from shopifyseo.internal_links.validation import ShopifyResourceMissing

    body = "<p>Check out our ceramic tanks collection.</p>"
    body_hash = _hash_body(body)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY,
            source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT,
            anchor_text TEXT, href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY,
            source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT,
            kind TEXT, anchor_phrase TEXT, ai_anchor_html TEXT, source_body_hash TEXT,
            score REAL DEFAULT 0, status TEXT DEFAULT 'suggested', created_at INTEGER, applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        """
    )
    # Create a ghost collection (exists locally but NOT in Shopify - simulated by having shopify_id)
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, description_html, seo_title, seo_description) "
        "VALUES ('gid://shopify/Collection/123', 'ghost-collection', 'Ghost', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status) VALUES "
        "('gid://shopify/Product/456', 'some-product', 'Product', 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('collection', 'ghost-collection', 'product', 'some-product', 'phrase_wrap', 'some product', ?, 1.0, 1)",
        (body_hash,),
    )
    conn.commit()

    # Mock the Shopify check to return False (simulating collection deleted from Shopify)
    import shopifyseo.internal_links.validation as validation_mod
    original_verify = validation_mod.verify_resource_exists_in_shopify

    def mock_verify(obj_type, shopify_id):
        if obj_type == "collection" and shopify_id == "gid://shopify/Collection/123":
            return False  # Ghost - doesn't exist in Shopify
        return True

    validation_mod.verify_resource_exists_in_shopify = mock_verify
    try:
        sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
        with pytest.raises(ShopifyResourceMissing) as exc_info:
            apply_suggestion(conn, sid, base_url="https://s.com", push_fn=lambda *a: {}, sanitize_fn=lambda b: b)
        assert "ghost-collection" in str(exc_info.value)
        assert "source" in str(exc_info.value).lower()
        # Suggestion status should remain unchanged
        row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
        assert row["status"] == "suggested"
    finally:
        validation_mod.verify_resource_exists_in_shopify = original_verify


def test_apply_rejects_source_with_null_shopify_id():
    """Apply should reject if the source has NULL shopify_id (already marked as deleted)."""
    from shopifyseo.internal_links.validation import ShopifyResourceMissing

    body = "<p>Check out our products.</p>"
    body_hash = _hash_body(body)

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY,
            source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT,
            anchor_text TEXT, href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY,
            source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT,
            kind TEXT, anchor_phrase TEXT, ai_anchor_html TEXT, source_body_hash TEXT,
            score REAL DEFAULT 0, status TEXT DEFAULT 'suggested', created_at INTEGER, applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        """
    )
    # Create a collection with NULL shopify_id (already marked as deleted)
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, description_html) "
        "VALUES (NULL, 'deleted-collection', 'Deleted', ?)",
        (body,),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status) VALUES "
        "('gid://shopify/Product/456', 'some-product', 'Product', 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('collection', 'deleted-collection', 'product', 'some-product', 'phrase_wrap', 'some product', ?, 1.0, 1)",
        (body_hash,),
    )
    conn.commit()

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(ShopifyResourceMissing) as exc_info:
        apply_suggestion(conn, sid, base_url="https://s.com", push_fn=lambda *a: {}, sanitize_fn=lambda b: b)
    assert "deleted-collection" in str(exc_info.value)
    assert "source" in str(exc_info.value).lower()
