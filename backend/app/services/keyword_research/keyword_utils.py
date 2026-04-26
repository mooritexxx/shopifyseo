"""Keyword scoring, classification, and data processing helpers."""

import math

INTENT_PRIORITY = ["transactional", "commercial", "local", "informational", "navigational", "branded"]
INTENT_TO_CONTENT = {
    "transactional": "Product / Collection page",
    "commercial": "Comparison / Buying guide",
    "local": "Local landing page",
    "informational": "Blog / Guide",
    "navigational": "Brand page",
    "branded": "Brand page",
}

SERP_FORMAT_MAP = {
    "featured_snippet": "direct_answer",
    "people_also_ask": "faq",
    "video": "video_embed",
    "shopping": "product_page",
    "knowledge_panel": "entity_page",
    "image_pack": "visual_guide",
    "local_pack": "local_page",
    "top_stories": "news_article",
}


def derive_content_format_hint(serp_features: dict | None, intent: str = "") -> str:
    """Derive a content format recommendation from SERP feature counts."""
    if not serp_features or not isinstance(serp_features, dict):
        return ""
    best_feature = ""
    best_count = 0
    for feature, count in serp_features.items():
        if isinstance(count, (int, float)) and count > best_count:
            mapped = SERP_FORMAT_MAP.get(feature)
            if mapped:
                best_feature = mapped
                best_count = count
    return best_feature


def compact_serp_features(serp_features: dict | None) -> str:
    """Serialize SERP features dict as compact comma-separated string."""
    if not serp_features or not isinstance(serp_features, dict):
        return ""
    return ", ".join(sorted(k for k, v in serp_features.items() if v))


def classify_ranking_status(position: float | None) -> str:
    if position is None:
        return "not_ranking"
    if position <= 10:
        return "ranking"
    if position <= 20:
        return "quick_win"
    if position <= 50:
        return "striking_distance"
    return "low_visibility"


def match_gsc_queries(keyword: str, gsc_data: dict[str, dict]) -> dict | None:
    """Match a keyword against aggregated GSC queries.

    Uses exact match first, then containment matching: ALL content words
    of the shorter phrase must appear in the longer phrase. Stop words are
    excluded to prevent false matches on common words.

    Args:
        keyword: Target keyword string.
        gsc_data: Dict mapping lowercase query to {"position": float, "clicks": int, "impressions": int}

    Returns:
        {"position": best_pos, "clicks": total_clicks, "impressions": total_imps} or None.
    """
    stop_words = {
        "a", "an", "the", "is", "it", "in", "on", "at", "to", "of", "for",
        "and", "or", "but", "not", "with", "by", "from", "as", "be", "was",
        "are", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "can", "this",
        "that", "what", "which", "who", "how", "when", "where", "why",
        "all", "each", "every", "both", "few", "more", "most", "other",
        "some", "such", "no", "nor", "too", "very", "just", "about",
        "up", "out", "so", "if", "then", "than", "also", "into",
    }
    kw = keyword.lower()
    kw_words = set(kw.split())
    matches: list[dict] = []
    for query, metrics in gsc_data.items():
        if kw == query:
            matches.append(metrics)
            continue
        query_words = set(query.split())
        # Remove stop words before computing containment
        kw_content = kw_words - stop_words
        query_content = query_words - stop_words
        # If either side has no content words, skip
        if not kw_content or not query_content:
            continue
        # Containment: ALL content words of the shorter phrase must
        # appear in the longer phrase. This prevents "thc vape juice"
        # from matching "elfbar vape canada" via shared {vape}.
        shorter, longer = (
            (kw_content, query_content)
            if len(kw_content) <= len(query_content)
            else (query_content, kw_content)
        )
        if len(shorter) >= 2 and shorter.issubset(longer):
            matches.append(metrics)
    if not matches:
        return None
    best_position = min(m["position"] for m in matches)
    total_clicks = sum(m["clicks"] for m in matches)
    total_impressions = sum(m["impressions"] for m in matches)
    return {
        "position": best_position,
        "clicks": total_clicks,
        "impressions": total_impressions,
    }


