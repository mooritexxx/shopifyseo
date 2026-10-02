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
from html.parser import HTMLParser
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
    # Lung healing / recovery claims
    re.compile(r"\blungs?\s+(?:heal|recover|can\s+heal)\b", re.IGNORECASE),
    re.compile(r"\brecover(?:y|ing)?\s+from\s+vaping\b", re.IGNORECASE),
    re.compile(r"\b(?:100%|fully)\s+recover\b", re.IGNORECASE),
    # Vaping vs smoking health comparisons
    re.compile(r"\b(?:vaping|smoking)\s+or\s+(?:vaping|smoking)\b", re.IGNORECASE),
    re.compile(r"\bharder\s+on\s+(?:your\s+)?lungs?\b", re.IGNORECASE),
    # Safety guarantees
    re.compile(r"\bguarantees?\s+(?:safety|safe)\b", re.IGNORECASE),
    re.compile(r"\b(?:guarantees?|ensures?)\s+(?:product\s+)?safety\b", re.IGNORECASE),
    # Health considerations (H2/body filter)
    re.compile(r"\bhealth\s+considerations?\b", re.IGNORECASE),
]

_CIGARETTE_TOBACCO_PATTERNS = [
    re.compile(r"\bhow\s+many\s+cigarettes?\s+(?:is|are|in|equal)\b", re.IGNORECASE),
    re.compile(r"\bcigarettes?\s+(?:is|are|in|equal|equivalent)\b", re.IGNORECASE),
    re.compile(r"\bequivalent\s+to\s+\d+\s+cigarettes?\b", re.IGNORECASE),
    re.compile(r"\bequal\s+to\s+\d+\s+cigarettes?\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\s+(?:smoking|tobacco)\b", re.IGNORECASE),
    re.compile(r"\bversus\s+(?:smoking|tobacco)\b", re.IGNORECASE),
    re.compile(r"\bcompared?\s+to\s+(?:smoking|cigarettes?|tobacco)\b", re.IGNORECASE),
    re.compile(r"\bswitch(?:ing)?\s+from\s+(?:smoking|cigarettes?)\b", re.IGNORECASE),
    re.compile(r"\breplac(?:e|ing)\s+(?:smoking|cigarettes?)\b", re.IGNORECASE),
    re.compile(r"\bcigarette\s+puff\b", re.IGNORECASE),
    re.compile(r"\bpuffs?\s+(?:per|in\s+a)\s+cigarette\b", re.IGNORECASE),
    re.compile(r"\btobacco\s+(?:vs|versus|compared)\b", re.IGNORECASE),
    # Transition / switching phrasing
    re.compile(r"\btransition(?:ing)?\s+(?:from|to)\s+(?:smoking|traditional)\b", re.IGNORECASE),
    re.compile(r"\btransitioner(?:s)?\b", re.IGNORECASE),
    re.compile(r"\balternative\s+to\s+(?:traditional\s+)?smoking\b", re.IGNORECASE),
    re.compile(r"\btraditional\s+smoking\b", re.IGNORECASE),
    # Puffs equal to cigarettes
    re.compile(r"\bpuffs?\s+(?:of\s+(?:a\s+)?vape\s+)?(?:equal|equivalent)\s+to\s+\d*\s*cigarette", re.IGNORECASE),
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
    # #1 disposable vape (expanded from just "#1 brand")
    re.compile(r"#\s*1\s+(?:disposable|vape|e-?cig)\b", re.IGNORECASE),
    # "top N" listicle patterns
    re.compile(r"\btop\s+\d+\b.*\bflavo(?:u)?rs?\b", re.IGNORECASE),
    re.compile(r"\btop\s+\d+\b.*\bvapes?\b", re.IGNORECASE),
    # "most sold / most popular" unverifiable claims
    re.compile(r"\bmost\s+(?:sold|popular)\s+(?:vape|flavo(?:u)?r)\b", re.IGNORECASE),
    re.compile(r"\bbest\s+(?:selling|sold)\b", re.IGNORECASE),
    # "best flavour of X" generic bait (but not "best flavour of [specific brand]")
    re.compile(r"\bbest\s+flavo(?:u)?r\s+of\s+(?:vape|vaping|e-?cig)\b", re.IGNORECASE),
]

# Consumption / puffs-per-day patterns
_PUFFS_PER_DAY_PATTERNS = [
    re.compile(r"\b\d+\s+puffs?\s+(?:of\s+(?:a\s+)?vape\s+)?(?:a|per)\s+day\b", re.IGNORECASE),
    re.compile(r"\bpuffs?\s+(?:of\s+(?:a\s+)?vape\s+)?(?:a|per)\s+day\s+(?:a\s+)?lot\b", re.IGNORECASE),
    re.compile(r"\bis\s+\d+\s+puffs?\b.*\b(?:bad|lot|much|okay)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+many\s+puffs?\s+(?:a|per)\s+day\b", re.IGNORECASE),
]

# Off-brand competitor mentions (for FAQ context filtering)
# These brands should not be the main topic of FAQs unless the article is about them
_COMPETITOR_BRAND_PATTERNS = [
    re.compile(r"\belfbar\b", re.IGNORECASE),
    re.compile(r"\belf\s+bar\b", re.IGNORECASE),
    re.compile(r"\bjuul\b", re.IGNORECASE),
    re.compile(r"\bvuse\b", re.IGNORECASE),
    re.compile(r"\bblu\s+(?:vape|e-?cig)\b", re.IGNORECASE),
    re.compile(r"\bsmok\s+(?:vape|nord|rpm)\b", re.IGNORECASE),
    re.compile(r"\bgeek\s*bar\b", re.IGNORECASE),
    re.compile(r"\blost\s*mary\b", re.IGNORECASE),
]

