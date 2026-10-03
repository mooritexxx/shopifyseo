"""Lossless, server-built link edits and short-lived preview approvals."""
from __future__ import annotations

import base64
import difflib
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import time
from html.parser import HTMLParser

AI_TYPES_KEY = "internal_links_ai_woven_enabled_types"
DEFAULT_AI_TYPES = ("product", "blog_article")
SOURCE_TYPES = ("product", "collection", "blog_article")
# A restart invalidates previews. Multi-worker deployments must share this secret.
_SECRET = os.environ.get("INTERNAL_LINK_PREVIEW_SECRET", "").encode() or secrets.token_bytes(32)
PREVIEW_TTL = 600
MANUAL_LINK_CAP = 8


class LinkConflict(ValueError):
    """Conflict during link suggestion processing.
    
    Args:
        message: Human-readable error message
        text_diff: Optional unified diff for text changes
        code: Error code for programmatic handling (default: "link_conflict")
        extra: Optional dict of additional details to include in self.detail
    """
    def __init__(self, message: str, *, text_diff: str = "", code: str = "link_conflict", extra: dict | None = None):
        super().__init__(message)
        self.code = code
        self.detail = {"message": message, "text_diff": text_diff, **(extra or {})}


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# Block-level tags where inter-tag whitespace is insignificant
_BLOCK_LEVEL_TAGS = frozenset({
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "blockquote",
    "section", "article", "header", "footer", "aside", "nav", "figure",
    "figcaption", "hr", "dl", "dt", "dd",
})

# Preformatted/raw elements where whitespace must never be touched
_PREFORMATTED_TAGS = frozenset({"pre", "textarea", "script", "style"})


