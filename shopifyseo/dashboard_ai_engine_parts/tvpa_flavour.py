"""TVPA (Tobacco and Vaping Products Act) flavour compliance guardrail.

Canada's TVPA bars promoting vaping products in ways that suggest confectionery,
dessert, soft-drink, energy-drink, or cannabis flavours, and it bars lifestyle
and testimonial-style promotion.

This module provides:
- TVPA_FLAVOUR_RULE: prompt text to inject into all writer prompts
- tvpa_flavour_matches(): detector for affirmative TVPA violations
- tvpa_flavour_issue_messages(): human-readable issue strings
"""
from __future__ import annotations

import html
import re
from typing import Iterable

__all__ = [
    "TVPA_FLAVOUR_RULE",
    "tvpa_flavour_matches",
    "tvpa_flavour_issue_messages",
]

TVPA_FLAVOUR_RULE = (
    "Flavour compliance (Canada Tobacco and Vaping Products Act): describe flavours only with plain fruit, "
    "menthol/mint/ice/cooling, tobacco, and the product's own named flavour descriptors from <context>. "
    "Never compare, liken or relate a flavour to candy or confectionery, desserts or baked goods, soda or soft drinks, "
    "energy drinks, or cannabis. That includes '-like', '-inspired', '-style', 'reminiscent of', 'tastes like', "
    "'evokes' and 'notes of' phrasing (for example never 'candy-like', 'dessert-inspired', 'tastes like cotton candy', "
    "'soda-style fizz'). Do not mention those categories at all, not even as a contrast. "
    "Avoid nostalgic or childhood wording, treat/indulgence wording, testimonial wording (what customers, fans or "
    "reviewers say or love), and lifestyle wording (e.g. 'fits your lifestyle'). "
    "The words sweet, premium, best and experience are allowed. "
    "If an official product or flavour name in <context> contains one of these words (e.g. 'Peaches & Cream', "
    "'Banana Bake', 'Chuggin Green Dew', 'Dragon Fruit Lemonade'), you may repeat that name exactly as written, "
    "but do not build a comparison around it."
)

# ---------------------------------------------------------------------------
# Regex patterns for matching TVPA violations
# ---------------------------------------------------------------------------

_CATEGORY: dict[str, str] = {
    "candy": (
        r"candy|candies|candied|confection(?:ery|eries|s)?|gumm(?:y|ies)|gumdrops?|"
        r"bubble ?gum|cotton candy|lollipops?|lollies|jelly ?beans?|taffy|toffee|fudge|"
        r"marshmallows?|chocolate bars?|sweet shop|candy shop"
    ),
    "dessert_baked": (
        r"desserts?|baked goods?|bakery|pastr(?:y|ies)|cakes?|cupcakes?|cheesecakes?|"
        r"cookies?|brownies?|muffins?|donuts?|doughnuts?|pies?|pop-tarts?|custards?|"
        r"puddings?|ice cream|milkshakes?|sundaes?|frosting|cereal"
    ),
    "soda_soft_drink": (
        r"sodas?|soda pop|soft drinks?|fizzy drinks?|colas?|root beer|cream soda|"
        r"ginger ale|lemon-lime soda|mountain dew"
    ),
    "energy_drink": r"energy drinks?|red bull|monster energy",
    "cannabis": r"cannabis|marijuana|weed|kush|thc|cbd|stoner",
}

_STYLE: dict[str, str] = {
    "nostalgic": r"nostalgi(?:a|c)|childhood|throwback|retro|old[- ]school",
    "treat": (
        r"(?:(?:a|an|the|sweet|little|tasty|guilty|seasonal|afternoon|occasional|"
        r"tropical|real|special|summer|daily)\s+treats?(?:-like)?)|treat yourself|"
        r"treat-like|guilty pleasure|indulg(?:e|ent|ence)"
    ),
    "testimonial": (
        r"customers (?:say|love|rave)|reviewers (?:say|love)|fans (?:say|love|rave)|"
        r"(?:five|5)[- ]star reviews?|rave reviews"
    ),
    "lifestyle": r"lifestyle|way of life|party (?:vibe|scene)|night out|vibe",
}

# Negation cues that indicate contrast/negation, not affirmation
_NEG = re.compile(
    r"\b(?:not|no|never|without|avoid(?:s|ed|ing)?|rather than|instead of|nor|unlike|"
    r"isn't|aren't|doesn't|don't|won't|free of|free from|over|than|versus|vs\.?|"
    r"departure from|away from|moved past|move past|tired of|skip(?:s|ping)?|"
    r"steer(?:s|ing)? clear of|beyond|less|alternative to|compared (?:to|with)|"
    r"break from|apart from|separates? (?:it|this)? ?from|distinguish(?:es|ing)? (?:it|this)? ?from|"
    r"eschew(?:s|ing)?|graduate from|change from)\b[^.;:!?]{0,100}$",
    re.IGNORECASE,
)

_NEG_SHORT_CHARS = 30

_AFTER_NEG = re.compile(
    r"^[^.;:!?]{0,50}\b(?:aren't|isn't|don't|doesn't|are not|is not)\b",
    re.IGNORECASE,
)

_CONTRAST_OPEN = re.compile(
    r"^\W*(?:(?:while|whereas|although|though)\b[^,]{0,140}"
    r"(?:many|some|other|most|standard|traditional|typical|generic|common)\b|"
    r"if you (?:often |ever |still )?(?:find|have grown tired|are tired)\b)",
    re.IGNORECASE,
)

_TOO_AFTER = re.compile(
    r"^[^.;:!?]{0,50}\btoo\s+(?:sweet|sugary|heavy|intense|rich|cloying|artificial|syrupy)\b",
    re.IGNORECASE,
)