# Community / external source bait
_EXTERNAL_SOURCE_PATTERNS = [
    re.compile(r"\breddit\b", re.IGNORECASE),
    re.compile(r"\bpdf\b", re.IGNORECASE),
    re.compile(r"\bforum\b", re.IGNORECASE),
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

_REPORTED_QUESTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in (
        r"\b(?:grossest|rarest|benefits?\s+of)\b",
        r"^(?:is|are)\s+.+\s+good\s*[?!]?$",
        r"\bbest\s+flavo(?:u)?r\s+for\s+(?:a\s+)?(?:disposable\s+)?vape\b",
        r"\bbest\s+(?:(?:disposable|vape)\s+)?(?:vapes?|brand)\b",
        r"\bmost[ -]+(?:selling|sold|popular)\b",
        r"\bbanned\s+in\s+canada\b",
        r"\b(?:dentist|vaper['’]?s?\s+tongue|puffs?|cigarettes?)\b",
    )
]

# Question-only rules: never apply these to ordinary headings or body prose.
_QUESTION_BAIT_PATTERNS = [
    (reason, re.compile(pattern, re.I)) for reason, pattern in (
        ("is_good_question", r"^(?:is|are)\s+.+\s+(?:a\s+)?good(?:\s+(?:vapes?|brands?|quality|or\s+bad))?\s*[?!]?$"),
        ("best_brand_question", r"(?<!\w)(?:best|no\.?\s*1|number\s+(?:one|1)|#\s*1|top[ -]+(?:selling|rated))\s+"
         r"(?:(?:refillable|disposable|pod|nicotine|salt|\d+)\s+)*"
         r"(?:vapes?|brands?|disposables?|pods?|e-?liquids?|juice)(?:\s+systems?)?\b|"
         r"\b(?:vapes?|brands?|disposables?|pods?)\s+(?:is|are)\s+(?:the\s+)?best\b"),
        ("benefits_question", r"\b(?:benefits|advantages|beneficial)\b|\bbenefit\s+of\b"),
        ("longest_lasting_question", r"\blongest[ -]+lasting\b|\blasts?\s+(?:the\s+)?longest\b|\blongest\s+battery\s+life\b"),
    )
]


def normalize_question(text: str) -> str:
    """Normalize only for matching; preserve the caller's original question."""
    text = html_module.unescape(text).translate(str.maketrans({
        "“": '"', "”": '"', "‘": "'", "’": "'",
    }))
    text = re.sub(r"[\u2010-\u2015\u2212]", "-", text)
    text = re.sub(r"\s+", " ", text).strip()
    while True:
        stripped = text.strip("\"' ")
        stripped = re.sub(r"^(?:(?:FAQ|Q)\s*(?:\d+\s*)?[.:)]|\d+\s*[.)])\s*", "", stripped, flags=re.I)
        if stripped == text:
            return text
        text = stripped


# Explicit product-line relationships; do not infer brands from arbitrary words.
_PRODUCT_FOCUS_RULES = {
    "fog pro x": [r"\bmr[ .]*fog\b", r"\bfogger\b", r"\bfog\s+vapes?\b"],
    "stlth 60k": [r"\bstlth\s+(?:pods?|titan|nexa)\b"],
    "beast mode": [r"\b(?:flavo(?:u)?r\s+beast\s+)?unleashed\b"],
}

# Applies to visible prose as well as questions. Keep question bait separate so
# manufacturer capacity figures and useful product-handling advice survive.
_HEALTH_CLAIM_PATTERNS = [re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bharm[ -]+reduction\b", r"\bless\s+harmful\b", r"\bsafer\s+alternative\b",
    r"\bsmoke[ -]+free\s+(?:alternatives?|experience|lifestyle|nicotine|individual|products?)\b", r"\bformer\s+smokers?\b",
    r"\bcut(?:ting)?\s+down(?:\s+on)?\s+(?:(?:your|their)\s+)?(?:smoking|cigarettes?|nicotine|vaping|tobacco)\b",
    r"\bhelps?\s+(?:you\s+)?cut\s+down\s*[.!?]?$", r"\b(?:nrt|nicotine\s+replacement\s+therapy)\b",
    r"\b(?:nicotine|cigarette|tobacco|smoking)\s+cravings?\b",
    r"\b(?:curb|manage|satisfy|satisfies|satisfying|reduce|suppress|control)(?:s|d|ing)?\s+(?:(?:your|their|the|existing)\s+)?cravings?\b",
    r"\bkeep\s+cravings?\s+at\s+bay\b", r"\bquit(?:ting)?[ -]+(?:smoking|cigarettes?)\b",
    r"\bstop(?:ping)?\s+smoking\b", r"\b(?:helps?\s+(?:you\s+)?quit|cessation)\b",
    r"\b(?:switch(?:ing)?|transition(?:ing)?)\s+(?:away\s+)?from\s+(?:traditional\s+|combustible\s+)?(?:smoking|tobacco|cigarettes?)\b",
    r"\b(?:healthier|harmless|safe\s+for\s+(?:your\s+)?lungs?)\b",
    r"\b(?:vapes?|vaping|e[ -]?liquids?)\b.{0,40}\b(?:safe|safer|safest)\b",
    r"\b(?:safe|safer|safest)\s+(?:vapes?|vaping|e[ -]?liquids?|vape\s+juice)\b",
    r"\blungs?\s+(?:can\s+)?(?:heal|recover)\b",
    r"\b(?:good\s+for\s+(?:your\s+)?health|safer\s+than)\b",
    r"\b(?:vaping|vapes?|e[ -]?cigarettes?)\b.{0,40}\bbetter\s+for\s+you\b",
    r"\bbetter\s+than\s+smoking\b|\b(?:smoke\s+or\s+vape|vaping\s+(?:vs\.?|or)\s+smoking)\b",
)]

