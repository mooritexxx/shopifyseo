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

# HTML ASCII whitespace: space, tab, newline, carriage return, form feed
_HTML_WHITESPACE = frozenset(" \t\n\r\f")


def _is_html_whitespace(char: str) -> bool:
    """Check if character is HTML ASCII whitespace (space, tab, LF, CR, FF)."""
    return char in _HTML_WHITESPACE


def _strip_html_whitespace(s: str) -> str:
    """Strip leading/trailing HTML ASCII whitespace only."""
    start = 0
    end = len(s)
    while start < end and s[start] in _HTML_WHITESPACE:
        start += 1
    while end > start and s[end - 1] in _HTML_WHITESPACE:
        end -= 1
    return s[start:end]


# Regex to match an HTML tag, handling quoted attribute values
# Matches: < followed by optional /, tag name, attributes (with quoted values), optional /, >
_TAG_RE = re.compile(r'<[^>"\']*(?:"[^"]*"|\'[^\']*\')*[^>]*>', re.DOTALL)


def _extract_tag_name(tag_content: str) -> str:
    """Extract lowercase tag name from tag content (between < and >).
    
    Uses exact tag name matching (alphanumeric only after first letter).
    """
    is_closing = tag_content.startswith('/')
    tag_part = tag_content[1:].lstrip() if is_closing else tag_content.lstrip()
    # Match only standard HTML tag names: letter followed by letters or digits
    # This excludes custom elements like <p-x>
    match = re.match(r'^([a-zA-Z][a-zA-Z0-9]*)(?:\s|/|$)', tag_part)
    return match.group(1).lower() if match else ""


def _find_tag_end(html: str, start: int) -> int:
    """Find the end position of a tag starting at start, handling quoted attributes.
    
    Returns the position of the closing > or -1 if malformed.
    """
    i = start + 1  # Skip the opening <
    in_single_quote = False
    in_double_quote = False
    
    while i < len(html):
        char = html[i]
        if in_single_quote:
            if char == "'":
                in_single_quote = False
        elif in_double_quote:
            if char == '"':
                in_double_quote = False
        elif char == "'":
            in_single_quote = True
        elif char == '"':
            in_double_quote = True
        elif char == '>':
            return i
        i += 1
    
    return -1  # Malformed - no closing >


def _strip_inter_block_whitespace(html: str) -> str:
    """Remove ASCII whitespace-only runs between block-level tags.
    
    Only HTML ASCII whitespace (space, tab, LF, CR, FF) is stripped.
    NBSP (U+00A0), ideographic space, and other Unicode whitespace are significant.
    
    Preserves whitespace:
    - Inside <pre>, <textarea>, <script>, <style>
    - Inside text nodes
    - Inside tags/attributes
    - Between inline tags (e.g., </a> <strong>)
    
    Also strips leading/trailing ASCII whitespace of the whole document (Q1 default).
    """
    if not html:
        return ""
    
    # Parse tokens: tags and text segments
    tokens: list[tuple[str, str]] = []  # ('tag'|'text', content)
    i = 0
    
    while i < len(html):
        if html[i] == '<':
            tag_end = _find_tag_end(html, i)
            if tag_end == -1:
                # Malformed: treat rest as text
                tokens.append(('text', html[i:]))
                break
            tokens.append(('tag', html[i:tag_end + 1]))
            i = tag_end + 1
        else:
            # Find next tag or end
            next_tag = html.find('<', i)
            if next_tag == -1:
                tokens.append(('text', html[i:]))
                break
            tokens.append(('text', html[i:next_tag]))
            i = next_tag
    
    # Now process tokens: remove ASCII whitespace between block-level tags
    # Track preformatted depth
    output: list[str] = []
    pre_depth = 0
    
    for idx, (token_type, content) in enumerate(tokens):
        if token_type == 'tag':
            tag_content = content[1:-1]  # Remove < and >
            tag_name = _extract_tag_name(tag_content)
            is_closing = tag_content.startswith('/')
            is_self_closing = tag_content.rstrip().endswith('/')
            
            if tag_name in _PREFORMATTED_TAGS:
                if is_closing:
                    pre_depth = max(0, pre_depth - 1)
                elif not is_self_closing:
                    pre_depth += 1
            
            output.append(content)
        else:
            # Text content
            if pre_depth > 0:
                # Inside preformatted - keep as-is
                output.append(content)
            else:
                # Check if this is ASCII whitespace-only between block tags
                is_ascii_ws_only = all(_is_html_whitespace(c) for c in content) and len(content) > 0
                
                if is_ascii_ws_only:
                    # Check preceding and following tags
                    prev_tag_name = ""
                    next_tag_name = ""
                    
                    # Find previous tag
                    for j in range(idx - 1, -1, -1):
                        if tokens[j][0] == 'tag':
                            prev_tag_name = _extract_tag_name(tokens[j][1][1:-1])
                            break
                    
                    # Find next tag
                    for j in range(idx + 1, len(tokens)):
                        if tokens[j][0] == 'tag':
                            next_tag_name = _extract_tag_name(tokens[j][1][1:-1])
                            break
                    
                    # Only strip if both are block-level tags
                    if prev_tag_name in _BLOCK_LEVEL_TAGS and next_tag_name in _BLOCK_LEVEL_TAGS:
                        # Skip this whitespace
                        continue
                
                output.append(content)
    
    # Strip leading/trailing ASCII whitespace of the whole document (Q1 default)
    return _strip_html_whitespace("".join(output))


