"""Find existing body phrases that can carry an internal link."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_EXCLUDED_TAGS = {"a", "h1", "h2", "h3", "h4", "h5", "h6", "script", "style"}


class _EligibleTextExtractor(HTMLParser):
    """Collect visible text that is NOT inside links, headings, scripts, or styles."""

    def __init__(self) -> None:
        super().__init__()
        self._excluded_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _EXCLUDED_TAGS:
            self._excluded_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _EXCLUDED_TAGS and self._excluded_depth > 0:
            self._excluded_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._excluded_depth == 0:
            self.parts.append(data)


def eligible_text(html: str | None) -> str:
    if not html:
        return ""
    extractor = _EligibleTextExtractor()
    extractor.feed(html)
    return " ".join(" ".join(extractor.parts).split())


def find_anchor_phrase(html: str | None, candidates: list[str]) -> str | None:
    """Return the first candidate phrase present in eligible body text (longest first).

    The returned string preserves the body's original casing.
    """
    text = eligible_text(html)
    if not text:
        return None
    for candidate in sorted({c.strip() for c in candidates if c and c.strip()}, key=len, reverse=True):
        pattern = r"\b" + re.escape(candidate) + r"\b"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None