def _strip_inter_block_whitespace(html: str) -> str:
    """Remove whitespace-only runs between block-level tags.
    
    Preserves whitespace:
    - Inside <pre>, <textarea>, <script>, <style>
    - Inside text nodes
    - Inside tags/attributes
    - Between inline tags (e.g., </a> <strong>)
    
    Also strips leading/trailing whitespace of the whole document (Q1 default).
    """
    if not html:
        return ""
    
    # Track depth in preformatted elements
    result = []
    i = 0
    pre_depth = 0
    
    while i < len(html):
        # Check for tag start
        if html[i] == '<':
            # Find end of tag
            tag_end = html.find('>', i)
            if tag_end == -1:
                # Malformed: copy rest and stop
                result.append(html[i:])
                break
            
            tag_content = html[i+1:tag_end]
            is_closing = tag_content.startswith('/')
            is_self_closing = tag_content.rstrip().endswith('/') or tag_content.rstrip().endswith('/')
            
            # Extract tag name
            if is_closing:
                tag_part = tag_content[1:].lstrip()
            else:
                tag_part = tag_content.lstrip()
            
            # Get just the tag name (before any attributes or /)
            tag_name_match = re.match(r'^([a-zA-Z][a-zA-Z0-9]*)', tag_part)
            tag_name = tag_name_match.group(1).lower() if tag_name_match else ""
            
            # Track preformatted depth
            if tag_name in _PREFORMATTED_TAGS:
                if is_closing:
                    pre_depth = max(0, pre_depth - 1)
                elif not is_self_closing:
                    pre_depth += 1
            
            result.append(html[i:tag_end + 1])
            i = tag_end + 1
        else:
            # Not a tag - find next tag or end
            next_tag = html.find('<', i)
            if next_tag == -1:
                # Rest of string
                result.append(html[i:])
                break
            
            text = html[i:next_tag]
            
            # If inside preformatted element, keep text as-is
            if pre_depth > 0:
                result.append(text)
            else:
                result.append(text)
            
            i = next_tag
    
    # Join and then apply inter-block whitespace removal
    joined = "".join(result)
    
    # Now do a second pass to remove whitespace between block-level tags
    # Pattern: >(whitespace)< where both tags are block-level
    # We need to parse more carefully to check both the preceding and following tag
    
    def is_block_tag_end(s: str, pos: int) -> bool:
        """Check if position pos is the > of a block-level closing or opening tag."""
        if pos <= 0 or s[pos] != '>':
            return False
        # Find the start of this tag
        tag_start = s.rfind('<', 0, pos)
        if tag_start == -1:
            return False
        tag_content = s[tag_start + 1:pos]
        is_closing = tag_content.startswith('/')
        tag_part = tag_content[1:].lstrip() if is_closing else tag_content.lstrip()
        tag_name_match = re.match(r'^([a-zA-Z][a-zA-Z0-9]*)', tag_part)
        tag_name = tag_name_match.group(1).lower() if tag_name_match else ""
        return tag_name in _BLOCK_LEVEL_TAGS
    
    def is_block_tag_start(s: str, pos: int) -> bool:
        """Check if position pos is the < of a block-level opening or closing tag."""
        if pos >= len(s) or s[pos] != '<':
            return False
        # Find end of tag
        tag_end = s.find('>', pos)
        if tag_end == -1:
            return False
        tag_content = s[pos + 1:tag_end]
        is_closing = tag_content.startswith('/')
        tag_part = tag_content[1:].lstrip() if is_closing else tag_content.lstrip()
        tag_name_match = re.match(r'^([a-zA-Z][a-zA-Z0-9]*)', tag_part)
        tag_name = tag_name_match.group(1).lower() if tag_name_match else ""
        return tag_name in _BLOCK_LEVEL_TAGS
    
    # Find all >\s+< patterns and check if both tags are block-level
    # We need to avoid preformatted elements
    output = []
    i = 0
    pre_depth = 0
    
    while i < len(joined):
        if joined[i] == '<':
            # Track preformatted
            tag_end = joined.find('>', i)
            if tag_end == -1:
                output.append(joined[i:])
                break
            tag_content = joined[i + 1:tag_end]
            is_closing = tag_content.startswith('/')
            tag_part = tag_content[1:].lstrip() if is_closing else tag_content.lstrip()
            tag_name_match = re.match(r'^([a-zA-Z][a-zA-Z0-9]*)', tag_part)
            tag_name = tag_name_match.group(1).lower() if tag_name_match else ""
            
            if tag_name in _PREFORMATTED_TAGS:
                if is_closing:
                    pre_depth = max(0, pre_depth - 1)
                else:
                    is_self_closing = tag_content.rstrip().endswith('/')
                    if not is_self_closing:
                        pre_depth += 1
            
            output.append(joined[i:tag_end + 1])
            i = tag_end + 1
        elif joined[i] == '>' and i + 1 < len(joined):
            # Already added > in the tag handling above, skip
            output.append(joined[i])
            i += 1
        else:
            # Text or whitespace
            if pre_depth > 0:
                # Inside preformatted - keep as-is
                output.append(joined[i])
                i += 1
            else:
                # Check if this is whitespace between block-level tags
                # Look back for > and forward for <
                if i > 0 and joined[i].isspace():
                    # Collect all whitespace
                    ws_start = i
                    while i < len(joined) and joined[i].isspace():
                        i += 1
                    ws_end = i
                    
                    # Check if preceding char is > of a block tag and next char is < of a block tag
                    if (ws_start > 0 and 
                        joined[ws_start - 1] == '>' and 
                        ws_end < len(joined) and 
                        joined[ws_end] == '<' and
                        is_block_tag_end(joined, ws_start - 1) and
                        is_block_tag_start(joined, ws_end)):
                        # Skip the whitespace (don't add it)
                        pass
                    else:
                        # Keep the whitespace
                        output.append(joined[ws_start:ws_end])
                else:
                    output.append(joined[i])
                    i += 1
    
    # Strip leading/trailing whitespace of the whole document (Q1 default)
    return "".join(output).strip()


