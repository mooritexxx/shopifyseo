"""One-shot discovery of Shopify catalog sizes and payloads before upsert phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .db import (
    fetch_all_articles_for_blog,
    fetch_all_blogs,
    fetch_all_collections,
    fetch_all_pages,
    fetch_all_products,
)


@dataclass(frozen=True)
class ShopifyCatalogDiscovery:
    products: list[dict[str, Any]]
    collections: list[dict[str, Any]]
    pages: list[dict[str, Any]]
    blogs: list[dict[str, Any]]
    articles_by_blog_id: dict[str, list[dict[str, Any]]]
    blog_articles_total: int


def discover_shopify_catalog(
    page_size: int,
    cancel_check: Callable[[], None] | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
) -> ShopifyCatalogDiscovery:
    """Fetch lists once; sum article counts per blog. Used to fix totals before sync.

    ``progress_callback(kind, count)`` is invoked after each major step so sync UI
    can show aggregate progress and ETA while discovery runs (counts only, done=0).
    Kinds: ``products``, ``collections``, ``pages``, ``blogs``, ``blog_articles`` (running total).
    """
    products = fetch_all_products(page_size, after_page=None)
    if progress_callback is not None:
        progress_callback("products", len(products))
    if cancel_check:
        cancel_check()
    collections = fetch_all_collections(page_size)
    if progress_callback is not None:
        progress_callback("collections", len(collections))
    if cancel_check:
        cancel_check()
    pages = fetch_all_pages(page_size)
    if progress_callback is not None:
        progress_callback("pages", len(pages))
    if cancel_check:
        cancel_check()
    blogs = fetch_all_blogs(page_size)
    if progress_callback is not None:
        progress_callback("blogs", len(blogs))
    articles_by_blog_id: dict[str, list[dict[str, Any]]] = {}
    blog_articles_total = 0
    for blog in blogs:
        if cancel_check:
            cancel_check()
        bid = str(blog.get("id") or "")
        arts = fetch_all_articles_for_blog(blog["id"], page_size)
        articles_by_blog_id[bid] = arts
        blog_articles_total += len(arts)
        if progress_callback is not None:
            progress_callback("blog_articles", blog_articles_total)
    return ShopifyCatalogDiscovery(
        products=products,
        collections=collections,
        pages=pages,
        blogs=blogs,
        articles_by_blog_id=articles_by_blog_id,
        blog_articles_total=blog_articles_total,
    )
