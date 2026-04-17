import json
import logging
import re
import sqlite3
from datetime import datetime, timezone
UTC = timezone.utc

from .. import dashboard_google as dg
from .. import dashboard_queries as dq

_log = logging.getLogger(__name__)


def setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    value = dg.get_service_setting(conn, key)
    return value.strip() if isinstance(value, str) else default


def trim_gsc_segment_summary_for_prompt(summary: dict | None, *, max_rollup: int = 5, max_pairs: int = 8) -> dict | None:
    """Cap segment payload for LLM prompts / Sidekick (~1–2k tokens budget for whole block)."""
    if not summary:
        return None
    if not any(
        summary.get(k)
        for k in ("device_mix", "top_countries", "search_appearances", "top_pairs")
    ):
        return None
    return {
        "fetched_at": summary.get("fetched_at"),
        "device_mix": (summary.get("device_mix") or [])[:max_rollup],
        "top_countries": (summary.get("top_countries") or [])[:max_rollup],
        "search_appearances": (summary.get("search_appearances") or [])[:max_rollup],
        "top_pairs": (summary.get("top_pairs") or [])[:max_pairs],
    }


def cluster_query_rows(query_rows: list[dict], country_name: str = "canada") -> list[dict]:
    _country_lower = country_name.lower()
    clusters: dict[str, dict] = {}
    for row in query_rows:
        query = str(row.get("query") or "").strip()
        if not query:
            continue
        lowered = query.lower()
        if any(token in lowered for token in [" vs ", " versus ", "compare", "comparison", "alternative", "alternatives"]):
            cluster = "comparison"
        elif any(token in lowered for token in ["buy", "shop", "order", "sale", "price", "best", "near me", _country_lower]):
            cluster = "transactional"
        elif any(token in lowered for token in ["how", "what", "guide", "review", "flavor", "flavours", "flavour"]):
            cluster = "informational"
        else:
            model = re.search(r"(bc10000|gh20000|ultra ?25k|nuud ?50k|stlth|elfbar|allo|geekbar)", lowered)
            cluster = model.group(1).replace(" ", "") if model else "general"
        item = clusters.setdefault(
            cluster,
            {"cluster": cluster, "queries": [], "clicks": 0, "impressions": 0, "top_position": None},
        )
        item["queries"].append(query)
        item["clicks"] += int(row.get("clicks") or 0)
        item["impressions"] += int(row.get("impressions") or 0)
        pos = float(row.get("position") or 0)
        if item["top_position"] is None or (pos and pos < item["top_position"]):
            item["top_position"] = pos
    output = list(clusters.values())
    output.sort(key=lambda item: (item["impressions"], item["clicks"]), reverse=True)
    for item in output:
        item["queries"] = item["queries"][:6]
    return output[:8]


