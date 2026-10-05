"""Tests for live-only missing-meta counters in the Overview.

These tests verify that `fetch_overview_metrics`, `fetch_catalog_meta_metrics`,
and `count_blog_articles_missing_meta` count only items that are live on the
Online Store, per the definitions in `_live_where`.
"""

import sqlite3

from shopifyseo import dashboard_queries as dq
from shopifyseo.shopify_catalog_sync import db as sync_db
from shopifyseo.shopify_catalog_sync.pages import upsert_page
from shopifyseo.shopify_catalog_sync.queries import PAGES_QUERY, PAGE_QUERY


def _ensure_api_unreachable_column(conn: sqlite3.Connection) -> None:
    """Add collections.api_unreachable if not present (normally added by dashboard_store)."""
    rows = conn.execute("PRAGMA table_info(collections)").fetchall()
    names = {r[1] if isinstance(r, (list, tuple)) else r["name"] for r in rows}
    if "api_unreachable" not in names:
        conn.execute("ALTER TABLE collections ADD COLUMN api_unreachable INTEGER DEFAULT 0")


def _insert_product(
    conn: sqlite3.Connection,
    shopify_id: str,
    handle: str,
    *,
    status: str = "ACTIVE",
    online_store_url: str | None = "https://store.com/products/x",
    seo_title: str = "",
    seo_description: str = "",
    description_html: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO products (
            shopify_id, legacy_resource_id, title, handle, vendor, product_type,
            status, created_at, updated_at, published_at, description_html,
            tags_json, seo_title, seo_description, total_inventory, tracks_inventory,
            category_full_name, online_store_url, options_json, featured_image_json,
            raw_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shopify_id,
            "",
            f"Product {handle}",
            handle,
            "",
            "",
            status,
            "",
            "",
            "",
            description_html,
            "[]",
            seo_title,
            seo_description,
            0,
            0,
            "",
            online_store_url,
            "[]",
            "",
            "{}",
            "",
        ),
    )


def _insert_collection(
    conn: sqlite3.Connection,
    shopify_id: str,
    handle: str,
    *,
    api_unreachable: int = 0,
    seo_title: str = "",
    seo_description: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO collections (
            shopify_id, title, handle, updated_at, description_html,
            seo_title, seo_description, rule_set_json, raw_json, synced_at, api_unreachable
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shopify_id,
            f"Collection {handle}",
            handle,
            "",
            "",
            seo_title,
            seo_description,
            "",
            "{}",
            "",
            api_unreachable,
        ),
    )


def _insert_page(
    conn: sqlite3.Connection,
    shopify_id: str,
    handle: str,
    *,
    is_published: int | None = 1,
    seo_title: str = "",
    seo_description: str = "",
    body: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO pages (
            shopify_id, title, handle, updated_at, body,
            seo_title, seo_description, is_published, published_at, raw_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shopify_id,
            f"Page {handle}",
            handle,
            "",
            body,
            seo_title,
            seo_description,
            is_published,
            "",
            "{}",
            "",
        ),
    )


