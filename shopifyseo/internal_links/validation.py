"""Shopify existence validation for internal links.

Ensures link sources/targets still exist in Shopify before apply/generation.
Ghost rows (local-only with no Shopify counterpart) are rejected.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Literal

logger = logging.getLogger(__name__)

ObjectType = Literal["product", "collection", "page", "blog_article"]


class ShopifyResourceMissing(ValueError):
    """Raised when a Shopify resource (source or target) no longer exists."""

    def __init__(
        self,
        resource_type: str,
        handle: str,
        shopify_id: str | None,
        *,
        role: str = "resource",
    ):
        self.resource_type = resource_type
        self.handle = handle
        self.shopify_id = shopify_id
        self.role = role
        id_part = f" (shopify_id={shopify_id})" if shopify_id else ""
        super().__init__(
            f"{role.capitalize()} {resource_type}/{handle}{id_part} not found in Shopify"
        )


def _verify_collection_exists_in_shopify(shopify_id: str) -> bool:
    """Check if a collection still exists in Shopify by GID."""
    from ..shopify_catalog_sync.db import fetch_collection_by_id

    try:
        result = fetch_collection_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify collection %s in Shopify", shopify_id, exc_info=True)
        return False


def _verify_product_exists_in_shopify(shopify_id: str) -> bool:
    """Check if a product still exists in Shopify by GID."""
    from ..shopify_catalog_sync.db import fetch_product_by_id

    try:
        result = fetch_product_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify product %s in Shopify", shopify_id, exc_info=True)
        return False


def _verify_page_exists_in_shopify(shopify_id: str) -> bool:
    """Check if a page still exists in Shopify by GID."""
    from ..shopify_catalog_sync.db import fetch_page_by_id

    try:
        result = fetch_page_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify page %s in Shopify", shopify_id, exc_info=True)
        return False


def _verify_article_exists_in_shopify(shopify_id: str) -> bool:
    """Check if a blog article still exists in Shopify by GID."""
    from ..shopify_catalog_sync.db import fetch_article_by_id

    try:
        result = fetch_article_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify article %s in Shopify", shopify_id, exc_info=True)
        return False


def verify_resource_exists_in_shopify(
    object_type: ObjectType,
    shopify_id: str,
) -> bool:
    """Verify a resource (product/collection/page/article) exists in Shopify.

    Returns True if the resource exists, False if it was deleted or cannot be found.
    """
    if object_type == "collection":
        return _verify_collection_exists_in_shopify(shopify_id)
    if object_type == "product":
        return _verify_product_exists_in_shopify(shopify_id)
    if object_type == "page":
        return _verify_page_exists_in_shopify(shopify_id)
    if object_type == "blog_article":
        return _verify_article_exists_in_shopify(shopify_id)
    return False


def get_shopify_id_for_handle(
    conn: sqlite3.Connection,
    object_type: ObjectType,
    handle: str,
) -> str | None:
    """Look up the Shopify GID from the local SQLite for a given handle."""
    if object_type == "blog_article":
        blog_h, _, article_h = handle.partition("/")
        row = conn.execute(
            "SELECT shopify_id FROM blog_articles WHERE blog_handle = ? AND handle = ?",
            (blog_h, article_h),
        ).fetchone()
    elif object_type == "product":
        row = conn.execute(
            "SELECT shopify_id FROM products WHERE handle = ?", (handle,)
        ).fetchone()
    elif object_type == "collection":
        row = conn.execute(
            "SELECT shopify_id FROM collections WHERE handle = ?", (handle,)
        ).fetchone()
    elif object_type == "page":
        row = conn.execute(
            "SELECT shopify_id FROM pages WHERE handle = ?", (handle,)
        ).fetchone()
    else:
        return None
    return row["shopify_id"] if row else None


def verify_source_exists_in_shopify(
    conn: sqlite3.Connection,
    source_type: ObjectType,
    source_handle: str,
) -> None:
    """Verify the source resource exists in Shopify before applying internal link changes.

    Raises ShopifyResourceMissing if the source is a ghost (local-only).
    """
    shopify_id = get_shopify_id_for_handle(conn, source_type, source_handle)
    if not shopify_id:
        raise ShopifyResourceMissing(source_type, source_handle, None, role="source")

    if not verify_resource_exists_in_shopify(source_type, shopify_id):
        raise ShopifyResourceMissing(source_type, source_handle, shopify_id, role="source")


def verify_target_exists_in_shopify(
    conn: sqlite3.Connection,
    target_type: ObjectType,
    target_handle: str,
) -> bool:
    """Check if a target resource exists in Shopify (for suggestion generation filtering).

    Returns True if target exists in Shopify, False if it's a ghost.
    Does not raise — intended for filtering during suggestion generation.
    """
    shopify_id = get_shopify_id_for_handle(conn, target_type, target_handle)
    if not shopify_id:
        return False
    return verify_resource_exists_in_shopify(target_type, shopify_id)


def mark_collection_deleted(conn: sqlite3.Connection, handle: str) -> None:
    """Mark a collection as deleted by clearing its shopify_id (soft-delete marker)."""
    conn.execute(
        "UPDATE collections SET shopify_id = NULL WHERE handle = ?",
        (handle,),
    )
    conn.commit()
    logger.info("Marked collection %s as deleted (cleared shopify_id)", handle)


def mark_product_deleted(conn: sqlite3.Connection, handle: str) -> None:
    """Mark a product as deleted by setting status to ARCHIVED."""
    conn.execute(
        "UPDATE products SET status = 'ARCHIVED' WHERE handle = ?",
        (handle,),
    )
    conn.commit()
    logger.info("Marked product %s as deleted (set status=ARCHIVED)", handle)


def mark_page_deleted(conn: sqlite3.Connection, handle: str) -> None:
    """Mark a page as deleted by clearing its shopify_id."""
    conn.execute(
        "UPDATE pages SET shopify_id = NULL WHERE handle = ?",
        (handle,),
    )
    conn.commit()
    logger.info("Marked page %s as deleted (cleared shopify_id)", handle)