ARTICLE_CONTENT_FILTER_INSTRUCTION = (
    " Final content rules override SERP/PAA suggestions: do not generate questions framed as "
    "'is X good', 'best vape', 'best brand', 'benefits/advantages', 'longest-lasting', "
    "'top rated/selling vape', 'No. 1 vape', 'grossest', 'rarest', 'most-selling', "
    "'most popular', banned flavours, health/safety, cigarette equivalence, dentist, vaper's tongue, "
    "or puff counts. Keep questions specific to the focus product, not broader or different lines. "
    "Do not repeat questions across body headings, FAQ, or Helpful questions. Do not write health "
    "or quit-smoking/quit-vaping claims in answers or body copy: harm reduction, less harmful, safer alternative, "
    "smoke-free, former smoker, cut down, NRT, or nicotine cravings. Preserve factual regulatory names "
    "and practical battery/charging guidance without health promises. Link naturally to at least "
    "3 DISTINCT approved product URLs; collection links and repeated product URLs do not count."
)


def _without_handling_safety(text: str) -> str:
    # Exempt specific handling phrases, not whole paragraphs mentioning batteries.
    patterns = (
        r"\b(?:safe|safer|safest)\s+to\s+(?:charge|recharge|store)\b",
        r"\b(?:safe|safer|safest)\s+(?:battery\s+)?(?:charging|storage|cable|charger)\b",
        r"\b(?:charging|recharging|storing)\s+[^.!?]{0,60}\bsafe\b",
        r"\bsafe\s+from\s+(?:heat|damage|leaks?)\b",
        r"\bsafer\s+by\s+storing\b",
        r"\bsafe\s+to\s+leave\b[^.!?]{0,60}\b(?:hot\s+car|sunlight)\b",
    )
    for pattern in patterns:
        text = re.sub(pattern, "product handling", text, flags=re.I)
    return text


def health_claim_reason(text: str) -> str | None:
    text = re.sub(r"\s+", " ", html_module.unescape(text))
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    text = _without_handling_safety(text)
    # Exact legal name, not a general exemption for smoke-free marketing.
    text = re.sub(r"smoke[ -]free ontario act", "Ontario Act", text, flags=re.I)
    # Narrow factual NRT context; health promises in the same passage still match.
    if re.search(r"\b(?:authorized|authorised|regulated)\b", text, re.I) and re.search(r"\bpharmac(?:y|ies)\b", text, re.I):
        text = re.sub(r"nicotine replacement therapy|\bNRT\b|smoking cessation aid", "regulated product", text, flags=re.I)
    if re.search(r"\bflavo(?:u)?r\s+cravings?\b", text, re.I):
        text = re.sub(r"\bflavo(?:u)?r\s+cravings?\b", "flavour preference", text, flags=re.I)
    for pattern in _HEALTH_CLAIM_PATTERNS:
        if pattern.search(text):
            return "health_claim"
    return None


# Medical question topics from the supplied Sep 29 replacement table. This is
# intentionally question-only: ordinary addiction/misuse warnings stay intact.
_MEDICAL_QUESTION_PATTERN = re.compile(
    r"\b(?:healthy|rsv|diabet(?:es|ics?)|cholesterol|triglycerides?|lupus|implantation|"
    r"quit(?:ting)?\s+vaping|creatinine|cortisol|emphysema|pregnan(?:cy|t)|poisoning|lung\s+infection|nicotine\s+replacement)\b|\b(?:hurt\s+you|bad\s+for\s+you)\b",
    re.I,
)


def question_drop_reason(text: str, target_brand: str | None = None) -> str | None:
    text = normalize_question(text)
    if _MEDICAL_QUESTION_PATTERN.search(text):
        return "medical_question"
    reason = health_claim_reason(text)
    if reason:
        return reason
    if re.search(r"\bsafe\b", _without_handling_safety(text), re.I):
        return "health_safety_question"
    for reason, pattern in _QUESTION_BAIT_PATTERNS:
        if pattern.search(text):
            return reason
    match = _match_denylist_category(text)
    if match:
        return match[0]
    if _mentions_competitor_brand(text, target_brand):
        return "off_brand_competitor"
    focus = normalize_spelling_for_comparison(target_brand or "")
    for product, patterns in _PRODUCT_FOCUS_RULES.items():
        if product in focus and any(re.search(pattern, text, re.I) for pattern in patterns):
            return "off_focus_product_line"
    return None


FAQ_DENYLIST_CATEGORIES: list[DenylistCategory] = [
    DenylistCategory(
        name="health_medical",
        patterns=_HEALTH_MEDICAL_PATTERNS,
        description="Health/medical claims (e.g. 'better for you', 'healthier', quitting/cessation, lung healing)",
    ),
    DenylistCategory(
        name="cigarette_tobacco_comparison",
        patterns=_CIGARETTE_TOBACCO_PATTERNS,
        description="Cigarette/tobacco comparisons (e.g. 'how many cigarettes', 'vs smoking', 'transitioning from smoking')",
    ),
    DenylistCategory(
        name="superlative_bait",
        patterns=_SUPERLATIVE_BAIT_PATTERNS,
        description="Superlative/bait phrasing (e.g. '#1 disposable', 'top 10 flavours', 'most sold', 'best flavour of vape')",
    ),
    DenylistCategory(
        name="wholesale_high_nicotine",
        patterns=_WHOLESALE_HIGH_NICOTINE_PATTERNS,
        description="Wholesale questions and high nicotine strengths (100 mg)",
    ),
    DenylistCategory(
        name="puffs_per_day",
        patterns=_PUFFS_PER_DAY_PATTERNS,
        description="Consumption/puffs per day questions (e.g. 'is 20 puffs a day a lot')",
    ),
    DenylistCategory(
        name="external_source",
        patterns=_EXTERNAL_SOURCE_PATTERNS,
        description="External source / community bait (e.g. 'reddit', 'forum', 'pdf')",
    ),
]

