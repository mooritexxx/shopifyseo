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
    result = apply_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push, sanitize_fn=fake_sanitize)
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
        apply_suggestion(conn, sid, base_url="https://s.com", push_fn=failing_push, sanitize_fn=fake_sanitize)
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "suggested"
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert "<a " not in body  # local untouched


def test_apply_ai_woven_preserves_correct_internal_links():
    """Apply must NOT remap correct internal links to different handles.

    Regression test for the bug where zipping two frozensets (allowed_paths,
    allowed_full) in arbitrary order caused path_to_canonical to pair paths
    with random URLs. For example, /collections/abt-85k would be mapped to
    https://vapely.ca/collections/draggg-4k instead of the correct URL.

    This test verifies that an ai_woven body with correct target URLs is
    preserved exactly, even when the allowlist contains other collections,
    products, and pages.
    """
    ai_body = (
        '<p>Check out our <a href="https://vapely.ca/collections/abt-85k-disposable-vapes">'
        'ABT 85K Disposable Vapes</a> collection featuring '
        '<a href="https://vapely.ca/products/abt-85k-mint-disposable">ABT 85K Mint</a> and '
        '<a href="https://vapely.ca/products/abt-85k-grape-disposable">ABT 85K Grape</a>.</p>'
    )
    body_hash = _hash_body(ai_body)

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
    # Insert the target collection plus several OTHER collections that could be
    # incorrectly swapped in if path_to_canonical is built wrong
    conn.execute("INSERT INTO collections (handle, title) VALUES ('abt-85k-disposable-vapes', 'ABT 85K Disposables')")
    conn.execute("INSERT INTO collections (handle, title) VALUES ('draggg-4k-disposable-vapes', 'Draggg 4K Disposables')")
    conn.execute("INSERT INTO collections (handle, title) VALUES ('elf-bar-5000', 'Elf Bar 5000')")
    conn.execute("INSERT INTO collections (handle, title) VALUES ('lost-mary', 'Lost Mary')")
    # Insert target products plus unrelated ones
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('abt-85k-mint-disposable', 'ABT 85K Mint', 'ACTIVE')")
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('abt-85k-grape-disposable', 'ABT 85K Grape', 'ACTIVE')")
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('draggg-peach', 'Draggg Peach', 'ACTIVE')")
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('elf-bar-strawberry', 'Elf Bar Strawberry', 'ACTIVE')")
    # Insert some pages too
    conn.execute("INSERT INTO pages (handle, title) VALUES ('about-us', 'About Us')")
    conn.execute("INSERT INTO pages (handle, title) VALUES ('contact', 'Contact')")
    # Source article with the AI-generated body
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/99', 'news', 'test-article', 'Test', ?, 'st', 'sd')",
        (ai_body,),
    )
    # ai_woven suggestion - the ai_anchor_html is the full replacement body
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, ai_anchor_html, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/test-article', 'collection', 'abt-85k-disposable-vapes', 'ai_woven', "
        "'ABT 85K Disposable Vapes', ?, ?, 1.0, 1)",
        (ai_body, body_hash),
    )
    conn.commit()

    pushed_body = {}

    def fake_push(source_type, row, new_body):
        pushed_body["html"] = new_body
        return {"ok": True}

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    # NOTE: We pass sanitize_fn=None to trigger the real sanitize logic in apply
    # We need to verify the REAL sanitize path works correctly
    result = apply_suggestion(conn, sid, base_url="https://vapely.ca", push_fn=fake_push, sanitize_fn=None)
    assert result["status"] == "applied"

    # The critical assertion: the pushed body must preserve the correct URLs
    html = pushed_body["html"]
    assert 'href="https://vapely.ca/collections/abt-85k-disposable-vapes"' in html, \
        "Target collection URL must be preserved, not remapped"
    assert 'href="https://vapely.ca/products/abt-85k-mint-disposable"' in html, \
        "Target product URL must be preserved"
    assert 'href="https://vapely.ca/products/abt-85k-grape-disposable"' in html, \
        "Target product URL must be preserved"

    # Must NOT contain wrong URLs
    assert "draggg-4k" not in html, "Must not remap to wrong collection"
    assert "elf-bar" not in html, "Must not remap to unrelated collection"
    assert "draggg-peach" not in html, "Must not remap to wrong product"


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
        apply_suggestion(conn, sid, base_url="https://s.com", push_fn=lambda *a: {}, sanitize_fn=lambda b: b)


def test_apply_succeeds_after_generate_updates_hash():
    """Apply should succeed when generate_ai_anchor has updated hash to current body.

    Regression test for bug: Apply failed with "Source body changed" even after
    Generate ran on the current body, because Generate didn't update source_body_hash.
    """
    from shopifyseo.internal_links.ai_weave import generate_ai_anchor

    current_body = "<p>Short body text.</p>"
    stale_hash = "stale_hash_from_old_rebuild"
    current_hash = _hash_body(current_body)
    ai_body = '<p>Short body text. See <a href="https://s.com/products/widget">Widget</a>.</p>'

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
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
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (current_body,),
    )
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('widget', 'Widget', 'ACTIVE')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'product', 'widget', 'ai_woven', ?, 1.0, 1)",
        (stale_hash,),
    )
    conn.commit()

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]

    def fake_call_ai(messages, json_schema):
        return {"revised_body": ai_body}

    generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=fake_call_ai)

    row = conn.execute("SELECT source_body_hash FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["source_body_hash"] == current_hash, "Generate should update hash"

    pushed = {}

    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}

    result = apply_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push, sanitize_fn=lambda b: b)
    assert result["status"] == "applied", "Apply should succeed after Generate updates hash"
    assert 'href="https://s.com/products/widget"' in pushed["body"]