_FLAVOUR_CTX = re.compile(
    r"\b(?:flavou?rs?|tastes?|tasting|notes?|profiles?|aromas?|inhale|exhale|"
    r"finish|palate|blend|sweetness)\b",
    re.IGNORECASE,
)

_LIST_FILLER: set[str] = {
    "or", "and", "to", "a", "an", "any", "other", "the", "flavour", "flavours",
    "flavor", "flavors", "goods", "drinks", "drink", "wording", "phrasing",
    "language", "like", "inspired", "style", "with", "of", "its", "their", "nor",
    "compare", "comparisons", "references", "descriptors", "testimonial",
    "testimonials", "nostalgic", "treat", "treats", "liken", "relate", "mention",
}

_NEG_CUE = re.compile(
    r"\b(?:not|no|never|without|avoid(?:s|ed|ing)?|nor|don't|do not)\b",
    re.IGNORECASE,
)


def _build_rx(patterns: dict[str, str]) -> dict[str, re.Pattern[str]]:
    """Build regex patterns with optional -like/-inspired/-style suffixes."""
    return {
        k: re.compile(
            r"(?<![\w-])(?:" + v + r")(?:-like|-inspired|-style)?(?![\w])",
            re.IGNORECASE,
        )
        for k, v in patterns.items()
    }


_CAT_RX = _build_rx(_CATEGORY)
_STYLE_RX = _build_rx(_STYLE)

# Combined pattern for checking if text contains any triggers (for rule echo detection)
_ALL_TRIGGERS = re.compile(
    "|".join(r.pattern for r in [*_CAT_RX.values(), *_STYLE_RX.values()]),
    re.IGNORECASE,
)


def _visible_text(html_text: str) -> str:
    """Extract visible text from HTML, stripping scripts and tags."""
    h = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_text or "")
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def _mask_allowed(text: str, allowed_names: Iterable[str]) -> str:
    """Mask allowed names with placeholder characters to prevent false positives."""
    low = text.lower()
    # Sort by length descending so longer names are masked first
    names_in_text = sorted(
        {n for n in allowed_names if n and n.lower() in low},
        key=len,
        reverse=True,
    )
    for name in names_in_text:
        text = re.sub(
            re.escape(name),
            lambda m: "\u2588" * len(m.group(0)),
            text,
            flags=re.IGNORECASE,
        )
    return text


def _negated_list(before: str) -> bool:
    """Check if the match is part of a negated list (rule echo)."""
    cues = list(_NEG_CUE.finditer(before))
    if not cues:
        return False
    rest = _ALL_TRIGGERS.sub(" ", before[cues[-1].end() :])
    return len(rest) <= 220 and all(
        w in _LIST_FILLER for w in re.findall(r"[a-z']+", rest.lower())
    )


def tvpa_flavour_matches(
    text_or_html: str,
    *,
    allowed_names: Iterable[str] = (),
) -> list[dict]:
    """Find affirmative TVPA flavour-comparison / lifestyle matches in text.

    Args:
        text_or_html: The text or HTML to check.
        allowed_names: Product/flavour names from the catalog that should not
            trigger matches when used verbatim.

    Returns:
        List of match dicts, each with:
        - group: "category" or "style"
        - key: the specific category (e.g. "candy", "nostalgic")
        - term: the matched text
        - sentence: the containing sentence (max 220 chars)
    """
    # The rule text itself must never trigger
    visible = _visible_text(text_or_html)
    masked = _mask_allowed(visible, (_visible_text(TVPA_FLAVOUR_RULE), *allowed_names))

    hits: list[dict] = []
    for sent in re.split(r"(?<=[.!?])\s+", masked):
        for group, table in (("category", _CAT_RX), ("style", _STYLE_RX)):
            for key, rx in table.items():
                for m in rx.finditer(sent):
                    before = sent[: m.start()]
                    after = sent[m.end() :]

                    # Skip if this is part of a negated list (rule echo)
                    if _negated_list(before):
                        continue

                    # Different negation window for style vs category
                    if group == "style":
                        if _NEG.search(before[-_NEG_SHORT_CHARS:]):
                            continue
                    else:
                        # Category matches: check full negation patterns
                        if _NEG.search(before):
                            continue
                        if _CONTRAST_OPEN.match(sent):
                            continue
                        if _TOO_AFTER.match(after):
                            continue
                        if _AFTER_NEG.match(after):
                            continue

                    # Special handling for cannabis and retro/throwback/old-school:
                    # require flavour context to avoid false positives
                    term_lower = m.group(0).lower()
                    if key == "cannabis" or term_lower in (
                        "old-school",
                        "old school",
                        "retro",
                        "throwback",
                    ):
                        if not _FLAVOUR_CTX.search(sent):
                            continue

                    # Unmask the sentence for display (replace placeholder with actual text)
                    display_sent = sent.strip()[:220]
                    # Replace mask characters with the original visible text
                    orig_visible = _visible_text(text_or_html)
                    if "\u2588" in display_sent:
                        # Find the position in original and reconstruct
                        display_sent = orig_visible[
                            : min(220, len(orig_visible))
                        ].strip()

                    hits.append(
                        {
                            "group": group,
                            "key": key,
                            "term": m.group(0),
                            "sentence": display_sent,
                        }
                    )

    return hits


def tvpa_flavour_issue_messages(matches: list[dict]) -> list[str]:
    """Convert match dicts to human-readable issue messages."""
    messages: list[str] = []
    for match in matches:
        term = match["term"]
        key = match["key"]
        sent = match["sentence"]
        # Truncate long sentences
        if len(sent) > 100:
            sent = sent[:97] + "..."
        messages.append(f"TVPA flavour wording: '{term}' ({key}) in: '{sent}'")
    return messages
