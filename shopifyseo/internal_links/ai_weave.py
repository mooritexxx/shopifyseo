"""Generate an AI-woven anchor sentence for suggestions with no matching phrase."""
from __future__ import annotations

import sqlite3
from typing import Callable

from ..dashboard_queries._urls import object_url_with_base
from .apply import _load_source_row, _SOURCE_META

WEAVE_SCHEMA = {
    "type": "object",
    "properties": {"revised_body": {"type": "string", "minLength": 1}},
    "required": ["revised_body"],
    "additionalProperties": False,
}

_PROMPT = (
    "You are an SEO editor. Revise the HTML body below so it naturally links to the target page. "
    "Change AT MOST one sentence (rewrite one existing sentence or append one short sentence to the most "
    "relevant paragraph). Keep every other character of the HTML identical. The link must use the exact "
    "URL given, with natural anchor text related to the target title.\n\n"
    "Target title: {title}\nTarget URL: {url}\n\nHTML body:\n{body}\n\n"
    'Return JSON: {{"revised_body": "<the full revised HTML body>"}}'
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


def generate_ai_anchor(
    conn: sqlite3.Connection,
    suggestion_id: int,
    base_url: str,
    call_ai_fn: Callable | None = None,
) -> dict:
    """Generate and persist the revised body for an ai_woven suggestion."""
    call_ai_fn = call_ai_fn or _default_call_ai
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if sug["kind"] != "ai_woven":
        raise ValueError("anchor generation only applies to ai_woven suggestions")

    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    body_col = _SOURCE_META[sug["source_type"]][2]
    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])
    title = _target_title(conn, sug["target_type"], sug["target_handle"])
    prompt = _PROMPT.format(title=title, url=url, body=row[body_col] or "")
    raw = call_ai_fn([{"role": "user", "content": prompt}], WEAVE_SCHEMA)
    revised = str(raw.get("revised_body") or "").strip()
    if f'href="{url}"' not in revised:
        raise ValueError("AI response does not contain the target link")
    conn.execute(
        "UPDATE link_suggestions SET ai_anchor_html = ? WHERE id = ?", (revised, suggestion_id)
    )
    conn.commit()
    return {"ai_anchor_html": revised, "current_body": row[body_col] or "", "url": url}
