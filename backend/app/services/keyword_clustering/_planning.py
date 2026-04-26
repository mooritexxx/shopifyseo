"""SEO-first cluster planning helpers.

This module turns a raw keyword cluster into a content-ready topic plan:
entity-safe membership, dominant intent/role, quality diagnostics, keyword
tiers, and cannibalization risk.  It is deliberately deterministic so it can
repair noisy LLM output before clusters are saved or used for generation.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter, defaultdict
from typing import Any

from ._helpers import _compute_cluster_stats
from ._scoring import cluster_priority_score, select_primary_keyword

MAX_CLUSTER_KEYWORDS = {
    "collection_page": 40,
    "product_page": 40,
    "buying_guide": 25,
    "blog_post": 25,
    "landing_page": 20,
}

CORE_KEYWORD_LIMIT = 10
SUPPORTING_KEYWORD_LIMIT = 20
EXTENDED_KEYWORD_LIMIT = 80

_WORD_RE = re.compile(r"[a-z0-9]+")
_MODEL_RE = re.compile(r"\b(?:[a-z]{1,4}\d{1,5}[a-z]?|\d{2,3}k|bc\s?pro|level\s?x|g[234]|xlim|xros)\b", re.I)

STATIC_ENTITY_ALIASES: dict[str, list[str]] = {
    "ELFBAR": ["elfbar", "elf bar", "elf bars", "elk bar", "elk bars"],
    "Geek Bar": ["geek bar", "geekbar", "geek bars"],
    "STLTH": ["stlth", "stlths"],
    "STLTH x GEEK BAR": ["stlth x geek bar", "stlth geek bar"],
    "Caliburn": ["caliburn", "uwell caliburn"],
    "Flavour Beast": ["flavour beast", "flavor beast"],
    "Vuse": ["vuse"],
    "ALLO": ["allo"],
    "ABT": ["abt"],
    "Fog Formulas": ["fog formulas", "fog formula"],
    "OXBAR": ["oxbar"],
    "OXVA": ["oxva"],
    "Uwell": ["uwell"],
    "Vaporesso": ["vaporesso"],
}

_GENERIC_COLLECTION_TERMS = {
    "accessories",
    "coils",
    "deals",
    "disposable",
    "disposables",
    "vapes",
    "vape",
    "e-liquid",
    "liquid",
    "pods",
    "pod",
    "systems",
    "batteries",
    "battery",
    "new",
    "arrivals",
    "replacement",
    "rechargeable",
    "salt",
    "nic",
    "freebase",
    "canada",
    "20mg",
}

COMPARISON_TERMS = {
    "vs",
    "versus",
    "compare",
    "comparison",
    "alternative",
    "alternatives",
    "best",
    "top",
    "brands",
}

ROLE_LABELS = {
    "brand_collection": "Product Collection",
    "category_collection": "Category Collection",
    "product_model": "Product Models",
    "flavours": "Flavours",
    "pods": "Pods and Cartridges",
    "price": "Prices and Deals",
    "troubleshooting": "Troubleshooting",
    "comparison": "Comparison",
    "local": "Local Shopping",
    "faq": "FAQ",
    "review": "Reviews",
    "buying_guide": "Buying Guide",
    "generic": "Topic",
}

_COLLECTION_COMPATIBLE_ROLES = {
    "brand_collection",
    "category_collection",
    "product_model",
    "flavours",
    "pods",
    "price",
}

_BLOG_ROLES = {"troubleshooting", "faq", "review"}


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _norm_key(text: str) -> str:
    return " ".join(_tokens(text))


def _json_dumps(value: object) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def _json_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        if not value.strip():
            return []
        try:
            raw = json.loads(value)
        except json.JSONDecodeError:
            return []
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            text = str(item.get("keyword") or item.get("query") or "").strip()
        else:
            text = str(item or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def parse_keyword_tier(value: object) -> list[str]:
    """Parse a stored keyword-tier JSON value into keyword strings."""
    return _json_list(value)


def _alias_variants(name: str) -> set[str]:
    norm = _norm_key(name)
    variants = {norm} if norm else set()
    compact = norm.replace(" ", "")
    if compact and compact != norm:
        variants.add(compact)
    if "elfbar" in variants:
        variants.update({"elf bar", "elf bars"})
    if "geekbar" in variants:
        variants.update({"geek bar", "geek bars"})
    return variants


def _collection_entity_candidate(title: str) -> str:
    text = (title or "").strip()
    if not text:
        return ""
    first = re.split(r"\s+-\s+|\s+(?:disposable|vapes|e-liquids?|pods|batteries|devices)\b", text, maxsplit=1, flags=re.I)[0]
    first = first.strip(" -")
    toks = _tokens(first)
    if not toks or len(toks) > 4:
        return ""
    if all(t in _GENERIC_COLLECTION_TERMS for t in toks):
        return ""
    if any(t.isdigit() for t in toks):
        return ""
    return first


def load_entity_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Build brand/entity aliases from static known terms plus Shopify catalog data."""
    rules: dict[str, dict[str, Any]] = {}

    def add(display: str, aliases: list[str] | None = None, source: str = "catalog") -> None:
        display = (display or "").strip()
        key = _norm_key(display)
        if not key or len(key) < 3:
            return
        rule = rules.setdefault(key, {"key": key, "display": display, "aliases": set(), "source": source})
        rule["display"] = rule.get("display") or display
        rule["aliases"].update(_alias_variants(display))
        for alias in aliases or []:
            rule["aliases"].update(_alias_variants(alias))

    for display, aliases in STATIC_ENTITY_ALIASES.items():
        add(display, aliases, source="static")

    try:
        for row in conn.execute("SELECT DISTINCT vendor FROM products WHERE COALESCE(vendor, '') != ''"):
            add(str(row[0] or ""), source="vendor")
    except Exception:
        pass

    try:
        for row in conn.execute("SELECT title FROM collections WHERE COALESCE(title, '') != ''"):
            candidate = _collection_entity_candidate(str(row[0] or ""))
            if candidate:
                add(candidate, source="collection")
    except Exception:
        pass

    out: list[dict[str, Any]] = []
    for rule in rules.values():
        aliases = sorted(rule["aliases"], key=lambda x: (-len(x), x))
        if aliases:
            out.append({**rule, "aliases": aliases})
    out.sort(key=lambda r: max(len(a) for a in r["aliases"]), reverse=True)
    return out