def _fetch_keyword_context(conn: sqlite3.Connection, object_type: str, handle: str, *, limit: int = 15) -> list[dict]:
    """Load enriched keyword data mapped to this object via keyword_page_map."""
    try:
        rows = conn.execute(
            """
            SELECT kpm.keyword, km.volume, km.difficulty, km.cpc, km.clicks, km.cps,
                   km.serp_features, km.content_format_hint, km.intent,
                   km.traffic_potential, km.opportunity, km.is_local,
                   kpm.gsc_clicks, kpm.gsc_impressions, kpm.gsc_position
            FROM keyword_page_map kpm
            JOIN keyword_metrics km ON LOWER(kpm.keyword) = LOWER(km.keyword)
            WHERE kpm.object_type = ? AND kpm.object_handle = ?
            ORDER BY km.opportunity DESC, kpm.gsc_impressions DESC
            LIMIT ?
            """,
            (object_type, handle, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_competitor_gaps(conn: sqlite3.Connection, object_type: str, handle: str, *, limit: int = 10) -> list[dict]:
    """Load competitor gap keywords relevant to this object."""
    try:
        rows = conn.execute(
            """
            SELECT ckg.keyword, ckg.competitor_domain, ckg.competitor_position,
                   ckg.volume, ckg.difficulty, ckg.traffic_potential, ckg.gap_type,
                   km.content_format_hint
            FROM competitor_keyword_gaps ckg
            JOIN keyword_metrics km ON LOWER(ckg.keyword) = LOWER(km.keyword)
            JOIN keyword_page_map kpm ON LOWER(ckg.keyword) = LOWER(kpm.keyword)
                AND kpm.object_type = ? AND kpm.object_handle = ?
            ORDER BY ckg.volume DESC
            LIMIT ?
            """,
            (object_type, handle, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _slim_keyword_context(keywords: list[dict], *, max_rows: int = 5) -> list[dict]:
    """Slim keyword context for title/description prompts (reduce token usage)."""
    out = []
    for kw in keywords[:max_rows]:
        out.append({
            "keyword": kw.get("keyword", ""),
            "volume": kw.get("volume"),
            "difficulty": kw.get("difficulty"),
            "intent": kw.get("intent", ""),
        })
    return out


_RAG_TYPE_QUOTAS = {
    "product": {"product": 3, "blog_article": 2, "collection": 1},
    "collection": {"collection": 2, "product": 2, "blog_article": 1},
    "page": {"page": 2, "blog_article": 2, "product": 1},
    "blog_article": {"blog_article": 3, "product": 1, "collection": 1},
}


def _fetch_rag_context(conn: sqlite3.Connection, object_type: str, handle: str) -> dict:
    """Retrieve RAG context for an object. Returns empty dicts on failure."""
    result: dict = {"similar_objects": [], "semantic_keywords": [], "competitor_content": []}
    try:
        from ..embedding_store import (
            retrieve_related_by_handle,
            find_semantic_keyword_matches,
            find_competitive_gaps,
        )
        quotas = _RAG_TYPE_QUOTAS.get(object_type, {"product": 2, "blog_article": 2, "collection": 1})
        result["similar_objects"] = retrieve_related_by_handle(conn, object_type, handle, type_quotas=quotas)
        result["semantic_keywords"] = find_semantic_keyword_matches(conn, object_type, handle, top_k=10)
        result["competitor_content"] = find_competitive_gaps(conn, object_type, handle, top_k=5)
    except Exception:
        _log.debug("RAG retrieval unavailable for %s/%s, using standard context", object_type, handle, exc_info=True)
    return result


def object_context(conn: sqlite3.Connection, object_type: str, handle: str) -> dict:
    from shopifyseo.market_context import get_primary_country_code, country_display_name
    _mkt_code = get_primary_country_code(conn)
    _mkt_name = country_display_name(_mkt_code)

    if object_type == "blog_article":
        blog_h, sep, art_h = handle.partition("/")
        if not sep or not art_h:
            raise RuntimeError(f"blog_article handle must be blog_handle/article_slug, got {handle!r}")
        detail = dq.fetch_blog_article_detail(conn, blog_h, art_h)
        if not detail:
            raise RuntimeError(f"blog_article not found: {handle!r}")
        article = dict(detail.get("article") or {})
        composite = dq.blog_article_composite_handle(blog_h, art_h)
        fact = dq.build_seo_fact(
            "blog_article",
            {**article, "handle": composite},
            detail.get("workflow"),
            detail.get("recommendation"),
        )
        query_rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT query, clicks, impressions, ctr, position, fetched_at
                FROM gsc_query_rows
                WHERE object_type = ? AND object_handle = ?
                ORDER BY impressions DESC, clicks DESC, query ASC
                LIMIT 20
                """,
                (object_type, handle),
            ).fetchall()
        ]
        recommendation_history = detail.get("recommendation_history", [])[:5]
        dim_rows = dq.fetch_gsc_query_dimension_rows(conn, object_type, handle)
        gsc_segment_summary = dq.build_gsc_segment_summary_from_rows(dim_rows)
        keyword_context = _fetch_keyword_context(conn, object_type, handle)
        competitor_gaps = _fetch_competitor_gaps(conn, object_type, handle)
        rag = _fetch_rag_context(conn, object_type, handle)
        return {
            "object_type": object_type,
            "_market_country_code": _mkt_code,
            "fact": fact,
            "detail": serialize_detail(detail),
            "gsc_query_rows": query_rows,
            "gsc_query_clusters": cluster_query_rows(query_rows, country_name=_mkt_name),
            "gsc_segment_summary": gsc_segment_summary,
            "recommendation_history": recommendation_history,
            "keyword_context": keyword_context,
            "competitor_gaps": competitor_gaps,
            "similar_objects": rag["similar_objects"],
            "semantic_keywords": rag["semantic_keywords"],
            "competitor_content": rag["competitor_content"],
        }

    detail = {
        "product": dq.fetch_product_detail,
        "collection": dq.fetch_collection_detail,
        "page": dq.fetch_page_detail,
    }[object_type](conn, handle)
    if not detail:
        raise RuntimeError(f"{object_type} not found: {handle}")
    fact = next(item for item in dq.fetch_seo_facts(conn, object_type) if item["handle"] == handle)
    query_rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT query, clicks, impressions, ctr, position, fetched_at
            FROM gsc_query_rows
            WHERE object_type = ? AND object_handle = ?
            ORDER BY impressions DESC, clicks DESC, query ASC
            LIMIT 20
            """,
            (object_type, handle),
        ).fetchall()
    ]
    if object_type == "product":
        product_row = detail.get("product") or {}
        if hasattr(product_row, "keys"):
            product_row = dict(product_row)
        brand_token = str(product_row.get("vendor") or "").strip()
        if not brand_token:
            brand_token = (str(product_row.get("title") or "").strip().split() or [""])[0]
        related_pages = [
            dict(row)
            for row in conn.execute(
                """
                SELECT handle, title
                FROM pages
                WHERE UPPER(title) LIKE ? OR UPPER(handle) LIKE ?
                ORDER BY title ASC
                LIMIT 12
                """,
                (f"%{brand_token.upper()}%", f"%{brand_token.upper()}%"),
            ).fetchall()
        ]
        detail["related_pages"] = related_pages
        # Fetch accepted SEO titles from the same brand (and preferably same model family)
        # to give the title generator concrete format anchors from the live catalog.
        # Prioritise same custom_collection (model family) then fall back to same vendor.
        model_family = str(product_row.get("custom_collection") or "").strip()
        catalog_title_rows: list[dict] = []
        if model_family:
            catalog_title_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT title, seo_title
                    FROM products
                    WHERE vendor = ? AND custom_collection = ?
                      AND seo_title != '' AND seo_title IS NOT NULL
                      AND handle != ?
                    ORDER BY updated_at DESC
                    LIMIT 3
                    """,
                    (product_row.get("vendor") or brand_token, model_family, handle),
                ).fetchall()
            ]
        if len(catalog_title_rows) < 3:
            needed = 3 - len(catalog_title_rows)
            existing_titles = {r["seo_title"] for r in catalog_title_rows}
            fallback_rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT title, seo_title
                    FROM products
                    WHERE vendor = ?
                      AND seo_title != '' AND seo_title IS NOT NULL
                      AND handle != ?
                    ORDER BY updated_at DESC
                    LIMIT 10
                    """,
                    (product_row.get("vendor") or brand_token, handle),
                ).fetchall()
                if dict(row)["seo_title"] not in existing_titles
            ]
            catalog_title_rows.extend(fallback_rows[:needed])
        detail["catalog_title_examples"] = catalog_title_rows[:3]
    recommendation_history = detail.get("recommendation_history", [])[:5]
    dim_rows = dq.fetch_gsc_query_dimension_rows(conn, object_type, handle)
    gsc_segment_summary = dq.build_gsc_segment_summary_from_rows(dim_rows)
    keyword_context = _fetch_keyword_context(conn, object_type, handle)
    competitor_gaps = _fetch_competitor_gaps(conn, object_type, handle)
    rag = _fetch_rag_context(conn, object_type, handle)
    return {
        "object_type": object_type,
        "fact": fact,
        "detail": serialize_detail(detail),
        "gsc_query_rows": query_rows,
        "gsc_query_clusters": cluster_query_rows(query_rows, country_name=_mkt_name),
        "_market_country_code": _mkt_code,
        "gsc_segment_summary": gsc_segment_summary,
        "recommendation_history": recommendation_history,
        "keyword_context": keyword_context,
        "competitor_gaps": competitor_gaps,
        "similar_objects": rag["similar_objects"],
        "semantic_keywords": rag["semantic_keywords"],
        "competitor_content": rag["competitor_content"],
    }


