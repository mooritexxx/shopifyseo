"""Canonical Shopify-hosted image URL sets for sync discovery and local cache warm.

Counts are **distinct normalized URLs** (same CDN object with different width params counts once).
Cache keys: product ``product_images.shopify_id`` when available, else ``catalogurl:<sha256(norm)>``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from shopifyseo.html_images import extract_shopify_images_from_html, is_shopify_hosted_image_url
from shopifyseo.product_image_seo import normalize_shopify_image_url
from shopifyseo.shopify_catalog_sync.products import _product_images_for_upsert
from shopifyseo.theme_template_images import collect_template_image_urls_for_pages


def catalog_url_cache_key_from_norm(norm: str) -> str:
    return "catalogurl:" + hashlib.sha256(norm.encode("utf-8")).hexdigest()


def _url_from_image_dict(node: dict[str, Any] | None) -> str | None:
    if not node or not isinstance(node, dict):
        return None
    u = (node.get("url") or "").strip()
    return u or None


def _urls_from_json_image_column(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(d, dict):
        return []
    u = _url_from_image_dict(d)
    return [u] if u else []


def _urls_from_template_images_column(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(d, list):
        return []
    return [str(x).strip() for x in d if str(x).strip()]


class CatalogImageRegistry:
    """norm_url -> one cache key + representative URL for HTTP fetch."""

    __slots__ = ("_norm_to_key", "_key_to_url")

    def __init__(self) -> None:
        self._norm_to_key: dict[str, str] = {}
        self._key_to_url: dict[str, str] = {}

    def register(self, url: str, *, preferred_key: str | None = None) -> None:
        raw = (url or "").strip()
        if not raw or not is_shopify_hosted_image_url(raw):
            return
        norm = normalize_shopify_image_url(raw)
        if not norm:
            return
        if norm in self._norm_to_key:
            existing = self._norm_to_key[norm]
            new_gid = preferred_key and preferred_key.startswith("gid://")
            old_cat = existing.startswith("catalogurl:")
            if new_gid and old_cat and preferred_key is not None:
                del self._key_to_url[existing]
                self._norm_to_key[norm] = preferred_key
                self._key_to_url[preferred_key] = raw
            return
        if preferred_key and preferred_key.startswith("gid://"):
            key = preferred_key
        else:
            key = catalog_url_cache_key_from_norm(norm)
        self._norm_to_key[norm] = key
        self._key_to_url[key] = raw

    def __len__(self) -> int:
        return len(self._norm_to_key)

    def work_items(self) -> list[tuple[str, str, str]]:
        """(cache_id, fetch_url, normalized_url) for warm."""
        out: list[tuple[str, str, str]] = []
        for key, url in self._key_to_url.items():
            norm = normalize_shopify_image_url(url)
            if key and url and norm:
                out.append((key, url, norm))
        return out

    def expected_cache_ids(self) -> frozenset[str]:
        return frozenset(self._key_to_url.keys())


def _register_product_dicts(products: list[dict[str, Any]], reg: CatalogImageRegistry) -> None:
    for product in products:
        for img in _product_images_for_upsert(product):
            uid = (img.get("id") or "").strip()
            u = (img.get("url") or "").strip()
            if u and uid:
                reg.register(u, preferred_key=uid)
            elif u:
                reg.register(u, preferred_key=None)
        fi = product.get("featuredImage")
        if isinstance(fi, dict):
            u = _url_from_image_dict(fi)
            kid = (fi.get("id") or "").strip() or None
            if u:
                reg.register(u, preferred_key=kid)
        for edge in (product.get("variants") or {}).get("edges", []) or []:
            node = (edge or {}).get("node") or {}
            vi = node.get("image")
            if isinstance(vi, dict):
                u = _url_from_image_dict(vi)
                kid = (vi.get("id") or "").strip() or None
                if u:
                    reg.register(u, preferred_key=kid)
        for u, _alt in extract_shopify_images_from_html(product.get("descriptionHtml")):
            reg.register(u, preferred_key=None)


def _register_collection_dicts(collections: list[dict[str, Any]], reg: CatalogImageRegistry) -> None:
    for coll in collections:
        img = coll.get("image")
        if isinstance(img, dict):
            u = _url_from_image_dict(img)
            kid = (img.get("id") or "").strip() or None
            if u:
                reg.register(u, preferred_key=kid)
        for u, _alt in extract_shopify_images_from_html(coll.get("descriptionHtml")):
            reg.register(u, preferred_key=None)


def _register_page_dicts(
    pages: list[dict[str, Any]],
    reg: CatalogImageRegistry,
    *,
    template_urls_by_page_id: dict[str, list[str]] | None,
) -> None:
    for page in pages:
        pid = str(page.get("id") or "")
        for u, _alt in extract_shopify_images_from_html(page.get("body")):
            reg.register(u, preferred_key=None)
        if template_urls_by_page_id and pid:
            for u in template_urls_by_page_id.get(pid) or []:
                reg.register(u, preferred_key=None)


def _register_article_dicts(articles: list[dict[str, Any]], reg: CatalogImageRegistry) -> None:
    for article in articles:
        img = article.get("image")
        if isinstance(img, dict):
            u = _url_from_image_dict(img)
            kid = (img.get("id") or "").strip() or None
            if u:
                reg.register(u, preferred_key=kid)
        for u, _alt in extract_shopify_images_from_html(article.get("body")):
            reg.register(u, preferred_key=None)
        for u, _alt in extract_shopify_images_from_html(article.get("summary")):
            reg.register(u, preferred_key=None)


def build_catalog_image_registry_from_discovery_payloads(
    *,
    products: list[dict[str, Any]],
    collections: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    articles_by_blog_id: dict[str, list[dict[str, Any]]],
    template_urls_by_page_id: dict[str, list[str]] | None = None,
) -> CatalogImageRegistry:
    """Same URL rules as post-sync DB (theme template URLs must match enrich)."""
    reg = CatalogImageRegistry()
    _register_product_dicts(products, reg)
    _register_collection_dicts(collections, reg)
    _register_page_dicts(pages, reg, template_urls_by_page_id=template_urls_by_page_id)
    for arts in articles_by_blog_id.values():
        _register_article_dicts(list(arts), reg)
    return reg


def count_catalog_image_urls_discover(
    products: list[dict[str, Any]],
    collections: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    articles_by_blog_id: dict[str, list[dict[str, Any]]],
) -> int:
    """Exact distinct image URL count for discovery payloads (includes theme template URLs when API works)."""
    tpl = collect_template_image_urls_for_pages(pages, theme_id=None)
    reg = build_catalog_image_registry_from_discovery_payloads(
        products=products,
        collections=collections,
        pages=pages,
        articles_by_blog_id=articles_by_blog_id,
        template_urls_by_page_id=tpl,
    )
    return len(reg)


def build_catalog_image_registry_from_db(conn: sqlite3.Connection) -> CatalogImageRegistry:
    """Mirror rows the sync pipeline writes: products, collections, pages, blog articles."""
    reg = CatalogImageRegistry()

    for row in conn.execute("SELECT shopify_id, url FROM product_images").fetchall():
        sid = (row["shopify_id"] or "").strip()
        url = (row["url"] or "").strip()
        if sid and url:
            reg.register(url, preferred_key=sid)

    for row in conn.execute(
        "SELECT featured_image_json, description_html FROM products"
    ).fetchall():
        for u in _urls_from_json_image_column(row["featured_image_json"]):
            reg.register(u, preferred_key=None)
        for u, _alt in extract_shopify_images_from_html(row["description_html"]):
            reg.register(u, preferred_key=None)

    for row in conn.execute("SELECT image_json FROM product_variants WHERE image_json IS NOT NULL").fetchall():
        raw = row["image_json"]
        if not raw or not str(raw).strip():
            continue
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict):
            continue
        u = _url_from_image_dict(d)
        kid = (d.get("id") or "").strip() or None
        if u:
            reg.register(u, preferred_key=kid if kid and kid.startswith("gid://") else None)

    for row in conn.execute("SELECT image_json, description_html FROM collections").fetchall():
        for u in _urls_from_json_image_column(row["image_json"]):
            reg.register(u, preferred_key=None)
        for u, _alt in extract_shopify_images_from_html(row["description_html"]):
            reg.register(u, preferred_key=None)

    for row in conn.execute("SELECT body, template_images_json FROM pages").fetchall():
        for u, _alt in extract_shopify_images_from_html(row["body"]):
            reg.register(u, preferred_key=None)
        for u in _urls_from_template_images_column(row["template_images_json"]):
            reg.register(u, preferred_key=None)

    for row in conn.execute("SELECT image_json, body, summary FROM blog_articles").fetchall():
        for u in _urls_from_json_image_column(row["image_json"]):
            reg.register(u, preferred_key=None)
        for u, _alt in extract_shopify_images_from_html(row["body"]):
            reg.register(u, preferred_key=None)
        for u, _alt in extract_shopify_images_from_html(row["summary"]):
            reg.register(u, preferred_key=None)

    return reg


def count_catalog_images_for_cache_db(conn: sqlite3.Connection) -> int:
    return len(build_catalog_image_registry_from_db(conn))
