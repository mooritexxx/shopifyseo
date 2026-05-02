"""Per-object detail fetchers for the product / collection / page / article views.

These bundle the row, workflow state, recommendation history, and (for pages
and articles) related-content joins driven by token overlap.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ._text_tokens import (
    _content_tokens_for_blog_article,
    _content_tokens_for_page,
    _related_collections_by_token_overlap,
    _related_pages_by_token_overlap,
    _related_products_by_token_overlap,
)
from ._urls import blog_article_composite_handle


def _recommendation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a seo_recommendations row to a dict, parsing details_json."""
    d = dict(row)
    raw = d.pop("details_json", None)
    d["details"] = json.loads(raw) if raw else {}
    return d


def _fetch_recommendation(conn: sqlite3.Connection, object_type: str, handle: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, summary, details_json, status, model, prompt_version, error_message, created_at, source
        FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ? AND status = 'success'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (object_type, handle),
    ).fetchone()
    return _recommendation_row_to_dict(row) if row else None


def _fetch_recommendation_event(conn: sqlite3.Connection, object_type: str, handle: str) -> dict[str, Any] | None:
    """Fetch the latest recommendation event (including errors/pending)."""
    row = conn.execute(
        """
        SELECT id, summary, details_json, status, model, prompt_version, error_message, created_at, source
        FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (object_type, handle),
    ).fetchone()
    return _recommendation_row_to_dict(row) if row else None


def _fetch_recommendation_history(conn: sqlite3.Connection, object_type: str, handle: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT summary, details_json, status, model, prompt_version, error_message, created_at, source
        FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (object_type, handle, limit),
    ).fetchall()
    result = []
    for row in rows:
        item = _recommendation_row_to_dict(row)
        item["priority"] = None
        result.append(item)
    return result


def _fetch_workflow(conn: sqlite3.Connection, object_type: str, handle: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT status, notes, updated_at FROM seo_workflow_states WHERE object_type = ? AND handle = ?",
        (object_type, handle),
    ).fetchone()
    return dict(row) if row else None


def fetch_product_detail(conn: sqlite3.Connection, handle: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM products WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    product = dict(row)

    variants = conn.execute(
        "SELECT * FROM product_variants WHERE product_shopify_id = ? ORDER BY position",
        (product["shopify_id"],),
    ).fetchall()
    metafields = conn.execute(
        "SELECT namespace, key, type, value FROM product_metafields WHERE product_shopify_id = ?",
        (product["shopify_id"],),
    ).fetchall()
    collections = conn.execute(
        """
        SELECT c.handle, c.title
        FROM collections c
        JOIN collection_products cp ON cp.collection_shopify_id = c.shopify_id
        WHERE cp.product_shopify_id = ?
        ORDER BY c.title
        """,
        (product["shopify_id"],),
    ).fetchall()
    product_images = conn.execute(
        """
        SELECT shopify_id, url, alt_text, position
        FROM product_images
        WHERE product_shopify_id = ?
        ORDER BY position ASC
        LIMIT 24
        """,
        (product["shopify_id"],),
    ).fetchall()

    return {
        "product": product,
        "variants": variants,
        "metafields": metafields,
        "collections": collections,
        "product_images": [dict(r) for r in product_images],
        "workflow": _fetch_workflow(conn, "product", handle),
        "recommendation": _fetch_recommendation(conn, "product", handle),
        "recommendation_event": _fetch_recommendation_event(conn, "product", handle),
        "recommendation_history": _fetch_recommendation_history(conn, "product", handle),
    }


def fetch_collection_detail(conn: sqlite3.Connection, handle: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM collections WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    collection = dict(row)

    products = conn.execute(
        """
        SELECT cp.product_handle, cp.product_title
        FROM collection_products cp
        WHERE cp.collection_shopify_id = ?
        ORDER BY cp.product_title
        """,
        (collection["shopify_id"],),
    ).fetchall()
    metafields = conn.execute(
        "SELECT namespace, key, type, value FROM collection_metafields WHERE collection_shopify_id = ?",
        (collection["shopify_id"],),
    ).fetchall()

    return {
        "collection": collection,
        "products": products,
        "metafields": metafields,
        "workflow": _fetch_workflow(conn, "collection", handle),
        "recommendation": _fetch_recommendation(conn, "collection", handle),
        "recommendation_event": _fetch_recommendation_event(conn, "collection", handle),
        "recommendation_history": _fetch_recommendation_history(conn, "collection", handle),
    }


def fetch_page_detail(conn: sqlite3.Connection, handle: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM pages WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    page = dict(row)

    page_tokens = _content_tokens_for_page(page)
    title_lower = (page.get("title") or "").lower()
    related_collections = _related_collections_by_token_overlap(
        conn, page_tokens, title_fallback_lower=title_lower, limit=10
    )
    related_products = _related_products_by_token_overlap(conn, page_tokens, limit=20)
    related_pages = _related_pages_by_token_overlap(
        conn, page_tokens, exclude_handle=handle, limit=10
    )

    return {
        "page": page,
        "related_collections": related_collections,
        "related_products": related_products,
        "related_pages": related_pages,
        "workflow": _fetch_workflow(conn, "page", handle),
        "recommendation": _fetch_recommendation(conn, "page", handle),
        "recommendation_event": _fetch_recommendation_event(conn, "page", handle),
        "recommendation_history": _fetch_recommendation_history(conn, "page", handle),
    }


def fetch_blog_article_detail(conn: sqlite3.Connection, blog_handle: str, article_handle: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM blog_articles WHERE blog_handle = ? AND handle = ?",
        (blog_handle, article_handle),
    ).fetchone()
    if not row:
        return None
    article = dict(row)
    composite = blog_article_composite_handle(blog_handle, article_handle)

    article_tokens = _content_tokens_for_blog_article(article)
    title_lower = (article.get("title") or "").lower()
    related_collections = _related_collections_by_token_overlap(
        conn, article_tokens, title_fallback_lower=title_lower, limit=10
    )
    related_products = _related_products_by_token_overlap(conn, article_tokens, limit=20)
    related_pages = _related_pages_by_token_overlap(conn, article_tokens, exclude_handle=None, limit=10)

    return {
        "article": article,
        "related_collections": related_collections,
        "related_products": related_products,
        "related_pages": related_pages,
        "workflow": _fetch_workflow(conn, "blog_article", composite),
        "recommendation": _fetch_recommendation(conn, "blog_article", composite),
        "recommendation_event": _fetch_recommendation_event(conn, "blog_article", composite),
        "recommendation_history": _fetch_recommendation_history(conn, "blog_article", composite),
    }