def serialize_detail(detail: dict) -> dict:
    serialized = {}
    for key, value in detail.items():
        if isinstance(value, list):
            serialized[key] = [dict(row) if hasattr(row, "keys") else row for row in value]
        elif hasattr(value, "keys"):
            serialized[key] = dict(value)
        else:
            serialized[key] = value
    return serialized


def json_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [item.strip() for item in str(value).split(",") if item.strip()]
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def product_metafield_map(detail_payload: dict) -> dict[str, str]:
    output: dict[str, str] = {}
    for row in detail_payload.get("metafields") or []:
        namespace = str(row.get("namespace") or "").strip()
        key = str(row.get("key") or "").strip()
        if not namespace or not key:
            continue
        output[f"{namespace}.{key}"] = str(row.get("value") or "").strip()
    return output


def product_specs(primary: dict, detail_payload: dict) -> dict:
    metafields = product_metafield_map(detail_payload)
    variant_titles = [str(row.get("title") or "").strip() for row in (detail_payload.get("variants") or []) if str(row.get("title") or "").strip()]
    tags = json_list(primary.get("tags_json") or primary.get("tags"))
    battery_type_labels = json_list(primary.get("battery_type_labels_json"))
    coil_connection_labels = json_list(primary.get("coil_connection_labels_json"))
    color_pattern_labels = json_list(primary.get("color_pattern_labels_json"))
    vaporizer_style_labels = json_list(primary.get("vaporizer_style_labels_json"))
    e_liquid_flavor_labels = json_list(primary.get("e_liquid_flavor_labels_json"))
    vaping_style_labels = json_list(primary.get("vaping_style_labels_json"))
    return {
        "brand": primary.get("vendor") or "",
        "model": primary.get("custom_collection") or metafields.get("custom.collection", ""),
        "flavor": str(primary.get("title") or "").strip(),
        "nicotine_strength": primary.get("nicotine_strength") or metafields.get("custom.nicotine_strength", ""),
        "puff_count": primary.get("puff_count") or metafields.get("custom.puff_count", ""),
        "device_type": primary.get("device_type") or metafields.get("custom.device_type", ""),
        "battery_size": primary.get("battery_size") or metafields.get("custom.battery_size", ""),
        "charging_port": primary.get("charging_port") or metafields.get("custom.charging_port", ""),
        "coil": primary.get("coil") or metafields.get("custom.coil", ""),
        "size": primary.get("size") or metafields.get("custom.size", ""),
        "battery_type_labels": battery_type_labels,
        "coil_connection_labels": coil_connection_labels,
        "color_pattern_labels": color_pattern_labels,
        "vaporizer_style_labels": vaporizer_style_labels,
        "e_liquid_flavor_labels": e_liquid_flavor_labels,
        "vaping_style_labels": vaping_style_labels,
        "resolved_attributes": {
            "battery_types": battery_type_labels,
            "coil_connections": coil_connection_labels,
            "color_patterns": color_pattern_labels,
            "vaporizer_styles": vaporizer_style_labels,
            "e_liquid_flavors": e_liquid_flavor_labels,
            "vaping_styles": vaping_style_labels,
        },
        "tags": tags,
        "variant_titles": variant_titles[:8],
        "inventory": primary.get("total_inventory"),
        "online_store_url": primary.get("online_store_url") or "",
    }


