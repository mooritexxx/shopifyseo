"""One-shot discovery of Shopify catalog sizes and payloads before upsert phases."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable

from .db import (
    fetch_all_articles_for_blog,
    fetch_all_blogs,
    fetch_all_collections,
    fetch_all_pages,
    fetch_all_products,
)


# Shopify caps GraphQL connections at 250 nodes per page.
MAX_SHOPIFY_PAGE_SIZE = 250


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

    ``progress_callback(kind, count)`` reports a running count. Products report after
    **every page**, not just at the end: fetching the catalog takes most of discovery, and
    reporting only on completion left the UI frozen on one label for the whole time.

    The four list fetches are independent, so they run concurrently. Blog articles depend
    on the blog list and follow it.

    Kinds: ``products``, ``collections``, ``pages``, ``blogs``, ``blog_articles``.
    """

    def _report(kind: str, count: int) -> None:
        if progress_callback is not None:
            progress_callback(kind, count)

    # Shopify caps connections at 250 per page. The heavy product query still fits well
    # inside the per-query cost limit at that size (measured: 772 of 1000 requested).
    effective_page_size = max(1, min(int(page_size or 1), MAX_SHOPIFY_PAGE_SIZE))

    if cancel_check:
        cancel_check()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            "products": pool.submit(
                fetch_all_products, effective_page_size, after_page=lambda n: _report("products", n)
            ),
            "collections": pool.submit(fetch_all_collections, effective_page_size),
            "pages": pool.submit(fetch_all_pages, effective_page_size),
            "blogs": pool.submit(fetch_all_blogs, effective_page_size),
        }
        products = futures["products"].result()
        collections = futures["collections"].result()
        pages = futures["pages"].result()
        blogs = futures["blogs"].result()

    _report("products", len(products))
    _report("collections", len(collections))
    _report("pages", len(pages))
    _report("blogs", len(blogs))

    if cancel_check:
        cancel_check()

    articles_by_blog_id: dict[str, list[dict[str, Any]]] = {}
    blog_articles_total = 0
    for blog in blogs:
        if cancel_check:
            cancel_check()
        bid = str(blog.get("id") or "")
        arts = fetch_all_articles_for_blog(blog["id"], effective_page_size)
        articles_by_blog_id[bid] = arts
        blog_articles_total += len(arts)
        _report("blog_articles", blog_articles_total)
    return ShopifyCatalogDiscovery(
        products=products,
        collections=collections,
        pages=pages,
        blogs=blogs,
        articles_by_blog_id=articles_by_blog_id,
        blog_articles_total=blog_articles_total,
    )
