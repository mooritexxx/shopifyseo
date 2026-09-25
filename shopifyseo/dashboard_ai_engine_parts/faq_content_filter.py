"""FAQ/PAA content filtering and normalization for article drafts.

This module provides:
1. Denylist patterns for filtering problematic FAQ/PAA questions
2. Case-preserving flavor -> flavour normalization
3. H3-heading mapping filter for FAQ entries

All patterns are designed for a Canadian vape retailer context but are
structured for easy extension to other domains.
"""

from __future__ import annotations

import html as html_module
import logging
import re
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)


class DenylistCategory(NamedTuple):
    """A category of content to filter with associated regex patterns."""
    name: str
    patterns: list[re.Pattern[str]]
    description: str


# ─────────────────────────────────────────────────────────────────────────────
# FAQ/PAA DENYLIST PATTERNS
# ─────────────────────────────────────────────────────────────────────────────
# Each category has a descriptive name, compiled patterns, and a reason.
# Patterns are case-insensitive and match anywhere in the question text.

_HEALTH_MEDICAL_PATTERNS = [
    re.compile(r"\bbetter\s+for\s+you\b", re.IGNORECASE),
    re.compile(r"\bhealthier\b", re.IGNORECASE),
    re.compile(r"\bsafe\s+for\s+lungs?\b", re.IGNORECASE),
    re.compile(r"\bsafer\s+than\b", re.IGNORECASE),
    re.compile(r"\bsafest\b", re.IGNORECASE),
    re.compile(r"\bquit(?:ting)?\s+(?:smoking|cigarettes?)\b", re.IGNORECASE),
    re.compile(r"\bcessation\b", re.IGNORECASE),
    re.compile(r"\bstop\s+smoking\b", re.IGNORECASE),
    re.compile(r"\bhelp(?:s)?\s+(?:you\s+)?quit\b", re.IGNORECASE),
    re.compile(r"\bgood\s+for\s+(?:your\s+)?health\b", re.IGNORECASE),
    re.compile(r"\blung\s+(?:health|damage|cancer)\b", re.IGNORECASE),
    re.compile(r"\bcancer\b", re.IGNORECASE),
    re.compile(r"\bharm(?:ful|less)?\s+(?:to|for|than)\b", re.IGNORECASE),
    re.compile(r"\bmedical\s+(?:advice|benefits?|claims?)\b", re.IGNORECASE),
    re.compile(r"\bdoctor(?:s)?(?:\s+(?:say|recommend))?\b", re.IGNORECASE),
    re.compile(r"\btherapeutic\b", re.IGNORECASE),
    re.compile(r"\btreat(?:ment|s)?\s+(?:for|of)\b", re.IGNORECASE),
]

_CIGARETTE_TOBACCO_PATTERNS = [
    re.compile(r"\bhow\s+many\s+cigarettes?\s+(?:is|are|in|equal)\b", re.IGNORECASE),
    re.compile(r"\bcigarettes?\s+(?:is|are|in|equal|equivalent)\b", re.IGNORECASE),
    re.compile(r"\bequivalent\s+to\s+\d+\s+cigarettes?\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\s+(?:smoking|tobacco)\b", re.IGNORECASE),
    re.compile(r"\bversus\s+(?:smoking|tobacco)\b", re.IGNORECASE),
    re.compile(r"\bcompared?\s+to\s+(?:smoking|cigarettes?|tobacco)\b", re.IGNORECASE),
    re.compile(r"\bswitch(?:ing)?\s+from\s+(?:smoking|cigarettes?)\b", re.IGNORECASE),
    re.compile(r"\breplac(?:e|ing)\s+(?:smoking|cigarettes?)\b", re.IGNORECASE),
    re.compile(r"\bcigarette\s+puff\b", re.IGNORECASE),
    re.compile(r"\bpuffs?\s+(?:per|in\s+a)\s+cigarette\b", re.IGNORECASE),
    re.compile(r"\btobacco\s+(?:vs|versus|compared)\b", re.IGNORECASE),
]

