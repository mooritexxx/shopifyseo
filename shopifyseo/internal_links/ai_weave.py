"""Generate an AI-woven anchor sentence for suggestions with no matching phrase."""
from __future__ import annotations

import json
import sqlite3

from ..dashboard_queries._urls import object_url_with_base
from .apply import _hash_body, _load_source_row

from . import shopify_io
from .safety import LinkConflict, build_edit, require_ai_enabled, text_diff, validate_edit

WEAVE_SCHEMA = {
    "type": "object",
    "properties": {
        "anchor_phrase": {"type": "string", "minLength": 1, "maxLength": 120},
        "insert_sentence": {"type": "string", "maxLength": 300},
        "insert_after_text": {"type": "string"},
    },
    "required": ["anchor_phrase"],
    "additionalProperties": False,
}

_PROMPT = (
    "Suggest exactly one additive internal link edit. Never return HTML or a revised body. "
    "Prefer anchor_phrase: an exact existing phrase outside headings and existing links. "
    "If no suitable phrase exists, also return insert_sentence (one short plain-text sentence, "
    "at most 300 characters, containing anchor_phrase exactly once) and insert_after_text "
    "(the entire visible text of exactly one existing paragraph). "
    "The server will add the link and, if requested, a new paragraph after that paragraph. "
    "Never rewrite, remove or replace existing text. Avoid unsupported product claims.\n\n"
    "Target title: {title}\nTarget URL: {url}\n\nCurrent live HTML:\n{body}"
)


def _default_call_ai(messages: list[dict], json_schema: dict) -> dict:
    from ..dashboard_ai_engine_parts.providers import _call_ai
    from ..dashboard_ai_engine_parts.settings import ai_settings
    from ..dashboard_store import db_connect

    conn = db_connect()
    try:
        settings = ai_settings(conn)
    finally:
        conn.close()
    provider = settings.get("generation_provider") or settings.get("provider") or ""
    model = settings.get("generation_model") or settings.get("model") or ""
    return _call_ai(settings, provider, model, messages, 120, json_schema=json_schema, stage="link_weave")


def _target_title(conn: sqlite3.Connection, t_type: str, t_handle: str) -> str:
    if t_type == "blog_article":
        blog_h, _, article_h = t_handle.partition("/")
        row = conn.execute(
            "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ?", (blog_h, article_h)
        ).fetchone()
    else:
        table = {"product": "products", "collection": "collections", "page": "pages"}[t_type]
        row = conn.execute(f"SELECT title FROM {table} WHERE handle = ?", (t_handle,)).fetchone()
    return (row["title"] if row else "") or t_handle


def generate_ai_anchor(conn, suggestion_id, base_url, call_ai_fn=None, fetch_fn=None):
    """Persist a small validated edit, never model-authored replacement HTML."""
    call_ai_fn = call_ai_fn or _default_call_ai
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug or sug["status"] != "suggested":
        raise LinkConflict("Suggestion not found or no longer pending.")
    if sug["kind"] != "ai_woven":
        raise ValueError("anchor generation only applies to ai_woven suggestions")
    require_ai_enabled(conn, sug["source_type"])
    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    body = (fetch_fn or shopify_io.fetch_body)(sug["source_type"], row)
    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])
    title = _target_title(conn, sug["target_type"], sug["target_handle"])
    raw = call_ai_fn([{"role": "user", "content": _PROMPT.format(title=title, url=url, body=body)}], WEAVE_SCHEMA)
    try:
        edit = validate_edit(raw)
        build_edit(body, edit, url)
    except LinkConflict as exc:
        if isinstance(raw, dict) and isinstance(raw.get("revised_body"), str):
            raise LinkConflict(str(exc), text_diff=text_diff(body, raw["revised_body"])) from None
        raise
    changed = conn.execute(
        "UPDATE link_suggestions SET ai_edit_json = ?, ai_anchor_html = NULL, anchor_phrase = ?, source_body_hash = ? "
        "WHERE id = ? AND status = 'suggested' AND NOT EXISTS ("
        "SELECT 1 FROM link_body_snapshots WHERE suggestion_id = link_suggestions.id "
        "AND status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation'))",
        (json.dumps(edit, sort_keys=True), edit["anchor_phrase"], _hash_body(body), suggestion_id),
    ).rowcount
    if not changed:
        conn.rollback()
        raise LinkConflict("Suggestion changed during generation. Refresh the list.")
    conn.commit()
    return {"edit": edit, "url": url}
