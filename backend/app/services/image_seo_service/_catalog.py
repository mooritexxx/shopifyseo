"""Catalog image SEO listing — products, collections, pages, and articles."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.app.db import open_db_connection
from shopifyseo.db import backend_for_connection, order_ci
from shopifyseo.catalog_image_work import catalog_url_cache_key_from_norm
from shopifyseo.dashboard_ai_engine_parts.images import vision_suggest_catalog_image_alt
from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings
from shopifyseo.dashboard_store import DB_PATH
from shopifyseo.html_images import extract_shopify_images_from_html, is_shopify_hosted_image_url
from shopifyseo.product_image_seo import (
    filename_from_image_url,
    image_format_label_from_mime,
    image_format_label_from_url,
    is_missing_or_generic_alt,
    is_probably_webp_url,
    is_weak_image_filename,
    normalize_shopify_image_url,
    product_image_seo_suggested_filename,
    stable_seo_filename_suffix,
)
from shopifyseo.shopify_image_cache import (
    catalog_gallery_image_cached_locally,
    image_cache_root,
    product_image_file_cache_index,
)
from shopifyseo.shopify_product_media import download_image_bytes

logger = logging.getLogger(__name__)


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"


def _as_int_dim(v: Any) -> int | None:
    if v is None:
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        return None
    return i if i > 0 else None


def _featured_url_by_product(conn: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in conn.execute(
        "SELECT shopify_id, featured_image_json FROM products WHERE featured_image_json IS NOT NULL AND featured_image_json != ''"
    ):
        try:
            data = json.loads(row[1])
        except json.JSONDecodeError:
            continue
        u = (data.get("url") or "").strip()
        if u:
            out[row[0]] = u
    return out


def _variants_by_product(conn: Any) -> dict[str, list[tuple[str, str, str]]]:
    m: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT product_shopify_id, shopify_id, title, image_json FROM product_variants WHERE image_json IS NOT NULL AND image_json != ''"
    ):
        try:
            im = json.loads(row[3])
        except json.JSONDecodeError:
            continue
        u = (im.get("url") or "").strip()
        if u:
            m[row[0]].append((row[1], (row[2] or "").strip(), u))
    return m


def _role_and_variants(
    product_id: str,
    image_url: str,
    featured_by_product: dict[str, str],
    variants_map: dict[str, list[tuple[str, str, str]]],
) -> tuple[list[str], str, list[str], bool]:
    norm = normalize_shopify_image_url(image_url)
    roles: list[str] = []
    fu = featured_by_product.get(product_id)
    is_featured = bool(fu and normalize_shopify_image_url(fu) == norm)
    if is_featured:
        roles.append("featured")
    roles.append("gallery")

    vlabels: list[str] = []
    for _vid, vtitle, vurl in variants_map.get(product_id, []):
        if normalize_shopify_image_url(vurl) == norm:
            if vtitle and vtitle not in vlabels:
                vlabels.append(vtitle)

    if vlabels:
        role_for = "variant"
        roles.append("variant")
    elif is_featured:
        role_for = "featured"
    else:
        role_for = "gallery"

    return roles, role_for, vlabels, is_featured


def _product_gallery_norm_urls(
    conn: Any,
    product_id: str,
    featured_by_product: dict[str, str],
) -> set[str]:
    s: set[str] = set()
    for (u,) in conn.execute(
        "SELECT url FROM product_images WHERE product_shopify_id = ?",
        (product_id,),
    ):
        s.add(normalize_shopify_image_url(u))
    fu = featured_by_product.get(product_id)
    if fu:
        s.add(normalize_shopify_image_url(fu))
    return s


def _product_gallery_seo_suffix_seed(
    product_shopify_id: str,
    role_for: str,
    position: int | None,
    variant_join: str | None,
) -> str:
    """Stable across Shopify media replace (MediaImage GID changes). Same gallery slot → same 4-char suffix."""
    pos = int(position) if position is not None else -1
    v = (variant_join or "").strip()
    return f"{product_shopify_id}|{role_for}|{pos}|{v}"


def _legacy_product_image_seo_suggested_filename(
    *,
    product_handle: str,
    role: str,
    gallery_position: int | None,
    variant_label: str | None,
    collision_suffix: str,
) -> str:
    """Old product naming kept only so previously optimized variant images stay accepted."""
    vjoin = (variant_label or "").strip()
    pos = gallery_position if gallery_position is not None else 1
    vslug = ""
    if role == "variant" and vjoin:
        from shopifyseo.seo_slug import slugify_article_handle

        vslug = slugify_article_handle(vjoin, max_len=16)
    base = product_image_seo_suggested_filename(
        product_handle=product_handle,
        role="gallery" if role == "variant" else role,
        gallery_position=pos,
        ext=".webp",
        collision_suffix=collision_suffix,
    )
    if role != "variant" or pos > 1:
        return base
    stem = base.rsplit(".", 1)[0]
    suffix = collision_suffix[:2]
    handle_part = stem[: -len(suffix) - 1] if stem.endswith(f"-{suffix}") else stem
    parts = [handle_part]
    if vslug:
        parts.append(vslug)
    parts.extend(["1", suffix])
    return ("-".join(parts) + ".webp").lower()
