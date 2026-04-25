"""Product image optimization pipeline — draft (local preview) and apply to Shopify."""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from backend.app.db import open_db_connection
from shopifyseo.dashboard_ai_engine_parts.images import (
    normalize_product_image_bytes,
    try_encode_image_bytes_as_webp,
    vision_suggest_catalog_image_alt,
)
from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings
from shopifyseo.dashboard_store import DB_PATH
from shopifyseo.catalog_image_work import catalog_url_cache_key_from_norm
from shopifyseo.product_image_seo import (
    infer_image_format_from_bytes,
    is_probably_webp_url,
    normalize_shopify_image_url,
    product_image_seo_suggested_filename,
    stable_seo_filename_suffix,
)
from shopifyseo.shopify_admin import (
    clear_collection_featured_image,
    update_collection_featured_image,
    upload_image_bytes_and_get_url,
)
from shopifyseo.shopify_catalog_sync import sync_collection, sync_product
from shopifyseo.shopify_image_cache import (
    cache_product_image_bytes,
    invalidate_product_image_cache_entry,
    read_cached_product_image,
)
from shopifyseo.shopify_product_media import (
    download_image_bytes,
    fetch_product_media_for_match,
    fetch_variants_with_image_urls,
    match_media_id_for_catalog_image,
    product_update_media_alt,
    replace_product_image_with_upload,
)

from ._catalog import (
    _featured_url_by_product,
    _fmt_bytes,
    _product_gallery_seo_suffix_seed,
    _role_and_variants,
    _variants_by_product,
)

logger = logging.getLogger(__name__)

# Cap base64 payload for draft preview (browser / JSON comfort).
_DRAFT_PREVIEW_MAX_BYTES = 5 * 1024 * 1024
# Shopify's documented alt text character limit.
_MAX_ALT_TEXT_LENGTH = 512


def _ext_for_mime(mime: str, *, force_webp: bool) -> str:
    if force_webp:
        return ".webp"
    m = (mime or "").lower()
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "png" in m:
        return ".png"
    if "webp" in m:
        return ".webp"
    if "gif" in m:
        return ".gif"
    return ".jpg"


def _mime_for_ext(ext: str) -> str:
    e = (ext or "").lower()
    if e == ".webp":
        return "image/webp"
    if e in (".jpg", ".jpeg"):
        return "image/jpeg"
    if e == ".png":
        return "image/png"
    if e == ".gif":
        return "image/gif"
    return "image/jpeg"


def _bytes_passthrough_ext_mime(raw: bytes, header_mime: str) -> tuple[str, str]:
    """Extension + mime for upload when we are not re-encoding (trust magic over Content-Type)."""
    inferred = infer_image_format_from_bytes(raw)
    if inferred:
        return inferred
    ext = _ext_for_mime(header_mime, force_webp=False)
    return ext, _mime_for_ext(ext)


def _catalog_url_cache_key(url: str) -> str:
    return catalog_url_cache_key_from_norm(normalize_shopify_image_url(url))


def _read_cached_catalog_url_image(
    conn: sqlite3.Connection,
    url: str,
) -> tuple[bytes, str] | None:
    cache_id = _catalog_url_cache_key(url)
    return read_cached_product_image(Path(DB_PATH), conn, cache_id, url)


def _product_image_replace_output(
    raw: bytes,
    url: str,
    header_mime: str,
    *,
    convert_webp_flag: bool,
) -> tuple[bytes, str, str, str | None]:
    """Normalize to 1000x1000 (pad/white bg), then encode as WebP when applicable.

    Returns ``(out_bytes, ext, mime, error)`` — ``error`` set on failure.
    """
    normalized, norm_err = normalize_product_image_bytes(raw)
    if normalized is None:
        logger.warning("Image normalization failed, proceeding with original: %s", norm_err)
        normalized = raw

    want_webp = convert_webp_flag or is_probably_webp_url(url)

    if want_webp:
        webp_bytes, webp_err = try_encode_image_bytes_as_webp(normalized)
        if webp_bytes is None:
            return normalized, ".webp", "image/webp", (webp_err or "WebP conversion failed").strip()
        return webp_bytes, ".webp", "image/webp", None

    inferred = infer_image_format_from_bytes(normalized)
    if inferred:
        return normalized, inferred[0], inferred[1], None
    ext, mime = _bytes_passthrough_ext_mime(normalized, header_mime)
    return normalized, ext, mime, None