FAQ_DENYLIST_CATEGORIES.append(DenylistCategory("reported_question_bait", _REPORTED_QUESTION_PATTERNS, "Reported off-angle FAQ bait"))

# Competitor brands that trigger off-brand filtering
# Questions mentioning these brands will be filtered if the article is not about them
COMPETITOR_BRAND_PATTERNS = _COMPETITOR_BRAND_PATTERNS


def _match_denylist_category(text: str) -> tuple[str, str] | None:
    """Check if text matches any denylist category.

    Returns (category_name, description) if matched, None otherwise.
    """
    for category in FAQ_DENYLIST_CATEGORIES:
        for pattern in category.patterns:
            if pattern.search(text):
                return category.name, category.description
    return None


def _mentions_competitor_brand(text: str, target_brand: str | None = None) -> str | None:
    """Check if text mentions a competitor brand that is not the target brand.

    Args:
        text: The text to check.
        target_brand: The brand the article is about (case-insensitive).
                     If None, all competitor brand mentions are flagged.

    Returns:
        The competitor brand name if found (and not the target), None otherwise.
    """
    if not text:
        return None

    text_lower = text.lower()
    target_lower = (target_brand or "").lower().strip()

    for pattern in _COMPETITOR_BRAND_PATTERNS:
        match = pattern.search(text_lower)
        if match:
            matched_brand = match.group(0).strip()
            # If the matched brand is the target brand, don't filter it
            if target_lower and matched_brand in target_lower:
                continue
            if target_lower and target_lower in matched_brand:
                continue
            return matched_brand
    return None


