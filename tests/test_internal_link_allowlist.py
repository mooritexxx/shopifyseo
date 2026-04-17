"""Tests for storefront internal URL allowlist and article body link sanitizer."""

import sqlite3

from shopifyseo.dashboard_ai_engine_parts.generation import sanitize_article_internal_links
from shopifyseo.dashboard_queries import build_store_internal_link_allowlist, object_url_with_base


def test_object_url_with_base_collection_and_blog():
    assert object_url_with_base("https://example-store.myshopify.com", "collection", "vapes") == "https://example-store.myshopify.com/collections/vapes"
    assert object_url_with_base("", "collection", "vapes") == "/collections/vapes"
    assert object_url_with_base("https://example-store.myshopify.com", "blog_article", "news/hello") == "https://example-store.myshopify.com/blogs/news/hello"
    assert object_url_with_base("https://example-store.myshopify.com", "product", "sku-1") == "https://example-store.myshopify.com/products/sku-1"


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE collections (handle TEXT NOT NULL, title TEXT);
        CREATE TABLE products (handle TEXT NOT NULL, title TEXT, status TEXT);
        CREATE TABLE pages (handle TEXT NOT NULL, title TEXT);
        CREATE TABLE blog_articles (blog_handle TEXT NOT NULL, handle TEXT NOT NULL, title TEXT);
        """
    )
    return conn


def test_build_allowlist_orders_rag_collections_first():
    conn = _memory_conn()
    conn.execute("INSERT INTO collections (handle, title) VALUES ('z-col', 'Zebra'), ('a-col', 'Alpha')")
    conn.commit()
    rag = [
        {"object_type": "collection", "object_handle": "z-col", "source_text_preview": "x"},
    ]
    targets, full, paths = build_store_internal_link_allowlist(conn, "https://example-store.myshopify.com", rag_results=rag)
    assert len(targets) == 2
    assert targets[0]["handle"] == "z-col"
    assert targets[0]["url"] == "https://example-store.myshopify.com/collections/z-col"
    assert "https://example-store.myshopify.com/collections/z-col" in full
    assert "/collections/z-col" in paths
    conn.close()


def test_sanitize_rewrites_path_and_unwraps_external():
    path_to_canonical = {"/collections/vapes": "https://example-store.myshopify.com/collections/vapes"}
    base = "https://example-store.myshopify.com"
    html = '<a href="/collections/vapes">vapes</a>'
    out = sanitize_article_internal_links(html, path_to_canonical=path_to_canonical, base_url=base)
    assert 'href="https://example-store.myshopify.com/collections/vapes"' in out
    assert "vapes" in out

    html_ext = '<a href="https://competitor.example/p">bad</a>'
    out2 = sanitize_article_internal_links(html_ext, path_to_canonical=path_to_canonical, base_url=base)
    assert out2 == "bad"

    html_mail = '<a href="mailto:a@b.co">e</a>'
    assert sanitize_article_internal_links(html_mail, path_to_canonical=path_to_canonical, base_url=base) == html_mail