def infer_product_intent(context: dict, country_code: str = "CA") -> dict:
    detail_payload = context.get("detail") or {}
    primary = detail_payload.get("product") or {}
    specs = product_specs(primary, detail_payload)
    query_clusters = context.get("gsc_query_clusters") or []
    title = str(primary.get("title") or "").strip()
    lowered_title = title.lower()
    brand = str(specs.get("brand") or "").strip()
    flavor = str(specs.get("flavor") or "").strip()
    nicotine = str(specs.get("nicotine_strength") or "").strip()
    puff_count = str(specs.get("puff_count") or "").strip()
    device_type = str(specs.get("device_type") or primary.get("product_type") or "").strip()
    # Build a search-ready brand+model+flavor term without dash separators (e.g. "ALLO ULTRA 800 Strawberry Banana")
    brand_model_flavor = re.sub(r"\s*[-\u2013]\s*", " ", flavor).strip() if flavor else ""
    # Format puff count with unit so it reads as a query term (e.g. "800 Puffs" not just "800")
    puff_label = f"{puff_count} Puffs" if puff_count and str(puff_count).strip().isdigit() else puff_count
    # Exclude the raw page title — it is not a search query; use the cleaned compound instead
    core_terms = [item for item in [brand, brand_model_flavor, puff_label, device_type, nicotine] if item]
    from shopifyseo.market_context import geo_modifier_keywords, country_display_name
    market_keywords = geo_modifier_keywords(country_code, brand=brand, flavor=flavor, device_type=device_type)
    _market_label = f"{country_code.lower()}_market"
    intent_labels = []
    if brand:
        intent_labels.append("brand")
    if flavor:
        intent_labels.append("flavor")
    if nicotine:
        intent_labels.append("nicotine_strength")
    if puff_count:
        intent_labels.append("puff_count")
    if device_type:
        intent_labels.append("device_type")
    intent_labels.extend(["transactional", _market_label])
    cluster_summary = []
    for row in query_clusters[:4]:
        cluster_summary.append({"cluster": row.get("cluster"), "impressions": row.get("impressions"), "clicks": row.get("clicks"), "queries": row.get("queries") or []})
    if "ice" in lowered_title or "mint" in lowered_title:
        flavor_family = "cooling"
    elif any(token in lowered_title for token in ["berry", "mango", "apple", "grape", "peach", "lemon", "banana", "cherry"]):
        flavor_family = "fruit"
    elif any(token in lowered_title for token in ["cola", "gummy", "candy", "bubblegum"]):
        flavor_family = "candy"
    else:
        flavor_family = "other"
    return {
        "intent_labels": intent_labels,
        "primary_terms": core_terms[:8],
        "canada_keywords": market_keywords[:6],
        "query_cluster_summary": cluster_summary,
        "flavor_family": flavor_family,
    }


