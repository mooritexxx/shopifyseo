"""Scoring helpers for cluster priority and primary-keyword selection."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping


_WORD_RE = re.compile(r"[a-z0-9]+")


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
) -> float:
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
    return round(
        (0.45 * opp)
        + (0.25 * centrality)
        + (0.15 * volume_score)
        + (0.10 * fit)
        + (0.05 * ai_bonus),
        4,
    )


def select_primary_keyword(
    cluster_keywords: list[str],
    keywords_map: Mapping[str, dict],
    *,
    ai_primary: str = "",
    content_type: str = "",
    centrality_scores: Mapping[str, float] | None = None,
) -> str:
    """Pick the best representative target keyword for a cluster."""
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
