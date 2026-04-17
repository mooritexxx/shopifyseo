"""Shopify Admin GraphQL helpers for product media (alt updates, replace via create/delete)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from shopifyseo.product_image_seo import normalize_shopify_image_url
from shopifyseo.shopify_admin import _wait_media_image_cdn_url, graphql_post, stage_image_bytes_post_resource_url

logger = logging.getLogger(__name__)


def _graphql_data(query: str, variables: dict | None = None) -> dict[str, Any]:
    raw = graphql_post(query, variables)
    if raw.get("errors"):
        raise RuntimeError(f"Shopify GraphQL errors: {raw.get('errors')}")
    data = raw.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Shopify GraphQL returned no data")
    return data


def fetch_product_media_for_match(product_gid: str) -> list[dict[str, Any]]:
    """Return MediaImage rows with id, alt, image.url (ordered by POSITION)."""
    q = """
    query ProductMediaForImageSeo($id: ID!) {
      product(id: $id) {
        id
        media(first: 50, sortKey: POSITION) {
          edges {
            node {
              ... on MediaImage {
                id
                alt
                image {
                  url
                }
              }
            }
          }
        }
      }
    }
    """
    data = _graphql_data(q, {"id": product_gid})
    product = data.get("product") or {}
    out: list[dict[str, Any]] = []
    for edge in (product.get("media") or {}).get("edges") or []:
        node = edge.get("node") or {}
        mid = (node.get("id") or "").strip()
        img = node.get("image") or {}
        url = (img.get("url") or "").strip()
        if mid and url:
            out.append({"id": mid, "alt": (node.get("alt") or "").strip(), "url": url})
    return out


def fetch_variants_with_image_urls(product_gid: str) -> list[dict[str, Any]]:
    """Variant id + image url for matching."""
    q = """
    query ProductVariantsImages($id: ID!) {
      product(id: $id) {
        variants(first: 100) {
          edges {
            node {
              id
              title
              image {
                id
                url
              }
            }
          }
        }
      }
    }
    """
    data = _graphql_data(q, {"id": product_gid})
    product = data.get("product") or {}
    out: list[dict[str, Any]] = []
    for edge in (product.get("variants") or {}).get("edges") or []:
        node = edge.get("node") or {}
        vid = (node.get("id") or "").strip()
        im = node.get("image") or {}
        url = (im.get("url") or "").strip()
        if vid and url:
            out.append(
                {
                    "id": vid,
                    "title": (node.get("title") or "").strip(),
                    "image_id": (im.get("id") or "").strip(),
                    "url": url,
                }
            )
    return out


def match_media_id_by_url(media_rows: list[dict[str, Any]], target_url: str) -> str | None:
    target = normalize_shopify_image_url(target_url)
    if not target:
        return None
    best: str | None = None
    for row in media_rows:
        if normalize_shopify_image_url(row.get("url", "")) == target:
            best = row["id"]
            break
    return best


def match_media_id_for_catalog_image(
    media_rows: list[dict[str, Any]],
    *,
    catalog_media_gid: str,
    catalog_image_url: str,
) -> str | None:
    """Resolve MediaImage id for a catalog row.

    Prefer the stable Shopify media GID from sync; fall back to normalized URL match when the CDN
    URL changed between sync and the live Admin API response.
    """
    gid = (catalog_media_gid or "").strip()
    if gid:
        for row in media_rows:
            if (row.get("id") or "").strip() == gid:
                return row["id"]
    return match_media_id_by_url(media_rows, catalog_image_url)


def product_update_media_alt(product_gid: str, media_gid: str, alt: str) -> None:
    q = """
    mutation ProductUpdateMediaAlt($productId: ID!, $media: [UpdateMediaInput!]!) {
      productUpdateMedia(productId: $productId, media: $media) {
        media {
          ... on MediaImage {
            id
            alt
          }
        }
        mediaUserErrors {
          field
          message
        }
      }
    }
    """
    data = _graphql_data(
        q,
        {
            "productId": product_gid,
            "media": [{"id": media_gid, "alt": alt[:512]}],
        },
    )
    block = data.get("productUpdateMedia") or {}
    errs = block.get("mediaUserErrors") or []
    if errs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in errs))


def product_create_media_from_url(
    product_gid: str,
    *,
    image_url: str,
    alt: str,
) -> str:
    """Attach new media from HTTPS URL. Returns new MediaImage id."""
    q = """
    mutation ProductCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
      productCreateMedia(productId: $productId, media: $media) {
        media {
          ... on MediaImage {
            id
            image {
              url
            }
          }
        }
        mediaUserErrors {
          field
          message
        }
      }
    }
    """
    data = _graphql_data(
        q,
        {
            "productId": product_gid,
            "media": [
                {
                    "originalSource": image_url,
                    "alt": alt[:512],
                    "mediaContentType": "IMAGE",
                }
            ],
        },
    )
    block = data.get("productCreateMedia") or {}
    errs = block.get("mediaUserErrors") or []
    if errs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in errs))
    media = block.get("media") or []
    if not media:
        raise RuntimeError("productCreateMedia returned no media")
    mid = (media[0].get("id") or "").strip()
    if not mid:
        raise RuntimeError("productCreateMedia returned media without id")
    return mid


def product_delete_media(product_gid: str, media_ids: list[str]) -> None:
    q = """
    mutation ProductDeleteMedia($productId: ID!, $mediaIds: [ID!]!) {
      productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
        deletedMediaIds
        mediaUserErrors {
          field
          message
        }
      }
    }
    """
    data = _graphql_data(q, {"productId": product_gid, "mediaIds": media_ids})
    block = data.get("productDeleteMedia") or {}
    errs = block.get("mediaUserErrors") or []
    if errs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in errs))


def product_variant_set_media(variant_gid: str, media_gid: str) -> None:
    q = """
    mutation ProductVariantUpdateOne($input: ProductVariantInput!) {
      productVariantUpdate(input: $input) {
        productVariant {
          id
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    data = _graphql_data(
        q,
        {"input": {"id": variant_gid, "mediaId": media_gid}},
    )
    block = data.get("productVariantUpdate") or {}
    errs = block.get("userErrors") or []
    if errs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in errs))


def product_reorder_media(product_gid: str, media_ids: list[str]) -> None:
    """Set the gallery order for a product. ``media_ids`` must list every media GID in the desired order."""
    q = """
    mutation ProductReorderMedia($id: ID!, $moves: [MoveInput!]!) {
      productReorderMedia(id: $id, moves: $moves) {
        mediaUserErrors {
          field
          message
        }
      }
    }
    """
    moves = [{"id": mid, "newPosition": str(i)} for i, mid in enumerate(media_ids)]
    data = _graphql_data(q, {"id": product_gid, "moves": moves})
    block = data.get("productReorderMedia") or {}
    errs = block.get("mediaUserErrors") or []
    if errs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in errs))


