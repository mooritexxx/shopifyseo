"""Storefront URL helpers and the internal-link allowlist builder.

The base URL cache is module-level state, so callers that need to invalidate
it (e.g. when the user changes their custom domain in Settings) should call
:func:`clear_base_url_cache` rather than mutate ``_BASE_URL_CACHE`` directly.
"""
from __future__ import annotations

import os
import sqlite3


_BASE_URL_CACHE: str | None = None


_OBJECT_PATH_PREFIX: dict[str, str] = {
    "product": "/products",
    "collection": "/collections",
    "page": "/pages",
    "blog": "/blogs",
}


DEFAULT_INTERNAL_LINK_CAPS: dict[str, int] = {
    "collection": 24,
    "product": 40,
    "page": 20,
    "blog_article": 20,
}


def clear_base_url_cache() -> None:
    """Reset the cached base URL. Call after the user changes the custom domain."""
    global _BASE_URL_CACHE
    _BASE_URL_CACHE = None


def _base_store_url(conn: sqlite3.Connection | None = None) -> str:
    """Return the canonical storefront base URL (no trailing slash).

    Single source of truth: the ``store_custom_domain`` setting on the
    Settings page (persisted in the DB, mirrored to SHOPIFY_STORE_URL env).
    Falls back to SHOPIFY_SHOP only when no custom domain is configured.
    """
    global _BASE_URL_CACHE
    if _BASE_URL_CACHE:
        return _BASE_URL_CACHE

    # 1. Check DB setting (the authoritative source)
    custom = ""
    try:
        from .. import dashboard_google as _dg
        _c = conn
        _own = _c is None
        if _own:
            from ..dashboard_store import db_connect
            _c = db_connect()
        try:
            custom = (_dg.get_service_setting(_c, "store_custom_domain") or "").strip().rstrip("/")
        finally:
            if _own:
                _c.close()
    except Exception:
        pass

    if not custom:
        custom = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")

    if custom:
        if not custom.startswith("http"):
            custom = f"https://{custom}"
        _BASE_URL_CACHE = custom
        return _BASE_URL_CACHE

    # Fallback: derive from SHOPIFY_SHOP env var
    shop = os.getenv("SHOPIFY_SHOP", "").strip().rstrip("/")
    if shop:
        shop = shop.replace("https://", "").replace("http://", "").rstrip("/")
        if not shop.endswith(".myshopify.com"):
            shop = f"{shop}.myshopify.com"
        _BASE_URL_CACHE = f"https://{shop}"
        return _BASE_URL_CACHE

    _BASE_URL_CACHE = ""
    return _BASE_URL_CACHE


def blog_article_composite_handle(blog_handle: str, article_handle: str) -> str:
    return f"{blog_handle}/{article_handle}"


def object_url(object_type: str, handle: str) -> str:
    """Return the canonical URL for a store object."""
    base = _base_store_url()
    return object_url_with_base(base, object_type, handle)


def object_url_with_base(base_url: str, object_type: str, handle: str) -> str:
    """Return storefront URL for *handle* using explicit *base_url* (no trailing slash).

    *object_type* is ``collection``, ``product``, ``page``, or ``blog_article``.
    Blog handles use ``blog_handle/article_slug`` composite form.
    If *base_url* is empty, returns a root-relative path (starts with ``/``).
    """
    base = (base_url or "").strip().rstrip("/")
    if object_type == "blog_article":
        blog_h, sep, article_h = handle.partition("/")
        if sep and article_h:
            path = f"/blogs/{blog_h}/{article_h}"
        else:
            path = f"/blogs/{handle}"
        return f"{base}{path}" if base else path
    prefix = _OBJECT_PATH_PREFIX.get(object_type, "")
    if not prefix:
        return base or ""
    path = f"{prefix}/{handle}"
    return f"{base}{path}" if base else path