def sanitize_recommendation_history(history: list[dict]) -> list[dict]:
    sanitized = []
    for row in history[:5]:
        details = row.get("details") or {}
        sanitized.append(
            {
                "created_at": row.get("created_at") or "",
                "status": row.get("status") or "",
                "model": row.get("model") or "",
                "prompt_version": row.get("prompt_version") or "",
                "summary": row.get("summary") or "",
                "seo_title": details.get("seo_title") or "",
                "seo_description": details.get("seo_description") or "",
                "body": details.get("body") or "",
                "priority_actions": (details.get("priority_actions") or [])[:3],
                "internal_links": (details.get("internal_links") or [])[:3],
            }
        )
    return sanitized


def parse_timestamp(value) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=UTC)
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def freshness_flags(primary: dict) -> dict:
    now = datetime.now(tz=UTC)
    flags = {}
    stale_after_seconds = 7 * 24 * 60 * 60
    for key in ["gsc_last_fetched_at", "ga4_last_fetched_at", "index_last_fetched_at", "pagespeed_last_fetched_at"]:
        stamp = parse_timestamp(primary.get(key))
        if stamp is None:
            flags[key.replace("_last_fetched_at", "")] = "missing"
            continue
        age_seconds = max(0, int((now - stamp.astimezone(UTC)).total_seconds()))
        flags[key.replace("_last_fetched_at", "")] = "stale" if age_seconds > stale_after_seconds else "fresh"
    return flags


def signal_availability_summary(context: dict) -> dict:
    fact = context.get("fact") or {}
    detail_payload = context.get("detail") or {}
    primary = (
        detail_payload.get("product")
        or detail_payload.get("collection")
        or detail_payload.get("page")
        or detail_payload.get("article")
        or {}
    )
    return {
        "gsc_queries": len(context.get("gsc_query_rows") or []),
        "internal_links": len(fact.get("internal_links") or []),
        "freshness": freshness_flags(primary),
    }


def strip_html(value: str) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def word_count(value: str) -> int:
    return len(re.findall(r"[a-z0-9']+", strip_html(value).lower()))


def curated_primary_object(context: dict) -> dict:
    detail_payload = context.get("detail") or {}
    primary = (
        detail_payload.get("product")
        or detail_payload.get("collection")
        or detail_payload.get("page")
        or detail_payload.get("article")
        or {}
    )
    current_tags = json_list(primary.get("tags_json") or primary.get("tags"))
    payload = {
        "title": primary.get("title") or "",
        "handle": primary.get("handle") or "",
        "status": primary.get("status") or "",
        "updated_at": primary.get("updated_at") or "",
        "published_at": primary.get("published_at") or "",
        "online_store_url": primary.get("online_store_url") or "",
        "current_seo_title": primary.get("seo_title") or "",
        "current_seo_description": primary.get("seo_description") or "",
        "current_body": primary.get("description_html") or primary.get("body") or "",
        "current_tags": current_tags,
    }
    if context.get("object_type") == "product":
        payload["vendor"] = primary.get("vendor") or ""
        payload["specs"] = product_specs(primary, detail_payload)
        payload["intent"] = infer_product_intent(context, country_code=context.get("_market_country_code", "CA"))
    return payload