def _clear_files_with_name(target_filename: str) -> None:
    """Delete Shopify Files whose name matches *target_filename*.

    ``productDeleteMedia`` removes media from a product but leaves the
    underlying file in Shopify's file storage.  If we then upload a new
    file with the same name, Shopify appends a UUID to de-duplicate.
    Clearing the orphaned file first guarantees a clean filename.
    """
    stem = os.path.splitext(target_filename)[0]
    if not stem:
        return
    find_q = """
    query FindFilesByName($query: String!) {
      files(first: 20, query: $query) {
        edges {
          node { id }
        }
      }
    }
    """
    try:
        data = _graphql_data(find_q, {"query": f"filename:'{stem}'"})
        edges = (data.get("files") or {}).get("edges") or []
        file_ids = [e["node"]["id"] for e in edges if e.get("node", {}).get("id")]
        if not file_ids:
            return
        del_q = """
        mutation FileDelete($fileIds: [ID!]!) {
          fileDelete(fileIds: $fileIds) {
            deletedFileIds
            userErrors { field message }
          }
        }
        """
        del_data = _graphql_data(del_q, {"fileIds": file_ids})
        del_errs = (del_data.get("fileDelete") or {}).get("userErrors") or []
        if del_errs:
            logger.warning("fileDelete userErrors for '%s': %s", stem, del_errs)
        else:
            logger.info("Cleared %d orphaned file(s) for '%s'", len(file_ids), stem)
    except Exception:
        logger.exception("Failed to clear files with name '%s'", stem)


def download_image_bytes(url: str, *, timeout_s: float = 90.0) -> tuple[bytes, str]:
    """GET image URL; returns (bytes, content-type or '')."""
    r = requests.get(
        url,
        timeout=timeout_s,
        headers={"User-Agent": "ShopifySEO/1.0 (image-optimize)"},
    )
    r.raise_for_status()
    mime = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    return r.content, mime


def replace_product_image_with_upload(
    product_gid: str,
    *,
    old_media_id: str,
    old_image_url: str,
    image_bytes: bytes,
    filename: str,
    mime_type: str,
    alt: str,
    variant_gids_to_repoint: list[str],
) -> dict[str, Any]:
    """Stage upload, delete old media + orphaned file, create new media, repoint variants, restore position."""
    current_media = fetch_product_media_for_match(product_gid)
    old_position = next(
        (i for i, m in enumerate(current_media) if m["id"] == old_media_id),
        None,
    )

    staged_resource_url = stage_image_bytes_post_resource_url(image_bytes, filename, mime_type)

    product_delete_media(product_gid, [old_media_id])
    _clear_files_with_name(filename)
    time.sleep(1)

    new_media_id = product_create_media_from_url(
        product_gid, image_url=staged_resource_url, alt=alt
    )
    new_cdn_url = _wait_media_image_cdn_url(new_media_id, timeout_s=90.0, interval_s=1.5)

    for vgid in variant_gids_to_repoint:
        try:
            product_variant_set_media(vgid, new_media_id)
        except Exception:
            logger.exception("Variant media repoint failed variant=%s", vgid)
            raise

    if old_position is not None:
        try:
            updated_media = fetch_product_media_for_match(product_gid)
            ordered_ids = [m["id"] for m in updated_media if m["id"] != new_media_id]
            ordered_ids.insert(old_position, new_media_id)
            product_reorder_media(product_gid, ordered_ids)
        except Exception:
            logger.exception("Media reorder failed for product=%s", product_gid)

    return {
        "new_media_id": new_media_id,
        "new_image_url": new_cdn_url,
        "old_media_id": old_media_id,
        "old_image_url": old_image_url,
    }