def filter_paa_questions(
    questions: list[dict[str, Any]],
    *,
    target_brand: str | None = None,
    log_dropped: bool = True,
) -> list[dict[str, Any]]:
    """Filter PAA/FAQ questions, removing those matching denylist patterns.

    Args:
        questions: List of question dicts, each with at least a 'question' key.
        target_brand: The brand the article is about. If provided, questions about
                     other competitor brands will be filtered out.
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

        # Check denylist patterns
        reason = question_drop_reason(question_text, target_brand)
        match = (reason, reason) if reason else None
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

        # Check for off-brand competitor mentions
        competitor = _mentions_competitor_brand(question_text, target_brand)
        if competitor:
            dropped_count += 1
            if log_dropped:
                logger.info(
                    "FAQ filter dropped question (category=off_brand_competitor): %r — "
                    "mentions competitor brand '%s' in an article about '%s'",
                    question_text[:80] + ("…" if len(question_text) > 80 else ""),
                    competitor,
                    target_brand or "(no target)",
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
    target_brand: str | None = None,
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

        reason = question_drop_reason(parent_q, target_brand)
        parent_match = (reason, reason) if reason else None
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
            reason = question_drop_reason(child_q, target_brand)
            child_match = (reason, reason) if reason else None
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
# US/CA SPELLING NORMALIZATION FOR COMPARISON
# ─────────────────────────────────────────────────────────────────────────────
# Simple regex patterns for normalizing US spellings to Canadian/UK for
# comparison purposes. These handle common variants without HTML-awareness
# (intended for plain text comparison keys, not for rendering).

_US_CA_SPELLING_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # flavor/flavour variants
    (re.compile(r"\bflavor\b", re.IGNORECASE), "flavour"),
    (re.compile(r"\bflavors\b", re.IGNORECASE), "flavours"),
    (re.compile(r"\bflavored\b", re.IGNORECASE), "flavoured"),
    (re.compile(r"\bflavorful\b", re.IGNORECASE), "flavourful"),
    (re.compile(r"\bflavoring\b", re.IGNORECASE), "flavouring"),
    (re.compile(r"\bflavorless\b", re.IGNORECASE), "flavourless"),
    # color/colour variants
    (re.compile(r"\bcolor\b", re.IGNORECASE), "colour"),
    (re.compile(r"\bcolors\b", re.IGNORECASE), "colours"),
    (re.compile(r"\bcolored\b", re.IGNORECASE), "coloured"),
    (re.compile(r"\bcolorful\b", re.IGNORECASE), "colourful"),
    (re.compile(r"\bcoloring\b", re.IGNORECASE), "colouring"),
    (re.compile(r"\bcolorless\b", re.IGNORECASE), "colourless"),
    # favorite/favourite variants
    (re.compile(r"\bfavorite\b", re.IGNORECASE), "favourite"),
    (re.compile(r"\bfavorites\b", re.IGNORECASE), "favourites"),
]


def normalize_spelling_for_comparison(text: str) -> str:
    """Normalize US spellings to Canadian/UK for comparison purposes.

    This function normalizes common US spelling variants (flavor, color,
    favorite) to their Canadian/UK equivalents in lowercase. Intended for
    creating comparison keys where both sides need consistent spelling.

    Unlike normalize_flavor_to_flavour, this function:
    - Works on plain text (not HTML-aware)
    - Returns lowercase output
    - Handles multiple spelling variant families (flavor, color, favorite)
    - Does not preserve case (designed for comparison, not rendering)

    Args:
        text: Plain text to normalize.

    Returns:
        Lowercase text with US spellings converted to Canadian/UK.
    """
    if not text:
        return text

    result = text.lower()
    for pattern, replacement in _US_CA_SPELLING_PATTERNS:
        result = pattern.sub(replacement, result)

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


# ─────────────────────────────────────────────────────────────────────────────
# BODY / H2 / ALT TEXT CONTENT FILTERING
# ─────────────────────────────────────────────────────────────────────────────

_H2_TAG_RE = re.compile(r"(?is)<h2\b[^>]*>(.*?)</h2\s*>")


def filter_body_html_content(
    body_html: str,
    *,
    target_brand: str | None = None,
    log_dropped: bool = True,
) -> str:
    """Filter body HTML to remove problematic H2 sections.

    Removes entire H2 sections (heading + content until next H2) that match
    health/safety denylist patterns like 'Health Considerations'.

    Args:
        body_html: The article body HTML.
        target_brand: The brand the article is about (for off-brand filtering).
        log_dropped: If True, log each removed H2 at INFO level.

    Returns:
        Filtered body HTML with problematic H2 sections removed.
    """
    if not body_html:
        return body_html

    # Find all H2 positions
    h2_matches = list(_H2_TAG_RE.finditer(body_html))
    if not h2_matches:
        return body_html

    sections_to_remove: list[tuple[int, int, str]] = []

    for i, match in enumerate(h2_matches):
        h2_content = match.group(1) or ""
        h2_plain = _TAG_STRIP_RE.sub(" ", h2_content)
        h2_plain = html_module.unescape(h2_plain)
        h2_plain = re.sub(r"\s+", " ", h2_plain).strip()

        # Check against denylist
        deny_match = _match_denylist_category(h2_plain)
        if deny_match:
            # Determine section boundaries
            start = match.start()
            if i + 1 < len(h2_matches):
                end = h2_matches[i + 1].start()
            else:
                # Last H2 - find end or take rest of content
                end = len(body_html)

            sections_to_remove.append((start, end, h2_plain))
            if log_dropped:
                category, description = deny_match
                logger.info(
                    "Body filter removed H2 section (category=%s): %r — %s",
                    category,
                    h2_plain[:60] + ("…" if len(h2_plain) > 60 else ""),
                    description,
                )

    if not sections_to_remove:
        return body_html

    # Remove sections in reverse order to maintain correct positions
    result = body_html
    for start, end, _ in reversed(sections_to_remove):
        result = result[:start] + result[end:]

    return result


def filter_and_dedupe_helpful_questions(
    questions: list[str],
    existing_questions: list[str] | None = None,
    *,
    target_brand: str | None = None,
    log_dropped: bool = True,
) -> list[str]:
    """Filter and dedupe a list of question strings.

    Used for the 'Helpful questions before you choose' block.

    Args:
        questions: List of question strings to filter.
        existing_questions: Questions already in the article (for deduplication).
        target_brand: The brand the article is about (for off-brand filtering).
        log_dropped: If True, log each dropped question at INFO level.

    Returns:
        Filtered and deduplicated list of questions.
    """
    if not questions:
        return []

    existing_normalized = set()
    for q in existing_questions or []:
        norm = _normalize_for_matching(normalize_spelling_for_comparison(q))
        if norm:
            existing_normalized.add(norm)

    kept: list[str] = []
    seen_normalized: set[str] = set(existing_normalized)

    for q in questions:
        q_stripped = q.strip()
        if not q_stripped:
            continue

        # Normalize spelling first
        q_normalized_spelling = normalize_flavor_to_flavour(q_stripped, log_changes=False)

        # Check denylist
        reason = question_drop_reason(q_normalized_spelling, target_brand)
        deny_match = (reason, reason) if reason else None
        if deny_match:
            if log_dropped:
                category, description = deny_match
                logger.info(
                    "Helpful questions filter dropped (category=%s): %r — %s",
                    category,
                    q_stripped[:80] + ("…" if len(q_stripped) > 80 else ""),
                    description,
                )
            continue

        # Check for off-brand competitor mentions
        competitor = _mentions_competitor_brand(q_normalized_spelling, target_brand)
        if competitor:
            if log_dropped:
                logger.info(
                    "Helpful questions filter dropped (category=off_brand_competitor): %r — "
                    "mentions competitor brand '%s'",
                    q_stripped[:80] + ("…" if len(q_stripped) > 80 else ""),
                    competitor,
                )
            continue

        # Deduplication
        q_norm_match = _normalize_for_matching(normalize_spelling_for_comparison(q_normalized_spelling))
        if q_norm_match in seen_normalized:
            if log_dropped:
                logger.info(
                    "Helpful questions filter dropped (duplicate): %r",
                    q_stripped[:80] + ("…" if len(q_stripped) > 80 else ""),
                )
            continue

        seen_normalized.add(q_norm_match)
        kept.append(q_normalized_spelling)

    return kept


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE ALT TEXT GUARDS
# ─────────────────────────────────────────────────────────────────────────────

# Patterns indicating prompt/instruction leakage in alt text
_ALT_PROMPT_LEAK_PATTERNS = [
    re.compile(r"\bmaximum\s+\d+\s+characters?\b", re.IGNORECASE),
    re.compile(r"\bcharacter\s+limit\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*[-–]\s*\d+\s+characters?\b", re.IGNORECASE),
    re.compile(r"^\s*\*\s*$"),  # Just an asterisk
    re.compile(r"\balt\s+text\b", re.IGNORECASE),
    re.compile(r"\bdescribe\s+(?:the\s+)?image\b", re.IGNORECASE),
    re.compile(r"\bimage\s+description\b", re.IGNORECASE),
    re.compile(r"\bprompt\b", re.IGNORECASE),
    re.compile(r"\binstruction\b", re.IGNORECASE),
]

# Characters that indicate cut-off alt text
_ALT_CUTOFF_INDICATORS = [
    r"[^.!?…]\s*$",  # Doesn't end with sentence-ending punctuation
]


def validate_and_fix_alt_text(
    alt_text: str,
    fallback_text: str = "",
    *,
    min_length: int = 20,
    max_length: int = 125,
    log_issues: bool = True,
) -> tuple[str, bool]:
    """Validate and fix image alt text.

    Guards against:
    - Prompt/instruction leakage
    - Cut-off text (truncated mid-word)
    - Text that's too short or too long
    - US spelling (normalizes to Canadian)

    Args:
        alt_text: The alt text to validate.
        fallback_text: Fallback text to use if alt is invalid/unfixable.
        min_length: Minimum acceptable length.
        max_length: Maximum acceptable length (will truncate at word boundary).
        log_issues: If True, log validation issues at INFO level.

    Returns:
        Tuple of (fixed_alt_text, was_modified).
    """
    if not alt_text or not alt_text.strip():
        if log_issues:
            logger.info("Alt text guard: empty alt text, using fallback")
        return fallback_text.strip() or "Product image", True

    original = alt_text.strip()
    result = original
    was_modified = False

    # Check for prompt/instruction leakage
    for pattern in _ALT_PROMPT_LEAK_PATTERNS:
        if pattern.search(result):
            if log_issues:
                logger.info(
                    "Alt text guard: prompt leakage detected in %r, using fallback",
                    result[:60] + ("…" if len(result) > 60 else ""),
                )
            return fallback_text.strip() or "Product image", True

    # Normalize spelling (flavor -> flavour)
    normalized = normalize_flavor_to_flavour(result, log_changes=False)
    if normalized != result:
        result = normalized
        was_modified = True

    # Check for cut-off text (ends mid-word or mid-sentence without punctuation)
    if len(result) < min_length:
        if log_issues:
            logger.info(
                "Alt text guard: too short (%d chars), using fallback: %r",
                len(result),
                result,
            )
        return fallback_text.strip() or "Product image", True

    # Truncate at word boundary if too long
    if len(result) > max_length:
        truncated = result[:max_length]
        # Find last word boundary
        last_space = truncated.rfind(" ")
        if last_space > min_length:
            truncated = truncated[:last_space].rstrip(".,;:!?")
        result = truncated.rstrip()
        was_modified = True
        if log_issues:
            logger.info(
                "Alt text guard: truncated at word boundary from %d to %d chars",
                len(original),
                len(result),
            )

    # Check for apparent cut-off (ends with incomplete word or parenthesis)
    if result and result[-1] in "([{":
        result = result[:-1].rstrip()
        was_modified = True
        if log_issues:
            logger.info("Alt text guard: removed trailing bracket/parenthesis")

    # Check for cut-off mid-word (common AI issue)
    if result and not result[-1].isalnum() and result[-1] not in ".!?…":
        # Likely cut off - try to clean up
        result = result.rstrip(" ,;:-–—")
        was_modified = True

    return result, was_modified


def filter_body_text_content(
    text: str,
    *,
    target_brand: str | None = None,
    log_dropped: bool = True,
) -> str:
    """Filter plain text content (like body paragraphs) to remove problematic sentences.

    Unlike filter_body_html_content which removes entire H2 sections, this
    removes individual sentences that match denylist patterns.

    Args:
        text: Plain text content.
        target_brand: The brand the article is about.
        log_dropped: If True, log each removed sentence at INFO level.

    Returns:
        Filtered text with problematic sentences removed.
    """
    if not text:
        return text

    # Split into sentences (simple heuristic)
    sentences = re.split(r'(?<=[.!?])\s+', text)
    kept: list[str] = []

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Check denylist
        deny_match = _match_denylist_category(sentence)
        if deny_match:
            if log_dropped:
                category, description = deny_match
                logger.info(
                    "Body text filter removed sentence (category=%s): %r — %s",
                    category,
                    sentence[:80] + ("…" if len(sentence) > 80 else ""),
                    description,
                )
            continue

        kept.append(sentence)

    return " ".join(kept)


# ─────────────────────────────────────────────────────────────────────────────
# EXCERPT / SUMMARY GUARDS
# ─────────────────────────────────────────────────────────────────────────────

# Patterns indicating generic/boilerplate excerpt text
_EXCERPT_BOILERPLATE_PATTERNS = [
    # "In this article" / "In this guide" / "In this post" openers
    re.compile(r"^in\s+this\s+(?:article|guide|post|blog|piece)\b", re.IGNORECASE),
    # "Read our article about..."
    re.compile(r"\bread\s+(?:our|this|the)\s+(?:article|guide|post|blog)\b", re.IGNORECASE),
    # "Learn more about..."
    re.compile(r"^learn\s+(?:more\s+)?about\b", re.IGNORECASE),
    # "Click to read..." / "Click here..."
    re.compile(r"\bclick\s+(?:to\s+read|here)\b", re.IGNORECASE),
    # "Find out..."
    re.compile(r"^find\s+out\b", re.IGNORECASE),
    # "Discover..." opener (generic)
    re.compile(r"^discover\s+(?:how|what|why|the)\b", re.IGNORECASE),
    # "We'll explore..." / "We will discuss..."
    re.compile(r"\bwe(?:'ll|'re\s+going\s+to|\s+will)\s+(?:explore|discuss|cover|look\s+at)\b", re.IGNORECASE),
    # "This article covers..." / "This guide explains..."
    re.compile(r"^this\s+(?:article|guide|post|blog)\s+(?:covers|explains|discusses|explores)\b", re.IGNORECASE),
    # "Everything you need to know..."
    re.compile(r"\beverything\s+you\s+need\s+to\s+know\b", re.IGNORECASE),
    # "Here's what you'll learn..."
    re.compile(r"\bhere(?:'s|\s+is)\s+what\s+you(?:'ll|\s+will)\b", re.IGNORECASE),
    # Prompt leakage patterns
    re.compile(r"\bcharacter\s+(?:count|limit)\b", re.IGNORECASE),
    re.compile(r"\bmaximum\s+\d+\s+characters?\b", re.IGNORECASE),
    re.compile(r"\b\d+\s*[-–]\s*\d+\s+characters?\b", re.IGNORECASE),
    re.compile(r"\bseo\s+description\b", re.IGNORECASE),
    re.compile(r"\bmeta\s+description\b", re.IGNORECASE),
    re.compile(r"\bexcerpt\b", re.IGNORECASE),
]


def validate_and_fix_excerpt(
    excerpt: str,
    fallback_excerpt: str = "",
    *,
    min_length: int = 50,
    max_length: int = 160,
    log_issues: bool = True,
) -> tuple[str, bool]:
    """Validate and fix article excerpt/summary text.

    Guards against:
    - Generic boilerplate phrases (e.g. "In this article, we...")
    - Prompt/instruction leakage
    - Text that's too short or too long
    - US spelling (normalizes to Canadian)

    Args:
        excerpt: The excerpt text to validate.
        fallback_excerpt: Fallback text to use if excerpt is invalid/unfixable.
        min_length: Minimum acceptable length.
        max_length: Maximum acceptable length (will truncate at word boundary).
        log_issues: If True, log validation issues at INFO level.

    Returns:
        Tuple of (fixed_excerpt, was_modified).
    """
    if not excerpt or not excerpt.strip():
        if log_issues:
            logger.info("Excerpt guard: empty excerpt, using fallback")
        return fallback_excerpt.strip() or "", True

    original = excerpt.strip()
    result = original
    was_modified = False

    # Check for boilerplate/prompt leakage patterns
    for pattern in _EXCERPT_BOILERPLATE_PATTERNS:
        if pattern.search(result):
            if log_issues:
                logger.info(
                    "Excerpt guard: boilerplate/leakage detected in %r, using fallback",
                    result[:60] + ("…" if len(result) > 60 else ""),
                )
            return fallback_excerpt.strip() or "", True

    # Normalize spelling (flavor -> flavour)
    normalized = normalize_flavor_to_flavour(result, log_changes=False)
    if normalized != result:
        result = normalized
        was_modified = True

    # Check for text that's too short
    if len(result) < min_length:
        if log_issues:
            logger.info(
                "Excerpt guard: too short (%d chars), using fallback: %r",
                len(result),
                result,
            )
        return fallback_excerpt.strip() or "", True

    # Truncate at word boundary if too long
    if len(result) > max_length:
        truncated = result[:max_length]
        # Find last word boundary
        last_space = truncated.rfind(" ")
        if last_space > min_length:
            truncated = truncated[:last_space].rstrip(".,;:!?")
        result = truncated.rstrip()
        was_modified = True
        if log_issues:
            logger.info(
                "Excerpt guard: truncated at word boundary from %d to %d chars",
                len(original),
                len(result),
            )

    return result, was_modified


class _ContentSpans(HTMLParser):
    """Index balanced HTML elements without reserializing untouched markup."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source = source
        self.lines = [0]
        for match in re.finditer('\n', source):
            self.lines.append(match.end())
        self.elements: list[dict[str, Any]] = []
        self.text_nodes: list[tuple[int, int, str]] = []
        self.stack: list[dict[str, Any]] = []
        self.feed(source)
        self.close()

    def source_offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}:
            return
        element = dict(tag=tag, start=self.source_offset(), inner=self.source_offset() + len(self.get_starttag_text()),
                       stop=len(self.source), end=len(self.source), parent=self.stack[-1] if self.stack else None)
        self.elements.append(element)
        self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i]['tag'] == tag:
                end = self.source.find('>', self.source_offset()) + 1
                for element in self.stack[i:]:
                    element['stop'] = self.source_offset()
                    element['end'] = end if element['tag'] == tag else self.source_offset()
                del self.stack[i:]
                break

    def handle_data(self, data):
        if not any(e['tag'] in {'script', 'style'} for e in self.stack):
            start = self.source_offset()
            self.text_nodes.append((start, start + len(data), data))

    def handle_entityref(self, name):
        self._entity('&' + name)

    def handle_charref(self, name):
        self._entity('&#' + name)

    def _entity(self, prefix):
        if any(e['tag'] in {'script', 'style'} for e in self.stack):
            return
        start = self.source_offset()
        raw = prefix + (';' if self.source[start + len(prefix):].startswith(';') else '')
        self.text_nodes.append((start, start + len(raw), html_module.unescape(raw)))