def freshness_summary(primary: dict) -> dict:
    return {
        "gsc_last_fetched_at": primary.get("gsc_last_fetched_at") or "",
        "ga4_last_fetched_at": primary.get("ga4_last_fetched_at") or "",
        "index_last_fetched_at": primary.get("index_last_fetched_at") or "",
        "pagespeed_last_fetched_at": primary.get("pagespeed_last_fetched_at") or "",
        "seo_signal_updated_at": primary.get("seo_signal_updated_at") or "",
    }


def condensed_context(context: dict) -> dict:
    fact = context.get("fact") or {}
    detail_payload = context.get("detail") or {}
    primary = (
        detail_payload.get("product")
        or detail_payload.get("collection")
        or detail_payload.get("page")
        or detail_payload.get("article")
        or {}
    )
    seg = context.get("gsc_segment_summary") or {}
    seo_fact_summary: dict = {
        "score": fact.get("score"),
        "priority": fact.get("priority"),
        "thin_content": fact.get("thin_content"),
        "internal_link_count": fact.get("internal_link_count"),
        "gsc_clicks": fact.get("gsc_clicks"),
        "gsc_impressions": fact.get("gsc_impressions"),
        "gsc_ctr": fact.get("gsc_ctr"),
        "gsc_position": fact.get("gsc_position"),
        "ga4_sessions": fact.get("ga4_sessions"),
        "ga4_views": fact.get("ga4_views"),
        "ga4_avg_session_duration": fact.get("ga4_avg_session_duration"),
        "index_status": fact.get("index_status"),
        "index_coverage": fact.get("index_coverage"),
        "google_canonical": fact.get("google_canonical"),
        "pagespeed_performance": fact.get("pagespeed_performance"),
        "pagespeed_seo": fact.get("pagespeed_seo"),
        "pagespeed_status": fact.get("pagespeed_status"),
        "evidence": fact.get("evidence") or {},
        "reasons": fact.get("reasons") or [],
    }
    if seg.get("device_mix") or seg.get("top_countries") or seg.get("search_appearances"):
        seo_fact_summary["gsc_device_mix"] = seg.get("device_mix") or []
        seo_fact_summary["gsc_top_countries"] = seg.get("top_countries") or []
        seo_fact_summary["gsc_search_appearances"] = seg.get("search_appearances") or []

    return {
        "current_fields": {
            "product_title": primary.get("title") or "",
            "seo_title": primary.get("seo_title") or "",
            "seo_description": primary.get("seo_description") or "",
            "body": primary.get("description_html") or primary.get("body") or "",
            "tags": json_list(primary.get("tags_json") or primary.get("tags")),
        },
        "seo_fact_summary": seo_fact_summary,
        "freshness": freshness_summary(primary),
        "gsc_query_rows": context.get("gsc_query_rows") or [],
        "gsc_query_clusters": context.get("gsc_query_clusters") or [],
        "recommendation_opportunities": (((context.get("detail") or {}).get("recommendation") or {}).get("details") or {}).get("opportunities", []),
    }


def _format_rag_similar(items: list[dict]) -> list[dict]:
    """Format similar_objects for prompt injection, providing rich content instead of snippets."""
    result = []
    for item in items:
        result.append({
            "type": item.get("object_type", ""),
            "handle": item.get("object_handle", ""),
            "score": round(item.get("score", 0), 3),
            "content": (item.get("source_text_preview") or ""),
        })
    return result


def _format_rag_keywords(items: list[dict]) -> list[dict]:
    return [
        {"keyword": i.get("keyword", ""), "intent": i.get("intent", ""), "volume": i.get("volume", 0)}
        for i in items
    ]


def _format_rag_competitors(items: list[dict]) -> list[dict]:
    """Format competitor gaps for prompt injection (maps find_competitive_gaps rows)."""
    out: list[dict] = []
    for i in items:
        traffic = i.get("estimated_traffic")
        if traffic is None:
            traffic = i.get("traffic_potential", 0)
        row = {
            "domain": i.get("competitor_domain", ""),
            "keyword": i.get("top_keyword") or i.get("keyword", ""),
            "gap_type": i.get("page_type") or i.get("gap_type", ""),
            "traffic_potential": traffic,
            "volume": i.get("volume", 0),
        }
        if "score" in i:
            row["score"] = round(float(i["score"]), 3)
        out.append(row)
    return out


