"""Commerce heading gate for article drafts.

This module detects and handles H2 headings that claim stock status or
availability, or that are about bulk/wholesale buying. Such headings are
inappropriate for a retail-only store where stock changes daily.

The gate operates on H2 headings only and does NOT overlap with the FAQ
content filter (which handles H3 questions).
"""
from __future__ import annotations

import html as html_module
import re
from typing import Literal

__all__ = [
    "classify_commerce_heading",
    "commerce_heading_violations",
    "commerce_heading_gaps",
    "rewrite_stock_status_heading",
    "repair_commerce_headings",
    "COMMERCE_HEADING_PROMPT_RULE",
]


COMMERCE_HEADING_PROMPT_RULE = (
    " Do not write H2 headings or sentences that claim stock status or availability "
    "(Available, In Stock, Sold Out, Restock, Back in stock) or that are about "
    "bulk/wholesale buying; the store is retail-only and stock changes daily. "
    "Name flavour sections plainly, for example 'Top <Line> Flavours' or '<Line> Flavours at <Store>'."
)


_LISTING_NOUN = (
    r"(?:flavou?rs?|colou?rs?|options|variants|models|devices|pods?|skus?|"
    r"products|strengths|sizes|picks|cartridges|e[- ]?liquids?|juices?)"
)


_STOCK_STATUS_PATTERNS = [
    re.compile(r"\b(?:in|out[\s-]+of)[\s-]+stock\b", re.IGNORECASE),
    re.compile(r"\bsold[\s-]+out\b", re.IGNORECASE),
    re.compile(r"\brestock(?:ed|ing|s)?\b", re.IGNORECASE),
    re.compile(r"\bstock\s+(?:status|levels?|updates?|checks?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:stock|flavou?r|product|sku|model|colou?r)s?\s+availability\b",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b{_LISTING_NOUN}\b(?:\W+\w+){{0,6}}?\W+(?:are\s+|is\s+)?(?:currently\s+|now\s+)?available\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:now|currently)\s+available\b", re.IGNORECASE),
    re.compile(r"\bavailable\s+(?:now|today|at\s+vapely)\b", re.IGNORECASE),
]


_BULK_WHOLESALE_PATTERNS = [
    re.compile(r"\bwholesale\b", re.IGNORECASE),
    re.compile(r"\bbulk\b", re.IGNORECASE),
    re.compile(r"\bresell(?:er|ers|ing)?\b", re.IGNORECASE),
    re.compile(r"\bdistributors?\b", re.IGNORECASE),
    re.compile(r"\bb2b\b", re.IGNORECASE),
    re.compile(r"\bvolume\s+(?:pricing|discounts?|orders?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:case|carton|box)\s+(?:pricing|deals?|discounts?|quantities)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bby\s+the\s+(?:case|box|master\s+case)\b", re.IGNORECASE),
]


_TAG_RE = re.compile(r"<[^>]+>")
_H2_RE = re.compile(r"(?is)<h2\b([^>]*)>(.*?)</h2\s*>")
_SCRIPT_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")


def _strip_tags_and_unescape(text: str) -> str:
    """Remove HTML tags and unescape entities."""
    text = _TAG_RE.sub(" ", text or "")
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def classify_commerce_heading(text: str) -> Literal["stock_status", "bulk_wholesale"] | None:
    """Classify an H2 heading as stock-status, bulk/wholesale, or neither.

    Parameters
    ----------
    text : str
        The heading text (may include tags; they are stripped).

    Returns
    -------
    str or None
        ``"stock_status"``, ``"bulk_wholesale"``, or ``None`` if neither.
        If both match, bulk/wholesale takes precedence.
    """
    plain = _strip_tags_and_unescape(text)
    if not plain:
        return None

    for pat in _BULK_WHOLESALE_PATTERNS:
        if pat.search(plain):
            return "bulk_wholesale"

    for pat in _STOCK_STATUS_PATTERNS:
        if pat.search(plain):
            return "stock_status"

    return None


def commerce_heading_violations(body_html: str) -> list[tuple[str, str]]:
    """Return H2 headings that violate commerce rules.

    Returns
    -------
    list of (kind, text)
        Each tuple is (``"stock_status"`` or ``"bulk_wholesale"``, heading text).
    """
    violations: list[tuple[str, str]] = []
    for m in _H2_RE.finditer(body_html or ""):
        inner = m.group(2) or ""
        plain = _strip_tags_and_unescape(inner)
        kind = classify_commerce_heading(plain)
        if kind:
            violations.append((kind, plain))
    return violations