def _alias_matches(text: str, alias: str) -> bool:
    alias = _norm_key(alias)
    if not alias:
        return False
    if len(alias) <= 3:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text))
    pattern = r"(?<![a-z0-9])" + r"\s+".join(re.escape(p) for p in alias.split()) + r"s?(?![a-z0-9])"
    return bool(re.search(pattern, text))


def detect_entities(text: str, entity_rules: list[dict[str, Any]]) -> list[str]:
    """Return detected entity display names for keyword text."""
    norm = _norm_key(text)
    if not norm:
        return []
    matches: list[str] = []
    matched_keys: set[str] = set()
    for rule in entity_rules:
        if rule["key"] in matched_keys:
            continue
        if any(_alias_matches(norm, alias) for alias in rule["aliases"]):
            matched_keys.add(rule["key"])
            matches.append(rule["display"])

    # Treat explicit collab terms as their own entity unless this is a comparison query.
    lower_matches = {_norm_key(m): m for m in matches}
    has_collab = "stlth x geek bar" in lower_matches
    if has_collab and not _has_comparison_signal(norm):
        return [lower_matches["stlth x geek bar"]]
    return matches


def _has_comparison_signal(text: str) -> bool:
    toks = set(_tokens(text))
    return bool(toks & COMPARISON_TERMS) or " vs " in f" {text.lower()} "


def keyword_role(keyword: str, metrics: dict | None = None, entities: list[str] | None = None) -> str:
    text = _norm_key(keyword)
    toks = set(_tokens(text))
    metrics = metrics or {}
    intent = str(metrics.get("intent") or "").lower()
    content_type = str(metrics.get("content_type") or metrics.get("content_type_label") or "").lower()
    entities = entities or []
    ordered_tokens = _tokens(text)

    if _has_comparison_signal(text) and len(entities) >= 2:
        return "comparison"
    if metrics.get("is_local") or {"near", "nearby"} & toks or "near me" in text:
        return "local"
    if any(p in text for p in ("not working", "won t", "wont", "blinking", "error", "not charging", "how to charge", "how to open", "recharge")):
        return "troubleshooting"
    if ordered_tokens and ordered_tokens[0] in {"how", "what", "why", "when", "where", "can", "is", "does"}:
        return "faq"
    if {"review", "reviews", "reddit"} & toks:
        return "review"
    if {"price", "prices", "cost", "cheap", "discount", "deal", "deals", "bulk", "wholesale"} & toks:
        return "price"
    if {"pod", "pods", "cartridge", "cartridges"} & toks:
        return "pods"
    if {"flavour", "flavours", "flavor", "flavors", "taste"} & toks:
        return "flavours"
    if entities and _MODEL_RE.search(text):
        return "product_model"
    if {"best", "top", "types", "brands"} & toks or "buying guide" in content_type:
        return "buying_guide"
    if entities and intent in {"transactional", "commercial", "navigational", "branded", ""}:
        return "brand_collection"
    if "collection" in content_type or intent in {"transactional", "commercial"}:
        return "category_collection"
    return "generic"