def _normalize_text_entities(text: str) -> str:
    """Normalize entity-encoded characters in text content to their plain equivalents.
    
    Uses html.unescape to handle all standard HTML entities (Q&amp;A → Q&A, R&amp;D → R&D,
    AT&amp;T → AT&T), with special handling to:
    
    1. Keep &nbsp; as a sentinel (significant per #117 rule)
    2. Keep &amp;lt; distinct from &lt; (escaped entity ≠ real entity)
    3. Keep &#x39; distinct from ' (hex 39 = digit '9', not apostrophe)
    
    The approach:
    - Replace &nbsp; with a sentinel before unescape, restore after
    - Replace double-escaped entities (&amp;lt; etc.) with sentinels, restore after
    - Use html.unescape for everything else
    - Handle &#x39; specially (it's '9', not apostrophe)
    """
    if not text:
        return text
    
    # Sentinel for &nbsp; - use a character unlikely to appear in content
    NBSP_SENTINEL = "\x00NBSP\x00"
    # Sentinels for double-escaped entities (they should NOT cascade)
    AMP_LT_SENTINEL = "\x00AMPLT\x00"
    AMP_GT_SENTINEL = "\x00AMPGT\x00"
    AMP_AMP_SENTINEL = "\x00AMPAMP\x00"
    AMP_HASH_SENTINEL = "\x00AMPHASH\x00"
    AMP_NBSP_SENTINEL = "\x00AMPNBSP\x00"  # &amp;nbsp; should stay distinct from &nbsp;
    X39_SENTINEL = "\x00X39\x00"  # &#x39; is hex 39 = '9', not apostrophe
    # Sentinels for structural HTML entities (keep them as-is)
    LT_SENTINEL = "\x00LT\x00"    # &lt; represents literal '<' in text
    GT_SENTINEL = "\x00GT\x00"    # &gt; represents literal '>' in text
    
    # Preserve double-escaped entities FIRST (they should NOT cascade)
    # Must do this before preserving &nbsp; so &amp;nbsp; doesn't partially match
    text = re.sub(r'&amp;lt;', AMP_LT_SENTINEL, text, flags=re.IGNORECASE)
    text = re.sub(r'&amp;gt;', AMP_GT_SENTINEL, text, flags=re.IGNORECASE)
    text = re.sub(r'&amp;amp;', AMP_AMP_SENTINEL, text, flags=re.IGNORECASE)
    text = re.sub(r'&amp;nbsp;', AMP_NBSP_SENTINEL, text, flags=re.IGNORECASE)
    text = re.sub(r'&amp;#', AMP_HASH_SENTINEL, text, flags=re.IGNORECASE)
    
    # Preserve &lt; and &gt; - these represent literal < and > in text content
    # Converting them would change HTML structure (text vs element)
    text = re.sub(r'&lt;', LT_SENTINEL, text, flags=re.IGNORECASE)
    text = re.sub(r'&gt;', GT_SENTINEL, text, flags=re.IGNORECASE)
    
    # Preserve &nbsp; (keep it significant)
    text = text.replace('&nbsp;', NBSP_SENTINEL)
    
    # Preserve &#x39; (hex 39 = decimal 57 = digit '9', NOT apostrophe)
    text = re.sub(r'&#x39;', X39_SENTINEL, text, flags=re.IGNORECASE)
    
    # Also handle &#0039; as apostrophe (leading zeros)
    text = re.sub(r'&#0+39;', "'", text, flags=re.IGNORECASE)
    
    # Now unescape everything else - this handles Q&amp;A → Q&A, &#x27; → ', etc.
    text = html.unescape(text)
    
    # Restore sentinels
    text = text.replace(NBSP_SENTINEL, '&nbsp;')
    text = text.replace(AMP_LT_SENTINEL, '&amp;lt;')
    text = text.replace(AMP_GT_SENTINEL, '&amp;gt;')
    text = text.replace(AMP_AMP_SENTINEL, '&amp;amp;')
    text = text.replace(AMP_NBSP_SENTINEL, '&amp;nbsp;')
    text = text.replace(AMP_HASH_SENTINEL, '&amp;#')
    text = text.replace(LT_SENTINEL, '&lt;')
    text = text.replace(GT_SENTINEL, '&gt;')
    text = text.replace(X39_SENTINEL, '&#x39;')
    
    return text