def commerce_heading_gaps(body_html: str) -> list[str]:
    """Return compliance gap messages for commerce heading violations.

    Returns
    -------
    list[str]
        Gap messages for each violating H2.
    """
    gaps: list[str] = []
    for kind, text in commerce_heading_violations(body_html):
        preview = text if len(text) <= 80 else text[:77] + "…"
        if kind == "stock_status":
            gaps.append(
                f"Remove stock-status H2 heading '{preview}': "
                "headings must not claim availability or stock levels (stock changes daily)."
            )
        else:
            gaps.append(
                f"Remove bulk/wholesale H2 heading '{preview}': "
                "the store is retail-only; do not write bulk, wholesale, case or reseller sections."
            )
    return gaps


_AVAILABLE_QUESTION_SUFFIX_RE = re.compile(
    r"\s+(are|is)\s+(?:currently\s+|now\s+)?available(\s*\?)?$",
    re.IGNORECASE,
)
_AVAILABLE_TRAILING_RE = re.compile(
    r"\s*\b(?:currently\s+|now\s+)?(?:available|in[\s-]+stock)(?:\s+(?:now|today))?\b",
    re.IGNORECASE,
)
_TRIM_TRAILING_PUNCT_RE = re.compile(r"[\s\-:–—,]+$")


def rewrite_stock_status_heading(text: str) -> str | None:
    """Attempt to rewrite a stock-status heading to remove availability claims.

    Parameters
    ----------
    text : str
        The heading text (tags stripped).

    Returns
    -------
    str or None
        The rewritten heading, or None if:
        - The result is empty
        - The result is unchanged
        - The result still classifies as stock_status or bulk_wholesale
    """
    original = _strip_tags_and_unescape(text)
    if not original:
        return None

    rewritten = original

    m = _AVAILABLE_QUESTION_SUFFIX_RE.search(rewritten)
    if m:
        verb = m.group(1).lower()
        question_mark = m.group(2) or ""
        if verb == "are":
            replacement = " Are There" + ("?" if question_mark else "")
        else:
            replacement = " Is There" + ("?" if question_mark else "")
        rewritten = rewritten[: m.start()] + replacement
    else:
        rewritten = _AVAILABLE_TRAILING_RE.sub("", rewritten)

    rewritten = re.sub(r"\s+", " ", rewritten).strip()

    rewritten = _TRIM_TRAILING_PUNCT_RE.sub("", rewritten).strip()

    if not rewritten:
        return None
    if rewritten == original:
        return None
    if classify_commerce_heading(rewritten):
        return None

    return rewritten


def repair_commerce_headings(body_html: str) -> tuple[str, list[str]]:
    """Repair or remove commerce heading violations.

    Stock-status H2s that can be rewritten: the heading text is replaced,
    keeping the ``<h2 …>`` tag and attributes intact.

    Bulk/wholesale H2s, or stock-status H2s that cannot be rewritten (e.g.
    "Sold Out …"): the entire section (from that H2 up to the next H2 or
    end of body) is removed, but ``<script>`` blocks (JSON-LD) are preserved.

    This function is idempotent.

    Parameters
    ----------
    body_html : str
        The article body HTML.

    Returns
    -------
    (new_html, changes)
        The repaired HTML and a list of change descriptions.
    """
    if not body_html:
        return body_html, []

    changes: list[str] = []
    result = body_html

    h2_matches = list(_H2_RE.finditer(result))
    if not h2_matches:
        return result, changes

    edits: list[tuple[int, int, str, str]] = []

    for i, m in enumerate(h2_matches):
        attrs = m.group(1) or ""
        inner = m.group(2) or ""
        plain = _strip_tags_and_unescape(inner)
        kind = classify_commerce_heading(plain)
        if not kind:
            continue

        if kind == "bulk_wholesale":
            end_pos = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(result)
            section_html = result[m.start() : end_pos]
            scripts = _SCRIPT_RE.findall(section_html)
            replacement = "\n".join(scripts)
            edits.append((m.start(), end_pos, replacement, f"Removed bulk/wholesale section: '{plain[:60]}'"))
        else:
            rewritten = rewrite_stock_status_heading(plain)
            if rewritten:
                new_inner = html_module.escape(rewritten)
                new_h2 = f"<h2{attrs}>{new_inner}</h2>"
                edits.append((m.start(), m.end(), new_h2, f"Rewrote heading: '{plain[:40]}' → '{rewritten[:40]}'"))
            else:
                end_pos = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(result)
                section_html = result[m.start() : end_pos]
                scripts = _SCRIPT_RE.findall(section_html)
                replacement = "\n".join(scripts)
                edits.append((m.start(), end_pos, replacement, f"Removed unrewritable stock-status section: '{plain[:60]}'"))

    edits.sort(key=lambda x: x[0], reverse=True)

    for start, end, replacement, change_desc in edits:
        result = result[:start] + replacement + result[end:]
        changes.append(change_desc)

    changes.reverse()

    result = re.sub(r"\n{3,}", "\n\n", result)

    return result, changes