_SUPERLATIVE_BAIT_PATTERNS = [
    re.compile(r"#\s*1\s+brand\b", re.IGNORECASE),
    re.compile(r"\bnumber\s+(?:one|1)\s+(?:brand|vape|product)\b", re.IGNORECASE),
    re.compile(r"\bbest\s+brand\b", re.IGNORECASE),
    re.compile(r"\btop\s+(?:rated|selling)\s+brand\b", re.IGNORECASE),
    re.compile(r"\brarest\s+flavo(?:u)?r\b", re.IGNORECASE),
    re.compile(r"\bmost\s+(?:rare|exclusive)\s+flavo(?:u)?r\b", re.IGNORECASE),
    re.compile(r"\bbest\s+(?:vape|e-?liquid|juice)\s+(?:brand|company)\b", re.IGNORECASE),
    re.compile(r"\bworld(?:'?s)?\s+(?:best|#\s*1|number\s+one)\b", re.IGNORECASE),
]

_WHOLESALE_HIGH_NICOTINE_PATTERNS = [
    re.compile(r"\bwholesale\b", re.IGNORECASE),
    re.compile(r"\bbulk\s+(?:order|buy|purchase|pricing)\b", re.IGNORECASE),
    re.compile(r"\bdistributor\b", re.IGNORECASE),
    re.compile(r"\bresell(?:er|ing)?\b", re.IGNORECASE),
    re.compile(r"\b100\s*mg\b", re.IGNORECASE),  # 100mg nicotine strength
    re.compile(r"\b(?:50|60|100)\s*mg\s*(?:nic(?:otine)?|strength)\b", re.IGNORECASE),
    re.compile(r"\bhigh(?:er)?\s+nicotine\s+(?:strength|level|content)\b", re.IGNORECASE),
]

FAQ_DENYLIST_CATEGORIES: list[DenylistCategory] = [
    DenylistCategory(
        name="health_medical",
        patterns=_HEALTH_MEDICAL_PATTERNS,
        description="Health/medical claims (e.g. 'better for you', 'healthier', quitting/cessation)",
    ),
    DenylistCategory(
        name="cigarette_tobacco_comparison",
        patterns=_CIGARETTE_TOBACCO_PATTERNS,
        description="Cigarette/tobacco comparisons (e.g. 'how many cigarettes', 'vs smoking')",
    ),
    DenylistCategory(
        name="superlative_bait",
        patterns=_SUPERLATIVE_BAIT_PATTERNS,
        description="Superlative/bait phrasing (e.g. '#1 brand', 'best brand', 'rarest flavour')",
    ),
    DenylistCategory(
        name="wholesale_high_nicotine",
        patterns=_WHOLESALE_HIGH_NICOTINE_PATTERNS,
        description="Wholesale questions and high nicotine strengths (100 mg)",
    ),
]


def _match_denylist_category(text: str) -> tuple[str, str] | None:
    """Check if text matches any denylist category.

    Returns (category_name, description) if matched, None otherwise.
    """
    for category in FAQ_DENYLIST_CATEGORIES:
        for pattern in category.patterns:
            if pattern.search(text):
                return category.name, category.description
    return None


def filter_paa_questions(
    questions: list[dict[str, Any]],
    *,
    log_dropped: bool = True,
) -> list[dict[str, Any]]:
    """Filter PAA/FAQ questions, removing those matching denylist patterns.

    Args:
        questions: List of question dicts, each with at least a 'question' key.
        log_dropped: If True, log each dropped question at INFO level.

    Returns:
        Filtered list with problematic questions removed.
    """
    if not questions:
        return []

    kept: list[dict[str, Any]] = []
    dropped_count = 0

    for item in questions:
        if not isinstance(item, dict):
            continue
        question_text = str(item.get("question") or "").strip()
        if not question_text:
            continue

        match = _match_denylist_category(question_text)
        if match:
            dropped_count += 1
            if log_dropped:
                category, description = match
                logger.info(
                    "FAQ filter dropped question (category=%s): %r — %s",
                    category,
                    question_text[:80] + ("…" if len(question_text) > 80 else ""),
                    description,
                )
            continue

        kept.append(item)

    if dropped_count > 0:
        logger.info(
            "FAQ filter: kept %d questions, dropped %d",
            len(kept),
            dropped_count,
        )

    return kept


