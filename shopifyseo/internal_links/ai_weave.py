"""Generate an AI-woven anchor sentence for suggestions with no matching phrase."""
from __future__ import annotations

import html as html_mod
import json
import logging
import re
import sqlite3
from html.parser import HTMLParser

from ..dashboard_queries._urls import object_url_with_base
from .apply import _hash_body, _load_source_row

from . import shopify_io
from .safety import LinkConflict, build_edit, require_ai_enabled, text_diff, validate_edit

logger = logging.getLogger(__name__)

# Error codes for generator-fault (retriable) vs safety-guard (non-retriable)
_RETRIABLE_CODES = frozenset({
    "ai_empty_anchor",
    "ai_anchor_too_long",
    "ai_anchor_word_count",
    "ai_anchor_not_in_sentence",
    "ai_anchor_not_in_text",
    "insert_locator_no_match",
    "insert_locator_ambiguous",
})

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

# ai_woven suggestions exist because no existing phrase matched, so require NEW-SENTENCE mode.
# We give the model plain allowed text (excluding headings and text inside existing <a>).
_PROMPT = (
    "You must write a NEW sentence containing the link anchor. Do NOT use an existing phrase. "
    "Return JSON with these keys: anchor_phrase, insert_sentence, insert_after_text.\n\n"
    "RULES:\n"
    "1. anchor_phrase: 2-6 words, at most 120 characters, describing the target page.\n"
    "2. insert_sentence: One plain-text sentence (at most 300 chars) containing anchor_phrase VERBATIM exactly once. "
    "No HTML. End with a period, question mark, or exclamation point.\n"
    "3. insert_after_text: Copy ONE sentence VERBATIM from the allowed paragraphs below. "
    "This locator sentence (40+ characters) identifies where to insert your new sentence.\n\n"
    "The server will insert your new sentence (with anchor_phrase linked) after the paragraph "
    "containing your locator.\n\n"
    "Target title: {title}\nTarget URL: {url}\n\n"
    "Allowed paragraphs (plain text, no headings, no existing link text):\n{allowed_text}"
)

_RETRY_FEEDBACK = (
    "Your previous response was rejected: {error}. Try again with a different approach.\n"
    "Previous anchor_phrase: {anchor}\n"
    "Previous insert_sentence: {sentence}\n"
    "Previous locator: {locator}\n\n"
)


