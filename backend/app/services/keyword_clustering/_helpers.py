"""Pure utility functions for keyword clustering — no DB or AI dependencies."""
import json
import re
from collections import Counter


def _suggested_match_object_key(suggested_match: dict | None) -> tuple[str, str] | None:
    if not suggested_match:
        return None
    mt = suggested_match.get("match_type")
    mh = (suggested_match.get("match_handle") or "").strip()
    if not mt or mt == "new" or not mh:
        return None
    if mt not in {"product", "collection", "page", "blog_article"}:
        return None
    return mt, mh


def _group_by_parent_topic(
    keywords: list[dict],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Group keywords by parent_topic. Null/empty parent_topic → orphans."""
    groups: dict[str, list[dict]] = {}
    orphans: list[dict] = []
    for kw in keywords:
        topic = kw.get("parent_topic") or ""
        if not topic.strip():
            orphans.append(kw)
        else:
            groups.setdefault(topic.strip(), []).append(kw)
    return groups, orphans


def _serp_feature_labels(sf: object) -> list[str]:
    """Normalize serp_features from target keywords (str, dict, list, or JSON-like)."""
    if sf is None:
        return []
    if isinstance(sf, str):
        return [x.strip() for x in sf.split(",") if x.strip()]
    if isinstance(sf, dict):
        return sorted(str(k) for k, v in sf.items() if v)
    if isinstance(sf, list):
        out: list[str] = []
        for x in sf:
            if x is None:
                continue
            if isinstance(x, str):
                s = x.strip()
                if s:
                    out.append(s)
            elif isinstance(x, dict):
                out.extend(_serp_feature_labels(x))
            else:
                s = str(x).strip()
                if s:
                    out.append(s)
        return out
    return []


def _format_hint_label(fmt: object) -> str:
    if fmt is None:
        return ""
    if isinstance(fmt, str):
        return fmt.strip()
    if isinstance(fmt, list):
        return ", ".join(str(x).strip() for x in fmt if x is not None and str(x).strip())
    return str(fmt).strip()


def _compute_cluster_stats(
    cluster_keywords: list[str], all_keywords_map: dict[str, dict]
) -> dict:
    """Compute aggregate stats for a cluster from keyword metrics."""
    found = [
        all_keywords_map[kw]
        for kw in cluster_keywords
        if kw in all_keywords_map
    ]
    count = len(found)
    if count == 0:
        return {
            "keyword_count": 0,
            "total_volume": 0,
            "avg_difficulty": 0.0,
            "avg_opportunity": 0.0,
            "avg_cps": 0.0,
            "dominant_serp_features": "",
            "content_format_hints": "",
        }
    total_volume = sum(item.get("volume", 0) or 0 for item in found)
    avg_difficulty = round(
        sum(item.get("difficulty", 0) or 0 for item in found) / count, 1
    )
    avg_opportunity = round(
        sum(item.get("opportunity", 0.0) or 0.0 for item in found) / count, 1
    )

    cps_vals = [item.get("cps") or 0.0 for item in found]
    avg_cps = round(sum(cps_vals) / count, 2) if count else 0.0

    serp_counter: Counter[str] = Counter(
        feat
        for item in found
        for feat in _serp_feature_labels(item.get("serp_features"))
    )
    dominant_serp = [feat for feat, _ in serp_counter.most_common(3)]

    fmt_counter: Counter[str] = Counter(
        fmt
        for item in found
        for fmt in [_format_hint_label(item.get("content_format_hint"))]
        if fmt
    )
    content_formats = [fmt for fmt, _ in fmt_counter.most_common(2)]

    return {
        "keyword_count": count,
        "total_volume": total_volume,
        "avg_difficulty": avg_difficulty,
        "avg_opportunity": avg_opportunity,
        "avg_cps": avg_cps,
        "dominant_serp_features": ", ".join(dominant_serp),
        "content_format_hints": ", ".join(content_formats),
    }


def _build_clustering_prompt(
    groups: dict[str, list[dict]], orphans: list[dict], country_name: str = "Canadian"
) -> tuple[str, str]:
    """Build system and user prompts for LLM clustering refinement."""
    system_prompt = (
        f"You are an SEO content strategist for a {country_name} online vape store. "
        "You will receive keyword data organized into preliminary groups (by provider parent topic) "
        "plus a list of ungrouped orphan keywords.\n\n"
        "Your job:\n"
        "1. Assign every orphan keyword to an existing group OR create a new group for it.\n"
        "2. Merge groups that are too similar — they should share one page on the website.\n"
        "3. Each cluster should have 2+ keywords and be focused enough for one page to rank for all of them.\n"
        "4. For each final cluster, provide:\n"
        "   - name: A clear descriptive label (e.g. 'Elf Bar Disposable Vapes')\n"
        "   - content_type: One of 'collection_page', 'product_page', 'blog_post', 'buying_guide', 'landing_page'\n"
        "   - primary_keyword: The single keyword with the highest search opportunity in the cluster\n"
        "   - content_brief: 1-2 sentences describing what the page should cover and its target intent\n"
        "   - keywords: Array of all keyword strings in the cluster\n\n"
        "Some keywords include extra signals: cps (clicks-per-search), format_hint (best content format for SERP), "
        "and serp (dominant SERP features). Use these to pick better content_type and write richer content_brief.\n\n"
        "Return ONLY the structured JSON. Do not include any keywords that were not provided in the input."
    )

    def _kw_fields(kw: dict) -> dict:
        d = {
            "keyword": kw.get("keyword", ""),
            "volume": kw.get("volume", 0),
            "difficulty": kw.get("difficulty", 0),
            "opportunity": kw.get("opportunity", 0.0),
            "intent": kw.get("intent", ""),
            "content_type": kw.get("content_type", ""),
            "ranking_status": kw.get("ranking_status"),
        }
        if kw.get("cps"):
            d["cps"] = kw["cps"]
        if kw.get("content_format_hint"):
            d["format_hint"] = kw["content_format_hint"]
        if kw.get("serp_features"):
            d["serp"] = kw["serp_features"]
        return d

    payload = {
        "groups": {
            topic: [_kw_fields(kw) for kw in kws]
            for topic, kws in groups.items()
        },
        "orphans": [_kw_fields(kw) for kw in orphans],
    }

    user_prompt = (
        "Here are the keyword groups and orphans to cluster:\n\n"
        + json.dumps(payload, indent=2)
    )
    return system_prompt, user_prompt


def _keyword_present_in_clean_text(kw: str, clean: str) -> bool:
    """True if the full keyword phrase appears as a substring (case-insensitive *clean* is lowercased)."""
    nk = (kw or "").strip().lower()
    if not nk:
        return False
    return nk in clean


def _keyword_coverage_detail(keywords: list[str], content: str) -> dict[str, object]:
    """Per-keyword coverage for auditing UI. Exact phrase match only (substring after HTML strip)."""
    total = len(keywords)
    if not keywords:
        return {"found": 0, "total": 0, "keywords_found": [], "keywords_missing": []}
    if not content:
        return {"found": 0, "total": total, "keywords_found": [], "keywords_missing": list(keywords)}

    clean = re.sub(r"<[^>]+>", " ", content).lower()
    found_list = [kw for kw in keywords if _keyword_present_in_clean_text(kw, clean)]
    found_set = set(found_list)
    missing_list = [kw for kw in keywords if kw not in found_set]
    return {
        "found": len(found_list),
        "total": total,
        "keywords_found": found_list,
        "keywords_missing": missing_list,
    }


def _check_keyword_coverage(keywords: list[str], content: str) -> tuple[int, int]:
    """Check how many cluster keywords appear as exact phrases in content (substring match)."""
    d = _keyword_coverage_detail(keywords, content)
    return int(d["found"]), int(d["total"])


def _detect_vendor(
    cluster_name: str,
    cluster_keywords: list[str],
    vendor_map: dict[str, dict],
) -> dict | None:
    """Detect if a cluster matches a product vendor/brand.

    Checks if any vendor name (lowercased key) appears as a substring in the
    cluster name or any of its keywords. Returns the vendor info dict or None.
    """
    name_lower = cluster_name.lower()
    kws_lower = [kw.lower() for kw in cluster_keywords]
    for vendor_lower, vendor_info in vendor_map.items():
        if vendor_lower in name_lower or any(vendor_lower in kw for kw in kws_lower):
            return vendor_info
    return None