def _normalize_for_entity_comparison(html_str: str) -> str:
    """Normalize HTML for entity-tolerant comparison.
    
    Applies _normalize_text_entities to text nodes only (not inside tags/attributes).
    """
    if not html_str:
        return ""
    
    result: list[str] = []
    i = 0
    
    while i < len(html_str):
        if html_str[i] == '<':
            # Find end of tag
            tag_end = _find_tag_end(html_str, i)
            if tag_end == -1:
                # Malformed: treat rest as text
                result.append(_normalize_text_entities(html_str[i:]))
                break
            # Preserve tag as-is (including attribute entities)
            result.append(html_str[i:tag_end + 1])
            i = tag_end + 1
        else:
            # Find next tag
            next_tag = html_str.find('<', i)
            if next_tag == -1:
                # Rest is text
                result.append(_normalize_text_entities(html_str[i:]))
                break
            # Text before next tag - normalize entities
            result.append(_normalize_text_entities(html_str[i:next_tag]))
            i = next_tag
    
    return "".join(result)


def html_equivalent(a: str, b: str) -> bool:
    """Check if two HTML strings are equivalent, tolerating inter-block whitespace
    and text-node entity encoding differences.
    
    Returns True when the strings are identical after:
    - Removing whitespace-only runs between two block-level tags
    - Stripping leading/trailing document whitespace
    - Normalizing text-node entity encoding (&#x27;/' and &quot;/" treated as equal)
    
    Block-level tags: p, div, h1-h6, ul, ol, li, table, thead, tbody, tfoot,
    tr, td, th, blockquote, section, article, header, footer, aside, nav,
    figure, figcaption, hr, dl, dt, dd.
    
    Does NOT collapse whitespace:
    - Between inline tags (visible space like "</a> <strong>")
    - Inside <pre>, <textarea>, <script>, <style>
    - Inside text nodes
    - Inside tags/attributes
    
    NBSP (&nbsp; / \xa0) differences remain significant per #117.
    Tag, attribute, and structural differences fail.
    """
    if a == b:
        return True
    # First try whitespace normalization only
    a_ws = _strip_inter_block_whitespace(a)
    b_ws = _strip_inter_block_whitespace(b)
    if a_ws == b_ws:
        return True
    # Then try entity normalization on the whitespace-normalized versions
    return _normalize_for_entity_comparison(a_ws) == _normalize_for_entity_comparison(b_ws)


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

# Regex to detect escaped anchor start: &lt;a or numeric equivalents (&#60; &#x3c;)
# followed by whitespace or &gt;/&#62;/&#x3e;
_ESCAPED_ANCHOR_START_RE = re.compile(
    r'(?:&lt;|&#60;|&#x3[cC];)a(?:\s|&gt;|&#62;|&#x3[eE];)',
    re.IGNORECASE
)
# Regex to detect escaped anchor end: &lt;/a&gt; or numeric equivalents
_ESCAPED_ANCHOR_END_RE = re.compile(
    r'(?:&lt;|&#60;|&#x3[cC];)/a(?:&gt;|&#62;|&#x3[eE];)',
    re.IGNORECASE
)