def _image_upload_output(
    raw: bytes,
    url: str,
    header_mime: str,
    *,
    apply_fn: bool,
    convert_webp: bool,
) -> tuple[bytes, str, str, str | None, bool]:
    """Return upload bytes while preserving pixels for filename-only WebP re-uploads.

    The final bool is True when original bytes were reused unchanged.
    """
    inferred = infer_image_format_from_bytes(raw)
    source_is_webp = inferred is not None and inferred[0] == ".webp"
    source_dims = _image_bytes_dimensions(raw)
    source_is_product_square = source_dims == (1000, 1000)
    filename_only_webp = (
        apply_fn
        and not convert_webp
        and source_is_webp
        and source_is_product_square
    )
    if filename_only_webp:
        return raw, ".webp", "image/webp", None, True
    effective_convert_webp = convert_webp or (apply_fn and not source_is_webp)
    out_bytes, out_ext, out_mime, err = _product_image_replace_output(
        raw,
        url,
        header_mime,
        convert_webp_flag=effective_convert_webp,
    )
    return out_bytes, out_ext, out_mime, err, False


def _image_bytes_dimensions(raw: bytes) -> tuple[int, int] | None:
    if not raw:
        return None
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(raw)) as im:
            return int(im.width), int(im.height)
    except Exception:
        return None


def _collection_featured_seo_suffix_seed(collection_shopify_id: str) -> str:
    return f"{collection_shopify_id}|featured"