def filter_paa_hierarchy(
    hierarchy: list[dict[str, Any]],
    *,
    log_dropped: bool = True,
) -> list[dict[str, Any]]:
    """Filter PAA hierarchy, removing parent and child questions matching denylist.

    Args:
        hierarchy: List of hierarchy dicts with 'parent_question' and 'children' keys.
        log_dropped: If True, log each dropped question at INFO level.

    Returns:
        Filtered hierarchy with problematic questions removed.
    """
    if not hierarchy:
        return []

    kept: list[dict[str, Any]] = []

    for layer in hierarchy:
        if not isinstance(layer, dict):
            continue

        parent_q = str(layer.get("parent_question") or "").strip()
        if not parent_q:
            continue

        parent_match = _match_denylist_category(parent_q)
        if parent_match:
            if log_dropped:
                category, description = parent_match
                logger.info(
                    "FAQ filter dropped parent question (category=%s): %r — %s",
                    category,
                    parent_q[:80] + ("…" if len(parent_q) > 80 else ""),
                    description,
                )
            continue

        children_raw = layer.get("children") or []
        filtered_children: list[dict[str, Any]] = []
        for child in children_raw:
            if not isinstance(child, dict):
                continue
            child_q = str(child.get("question") or "").strip()
            if not child_q:
                continue
            child_match = _match_denylist_category(child_q)
            if child_match:
                if log_dropped:
                    category, description = child_match
                    logger.info(
                        "FAQ filter dropped child question (category=%s): %r — %s",
                        category,
                        child_q[:80] + ("…" if len(child_q) > 80 else ""),
                        description,
                    )
                continue
            filtered_children.append(child)

        kept.append({
            **layer,
            "children": filtered_children,
        })

    return kept


# ─────────────────────────────────────────────────────────────────────────────
# FLAVOR -> FLAVOUR NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

# Match 'flavor' variants case-preservingly, but skip URLs, href attributes,
# HTML attributes with URLs, and known brand/product names.
_FLAVOR_PATTERN = re.compile(
    r"""
    (?<![/=])           # Not preceded by / (URL path) or = (attribute value start)
    \b                  # Word boundary
    (                   # Capture group for the word
        [Ff]            # F or f
        [Ll]            # L or l
        [Aa]            # A or a
        [Vv]            # V or v
        [Oo]            # O or o
        [Rr]            # R or r
        (?:             # Optional suffixes
            [Ss]?       # -s (flavors)
            |
            [Ee][Dd]    # -ed (flavored)
            |
            [Ff][Uu][Ll]  # -ful (flavorful)
            |
            [Ii][Nn][Gg]  # -ing (flavoring)
            |
            [Ll][Ee][Ss][Ss]  # -less (flavorless)
        )?
    )
    \b                  # Word boundary
    (?![/])             # Not followed by / (URL path)
    """,
    re.VERBOSE,
)