def prompt_context(context: dict) -> dict:
    detail_payload = context.get("detail") or {}
    workflow = (context.get("fact") or {}).get("workflow") or {}
    collections = [{"handle": row.get("handle"), "title": row.get("title")} for row in (detail_payload.get("collections") or detail_payload.get("related_collections") or [])[:12]]
    related_products = [{"handle": row.get("handle") or row.get("product_handle"), "title": row.get("title") or row.get("product_title")} for row in (detail_payload.get("related_products") or detail_payload.get("products") or [])[:12]]
    related_pages = [{"handle": row.get("handle"), "title": row.get("title")} for row in (detail_payload.get("related_pages") or [])[:12]]
    def _link_target(kind: str, row: dict) -> dict | None:
        h = row.get("handle")
        if not h:
            return None
        path_prefix = {"collection": "/collections", "product": "/products", "page": "/pages"}.get(kind, "")
        if not path_prefix:
            return None
        path = f"{path_prefix}/{h}"
        return {
            "type": kind,
            "handle": h,
            "title": row.get("title"),
            "path": path,
            "url": dq.object_url(kind, h),
        }

    approved_internal_link_targets: list[dict] = []
    for row in collections:
        t = _link_target("collection", row)
        if t:
            approved_internal_link_targets.append(t)
    for row in related_products:
        t = _link_target("product", row)
        if t:
            approved_internal_link_targets.append(t)
    for row in related_pages:
        t = _link_target("page", row)
        if t:
            approved_internal_link_targets.append(t)
    trimmed_segments = trim_gsc_segment_summary_for_prompt(context.get("gsc_segment_summary"))
    segment_query_keywords = [
        {
            "query": row.get("query"),
            "dimension_kind": row.get("dimension_kind"),
            "dimension_value": row.get("dimension_value"),
            "impressions": row.get("impressions"),
            "clicks": row.get("clicks"),
            "position": row.get("position"),
        }
        for row in ((trimmed_segments or {}).get("top_pairs") or [])
    ]
    return {
        "object_type": context.get("object_type"),
        "primary_object": curated_primary_object(context),
        "gsc_segment_summary": trimmed_segments,
        "segment_query_keywords": segment_query_keywords,
        "relationships": {
            "collections": collections,
            "related_products": related_products,
            "related_pages": related_pages,
        },
        "approved_internal_link_targets": approved_internal_link_targets,
        "workflow": {"status": workflow.get("status") or "", "notes": workflow.get("notes") or "", "updated_at": workflow.get("updated_at") or ""},
        "top_queries": [{"query": row.get("query"), "impressions": row.get("impressions"), "clicks": row.get("clicks"), "ctr": row.get("ctr"), "position": row.get("position")} for row in (context.get("gsc_query_rows") or [])[:20]],
        "query_clusters": context.get("gsc_query_clusters") or [],
        "internal_links": (context.get("fact") or {}).get("internal_links") or [],
        "evidence": (context.get("fact") or {}).get("evidence") or {},
        "reasons": (context.get("fact") or {}).get("reasons") or [],
        "signal_availability": signal_availability_summary(context),
        "recommendation_history": sanitize_recommendation_history(context.get("recommendation_history") or []),
        "seo_context": condensed_context(context),
        "cluster_seo_context": context.get("cluster_seo_context"),
        "seo_keyword_gaps": context.get("seo_keyword_gaps"),
        "keyword_context": context.get("keyword_context") or [],
        "competitor_gaps": context.get("competitor_gaps") or [],
        "catalog_title_examples": [
            {"product_title": row.get("title"), "seo_title": row.get("seo_title")}
            for row in (detail_payload.get("catalog_title_examples") or [])
            if row.get("seo_title")
        ],
        "related_content_examples": _format_rag_similar(context.get("similar_objects") or []),
        "additional_keyword_opportunities": _format_rag_keywords(context.get("semantic_keywords") or []),
        "competitor_coverage": _format_rag_competitors(context.get("competitor_content") or []),
    }