def html_equivalent(a: str, b: str) -> bool:
    """Check if two HTML strings are equivalent, tolerating inter-block whitespace.
    
    Returns True when the strings are identical after:
    - Removing whitespace-only runs between two block-level tags
    - Stripping leading/trailing document whitespace
    
    Block-level tags: p, div, h1-h6, ul, ol, li, table, thead, tbody, tfoot,
    tr, td, th, blockquote, section, article, header, footer, aside, nav,
    figure, figcaption, hr, dl, dt, dd.
    
    Does NOT collapse whitespace:
    - Between inline tags (visible space like "</a> <strong>")
    - Inside <pre>, <textarea>, <script>, <style>
    - Inside text nodes
    - Inside tags/attributes
    
    Any other difference (text, attributes, entity encoding, tag names) fails.
    """
    if a == b:
        return True
    return _strip_inter_block_whitespace(a) == _strip_inter_block_whitespace(b)


def ai_enabled_types(conn) -> list[str]:
    row = conn.execute("SELECT value FROM service_settings WHERE key = ?", (AI_TYPES_KEY,)).fetchone()
    if row is None:
        return list(DEFAULT_AI_TYPES)
    return [t for t in (row[0] or "").split(",") if t in SOURCE_TYPES]


def require_ai_enabled(conn, source_type: str) -> None:
    if source_type not in ai_enabled_types(conn):
        raise LinkConflict("AI link suggestions are disabled for this source type in Internal Link Settings.")