# Patterns that indicate we're inside a URL or attribute value
_URL_CONTEXT_PATTERN = re.compile(
    r"""
    (?:
        href\s*=\s*["'][^"']*  # Inside href attribute
        |
        src\s*=\s*["'][^"']*   # Inside src attribute
        |
        https?://[^\s"'<>]*   # HTTP(S) URL
        |
        /[a-z0-9_-]+/[a-z0-9_-]*  # URL path segment
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Known brand/product names that contain "flavor" and should be preserved
_FLAVOR_BRAND_NAMES = frozenset([
    "flavor beast",
    "flavour beast",  # both spellings may appear in data
    "flavor god",
    "flavor drops",
])


def _is_protected_context(text: str, match_start: int, match_end: int) -> bool:
    """Check if a match position is inside a protected context (URL, attribute, brand name)."""
    window_start = max(0, match_start - 200)
    window_end = min(len(text), match_end + 50)
    window = text[window_start:window_end].lower()
    adjusted_start = match_start - window_start
    adjusted_end = match_end - window_start

    for url_match in _URL_CONTEXT_PATTERN.finditer(window):
        if url_match.start() <= adjusted_start < url_match.end():
            return True
        if url_match.start() < adjusted_end <= url_match.end():
            return True

    for brand in _FLAVOR_BRAND_NAMES:
        brand_pos = window.find(brand)
        if brand_pos != -1:
            brand_end = brand_pos + len(brand)
            if brand_pos <= adjusted_start < brand_end:
                return True

    return False


def _replace_flavor_preserve_case(match: re.Match[str]) -> str:
    """Replace 'flavor' with 'flavour', preserving original case pattern."""
    word = match.group(1)

    if word.isupper():
        return word.replace("OR", "OUR").replace("or", "our")
    if word.islower():
        return word.replace("or", "our")

    result = []
    i = 0
    while i < len(word):
        chunk = word[i:i+2].lower()
        if chunk == "or" and i >= 4:
            if word[i:i+2].isupper():
                result.append("OUR")
            elif word[i].isupper():
                result.append("Our")
            else:
                result.append("our")
            i += 2
        else:
            result.append(word[i])
            i += 1
    return "".join(result)


def normalize_flavor_to_flavour(text: str, *, log_changes: bool = True) -> str:
    """Normalize US 'flavor' spelling to Canadian/UK 'flavour' case-preservingly.

    Handles: flavor, Flavor, FLAVOR, flavors, flavored, flavorful, flavoring, flavorless
    Skips: URLs, href/src attributes, brand names like 'Flavor Beast'

    Args:
        text: HTML or plain text content.
        log_changes: If True, log each replacement at DEBUG level.

    Returns:
        Text with 'flavor' variants replaced by 'flavour' variants.
    """
    if not text or "flavor" not in text.lower():
        return text

    result_parts: list[str] = []
    last_end = 0
    changes_count = 0

    for match in _FLAVOR_PATTERN.finditer(text):
        start = match.start()
        end = match.end()
        original = match.group(0)

        if _is_protected_context(text, start, end):
            continue

        replacement = _replace_flavor_preserve_case(match)
        if replacement != original:
            result_parts.append(text[last_end:start])
            result_parts.append(replacement)
            last_end = end
            changes_count += 1
            if log_changes:
                logger.debug(
                    "Normalized spelling: %r -> %r",
                    original,
                    replacement,
                )

    if changes_count == 0:
        return text

    result_parts.append(text[last_end:])
    result = "".join(result_parts)

    if log_changes:
        logger.info(
            "Flavor normalization: %d replacement(s) made",
            changes_count,
        )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# FAQ H3 HEADING MAPPING FILTER
# ─────────────────────────────────────────────────────────────────────────────

_H3_TAG_RE = re.compile(r"(?is)<h3\b[^>]*>(.*?)</h3\s*>")
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _normalize_for_matching(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip punctuation, collapse whitespace."""
    t = html_module.unescape(text or "").strip().lower()
    for ch in ("\u2019", "\u2018", "\u2032", "\u00b4"):
        t = t.replace(ch, "'")
    t = t.replace("`", "'")
    t = re.sub(r"[^\w\s']", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_h3_texts(body_html: str) -> list[str]:
    """Extract plain text content from all H3 headings."""
    h3_texts: list[str] = []
    for match in _H3_TAG_RE.finditer(body_html or ""):
        inner = match.group(1) or ""
        inner = _TAG_STRIP_RE.sub(" ", inner)
        inner = html_module.unescape(inner)
        inner = re.sub(r"\s+", " ", inner).strip()
        if inner:
            h3_texts.append(inner)
    return h3_texts


def _question_matches_heading(question: str, h3_normalized: set[str]) -> bool:
    """Check if a question matches any H3 heading (fuzzy match).

    Matching rules (in order of strictness):
    1. Exact normalized match
    2. Question is a substring of an H3 (for truncated questions)
    3. H3 is a substring of question (for expanded questions)
    4. High keyword overlap (>=70% of significant words match)
    """
    q_norm = _normalize_for_matching(question)
    if not q_norm:
        return False

    if q_norm in h3_normalized:
        return True

    for h3 in h3_normalized:
        if q_norm in h3 and len(q_norm) >= 12:
            return True
        if h3 in q_norm and len(h3) >= 12:
            return True

    q_words = set(w for w in q_norm.split() if len(w) >= 3)
    if len(q_words) < 3:
        return any(q_norm in h3 for h3 in h3_normalized)

    for h3 in h3_normalized:
        h3_words = set(w for w in h3.split() if len(w) >= 3)
        if not h3_words:
            continue
        overlap = len(q_words & h3_words)
        if overlap >= 0.7 * len(q_words) or overlap >= 0.7 * len(h3_words):
            return True

    return False


def filter_faq_items_by_h3_headings(
    faq_items: list[dict[str, str]],
    body_html: str,
    *,
    log_dropped: bool = True,
) -> list[dict[str, str]]:
    """Filter FAQ items to only those that map 1:1 to an H3 heading in the draft.

    This ensures FAQs don't introduce topics that the article body doesn't cover.

    Args:
        faq_items: List of FAQ dicts with 'question' and 'answer' keys.
        body_html: The article body HTML to check H3 headings against.
        log_dropped: If True, log each dropped FAQ at INFO level.

    Returns:
        Filtered list of FAQ items that match article H3 headings.

    Matching rule: A question matches if its normalized text has significant
    overlap with any H3 heading (exact match, substring, or >=70% keyword overlap).
    """
    if not faq_items or not body_html:
        return faq_items or []

    h3_texts = _extract_h3_texts(body_html)
    h3_normalized = {_normalize_for_matching(t) for t in h3_texts}
    h3_normalized.discard("")

    if not h3_normalized:
        if log_dropped and faq_items:
            logger.info(
                "H3 filter: no H3 headings found, dropping all %d FAQ items",
                len(faq_items),
            )
        return []

    kept: list[dict[str, str]] = []
    dropped_count = 0

    for item in faq_items:
        question = str(item.get("question") or "").strip()
        if not question:
            continue

        if _question_matches_heading(question, h3_normalized):
            kept.append(item)
        else:
            dropped_count += 1
            if log_dropped:
                logger.info(
                    "H3 filter dropped FAQ (no matching heading): %r",
                    question[:80] + ("…" if len(question) > 80 else ""),
                )

    if dropped_count > 0:
        logger.info(
            "H3 filter: kept %d FAQs, dropped %d (no matching H3 heading)",
            len(kept),
            dropped_count,
        )

    return kept


def filter_required_questions_by_h3(
    required_questions: list[str],
    body_html: str,
    *,
    log_dropped: bool = True,
) -> list[str]:
    """Filter required questions to only those that match H3 headings.

    Similar to filter_faq_items_by_h3_headings but for plain question strings.

    Args:
        required_questions: List of question strings.
        body_html: The article body HTML to check H3 headings against.
        log_dropped: If True, log each dropped question at INFO level.

    Returns:
        Filtered list of questions that match article H3 headings.
    """
    if not required_questions or not body_html:
        return required_questions or []

    h3_texts = _extract_h3_texts(body_html)
    h3_normalized = {_normalize_for_matching(t) for t in h3_texts}
    h3_normalized.discard("")

    if not h3_normalized:
        if log_dropped and required_questions:
            logger.info(
                "H3 filter: no H3 headings found, dropping all %d required questions",
                len(required_questions),
            )
        return []

    kept: list[str] = []
    dropped_count = 0

    for question in required_questions:
        q = question.strip()
        if not q:
            continue

        if _question_matches_heading(q, h3_normalized):
            kept.append(q)
        else:
            dropped_count += 1
            if log_dropped:
                logger.info(
                    "H3 filter dropped required question (no matching heading): %r",
                    q[:80] + ("…" if len(q) > 80 else ""),
                )

    if dropped_count > 0:
        logger.info(
            "H3 filter: kept %d required questions, dropped %d (no matching H3 heading)",
            len(kept),
            dropped_count,
        )

    return kept
