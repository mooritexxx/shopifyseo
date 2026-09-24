"""Tests for wrapping anchors and applying suggestions."""

import sqlite3

import pytest

from shopifyseo.internal_links.apply import apply_suggestion, wrap_phrase_in_html


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
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', "
        "'<p>Love ceramic tanks.</p>', 'st', 'sd')"
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', 1.0, 1)"
    )
    conn.commit()
    return conn


def test_apply_phrase_wrap_pushes_and_updates_state():
    conn = _conn()
    pushed = {}

    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    result = apply_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push)
    assert result["status"] == "applied"
    assert '<a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>' in pushed["body"]
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

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(RuntimeError):
        apply_suggestion(conn, sid, base_url="https://s.com", push_fn=failing_push)
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "suggested"
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert "<a " not in body  # local untouched
