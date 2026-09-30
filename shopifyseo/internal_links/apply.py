"""Preview, apply and undo lossless edits against the current Shopify body."""
from __future__ import annotations

import difflib
import json
import logging
import re
import sqlite3
import time
from urllib.parse import urlparse

from ..dashboard_queries._urls import object_url_with_base
from . import shopify_io
from .graph import extract_links, resolve_internal_target
from .safety import (LinkConflict, body_hash, build_edit, guard_edit, preview_token,
                     require_ai_enabled, text_diff, validate_edit, verify_token)

logger = logging.getLogger(__name__)
_hash_body = body_hash
_SOURCE_META = {
    "blog_article": ("blog_articles", "blog_handle = ? AND handle = ?", "body", "shopify_id, body"),
    "product": ("products", "handle = ?", "description_html", "shopify_id, description_html"),
    "collection": ("collections", "handle = ?", "description_html", "shopify_id, description_html"),
}

def _log_suggestion_event(
    conn: sqlite3.Connection,
    suggestion: sqlite3.Row | dict,
    event_type: str,
) -> None:
    """Log an event to link_suggestion_events for measurement (Phase D).
    
    Args:
        conn: Database connection
        suggestion: The suggestion row/dict
        event_type: One of 'apply', 'undo', 'dismiss', 'auto_apply'
    """
    try:
        # Get current GSC clicks for the source page
        gsc_clicks = None
        src_type = suggestion["source_type"]
        src_handle = suggestion["source_handle"]
        
        if src_type == "blog_article":
            blog_h, _, article_h = src_handle.partition("/")
            row = conn.execute(
                "SELECT gsc_clicks FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                (blog_h, article_h),
            ).fetchone()
        elif src_type in ("product", "collection", "page"):
            table = {"product": "products", "collection": "collections", "page": "pages"}[src_type]
            row = conn.execute(
                f"SELECT gsc_clicks FROM {table} WHERE handle = ?",
                (src_handle,),
            ).fetchone()
        else:
            row = None
        
        if row:
            gsc_clicks = row["gsc_clicks"]
        
        conn.execute(
            """
            INSERT INTO link_suggestion_events 
            (suggestion_id, event_type, source_type, source_handle, target_type, target_handle, 
             kind, score, gsc_clicks_at_event, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                suggestion["id"],
                event_type,
                src_type,
                src_handle,
                suggestion["target_type"],
                suggestion["target_handle"],
                suggestion["kind"],
                suggestion["score"] if "score" in suggestion.keys() else None,
                gsc_clicks,
                int(time.time()),
            ),
        )
    except Exception:
        logger.warning("Failed to log suggestion event", exc_info=True)


def _params(source_type, source_handle):
    return tuple(source_handle.split("/", 1)) if source_type == "blog_article" else (source_handle,)


def _load_source_row(conn, source_type, source_handle):
    if source_type not in _SOURCE_META:
        raise LinkConflict("This object type is not supported as a link source.")
    table, where, _, cols = _SOURCE_META[source_type]
    row = conn.execute(f"SELECT {cols} FROM {table} WHERE {where}", _params(source_type, source_handle)).fetchone()
    if not row or not row["shopify_id"]:
        raise LinkConflict("The source object has no Shopify identity. Refresh the catalog.")
    return row


def wrap_phrase_in_html(body, phrase, url):
    try:
        return build_edit(body, {"anchor_phrase": phrase}, url)
    except LinkConflict:
        return None


def _suggestion(conn, suggestion_id):
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise LinkConflict("Suggestion not found. Refresh the list.")
    return sug


def _candidate(conn, suggestion_id, base_url, fetch_fn):
    sug = _suggestion(conn, suggestion_id)
    if sug["status"] != "suggested":
        raise LinkConflict(f"Suggestion is {sug['status']}. Refresh the list.")
    if sug["kind"] == "ai_woven":
        require_ai_enabled(conn, sug["source_type"])
        if not sug["ai_edit_json"]:
            raise LinkConflict("Generate a new suggestion. Legacy full-body AI responses cannot be applied.")
        try:
            edit = validate_edit(json.loads(sug["ai_edit_json"]))
        except (ValueError, TypeError) as exc:
            if isinstance(exc, LinkConflict):
                raise
            raise LinkConflict("Invalid AI edit. Generate a new suggestion.") from None
    elif sug["kind"] == "phrase_wrap":
        edit = {"anchor_phrase": sug["anchor_phrase"]}
    else:
        raise LinkConflict("Unsupported link edit.")
    # Validate just this target; never sanitize or scan the entire catalog body.
    from .pipeline import _target_exists_and_published
    if not _target_exists_and_published(conn, sug["target_type"], sug["target_handle"]):
        raise LinkConflict("The target is no longer eligible for linking. Refresh the catalog.")
    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    old = fetch_fn(sug["source_type"], row)
    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])
    if check_link_present_in_body(old, url):
        raise LinkConflict("The current Shopify body already links to this target.")
    new = build_edit(old, edit, url)
    guard_edit(old, new, edit, url)
    binding = {"suggestion_id": suggestion_id, "shopify_id": row["shopify_id"],
               "source_type": sug["source_type"], "target_url": url,
               "old_hash": body_hash(old), "new_hash": body_hash(new),
               "edit_hash": body_hash(json.dumps(edit, sort_keys=True))}
    return sug, row, old, new, edit, url, binding


def preview_suggestion(conn, suggestion_id, base_url, fetch_fn=None):
    # Stateless signed approval: this endpoint performs no database or Shopify writes.
    try:
        sug, row, old, new, edit, url, binding = _candidate(conn, suggestion_id, base_url, fetch_fn or shopify_io.fetch_body)
    except LinkConflict as exc:
        return {"suggestion_id": suggestion_id, "allowed": False, "reason": str(exc),
                "old_html": None, "new_html": None, "text_diff": exc.detail["text_diff"], "preview_token": None}
    return {"suggestion_id": suggestion_id, "kind": sug["kind"], "old_html": old, "new_html": new,
            "text_diff": text_diff(old, new), "html_diff": "\n".join(difflib.unified_diff(
                old.splitlines(), new.splitlines(), fromfile="Current Shopify HTML", tofile="With link", lineterm="")),
            "anchor_phrase": edit["anchor_phrase"], "target_url": url, "allowed": True,
            "preview_token": preview_token(binding)}


def _status(conn, snapshot_id, status, error=None):
    conn.execute("UPDATE link_body_snapshots SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                 (status, error, int(time.time()), snapshot_id))
    conn.commit()


def _update_local(conn, sug, body, base_url, event):
    table, where, body_col, _ = _SOURCE_META[sug["source_type"]]
    conn.execute(f"UPDATE {table} SET {body_col} = ? WHERE {where}",
                 (body, *_params(sug["source_type"], sug["source_handle"])))
    # Reconcile every edge on this object to the actual live body, including live-only links.
    conn.execute("DELETE FROM internal_links WHERE source_type = ? AND source_handle = ?",
                 (sug["source_type"], sug["source_handle"]))
    for href, anchor in extract_links(body):
        target = resolve_internal_target(href, base_url)
        if target:
            conn.execute("INSERT OR IGNORE INTO internal_links "
                         "(source_type, source_handle, target_type, target_handle, anchor_text, href) VALUES (?,?,?,?,?,?)",
                         (sug["source_type"], sug["source_handle"], *target, anchor, href))
    conn.execute("UPDATE link_suggestions SET status = ?, applied_at = ? WHERE id = ?",
                 ("undone" if event == "undo" else "applied", int(time.time()), sug["id"]))
    conn.execute("UPDATE link_suggestions SET source_body_hash = ?, ai_edit_json = NULL, ai_anchor_html = NULL "
                 "WHERE source_type = ? AND source_handle = ? AND status = 'suggested'",
                 (body_hash(body), sug["source_type"], sug["source_handle"]))
    _log_suggestion_event(conn, sug, event)


def _finish(conn, snapshot, sug, base_url, undo=False):
    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    if row["shopify_id"] != snapshot["shopify_id"]:
        raise LinkConflict("The Shopify object identity changed. Reconciliation is required.")
    _update_local(conn, sug, snapshot["old_body"] if undo else snapshot["new_body"], base_url, "undo" if undo else "apply")
    _status(conn, snapshot["id"], "undone" if undo else "applied")


def apply_suggestion(conn, suggestion_id, base_url, *, preview_token_value="", fetch_fn=None, push_fn=None):
    fetch_fn, push_fn = fetch_fn or shopify_io.fetch_body, push_fn or shopify_io.push_body
    sug, row, old, new, edit, url, binding = _candidate(conn, suggestion_id, base_url, fetch_fn)
    verify_token(preview_token_value, binding)
    now = int(time.time())
    # Commit the backup and reservation BEFORE network dispatch. The unique partial
    # index serializes writes to an object across threads and app processes.
    try:
        cursor = conn.execute("""INSERT INTO link_body_snapshots
            (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,'prepared',?,?)""",
            (suggestion_id, sug["source_type"], sug["source_handle"], row["shopify_id"], old, new, now, now))
        snapshot_id = cursor.lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise LinkConflict("Another write on this page is in progress or needs reconciliation.") from None
    try:
        live = fetch_fn(sug["source_type"], row)
        if live != old:
            raise LinkConflict("Shopify changed after preview. Open a fresh preview.", text_diff=text_diff(old, live))
        fresh = _suggestion(conn, suggestion_id)
        fields = ("kind", "source_type", "source_handle", "target_type", "target_handle", "anchor_phrase", "ai_edit_json")
        if fresh["status"] != "suggested" or any(fresh[field] != sug[field] for field in fields):
            raise LinkConflict("Suggestion changed after preview. Open a fresh preview.")
        if _load_source_row(conn, sug["source_type"], sug["source_handle"])["shopify_id"] != row["shopify_id"]:
            raise LinkConflict("The Shopify object identity changed. Open a fresh preview.")
        if sug["kind"] == "ai_woven":
            require_ai_enabled(conn, sug["source_type"])
        guard_edit(old, new, edit, url)
    except Exception:
        _status(conn, snapshot_id, "failed", "Pre-write validation failed; no Shopify write was attempted.")
        raise
    snapshot = conn.execute("SELECT * FROM link_body_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    try:
        accepted = push_fn(sug["source_type"], row, new)
        if accepted != new:
            raise RuntimeError("Shopify returned different HTML. Reconcile this operation before retrying.")
        _finish(conn, snapshot, sug, base_url)
    except Exception:
        conn.rollback()
        _status(conn, snapshot_id, "needs_reconciliation", "Write outcome needs a live read before retrying.")
        raise
    return {"status": "applied", "url": url, "snapshot_id": snapshot_id}


def undo_suggestion(conn, suggestion_id, base_url, *, fetch_fn=None, push_fn=None):
    fetch_fn, push_fn = fetch_fn or shopify_io.fetch_body, push_fn or shopify_io.push_body
    sug = _suggestion(conn, suggestion_id)
    if sug["status"] != "applied":
        raise LinkConflict("Suggestion is not applied.")
    snapshot = conn.execute("SELECT * FROM link_body_snapshots WHERE suggestion_id = ? AND status = 'applied' "
                            "ORDER BY id DESC LIMIT 1", (suggestion_id,)).fetchone()
    if not snapshot:
        raise LinkConflict("This older apply has no backup. Restore it manually in Shopify.")
    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    if row["shopify_id"] != snapshot["shopify_id"]:
        raise LinkConflict("The Shopify object identity changed. Undo is blocked.")
    try:
        changed = conn.execute("UPDATE link_body_snapshots SET status = 'undo_prepared', updated_at = ? "
                               "WHERE id = ? AND status = 'applied'", (int(time.time()), snapshot["id"])).rowcount
        if not changed:
            raise LinkConflict("Another undo is in progress.")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise LinkConflict("Another write on this page is in progress or needs reconciliation.") from None
    try:
        current = fetch_fn(sug["source_type"], row)
        if current != snapshot["new_body"]:
            raise LinkConflict("The page changed after Apply. Undo would overwrite newer work and was blocked.",
                               text_diff=text_diff(snapshot["new_body"], current))
    except Exception:
        _status(conn, snapshot["id"], "applied")
        raise
    try:
        if push_fn(sug["source_type"], row, snapshot["old_body"]) != snapshot["old_body"]:
            raise RuntimeError("Shopify did not confirm the restored HTML. Reconciliation is required.")
        _finish(conn, snapshot, sug, base_url, undo=True)
    except Exception:
        conn.rollback()
        _status(conn, snapshot["id"], "undo_needs_reconciliation", "Undo outcome needs a live read before retrying.")
        raise
    return {"status": "undone", "snapshot_id": snapshot["id"]}


def reconcile_suggestion(conn, suggestion_id, base_url, *, fetch_fn=None):
    """Resolve a timeout/crash by reading Shopify; never repeat a remote write."""
    snapshot = conn.execute("SELECT * FROM link_body_snapshots WHERE suggestion_id = ? AND status IN "
                            "('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation') "
                            "ORDER BY id DESC LIMIT 1", (suggestion_id,)).fetchone()
    if not snapshot:
        raise LinkConflict("There is no unfinished write to reconcile.")
    if snapshot["status"] in ("prepared", "undo_prepared") and time.time() - snapshot["updated_at"] < 900:
        raise LinkConflict("The write may still be running. Wait before reconciling.")
    sug = _suggestion(conn, suggestion_id)
    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    if row["shopify_id"] != snapshot["shopify_id"]:
        raise LinkConflict("The Shopify object identity changed. Reconciliation is blocked.")
    undo = snapshot["status"].startswith("undo")
    claimed = conn.execute(
        "UPDATE link_body_snapshots SET status = ?, updated_at = ? WHERE id = ? AND status = ? AND updated_at = ?",
        ("undo_prepared" if undo else "prepared", int(time.time()), snapshot["id"], snapshot["status"], snapshot["updated_at"]),
    ).rowcount
    conn.commit()
    if not claimed:
        raise LinkConflict("This operation is already being reconciled. Refresh its status.")
    try:
        live = (fetch_fn or shopify_io.fetch_body)(sug["source_type"], row)
        if live == (snapshot["old_body"] if undo else snapshot["new_body"]):
            _finish(conn, snapshot, sug, base_url, undo=undo)
            return {"status": "undone" if undo else "applied"}
        if live == (snapshot["new_body"] if undo else snapshot["old_body"]):
            _status(conn, snapshot["id"], "applied" if undo else "failed")
            return {"status": "not_written"}
        raise LinkConflict("Live content matches neither backup. Keep the backup and reconcile the page manually.")
    except Exception:
        conn.rollback()
        _status(conn, snapshot["id"], "undo_needs_reconciliation" if undo else "needs_reconciliation",
                "Reconciliation could not confirm the live body.")
        raise


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


def check_link_present_in_body(body, href):
    target_path = urlparse(href).path.rstrip("/") or "/"
    target_host = urlparse(href).netloc.lower()
    return any((not urlparse(link).netloc or urlparse(link).netloc.lower() == target_host)
               and (urlparse(link).path.rstrip("/") or "/") == target_path
               for link, _ in extract_links(body or ""))