def _ensure_blog_exists(conn: sqlite3.Connection, blog_shopify_id: str, blog_handle: str) -> None:
    """Insert the parent blog if it doesn't exist (required by FK constraint)."""
    row = conn.execute("SELECT 1 FROM blogs WHERE shopify_id = ?", (blog_shopify_id,)).fetchone()
    if not row:
        conn.execute(
            """
            INSERT INTO blogs (
                shopify_id, title, handle, created_at, updated_at,
                comment_policy, tags_json, raw_json, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (blog_shopify_id, f"Blog {blog_handle}", blog_handle, "", "", "", "[]", "{}", ""),
        )


def _insert_article(
    conn: sqlite3.Connection,
    shopify_id: str,
    handle: str,
    blog_shopify_id: str = "blog-1",
    blog_handle: str = "news",
    *,
    is_published: int = 1,
    seo_title: str = "",
    seo_description: str = "",
) -> None:
    _ensure_blog_exists(conn, blog_shopify_id, blog_handle)
    conn.execute(
        """
        INSERT INTO blog_articles (
            shopify_id, blog_shopify_id, blog_handle, title, handle,
            published_at, updated_at, is_published, body, summary, tags_json,
            author_name, seo_title, seo_description, image_json, raw_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            shopify_id,
            blog_shopify_id,
            blog_handle,
            f"Article {handle}",
            handle,
            "",
            "",
            is_published,
            "",
            "",
            "[]",
            "",
            seo_title,
            seo_description,
            "",
            "{}",
            "",
        ),
    )


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create full schema using sync_db.ensure_schema, plus api_unreachable."""
    sync_db.ensure_schema(conn)
    _ensure_api_unreachable_column(conn)


def test_unpublished_items_excluded():
    """Unpublished items with missing meta are NOT counted."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    _insert_product(conn, "p1", "unpub-prod-url", online_store_url="", seo_title="", seo_description="")
    _insert_product(conn, "p2", "unpub-prod-draft", status="DRAFT", seo_title="", seo_description="")
    _insert_collection(conn, "c1", "unreachable-coll", api_unreachable=1, seo_title="", seo_description="")
    _insert_page(conn, "pg1", "unpub-page", is_published=0, seo_title="", seo_description="")
    _insert_article(conn, "a1", "unpub-article", is_published=0, seo_title="", seo_description="")
    conn.commit()

    metrics = dq.fetch_overview_metrics(conn)
    assert metrics["products_missing_meta"] == 0
    assert metrics["products_thin_body"] == 0
    assert metrics["collections_missing_meta"] == 0
    assert metrics["pages_missing_meta"] == 0

    catalog_metrics = dq.fetch_catalog_meta_metrics(conn)
    assert catalog_metrics["products_missing_meta"] == 0
    assert catalog_metrics["products_thin_body"] == 0
    assert catalog_metrics["collections_missing_meta"] == 0
    assert catalog_metrics["pages_missing_meta"] == 0

    assert dq.count_blog_articles_missing_meta(conn) == 0


def test_published_items_missing_meta_counted():
    """Published items with missing meta ARE counted."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    _insert_product(conn, "p1", "active-prod", status="ACTIVE", online_store_url="https://x.com/p", seo_title="", seo_description="")
    _insert_collection(conn, "c1", "reachable-coll", api_unreachable=0, seo_title="", seo_description="")
    _insert_page(conn, "pg1", "pub-page", is_published=1, seo_title="", seo_description="")
    _insert_article(conn, "a1", "pub-article", is_published=1, seo_title="", seo_description="")
    conn.commit()

    metrics = dq.fetch_overview_metrics(conn)
    assert metrics["products_missing_meta"] == 1
    assert metrics["collections_missing_meta"] == 1
    assert metrics["pages_missing_meta"] == 1

    catalog_metrics = dq.fetch_catalog_meta_metrics(conn)
    assert catalog_metrics["products_missing_meta"] == 1
    assert catalog_metrics["collections_missing_meta"] == 1
    assert catalog_metrics["pages_missing_meta"] == 1

    assert dq.count_blog_articles_missing_meta(conn) == 1


def test_null_is_published_treated_as_live():
    """Legacy pages with is_published IS NULL are counted (unknown = live)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    _insert_page(conn, "pg1", "legacy-page", is_published=None, seo_title="", seo_description="")
    _insert_product(conn, "p1", "null-url-prod", status="ACTIVE", online_store_url=None, seo_title="", seo_description="")
    conn.commit()

    metrics = dq.fetch_catalog_meta_metrics(conn)
    assert metrics["pages_missing_meta"] == 1
    assert metrics["products_missing_meta"] == 1


def test_three_page_scenario():
    """The 19-page scenario: 3 unpublished with missing meta, 16 published with full meta."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    for i in range(16):
        _insert_page(
            conn,
            f"pub-{i}",
            f"published-page-{i}",
            is_published=1,
            seo_title="Good title",
            seo_description="Good desc",
            body="<p>Some content</p>",
        )

    _insert_page(conn, "unpub-1", "shipping", is_published=0, seo_title="", seo_description="", body="")
    _insert_page(conn, "unpub-2", "returns", is_published=0, seo_title="", seo_description="", body="")
    _insert_page(conn, "unpub-3", "information-security-policy", is_published=0, seo_title="", seo_description="", body="")
    conn.commit()

    metrics = dq.fetch_catalog_meta_metrics(conn)
    assert metrics["pages_missing_meta"] == 0

    conn.execute("UPDATE pages SET is_published = NULL WHERE handle IN ('shipping', 'returns', 'information-security-policy')")
    conn.commit()

    metrics2 = dq.fetch_catalog_meta_metrics(conn)
    assert metrics2["pages_missing_meta"] == 3


def test_products_thin_body_excludes_unpublished():
    """Unpublished products with short body are NOT counted in products_thin_body."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    _insert_product(conn, "p1", "unpub-thin", status="DRAFT", online_store_url="", description_html="<p>Short</p>", seo_title="Has title", seo_description="Has desc")
    _insert_product(conn, "p2", "pub-thin", status="ACTIVE", online_store_url="https://x.com/p", description_html="<p>Short</p>", seo_title="Has title", seo_description="Has desc")
    conn.commit()

    metrics = dq.fetch_catalog_meta_metrics(conn)
    assert metrics["products_thin_body"] == 1


