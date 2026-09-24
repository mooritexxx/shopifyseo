"""Tests for internal link extraction and graph rebuild."""

import sqlite3

from shopifyseo.internal_links.graph import (
    extract_links,
    rebuild_internal_link_graph,
    resolve_internal_target,
)

BASE = "https://example-store.com"


def test_resolve_internal_target_paths():
    assert resolve_internal_target("/products/widget", BASE) == ("product", "widget")
    assert resolve_internal_target(f"{BASE}/collections/vapes/", BASE) == ("collection", "vapes")
    assert resolve_internal_target("/pages/faq?x=1#top", BASE) == ("page", "faq")
    assert resolve_internal_target("/blogs/news/hello-world", BASE) == ("blog_article", "news/hello-world")


def test_resolve_rejects_external_mailto_fragment_and_unknown():
    assert resolve_internal_target("https://competitor.example/products/widget", BASE) is None
    assert resolve_internal_target("mailto:a@b.co", BASE) is None
    assert resolve_internal_target("#section", BASE) is None
    assert resolve_internal_target("/cart", BASE) is None
    assert resolve_internal_target("", BASE) is None


def test_extract_links_returns_href_and_text():
    html = '<p>See <a href="/products/widget">the widget</a> and <a href="https://x.example/p">ext</a>.</p>'
    assert extract_links(html) == [("/products/widget", "the widget"), ("https://x.example/p", "ext")]


def _catalog_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER, gsc_impressions INTEGER,
            seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, gsc_clicks INTEGER, gsc_impressions INTEGER,
            seo_title TEXT, seo_description TEXT);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER, gsc_impressions INTEGER);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER, gsc_impressions INTEGER,
            seo_title TEXT, seo_description TEXT);
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
        """
    )
    return conn


def test_rebuild_graph_fills_rows_and_is_idempotent():
    conn = _catalog_conn()
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body) VALUES "
        "('news', 'post', 'Post', '<p><a href=\"/products/widget\">widget</a></p>')"
    )
    conn.execute(
        "INSERT INTO products (handle, title, description_html) VALUES "
        "('widget', 'Widget', '<p><a href=\"/collections/all\">all</a></p>')"
    )
    n = rebuild_internal_link_graph(conn, base_url="https://example-store.com")
    assert n == 2
    rows = conn.execute(
        "SELECT source_type, source_handle, target_type, target_handle FROM internal_links ORDER BY source_type"
    ).fetchall()
    assert (rows[0]["source_type"], rows[0]["source_handle"]) == ("blog_article", "news/post")
    assert (rows[0]["target_type"], rows[0]["target_handle"]) == ("product", "widget")
    assert (rows[1]["source_type"], rows[1]["target_handle"]) == ("product", "all")
    # Re-run replaces, not duplicates
    assert rebuild_internal_link_graph(conn, base_url="https://example-store.com") == 2
    assert conn.execute("SELECT COUNT(*) AS c FROM internal_links").fetchone()["c"] == 2
