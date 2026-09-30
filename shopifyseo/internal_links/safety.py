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


class LinkConflict(ValueError):
    def __init__(self, message: str, *, text_diff: str = ""):
        super().__init__(message)
        self.detail = {"message": message, "text_diff": text_diff}


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


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
    keys = {"anchor_phrase", "insert_sentence", "insert_after_text"}
    if not isinstance(raw, dict) or set(raw) - keys:
        raise LinkConflict("Full-body AI responses and unsupported edit fields are blocked. Generate a new suggestion.")
    edit = {k: v.strip() for k, v in raw.items() if isinstance(v, str) and v.strip()}
    if any(v is not None and not isinstance(v, str) for v in raw.values()):
        raise LinkConflict("Link edits must contain plain text.")
    phrase = edit.get("anchor_phrase", "")
    if not phrase or len(phrase) > 120:
        raise LinkConflict("A short, exact anchor phrase is required.")
    if any("<" in v or ">" in v for v in edit.values()):
        raise LinkConflict("AI edits must contain plain text, never HTML.")
    sentence = edit.get("insert_sentence")
    if sentence:
        if len(sentence) > 300 or "\n" in sentence or re.search(r"[.!?]\s+\S", sentence):
            raise LinkConflict("Only one short sentence may be added.")
        if sentence.count(phrase) != 1 or not edit.get("insert_after_text"):
            raise LinkConflict("The sentence must contain the exact anchor once and identify a paragraph.")
    elif edit.get("insert_after_text"):
        raise LinkConflict("A paragraph location requires an insertion sentence.")
    return edit


def build_edit(old: str, raw: dict, url: str) -> str:
    edit = validate_edit(raw)
    phrase = edit["anchor_phrase"]
    parser = BodyParser(old)
    href = html.escape(url, quote=True)
    sentence = edit.get("insert_sentence")
    if sentence:
        location = " ".join(edit["insert_after_text"].split())
        matches = [end for start, end in parser.paragraphs if visible_text(old[start:end]) == location]
        if len(matches) != 1:
            raise LinkConflict("The insertion paragraph is missing or ambiguous. Generate a new suggestion.")
        linked = html.escape(sentence).replace(html.escape(phrase), f'<a href="{href}">{html.escape(phrase)}</a>', 1)
        offset = matches[0]
        return old[:offset] + "<p>" + linked + "</p>" + old[offset:]
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
