"""Tests for token-overlap related catalog ranking (articles / pages)."""

import json
import sqlite3

from shopifyseo import dashboard_queries as dq


def _minimal_catalog_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE products (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          vendor TEXT,
          product_type TEXT,
          status TEXT,
          tags_json TEXT NOT NULL,
          seo_title TEXT
        );
        CREATE TABLE collections (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          seo_title TEXT,
          description_html TEXT
        );
        CREATE TABLE pages (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          body TEXT,
          seo_title TEXT,
          seo_description TEXT
        );
        """
    )


def test_related_products_prefers_token_overlap_over_alphabetical_order():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _minimal_catalog_schema(conn)
    conn.execute(
        "INSERT INTO products VALUES ('1','ABT Berry Ice Disposable','abt-berry','ABT','Disposable','ACTIVE','[]','')",
    )
    conn.execute(
        "INSERT INTO products VALUES ('2','UWELL Caliburn Pod Kit','uwell-cal','UWELL','Pods','ACTIVE','[]','')",
    )
    conn.execute(
        "INSERT INTO products VALUES ('3','ZZZ Unrelated Widget','zzz-other','Acme','Gadget','ACTIVE','[]','')",
    )
    article = {
        "title": "UWELL Caliburn guide for beginners",
        "seo_title": "",
        "seo_description": "",
        "summary": "",
        "body": "<p>Caliburn pods and refillable systems in Canada.</p>",
        "tags_json": json.dumps(["uwell", "refillable"]),
    }
    tokens = dq._content_tokens_for_blog_article(article)
    assert "uwell" in tokens
    assert "caliburn" in tokens
    rel = dq._related_products_by_token_overlap(conn, tokens, limit=20)
    assert [p["handle"] for p in rel][:1] == ["uwell-cal"]


def test_related_pages_respects_exclude_handle_and_overlap():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _minimal_catalog_schema(conn)
    conn.execute(
        "INSERT INTO pages VALUES ('1','Shipping Info','shipping','<p>Free shipping details</p>','','')",
    )
    conn.execute(
        "INSERT INTO pages VALUES ('2','Age Policy for Customers','age-policy','<p>Minimum age requirements</p>','','')",
    )
    conn.execute(
        "INSERT INTO pages VALUES ('3','Contact Us','contact','<p>Email us</p>','','')",
    )
    page_tokens = dq._content_tokens_for_page(
        {
            "title": "Customer age requirements",
            "seo_title": "",
            "seo_description": "",
            "body": "<p>Our policy explains age verification.</p>",
        }
    )
    rel = dq._related_pages_by_token_overlap(conn, page_tokens, exclude_handle="age-policy", limit=10)
    handles = {p["handle"] for p in rel}
    assert "age-policy" not in handles
    assert "shipping" in handles or "contact" in handles


def test_related_collections_uses_token_overlap_first():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _minimal_catalog_schema(conn)
    conn.execute(
        "INSERT INTO collections VALUES ('1','Disposable Vapes Sale','disp-sale','','')",
    )
    conn.execute(
        "INSERT INTO collections VALUES ('2','Refillable Pod Systems','pods-refill','','<p>Caliburn and open systems</p>')",
    )
    tokens = dq._tokens_from_blob("uwell caliburn refillable pod guide")
    rel = dq._related_collections_by_token_overlap(
        conn, tokens, title_fallback_lower="ignored when overlap exists", limit=10
    )
    assert rel and rel[0]["handle"] == "pods-refill"