def build_store_internal_link_allowlist(
    conn: sqlite3.Connection,
    base_url: str,
    *,
    rag_results: list[dict] | None = None,
    caps: dict[str, int] | None = None,
) -> tuple[list[dict], frozenset[str], frozenset[str]]:
    """Build canonical internal link targets for prompts and HTML sanitization.

    Returns ``(targets, allowed_full_urls, allowed_paths)`` where *targets* are
    dicts ``{"type", "handle", "title", "url"}`` sorted for prompt injection
    (RAG hits first per type, then alphabetical DB fill up to caps).

    *allowed_full_urls* includes every ``url`` plus alternate forms (e.g. with
    trailing slash stripped). *allowed_paths* is normalized path keys like
    ``/collections/foo`` (no trailing slash).
    """
    caps = {**DEFAULT_INTERNAL_LINK_CAPS, **(caps or {})}
    rag_results = rag_results or []
    base = (base_url or "").strip().rstrip("/")

    def _blog_composite(bh: str, ah: str) -> str:
        return f"{bh}/{ah}"

    collections: list[tuple[str, str]] = []
    products: list[tuple[str, str]] = []
    pages: list[tuple[str, str]] = []
    blogs: list[tuple[str, str, str]] = []
    try:
        for r in conn.execute(
            "SELECT handle, title FROM collections WHERE handle IS NOT NULL AND TRIM(handle) != '' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            h = (r[0] or "").strip()
            if h:
                collections.append((h, (r[1] or h).strip() or h))
        for r in conn.execute(
            "SELECT handle, title FROM products WHERE handle IS NOT NULL AND TRIM(handle) != '' "
            "AND (status IS NULL OR status = '' OR UPPER(status) = 'ACTIVE') "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            h = (r[0] or "").strip()
            if h:
                products.append((h, (r[1] or h).strip() or h))
        for r in conn.execute(
            "SELECT handle, title FROM pages WHERE handle IS NOT NULL AND TRIM(handle) != '' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            h = (r[0] or "").strip()
            if h:
                pages.append((h, (r[1] or h).strip() or h))
        for r in conn.execute(
            "SELECT blog_handle, handle, title FROM blog_articles "
            "WHERE blog_handle IS NOT NULL AND TRIM(blog_handle) != '' "
            "AND handle IS NOT NULL AND TRIM(handle) != '' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            bh = (r[0] or "").strip()
            ah = (r[1] or "").strip()
            if bh and ah:
                blogs.append((bh, ah, (r[2] or ah).strip() or ah))
    except Exception:
        collections, products, pages, blogs = [], [], [], []

    coll_map = {h: t for h, t in collections}
    prod_map = {h: t for h, t in products}
    page_map = {h: t for h, t in pages}
    blog_map = {_blog_composite(bh, ah): (bh, ah, title) for bh, ah, title in blogs}

    rag_priority: dict[str, list[str]] = {"collection": [], "product": [], "blog_article": []}
    for r in rag_results:
        ot = r.get("object_type")
        oh = (r.get("object_handle") or "").strip()
        if ot in rag_priority and oh and oh not in rag_priority[ot]:
            rag_priority[ot].append(oh)

    def _pick(
        kind: str,
        rag_handles: list[str],
        id_set: set[str],
        ordered_items: list[tuple],
        cap: int,
        url_type: str,
    ) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        def _add(handle_key: str, title: str) -> None:
            if handle_key in seen or len(out) >= cap:
                return
            if handle_key not in id_set:
                return
            seen.add(handle_key)
            url = object_url_with_base(base, url_type, handle_key)
            out.append({"type": url_type, "handle": handle_key, "title": title, "url": url})

        for h in rag_handles:
            if kind == "blog":
                if h in blog_map:
                    _bh, _ah, title = blog_map[h]
                    _add(h, title)
            elif h in id_set:
                title = (coll_map if kind == "coll" else prod_map if kind == "prod" else page_map).get(h, h)
                _add(h, title)

        for item in ordered_items:
            if len(out) >= cap:
                break
            if kind == "blog":
                bh, ah, title = item
                key = _blog_composite(bh, ah)
                _add(key, title)
            else:
                h, title = item
                _add(h, title)
        return out

    targets: list[dict] = []
    targets.extend(
        _pick(
            "coll",
            rag_priority["collection"],
            set(coll_map),
            collections,
            caps.get("collection", 24),
            "collection",
        )
    )
    targets.extend(
        _pick(
            "prod",
            rag_priority["product"],
            set(prod_map),
            products,
            caps.get("product", 40),
            "product",
        )
    )
    targets.extend(
        _pick("page", [], set(page_map), pages, caps.get("page", 20), "page")
    )
    targets.extend(
        _pick(
            "blog",
            rag_priority["blog_article"],
            set(blog_map),
            blogs,
            caps.get("blog_article", 20),
            "blog_article",
        )
    )

    allowed_full: set[str] = set()
    allowed_paths: set[str] = set()
    from urllib.parse import urlparse

    for t in targets:
        u = (t.get("url") or "").strip()
        if not u:
            continue
        allowed_full.add(u)
        allowed_full.add(u.rstrip("/"))
        if u.startswith("http"):
            p = urlparse(u).path or ""
            if p:
                allowed_paths.add(p.rstrip("/") or "/")
        elif u.startswith("/"):
            allowed_paths.add(u.rstrip("/") or "/")

    return targets, frozenset(allowed_full), frozenset(allowed_paths)
