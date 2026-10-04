"""Generate an AI-woven anchor sentence for suggestions with no matching phrase."""
from __future__ import annotations

import json
import logging
import sqlite3

from ..dashboard_queries._urls import object_url_with_base
from .apply import _hash_body, _load_source_row

from . import shopify_io
from .safety import (
    LinkConflict, build_edit, require_ai_enabled, text_diff, validate_edit,
    extract_prompt_paragraphs, AI_RETRIABLE_CODES,
)

logger = logging.getLogger(__name__)

WEAVE_SCHEMA = {
    "name": "link_weave_edit",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "anchor_phrase": {"type": "string", "minLength": 1, "maxLength": 120},
            "insert_sentence": {"type": "string", "maxLength": 300},
            "insert_after_text": {"type": "string"},
        },
        "required": ["anchor_phrase", "insert_sentence", "insert_after_text"],
        "additionalProperties": False,
    },
}

MAX_PROMPT_CHARS = 12000

_PROMPT_TEMPLATE = """\
Generate one internal link by adding a NEW sentence to the existing content.

REQUIRED: You MUST provide all three fields:
1. anchor_phrase: The exact text (2-6 words, max 120 chars) to become the link
2. insert_sentence: A new short sentence (max 300 chars) containing anchor_phrase EXACTLY ONCE
3. insert_after_text: Copy ONE complete sentence from the paragraphs below VERBATIM (at least 40 chars)

The insert_after_text locator MUST be copied exactly as it appears - the server matches it character-for-character.
The server will insert your new sentence after the matching paragraph.

CONSTRAINTS:
- Never return HTML or modify existing text
- anchor_phrase must appear exactly once in insert_sentence
- insert_after_text must match exactly one sentence below
- Avoid unsupported product claims

Target: {title}
URL: {url}

PARAGRAPHS (copy locator verbatim from these):
{paragraphs}"""

_RETRY_PROMPT = """\
Your previous response failed validation: {error}

Please try again with a corrected response. Remember:
- anchor_phrase: 2-6 words, max 120 chars
- insert_sentence: new sentence containing anchor_phrase exactly once
- insert_after_text: copy a complete sentence (40+ chars) VERBATIM from the paragraphs

{original_prompt}"""


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


def _build_prompt(title: str, url: str, body: str) -> str:
    """Build the AI prompt using extracted paragraphs.
    
    Uses extract_prompt_paragraphs to get normalized paragraph text that
    excludes headings and link text. This ensures sentences copied verbatim
    from the prompt will match during locator validation.
    """
    paragraphs = extract_prompt_paragraphs(body)
    
    # Cap total prompt size
    para_text = ""
    for i, para in enumerate(paragraphs, 1):
        line = f"{i}. {para}\n"
        if len(para_text) + len(line) > MAX_PROMPT_CHARS:
            para_text += f"... ({len(paragraphs) - i + 1} more paragraphs truncated)\n"
            break
        para_text += line
    
    if not para_text.strip():
        para_text = "(No eligible paragraph text found)"
    
    return _PROMPT_TEMPLATE.format(title=title, url=url, paragraphs=para_text.strip())


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate string for logging."""
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


def _log_ai_rejection(suggestion_id: int, source_handle: str, code: str, raw: dict) -> None:
    """Log AI rejection at WARNING level with truncated fields."""
    fields = {}
    if isinstance(raw, dict):
        for key in ("anchor_phrase", "insert_sentence", "insert_after_text"):
            val = raw.get(key)
            if val and isinstance(val, str):
                fields[key] = _truncate(val, 200)
    
    logger.warning(
        "AI rejection for suggestion %d (page=%s): code=%s, fields=%s",
        suggestion_id, source_handle, code, fields
    )


def _validate_ai_response(raw, body: str, url: str) -> dict:
    """Validate AI response and build edit. Returns edit dict on success.
    
    Raises LinkConflict with specific error codes on failure.
    """
    # validate_edit handles non-dict, empty anchor, too long, word count, etc.
    edit = validate_edit(raw)
    
    # ai_woven always requires new-sentence mode
    if not edit.get("insert_sentence") or not edit.get("insert_after_text"):
        raise LinkConflict(
            "AI must provide insert_sentence and insert_after_text for ai_woven suggestions.",
            code="ai_missing_sentence"
        )
    
    # build_edit validates locator matching and anchor placement
    build_edit(body, edit, url)
    
    return edit


def generate_ai_anchor(conn, suggestion_id, base_url, call_ai_fn=None, fetch_fn=None):
    """Persist a small validated edit, never model-authored replacement HTML.
    
    Retries AI exactly ONCE on retriable error codes with feedback naming the failure.
    At most 2 AI calls per invocation.
    """
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
    
    # Build prompt using shared extraction
    prompt = _build_prompt(title, url, body)
    
    # First AI call
    raw = call_ai_fn([{"role": "user", "content": prompt}], WEAVE_SCHEMA)
    
    first_error = None
    try:
        edit = _validate_ai_response(raw, body, url)
    except LinkConflict as exc:
        first_error = exc
        
        # Log AI rejection
        if exc.code not in ("page_write_pending", "link_conflict"):
            _log_ai_rejection(suggestion_id, sug["source_handle"], exc.code, raw)
        
        # Only retry on AI-retriable codes
        if exc.code in AI_RETRIABLE_CODES or exc.code in ("insert_locator_no_match", "insert_locator_ambiguous"):
            # Build retry prompt with feedback
            retry_prompt = _RETRY_PROMPT.format(
                error=str(exc),
                original_prompt=prompt
            )
            
            # Second (and final) AI call
            raw = call_ai_fn([{"role": "user", "content": retry_prompt}], WEAVE_SCHEMA)
            
            try:
                edit = _validate_ai_response(raw, body, url)
                first_error = None  # Success on retry
            except LinkConflict as retry_exc:
                # Log retry failure too
                if retry_exc.code not in ("page_write_pending", "link_conflict"):
                    _log_ai_rejection(suggestion_id, sug["source_handle"], retry_exc.code, raw)
                
                # Re-raise with revised_body diff if present
                if isinstance(raw, dict) and isinstance(raw.get("revised_body"), str):
                    raise LinkConflict(
                        str(retry_exc),
                        text_diff=text_diff(body, raw["revised_body"]),
                        code=retry_exc.code,
                        extra={k: v for k, v in retry_exc.detail.items() if k not in ("message", "text_diff")}
                    ) from None
                raise
        else:
            # Non-retriable error (safety refusal) - re-raise with diff if present
            if isinstance(raw, dict) and isinstance(raw.get("revised_body"), str):
                raise LinkConflict(
                    str(exc),
                    text_diff=text_diff(body, raw["revised_body"]),
                    code=exc.code,
                    extra={k: v for k, v in exc.detail.items() if k not in ("message", "text_diff")}
                ) from None
            raise
    
    if first_error:
        raise first_error
    
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