def _find_escaped_anchor_regions(text: str) -> list[tuple[int, int]]:
    """Find regions of escaped anchor markup in text: &lt;a ...&gt;...&lt;/a&gt;.
    
    Returns list of (start, end) tuples marking escaped anchor regions.
    These regions should be protected from phrase wrapping.
    
    Limits pairing to the same block element: if a block-level tag (e.g., </p>, <p>)
    appears between the start and end, the region is not formed. This prevents an
    unclosed &lt;a in paragraph 1 from incorrectly protecting content in paragraph 3.
    """
    regions: list[tuple[int, int]] = []
    
    # Block-level tag pattern (opening or closing)
    block_tag_re = re.compile(r'</?(?:p|div|h[1-6]|ul|ol|li|table|tr|td|th|blockquote|section|article|header|footer)[\s>]', re.IGNORECASE)
    
    # Find all escaped anchor starts
    starts = [(m.start(), m.group()) for m in _ESCAPED_ANCHOR_START_RE.finditer(text)]
    if not starts:
        return regions
    
    # For each start, find the matching end within the same block
    for start_pos, _ in starts:
        # Find the closing &lt;/a&gt; after this start
        end_match = _ESCAPED_ANCHOR_END_RE.search(text, start_pos)
        if end_match:
            # Check if there's a block-level tag between start and end
            between = text[start_pos:end_match.start()]
            if not block_tag_re.search(between):
                # No block boundary crossed - valid region
                regions.append((start_pos, end_match.end()))
    
    return regions


def _is_inside_escaped_anchor(text: str, offset: int) -> bool:
    """Check if offset is inside an escaped anchor region (&lt;a ...&gt;...&lt;/a&gt;)."""
    for start, end in _find_escaped_anchor_regions(text):
        if start <= offset < end:
            return True
    return False