def _num(value: int | float | str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bounded_log_score(value: int | float | str | None, reference: float) -> float:
    v = max(_num(value), 0.0)
    if v <= 0:
        return 0.0
    return min(100.0, math.log1p(v) / math.log1p(reference) * 100.0)


def _difficulty_ease_score(difficulty: int | float | str | None) -> float:
    if difficulty is None:
        return 50.0
    d = max(0.0, min(100.0, _num(difficulty, 50.0)))
    return 100.0 - d


def _intent_opportunity_score(intent: str | None) -> float:
    match (intent or "").strip().lower():
        case "transactional":
            return 100.0
        case "commercial":
            return 95.0
        case "local":
            return 90.0
        case "informational":
            return 70.0
        case "branded":
            return 50.0
        case "navigational":
            return 35.0
        case _:
            return 65.0


def _ranking_opportunity_score(
    ranking_status: str | None,
    gsc_position: int | float | str | None,
) -> float:
    status = (ranking_status or "").strip().lower()
    if not status and gsc_position is not None:
        pos = _num(gsc_position, -1.0)
        status = classify_ranking_status(pos) if pos > 0 else "not_ranking"
    return {
        "quick_win": 100.0,
        "striking_distance": 85.0,
        "low_visibility": 65.0,
        "not_ranking": 55.0,
        "ranking": 45.0,
    }.get(status, 55.0)


def compute_opportunity(
    volume: int | float | None,
    traffic_potential: int | float | None,
    difficulty: int | float | None,
    *,
    intent: str | None = None,
    ranking_status: str | None = None,
    gsc_position: int | float | None = None,
) -> float:
    """Return an un-normalized 0-100 keyword opportunity score.

    Demand and traffic potential are log-scaled so one head term does not
    flatten the whole keyword set. Difficulty is an ease signal, with missing
    KD treated as neutral instead of "free." GSC ranking and intent make the
    score useful for prioritizing actual SEO work, not just theoretical volume.
    """
    v = max(_num(volume), 0.0)
    if v <= 0:
        return 0.0
    tp = max(_num(traffic_potential, v), 0.0) or v
    demand_score = _bounded_log_score(v, 10000.0)
    traffic_score = _bounded_log_score(tp, 10000.0)
    ease_score = _difficulty_ease_score(difficulty)
    ranking_score = _ranking_opportunity_score(ranking_status, gsc_position)
    intent_score = _intent_opportunity_score(intent)
    return round(
        (0.35 * demand_score)
        + (0.20 * traffic_score)
        + (0.20 * ease_score)
        + (0.15 * ranking_score)
        + (0.10 * intent_score),
        4,
    )


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) < 20:
        return ordered[-1]
    idx = (len(ordered) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[int(idx)]
    frac = idx - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def normalize_opportunity_scores(items: list[dict]) -> None:
    if not items:
        return
    raw_values = [
        max(_num(item.get("opportunity_raw")), 0.0)
        for item in items
        if _num(item.get("opportunity_raw")) > 0
    ]
    max_raw = max(raw_values) if raw_values else 0.0
    cap = 100.0 if max_raw <= 100.0 else _percentile(raw_values, 0.95)
    for item in items:
        raw = max(_num(item.get("opportunity_raw")), 0.0)
        item["opportunity"] = round(min(raw / cap, 1.0) * 100, 1) if cap > 0 else 0.0


def recompute_opportunity_scores(items: list[dict]) -> None:
    """Rebuild raw and normalized opportunity scores for a keyword list in place."""
    for item in items:
        item["opportunity_raw"] = compute_opportunity(
            volume=item.get("volume") or 0,
            traffic_potential=item.get("traffic_potential"),
            difficulty=item.get("difficulty"),
            intent=item.get("intent"),
            ranking_status=item.get("ranking_status"),
            gsc_position=item.get("gsc_position"),
        )
    normalize_opportunity_scores(items)
    for item in items:
        item.pop("opportunity_raw", None)


def classify_intent(intents: dict | None) -> tuple[str, str]:
    if not intents:
        return "informational", INTENT_TO_CONTENT["informational"]
    for intent_key in INTENT_PRIORITY:
        is_prefixed = f"is_{intent_key}" if intent_key != "branded" else "is_branded"
        if intents.get(intent_key) or intents.get(is_prefixed):
            return intent_key, INTENT_TO_CONTENT[intent_key]
    return "informational", INTENT_TO_CONTENT["informational"]


def _merge_serp_features(a: dict | None, b: dict | None) -> dict | None:
    """Merge two SERP feature dicts, keeping the max count per feature."""
    if not a and not b:
        return None
    merged = dict(a or {})
    for k, v in (b or {}).items():
        if isinstance(v, (int, float)):
            merged[k] = max(merged.get(k, 0), v)
    return merged or None


def deduplicate_results(raw_items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in raw_items:
        key = item["keyword"].lower()
        if key in seen:
            existing = seen[key]
            if item.get("volume", 0) > existing.get("volume", 0):
                seeds = existing.get("seed_keywords", set())
                seeds.update(item.get("seed_keywords", set()))
                item["seed_keywords"] = seeds
                item["serp_features"] = _merge_serp_features(
                    item.get("serp_features"), existing.get("serp_features")
                )
                for field in ("competitor_domain", "competitor_position", "best_position_url", "best_position_kind"):
                    if not item.get(field) and existing.get(field):
                        item[field] = existing[field]
                if existing.get("source_endpoint") == "site_explorer" and item.get("source_endpoint") != "site_explorer":
                    item.setdefault("competitor_domain", existing.get("competitor_domain"))
                    item.setdefault("competitor_position", existing.get("best_position"))
                    item.setdefault("competitor_url", existing.get("best_position_url"))
                seen[key] = item
            else:
                existing.setdefault("seed_keywords", set()).update(item.get("seed_keywords", set()))
                existing["serp_features"] = _merge_serp_features(
                    existing.get("serp_features"), item.get("serp_features")
                )
                for field in ("competitor_domain", "competitor_position", "best_position_url", "best_position_kind"):
                    if not existing.get(field) and item.get(field):
                        existing[field] = item[field]
                if item.get("source_endpoint") == "site_explorer" and existing.get("source_endpoint") != "site_explorer":
                    existing.setdefault("competitor_domain", item.get("competitor_domain"))
                    existing.setdefault("competitor_position", item.get("best_position"))
                    existing.setdefault("competitor_url", item.get("best_position_url"))
        else:
            item["seed_keywords"] = set(item.get("seed_keywords", set()))
            seen[key] = item
    result = list(seen.values())
    for item in result:
        item["seed_keywords"] = sorted(item["seed_keywords"])
    return result


def merge_with_existing(existing: list[dict], new_items: list[dict]) -> list[dict]:
    existing_map = {item["keyword"].lower(): item for item in existing}
    new_keys: set[str] = set()
    merged = []
    for item in new_items:
        key = item["keyword"].lower()
        new_keys.add(key)
        if key in existing_map:
            item["status"] = existing_map[key]["status"]
        merged.append(item)
    # Keep existing keywords that didn't appear in new results
    for item in existing:
        if item["keyword"].lower() not in new_keys:
            merged.append(item)
    return merged


def batch_seeds(seeds: list[str], batch_size: int = 5) -> list[list[str]]:
    """Chunk seed strings for keyword Labs API calls."""
    return [seeds[i : i + batch_size] for i in range(0, len(seeds), batch_size)]