_PROTECTED = {"a", "script", "style", "textarea", "title", "code", "pre", "button", "svg", "template",
              "h1", "h2", "h3", "h4", "h5", "h6"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class BodyParser(HTMLParser):
    """Locate eligible text and paragraph boundaries without reserializing HTML."""
    def __init__(self, body: str):
        super().__init__(convert_charrefs=False)
        self.body = body
        self.lines = [0]
        self.lines.extend(m.end() for m in re.finditer("\n", body))
        self.stack: list[str] = []
        self.text_spans: list[tuple[int, str]] = []
        self.paragraphs: list[tuple[int, int]] = []
        self.paragraph_start: int | None = None
        self.feed(body)
        self.close()

    def source_offset(self) -> int:
        line, col = self.getpos()
        return self.lines[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag == "p" and not any(t in _PROTECTED for t in self.stack):
            self.paragraph_start = self.source_offset()
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag == "p" and self.paragraph_start is not None and not any(t in _PROTECTED for t in self.stack):
            end = self.body.find(">", self.source_offset()) + 1
            self.paragraphs.append((self.paragraph_start, end))
            self.paragraph_start = None
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

    def handle_data(self, data):
        if not any(t in _PROTECTED for t in self.stack):
            self.text_spans.append((self.source_offset(), data))


class _Text(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "template"):
            self.hidden += 1
        if tag in ("p", "div", "br", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "template"):
            self.hidden = max(0, self.hidden - 1)
        if tag in ("p", "div", "li"):
            self.parts.append(" ")

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


def visible_text(body: str) -> str:
    return " ".join("".join(_Text(body).parts).split())


def text_diff(old: str, new: str) -> str:
    return "\n".join(difflib.unified_diff(visible_text(old).splitlines(), visible_text(new).splitlines(),
                                        fromfile="Current text", tofile="Proposed text", lineterm=""))


def validate_edit(raw: dict) -> dict:
    """Validate and normalize an edit descriptor.
    
    Supports three modes:
    - phrase_wrap: just anchor_phrase, wrap existing text
    - insert_sentence: AI-generated sentence insertion after a paragraph
    - manual_append: manual mode with origin="manual", after_sentence, append_text
    """
    keys = {"anchor_phrase", "insert_sentence", "insert_after_text", "origin", "after_sentence", "append_text"}
    if not isinstance(raw, dict) or set(raw) - keys:
        raise LinkConflict("Full-body AI responses and unsupported edit fields are blocked. Generate a new suggestion.")
    # Drop empty string values first (AI path may send empty strings for unused fields)
    edit = {k: v.strip() for k, v in raw.items() if isinstance(v, str) and v.strip()}
    if any(v is not None and not isinstance(v, str) for v in raw.values()):
        raise LinkConflict("Link edits must contain plain text.")
    phrase = edit.get("anchor_phrase", "")
    if not phrase or len(phrase) > 120:
        raise LinkConflict("A short, exact anchor phrase is required.")
    if any("<" in v or ">" in v for v in edit.values()):
        raise LinkConflict("AI edits must contain plain text, never HTML.")
    
    # Check for manual mode
    is_manual = edit.get("origin") == "manual"
    has_after_sentence = bool(edit.get("after_sentence"))
    has_append_text = bool(edit.get("append_text"))
    has_insert_sentence = bool(edit.get("insert_sentence"))
    has_insert_after_text = bool(edit.get("insert_after_text"))
    
    if is_manual:
        # Manual mode: requires after_sentence and append_text, mutually exclusive with insert_sentence
        if has_insert_sentence or has_insert_after_text:
            raise LinkConflict("Manual mode cannot be combined with insert_sentence or insert_after_text.")
        if not has_after_sentence:
            raise LinkConflict("Manual mode requires after_sentence (the sentence to append to).")
        if not has_append_text:
            raise LinkConflict("Manual mode requires append_text (the text to add).")
        
        append_text = edit["append_text"]
        after_sentence = edit["after_sentence"]
        
        # C1: Reject control characters
        _CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
        if _CONTROL_CHAR_RE.search(append_text) or _CONTROL_CHAR_RE.search(after_sentence):
            raise LinkConflict("Control characters are not allowed in manual edit fields.")
        
        # Validate structural requirements (no newlines, anchor appears once)
        # Shape validation (300 chars, 2 sentences) is done in submit_manual_weave
        # so it can return 400 ManualWeaveRejected instead of 409 LinkConflict
        if "\n" in append_text:
            raise LinkConflict("Appended text must not contain newlines.")
        
        # Anchor must appear exactly once in append_text
        if append_text.count(phrase) != 1:
            raise LinkConflict("The anchor phrase must appear exactly once in the appended text.")
        
        return edit
    
    # Non-manual modes: after_sentence/append_text not allowed
    if has_after_sentence or has_append_text:
        raise LinkConflict("after_sentence and append_text require origin='manual'.")
    
    sentence = edit.get("insert_sentence")
    if sentence:
        if len(sentence) > 300 or "\n" in sentence or re.search(r"[.!?]\s+\S", sentence):
            raise LinkConflict("Only one short sentence may be added.")
        if sentence.count(phrase) != 1 or not edit.get("insert_after_text"):
            raise LinkConflict("The sentence must contain the exact anchor once and identify a paragraph.")
    elif edit.get("insert_after_text"):
        raise LinkConflict("A paragraph location requires an insertion sentence.")
    return edit


def _normalize_for_matching(text: str) -> str:
    """Normalize text for sentence matching: unescape HTML entities and normalize whitespace."""
    import html as html_mod
    text = html_mod.unescape(text)
    # Normalize various dashes, quotes, and special spaces
    text = text.replace("\u00a0", " ")  # nbsp
    text = text.replace("\u2019", "'")  # right single quote
    text = text.replace("\u2018", "'")  # left single quote
    text = text.replace("\u201c", '"')  # left double quote
    text = text.replace("\u201d", '"')  # right double quote
    text = text.replace("\u2014", "-")  # em dash
    text = text.replace("\u2013", "-")  # en dash
    return " ".join(text.split())


def _count_links_in_body(body: str) -> int:
    """Count links with non-empty href in body HTML."""
    from .graph import extract_links
    return len([href for href, _ in extract_links(body) if href.strip()])


def _find_sentence_in_body(body: str, sentence: str) -> list[tuple[int, int]]:
    """Find all occurrences of a sentence in the eligible visible text of a body.
    
    Returns list of (start_offset, end_offset) tuples in the source HTML.
    Matches are within eligible text spans (not in _PROTECTED tags).
    The match must sit on sentence boundaries: block start or [.!?] + whitespace
    before it, and a sentence terminator after it.
    """
    parser = BodyParser(body)
    sentence_norm = _normalize_for_matching(sentence)
    if not sentence_norm:
        return []
    
    matches: list[tuple[int, int]] = []
    
    # Build a map of normalized text to source offsets
    for span_offset, span_text in parser.text_spans:
        span_norm = _normalize_for_matching(span_text)
        
        # Try to find the sentence in this span
        search_pos = 0
        while True:
            idx = span_norm.find(sentence_norm, search_pos)
            if idx < 0:
                break
            
            # Check sentence boundaries
            # Before: must be start of span or preceded by [.!?] and whitespace
            before_ok = False
            if idx == 0:
                before_ok = True
            else:
                before_text = span_norm[:idx].rstrip()
                if before_text and before_text[-1] in ".!?":
                    before_ok = True
            
            # After: must end with a sentence terminator
            end_idx = idx + len(sentence_norm)
            after_ok = False
            if end_idx >= len(span_norm):
                after_ok = True
            else:
                # Check if followed by whitespace (sentence boundary)
                remainder = span_norm[end_idx:]
                if remainder and (remainder[0].isspace() or remainder[0] in ".!?"):
                    after_ok = True
            
            # Also check sentence ends with terminator
            if sentence_norm and sentence_norm[-1] in ".!?":
                after_ok = True
            
            if before_ok and after_ok:
                # Map back to source offsets - approximate by character ratio
                # This is a simplification; proper handling would track char-by-char
                ratio = len(span_text) / len(span_norm) if span_norm else 1
                approx_start = span_offset + int(idx * ratio)
                approx_end = span_offset + int(end_idx * ratio)
                matches.append((approx_start, approx_end))
            
            search_pos = idx + 1
    
    return matches


_MATCH_PROTECTED = {"script", "style", "textarea", "template", "code", "pre",
                    "h1", "h2", "h3", "h4", "h5", "h6"}

_INSERT_PROTECTED = _PROTECTED
_BLOCK_SEP = "\x00"
_BLOCK_TAGS = {"p", "div", "li", "ul", "ol", "br", "td", "th", "tr", "table", "blockquote", "section",
               "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "hr"}


def _normalize_decoded(text: str) -> str:
    """Like _normalize_for_matching but for already-decoded text (no second unescape)."""
    for a, b in (("\u00a0", " "), ("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"'), ("\u2014", "-"), ("\u2013", "-")):
        text = text.replace(a, b)
    return " ".join(text.split())


class _FullTextExtractor(HTMLParser):
    """Extract text from HTML for sentence matching.
    
    Creates a mapping from text positions to HTML positions.
    Excludes text inside match-protected elements (headings, script, etc.).
    Text inside links (<a>) IS included for matching but insert point is checked separately.
    Uses convert_charrefs=False to preserve raw HTML offsets.
    """
    
    def __init__(self, html_str: str):
        super().__init__(convert_charrefs=False)
        self.html = html_str
        self.text_parts: list[str] = []
        self.text_to_html_pos: list[int] = []
        self.protected_depth = 0
        self.tag_stack: list[str] = []
        self._lines = [0] + [m.end() for m in re.finditer("\n", html_str)]
        self.feed(html_str)
        self.close()
    
    def _source_offset(self) -> int:
        line, col = self.getpos()
        return self._lines[line - 1] + col
    
    def _block_sep(self):
        self.text_parts.append(_BLOCK_SEP)
        self.text_to_html_pos.append(self._source_offset() - 1)

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self._block_sep()
        if tag in _MATCH_PROTECTED:
            self.protected_depth += 1
        if tag not in _VOID:
            self.tag_stack.append(tag)
    
    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self._block_sep()
        if tag in self.tag_stack:
            idx = len(self.tag_stack) - 1 - self.tag_stack[::-1].index(tag)
            popped = self.tag_stack[idx:]
            self.tag_stack = self.tag_stack[:idx]
            for t in popped:
                if t in _MATCH_PROTECTED and self.protected_depth > 0:
                    self.protected_depth -= 1
    
    def handle_data(self, data):
        if self.protected_depth == 0:
            offset = self._source_offset()
            for i, char in enumerate(data):
                self.text_parts.append(char)
                self.text_to_html_pos.append(offset + i)
    
    def handle_entityref(self, name):
        if self.protected_depth == 0:
            offset = self._source_offset()
            raw_m = re.match(r"&" + re.escape(name) + r";?", self.html[offset:])
            raw = raw_m.group(0) if raw_m else "&" + name + ";"
            decoded = html.unescape(raw)
            if decoded == raw:  # unknown entity: keep literal chars at their raw offsets
                for k, ch in enumerate(raw):
                    self.text_parts.append(ch)
                    self.text_to_html_pos.append(offset + k)
            else:
                for ch in decoded:
                    self.text_parts.append(ch)
                    self.text_to_html_pos.append(offset + len(raw) - 1)
    
    def handle_charref(self, name):
        if self.protected_depth == 0:
            offset = self._source_offset()
            try:
                if name.startswith(("x", "X")):
                    char = chr(int(name[1:], 16))
                else:
                    char = chr(int(name))
                self.text_parts.append(char)
                entity_len = len(name) + 3
                self.text_to_html_pos.append(offset + entity_len - 1)
            except (ValueError, OverflowError):
                pass
    
    def get_text(self) -> str:
        return "".join(self.text_parts)
    
    def html_pos_for_text_pos(self, text_pos: int) -> int:
        """Get the HTML position after the character at text_pos."""
        if text_pos < 0 or text_pos >= len(self.text_to_html_pos):
            return len(self.html)
        return self.text_to_html_pos[text_pos] + 1


def _is_inside_protected(html_str: str, offset: int) -> bool:
    """Check if offset is inside a _INSERT_PROTECTED element (links, headings, etc.)."""
    class _ProtectedChecker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.protected_depth = 0
            self.tag_stack = []
            self.result = False
            self.target = offset
            self._lines = [0] + [m.end() for m in re.finditer("\n", html_str)]
        
        def _pos(self):
            line, col = self.getpos()
            return self._lines[line - 1] + col
        
        def handle_starttag(self, tag, attrs):
            if tag in _INSERT_PROTECTED:
                self.protected_depth += 1
            if tag not in _VOID:
                self.tag_stack.append(tag)
        
        def handle_endtag(self, tag):
            if tag in self.tag_stack:
                idx = len(self.tag_stack) - 1 - self.tag_stack[::-1].index(tag)
                popped = self.tag_stack[idx:]
                self.tag_stack = self.tag_stack[:idx]
                for t in popped:
                    if t in _INSERT_PROTECTED and self.protected_depth > 0:
                        self.protected_depth -= 1
        
        def handle_data(self, data):
            start = self._pos()
            end = start + len(data)
            if start <= self.target < end and self.protected_depth > 0:
                self.result = True
    
    checker = _ProtectedChecker()
    try:
        checker.feed(html_str)
    except Exception:
        pass
    return checker.result


def _find_sentence_end_in_html(html_str: str, sentence: str) -> int | None:
    """Find the HTML offset where a sentence ends, excluding _PROTECTED text.
    
    Returns:
        int: HTML offset to insert after (right after the sentence's period)
        None: If sentence not found or only found in protected elements
        -1: If sentence appears multiple times (ambiguous)
        -2: If insert point would be inside a protected element
    """
    extractor = _FullTextExtractor(html_str)
    full_text = extractor.get_text()
    
    sentence_norm = _normalize_for_matching(sentence)
    text_norm = _normalize_decoded(full_text)
    
    if not sentence_norm:
        return None
    
    matches: list[int] = []
    search_start = 0
    
    while True:
        pos = text_norm.find(sentence_norm, search_start)
        if pos < 0:
            break
        
        end_pos = pos + len(sentence_norm)
        
        before_ok = False
        if pos == 0:
            before_ok = True
        else:
            before_text = text_norm[:pos].rstrip(" ")
            if before_text and before_text[-1] == _BLOCK_SEP:
                before_ok = True
            elif before_text and before_text[-1] in ".!?" and pos > 0 and text_norm[pos - 1] == " ":
                before_ok = True
        nxt = text_norm[end_pos] if end_pos < len(text_norm) else ""
        after_ok = bool(sentence_norm) and sentence_norm[-1] in ".!?" and (nxt in ("", " ", _BLOCK_SEP))
        
        if before_ok and after_ok:
            norm_end = end_pos - 1
            text_char_idx = _map_norm_to_text_pos(full_text, norm_end)
            if text_char_idx is not None:
                html_pos = extractor.html_pos_for_text_pos(text_char_idx)
                if not _is_inside_protected(html_str, html_pos - 1):
                    matches.append(html_pos)
        
        search_start = pos + 1
    
    if len(matches) == 0:
        return None
    if len(matches) > 1:
        return -1
    return matches[0]


def _map_norm_to_text_pos(full_text: str, norm_pos: int) -> int | None:
    """Map a position in normalized text back to position in original full text.
    
    Accounts for whitespace collapsing and dash normalization.
    """
    if not full_text:
        return None
    
    normalized = _normalize_decoded(full_text)
    if norm_pos < 0 or norm_pos >= len(normalized):
        return None
    
    norm_idx = 0
    in_whitespace = True  # leading whitespace is stripped by " ".join(split())
    
    for text_idx, char in enumerate(full_text):
        if char.isspace() or char in "\u00a0":
            if not in_whitespace:
                if norm_idx == norm_pos:
                    return text_idx
                in_whitespace = True
                norm_idx += 1
        else:
            in_whitespace = False
            norm_char = char
            if char == "\u2014" or char == "\u2013":
                norm_char = "-"
            elif char == "\u2019" or char == "\u2018":
                norm_char = "'"
            elif char == "\u201c" or char == "\u201d":
                norm_char = '"'
            
            if norm_idx == norm_pos:
                return text_idx
            norm_idx += 1
    
    if norm_idx == norm_pos + 1:
        return len(full_text) - 1
    
    return None


def build_edit(old: str, raw: dict, url: str) -> str:
    """Build the new HTML body with the link inserted.
    
    Supports three modes:
    - phrase_wrap: wrap existing phrase with a link
    - insert_sentence: insert a new paragraph after a matched paragraph
    - manual_append: append text after an existing sentence
    """
    edit = validate_edit(raw)
    phrase = edit["anchor_phrase"]
    parser = BodyParser(old)
    href = html.escape(url, quote=True)
    
    # Manual append mode
    if edit.get("origin") == "manual":
        after_sentence = edit["after_sentence"]
        append_text = edit["append_text"]
        
        # Check 8-link cap
        existing_links = _count_links_in_body(old)
        if existing_links + 1 > MANUAL_LINK_CAP:
            raise LinkConflict(
                f"Link cap reached: {existing_links} existing links (max {MANUAL_LINK_CAP} including this one)."
            )
        
        # Find the sentence in the body, excluding _PROTECTED elements
        # Use _find_sentence_end_in_html which handles sentences spanning links
        insert_offset = _find_sentence_end_in_html(old, after_sentence)
        
        if insert_offset is None:
            raise LinkConflict("Sentence not found in eligible body text. It may be inside a heading or link, or the page changed (drift).")
        if insert_offset == -1:
            raise LinkConflict("Sentence appears multiple times in the body (ambiguous). Use a more specific sentence.")
        if insert_offset == -2:
            raise LinkConflict("Insert point would be inside a heading or link. Choose a different sentence.")
        
        # Build the insertion: space + append_text with anchor linked
        linked_text = html.escape(append_text).replace(
            html.escape(phrase),
            f'<a href="{href}">{html.escape(phrase)}</a>',
            1
        )
        insertion = " " + linked_text
        
        return old[:insert_offset] + insertion + old[insert_offset:]
    
    # AI insert_sentence mode
    sentence = edit.get("insert_sentence")
    if sentence:
        raw_locator = edit["insert_after_text"]
        # Strip trailing ellipsis from the locator (Q4 default: yes)
        locator_text = raw_locator.rstrip()
        if locator_text.endswith("…"):
            locator_text = locator_text[:-1].rstrip()
        elif locator_text.endswith("..."):
            locator_text = locator_text[:-3].rstrip()
        
        # Normalize the locator for matching
        loc_norm = _normalize_for_matching(locator_text)
        
        # Find matching paragraphs using normalized comparison
        exact_matches = []
        prefix_matches = []
        for start, end in parser.paragraphs:
            p_norm = _normalize_for_matching(visible_text(old[start:end]))
            if p_norm == loc_norm:
                exact_matches.append(end)
            elif len(loc_norm) >= 40 and p_norm.startswith(loc_norm):
                prefix_matches.append(end)
        
        # Use exact matches if any, otherwise fall back to prefix matches
        matches = exact_matches if exact_matches else prefix_matches
        
        # Truncate locator for error messages (first 300 chars)
        locator_preview = raw_locator[:300] if len(raw_locator) > 300 else raw_locator
        
        if len(matches) == 0:
            raise LinkConflict(
                "The insertion paragraph was not found in the live body. Generate a new suggestion.",
                code="insert_locator_no_match",
                extra={"insert_after_text": locator_preview}
            )
        if len(matches) > 1:
            raise LinkConflict(
                f"The insertion paragraph is ambiguous ({len(matches)} paragraphs match). Generate a new suggestion.",
                code="insert_locator_ambiguous",
                extra={"insert_after_text": locator_preview, "match_count": len(matches)}
            )
        
        linked = html.escape(sentence).replace(html.escape(phrase), f'<a href="{href}">{html.escape(phrase)}</a>', 1)
        offset = matches[0]
        # Match the body's block separator style: if it uses newlines between blocks, preserve that
        if "</p>\n<p>" in old or (offset < len(old) and old[offset:offset + 1] == "\n"):
            return old[:offset] + "\n<p>" + linked + "</p>" + old[offset:]
        return old[:offset] + "<p>" + linked + "</p>" + old[offset:]
    
    # phrase_wrap mode
    pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)
    for offset, text in parser.text_spans:
        match = pattern.search(text)
        if match:
            start, end = offset + match.start(), offset + match.end()
            return old[:start] + f'<a href="{href}">' + old[start:end] + "</a>" + old[end:]
    raise LinkConflict("The anchor phrase is no longer present in eligible live text. Generate a new suggestion.")


def guard_edit(old: str, new: str, edit: dict, url: str) -> None:
    # Exact reconstruction protects images, existing links, attributes and formatting,
    # including changes that would be invisible in a text-only comparison.
    if new != build_edit(old, edit, url):
        raise LinkConflict("Changes outside the approved link insertion are blocked.", text_diff=text_diff(old, new))


def preview_token(binding: dict) -> str:
    payload = json.dumps({**binding, "expires": int(time.time()) + PREVIEW_TTL}, sort_keys=True).encode()
    encoded = base64.urlsafe_b64encode(payload).decode()
    signature = hmac.new(_SECRET, encoded.encode(), hashlib.sha256).hexdigest()
    return encoded + "." + signature


def verify_token(token: str, binding: dict) -> None:
    try:
        encoded, signature = token.split(".")
        if not hmac.compare_digest(signature, hmac.new(_SECRET, encoded.encode(), hashlib.sha256).hexdigest()):
            raise ValueError()
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        if payload.pop("expires") < time.time() or payload != binding:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise LinkConflict("Preview expired or content changed. Open a fresh preview before confirming.") from None
