"""Tests for AI-woven anchor generation."""

import hashlib
import sqlite3

import pytest

from shopifyseo.internal_links.ai_weave import generate_ai_anchor


def _hash_body(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()


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
        "INSERT INTO blog_articles (blog_handle, handle, title, body) VALUES "
        "('news', 'post', 'Post', '<p>Original text.</p>')"
    )
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('widget', 'Widget Pro', 'ACTIVE')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "score, created_at) VALUES ('blog_article', 'news/post', 'product', 'widget', 'ai_woven', 1.0, 1)"
    )
    conn.commit()
    return conn


def test_generate_ai_anchor_stores_revised_body():
    conn = _conn()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    revised = '<p>Original text. Check out the <a href="https://s.com/products/widget">Widget Pro</a>.</p>'

    def fake_call_ai(messages, json_schema):
        return {"revised_body": revised}

    result = generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=fake_call_ai)
    assert result["ai_anchor_html"] == revised
    row = conn.execute("SELECT ai_anchor_html FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["ai_anchor_html"] == revised


def test_generate_ai_anchor_rejects_missing_link():
    conn = _conn()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]

    def fake_call_ai(messages, json_schema):
        return {"revised_body": "<p>No link at all.</p>"}

    with pytest.raises(ValueError, match="target link"):
        generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=fake_call_ai)


def test_generate_ai_anchor_only_for_ai_woven():
    conn = _conn()
    conn.execute("UPDATE link_suggestions SET kind = 'phrase_wrap'")
    conn.commit()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(ValueError, match="ai_woven"):
        generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=lambda m, j: {})


def test_generate_ai_anchor_updates_source_body_hash():
    """generate_ai_anchor must update source_body_hash to current body hash."""
    conn = _conn()
    current_body = "<p>Original text.</p>"
    expected_hash = _hash_body(current_body)
    conn.execute("UPDATE link_suggestions SET source_body_hash = 'stale_hash_from_rebuild'")
    conn.commit()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    revised = '<p>Original text. Check out the <a href="https://s.com/products/widget">Widget Pro</a>.</p>'

    def fake_call_ai(messages, json_schema):
        return {"revised_body": revised}

    generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=fake_call_ai)
    row = conn.execute("SELECT source_body_hash FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["source_body_hash"] == expected_hash, "Hash should be updated to current body hash"