# Both filtering and validation use these same visible units. Container text is
# split around child blocks; inline tags remain part of their parent sentence.
_FAQ_QUESTION_HEADING_TAGS = {'h2', 'h3', 'h4'}

_HEALTH_BLOCK_TAGS = {
    'address', 'article', 'aside', 'blockquote', 'caption', 'dd', 'details', 'div',
    'dl', 'dt', 'fieldset', 'figcaption', 'figure', 'footer', 'form', 'header',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'main', 'nav', 'ol', 'p', 'pre',
    'section', 'summary', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul',
}


def _health_sentences(body: str, *, skip_faq_headings: bool = False):
    parsed = _ContentSpans(body)
    blocks = [e for e in parsed.elements if e['tag'] in _HEALTH_BLOCK_TAGS | {'script', 'style'}]
    children: dict[int, list[dict[str, Any]]] = {}
    for block in blocks:
        parent = block['parent']
        while parent and parent['tag'] not in _HEALTH_BLOCK_TAGS:
            parent = parent['parent']
        children.setdefault(id(parent) if parent else 0, []).append(block)
    units = []
    root = dict(inner=0, stop=len(body))
    for block in [root] + blocks:
        if block.get('tag') in {'script', 'style'} or (skip_faq_headings and block.get('tag') in _FAQ_QUESTION_HEADING_TAGS):
            continue
        cursor = block['inner']
        for child in children.get(0 if block is root else id(block), []):
            units.append((cursor, child['start']))
            cursor = child['end']
        units.append((cursor, block['stop']))
    for start, end in sorted(units):
        nodes = [(a, b, text) for a, b, text in parsed.text_nodes if start <= a and b <= end]
        text = ''.join(node[2] for node in nodes)
        offsets = []
        for a, b, value in nodes:
            if body[a:b] == value:
                offsets.extend((i, i + 1) for i in range(a, b))
            else:
                offsets.extend([(a, b)] * len(value))
        for sentence in re.finditer(r'.+?(?:[.!?](?=\s|$)|$)', text, re.S):
            if sentence.group().strip():
                yield sentence.group(), offsets[sentence.start():sentence.end()]


