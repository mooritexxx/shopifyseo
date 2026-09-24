"""Shopify Admin API reachability validation for internal links.

Some collections (particularly smart collections with only PRODUCT_METAFIELD_DEFINITION
rules) are not visible via Admin GraphQL/REST APIs even though they exist in
Shopify Admin UI and on the storefront. These "API-unreachable" resources cannot
be updated via the API, so we must reject Apply operations that target them.

This module validates that sources/targets are reachable via Admin API before
allowing internal link mutations.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Literal

logger = logging.getLogger(__name__)

ObjectType = Literal["product", "collection", "page", "blog_article"]


class ShopifyResourceUnreachable(ValueError):
    """Raised when a Shopify resource cannot be accessed via Admin API.

    The resource may still exist in Shopify Admin UI and on the storefront,
    but the Admin GraphQL/REST APIs cannot read or write it. This typically
    affects smart collections with certain metafield-only rule configurations.
    """

    def __init__(
        self,
        resource_type: str,
        handle: str,
        shopify_id: str | None,
        *,
        role: str = "resource",
        reason: str = "Admin API cannot access this resource",
    ):
        self.resource_type = resource_type
        self.handle = handle
        self.shopify_id = shopify_id
        self.role = role
        self.reason = reason
        id_part = f" (shopify_id={shopify_id})" if shopify_id else ""
        super().__init__(
            f"{role.capitalize()} {resource_type}/{handle}{id_part}: {reason}. "
            "The resource may still exist in Shopify Admin but is not accessible via API."
        )


def _check_collection_api_reachable(shopify_id: str) -> bool:
    """Check if a collection is reachable via Admin GraphQL API."""
    from ..shopify_catalog_sync.db import fetch_collection_by_id

    try:
        result = fetch_collection_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify collection %s via Admin API", shopify_id, exc_info=True)
        return False


def _check_product_api_reachable(shopify_id: str) -> bool:
    """Check if a product is reachable via Admin GraphQL API."""
    from ..shopify_catalog_sync.db import fetch_product_by_id

    try:
        result = fetch_product_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify product %s via Admin API", shopify_id, exc_info=True)
        return False


def _check_page_api_reachable(shopify_id: str) -> bool:
    """Check if a page is reachable via Admin GraphQL API."""
    from ..shopify_catalog_sync.db import fetch_page_by_id

    try:
        result = fetch_page_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify page %s via Admin API", shopify_id, exc_info=True)
        return False


def _check_article_api_reachable(shopify_id: str) -> bool:
    """Check if a blog article is reachable via Admin GraphQL API."""
    from ..shopify_catalog_sync.db import fetch_article_by_id

    try:
        result = fetch_article_by_id(shopify_id)
        return result is not None
    except Exception:
        logger.warning("Failed to verify article %s via Admin API", shopify_id, exc_info=True)
        return False


def check_resource_api_reachable(
    object_type: ObjectType,
    shopify_id: str,
) -> bool:
    """Check if a resource (product/collection/page/article) is reachable via Admin API.

    Returns True if the resource is accessible via Admin GraphQL API,
    False if the API returns null or an error.
    """
    if object_type == "collection":
        return _check_collection_api_reachable(shopify_id)
    if object_type == "product":
        return _check_product_api_reachable(shopify_id)
    if object_type == "page":
        return _check_page_api_reachable(shopify_id)
    if object_type == "blog_article":
        return _check_article_api_reachable(shopify_id)
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


def is_resource_api_unreachable(
    conn: sqlite3.Connection,
    object_type: ObjectType,
    handle: str,
) -> bool:
    """Check if a resource is marked as api_unreachable in the local DB.

    Currently only implemented for collections. Other types return False.
    """
    if object_type == "collection":
        row = conn.execute(
            "SELECT api_unreachable FROM collections WHERE handle = ?", (handle,)
        ).fetchone()
        return bool(row and row["api_unreachable"])
    return False


def verify_source_api_reachable(
    conn: sqlite3.Connection,
    source_type: ObjectType,
    source_handle: str,
) -> None:
    """Verify the source resource is reachable via Admin API before applying changes.

    Raises ShopifyResourceUnreachable if the source cannot be accessed via Admin API.
    This is required because we cannot push changes to API-unreachable resources.
    """
    shopify_id = get_shopify_id_for_handle(conn, source_type, source_handle)
    if not shopify_id:
        raise ShopifyResourceUnreachable(
            source_type, source_handle, None, role="source",
            reason="No shopify_id found in local database"
        )

    # Check if already marked as unreachable
    if is_resource_api_unreachable(conn, source_type, source_handle):
        raise ShopifyResourceUnreachable(
            source_type, source_handle, shopify_id, role="source",
            reason="Resource is marked as API-unreachable (Admin API cannot access it)"
        )

    # Live check via Admin API
    if not check_resource_api_reachable(source_type, shopify_id):
        # Mark as unreachable for future reference
        if source_type == "collection":
            mark_collection_api_unreachable(conn, source_handle)
        raise ShopifyResourceUnreachable(
            source_type, source_handle, shopify_id, role="source",
            reason="Admin API returned null for this resource"
        )


def is_target_valid_for_suggestions(
    conn: sqlite3.Connection,
    target_type: ObjectType,
    target_handle: str,
) -> bool:
    """Check if a target resource is valid for link suggestions.

    A target is valid if:
    - It exists in the local DB with a shopify_id
    - It is not marked as api_unreachable
    - It is in published/active status

    Does not make live API calls - uses local cached state only for performance.
    """
    if target_type == "product":
        row = conn.execute(
            "SELECT shopify_id, status FROM products WHERE handle = ?", (target_handle,)
        ).fetchone()
        if not row or not row["shopify_id"]:
            return False
        return (row["status"] or "ACTIVE").upper() == "ACTIVE"

    if target_type == "collection":
        row = conn.execute(
            "SELECT shopify_id, api_unreachable FROM collections WHERE handle = ?", (target_handle,)
        ).fetchone()
        if not row or not row["shopify_id"]:
            return False
        return not row["api_unreachable"]

    if target_type == "page":
        row = conn.execute(
            "SELECT shopify_id FROM pages WHERE handle = ?", (target_handle,)
        ).fetchone()
        return bool(row and row["shopify_id"])

    if target_type == "blog_article":
        blog_h, _, article_h = target_handle.partition("/")
        row = conn.execute(
            "SELECT shopify_id, is_published FROM blog_articles WHERE blog_handle = ? AND handle = ?",
            (blog_h, article_h),
        ).fetchone()
        return bool(row and row["shopify_id"] and row["is_published"])

    return False


def mark_collection_api_unreachable(conn: sqlite3.Connection, handle: str) -> None:
    """Mark a collection as API-unreachable (cannot be read/written via Admin API)."""
    conn.execute(
        "UPDATE collections SET api_unreachable = 1 WHERE handle = ?",
        (handle,),
    )
    conn.commit()
    logger.info("Marked collection %s as API-unreachable", handle)


def mark_collection_api_reachable(conn: sqlite3.Connection, handle: str) -> None:
    """Clear the API-unreachable flag for a collection (it's now accessible)."""
    conn.execute(
        "UPDATE collections SET api_unreachable = 0 WHERE handle = ?",
        (handle,),
    )
    conn.commit()
    logger.info("Marked collection %s as API-reachable", handle)