def keyword_profile(keyword: str, metrics: dict | None, entity_rules: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = metrics or {}
    entities = detect_entities(keyword, entity_rules)
    role = keyword_role(keyword, metrics, entities)
    return {
        "keyword": keyword,
        "entities": entities,
        "entity_key": "|".join(_norm_key(e) for e in entities),
        "role": role,
        "intent": str(metrics.get("intent") or "unknown").lower(),
        "content_type": str(metrics.get("content_type") or metrics.get("content_type_label") or "").lower(),
        "parent_topic": str(metrics.get("parent_topic") or "").strip(),
    }


def content_type_for_role(role: str, current: str = "") -> str:
    current = (current or "").strip()
    if role == "local":
        return "landing_page"
    if role in _BLOG_ROLES:
        return "blog_post"
    if role in {"comparison", "buying_guide"}:
        return "buying_guide"
    if role in _COLLECTION_COMPATIBLE_ROLES:
        return "collection_page"
    return current if current in {"collection_page", "product_page", "blog_post", "buying_guide", "landing_page"} else "blog_post"


def _dominant(counter: Counter[str], default: str = "") -> tuple[str, float]:
    total = sum(counter.values())
    if total <= 0:
        return default, 1.0
    value, count = counter.most_common(1)[0]
    return value or default, count / total


def _page_role_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    return {a, b} <= {"collection_page", "product_page"}


def _role_compatible(a: str, b: str) -> bool:
    if a == b:
        return True
    if "generic" in {a, b} or "brand_collection" in {a, b} or "category_collection" in {a, b}:
        return True
    if {a, b} <= _COLLECTION_COMPATIBLE_ROLES:
        return True
    return False


def _cluster_profiles(cluster: dict, keywords_map: dict[str, dict], entity_rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profiles = []
    for kw in cluster.get("keywords") or []:
        text = str(kw or "").strip()
        if text:
            profiles.append(keyword_profile(text, keywords_map.get(text.lower(), {}), entity_rules))
    return profiles


def clusters_can_merge(
    left: dict,
    right: dict,
    keywords_map: dict[str, dict],
    entity_rules: list[dict[str, Any]],
) -> bool:
    """Return True when two clusters are safe to merge beyond embedding similarity."""
    lp = cluster_profile(left, keywords_map, entity_rules, conn=None)
    rp = cluster_profile(right, keywords_map, entity_rules, conn=None)

    left_entity = lp.get("detected_entity") or ""
    right_entity = rp.get("detected_entity") or ""
    if left_entity and right_entity and left_entity != right_entity:
        return False
    if lp.get("_has_mixed_entities") or rp.get("_has_mixed_entities"):
        return lp.get("cluster_role") == "comparison" and rp.get("cluster_role") == "comparison"

    if not _role_compatible(lp.get("cluster_role", ""), rp.get("cluster_role", "")):
        return False
    if not _page_role_compatible(lp.get("cluster_content_type", ""), rp.get("cluster_content_type", "")):
        return False
    return True


def partition_keywords_for_generation(
    keywords: list[dict],
    conn: sqlite3.Connection,
    *,
    max_bucket_size: int = 60,
) -> tuple[list[list[dict]], list[dict[str, Any]]]:
    """Pre-bucket keywords into entity/intent-safe groups before LLM refinement."""
    entity_rules = load_entity_rules(conn)
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in keywords:
        kw = str(item.get("keyword") or "").strip()
        if not kw:
            continue
        profile = keyword_profile(kw, item, entity_rules)
        role = profile["role"]
        if len(profile["entities"]) >= 2 and role != "comparison":
            entity_key = "multi:" + profile["entity_key"]
        elif profile["entities"]:
            entity_key = "entity:" + _norm_key(profile["entities"][0])
        else:
            entity_key = "generic:" + (_norm_key(profile["parent_topic"]) or role or "topic")
        role_key = role if role not in {"brand_collection", "category_collection", "generic"} else "core"
        buckets[f"{entity_key}|{role_key}"].append(item)

    out: list[list[dict]] = []
    for bucket in buckets.values():
        if len(bucket) <= max_bucket_size:
            out.append(bucket)
            continue
        grouped: dict[str, list[dict]] = defaultdict(list)
        for item in bucket:
            profile = keyword_profile(item["keyword"], item, entity_rules)
            grouped[_split_key(item["keyword"], item, profile)].append(item)
        for group in grouped.values():
            out.extend(_chunk_keywords(group, max_bucket_size))
    return out, entity_rules


def _split_key(keyword: str, metrics: dict, profile: dict[str, Any]) -> str:
    entity = profile.get("entity_key") or "generic"
    role = profile.get("role") or "generic"
    parent = _norm_key(str(metrics.get("parent_topic") or ""))
    subtopic = _subtopic_key(keyword, profile)
    return "|".join([entity, role, parent or subtopic or "topic"])


def _subtopic_key(keyword: str, profile: dict[str, Any]) -> str:
    text = _norm_key(keyword)
    role = profile.get("role") or ""
    if role in {"flavours", "pods", "price", "troubleshooting", "review", "local", "faq", "comparison"}:
        return role
    model = _MODEL_RE.search(text)
    if model:
        return _norm_key(model.group(0))
    toks = [t for t in _tokens(text) if t not in {"vape", "vapes", "disposable", "disposables", "canada", "online"}]
    return " ".join(toks[:2])


def _topic_family(keyword: str) -> str:
    toks = set(_tokens(keyword))
    text = _norm_key(keyword)
    if toks & {"cigarette", "cigarettes", "smoke", "smokes", "smoking", "carton", "cartons", "menthol"}:
        return "cigarettes"
    if toks & {"liquid", "e-liquid", "eliquid", "juice", "nic", "salt", "freebase"} or "e liquid" in text:
        return "e-liquid"
    if toks & {"coil", "coils", "tank", "tanks", "battery", "batteries", "charger", "mod", "mods"}:
        return "hardware"
    if toks & {"vape", "vapes", "vaping", "ecig", "e-cig", "disposable", "disposables"}:
        return "vapes"
    return "general"


def _chunk_keywords(items: list[dict], size: int) -> list[list[dict]]:
    sorted_items = sorted(
        items,
        key=lambda it: (-(float(it.get("opportunity") or 0.0)), -(int(it.get("volume") or 0)), str(it.get("keyword") or "")),
    )
    return [sorted_items[i : i + size] for i in range(0, len(sorted_items), size)]


def cluster_profile(
    cluster: dict,
    keywords_map: dict[str, dict],
    entity_rules: list[dict[str, Any]],
    *,
    conn: sqlite3.Connection | None,
) -> dict[str, Any]:
    profiles = _cluster_profiles(cluster, keywords_map, entity_rules)
    if not profiles:
        return {
            "detected_entity": "",
            "cluster_intent": "unknown",
            "cluster_role": "generic",
            "cluster_content_type": cluster.get("content_type") or "blog_post",
            "quality_score": 0.0,
            "cannibalization_risk": "none",
            "_has_mixed_entities": False,
        }

    entity_counts: Counter[str] = Counter()
    entity_display: dict[str, str] = {}
    mixed_entity_keywords = 0
    for p in profiles:
        ents = p["entities"]
        if len(ents) > 1 and p["role"] != "comparison":
            mixed_entity_keywords += 1
        for ent in ents:
            key = _norm_key(ent)
            entity_counts[key] += 1
            entity_display[key] = ent
    role, role_ratio = _dominant(Counter(p["role"] for p in profiles), "generic")
    intent, intent_ratio = _dominant(Counter(p["intent"] for p in profiles), "unknown")
    has_comparison = role == "comparison"
    detected_entity = ""
    entity_ratio = 1.0
    if entity_counts:
        entity_key, entity_ratio = _dominant(entity_counts, "")
        if len(entity_counts) == 1 or (entity_ratio >= 0.75 and not has_comparison):
            detected_entity = entity_display.get(entity_key, "")

    content_type = content_type_for_role(role, cluster.get("content_type", ""))
    size = len(profiles)
    max_size = MAX_CLUSTER_KEYWORDS.get(content_type, 25)
    overage = max(0, size - max_size)
    has_mixed_entities = bool(len(entity_counts) > 1 and not has_comparison)

    quality = 100.0
    if has_mixed_entities:
        quality -= 38.0 * (1.0 - entity_ratio)
        quality -= 20.0
    if mixed_entity_keywords:
        quality -= min(20.0, mixed_entity_keywords * 4.0)
    quality -= max(0.0, (1.0 - role_ratio) * 22.0)
    quality -= max(0.0, (1.0 - intent_ratio) * 14.0)
    if overage:
        quality -= min(28.0, overage / max(1, max_size) * 28.0)
    if role in {"generic"} and not detected_entity:
        quality -= 8.0
    quality = round(max(0.0, min(100.0, quality)), 1)

    return {
        "detected_entity": detected_entity,
        "cluster_intent": intent,
        "cluster_role": role,
        "cluster_content_type": content_type,
        "quality_score": quality,
        "cannibalization_risk": _cannibalization_risk(conn, [p["keyword"] for p in profiles]) if conn else "none",
        "_has_mixed_entities": has_mixed_entities,
        "_max_size": max_size,
        "_role_ratio": role_ratio,
        "_entity_ratio": entity_ratio,
    }


def _cannibalization_risk(conn: sqlite3.Connection | None, keywords: list[str]) -> str:
    if conn is None or not keywords:
        return "none"
    pages: Counter[tuple[str, str]] = Counter()
    good_rank_pages: set[tuple[str, str]] = set()
    for kw in keywords:
        try:
            rows = conn.execute(
                """
                SELECT object_type, object_handle, gsc_position
                FROM keyword_page_map
                WHERE LOWER(keyword) = LOWER(?)
                  AND COALESCE(object_handle, '') != ''
                """,
                (kw,),
            ).fetchall()
        except Exception:
            return "none"
        for row in rows:
            key = (str(row[0] or ""), str(row[1] or ""))
            if not key[0] or not key[1]:
                continue
            pages[key] += 1
            try:
                pos = float(row[2]) if row[2] is not None else 999.0
            except (TypeError, ValueError):
                pos = 999.0
            if pos <= 20:
                good_rank_pages.add(key)
    if len(pages) >= 3:
        return "high"
    if len(pages) >= 2:
        return "medium"
    if good_rank_pages:
        return "low"
    return "none"


def _keyword_sort_score(keyword: str, keywords_map: dict[str, dict], *, primary: str = "") -> tuple[float, float, float]:
    metrics = keywords_map.get(keyword.lower(), {})
    opp = float(metrics.get("opportunity") or 0.0)
    volume = max(float(metrics.get("volume") or 0.0), 0.0)
    volume_score = math.log1p(volume)
    ranking = 12.0 if (metrics.get("ranking_status") or "") in {"quick_win", "striking_distance"} else 0.0
    primary_bonus = 25.0 if primary and keyword.lower() == primary.lower() else 0.0
    competitor = 5.0 if metrics.get("competitor_domain") or metrics.get("competitor_position") else 0.0
    return (opp + ranking + primary_bonus + competitor, volume_score, -len(keyword))


def keyword_tiers(cluster: dict, keywords_map: dict[str, dict]) -> dict[str, list[str]]:
    keywords = [str(k or "").strip() for k in cluster.get("keywords") or [] if str(k or "").strip()]
    primary = str(cluster.get("primary_keyword") or "").strip()
    ordered = sorted(
        keywords,
        key=lambda kw: _keyword_sort_score(kw, keywords_map, primary=primary),
        reverse=True,
    )
    if primary and primary in keywords:
        ordered = [primary] + [kw for kw in ordered if kw.lower() != primary.lower()]
    seen: set[str] = set()
    deduped: list[str] = []
    for kw in ordered:
        key = kw.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(kw)
    core = deduped[:CORE_KEYWORD_LIMIT]
    supporting = deduped[CORE_KEYWORD_LIMIT : CORE_KEYWORD_LIMIT + SUPPORTING_KEYWORD_LIMIT]
    extended = deduped[CORE_KEYWORD_LIMIT + SUPPORTING_KEYWORD_LIMIT : CORE_KEYWORD_LIMIT + SUPPORTING_KEYWORD_LIMIT + EXTENDED_KEYWORD_LIMIT]
    return {"core_keywords": core, "supporting_keywords": supporting, "extended_keywords": extended}


def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role or "", "Topic")


def _build_cluster_name(cluster: dict, profile: dict[str, Any]) -> str:
    entity = profile.get("detected_entity") or ""
    role = profile.get("cluster_role") or "generic"
    primary = str(cluster.get("primary_keyword") or "").strip()
    current = str(cluster.get("name") or "").strip()
    if (
        not entity
        and not cluster.get("_split_repaired")
        and current
        and current.lower() not in {"unnamed", "unnamed cluster", "keyword topic"}
    ):
        return current
    if entity:
        suffix = _role_label(role)
        if role == "brand_collection":
            return f"{entity} Products"
        return f"{entity} {suffix}"
    if role == "local":
        return "Local Vape and Smoke Shop Finder"
    if primary:
        return " ".join(w.capitalize() for w in primary.split()[:6])
    return cluster.get("name") or "Keyword Topic"


def _build_content_brief(cluster: dict, profile: dict[str, Any], tiers: dict[str, list[str]]) -> str:
    entity = profile.get("detected_entity") or "this topic"
    role = _role_label(profile.get("cluster_role") or "generic").lower()
    intent = profile.get("cluster_intent") or "search"
    core = ", ".join(tiers.get("core_keywords", [])[:5])
    value = "Give shoppers practical, original guidance and link naturally to the best matching store page."
    if profile.get("cluster_role") in {"troubleshooting", "faq"}:
        value = "Answer the problem clearly, avoid unsupported health or safety claims, and point readers to relevant products only when useful."
    elif profile.get("cluster_role") in {"comparison", "buying_guide"}:
        value = "Compare options honestly with concrete buying criteria, not keyword stuffing."
    return (
        f"A {role} content plan for {entity}, serving {intent} intent. "
        f"Prioritize {core or cluster.get('primary_keyword', '')}. {value}"
    ).strip()


def enrich_cluster_for_content(
    cluster: dict,
    conn: sqlite3.Connection,
    keywords_map: dict[str, dict],
    entity_rules: list[dict[str, Any]],
) -> dict:
    kw_list = [str(k or "").strip() for k in cluster.get("keywords") or [] if str(k or "").strip()]
    stats = _compute_cluster_stats([kw.lower() for kw in kw_list], keywords_map)
    profile = cluster_profile({**cluster, "keywords": kw_list}, keywords_map, entity_rules, conn=conn)
    content_type = profile["cluster_content_type"]
    primary = select_primary_keyword(
        kw_list,
        keywords_map,
        ai_primary=cluster.get("primary_keyword", ""),
        content_type=content_type,
    )
    base = {
        **cluster,
        "content_type": content_type,
        "primary_keyword": primary,
        "keywords": kw_list,
        **stats,
    }
    tiers = keyword_tiers(base, keywords_map)
    priority = cluster_priority_score(kw_list, keywords_map)
    priority *= 0.65 + (float(profile["quality_score"]) / 200.0)
    if profile["cannibalization_risk"] == "high":
        priority *= 0.82
    elif profile["cannibalization_risk"] == "medium":
        priority *= 0.90
    if any(keywords_map.get(kw.lower(), {}).get("competitor_domain") for kw in kw_list):
        priority += 3.0
    base.update(
        {
            "detected_entity": profile["detected_entity"],
            "cluster_intent": profile["cluster_intent"],
            "cluster_role": profile["cluster_role"],
            "quality_score": profile["quality_score"],
            "cannibalization_risk": profile["cannibalization_risk"],
            "priority_score": round(max(0.0, min(100.0, priority)), 1),
            **tiers,
        }
    )
    base["name"] = _build_cluster_name(base, profile)
    base["content_brief"] = _build_content_brief(base, profile, tiers)
    return base


def _split_cluster(cluster: dict, keywords_map: dict[str, dict], entity_rules: list[dict[str, Any]]) -> list[dict]:
    profiles = _cluster_profiles(cluster, keywords_map, entity_rules)
    if len(profiles) <= 1:
        return [cluster]
    max_size = MAX_CLUSTER_KEYWORDS.get(cluster.get("content_type") or "blog_post", 25)
    broad_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in profiles:
        entity_key = p.get("entity_key") or "generic"
        role = p.get("role") or "generic"
        if role in {"brand_collection", "category_collection", "generic"}:
            role = "core"
        broad_groups[f"{entity_key}|{role}"].append(p)

    grouped_keywords: dict[str, list[str]] = {}
    for broad_key, group_profiles in broad_groups.items():
        if len(group_profiles) <= max_size:
            grouped_keywords[broad_key] = [p["keyword"] for p in group_profiles]
            continue
        if broad_key.startswith("generic|"):
            family_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for p in group_profiles:
                family_groups[_topic_family(p["keyword"])].append(p)
            for family, family_profiles in family_groups.items():
                chunks = _chunk_keywords(
                    [{"keyword": p["keyword"], **keywords_map.get(p["keyword"].lower(), {})} for p in family_profiles],
                    max_size,
                )
                for i, chunk in enumerate(chunks):
                    grouped_keywords[f"{broad_key}|{family}|chunk-{i}"] = [item["keyword"] for item in chunk]
            continue
        subgroups: dict[str, list[str]] = defaultdict(list)
        for p in group_profiles:
            metrics = keywords_map.get(p["keyword"].lower(), {})
            subgroups[_split_key(p["keyword"], metrics, p)].append(p["keyword"])
        small_pool: list[str] = []
        for sub_key, kws in subgroups.items():
            if len(kws) < 3:
                small_pool.extend(kws)
                continue
            if len(kws) <= max_size:
                grouped_keywords[f"{broad_key}|{sub_key}"] = kws
            else:
                chunks = _chunk_keywords(
                    [{"keyword": kw, **keywords_map.get(kw.lower(), {})} for kw in kws],
                    max_size,
                )
                for i, chunk in enumerate(chunks):
                    grouped_keywords[f"{broad_key}|{sub_key}|chunk-{i}"] = [item["keyword"] for item in chunk]
        if small_pool:
            chunks = _chunk_keywords(
                [{"keyword": kw, **keywords_map.get(kw.lower(), {})} for kw in small_pool],
                max_size,
            )
            for i, chunk in enumerate(chunks):
                grouped_keywords[f"{broad_key}|misc-{i}"] = [item["keyword"] for item in chunk]
    out: list[dict] = []
    for kws in grouped_keywords.values():
        if not kws:
            continue
        out.append({**cluster, "_split_repaired": True, "keywords": kws, "primary_keyword": kws[0]})
    return out


def repair_and_enrich_clusters(
    clusters: list[dict],
    conn: sqlite3.Connection,
    keywords_map: dict[str, dict],
) -> list[dict]:
    """Split unsafe clusters and attach content-generation metadata."""
    entity_rules = load_entity_rules(conn)
    repaired: list[dict] = []
    queue: list[dict] = list(clusters)
    for _ in range(3):
        next_queue: list[dict] = []
        changed = False
        for cluster in queue:
            profile = cluster_profile(cluster, keywords_map, entity_rules, conn=conn)
            size = len(cluster.get("keywords") or [])
            max_size = int(profile.get("_max_size") or 25)
            needs_split = (
                size > max_size
                or profile.get("_has_mixed_entities")
                or float(profile.get("quality_score") or 0.0) < 68.0
            )
            if needs_split and size > 1:
                parts = _split_cluster(cluster, keywords_map, entity_rules)
                if len(parts) > 1:
                    next_queue.extend(parts)
                    changed = True
                    continue
            next_queue.append(cluster)
        queue = next_queue
        if not changed:
            break

    seen_signature: set[tuple[str, tuple[str, ...]]] = set()
    for cluster in queue:
        enriched = enrich_cluster_for_content(cluster, conn, keywords_map, entity_rules)
        signature = (
            (enriched.get("primary_keyword") or "").lower(),
            tuple(sorted(k.lower() for k in enriched.get("keywords", []))),
        )
        if signature in seen_signature:
            continue
        seen_signature.add(signature)
        repaired.append(enriched)
    return repaired


def serialize_keyword_tiers(cluster: dict) -> dict[str, str]:
    """Return JSON payloads for tier columns."""
    return {
        "core_keywords_json": _json_dumps(cluster.get("core_keywords")),
        "supporting_keywords_json": _json_dumps(cluster.get("supporting_keywords")),
        "extended_keywords_json": _json_dumps(cluster.get("extended_keywords")),
    }