def article_health_claims(body: str) -> list[str]:
    """The exact sentences the final health filter can remove."""
    return [text.strip() for text, _ in _health_sentences(body) if health_claim_reason(text)]


def _filter_health_sentences(body: str, *, skip_faq_headings: bool = False) -> str:
    spans: list[tuple[int, int]] = []
    for text, offsets in _health_sentences(body, skip_faq_headings=skip_faq_headings):
        if health_claim_reason(text):
            logger.info('Final article filter dropped sentence (reason=health_claim): %r', text.strip()[:160])
            spans.extend(offsets)
    if not spans:
        return body
    merged: list[list[int]] = []
    for start, end in sorted(set(spans)):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        body = body[:start] + body[end:]
    # Text-only deletion keeps inline markup balanced. Remove wrappers emptied
    # by a rejected sentence; structural table/list containers are left intact.
    empty = re.compile(r'(?is)<(p|h[1-6]|dd|dt|figcaption|a|span|strong|em|b|i|u)\b[^>]*>\s*</\1\s*>')
    while True:
        clean = empty.sub('', body)
        if clean == body:
            return body
        body = clean


def _visible_text(fragment: str) -> str:
    fragment = re.sub(r'(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>', '', fragment)
    return re.sub(r'\s+', ' ', html_module.unescape(_TAG_STRIP_RE.sub(' ', fragment))).strip()