def _is_inside_real_anchor(html_str: str, offset: int) -> bool:
    """Check if offset is inside a real <a> tag (not escaped).
    
    Parses the HTML and tracks anchor depth. Returns True if the given offset
    is inside an open anchor (depth > 0). Handles unclosed anchors that extend
    to end of document.
    """
    class _AnchorDepthAtOffset(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.anchor_depth = 0
            self.depth_at_offset: int | None = None
            self.target_offset = offset
            self._lines = [0]
        
        def feed(self, data):
            self._lines = [0] + [m.end() for m in re.finditer("\n", data)]
            super().feed(data)
        
        def _current_offset(self):
            line, col = self.getpos()
            return self._lines[line - 1] + col
        
        def handle_starttag(self, tag, attrs):
            current = self._current_offset()
            if self.depth_at_offset is None and current >= self.target_offset:
                self.depth_at_offset = self.anchor_depth
            if tag == 'a':
                self.anchor_depth += 1
        
        def handle_endtag(self, tag):
            current = self._current_offset()
            if self.depth_at_offset is None and current >= self.target_offset:
                self.depth_at_offset = self.anchor_depth
            if tag == 'a':
                self.anchor_depth = max(0, self.anchor_depth - 1)
        
        def handle_data(self, data):
            current = self._current_offset()
            end = current + len(data)
            if self.depth_at_offset is None and current <= self.target_offset < end:
                self.depth_at_offset = self.anchor_depth
    
    checker = _AnchorDepthAtOffset()
    try:
        checker.feed(html_str)
    except Exception:
        pass
    
    # If we never reached the offset, use final depth (for unclosed anchors)
    depth = checker.depth_at_offset if checker.depth_at_offset is not None else checker.anchor_depth
    return depth > 0


def _find_inserted_anchor_offset(old: str, new: str) -> int | None:
    """Find the offset in old where our new anchor was inserted.
    
    Compares old and new to find where the new <a href="..."> was added.
    Returns the offset in `old` where content was inserted, or None if not found.
    """
    # Find the first difference between old and new
    min_len = min(len(old), len(new))
    diff_start = 0
    for i in range(min_len):
        if old[i] != new[i]:
            diff_start = i
            break
    else:
        diff_start = min_len
    
    # The insertion point in old is at diff_start
    return diff_start if diff_start < len(new) else None


def _verify_anchor_wellformed(html_str: str, anchor_start: int) -> None:
    """Verify the inserted anchor at anchor_start is well-formed.
    
    Raises LinkConflict if:
    - The anchor contains other tags (e.g., <a>...<strong>...</a>)
    - The anchor is nested inside another anchor
    - The anchor is not properly closed before another anchor opens
    """
    class _AnchorChecker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.in_our_anchor = False
            self.our_anchor_depth = 0
            self.found_other_tag = False
            self.found_nested_anchor = False
            self.anchor_depth = 0
            self.target_pos = anchor_start
            self._lines = [0]
            self.our_anchor_found = False
        
        def feed(self, data):
            self._lines = [0] + [m.end() for m in re.finditer("\n", data)]
            super().feed(data)
        
        def _pos(self):
            line, col = self.getpos()
            return self._lines[line - 1] + col
        
        def handle_starttag(self, tag, attrs):
            pos = self._pos()
            if tag == 'a':
                if pos == self.target_pos:
                    # This is our inserted anchor
                    self.in_our_anchor = True
                    self.our_anchor_found = True
                    if self.anchor_depth > 0:
                        # We're inside an existing anchor - bad!
                        self.found_nested_anchor = True
                self.anchor_depth += 1
                if self.in_our_anchor and self.our_anchor_depth > 0:
                    # Another <a> inside our anchor - bad!
                    self.found_nested_anchor = True
                if self.in_our_anchor:
                    self.our_anchor_depth += 1
            elif self.in_our_anchor and tag not in ('br',):
                # Found a non-anchor tag inside our anchor
                self.found_other_tag = True
        
        def handle_endtag(self, tag):
            if tag == 'a':
                self.anchor_depth -= 1
                if self.in_our_anchor:
                    self.our_anchor_depth -= 1
                    if self.our_anchor_depth == 0:
                        self.in_our_anchor = False
    
    checker = _AnchorChecker()
    try:
        checker.feed(html_str)
    except Exception:
        raise LinkConflict("Malformed HTML after link insertion.", code="malformed_anchor")
    
    if not checker.our_anchor_found:
        raise LinkConflict("Could not find inserted anchor at expected position.", code="anchor_not_found")
    
    if checker.found_nested_anchor:
        raise LinkConflict(
            "Link insertion would create nested anchors. Generate a new suggestion.",
            code="nested_anchor_created"
        )
    
    if checker.found_other_tag:
        raise LinkConflict(
            "Link would span across other tags. Generate a new suggestion.",
            code="anchor_spans_tags"
        )


def _has_nested_anchors(html_str: str) -> bool:
    """Check if HTML contains nested <a> tags (real nesting, not escaped).
    
    Returns True if any <a> tag opens while another <a> is already open.
    """
    class _AnchorNestChecker(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.anchor_depth = 0
            self.has_nesting = False
        
        def handle_starttag(self, tag, attrs):
            if tag == 'a':
                if self.anchor_depth > 0:
                    self.has_nesting = True
                self.anchor_depth += 1
        
        def handle_endtag(self, tag):
            if tag == 'a':
                self.anchor_depth = max(0, self.anchor_depth - 1)
    
    checker = _AnchorNestChecker()
    try:
        checker.feed(html_str)
    except Exception:
        pass
    return checker.has_nesting


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

    def handle_entityref(self, name):
        if not any(t in _PROTECTED for t in self.stack):
            # Reconstruct the entity reference as it appears in the source
            # source_offset points to the '&', and the entity name follows
            offset = self.source_offset()
            # Check if there's a semicolon after the entity name
            end_pos = offset + 1 + len(name)  # &name
            if end_pos < len(self.body) and self.body[end_pos] == ';':
                self.text_spans.append((offset, f"&{name};"))
            else:
                self.text_spans.append((offset, f"&{name}"))

    def handle_charref(self, name):
        if not any(t in _PROTECTED for t in self.stack):
            # Character references have the form &#digits; or &#xhex;
            # The 'name' includes 'x' for hex refs (e.g., name='x27' for &#x27;)
            # So prefix is always '&#' (2 chars), and name contains the rest
            offset = self.source_offset()
            # Check if there's a semicolon
            prefix_len = 2  # Always '&#', the 'x' is part of name for hex refs
            end_pos = offset + prefix_len + len(name)
            if end_pos < len(self.body) and self.body[end_pos] == ';':
                self.text_spans.append((offset, f"&#{name};"))
            else:
                self.text_spans.append((offset, f"&#{name}"))


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
        
        # Check if insertion point is inside an escaped anchor region
        if _is_inside_escaped_anchor(old, insert_offset):
            raise LinkConflict(
                "The insertion point is inside an escaped anchor region (&lt;a&gt;...&lt;/a&gt;). "
                "Choose a different sentence or fix the source content.",
                code="insert_inside_existing_anchor"
            )
        
        # Build the insertion: space + append_text with anchor linked
        # Use quote=False: apostrophes and quotes in text nodes don't need escaping
        # (Shopify stores them as plain characters; escaping causes verify mismatches)
        linked_text = html.escape(append_text, quote=False).replace(
            html.escape(phrase, quote=False),
            f'<a href="{href}">{html.escape(phrase, quote=False)}</a>',
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
        
        # S2: Empty or punctuation-only locator must reject (never match spacer paragraphs)
        # Check if loc_norm contains any alphanumeric characters
        if not loc_norm or not any(c.isalnum() for c in loc_norm):
            locator_preview = raw_locator[:300] if len(raw_locator) > 300 else raw_locator
            raise LinkConflict(
                "The insertion paragraph locator is empty or contains only punctuation. Generate a new suggestion.",
                code="insert_locator_no_match",
                extra={"insert_after_text": locator_preview}
            )
        
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
        
        # Use quote=False: apostrophes and quotes in text nodes don't need escaping
        # (Shopify stores them as plain characters; escaping causes verify mismatches)
        linked = html.escape(sentence, quote=False).replace(html.escape(phrase, quote=False), f'<a href="{href}">{html.escape(phrase, quote=False)}</a>', 1)
        offset = matches[0]
        # Match the body's block separator style: if it uses newlines between blocks, preserve that
        if "</p>\n<p>" in old or (offset < len(old) and old[offset:offset + 1] == "\n"):
            return old[:offset] + "\n<p>" + linked + "</p>" + old[offset:]
        return old[:offset] + "<p>" + linked + "</p>" + old[offset:]
    
    # phrase_wrap mode: find first safe occurrence (not inside an escaped anchor)
    # CRITICAL: Match must be entirely within ONE contiguous text run.
    # Entity refs are allowed within a text run, but tags are not.
    # Group contiguous spans (no gap between end of one and start of next).
    
    # Build text runs: groups of spans with no gaps between them
    text_runs: list[list[tuple[int, str]]] = []
    current_run: list[tuple[int, str]] = []
    prev_end = -1
    
    for html_offset, span_text in parser.text_spans:
        if prev_end != -1 and html_offset != prev_end:
            # Gap detected - start new run
            if current_run:
                text_runs.append(current_run)
            current_run = []
        current_run.append((html_offset, span_text))
        prev_end = html_offset + len(span_text)
    
    if current_run:
        text_runs.append(current_run)
    
    pattern = re.compile(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", re.IGNORECASE)
    found_inside_escaped_anchor = False
    found_inside_real_anchor = False
    
    for run in text_runs:
        # Build combined text for this run only
        combined_text = ""
        combined_to_html: list[int] = []
        for html_offset, span_text in run:
            for i, ch in enumerate(span_text):
                combined_text += ch
                combined_to_html.append(html_offset + i)
        
        # Unescape for matching (Q&amp;A → Q&A)
        unescaped_text = ""
        unescaped_to_combined: list[int] = []
        i = 0
        while i < len(combined_text):
            if combined_text[i] == '&':
                j = i + 1
                while j < len(combined_text) and combined_text[j] not in ';&< \t\n':
                    j += 1
                if j < len(combined_text) and combined_text[j] == ';':
                    j += 1
                entity = combined_text[i:j]
                decoded = html.unescape(entity)
                for ch in decoded:
                    unescaped_text += ch
                    unescaped_to_combined.append(i)
                i = j
            else:
                unescaped_text += combined_text[i]
                unescaped_to_combined.append(i)
                i += 1
        
        # Search within this run
        search_start = 0
        while True:
            match = pattern.search(unescaped_text, search_start)
            if not match:
                break
            
            # Map positions back to HTML
            combined_start = unescaped_to_combined[match.start()] if match.start() < len(unescaped_to_combined) else 0
            abs_match_start = combined_to_html[combined_start] if combined_start < len(combined_to_html) else 0
            
            last_unescaped_idx = match.end() - 1
            combined_end = unescaped_to_combined[last_unescaped_idx] if last_unescaped_idx < len(unescaped_to_combined) else len(combined_text) - 1
            
            # Find end of the entity/char at match end
            j = combined_end
            if j < len(combined_text) and combined_text[j] == '&':
                j += 1
                while j < len(combined_text) and combined_text[j] not in ';&< \t\n':
                    j += 1
                if j < len(combined_text) and combined_text[j] == ';':
                    j += 1
            else:
                j += 1
            abs_match_end = combined_to_html[j - 1] + 1 if j > 0 and j - 1 < len(combined_to_html) else len(old)
            
            # Skip if inside escaped anchor
            if _is_inside_escaped_anchor(old, abs_match_start):
                found_inside_escaped_anchor = True
                search_start = match.start() + 1
                continue
            
            # Skip if inside real anchor
            if _is_inside_real_anchor(old, abs_match_start):
                found_inside_real_anchor = True
                search_start = match.start() + 1
                continue
            
            # Verify match doesn't start/end inside an entity
            # Check if abs_match_start is in the middle of an entity
            if abs_match_start > 0 and old[abs_match_start - 1] == '&':
                search_start = match.start() + 1
                continue
            # Check if we're inside an entity by looking backwards for '&' without ';'
            look_back = abs_match_start - 1
            while look_back >= 0 and old[look_back] not in ';&<> \t\n':
                look_back -= 1
            if look_back >= 0 and old[look_back] == '&':
                search_start = match.start() + 1
                continue
            
            # Build the result
            result = old[:abs_match_start] + f'<a href="{href}">' + old[abs_match_start:abs_match_end] + "</a>" + old[abs_match_end:]
            
            # Final guard: verify the new anchor is well-formed
            _verify_anchor_wellformed(result, abs_match_start)
            
            return result
    
    # If we found the phrase but only inside anchors, report that specific error
    if found_inside_real_anchor:
        raise LinkConflict(
            "The anchor phrase appears only inside existing links. "
            "No safe occurrence found. Generate a new suggestion.",
            code="insert_inside_existing_anchor"
        )
    if found_inside_escaped_anchor:
        raise LinkConflict(
            "The anchor phrase appears only inside escaped anchor regions (&lt;a&gt;...&lt;/a&gt;). "
            "No safe occurrence found. Generate a new suggestion or fix the source content.",
            code="insert_inside_existing_anchor"
        )
    raise LinkConflict("The anchor phrase is no longer present in eligible live text. Generate a new suggestion.")


def guard_edit(old: str, new: str, edit: dict, url: str) -> None:
    """Guard against unauthorized changes and structural problems in the edit result.
    
    Checks:
    1. Exact reconstruction: new must equal build_edit(old, edit, url)
    2. Insertion point check: verify our anchor wasn't inserted inside an existing
       anchor (real or escaped). Pre-existing nesting elsewhere is irrelevant.
    """
    # Exact reconstruction protects images, existing links, attributes and formatting,
    # including changes that would be invisible in a text-only comparison.
    if new != build_edit(old, edit, url):
        raise LinkConflict("Changes outside the approved link insertion are blocked.", text_diff=text_diff(old, new))
    
    # Check if our insertion point is inside an existing anchor.
    # This is the second line of defense - build_edit should prevent this,
    # but we verify here to catch edge cases.
    # Pre-existing nesting elsewhere must NOT disable this check.
    insert_offset = _find_inserted_anchor_offset(old, new)
    if insert_offset is not None:
        # Check both real anchors and escaped anchors at the insertion point
        if _is_inside_real_anchor(old, insert_offset):
            raise LinkConflict(
                "The edit would create nested anchor tags (<a> inside <a>), which is invalid HTML.",
                code="insert_inside_existing_anchor"
            )
        if _is_inside_escaped_anchor(old, insert_offset):
            raise LinkConflict(
                "The insertion point is inside an escaped anchor region (&lt;a&gt;...&lt;/a&gt;).",
                code="insert_inside_existing_anchor"
            )


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