def test_upsert_page_stores_is_published():
    """upsert_page correctly stores is_published from the API payload."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _create_schema(conn)

    upsert_page(
        conn,
        {
            "id": "gid://shopify/Page/1",
            "title": "Test Unpublished",
            "handle": "test-unpub",
            "isPublished": False,
            "publishedAt": None,
        },
        synced_at="2026-10-03T00:00:00Z",
    )
    conn.commit()

    row = conn.execute("SELECT is_published, published_at FROM pages WHERE handle = 'test-unpub'").fetchone()
    assert row["is_published"] == 0
    assert not row["published_at"]

    upsert_page(
        conn,
        {
            "id": "gid://shopify/Page/2",
            "title": "Test Published",
            "handle": "test-pub",
            "isPublished": True,
            "publishedAt": "2026-10-01T12:00:00Z",
        },
        synced_at="2026-10-03T00:00:00Z",
    )
    conn.commit()

    row2 = conn.execute("SELECT is_published, published_at FROM pages WHERE handle = 'test-pub'").fetchone()
    assert row2["is_published"] == 1
    assert row2["published_at"] == "2026-10-01T12:00:00Z"

    upsert_page(
        conn,
        {
            "id": "gid://shopify/Page/3",
            "title": "Test Legacy (no key)",
            "handle": "test-legacy",
        },
        synced_at="2026-10-03T00:00:00Z",
    )
    conn.commit()

    row3 = conn.execute("SELECT is_published, published_at FROM pages WHERE handle = 'test-legacy'").fetchone()
    assert row3["is_published"] is None
    assert not row3["published_at"]


def test_pages_query_contains_is_published():
    """PAGES_QUERY and PAGE_QUERY both include isPublished."""
    assert "isPublished" in PAGES_QUERY
    assert "isPublished" in PAGE_QUERY
    assert "publishedAt" in PAGES_QUERY
    assert "publishedAt" in PAGE_QUERY


def test_schema_tolerance_missing_columns():
    """Functions still work when schema lacks is_published/online_store_url/api_unreachable."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    conn.executescript(
        """
        CREATE TABLE products (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            status TEXT,
            description_html TEXT,
            seo_title TEXT,
            seo_description TEXT
        );
        CREATE TABLE collections (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            seo_title TEXT,
            seo_description TEXT
        );
        CREATE TABLE pages (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            body TEXT,
            seo_title TEXT,
            seo_description TEXT
        );
        CREATE TABLE blog_articles (
            shopify_id TEXT PRIMARY KEY,
            blog_shopify_id TEXT NOT NULL,
            blog_handle TEXT NOT NULL,
            title TEXT NOT NULL,
            handle TEXT NOT NULL,
            is_published INTEGER NOT NULL DEFAULT 0,
            seo_title TEXT,
            seo_description TEXT
        );
        """
    )

    conn.execute("INSERT INTO products VALUES ('p1', 'Prod', 'prod-1', 'ACTIVE', '', '', '')")
    conn.execute("INSERT INTO collections VALUES ('c1', 'Coll', 'coll-1', '', '')")
    conn.execute("INSERT INTO pages VALUES ('pg1', 'Page', 'page-1', '', '', '')")
    conn.execute("INSERT INTO blog_articles VALUES ('a1', 'b1', 'blog', 'Art', 'art-1', 1, '', '')")
    conn.commit()

    metrics = dq.fetch_catalog_meta_metrics(conn)
    assert metrics["products_missing_meta"] == 1
    assert metrics["collections_missing_meta"] == 1
    assert metrics["pages_missing_meta"] == 1

    assert dq.count_blog_articles_missing_meta(conn) == 1

    overview_metrics = dq.fetch_overview_metrics(conn)
    assert overview_metrics["products_missing_meta"] == 1