# Protected tags for eligible text extraction (headings and links excluded)
_ELIGIBLE_PROTECTED = {"a", "script", "style", "textarea", "title", "code", "pre", "button", "svg", "template",
                       "h1", "h2", "h3", "h4", "h5", "h6"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _EligibleTextExtractor(HTMLParser):
    """Extract plain text eligible for anchor placement (excludes headings and link text)."""
    
    def __init__(self, body: str):
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._current_para: list[str] = []
        self._in_para = False
        self._protected_depth = 0
        self._stack: list[str] = []
        self.feed(body)
        self.close()
        # Flush any remaining paragraph
        if self._current_para:
            text = " ".join("".join(self._current_para).split())
            if text:
                self.paragraphs.append(text)
    
    def handle_starttag(self, tag, attrs):
        if tag in _ELIGIBLE_PROTECTED:
            self._protected_depth += 1
        if tag == "p" and self._protected_depth == 0:
            self._in_para = True
            self._current_para = []
        if tag not in _VOID_TAGS:
            self._stack.append(tag)
    
    def handle_endtag(self, tag):
        if tag in self._stack:
            idx = len(self._stack) - 1 - self._stack[::-1].index(tag)
            popped = self._stack[idx:]
            self._stack = self._stack[:idx]
            for t in popped:
                if t in _ELIGIBLE_PROTECTED and self._protected_depth > 0:
                    self._protected_depth -= 1
        
        if tag == "p" and self._in_para:
            text = " ".join("".join(self._current_para).split())
            if text:
                self.paragraphs.append(text)
            self._current_para = []
            self._in_para = False
    
    def handle_data(self, data):
        if self._protected_depth == 0 and self._in_para:
            self._current_para.append(data)


def _extract_eligible_paragraphs(body: str) -> list[str]:
    """Extract paragraphs of plain text eligible for anchor placement."""
    extractor = _EligibleTextExtractor(body)
    return extractor.paragraphs


def _format_allowed_text(paragraphs: list[str]) -> str:
    """Format paragraphs for the prompt, numbering them."""
    if not paragraphs:
        return "(No eligible paragraphs found.)"
    lines = []
    for i, p in enumerate(paragraphs, 1):
        # Truncate very long paragraphs to avoid overwhelming the model
        if len(p) > 500:
            p = p[:497] + "..."
        lines.append(f"[{i}] {p}")
    return "\n".join(lines)


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


def _log_rejection(suggestion_id: int, source_type: str, source_handle: str, 
                   error_code: str, raw: dict) -> None:
    """Log rejected AI output at warning level with structured data."""
    anchor = str(raw.get("anchor_phrase", ""))[:200]
    sentence = str(raw.get("insert_sentence", ""))[:200]
    locator = str(raw.get("insert_after_text", ""))[:200]
    
    logger.warning(
        "AI weave rejected: suggestion_id=%d page=%s/%s code=%s anchor=%r sentence=%r locator=%r",
        suggestion_id, source_type, source_handle, error_code, anchor, sentence, locator,
        extra={
            "suggestion_id": suggestion_id,
            "source_type": source_type,
            "source_handle": source_handle,
            "error_code": error_code,
            "ai_anchor": anchor,
            "ai_sentence": sentence,
            "ai_locator": locator,
        }
    )


def _validate_anchor_constraints(raw: dict, eligible_paragraphs: list[str]) -> None:
    """Validate anchor constraints and raise LinkConflict with specific codes."""
    phrase = raw.get("anchor_phrase", "")
    if isinstance(phrase, str):
        phrase = phrase.strip()
    else:
        phrase = ""
    
    sentence = raw.get("insert_sentence", "")
    if isinstance(sentence, str):
        sentence = sentence.strip()
    else:
        sentence = ""
    
    # Check empty anchor
    if not phrase:
        raise LinkConflict(
            "The AI returned an empty anchor phrase. Generate a new suggestion.",
            code="ai_empty_anchor",
            extra={"anchor_phrase": ""}
        )
    
    # Check anchor length
    if len(phrase) > 120:
        raise LinkConflict(
            f"The AI anchor phrase is too long ({len(phrase)} chars, max 120).",
            code="ai_anchor_too_long",
            extra={"anchor_phrase": phrase[:200], "length": len(phrase)}
        )
    
    # Check word count (2-6 words)
    word_count = len(phrase.split())
    if word_count < 2 or word_count > 6:
        raise LinkConflict(
            f"The AI anchor phrase has {word_count} words (must be 2-6 words).",
            code="ai_anchor_word_count",
            extra={"anchor_phrase": phrase, "word_count": word_count}
        )
    
    # Check anchor appears in sentence (for insert mode)
    if sentence:
        if phrase not in sentence:
            raise LinkConflict(
                "The anchor phrase does not appear verbatim in the new sentence.",
                code="ai_anchor_not_in_sentence",
                extra={"anchor_phrase": phrase, "insert_sentence": sentence[:200]}
            )
    else:
        # phrase_wrap mode: check anchor appears in eligible text
        combined_text = " ".join(eligible_paragraphs)
        if phrase.lower() not in combined_text.lower():
            raise LinkConflict(
                "The anchor phrase is not present in eligible body text. "
                "Use insert_sentence mode with a new sentence.",
                code="ai_anchor_not_in_text",
                extra={"anchor_phrase": phrase}
            )


def generate_ai_anchor(conn, suggestion_id, base_url, call_ai_fn=None, fetch_fn=None):
    """Persist a small validated edit, never model-authored replacement HTML.
    
    Makes at most 2 AI calls: one initial attempt and one retry on retriable errors.
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
    
    # Extract eligible paragraphs for the prompt
    eligible_paragraphs = _extract_eligible_paragraphs(body)
    allowed_text = _format_allowed_text(eligible_paragraphs)
    
    # Build initial prompt
    prompt_content = _PROMPT.format(title=title, url=url, allowed_text=allowed_text)
    messages = [{"role": "user", "content": prompt_content}]
    
    # Track AI call count (max 2)
    ai_calls = 0
    last_error = None
    last_raw = None
    
    for attempt in range(2):  # At most 2 AI calls
        ai_calls += 1
        raw = call_ai_fn(messages, WEAVE_SCHEMA)
        
        try:
            # Validate anchor constraints first (before validate_edit)
            _validate_anchor_constraints(raw, eligible_paragraphs)
            
            edit = validate_edit(raw)
            build_edit(body, edit, url)
            
            # Success - persist and return
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
            
        except LinkConflict as exc:
            last_error = exc
            last_raw = raw
            
            # Log the rejection
            _log_rejection(suggestion_id, sug["source_type"], sug["source_handle"], exc.code, raw)
            
            # Check if we should retry
            if attempt == 0 and exc.code in _RETRIABLE_CODES:
                # Build retry feedback
                anchor = str(raw.get("anchor_phrase", ""))[:100]
                sentence = str(raw.get("insert_sentence", ""))[:200]
                locator = str(raw.get("insert_after_text", ""))[:200]
                
                feedback = _RETRY_FEEDBACK.format(
                    error=str(exc),
                    anchor=anchor,
                    sentence=sentence,
                    locator=locator
                )
                
                # Add feedback and re-prompt
                messages = [{"role": "user", "content": feedback + prompt_content}]
                continue
            
            # Non-retriable error or second attempt failed
            if isinstance(raw, dict) and isinstance(raw.get("revised_body"), str):
                raise LinkConflict(
                    str(exc),
                    text_diff=text_diff(body, raw["revised_body"]),
                    code=exc.code,
                    extra={k: v for k, v in exc.detail.items() if k not in ("message", "text_diff")}
                ) from None
            raise
    
    # Should not reach here, but if we do, raise the last error
    raise last_error
