"""Find existing body phrases that can carry an internal link."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_EXCLUDED_TAGS = {"a", "h1", "h2", "h3", "h4", "h5", "h6", "script", "style"}

# Generic/weak words that make poor anchor text when used alone
WEAK_ANCHOR_WORDS = frozenset({
    # Generic action words
    "here", "click", "link", "page", "read", "more", "this", "that", "view",
    "see", "learn", "check", "visit", "go", "find", "get", "buy", "shop",
    # Common generic nouns
    "product", "products", "item", "items", "thing", "things", "stuff",
    "website", "site", "article", "post", "info", "information",
    # Disposable/vape-related generic words (domain-specific weak anchors)
    "disposable", "disposables", "vape", "vapes", "vaping", "device", "devices",
    "pod", "pods", "kit", "kits", "mod", "mods", "tank", "tanks",
    "juice", "liquid", "flavor", "flavors", "flavour", "flavours",
    # Generic adjectives
    "new", "best", "top", "great", "good", "nice", "cool", "amazing",
})


def is_weak_anchor(phrase: str | None) -> bool:
    """Check if the anchor phrase is weak (generic single word).
    
    Returns True if the phrase is a single word that's in the weak anchor list.
    Multi-word phrases are generally acceptable even if they contain weak words.
    """
    if not phrase or not phrase.strip():
        return True
    
    words = phrase.strip().lower().split()
    if len(words) != 1:
        return False
    
    return words[0] in WEAK_ANCHOR_WORDS


def get_weak_anchor_warning(phrase: str | None) -> str | None:
    """Get a warning message if the anchor phrase is weak, or None if acceptable."""
    if not phrase:
        return "Empty anchor phrase"
    
    if is_weak_anchor(phrase):
        return f'"{phrase}" is a generic term that may not provide good SEO value as anchor text. Consider using a more descriptive phrase.'
    
    return None


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