def is_question_heading(text: str) -> bool:
    text = normalize_question(text)
    return '?' in text or bool(re.match(r'(?i)^(?:what|why|how|is|are|can|does|do|which|where|when)\b', text))


def article_denied_question_headings(body: str, *, target_brand: str = '') -> list[str]:
    """Visible H2–H4 questions rejected by the final filter's question rules."""
    questions = []
    for heading in _ContentSpans(body).elements:
        if heading['tag'] not in _FAQ_QUESTION_HEADING_TAGS:
            continue
        text = _visible_text(body[heading['inner']:heading['stop']])
        if is_question_heading(text) and question_drop_reason(text, target_brand):
            questions.append(text)
    return questions


def filter_final_article_content(body_html: str, *, target_brand: str = '') -> tuple[str, int, int]:
    """Filter the assembled visible body, returning HTML and before/after question counts.

    Remove health-claim sentences while retaining balanced inline markup.
    Rejected question sections stop at their container or next peer heading.
    FAQ JSON-LD must be rebuilt by the caller *after* this pass.
    """
    body = normalize_flavor_to_flavour(body_html, log_changes=False)
    original_headings = [e for e in _ContentSpans(body).elements if e['tag'] in _FAQ_QUESTION_HEADING_TAGS]
    original_candidate_count = sum(is_question_heading(_visible_text(body[e['inner']:e['stop']])) for e in original_headings)
    body = _filter_health_sentences(body, skip_faq_headings=True)
    parsed = _ContentSpans(body)
    headings = [e for e in parsed.elements if e['tag'] in _FAQ_QUESTION_HEADING_TAGS]
    removals: list[tuple[int, int]] = []
    seen: set[str] = set()
    candidate_starts: list[int] = []

    def removed(start: int) -> bool:
        return any(a <= start < b for a, b in removals)

    def section_end(element):
        parent = element['parent']
        limit = parent['stop'] if parent else len(body)
        return next((h['start'] for h in headings if element['start'] < h['start'] < limit and h['tag'] <= element['tag']), limit)

    for heading in headings:
        text = _visible_text(body[heading['inner']:heading['stop']])
        question = is_question_heading(text)
        if question:
            candidate_starts.append(heading['start'])
        if removed(heading['start']):
            continue
        key = _normalize_for_matching(normalize_spelling_for_comparison(text))
        reason = question_drop_reason(text, target_brand) if question else health_claim_reason(text)
        if not question and heading['tag'] == 'h2' and not reason:
            legacy_match = _match_denylist_category(text)
            if legacy_match and legacy_match[0] != 'reported_question_bait':
                reason = legacy_match[0]
        if question and key in seen:
            reason = 'duplicate_question'
        end = section_end(heading)
        # Reject a question-answer pair when its answer contains a health claim.
        if question and not reason:
            answer = body[heading['end']:end]
            if not _visible_text(answer):
                reason = 'empty_answer_after_filtering'
        if reason:
            logger.info('Final article filter dropped heading (reason=%s): %r', reason, text)
            removals.append((heading['start'], end))
        else:
            seen.add(key)

    remaining = sum(not removed(start) for start in candidate_starts)
    # Merge overlaps before slicing so nested removed blocks cannot corrupt HTML.
    merged: list[list[int]] = []
    for start, end in sorted(removals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    for start, end in reversed(merged):
        body = body[:start] + body[end:]
    # Remove empty FAQ section labels left after their last question was removed.
    body = re.sub(r'(?is)<h2\b[^>]*>\s*(?:FAQ(?:s)?|Frequently asked questions|Helpful questions(?: before you choose)?)\s*</h2>\s*(?=<h2\b|</(?:div|section)>|$)', '', body)
    return body, original_candidate_count, remaining
