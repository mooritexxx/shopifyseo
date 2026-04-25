"""Catalog image SEO listing — products, collections, pages, and articles."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from backend.app.db import open_db_connection
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


def _featured_url_by_product(conn: sqlite3.Connection) -> dict[str, str]:
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


def _variants_by_product(conn: sqlite3.Connection) -> dict[str, list[tuple[str, str, str]]]:
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
    conn: sqlite3.Connection,
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


def _catalog_image_row(
    *,
    resource_type: str,
    resource_shopify_id: str,
    resource_handle: str,
    resource_title: str,
    image_row_id: str,
    url: str,
    alt_text: str,
    position: int | None,
    roles: list[str],
    role_for: str,
    variant_labels: list[str],
    blog_handle: str = "",
    article_handle: str = "",
    optimize_supported: bool = False,
    image_shopify_id: str = "",
    local_file_cached: bool | None = None,
    image_width: int | None = None,
    image_height: int | None = None,
    cached_mime: str = "",
    file_size_bytes: int | None = None,
) -> dict[str, Any]:
    miss_alt = is_missing_or_generic_alt(alt_text)
    is_featured = "featured" in roles
    vjoin = ", ".join(variant_labels[:3]) if variant_labels else None
    rh = resource_handle or "item"
    if optimize_supported and resource_type == "product":
        seed = _product_gallery_seo_suffix_seed(resource_shopify_id, role_for, position, vjoin)
    elif optimize_supported and resource_type == "collection":
        seed = f"{resource_shopify_id}|featured"
    else:
        seed = (image_shopify_id or "").strip() or (image_row_id or "x")
    suffix = stable_seo_filename_suffix(seed)
    suggested_fn = product_image_seo_suggested_filename(
        product_handle=rh,
        role=role_for,
        gallery_position=position,
        variant_label=vjoin,
        ext=".webp",
        collision_suffix=suffix,
    )
    acceptable_names = {suggested_fn.lower()}
    # Media replace assigns a new GID; uploads before slot-based seed used suffix(media_gid).
    if optimize_supported and resource_type == "product" and (image_shopify_id or "").strip():
        leg_suf = stable_seo_filename_suffix((image_shopify_id or "").strip())
        if leg_suf != suffix:
            acceptable_names.add(
                product_image_seo_suggested_filename(
                    product_handle=rh,
                    role=role_for,
                    gallery_position=position,
                    variant_label=vjoin,
                    ext=".webp",
                    collision_suffix=leg_suf,
                ).lower()
            )
        acceptable_names.add(
            _legacy_product_image_seo_suggested_filename(
                product_handle=rh,
                role=role_for,
                gallery_position=position,
                variant_label=vjoin,
                collision_suffix=suffix,
            )
        )
        if leg_suf != suffix:
            acceptable_names.add(
                _legacy_product_image_seo_suggested_filename(
                    product_handle=rh,
                    role=role_for,
                    gallery_position=position,
                    variant_label=vjoin,
                    collision_suffix=leg_suf,
                )
            )
    current_fn = (filename_from_image_url(url) or "").strip()
    # New optimizations should use the clean SEO template; legacy product names are added above.
    weak_fn = is_weak_image_filename(url)
    seo_filename_mismatch = bool(current_fn) and current_fn.lower() not in acceptable_names
    is_product = resource_type == "product"
    bad_product_dimensions = (
        is_product
        and optimize_supported
        and image_width is not None
        and image_height is not None
        and (image_width != 1000 or image_height != 1000)
    )
    fmt = image_format_label_from_url(url)
    if not fmt:
        fmt = image_format_label_from_mime((cached_mime or "").strip()) if (cached_mime or "").strip() else ""
    return {
        "resource_type": resource_type,
        "resource_shopify_id": resource_shopify_id,
        "resource_handle": resource_handle,
        "resource_title": resource_title,
        "blog_handle": blog_handle,
        "article_handle": article_handle,
        "image_row_id": image_row_id,
        "image_shopify_id": image_shopify_id,
        "product_shopify_id": resource_shopify_id if is_product else "",
        "product_handle": resource_handle if is_product else "",
        "product_title": resource_title if is_product else "",
        "url": url,
        "alt_text": alt_text,
        "position": position,
        "roles": roles,
        "role_for_suggestions": role_for,
        "variant_labels": variant_labels,
        "suggested_filename_webp": suggested_fn,
        "optimize_supported": optimize_supported,
        "local_file_cached": local_file_cached,
        "image_width": image_width,
        "image_height": image_height,
        "image_format": fmt,
        "file_size_bytes": file_size_bytes,
        "flags": {
            "missing_or_weak_alt": miss_alt,
            "weak_filename": weak_fn,
            "seo_filename_mismatch": seo_filename_mismatch,
            "not_webp": not is_probably_webp_url(url),
            "bad_dimensions": bad_product_dimensions,
            "is_featured": is_featured,
        },
    }


def _passes_filters(
    row: dict[str, Any],
    *,
    missing_alt: bool | None,
    weak_filename: bool | None,
    status: str | None = None,
) -> bool:
    flags = row["flags"]
    miss_alt = flags["missing_or_weak_alt"]
    weak_fn = flags["weak_filename"]
    not_webp = flags.get("not_webp", False)
    seo_fn_mismatch = bool(flags.get("seo_filename_mismatch"))
    bad_dimensions = bool(flags.get("bad_dimensions"))
    filename_issue = weak_fn or seo_fn_mismatch
    # status filter must match summary counts + UI status column.

    not_seo_optimized = miss_alt or weak_fn or seo_fn_mismatch or not_webp or bad_dimensions
    if status == "optimized" and not_seo_optimized:
        return False
    if status == "not_optimized" and not not_seo_optimized:
        return False

    if missing_alt is True and not miss_alt:
        return False
    if missing_alt is False and miss_alt:
        return False
    if weak_filename is True and not filename_issue:
        return False
    if weak_filename is False and filename_issue:
        return False
    return True


_TYPE_ORDER = {"product": 0, "collection": 1, "page": 2, "article": 3}


def list_catalog_image_seo_rows(
    *,
    limit: int = 50,
    offset: int = 0,
    missing_alt: bool | None = None,
    weak_filename: bool | None = None,
    status: str | None = None,
    search_query: str = "",
    resource_type_filter: str | None = None,
    sort: str = "handle",
    direction: str = "asc",
) -> tuple[list[dict[str, Any]], int]:
    conn = open_db_connection()
    try:
        return _list_catalog_image_seo_rows_impl(
            conn,
            limit=limit,
            offset=offset,
            missing_alt=missing_alt,
            weak_filename=weak_filename,
            status=status,
            search_query=search_query,
            resource_type_filter=resource_type_filter,
            sort=sort,
            direction=direction,
        )
    finally:
        conn.close()


def _list_catalog_image_seo_rows_impl(
    conn: sqlite3.Connection,
    *,
    limit: int = 50,
    offset: int = 0,
    missing_alt: bool | None = None,
    weak_filename: bool | None = None,
    status: str | None = None,
    search_query: str = "",
    resource_type_filter: str | None = None,
    sort: str = "handle",
    direction: str = "asc",
) -> tuple[list[dict[str, Any]], int]:
    cache_root = image_cache_root(Path(DB_PATH))
    gallery_cache_index = product_image_file_cache_index(conn)
    # mime + file size from the cache DB — ground truth for product gallery images.
    gallery_cache_info: dict[str, tuple[str, int | None]] = {}
    for row in conn.execute("SELECT image_shopify_id, mime, content_length FROM product_image_file_cache"):
        sid = (row[0] or "").strip()
        if sid:
            gallery_cache_info[sid] = ((row[1] or "").strip(), row[2])
    featured_by_product = _featured_url_by_product(conn)
    variants_map = _variants_by_product(conn)

    sq = (search_query or "").strip().lower()
    rt_f = (resource_type_filter or "").strip().lower()
    if rt_f in ("", "all"):
        rt_f = ""

    def matches_search(
        title: str,
        handle: str,
        blog_h: str = "",
        article_h: str = "",
    ) -> bool:
        if not sq:
            return True
        parts = [title, handle, blog_h, article_h]
        return any(sq in (p or "").lower() for p in parts)

    items: list[dict[str, Any]] = []

    # --- Products: gallery ---
    rows = conn.execute(
        """
        SELECT pi.shopify_id, pi.product_shopify_id, pi.position, pi.alt_text, pi.url,
               pi.width, pi.height, p.handle, p.title
        FROM product_images pi
        JOIN products p ON p.shopify_id = pi.product_shopify_id
        ORDER BY p.handle COLLATE NOCASE, COALESCE(pi.position, 9999), pi.shopify_id
        """
    ).fetchall()
    for r in rows:
        img_id, prod_id, position, alt_text, url, width, height, handle, title = (
            r[0],
            r[1],
            r[2],
            r[3] or "",
            r[4],
            r[5],
            r[6],
            r[7],
            r[8] or "",
        )
        if not matches_search(title, handle):
            continue
        roles, role_for, variant_labels, _is_feat = _role_and_variants(
            prod_id, url, featured_by_product, variants_map
        )
        pos_i = int(position) if position is not None else None
        row = _catalog_image_row(
            resource_type="product",
            resource_shopify_id=prod_id,
            resource_handle=handle,
            resource_title=title,
            image_row_id=img_id,
            url=url,
            alt_text=alt_text,
            position=pos_i,
            roles=roles,
            role_for=role_for,
            variant_labels=variant_labels,
            optimize_supported=True,
            image_shopify_id=img_id,
            local_file_cached=catalog_gallery_image_cached_locally(
                gallery_cache_index,
                cache_root,
                image_shopify_id=img_id,
                catalog_image_url=url,
            ),
            image_width=_as_int_dim(width),
            image_height=_as_int_dim(height),
            cached_mime=gallery_cache_info.get(img_id, ("", None))[0],
            file_size_bytes=gallery_cache_info.get(img_id, ("", None))[1],
        )
        if rt_f and rt_f != "product":
            continue
        if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
            items.append(row)

    # --- Products: description HTML (Shopify CDN only; skip gallery URLs) ---
    if not rt_f or rt_f == "product":
        for pr in conn.execute(
            """
            SELECT shopify_id, handle, title, description_html
            FROM products
            WHERE description_html IS NOT NULL AND TRIM(description_html) != ''
            """
        ).fetchall():
            prod_id, handle, title, dhtml = pr[0], pr[1], pr[2] or "", pr[3] or ""
            if not matches_search(title, handle):
                continue
            seen = _product_gallery_norm_urls(conn, prod_id, featured_by_product)
            for idx, (url, alt_text) in enumerate(extract_shopify_images_from_html(dhtml), start=1):
                if normalize_shopify_image_url(url) in seen:
                    continue
                seen.add(normalize_shopify_image_url(url))
                row = _catalog_image_row(
                    resource_type="product",
                    resource_shopify_id=prod_id,
                    resource_handle=handle,
                    resource_title=title,
                    image_row_id=f"product-body|{prod_id}|{idx}",
                    url=url,
                    alt_text=alt_text,
                    position=idx,
                    roles=["description"],
                    role_for="gallery",
                    variant_labels=[],
                    optimize_supported=False,
                    image_shopify_id="",
                )
                if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                    items.append(row)

    # --- Collections: featured image (Admin API) + description HTML <img> ---
    if not rt_f or rt_f == "collection":
        for cr in conn.execute(
            """
            SELECT shopify_id, handle, title, description_html, image_json
            FROM collections
            WHERE (image_json IS NOT NULL AND TRIM(image_json) != '')
               OR (description_html IS NOT NULL AND TRIM(description_html) != '')
            """
        ).fetchall():
            cid, handle, title, dhtml, image_json_s = cr[0], cr[1], cr[2] or "", cr[3] or "", cr[4] or ""
            if not matches_search(title, handle):
                continue
            seen: set[str] = set()
            if (image_json_s or "").strip():
                try:
                    im = json.loads(image_json_s)
                    if isinstance(im, dict):
                        u_feat = (im.get("url") or "").strip()
                        alt_feat = (im.get("altText") or im.get("alt") or "").strip()
                        if u_feat and is_shopify_hosted_image_url(u_feat):
                            seen.add(normalize_shopify_image_url(u_feat))
                            cache_id = catalog_url_cache_key_from_norm(normalize_shopify_image_url(u_feat))
                            row = _catalog_image_row(
                                resource_type="collection",
                                resource_shopify_id=cid,
                                resource_handle=handle,
                                resource_title=title,
                                image_row_id=f"collection|{cid}|featured",
                                url=u_feat,
                                alt_text=alt_feat,
                                position=0,
                                roles=["featured"],
                                role_for="featured",
                                variant_labels=[],
                                optimize_supported=True,
                                image_shopify_id=(im.get("id") or "") if isinstance(im.get("id"), str) else "",
                                local_file_cached=catalog_gallery_image_cached_locally(
                                    gallery_cache_index,
                                    cache_root,
                                    image_shopify_id=cache_id,
                                    catalog_image_url=u_feat,
                                ),
                                image_width=_as_int_dim(im.get("width")),
                                image_height=_as_int_dim(im.get("height")),
                                cached_mime=gallery_cache_info.get(cache_id, ("", None))[0],
                                file_size_bytes=gallery_cache_info.get(cache_id, ("", None))[1],
                            )
                            if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                                items.append(row)
                except json.JSONDecodeError:
                    pass
            for idx, (url, alt_text) in enumerate(extract_shopify_images_from_html(dhtml), start=1):
                nu = normalize_shopify_image_url(url)
                if nu in seen:
                    continue
                seen.add(nu)
                row = _catalog_image_row(
                    resource_type="collection",
                    resource_shopify_id=cid,
                    resource_handle=handle,
                    resource_title=title,
                    image_row_id=f"collection|{cid}|body|{idx}",
                    url=url,
                    alt_text=alt_text,
                    position=idx,
                    roles=["description"],
                    role_for="gallery",
                    variant_labels=[],
                    optimize_supported=False,
                    image_shopify_id="",
                )
                if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                    items.append(row)

    # --- Pages: theme template JSON (main theme) + page body HTML ---
    if not rt_f or rt_f == "page":
        for pg in conn.execute(
            """
            SELECT shopify_id, handle, title, body, COALESCE(template_images_json, '') AS template_images_json
            FROM pages
            """
        ).fetchall():
            pid, handle, title, body, tij = pg[0], pg[1], pg[2] or "", pg[3] or "", pg[4] or ""
            dhtml = body or ""
            template_urls: list[str] = []
            if tij.strip():
                try:
                    parsed = json.loads(tij)
                    if isinstance(parsed, list):
                        template_urls = [str(u).strip() for u in parsed if str(u).strip()]
                except json.JSONDecodeError:
                    template_urls = []
            if not dhtml.strip() and not template_urls:
                continue
            if not matches_search(title, handle):
                continue
            seen_pg: set[str] = set()
            for t_idx, url in enumerate(template_urls, start=1):
                if not is_shopify_hosted_image_url(url):
                    continue
                nu = normalize_shopify_image_url(url)
                if nu in seen_pg:
                    continue
                seen_pg.add(nu)
                row = _catalog_image_row(
                    resource_type="page",
                    resource_shopify_id=pid,
                    resource_handle=handle,
                    resource_title=title,
                    image_row_id=f"page|{pid}|template|{t_idx}",
                    url=url,
                    alt_text="",
                    position=t_idx,
                    roles=["template"],
                    role_for="featured" if t_idx == 1 else "gallery",
                    variant_labels=[],
                    optimize_supported=False,
                    image_shopify_id="",
                )
                if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                    items.append(row)
            for idx, (url, alt_text) in enumerate(extract_shopify_images_from_html(dhtml), start=1):
                nu = normalize_shopify_image_url(url)
                if nu in seen_pg:
                    continue
                seen_pg.add(nu)
                row = _catalog_image_row(
                    resource_type="page",
                    resource_shopify_id=pid,
                    resource_handle=handle,
                    resource_title=title,
                    image_row_id=f"page|{pid}|body|{idx}",
                    url=url,
                    alt_text=alt_text,
                    position=idx,
                    roles=["body"],
                    role_for="gallery",
                    variant_labels=[],
                    optimize_supported=False,
                    image_shopify_id="",
                )
                if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                    items.append(row)

    # --- Blog articles: featured + body ---
    if not rt_f or rt_f == "article":
        for ar in conn.execute(
            """
            SELECT shopify_id, blog_shopify_id, blog_handle, title, handle, body, image_json
            FROM blog_articles
            """
        ).fetchall():
            aid, _blog_sid, blog_handle, title, article_handle, body, image_json = (
                ar[0],
                ar[1],
                ar[2] or "",
                ar[3] or "",
                ar[4] or "",
                ar[5] or "",
                ar[6] or "",
            )
            if not matches_search(title, article_handle, blog_handle, article_handle):
                continue
            seen: set[str] = set()
            if (image_json or "").strip():
                try:
                    im = json.loads(image_json)
                    u_feat = (im.get("url") or "").strip()
                    alt_feat = (im.get("altText") or im.get("alt") or "").strip()
                    if u_feat and is_shopify_hosted_image_url(u_feat):
                        seen.add(normalize_shopify_image_url(u_feat))
                        row = _catalog_image_row(
                            resource_type="article",
                            resource_shopify_id=aid,
                            resource_handle=article_handle,
                            resource_title=title,
                            image_row_id=f"article|{aid}|featured",
                            url=u_feat,
                            alt_text=alt_feat,
                            position=0,
                            roles=["featured"],
                            role_for="featured",
                            variant_labels=[],
                            blog_handle=blog_handle,
                            article_handle=article_handle,
                            optimize_supported=False,
                            image_shopify_id="",
                            image_width=_as_int_dim(im.get("width")),
                            image_height=_as_int_dim(im.get("height")),
                        )
                        if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                            items.append(row)
                except json.JSONDecodeError:
                    pass
            for idx, (url, alt_text) in enumerate(extract_shopify_images_from_html(body), start=1):
                nu = normalize_shopify_image_url(url)
                if nu in seen:
                    continue
                seen.add(nu)
                row = _catalog_image_row(
                    resource_type="article",
                    resource_shopify_id=aid,
                    resource_handle=article_handle,
                    resource_title=title,
                    image_row_id=f"article|{aid}|body|{idx}",
                    url=url,
                    alt_text=alt_text,
                    position=idx,
                    roles=["body"],
                    role_for="gallery",
                    variant_labels=[],
                    blog_handle=blog_handle,
                    article_handle=article_handle,
                    optimize_supported=False,
                    image_shopify_id="",
                )
                if _passes_filters(row, missing_alt=missing_alt, weak_filename=weak_filename, status=status):
                    items.append(row)

    rev = direction.lower() == "desc"

    def sort_key(x: dict[str, Any]) -> tuple:
        t = _TYPE_ORDER.get(x["resource_type"], 9)
        h = (x.get("resource_handle") or "").lower()
        tit = (x.get("resource_title") or "").lower()
        pos = x.get("position")
        pos_v = pos if isinstance(pos, int) else 9999
        rid = x.get("image_row_id") or ""
        alt_l = (x.get("alt_text") or "").lower()
        f = x.get("flags") or {}
        miss = bool(f.get("missing_or_weak_alt"))
        weak = bool(f.get("weak_filename"))
        mismatch = bool(f.get("seo_filename_mismatch"))
        not_webp = bool(f.get("not_webp"))
        seo_ok = not miss and not weak and not mismatch and not not_webp
        status_key = 0 if seo_ok else 1
        opt_sup = bool(x.get("optimize_supported"))

        if sort == "title":
            return (t, tit, h, pos_v, rid)
        if sort == "position":
            return (t, h, pos_v, rid)
        if sort == "type":
            return (t, h, pos_v, rid)
        if sort == "alt":
            return (alt_l, t, tit, h, pos_v, rid)
        if sort == "status":
            return (status_key, t, h, tit, pos_v, rid)
        if sort == "optimize":
            # Asc: product-optimizable rows first (0), then unsupported (1)
            return ((0 if opt_sup else 1), t, h, tit, pos_v, rid)
        return (t, h, pos_v, rid)

    items.sort(key=sort_key, reverse=rev)
    total = len(items)

    summary = {
        "total_images": total,
        "optimized": 0,
        "missing_alt": 0,
        "not_webp": 0,
        "weak_filename": 0,
        "locally_cached": 0,
    }
    for it in items:
        f = it.get("flags") or {}
        filename_issue = bool(f.get("weak_filename")) or bool(f.get("seo_filename_mismatch"))
        if (
            not f.get("missing_or_weak_alt")
            and not filename_issue
            and not f.get("not_webp")
            and not f.get("bad_dimensions")
        ):
            summary["optimized"] += 1
        if f.get("missing_or_weak_alt"):
            summary["missing_alt"] += 1
        if f.get("not_webp"):
            summary["not_webp"] += 1
        if filename_issue:
            summary["weak_filename"] += 1
        if it.get("local_file_cached") is True:
            summary["locally_cached"] += 1

    return items[offset : offset + limit], total, summary


def suggest_catalog_image_alt_vision(payload: dict[str, Any]) -> dict[str, Any]:
    """Download a Shopify-hosted image and return vision-based alt text (Settings → Vision)."""
    url = (payload.get("url") or "").strip()
    resource_type = (payload.get("resource_type") or "product").strip().lower()
    resource_title = (payload.get("resource_title") or "").strip()
    resource_handle = (payload.get("resource_handle") or "").strip()
    role_for = (payload.get("role_for_suggestions") or "gallery").strip() or "gallery"
    variant_labels = payload.get("variant_labels")
    if not isinstance(variant_labels, list):
        variant_labels = []
    vclean = [str(x).strip() for x in variant_labels if str(x).strip()]

    if not url:
        return {"ok": False, "message": "url is required"}
    if not is_shopify_hosted_image_url(url):
        return {"ok": False, "message": "Only Shopify CDN image URLs are allowed."}
    if resource_type not in {"product", "collection", "page", "article"}:
        return {"ok": False, "message": "resource_type must be product, collection, page, or article"}

    conn = open_db_connection()
    try:
        settings = ai_settings(conn)
        prov = (settings.get("vision_provider") or "").strip().lower()
        if prov not in {"openai", "gemini", "openrouter"}:
            return {
                "ok": False,
                "message": "Vision alt suggestions require Vision provider set to OpenAI, Gemini, or OpenRouter in Settings → AI models.",
            }
    finally:
        conn.close()

    try:
        raw, mime = download_image_bytes(url)
    except Exception as exc:
        return {"ok": False, "message": f"Could not download image: {exc}"}

    out = vision_suggest_catalog_image_alt(
        settings,
        image_bytes=raw,
        mime=mime,
        resource_type=resource_type,
        resource_title=resource_title,
        resource_handle=resource_handle,
        role_hint=role_for,
        variant_labels=vclean or None,
    )
    if not out:
        return {"ok": False, "message": "Vision model returned no suggestion."}
    return {"ok": True, "alt": out.strip()[:512]}


def list_product_image_seo_rows(
    *,
    limit: int = 50,
    offset: int = 0,
    missing_alt: bool | None = None,
    weak_filename: bool | None = None,
    status: str | None = None,
    product_query: str = "",
    resource_type: str | None = None,
    sort: str = "handle",
    direction: str = "asc",
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    return list_catalog_image_seo_rows(
        limit=limit,
        offset=offset,
        missing_alt=missing_alt,
        weak_filename=weak_filename,
        status=status,
        search_query=product_query,
        resource_type_filter=resource_type,
        sort=sort,
        direction=direction,
    )
