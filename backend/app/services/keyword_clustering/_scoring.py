"""Scoring helpers for cluster priority and primary-keyword selection."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping


_WORD_RE = re.compile(r"[a-z0-9]+")

# Geo / location patterns that indicate a keyword is a store location, mall, or
# geographic query rather than a brand head term. These penalize primary selection.
GEO_NOISE_PATTERNS: frozenset[str] = frozenset({
    "near me",
    "near by",
    "nearby",
    "store",
    "stores",
    "shop",
    "shops",
    "location",
    "locations",
    "montreal",
    "toronto",
    "vancouver",
    "calgary",
    "ottawa",
    "winnipeg",
    "edmonton",
    "quebec",
    "laval",
    "mississauga",
    "brampton",
    "hamilton",
    "london",
    "markham",
    "surrey",
    "burnaby",
    "richmond",
    "dix30",  # Quartier DIX30 mall
    "carrefour",
    "mall",
    "plaza",
    "centre",
    "center",
})

# Homonym / false-friend patterns: keywords that look like brand terms but are
# actually radio stations, video games, or other unrelated topics.
HOMONYM_NOISE_PATTERNS: frozenset[str] = frozenset({
    # Radio stations
    "101.3",
    "101 3",
    "fm",
    "radio",
    "station",
    "playlist",
    "hit",
    "hits",
    # Video games
    "game",
    "games",
    "gaming",
    "xbox",
    "playstation",
    "ps4",
    "ps5",
    "steam",
    "call of duty",
    "fortnite",
    "apex",
    "valorant",
    "warzone",
    "cod",
    "sniper elite",  # Video game title (SNIPER brand homonym)
    "elite sniper",
    # Media / entertainment
    "movie",
    "movies",
    "film",
    "tv",
    "show",
    "shows",
    "episode",
    "season",
    "youtube",
    "tiktok",
    "instagram",
    # Years as titles (not product models)
    "2020",
    "2021",
    "2022",
    "2023",
    "2024",
    "2025",
    "2026",
})

# Role-aligned keyword patterns: when cluster has a specific role, prefer
# keywords that match these patterns for that role.
ROLE_KEYWORD_PATTERNS: dict[str, frozenset[str]] = {
    "flavours": frozenset({
        "flavour",
        "flavours",
        "flavor",
        "flavors",
        "taste",
        "tastes",
    }),
    "pods": frozenset({
        "pod",
        "pods",
        "cartridge",
        "cartridges",
        "refill",
        "refills",
    }),
    "product_model": frozenset({
        # Model numbers look like alphanumeric codes
    }),
    "price": frozenset({
        "price",
        "prices",
        "cost",
        "cheap",
        "cheapest",
        "deal",
        "deals",
        "discount",
        "sale",
    }),
    "troubleshooting": frozenset({
        "not working",
        "won t",
        "wont",
        "blinking",
        "error",
        "not charging",
        "how to",
        "fix",
        "problem",
    }),
}

# Model string pattern: alphanumeric codes that look like product SKUs
_MODEL_PATTERN = re.compile(
    r"\b(?:[a-z]{1,4}\d{1,5}[a-z]?|\d{2,3}k|bc\s?pro|level\s?x|g[234]|xlim|xros)\b",
    re.I,
)


def _num(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _log_score(value: object, reference: float) -> float:
    v = max(_num(value), 0.0)
    if v <= 0:
        return 0.0
    return min(100.0, math.log1p(v) / math.log1p(reference) * 100.0)


def _keyword_tokens(keyword: str) -> set[str]:
    return set(_WORD_RE.findall((keyword or "").lower()))


def _normalize_text(text: str) -> str:
    """Lowercase and normalize whitespace."""
    return " ".join((text or "").lower().split())


def _text_contains_any(text: str, patterns: frozenset[str]) -> bool:
    """Check if normalized text contains any pattern (word boundary aware)."""
    norm = _normalize_text(text)
    if not norm:
        return False
    for p in patterns:
        p_words = p.split()
        if len(p_words) == 1:
            if re.search(rf"\b{re.escape(p)}\b", norm):
                return True
        else:
            if p in norm:
                return True
    return False


def _has_geo_noise(keyword: str) -> bool:
    """Check if keyword has geo/location signals making it a poor brand head term."""
    return _text_contains_any(keyword, GEO_NOISE_PATTERNS)


def _has_homonym_noise(keyword: str) -> bool:
    """Check if keyword has homonym/false-friend signals (radio, games, media)."""
    return _text_contains_any(keyword, HOMONYM_NOISE_PATTERNS)


def _matches_entity(keyword: str, detected_entity: str) -> bool:
    """Check if keyword clearly contains the detected entity (brand match)."""
    if not detected_entity:
        return False
    entity_norm = _normalize_text(detected_entity)
    keyword_norm = _normalize_text(keyword)
    if not entity_norm or not keyword_norm:
        return False
    # Check entity as word boundary match
    entity_words = entity_norm.split()
    if len(entity_words) == 1:
        return bool(re.search(rf"\b{re.escape(entity_norm)}\b", keyword_norm))
    # Multi-word entity: check if all words appear
    return entity_norm in keyword_norm or all(
        re.search(rf"\b{re.escape(w)}\b", keyword_norm) for w in entity_words
    )


def _role_alignment_score(keyword: str, cluster_role: str) -> float:
    """Score how well keyword aligns with the cluster's semantic role.

    Returns 0-100 where 100 = perfect alignment, 0 = no alignment signals.
    For specific roles like flavours/pods/troubleshooting, keywords containing
    role-specific terms get a significant boost.
    """
    if not cluster_role or cluster_role == "generic":
        return 50.0  # Neutral for generic clusters

    keyword_norm = _normalize_text(keyword)

    # Product model role: prefer alphanumeric model codes
    if cluster_role == "product_model":
        if _MODEL_PATTERN.search(keyword_norm):
            return 100.0  # Strong preference for actual model codes
        return 30.0  # Significant penalty for no model code

    # Check role-specific patterns - strong preference
    patterns = ROLE_KEYWORD_PATTERNS.get(cluster_role)
    if patterns:
        if _text_contains_any(keyword_norm, patterns):
            return 100.0  # Perfect alignment with role
        # Significant penalty for not matching role-specific terms
        if cluster_role in {"flavours", "pods", "price", "troubleshooting"}:
            return 25.0  # Keywords without role terms score low

    # Brand/category collection roles: prefer shorter, cleaner brand terms
    if cluster_role in {"brand_collection", "category_collection"}:
        word_count = len(keyword_norm.split())
        if word_count <= 3:
            return 75.0
        if word_count <= 5:
            return 55.0
        return 35.0  # Long keywords are worse for brand head terms

    return 50.0  # Neutral


def _lexical_centrality_scores(keywords: list[str]) -> dict[str, float]:
    tokens_by_kw = {kw.lower(): _keyword_tokens(kw) for kw in keywords}
    out: dict[str, float] = {}
    for kw in keywords:
        key = kw.lower()
        tokens = tokens_by_kw.get(key) or set()
        if not tokens or len(keywords) <= 1:
            out[key] = 100.0
            continue
        sims: list[float] = []
        for other, other_tokens in tokens_by_kw.items():
            if other == key or not other_tokens:
                continue
            union = tokens | other_tokens
            sims.append(len(tokens & other_tokens) / len(union) if union else 0.0)
        out[key] = round((sum(sims) / len(sims)) * 100.0, 2) if sims else 0.0
    return out


def _content_type_intent_fit(content_type: str | None, intent: str | None) -> float:
    ct = (content_type or "").strip().lower()
    it = (intent or "").strip().lower()
    if not ct or not it:
        return 65.0
    if ct in {"collection_page", "product_page"}:
        return {
            "transactional": 100.0,
            "commercial": 92.0,
            "local": 80.0,
            "informational": 45.0,
            "branded": 70.0,
            "navigational": 40.0,
        }.get(it, 65.0)
    if ct == "buying_guide":
        return {
            "commercial": 100.0,
            "informational": 88.0,
            "transactional": 75.0,
            "local": 65.0,
            "branded": 55.0,
            "navigational": 40.0,
        }.get(it, 65.0)
    if ct == "blog_post":
        return {
            "informational": 100.0,
            "commercial": 82.0,
            "local": 70.0,
            "transactional": 55.0,
            "branded": 50.0,
            "navigational": 40.0,
        }.get(it, 65.0)
    if ct == "landing_page":
        return {
            "local": 100.0,
            "commercial": 88.0,
            "transactional": 84.0,
            "informational": 65.0,
            "branded": 60.0,
            "navigational": 45.0,
        }.get(it, 65.0)
    return 65.0


def _primary_keyword_score(
    keyword: str,
    keywords_map: Mapping[str, dict],
    *,
    ai_primary: str = "",
    content_type: str = "",
    centrality_scores: Mapping[str, float] | None = None,
    detected_entity: str = "",
    cluster_role: str = "",
) -> float:
    """Score a keyword's suitability as the cluster's primary keyword.

    When detected_entity is provided, strongly prefers short brand head terms
    that match the entity over geo/homonym-polluted long-tail variants.

    Weight distribution (adjusted based on entity presence):
    - With detected_entity: entity_fit=30%, centrality=25%, role_alignment=15%,
      opportunity=12%, volume=8%, content_fit=5%, ai_bonus=5%
    - Without entity: opportunity=38%, centrality=28%, volume=14%,
      content_fit=10%, ai_bonus=5%, role_alignment=5%
    """
    key = keyword.lower()
    metrics = keywords_map.get(key, {})
    opp = max(_num(metrics.get("opportunity")), 0.0)
    volume_score = _log_score(metrics.get("volume"), 10000.0)
    centrality = (
        _num((centrality_scores or {}).get(key), 65.0)
        if centrality_scores is not None
        else _lexical_centrality_scores([keyword]).get(key, 65.0)
    )
    fit = _content_type_intent_fit(content_type, metrics.get("intent"))
    ai_bonus = 100.0 if ai_primary and key == ai_primary.lower().strip() else 0.0

    # Calculate entity fit and noise penalties
    entity_fit = 50.0  # Neutral default
    noise_penalty = 0.0

    if detected_entity:
        # When we have a detected entity, heavily weight entity match
        if _matches_entity(keyword, detected_entity):
            entity_fit = 100.0
            # Bonus for shorter, cleaner brand terms
            word_count = len(keyword.split())
            if word_count <= 2:
                entity_fit = 100.0
            elif word_count <= 4:
                entity_fit = 85.0
            else:
                entity_fit = 70.0  # Long tail with entity still okay but not ideal
        else:
            entity_fit = 15.0  # Heavy penalty for not matching entity

        # Penalize geo/homonym noise when entity is present
        if _has_geo_noise(keyword):
            noise_penalty += 35.0
        if _has_homonym_noise(keyword):
            noise_penalty += 40.0

    # Role alignment score
    role_score = _role_alignment_score(keyword, cluster_role)

    # Apply noise penalty to entity_fit
    entity_fit = max(0.0, entity_fit - noise_penalty)

    # Weight distribution depends on whether we have entity context
    if detected_entity:
        # Entity-aware scoring: prioritize entity match and clean brand terms
        return round(
            (0.30 * entity_fit)
            + (0.25 * centrality)
            + (0.15 * role_score)
            + (0.12 * opp)
            + (0.08 * volume_score)
            + (0.05 * fit)
            + (0.05 * ai_bonus),
            4,
        )
    else:
        # No entity: use opportunity-weighted scoring but include role alignment
        return round(
            (0.38 * opp)
            + (0.28 * centrality)
            + (0.14 * volume_score)
            + (0.10 * fit)
            + (0.05 * ai_bonus)
            + (0.05 * role_score),
            4,
        )


def select_primary_keyword(
    cluster_keywords: list[str],
    keywords_map: Mapping[str, dict],
    *,
    ai_primary: str = "",
    content_type: str = "",
    centrality_scores: Mapping[str, float] | None = None,
    detected_entity: str = "",
    cluster_role: str = "",
) -> str:
    """Pick the best representative target keyword for a cluster.

    When detected_entity is provided, strongly prefers short brand head terms
    that match the entity over geo/homonym-polluted long-tail variants. This
    prevents issues like selecting "allo mon coco dix30" (a mall location)
    over "allo vape" for an ALLO brand cluster.
    """
    candidates = [
        kw for kw in cluster_keywords
        if kw and kw.lower() in keywords_map
    ] or [kw for kw in cluster_keywords if kw]
    if not candidates:
        return ai_primary.strip()
    if centrality_scores is None:
        centrality_scores = _lexical_centrality_scores(candidates)
    return max(
        candidates,
        key=lambda kw: (
            _primary_keyword_score(
                kw,
                keywords_map,
                ai_primary=ai_primary,
                content_type=content_type,
                centrality_scores=centrality_scores,
                detected_entity=detected_entity,
                cluster_role=cluster_role,
            ),
            _num(keywords_map.get(kw.lower(), {}).get("opportunity")),
            _num(keywords_map.get(kw.lower(), {}).get("volume")),
        ),
    )


def cluster_priority_score(cluster_keywords: list[str], keywords_map: Mapping[str, dict]) -> float:
    """Rank clusters by practical SEO value without letting long-tail averages dominate."""
    found = [
        keywords_map[kw.lower()]
        for kw in cluster_keywords
        if kw.lower() in keywords_map
    ]
    if not found:
        return 0.0

    opps = sorted((max(_num(item.get("opportunity")), 0.0) for item in found), reverse=True)
    top_opp = opps[0] if opps else 0.0
    top_three_avg = sum(opps[:3]) / min(len(opps), 3)
    total_volume = sum(max(_num(item.get("volume")), 0.0) for item in found)
    demand_score = _log_score(total_volume, 50000.0)
    quick_win_bonus = 100.0 if any(
        (item.get("ranking_status") or "").lower() in {"quick_win", "striking_distance"}
        for item in found
    ) else 0.0
    depth_score = min(100.0, math.log1p(len(found)) / math.log1p(12.0) * 100.0)

    return round(
        (0.40 * top_opp)
        + (0.25 * top_three_avg)
        + (0.20 * demand_score)
        + (0.10 * quick_win_bonus)
        + (0.05 * depth_score),
        1,
    )