def _collection_featured_row(
    conn: sqlite3.Connection,
    collection_shopify_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT shopify_id, handle, title, image_json
        FROM collections
        WHERE shopify_id = ?
        """,
        (collection_shopify_id,),
    ).fetchone()
    if not row:
        return None
    raw = (row["image_json"] or "").strip()
    if not raw:
        return None
    try:
        image = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(image, dict):
        return None
    url = (image.get("url") or "").strip()
    if not url:
        return None
    return {
        "collection_shopify_id": row["shopify_id"],
        "handle": (row["handle"] or "").strip(),
        "title": (row["title"] or "").strip(),
        "url": url,
        "alt_text": (image.get("altText") or image.get("alt") or "").strip(),
        "image_shopify_id": (image.get("id") or "").strip(),
    }


def draft_optimize_collection_image(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a local draft for a collection featured image (no Shopify writes)."""
    collection_shopify_id = (payload.get("collection_shopify_id") or "").strip()
    apply_fn = bool(payload.get("apply_suggested_filename"))
    convert_webp = bool(payload.get("convert_webp"))
    auto_vision = bool(payload.get("auto_vision_alt", True))

    steps: list[dict[str, Any]] = []
    if not collection_shopify_id:
        return {"ok": False, "message": "collection_shopify_id is required", "steps": steps}

    conn = open_db_connection()
    try:
        return _draft_optimize_collection_image_impl(
            conn,
            collection_shopify_id,
            apply_fn=apply_fn,
            convert_webp=convert_webp,
            auto_vision=auto_vision,
            steps=steps,
        )
    finally:
        conn.close()


def _draft_optimize_collection_image_impl(
    conn: sqlite3.Connection,
    collection_shopify_id: str,
    *,
    apply_fn: bool,
    convert_webp: bool,
    auto_vision: bool,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    row = _collection_featured_row(conn, collection_shopify_id)
    if not row:
        return {"ok": False, "message": "Collection featured image not found in catalog (run Shopify sync).", "steps": steps}

    url = row["url"]
    handle = row["handle"] or "collection"
    title = row["title"] or handle

    cached = _read_cached_catalog_url_image(conn, url)
    if cached:
        raw, mime = cached
        orig_size = len(raw)
        steps.append(
            {
                "id": "download",
                "label": "Download original",
                "status": "ok",
                "detail": f"{_fmt_bytes(orig_size)} (local cache)",
            }
        )
    else:
        try:
            raw, mime = download_image_bytes(url)
        except Exception as exc:
            steps.append(
                {
                    "id": "download",
                    "label": "Download original",
                    "status": "error",
                    "detail": str(exc) or "Download failed",
                }
            )
            return {"ok": False, "message": f"Could not download image: {exc}", "steps": steps}
        orig_size = len(raw)
        steps.append(
            {
                "id": "download",
                "label": "Download original",
                "status": "ok",
                "detail": _fmt_bytes(orig_size),
            }
        )

    draft_alt = (row.get("alt_text") or "").strip()
    if auto_vision:
        settings = ai_settings(conn)
        prov = (settings.get("vision_provider") or "").strip().lower()
        if prov in {"openai", "gemini", "openrouter"}:
            vision_out = vision_suggest_catalog_image_alt(
                settings,
                image_bytes=raw,
                mime=mime,
                resource_type="collection",
                resource_title=title,
                resource_handle=handle,
                role_hint="featured",
                variant_labels=None,
            )
            if vision_out:
                draft_alt = vision_out.strip()[:_MAX_ALT_TEXT_LENGTH]
                preview_alt = draft_alt if len(draft_alt) <= 100 else draft_alt[:97] + "..."
                steps.append(
                    {
                        "id": "alt",
                        "label": "AI alt text (vision)",
                        "status": "ok",
                        "detail": preview_alt,
                    }
                )
            else:
                steps.append(
                    {
                        "id": "alt",
                        "label": "AI alt text (vision)",
                        "status": "warning",
                        "detail": "No suggestion — kept existing catalog alt.",
                    }
                )
        else:
            steps.append(
                {
                    "id": "alt",
                    "label": "AI alt text (vision)",
                    "status": "skipped",
                    "detail": "Set Vision to OpenAI, Gemini, or OpenRouter in Settings to auto-generate alt.",
                }
            )
    else:
        steps.append(
            {
                "id": "alt",
                "label": "Alt text",
                "status": "skipped",
                "detail": "Using catalog alt (auto vision off).",
            }
        )

    suffix = stable_seo_filename_suffix(_collection_featured_seo_suffix_seed(collection_shopify_id))
    draft_filename = product_image_seo_suggested_filename(
        product_handle=handle,
        role="featured",
        gallery_position=1,
        ext=".webp",
        collision_suffix=suffix,
    )
    steps.append(
        {
            "id": "filename",
            "label": "SEO filename",
            "status": "ok",
            "detail": draft_filename,
        }
    )

    out_bytes, _out_ext, out_mime, webp_err, preserved_original = _image_upload_output(
        raw,
        url,
        mime,
        apply_fn=apply_fn,
        convert_webp=convert_webp,
    )
    if webp_err:
        steps.append(
            {
                "id": "webp",
                "label": "Convert to WebP",
                "status": "error",
                "detail": webp_err,
            }
        )
        return {
            "ok": False,
            "message": f"WebP conversion failed for this image: {webp_err}",
            "steps": steps,
        }
    if preserved_original:
        steps.append(
            {
                "id": "encode",
                "label": "Encoding",
                "status": "ok",
                "detail": "Original WebP bytes kept; only the filename will change.",
            }
        )
    else:
        steps.append(
            {
                "id": "webp",
                "label": "Convert to WebP",
                "status": "ok",
                "detail": f"{_fmt_bytes(orig_size)} -> {_fmt_bytes(len(out_bytes))}",
            }
        )

    preview_b64: str | None = None
    preview_omitted = False
    if len(out_bytes) <= _DRAFT_PREVIEW_MAX_BYTES:
        preview_b64 = base64.b64encode(out_bytes).decode("ascii")
    else:
        preview_omitted = True

    return {
        "ok": True,
        "message": "Draft ready — review and save to Shopify.",
        "steps": steps,
        "original_size_bytes": orig_size,
        "draft_size_bytes": len(out_bytes),
        "draft_alt": draft_alt[:_MAX_ALT_TEXT_LENGTH],
        "draft_filename": draft_filename,
        "draft_mime": out_mime,
        "preview_base64": preview_b64,
        "preview_omitted": preview_omitted,
    }


def draft_optimize_product_image(payload: dict[str, Any]) -> dict[str, Any]:
    """Build a local draft: download, optional vision alt, filename + WebP preview (no Shopify writes)."""
    product_shopify_id = (payload.get("product_shopify_id") or "").strip()
    image_shopify_id = (payload.get("image_shopify_id") or "").strip()
    apply_fn = bool(payload.get("apply_suggested_filename"))
    convert_webp = bool(payload.get("convert_webp"))
    auto_vision = bool(payload.get("auto_vision_alt", True))

    steps: list[dict[str, Any]] = []

    if not product_shopify_id or not image_shopify_id:
        return {"ok": False, "message": "product_shopify_id and image_shopify_id are required", "steps": steps}

    conn = open_db_connection()
    try:
        return _draft_optimize_product_image_impl(
            conn, product_shopify_id, image_shopify_id,
            apply_fn=apply_fn, convert_webp=convert_webp, auto_vision=auto_vision,
            steps=steps,
        )
    finally:
        conn.close()


def _draft_optimize_product_image_impl(
    conn: sqlite3.Connection,
    product_shopify_id: str,
    image_shopify_id: str,
    *,
    apply_fn: bool,
    convert_webp: bool,
    auto_vision: bool,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT pi.shopify_id, pi.product_shopify_id, pi.position, pi.alt_text, pi.url, p.handle, p.title
        FROM product_images pi
        JOIN products p ON p.shopify_id = pi.product_shopify_id
        WHERE pi.shopify_id = ? AND pi.product_shopify_id = ?
        """,
        (image_shopify_id, product_shopify_id),
    ).fetchone()
    if not row:
        return {"ok": False, "message": "Image row not found in catalog (run Shopify sync).", "steps": steps}

    _img_id, prod_id, position, alt_text, url, handle, title = row
    featured_by_product = _featured_url_by_product(conn)
    variants_map = _variants_by_product(conn)
    _roles, role_for, variant_labels, _is_feat = _role_and_variants(
        prod_id, url, featured_by_product, variants_map
    )

    pos_i = int(position) if position is not None else None
    vjoin = ", ".join(variant_labels[:3]) if variant_labels else None

    media_rows = fetch_product_media_for_match(product_shopify_id)
    media_id = match_media_id_for_catalog_image(
        media_rows, catalog_media_gid=image_shopify_id, catalog_image_url=url
    )
    if not media_id:
        steps.append(
            {
                "id": "match",
                "label": "Match Shopify media",
                "status": "error",
                "detail": "Media not found on product — re-sync products and retry.",
            }
        )
        return {
            "ok": False,
            "message": "Could not match this image to Shopify product media (removed or changed in Shopify — re-sync and retry).",
            "steps": steps,
        }
    steps.append(
        {
            "id": "match",
            "label": "Match Shopify media",
            "status": "ok",
            "detail": "Ready to save when you confirm.",
        }
    )

    cached = read_cached_product_image(Path(DB_PATH), conn, image_shopify_id, url)
    if cached:
        raw, mime = cached
        orig_size = len(raw)
        steps.append(
            {
                "id": "download",
                "label": "Download original",
                "status": "ok",
                "detail": f"{_fmt_bytes(orig_size)} (local cache)",
            }
        )
    else:
        try:
            raw, mime = download_image_bytes(url)
        except Exception as exc:
            steps.append(
                {
                    "id": "download",
                    "label": "Download original",
                    "status": "error",
                    "detail": str(exc) or "Download failed",
                }
            )
            return {"ok": False, "message": f"Could not download image: {exc}", "steps": steps}

        orig_size = len(raw)
        steps.append(
            {
                "id": "download",
                "label": "Download original",
                "status": "ok",
                "detail": _fmt_bytes(orig_size),
            }
        )

    draft_alt = (alt_text or "").strip()
    if auto_vision:
        settings = ai_settings(conn)
        prov = (settings.get("vision_provider") or "").strip().lower()
        if prov in {"openai", "gemini", "openrouter"}:
            vision_out = vision_suggest_catalog_image_alt(
                settings,
                image_bytes=raw,
                mime=mime,
                resource_type="product",
                resource_title=(title or "").strip() or (handle or "Product"),
                resource_handle=(handle or "").strip(),
                role_hint=role_for,
                variant_labels=variant_labels or None,
            )
            if vision_out:
                draft_alt = vision_out.strip()[:_MAX_ALT_TEXT_LENGTH]
                preview_alt = draft_alt if len(draft_alt) <= 100 else draft_alt[:97] + "…"
                steps.append(
                    {
                        "id": "alt",
                        "label": "AI alt text (vision)",
                        "status": "ok",
                        "detail": preview_alt,
                    }
                )
            else:
                steps.append(
                    {
                        "id": "alt",
                        "label": "AI alt text (vision)",
                        "status": "warning",
                        "detail": "No suggestion — kept existing catalog alt.",
                    }
                )
        else:
            steps.append(
                {
                    "id": "alt",
                    "label": "AI alt text (vision)",
                    "status": "skipped",
                    "detail": "Set Vision to OpenAI, Gemini, or OpenRouter in Settings to auto-generate alt.",
                }
            )
    else:
        steps.append(
            {
                "id": "alt",
                "label": "Alt text",
                "status": "skipped",
                "detail": "Using catalog alt (auto vision off).",
            }
        )

    want_webp = convert_webp or is_probably_webp_url(url)
    inferred_pre = infer_image_format_from_bytes(raw)
    is_webp_bytes = inferred_pre is not None and inferred_pre[0] == ".webp"

    if want_webp:
        planned_ext = ".webp"
    else:
        planned_ext, _ = _bytes_passthrough_ext_mime(raw, mime)

    draft_filename = product_image_seo_suggested_filename(
        product_handle=handle or "product",
        role=role_for,
        gallery_position=pos_i,
        variant_label=vjoin,
        ext=planned_ext,
        collision_suffix=stable_seo_filename_suffix(
            _product_gallery_seo_suffix_seed(product_shopify_id, role_for, pos_i, vjoin)
        ),
    )
    would_upload = apply_fn or convert_webp
    steps.append(
        {
            "id": "filename",
            "label": "SEO filename",
            "status": "ok",
            "detail": draft_filename if would_upload else f"{draft_filename} (enable re-upload to apply)",
        }
    )

    out_bytes, _out_ext, out_mime, webp_err, preserved_original = _image_upload_output(
        raw,
        url,
        mime,
        apply_fn=apply_fn,
        convert_webp=convert_webp,
    )
    if webp_err:
        steps.append(
            {
                "id": "webp",
                "label": "Convert to WebP",
                "status": "error",
                "detail": webp_err,
            }
        )
        return {
            "ok": False,
            "message": f"WebP conversion failed for this image: {webp_err}",
            "steps": steps,
        }

    if preserved_original:
        steps.append(
            {
                "id": "encode",
                "label": "Encoding",
                "status": "ok",
                "detail": "Original WebP bytes kept; only the filename will change.",
            }
        )
    elif want_webp:
        if is_webp_bytes:
            steps.append(
                {
                    "id": "encode",
                    "label": "Encoding",
                    "status": "ok",
                    "detail": "WebP — source bytes already WebP (matches storefront URL).",
                }
            )
        else:
            label = "Convert to WebP" if convert_webp else "Normalize to WebP"
            detail = f"{_fmt_bytes(orig_size)} → {_fmt_bytes(len(out_bytes))}"
            if not convert_webp:
                src = inferred_pre[0] if inferred_pre else "unknown"
                detail += f" (storefront URL is WebP; CDN bytes were {src})."
            steps.append(
                {
                    "id": "webp",
                    "label": label,
                    "status": "ok",
                    "detail": detail,
                }
            )
    else:
        steps.append(
            {
                "id": "encode",
                "label": "Encoding",
                "status": "ok",
                "detail": "Original format kept (detected from file bytes, not CDN Content-Type).",
            }
        )

    draft_size = len(out_bytes)
    preview_b64: str | None = None
    preview_omitted = False
    if len(out_bytes) <= _DRAFT_PREVIEW_MAX_BYTES:
        preview_b64 = base64.b64encode(out_bytes).decode("ascii")
    else:
        preview_omitted = True

    return {
        "ok": True,
        "message": "Draft ready — review and save to Shopify.",
        "steps": steps,
        "original_size_bytes": orig_size,
        "draft_size_bytes": draft_size,
        "draft_alt": draft_alt[:_MAX_ALT_TEXT_LENGTH],
        "draft_filename": draft_filename,
        "draft_mime": out_mime,
        "preview_base64": preview_b64,
        "preview_omitted": preview_omitted,
    }


def optimize_product_image(payload: dict[str, Any]) -> dict[str, Any]:
    product_shopify_id = (payload.get("product_shopify_id") or "").strip()
    image_shopify_id = (payload.get("image_shopify_id") or "").strip()
    apply_alt = bool(payload.get("apply_suggested_alt"))
    apply_fn = bool(payload.get("apply_suggested_filename"))
    convert_webp = bool(payload.get("convert_webp"))
    raw_alt_override = payload.get("alt_override")
    dry_run = bool(payload.get("dry_run"))

    if not product_shopify_id or not image_shopify_id:
        return {"ok": False, "message": "product_shopify_id and image_shopify_id are required", "dry_run": dry_run}

    conn = open_db_connection()
    try:
        return _optimize_product_image_impl(
            conn, product_shopify_id, image_shopify_id,
            apply_alt=apply_alt, apply_fn=apply_fn, convert_webp=convert_webp,
            raw_alt_override=raw_alt_override, dry_run=dry_run,
        )
    finally:
        conn.close()


def optimize_collection_image(payload: dict[str, Any]) -> dict[str, Any]:
    collection_shopify_id = (payload.get("collection_shopify_id") or "").strip()
    apply_alt = bool(payload.get("apply_suggested_alt"))
    apply_fn = bool(payload.get("apply_suggested_filename"))
    convert_webp = bool(payload.get("convert_webp"))
    raw_alt_override = payload.get("alt_override")
    dry_run = bool(payload.get("dry_run"))

    if not collection_shopify_id:
        return {"ok": False, "message": "collection_shopify_id is required", "dry_run": dry_run}

    conn = open_db_connection()
    try:
        return _optimize_collection_image_impl(
            conn,
            collection_shopify_id,
            apply_alt=apply_alt,
            apply_fn=apply_fn,
            convert_webp=convert_webp,
            raw_alt_override=raw_alt_override,
            dry_run=dry_run,
        )
    finally:
        conn.close()


def _optimize_collection_image_impl(
    conn: sqlite3.Connection,
    collection_shopify_id: str,
    *,
    apply_alt: bool,
    apply_fn: bool,
    convert_webp: bool,
    raw_alt_override: Any,
    dry_run: bool,
) -> dict[str, Any]:
    row = _collection_featured_row(conn, collection_shopify_id)
    if not row:
        return {
            "ok": False,
            "message": "Collection featured image not found in catalog (run Shopify sync).",
            "dry_run": dry_run,
        }

    url = row["url"]
    handle = row["handle"] or "collection"
    current_alt = (row.get("alt_text") or "")[:_MAX_ALT_TEXT_LENGTH]
    final_alt = (
        str(raw_alt_override).strip()[:_MAX_ALT_TEXT_LENGTH]
        if apply_alt and raw_alt_override is not None
        else current_alt
    )

    suffix = stable_seo_filename_suffix(_collection_featured_seo_suffix_seed(collection_shopify_id))
    fname = product_image_seo_suggested_filename(
        product_handle=handle,
        role="featured",
        gallery_position=1,
        ext=".webp",
        collision_suffix=suffix,
    )

    do_replace = apply_fn or convert_webp
    if not apply_alt and not do_replace:
        return {"ok": False, "message": "Select at least one action.", "dry_run": dry_run}

    if dry_run:
        return {
            "ok": True,
            "message": "Dry run — no changes sent to Shopify.",
            "dry_run": True,
            "applied_alt": final_alt if (apply_alt or do_replace) else None,
            "applied_filename": fname if do_replace else None,
            "details": {"would_upload": do_replace},
        }

    if not do_replace:
        update_collection_featured_image(collection_shopify_id, url, final_alt)
        sync_collection(Path(DB_PATH), collection_shopify_id)
        return {
            "ok": True,
            "message": "Collection image alt text updated in Shopify.",
            "applied_alt": final_alt,
            "dry_run": False,
        }

    cached = _read_cached_catalog_url_image(conn, url)
    if cached:
        raw, mime = cached
    else:
        raw, mime = download_image_bytes(url)

    out_bytes, out_ext, _, webp_err, _preserved_original = _image_upload_output(
        raw,
        url,
        mime,
        apply_fn=apply_fn,
        convert_webp=convert_webp,
    )
    if webp_err:
        return {
            "ok": False,
            "message": f"WebP conversion failed for this image: {webp_err}",
            "dry_run": False,
        }

    if out_ext != ".webp":
        out_ext = ".webp"
    out_mime = _mime_for_ext(out_ext)
    if not (final_alt or "").strip():
        final_alt = current_alt

    new_url = upload_image_bytes_and_get_url(
        out_bytes,
        fname,
        out_mime,
        alt=final_alt,
    )
    clear_collection_featured_image(collection_shopify_id)
    try:
        details = update_collection_featured_image(collection_shopify_id, new_url, final_alt)
    except Exception:
        try:
            update_collection_featured_image(collection_shopify_id, url, current_alt)
        except Exception:
            logger.exception(
                "Failed to restore collection image after replacement error for %s",
                collection_shopify_id,
            )
        raise
    sync_collection(Path(DB_PATH), collection_shopify_id)

    if new_url and out_bytes:
        try:
            cache_product_image_bytes(
                Path(DB_PATH),
                conn,
                _catalog_url_cache_key(new_url),
                new_url,
                out_bytes,
                out_mime,
            )
        except Exception:
            logger.exception("Auto-cache after collection optimize failed for %s", collection_shopify_id)

    return {
        "ok": True,
        "message": "Collection image replaced in Shopify.",
        "applied_alt": final_alt,
        "applied_filename": fname,
        "new_image_url": new_url,
        "new_media_id": None,
        "details": details,
        "dry_run": False,
    }


def _optimize_product_image_impl(
    conn: sqlite3.Connection,
    product_shopify_id: str,
    image_shopify_id: str,
    *,
    apply_alt: bool,
    apply_fn: bool,
    convert_webp: bool,
    raw_alt_override: Any,
    dry_run: bool,
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT pi.shopify_id, pi.product_shopify_id, pi.position, pi.alt_text, pi.url, p.handle, p.title
        FROM product_images pi
        JOIN products p ON p.shopify_id = pi.product_shopify_id
        WHERE pi.shopify_id = ? AND pi.product_shopify_id = ?
        """,
        (image_shopify_id, product_shopify_id),
    ).fetchone()
    if not row:
        return {"ok": False, "message": "Image row not found in catalog (run Shopify sync).", "dry_run": dry_run}

    _img_id, prod_id, position, alt_text, url, handle, title = row
    featured_by_product = _featured_url_by_product(conn)
    variants_map = _variants_by_product(conn)
    _roles, role_for, variant_labels, _is_feat = _role_and_variants(
        prod_id, url, featured_by_product, variants_map
    )

    pos_i = int(position) if position is not None else None
    vjoin = ", ".join(variant_labels[:3]) if variant_labels else None

    if apply_alt:
        if raw_alt_override is not None:
            final_alt = str(raw_alt_override).strip()[:_MAX_ALT_TEXT_LENGTH]
        else:
            final_alt = (alt_text or "")[:_MAX_ALT_TEXT_LENGTH]
    else:
        final_alt = (alt_text or "")[:_MAX_ALT_TEXT_LENGTH]

    seo_suffix = stable_seo_filename_suffix(
        _product_gallery_seo_suffix_seed(product_shopify_id, role_for, pos_i, vjoin)
    )
    suggested_fn = product_image_seo_suggested_filename(
        product_handle=handle or "product",
        role=role_for,
        gallery_position=pos_i,
        variant_label=vjoin,
        ext=".webp",
        collision_suffix=seo_suffix,
    )

    do_replace = apply_fn or convert_webp
    if not apply_alt and not do_replace:
        return {"ok": False, "message": "Select at least one action.", "dry_run": dry_run}

    media_rows = fetch_product_media_for_match(product_shopify_id)
    media_id = match_media_id_for_catalog_image(
        media_rows, catalog_media_gid=image_shopify_id, catalog_image_url=url
    )
    if not media_id:
        return {
            "ok": False,
            "message": "Could not match this image to Shopify product media (removed or changed in Shopify — re-sync and retry).",
            "dry_run": dry_run,
        }

    if dry_run:
        return {
            "ok": True,
            "message": "Dry run — no changes sent to Shopify.",
            "dry_run": True,
            "applied_alt": final_alt if (apply_alt or do_replace) else None,
            "applied_filename": suggested_fn if do_replace else None,
            "details": {"media_id": media_id, "would_upload": do_replace},
        }

    if not do_replace:
        alt_to_set = final_alt
        product_update_media_alt(product_shopify_id, media_id, alt_to_set)
        sync_product(Path(DB_PATH), product_shopify_id)
        return {
            "ok": True,
            "message": "Alt text updated in Shopify.",
            "applied_alt": alt_to_set,
            "dry_run": False,
        }

    cached = read_cached_product_image(Path(DB_PATH), conn, image_shopify_id, url)
    if cached:
        raw, mime = cached
    else:
        raw, mime = download_image_bytes(url)

    out_bytes, out_ext, _, webp_err, _preserved_original = _image_upload_output(
        raw,
        url,
        mime,
        apply_fn=apply_fn,
        convert_webp=convert_webp,
    )
    if webp_err:
        return {
            "ok": False,
            "message": f"WebP conversion failed for this image: {webp_err}",
            "dry_run": False,
        }

    fname = product_image_seo_suggested_filename(
        product_handle=handle or "product",
        role=role_for,
        gallery_position=pos_i,
        variant_label=vjoin,
        ext=out_ext,
        collision_suffix=seo_suffix,
    )

    alt_for_upload = final_alt
    if not (alt_for_upload or "").strip():
        alt_for_upload = (alt_text or "")[:_MAX_ALT_TEXT_LENGTH]

    variant_rows = fetch_variants_with_image_urls(product_shopify_id)
    target_norm = normalize_shopify_image_url(url)
    variant_gids = [vr["id"] for vr in variant_rows if normalize_shopify_image_url(vr.get("url", "")) == target_norm]

    out_mime = _mime_for_ext(out_ext)
    details = replace_product_image_with_upload(
        product_shopify_id,
        old_media_id=media_id,
        old_image_url=url,
        image_bytes=out_bytes,
        filename=fname,
        mime_type=out_mime,
        alt=alt_for_upload,
        variant_gids_to_repoint=variant_gids,
    )
    sync_product(Path(DB_PATH), product_shopify_id)
    invalidate_product_image_cache_entry(Path(DB_PATH), conn, image_shopify_id)
    conn.commit()

    new_url = (details.get("new_image_url") or "").strip()
    new_media_id = (details.get("new_media_id") or "").strip()
    if new_media_id and out_bytes:
        try:
            cache_product_image_bytes(
                Path(DB_PATH), conn, new_media_id,
                new_url or url, out_bytes, out_mime,
            )
        except Exception:
            logger.exception("Auto-cache after optimize failed for %s", new_media_id)

    return {
        "ok": True,
        "message": "Image replaced in Shopify (new media + variants repointed).",
        "applied_alt": alt_for_upload,
        "applied_filename": fname,
        "new_image_url": details.get("new_image_url"),
        "new_media_id": details.get("new_media_id"),
        "details": details,
        "dry_run": False,
    }
