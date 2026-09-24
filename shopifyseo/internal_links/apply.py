"""Apply link suggestions: wrap the anchor, push to Shopify, update local state."""
from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from typing import Callable
from urllib.parse import urlparse

from ..dashboard_queries._urls import build_store_internal_link_allowlist, object_url_with_base


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
) -> dict:
    """Apply one suggestion: wrap anchor, push to Shopify, then update local DB.

    Raises on push failure; local state is only mutated after the push succeeds.
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
        from urllib.parse import urlparse

        from ..dashboard_ai_engine_parts._article_draft import sanitize_article_internal_links

        no_caps = {"collection": 10_000, "product": 10_000, "page": 10_000, "blog_article": 10_000}
        targets, _, _ = build_store_internal_link_allowlist(conn, base_url, caps=no_caps)
        path_to_canonical: dict[str, str] = {}
        for t in targets:
            allowlist_url = (t.get("url") or "").strip()
            if not allowlist_url:
                continue
            parsed_path = urlparse(allowlist_url).path or ""
            pk = parsed_path.rstrip("/") or "/"
            if pk and pk not in path_to_canonical:
                path_to_canonical[pk] = allowlist_url
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

    # Update sibling suggestions for the same source: refresh source_body_hash
    # so subsequent applies on the same page don't fail the stale body check.
    new_body_hash = _hash_body(new_body)
    conn.execute(
        "UPDATE link_suggestions SET source_body_hash = ? "
        "WHERE source_type = ? AND source_handle = ? AND status = 'suggested'",
        (new_body_hash, source_type, source_handle),
    )
    # Clear ai_anchor_html for ai_woven siblings - their draft was for the old body
    conn.execute(
        "UPDATE link_suggestions SET ai_anchor_html = NULL "
        "WHERE source_type = ? AND source_handle = ? AND status = 'suggested' AND kind = 'ai_woven'",
        (source_type, source_handle),
    )

    conn.commit()
    return {"status": "applied", "url": url}


def remove_link_from_html(html: str, href: str) -> str | None:
    """Remove a single <a href="...">...</a> link from HTML by its href.
    
    Returns the modified HTML, or None if no matching link was found.
    Only removes the first matching link to be safe.
    """
    if not html or not href:
        return None
    
    parsed_href = urlparse(href)
    href_path = parsed_href.path.rstrip("/") or "/"
    
    # Pattern to find <a href="..." ...>...</a> - handles various attribute orderings
    link_pattern = re.compile(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL
    )
    
    def matches_href(found_href: str) -> bool:
        """Check if found_href matches the target href (path comparison)."""
        found_parsed = urlparse(found_href)
        found_path = found_parsed.path.rstrip("/") or "/"
        return found_path == href_path
    
    # Find all links and replace the first match
    for match in link_pattern.finditer(html):
        found_href = match.group(1)
        if matches_href(found_href):
            anchor_text = match.group(2)
            # Replace the entire <a>...</a> with just the anchor text
            return html[:match.start()] + anchor_text + html[match.end():]
    
    return None


def undo_suggestion(
    conn: sqlite3.Connection,
    suggestion_id: int,
    base_url: str,
    push_fn: Callable | None = None,
) -> dict:
    """Undo an applied suggestion: remove the link from live Shopify body.
    
    - Only works on suggestions with status='applied'
    - Removes the specific <a href="...">...</a> from the body, keeping anchor text
    - Pushes updated body to Shopify
    - Removes the internal_links edge
    - Sets suggestion status to 'undone'
    
    Raises on push failure; local state is only mutated after push succeeds.
    """
    push_fn = push_fn or _shopify_push
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if sug["status"] != "applied":
        raise ValueError(f"suggestion {suggestion_id} is {sug['status']}, not applied")

    source_type, source_handle = sug["source_type"], sug["source_handle"]
    table, where, body_col, _cols = _SOURCE_META[source_type]
    row = _load_source_row(conn, source_type, source_handle)
    
    current_body = row[body_col] or ""
    
    # Build the target URL that was used when applying
    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])
    
    # Remove the link from the body
    new_body = remove_link_from_html(current_body, url)
    if new_body is None:
        # Link not found in current body - it may have been manually removed
        # Still update status but note this in the response
        conn.execute(
            "UPDATE link_suggestions SET status = 'undone' WHERE id = ?",
            (suggestion_id,),
        )
        conn.execute(
            "DELETE FROM internal_links WHERE source_type = ? AND source_handle = ? "
            "AND target_type = ? AND target_handle = ?",
            (source_type, source_handle, sug["target_type"], sug["target_handle"]),
        )
        conn.commit()
        return {"status": "undone", "link_not_found": True, "message": "Link not found in current body"}
    
    # Push to Shopify (raises on failure -> nothing below runs)
    push_fn(source_type, row, new_body)
    
    # Update local body
    if source_type == "blog_article":
        blog_h, _, article_h = source_handle.partition("/")
        conn.execute(
            f"UPDATE {table} SET {body_col} = ? WHERE blog_handle = ? AND handle = ?",
            (new_body, blog_h, article_h),
        )
    else:
        conn.execute(f"UPDATE {table} SET {body_col} = ? WHERE handle = ?", (new_body, source_handle))
    
    # Remove the internal_links edge
    conn.execute(
        "DELETE FROM internal_links WHERE source_type = ? AND source_handle = ? "
        "AND target_type = ? AND target_handle = ?",
        (source_type, source_handle, sug["target_type"], sug["target_handle"]),
    )
    
    # Update suggestion status to 'undone'
    conn.execute(
        "UPDATE link_suggestions SET status = 'undone' WHERE id = ?",
        (suggestion_id,),
    )
    
    conn.commit()
    return {"status": "undone", "url": url}


def check_link_present_in_body(body: str | None, href: str) -> bool:
    """Check if a link with the given href exists in the HTML body."""
    if not body or not href:
        return False
    
    parsed_href = urlparse(href)
    href_path = parsed_href.path.rstrip("/") or "/"
    
    link_pattern = re.compile(
        r'<a\s+[^>]*href=["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    
    for match in link_pattern.finditer(body):
        found_href = match.group(1)
        found_parsed = urlparse(found_href)
        found_path = found_parsed.path.rstrip("/") or "/"
        if found_path == href_path:
            return True
    
    return False
