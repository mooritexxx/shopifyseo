"""Catalog fetch helpers and aggregate counters.

The thin SQL layer that the dashboard / overview / blog views read from.
SEO scoring lives in :mod:`._seo_facts`; detail joins live in
:mod:`._object_detail`.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ._urls import object_url


# Tables that carry SEO signal columns (gsc_*, ga4_*, index_*, pagespeed_*).
_SEO_SIGNAL_TABLES: tuple[str, ...] = ("products", "collections", "pages", "blog_articles")


def _row_factory(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    return conn


def fetch_all_products(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM products ORDER BY title").fetchall()


def fetch_all_collections(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM collections ORDER BY title").fetchall()


def fetch_all_pages(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM pages ORDER BY title").fetchall()


def fetch_all_blogs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM blogs ORDER BY title").fetchall()


def fetch_all_blog_articles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM blog_articles ORDER BY blog_handle, title"
    ).fetchall()


def fetch_all_blog_articles_enriched(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All articles with blog title for cross-blog listings."""
    return conn.execute(
        """
        SELECT a.*, b.title AS blog_title
        FROM blog_articles a
        LEFT JOIN blogs b ON b.handle = a.blog_handle
        ORDER BY COALESCE(a.published_at, a.updated_at, '') DESC, b.title, a.title
        """
    ).fetchall()


def fetch_blog_by_handle(conn: sqlite3.Connection, handle: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM blogs WHERE handle = ?", (handle,)).fetchone()


def fetch_articles_by_blog_handle(conn: sqlite3.Connection, blog_handle: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM blog_articles
        WHERE blog_handle = ?
        ORDER BY COALESCE(published_at, updated_at, '') DESC, title
        """,
        (blog_handle,),
    ).fetchall()


def count_blog_articles_missing_meta(conn: sqlite3.Connection) -> int:
    """Articles missing SEO title or description (either field absent counts as incomplete)."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM blog_articles
        WHERE (seo_title IS NULL OR seo_title = '')
           OR (seo_description IS NULL OR seo_description = '')
        """
    ).fetchone()
    return int(row[0]) if row else 0


def fetch_counts(conn: sqlite3.Connection) -> dict[str, int]:
    def _count(table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] if row else 0

    return {
        "products": _count("products"),
        "variants": _count("product_variants"),
        "images": _count("product_images"),
        "product_metafields": _count("product_metafields"),
        "collections": _count("collections"),
        "collection_metafields": _count("collection_metafields"),
        "collection_products": _count("collection_products"),
        "pages": _count("pages"),
        "blogs": _count("blogs"),
        "blog_articles": _count("blog_articles"),
    }


def fetch_recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def fetch_overview_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    """Return aggregated metrics matching the OverviewMetrics schema."""
    products_missing_meta = conn.execute(
        "SELECT COUNT(*) FROM products WHERE (seo_title IS NULL OR seo_title = '') OR (seo_description IS NULL OR seo_description = '')"
    ).fetchone()[0]
    products_thin_body = conn.execute(
        "SELECT COUNT(*) FROM products WHERE description_html IS NULL OR LENGTH(description_html) < 200"
    ).fetchone()[0]
    collections_missing_meta = conn.execute(
        "SELECT COUNT(*) FROM collections WHERE (seo_title IS NULL OR seo_title = '') OR (seo_description IS NULL OR seo_description = '')"
    ).fetchone()[0]
    pages_missing_meta = conn.execute(
        "SELECT COUNT(*) FROM pages WHERE (seo_title IS NULL OR seo_title = '') OR (seo_description IS NULL OR seo_description = '')"
    ).fetchone()[0]

    try:
        gsc_pages = conn.execute(
            "SELECT COUNT(DISTINCT object_handle) FROM gsc_query_rows"
        ).fetchone()[0]
        row = conn.execute(
            " UNION ALL ".join(
                f"SELECT COALESCE(SUM(gsc_clicks),0), COALESCE(SUM(gsc_impressions),0) FROM {t}"
                for t in _SEO_SIGNAL_TABLES
            )
        ).fetchall()
        gsc_clicks = sum(r[0] for r in row)
        gsc_impressions = sum(r[1] for r in row)
    except Exception:
        gsc_pages = gsc_clicks = gsc_impressions = 0

    try:
        ga4_row = conn.execute(
            " UNION ALL ".join(
                f"SELECT COUNT(CASE WHEN ga4_views>0 THEN 1 END), COALESCE(SUM(ga4_sessions),0), COALESCE(SUM(ga4_views),0) FROM {t}"
                for t in _SEO_SIGNAL_TABLES
            )
        ).fetchall()
        ga4_pages = sum(r[0] for r in ga4_row)
        ga4_sessions = sum(r[1] for r in ga4_row)
        ga4_views = sum(r[2] for r in ga4_row)
    except Exception:
        ga4_pages = ga4_sessions = ga4_views = 0

    return {
        "products_missing_meta": int(products_missing_meta),
        "products_thin_body": int(products_thin_body),
        "collections_missing_meta": int(collections_missing_meta),
        "pages_missing_meta": int(pages_missing_meta),
        "gsc_pages": int(gsc_pages),
        "gsc_clicks": int(gsc_clicks),
        "gsc_impressions": int(gsc_impressions),
        "ga4_pages": int(ga4_pages),
        "ga4_sessions": int(ga4_sessions),
        "ga4_views": int(ga4_views),
    }


def fetch_top_organic_pages(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Return the top N entities ranked by GSC clicks across all entity types."""
    rows = conn.execute(
        """
        SELECT entity_type, handle, title,
               gsc_clicks, gsc_impressions, gsc_ctr, gsc_position
        FROM (
            SELECT 'product'      AS entity_type, handle, title,
                   COALESCE(gsc_clicks, 0)      AS gsc_clicks,
                   COALESCE(gsc_impressions, 0) AS gsc_impressions,
                   COALESCE(gsc_ctr, 0.0)       AS gsc_ctr,
                   gsc_position
            FROM products WHERE gsc_clicks > 0
            UNION ALL
            SELECT 'collection'   AS entity_type, handle, title,
                   COALESCE(gsc_clicks, 0),
                   COALESCE(gsc_impressions, 0),
                   COALESCE(gsc_ctr, 0.0),
                   gsc_position
            FROM collections WHERE gsc_clicks > 0
            UNION ALL
            SELECT 'page'         AS entity_type, handle, title,
                   COALESCE(gsc_clicks, 0),
                   COALESCE(gsc_impressions, 0),
                   COALESCE(gsc_ctr, 0.0),
                   gsc_position
            FROM pages WHERE gsc_clicks > 0
            UNION ALL
            SELECT 'blog_article' AS entity_type,
                   blog_handle || '/' || handle AS handle,
                   title,
                   COALESCE(gsc_clicks, 0),
                   COALESCE(gsc_impressions, 0),
                   COALESCE(gsc_ctr, 0.0),
                   gsc_position
            FROM blog_articles WHERE gsc_clicks > 0
        )
        ORDER BY gsc_clicks DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        entity_type = row[0]
        handle = row[1]
        result.append(
            {
                "entity_type": entity_type,
                "handle": handle,
                "title": row[2] or "",
                "gsc_clicks": int(row[3]),
                "gsc_impressions": int(row[4]),
                "gsc_ctr": float(row[5]),
                "gsc_position": float(row[6]) if row[6] is not None else None,
                "url": object_url(entity_type, handle),
            }
        )
    return result
