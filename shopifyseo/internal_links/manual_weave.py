"""Manual-weave: apply a hand-written ai_woven sentence without running an AI job.

This module provides a way to manually submit an append-only sentence insertion
for ai_woven link suggestions, bypassing the AI generation step. The inserted
text must pass all content compliance checks (G8-G11) defined in the design.

No AI or LLM code path is reachable from this module.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import sqlite3
from html.parser import HTMLParser
from typing import Callable

from ..dashboard_queries._urls import object_url_with_base
from . import shopify_io
from .apply import preview_suggestion, _load_source_row, check_link_present_in_body
from .compliance import manual_weave_gaps, build_tvpa_allowlist
from .graph import resolve_internal_target, extract_links
from .pipeline import _target_exists_and_published, _target_title_and_keywords
from .safety import (
    LinkConflict,
    require_ai_enabled,
    body_hash,
    build_edit,
    guard_edit,
    _normalize_for_matching,
    MANUAL_LINK_CAP,
)

__all__ = ["submit_manual_weave", "ManualWeaveRejected"]


class ManualWeaveRejected(ValueError):
    """Raised when manual-weave content fails validation checks."""
    
    def __init__(self, message: str, gaps: list[str]):
        super().__init__(message)
        self.gaps = gaps


class _LinkExtractor(HTMLParser):
    """Extract links from a minimal HTML snippet."""
    
    def __init__(self):
        super().__init__()
        self.links: list[dict] = []
        self._current_link: dict | None = None
        self._current_text: list[str] = []
        self._other_tags: list[str] = []
    
    def handle_starttag(self, tag: str, attrs: list):
        if tag == "a":
            if self._current_link is not None:
                self._other_tags.append("nested_a")
            attr_dict = dict(attrs)
            self._current_link = {"href": attr_dict.get("href", ""), "attrs": attr_dict}
            self._current_text = []
        else:
            self._other_tags.append(tag)
    
    def handle_endtag(self, tag: str):
        if tag == "a" and self._current_link is not None:
            self._current_link["text"] = "".join(self._current_text).strip()
            self.links.append(self._current_link)
            self._current_link = None
    
    def handle_data(self, data: str):
        if self._current_link is not None:
            self._current_text.append(data)


def _parse_replacement_sentence(replacement: str, base_url: str, target_type: str, target_handle: str) -> dict:
    """Parse and validate the replacement_sentence HTML.
    
    Returns:
        {"anchor": str, "href": str, "addition_plain": str}
    
    Raises:
        ManualWeaveRejected: If the HTML structure is invalid
    """
    extractor = _LinkExtractor()
    try:
        extractor.feed(replacement)
    except Exception as e:
        raise ManualWeaveRejected(f"Invalid HTML: {e}", ["replacement_sentence contains invalid HTML"])
    
    # Must have exactly one <a> tag
    if len(extractor.links) == 0:
        raise ManualWeaveRejected(
            "No link found in replacement_sentence",
            ["replacement_sentence must contain exactly one <a> tag with an href"]
        )
    if len(extractor.links) > 1:
        raise ManualWeaveRejected(
            "Multiple links found in replacement_sentence",
            ["replacement_sentence must contain exactly one <a> tag"]
        )
    
    link = extractor.links[0]
    
    # Check for disallowed tags
    if extractor._other_tags:
        raise ManualWeaveRejected(
            f"Disallowed tags in replacement_sentence: {extractor._other_tags}",
            ["replacement_sentence may only contain one <a> tag with no other HTML tags"]
        )
    
    # Check link attributes - only href allowed
    allowed_attrs = {"href"}
    extra_attrs = set(link["attrs"].keys()) - allowed_attrs
    if extra_attrs:
        raise ManualWeaveRejected(
            f"Disallowed attributes on link: {extra_attrs}",
            [f"Link may only have href attribute, not {', '.join(extra_attrs)}"]
        )
    
    href = link["href"]
    anchor = link["text"]
    
    if not href:
        raise ManualWeaveRejected(
            "Link has no href attribute",
            ["Link must have a non-empty href"]
        )
    
    if not anchor:
        raise ManualWeaveRejected(
            "Link has no anchor text",
            ["Link must have visible anchor text"]
        )
    
    # Validate href resolves to the expected target
    resolved = resolve_internal_target(href, base_url)
    if not resolved:
        raise ManualWeaveRejected(
            f"Link href '{href}' does not resolve to an internal target",
            ["Link href must be an internal URL (e.g. /products/... or /collections/...)"]
        )
    
    resolved_type, resolved_handle = resolved
    if resolved_type != target_type or resolved_handle != target_handle:
        raise ManualWeaveRejected(
            f"Link points to {resolved_type}/{resolved_handle}, expected {target_type}/{target_handle}",
            [f"Link href must point to the suggestion target: {target_type}/{target_handle}"]
        )
    
    # Extract plain text of the addition (HTML unescaped)
    # The addition is the full replacement minus the <a> tags
    addition_with_link = replacement.strip()
    # Replace the link with just its anchor text to get the plain addition
    link_pattern = re.compile(r'<a\b[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    addition_plain = link_pattern.sub(r'\1', addition_with_link)
    addition_plain = html_mod.unescape(addition_plain).strip()
    
    return {
        "anchor": anchor,
        "href": href,
        "addition_plain": addition_plain,
    }


def _extract_addition(original_sentence: str, replacement_sentence: str) -> str:
    """Extract the addition from replacement_sentence.
    
    The addition is replacement_sentence minus the leading original_sentence.
    Both are compared after HTML unescape and whitespace normalization.
    
    Raises:
        ManualWeaveRejected: If replacement doesn't start with original
    """
    orig_norm = _normalize_for_matching(html_mod.unescape(original_sentence))
    
    # Strip the <a> tag to compare plain text
    link_pattern = re.compile(r'<a\b[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    repl_plain = link_pattern.sub(r'\1', replacement_sentence)
    repl_norm = _normalize_for_matching(html_mod.unescape(repl_plain))
    
    if not repl_norm.startswith(orig_norm):
        raise ManualWeaveRejected(
            "Manual weave is append-only: replacement_sentence must start with the exact original_sentence",
            ["replacement_sentence must begin with the original_sentence verbatim; changes to the original are not allowed"]
        )
    
    # The addition is everything after the original
    addition_norm = repl_norm[len(orig_norm):].strip()
    
    return addition_norm


def submit_manual_weave(
    conn: sqlite3.Connection,
    suggestion_id: int,
    base_url: str,
    original_sentence: str,
    replacement_sentence: str,
    *,
    fetch_fn: Callable | None = None,
) -> dict:
    """Submit a manual-weave edit for an ai_woven suggestion.
    
    This validates and persists a hand-written sentence addition without
    running any AI generation. The caller provides the exact original
    sentence from the live body and a replacement containing the original
    plus an appended addition with exactly one link.
    
    Args:
        conn: Database connection
        suggestion_id: ID of the link_suggestions row
        base_url: Store base URL for link resolution
        original_sentence: Exact sentence from the live body to append to
        replacement_sentence: original_sentence + addition with one <a> link
        fetch_fn: Optional body fetch function (for testing)
    
    Returns:
        Dict with preview data plus edit, existing_link_count, and link_cap
    
    Raises:
        LinkConflict: For state/drift issues (409)
        ManualWeaveRejected: For input/content validation failures (400)
    """
    fetch_fn = fetch_fn or shopify_io.fetch_body
    
    # G1: Load suggestion and validate state
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise LinkConflict("Suggestion not found. Refresh the list.")
    if sug["status"] != "suggested":
        raise LinkConflict(f"Suggestion is {sug['status']}. Refresh the list.")
    if sug["kind"] != "ai_woven":
        raise ManualWeaveRejected(
            f"Manual weave only applies to ai_woven suggestions, not {sug['kind']}",
            [f"This suggestion has kind='{sug['kind']}'; manual-weave requires kind='ai_woven'"]
        )
    
    # G2: Check AI setting is enabled for this source type
    require_ai_enabled(conn, sug["source_type"])
    
    # G3: Check target is linkable
    if not _target_exists_and_published(conn, sug["target_type"], sug["target_handle"]):
        raise LinkConflict("The target is no longer eligible for linking. Refresh the catalog.")
    
    # Parse the replacement sentence to extract link info
    parsed = _parse_replacement_sentence(
        replacement_sentence,
        base_url,
        sug["target_type"],
        sug["target_handle"],
    )
    anchor = parsed["anchor"]
    addition_plain = parsed["addition_plain"]
    
    # G7: Validate append-only
    addition_text = _extract_addition(original_sentence, replacement_sentence)
    
    # Build the edit descriptor
    after_sentence_norm = _normalize_for_matching(original_sentence)
    edit = {
        "origin": "manual",
        "anchor_phrase": anchor,
        "after_sentence": after_sentence_norm,
        "append_text": addition_text,
    }
    
    # Get target title and keywords for anchor quality check
    target_title_keywords = _target_title_and_keywords(conn, sug["target_type"], sug["target_handle"])
    target_title = target_title_keywords[0] if target_title_keywords else ""
    target_keywords = target_title_keywords[1:] if len(target_title_keywords) > 1 else []
    
    # Get source title for TVPA allowlist
    source_title = ""
    if sug["source_type"] == "blog_article":
        blog_h, _, article_h = sug["source_handle"].partition("/")
        row = conn.execute(
            "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ?",
            (blog_h, article_h),
        ).fetchone()
        if row:
            source_title = row["title"] or ""
    elif sug["source_type"] in ("product", "collection"):
        table = "products" if sug["source_type"] == "product" else "collections"
        row = conn.execute(f"SELECT title FROM {table} WHERE handle = ?", (sug["source_handle"],)).fetchone()
        if row:
            source_title = row["title"] or ""
    
    # Build TVPA allowlist
    tvpa_allowlist = build_tvpa_allowlist(source_title, target_title)
    
    # G8-G11: Run compliance checks on the addition
    gaps = manual_weave_gaps(
        addition_text,
        anchor,
        allowed_names=tvpa_allowlist,
        target_title=target_title,
        target_keywords=target_keywords,
    )
    
    if gaps:
        raise ManualWeaveRejected(
            f"Content validation failed: {len(gaps)} issue(s)",
            gaps,
        )
    
    # Load source row and fetch live body
    source_row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    live_body = fetch_fn(sug["source_type"], source_row)
    
    # G3: Check target not already linked
    target_url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])
    if check_link_present_in_body(live_body, target_url):
        raise LinkConflict("The current Shopify body already links to this target.")
    
    # G4-G6, G12: Build and validate the edit
    try:
        new_body = build_edit(live_body, edit, target_url)
    except LinkConflict:
        raise
    
    guard_edit(live_body, new_body, edit, target_url)
    
    # Count existing links for response
    existing_links = len([h for h, _ in extract_links(live_body) if h.strip()])
    
    # Persist the edit (G14, G16)
    # Use conditional UPDATE to ensure no concurrent modification
    live_hash = body_hash(live_body)
    edit_json = json.dumps(edit, sort_keys=True)
    
    # Check for pending snapshot that would block the update
    pending = conn.execute(
        "SELECT 1 FROM link_body_snapshots WHERE suggestion_id = ? AND status IN "
        "('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')",
        (suggestion_id,),
    ).fetchone()
    if pending:
        raise LinkConflict("Another write on this page is in progress or needs reconciliation.")
    
    cursor = conn.execute(
        """
        UPDATE link_suggestions
        SET ai_edit_json = ?, ai_anchor_html = NULL, anchor_phrase = ?, source_body_hash = ?
        WHERE id = ? AND status = 'suggested'
        AND NOT EXISTS (
            SELECT 1 FROM link_body_snapshots
            WHERE suggestion_id = link_suggestions.id
            AND status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')
        )
        """,
        (edit_json, anchor, live_hash, suggestion_id),
    )
    
    if cursor.rowcount == 0:
        conn.rollback()
        raise LinkConflict("Suggestion changed while processing. Refresh the list.")
    
    conn.commit()
    
    # Return preview result plus extra fields
    preview_result = preview_suggestion(conn, suggestion_id, base_url, fetch_fn=fetch_fn)
    
    if not preview_result.get("allowed"):
        # Race condition: something changed between persist and preview
        raise LinkConflict(preview_result.get("reason", "Preview not allowed after persist"))
    
    return {
        **preview_result,
        "edit": edit,
        "existing_link_count": existing_links,
        "link_cap": MANUAL_LINK_CAP,
    }
