"""Apply link suggestions: wrap the anchor, push to Shopify, update local state."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from typing import Callable

from ..dashboard_queries._urls import build_store_internal_link_allowlist, object_url_with_base
from .validation import ShopifyResourceUnreachable, verify_source_api_reachable


def _hash_body(body: str) -> str:
    """SHA-256 hex digest of body text for stale detection."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()

# Split out regions we must not touch: existing links, headings, script/style.
_PROTECTED_RE = re.compile(
    r"(<a\b.*?</a>|<h[1-6]\b.*?</h[1-6]>|<script\b.*?</script>|<style\b.*?</style>)",
    re.IGNORECASE | re.DOTALL,
)


def wrap_phrase_in_html(html: str, phrase: str, url: str) -> str | None:
    """Wrap the first occurrence of *phrase* outside protected regions. None if absent."""
    if not html or not phrase:
        return None
    pattern = re.compile(r"\b(" + re.escape(phrase) + r")\b", re.IGNORECASE)
    segments = _PROTECTED_RE.split(html)
    for i, segment in enumerate(segments):
        if _PROTECTED_RE.fullmatch(segment):
            continue
        # Also skip text that sits inside a tag attribute by only replacing in text nodes:
        # split the segment on tags and substitute only in non-tag parts.
        parts = re.split(r"(<[^>]+>)", segment)
        for j, part in enumerate(parts):
            if part.startswith("<"):
                continue
            new_part, n = pattern.subn(rf'<a href="{url}">\1</a>', part, count=1)
            if n:
                parts[j] = new_part
                segments[i] = "".join(parts)
                return "".join(segments)
    return None


_SOURCE_META = {
    # source_type: (table, where_sql, body_col, select_cols)
    "blog_article": ("blog_articles", "blog_handle = ? AND handle = ?", "body",
                     "shopify_id, title, seo_title, seo_description, body"),
    "product": ("products", "handle = ?", "description_html",
                "shopify_id, title, seo_title, seo_description, description_html, tags_json"),
    "collection": ("collections", "handle = ?", "description_html",
                   "shopify_id, title, seo_title, seo_description, description_html"),
}


def _load_source_row(conn: sqlite3.Connection, source_type: str, source_handle: str) -> sqlite3.Row:
    table, where, _body_col, cols = _SOURCE_META[source_type]
    if source_type == "blog_article":
        blog_h, _, article_h = source_handle.partition("/")
        params: tuple = (blog_h, article_h)
    else:
        params = (source_handle,)
    row = conn.execute(f"SELECT {cols} FROM {table} WHERE {where}", params).fetchone()
    if not row:
        raise ValueError(f"source not found: {source_type}/{source_handle}")
    return row


def _shopify_push(source_type: str, row: sqlite3.Row, new_body: str) -> dict:
    import json

    from ..dashboard_live_updates import (
        live_update_article,
        live_update_collection,
        live_update_product,
    )
    from ..dashboard_store import DB_PATH

    title = row["title"] or ""
    seo_title = row["seo_title"] or ""
    seo_description = row["seo_description"] or ""
    if source_type == "blog_article":
        return live_update_article(str(DB_PATH), row["shopify_id"], title, seo_title, seo_description, new_body)
    if source_type == "collection":
        return live_update_collection(str(DB_PATH), row["shopify_id"], title, seo_title, seo_description, new_body)
    tags = ", ".join(json.loads(row["tags_json"] or "[]"))
    return live_update_product(str(DB_PATH), row["shopify_id"], title, seo_title, seo_description, new_body, tags)


def apply_suggestion(
    conn: sqlite3.Connection,
    suggestion_id: int,
    base_url: str,
    push_fn: Callable | None = None,
    sanitize_fn: Callable | None = None,
    *,
    skip_shopify_check: bool = False,
) -> dict:
    """Apply one suggestion: wrap anchor, push to Shopify, then update local DB.

    Raises on push failure; local state is only mutated after the push succeeds.
    Raises ShopifyResourceUnreachable if the source cannot be accessed via Admin API.

    Args:
        skip_shopify_check: If True, skip the Admin API reachability preflight (for tests).
    """
    push_fn = push_fn or _shopify_push
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if sug["status"] == "applied":
        return {"status": "applied", "already": True}
    if sug["status"] != "suggested":
        raise ValueError(f"suggestion {suggestion_id} is {sug['status']}")

    source_type, source_handle = sug["source_type"], sug["source_handle"]

    # Preflight: verify source is reachable via Admin API before attempting mutation.
    # Some resources (e.g., smart collections with metafield-only rules) exist in Shopify
    # but are not accessible via Admin GraphQL/REST APIs. We cannot push changes to them.
    if not skip_shopify_check:
        verify_source_api_reachable(conn, source_type, source_handle)

    table, where, body_col, _cols = _SOURCE_META[source_type]
    row = _load_source_row(conn, source_type, source_handle)

    current_body = row[body_col] or ""
    current_hash = _hash_body(current_body)
    stored_hash = sug["source_body_hash"] or ""
    if stored_hash and current_hash != stored_hash:
        raise ValueError("Source body changed since suggestion was generated. Regenerate suggestion.")

    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])

    if sug["kind"] == "phrase_wrap":
        new_body = wrap_phrase_in_html(current_body, sug["anchor_phrase"], url)
        if new_body is None:
            raise ValueError("anchor phrase no longer present in body")
    else:
        if not sug["ai_anchor_html"]:
            raise ValueError("ai_woven suggestion has no generated anchor yet")
        new_body = sug["ai_anchor_html"]  # full replacement body produced at review time

    if sanitize_fn is None:
        from ..dashboard_ai_engine_parts._article_draft import sanitize_article_internal_links

        _, allowed_full, allowed_paths = build_store_internal_link_allowlist(conn, base_url)
        path_to_canonical = {p: f for p, f in zip(allowed_paths, allowed_full) if p and f}
        new_body = sanitize_article_internal_links(
            new_body, path_to_canonical=path_to_canonical, base_url=base_url
        )
    else:
        new_body = sanitize_fn(new_body)

    push_fn(source_type, row, new_body)  # raises on failure -> nothing below runs

    if source_type == "blog_article":
        blog_h, _, article_h = source_handle.partition("/")
        conn.execute(
            f"UPDATE {table} SET {body_col} = ? WHERE blog_handle = ? AND handle = ?",
            (new_body, blog_h, article_h),
        )
    else:
        conn.execute(f"UPDATE {table} SET {body_col} = ? WHERE handle = ?", (new_body, source_handle))
    conn.execute(
        "INSERT OR IGNORE INTO internal_links "
        "(source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_type, source_handle, sug["target_type"], sug["target_handle"],
         sug["anchor_phrase"] or "", url),
    )
    conn.execute(
        "UPDATE link_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
        (int(time.time()), suggestion_id),
    )
    conn.commit()
    return {"status": "applied", "url": url}