def _conn_with_siblings() -> sqlite3.Connection:
    """Create a DB with multiple suggestions for the same source (sibling suggestions)."""
    body = "<p>Love ceramic tanks. And glass tanks too.</p>"
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
            applied_at INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute("INSERT INTO collections (handle, title) VALUES ('glass-tanks', 'Glass Tanks')")
    # First suggestion: phrase_wrap for "ceramic tanks"
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 1)",
        (body_hash,),
    )
    # Second suggestion: phrase_wrap for "glass tanks" (sibling)
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'glass-tanks', 'phrase_wrap', 'glass tanks', ?, 0.9, 2)",
        (body_hash,),
    )
    conn.commit()
    return conn


def test_apply_sibling_succeeds_without_rebuild():
    """Applying one suggestion should update sibling hashes so the next apply succeeds.
    
    Regression test for the bug where applying one suggestion didn't update the 
    source_body_hash on other pending suggestions for the same source, causing
    subsequent applies to fail with "Source body changed since suggestion was generated".
    """
    conn = _conn_with_siblings()

    def fake_push(source_type, row, new_body):
        return {"ok": True}

    def fake_sanitize(body):
        return body

    # Get both suggestion IDs
    rows = conn.execute(
        "SELECT id, target_handle FROM link_suggestions ORDER BY created_at"
    ).fetchall()
    first_id, second_id = rows[0]["id"], rows[1]["id"]
    assert rows[0]["target_handle"] == "ceramic-tanks"
    assert rows[1]["target_handle"] == "glass-tanks"

    # Apply the first suggestion
    result1 = apply_suggestion(conn, first_id, base_url="https://s.com", push_fn=fake_push, sanitize_fn=fake_sanitize)
    assert result1["status"] == "applied"

    # The sibling should now have an updated source_body_hash matching the new body
    sibling = conn.execute(
        "SELECT source_body_hash, status FROM link_suggestions WHERE id = ?", (second_id,)
    ).fetchone()
    assert sibling["status"] == "suggested"
    
    # Get the actual current body hash
    current_body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    current_hash = _hash_body(current_body)
    assert sibling["source_body_hash"] == current_hash, "Sibling hash should be updated to match new body"

    # Now apply the second suggestion - this should succeed without "Source body changed" error
    result2 = apply_suggestion(conn, second_id, base_url="https://s.com", push_fn=fake_push, sanitize_fn=fake_sanitize)
    assert result2["status"] == "applied"

    # Verify both links were applied
    final_body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert 'href="https://s.com/collections/ceramic-tanks"' in final_body
    assert 'href="https://s.com/collections/glass-tanks"' in final_body


def test_apply_clears_ai_anchor_html_on_siblings():
    """Applying a suggestion should clear ai_anchor_html on ai_woven siblings.
    
    ai_woven suggestions have a pre-generated ai_anchor_html that is the full
    replacement body. After applying a different suggestion on the same source,
    the ai_anchor_html is stale (it was generated for the old body), so it must
    be cleared to force regeneration.
    """
    body = "<p>Love ceramic tanks. And glass tanks too.</p>"
    body_hash = _hash_body(body)
    ai_body = "<p>Love ceramic tanks. Check out our <a href='https://s.com/collections/glass-tanks'>glass tanks</a> too.</p>"
    
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
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
    conn.execute("INSERT INTO collections (handle, title) VALUES ('glass-tanks', 'Glass Tanks')")
    # First suggestion: phrase_wrap 
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 1)",
        (body_hash,),
    )
    # Second suggestion: ai_woven with pre-generated ai_anchor_html
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, ai_anchor_html, source_body_hash, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'glass-tanks', 'ai_woven', NULL, ?, ?, 0.9, 2)",
        (ai_body, body_hash),
    )
    conn.commit()

    rows = conn.execute("SELECT id, kind FROM link_suggestions ORDER BY created_at").fetchall()
    phrase_wrap_id, ai_woven_id = rows[0]["id"], rows[1]["id"]

    def fake_push(*a):
        return {"ok": True}

    # Verify ai_woven has ai_anchor_html before apply
    before = conn.execute(
        "SELECT ai_anchor_html FROM link_suggestions WHERE id = ?", (ai_woven_id,)
    ).fetchone()
    assert before["ai_anchor_html"] is not None

    # Apply the phrase_wrap suggestion
    apply_suggestion(conn, phrase_wrap_id, base_url="https://s.com", push_fn=fake_push, sanitize_fn=lambda b: b)

    # ai_woven sibling should have ai_anchor_html cleared
    after = conn.execute(
        "SELECT ai_anchor_html, source_body_hash FROM link_suggestions WHERE id = ?", (ai_woven_id,)
    ).fetchone()
    assert after["ai_anchor_html"] is None, "ai_anchor_html must be cleared for ai_woven siblings"
    # But source_body_hash should be updated
    current_body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert after["source_body_hash"] == _hash_body(current_body)


def test_apply_returns_target_url_not_shadowed():
    """Apply should return the target URL, not a random URL from the sanitize allowlist.
    
    Regression test for the bug where the `url` variable was shadowed in the
    sanitize allowlist loop, causing the returned {"url": url} to be a random
    allowlist URL instead of the applied target URL.
    """
    conn = _conn()
    target_url = "https://s.com/collections/ceramic-tanks"

    def fake_push(source_type, row, new_body):
        return {"ok": True}

    def fake_sanitize(body):
        return body

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    result = apply_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push, sanitize_fn=fake_sanitize)
    
    assert result["status"] == "applied"
    assert result["url"] == target_url, f"Expected target URL {target_url}, got {result['url']}"
