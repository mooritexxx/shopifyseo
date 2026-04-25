"""Article ideas — gap analysis inputs and CRUD helpers.

Extracted from dashboard_queries.py to keep that module focused on
catalog and overview queries.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


def normalize_audience_questions_json(value: Any) -> list[dict[str, str]]:
    """Coerce ``audience_questions`` / DB JSON to ``[{question, snippet}, ...]`` (legacy: ``answer``, list of strings)."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            q = item.strip()
            if q:
                out.append({"question": q, "snippet": ""})
        elif isinstance(item, dict):
            q = str(item.get("question") or "").strip()
            if not q:
                continue
            sn = item.get("snippet") if item.get("snippet") is not None else item.get("answer")
            out.append({"question": q, "snippet": str(sn or "").strip()})
        if len(out) >= 80:
            break
    return out


def normalize_paa_expansion_json(value: Any) -> list[dict[str, Any]]:
    """Coerce SerpAPI PAA expansion tree: ``[{parent_question, children: [{question, snippet}, ...]}, ...]``."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        pq = str(item.get("parent_question") or "").strip()
        raw_ch = item.get("children")
        if not pq or not isinstance(raw_ch, list):
            continue
        children: list[dict[str, str]] = []
        for ch in raw_ch:
            if not isinstance(ch, dict):
                continue
            q = str(ch.get("question") or "").strip()
            if not q:
                continue
            sn = str(ch.get("snippet") or "").strip()
            children.append({"question": q, "snippet": sn})
            if len(children) >= 80:
                break
        if children:
            out.append({"parent_question": pq, "children": children})
    return out


def normalize_top_ranking_pages_json(value: Any) -> list[dict[str, str]]:
    """Coerce ``top_ranking_pages`` / DB JSON to ``[{title, url}, ...]`` (accepts legacy ``link``)."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        if not title:
            title = url if len(url) <= 120 else url[:117] + "…"
        out.append({"title": title, "url": url})
        if len(out) >= 20:
            break
    return out


def normalize_related_searches_json(value: Any) -> list[dict[str, Any]]:
    """Coerce SerpAPI ``related_searches`` to ``[{query, position}, ...]`` (1-based position when missing)."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        q = str(item.get("query") or "").strip()
        if not q:
            continue
        pos_raw = item.get("position", i + 1)
        try:
            if isinstance(pos_raw, bool):
                pos_i = i + 1
            elif isinstance(pos_raw, int):
                pos_i = pos_raw
            elif isinstance(pos_raw, float) and pos_raw.is_integer():
                pos_i = int(pos_raw)
            else:
                pos_i = int(float(str(pos_raw).strip()))
        except (TypeError, ValueError):
            pos_i = i + 1
        out.append({"query": q, "position": pos_i})
        if len(out) >= 40:
            break
    return out


def serialize_ai_overview_json(value: Any) -> str:
    """Serialize SerpAPI ``ai_overview`` subset for SQLite; ``{}`` when absent."""
    if value is None:
        return "{}"
    if isinstance(value, str):
        s = value.strip()
        return s if s else "{}"
    if isinstance(value, dict):
        if not value.get("text_blocks") and not value.get("references"):
            return "{}"
        return json.dumps(value, ensure_ascii=False)
    return "{}"


def parse_ai_overview_json(raw: Any) -> dict[str, Any] | None:
    """Load ``ai_overview`` for API; ``None`` when empty or invalid."""
    if raw is None or raw == "":
        return None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if not obj.get("text_blocks") and not obj.get("references"):
        return None
    return obj


# ---------------------------------------------------------------------------
# Article Ideas — gap analysis + CRUD
# ---------------------------------------------------------------------------


def fetch_article_idea_inputs(conn: sqlite3.Connection) -> dict[str, Any]:
    """Gather all datapoints needed to generate article ideas.

    Returns a dict with:
    - cluster_gaps: blog/buying-guide clusters with no existing article, enriched with
      top-5 keyword metrics (volume, KD, CPC, intent, ranking_status, gsc_position,
      content_format_hint, serp_features) plus cluster-level dominant_serp_features,
      content_format_hints, avg_cps when those columns exist
    - competitor_gaps: informational competitor gaps not already covered by cluster keywords
    - competitor_gaps_dedupe_skipped: count of competitor-gap rows omitted as duplicates of cluster keywords
    - collection_gaps: collections with high GSC impressions but no supporting article
    - informational_query_gaps: GSC queries with informational intent landing on non-article pages
    - existing_article_titles: titles already covered (to avoid duplicates)
    - top_collections: top collections by GSC impressions (for context)
    """
    cluster_stat_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()
    }
    cluster_stats_sql = (
        ", c.dominant_serp_features, c.content_format_hints, c.avg_cps"
        if "dominant_serp_features" in cluster_stat_cols
        else ", NULL AS dominant_serp_features, NULL AS content_format_hints, NULL AS avg_cps"
    )

    # 1. Blog/buying-guide clusters with no matching article.
    #    Only surface content_type values that map to blog posts.
    cluster_rows = conn.execute(
        f"""
        SELECT c.id, c.name, c.primary_keyword, c.content_brief,
               c.total_volume, c.avg_difficulty, c.avg_opportunity,
               c.content_type, c.match_type, c.match_handle, c.match_title
               {cluster_stats_sql}
        FROM clusters c
        WHERE c.content_type IN ('blog_post', 'buying_guide')
          AND NOT EXISTS (
            SELECT 1 FROM blog_articles ba
            WHERE (
                LOWER(ba.title) LIKE '%' || LOWER(c.primary_keyword) || '%'
                OR LOWER(ba.seo_title) LIKE '%' || LOWER(c.primary_keyword) || '%'
                OR LOWER(ba.body) LIKE '%' || LOWER(c.primary_keyword) || '%'
            )
        )
        ORDER BY c.total_volume DESC, c.avg_opportunity DESC
        LIMIT 12
        """
    ).fetchall()

    # For each cluster, fetch top 5 keywords by opportunity from keyword_metrics.
    # Fall back to cluster_keywords-only rows if keyword_metrics has no data yet.
    cluster_gaps: list[dict[str, Any]] = []
    for r in cluster_rows:
        cluster_id = r[0]
        kw_rows = conn.execute(
            """
            SELECT ck.keyword,
                   COALESCE(km.volume, 0)               AS volume,
                   COALESCE(km.difficulty, 0)            AS difficulty,
                   COALESCE(km.cpc, 0.0)                 AS cpc,
                   COALESCE(km.intent, 'informational')  AS intent,
                   COALESCE(km.ranking_status, 'not_ranking') AS ranking_status,
                   km.gsc_position,
                   COALESCE(km.opportunity, 0.0)         AS opportunity,
                   km.clicks,
                   km.cps,
                   km.content_format_hint,
                   km.serp_features,
                   km.word_count,
                   km.first_seen,
                   COALESCE(km.traffic_potential, 0)     AS traffic_potential,
                   COALESCE(km.global_volume, 0)         AS global_volume
            FROM cluster_keywords ck
            LEFT JOIN keyword_metrics km ON LOWER(km.keyword) = LOWER(ck.keyword)
            WHERE ck.cluster_id = ?
            ORDER BY km.opportunity DESC NULLS LAST
            LIMIT 5
            """,
            (cluster_id,),
        ).fetchall()

        top_keywords = [
            {
                "keyword": kw[0],
                "volume": int(kw[1] or 0),
                "difficulty": int(kw[2] or 0),
                "cpc": round(float(kw[3] or 0), 2),
                "intent": kw[4],
                "ranking_status": kw[5],
                "gsc_position": round(float(kw[6]), 1) if kw[6] is not None else None,
                "clicks": round(float(kw[8] or 0), 1) if kw[8] is not None else None,
                "cps": round(float(kw[9] or 0), 2) if kw[9] is not None else None,
                "content_format_hint": kw[10] or "",
                "serp_features_compact": kw[11][:80] if kw[11] else "",
                "word_count": int(kw[12]) if kw[12] is not None else None,
                "first_seen": kw[13] or None,
                "traffic_potential": int(kw[14] or 0),
                "global_volume": int(kw[15] or 0),
            }
            for kw in kw_rows
        ]

        # Flag if any keyword is a quick-win or striking-distance opportunity
        has_ranking_opportunity = any(
            kw["ranking_status"] in ("quick_win", "striking_distance")
            for kw in top_keywords
        )

        dsf = (r[11] or "").strip()
        cfh = (r[12] or "").strip()
        ac_raw = r[13]
        avg_cps_cluster = round(float(ac_raw), 2) if ac_raw is not None else 0.0

        cluster_gaps.append(
            {
                "id": r[0],
                "name": r[1],
                "primary_keyword": r[2],
                "content_brief": r[3],
                "total_volume": r[4] or 0,
                "avg_difficulty": round(float(r[5] or 0), 1),
                "avg_opportunity": round(float(r[6] or 0), 1),
                "content_type": r[7],
                "match_type": r[8],
                "match_handle": r[9],
                "match_title": r[10],
                "dominant_serp_features": dsf,
                "content_format_hints": cfh,
                "avg_cps": avg_cps_cluster,
                "top_keywords": top_keywords,
                "has_ranking_opportunity": has_ranking_opportunity,
            }
        )

    # Enrich cluster_gaps with keyword coverage against the suggested match content.
    from backend.app.services.keyword_clustering import _check_keyword_coverage
    import re as _re
    for cg in cluster_gaps:
        all_kw_rows = conn.execute(
            "SELECT keyword FROM cluster_keywords WHERE cluster_id = ?",
            (cg["id"],),
        ).fetchall()
        all_kws = [kr[0] for kr in all_kw_rows]
        match_content = ""
        mt, mh = cg.get("match_type"), cg.get("match_handle")
        if mt == "collection" and mh:
            mc_row = conn.execute(
                "SELECT seo_title, seo_description, description_html FROM collections WHERE handle = ?",
                (mh,),
            ).fetchone()
            if mc_row:
                match_content = " ".join(mc_row[i] or "" for i in range(3))
        elif mt == "page" and mh:
            mc_row = conn.execute(
                "SELECT seo_title, seo_description, body FROM pages WHERE handle = ?",
                (mh,),
            ).fetchone()
            if mc_row:
                match_content = " ".join(mc_row[i] or "" for i in range(3))
        elif mt == "blog_article" and mh and "/" in mh:
            parts = mh.split("/", 1)
            mc_row = conn.execute(
                "SELECT seo_title, seo_description, body FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                (parts[0], parts[1]),
            ).fetchone()
            if mc_row:
                match_content = " ".join(mc_row[i] or "" for i in range(3))
        found, total = _check_keyword_coverage(all_kws, match_content) if all_kws else (0, 0)
        cg["coverage_found"] = found
        cg["coverage_total"] = total
        cg["coverage_ratio"] = f"{found}/{total}" if total else "0/0"

    # Enrich cluster_gaps with keyword_page_map: find the best-ranking existing page
    # for each cluster's primary keyword.
    _kpm_cols = {row[1] for row in conn.execute("PRAGMA table_info(keyword_page_map)").fetchall()}
    if _kpm_cols:
        for cg in cluster_gaps:
            try:
                kpm_row = conn.execute(
                    """
                    SELECT object_type, object_handle, COALESCE(gsc_position, 999) AS pos
                    FROM keyword_page_map
                    WHERE LOWER(keyword) = LOWER(?)
                    ORDER BY pos ASC
                    LIMIT 1
                    """,
                    (cg["primary_keyword"],),
                ).fetchone()
                if kpm_row:
                    cg["existing_page"] = {
                        "object_type": kpm_row[0],
                        "object_handle": kpm_row[1],
                        "gsc_position": round(float(kpm_row[2]), 1) if kpm_row[2] < 900 else None,
                    }
                else:
                    cg["existing_page"] = None
            except Exception:
                cg["existing_page"] = None
    else:
        for cg in cluster_gaps:
            cg["existing_page"] = None

    # 2. Collections with impressions > 200 but no supporting article
    coll_col_names = {
        row[1] for row in conn.execute("PRAGMA table_info(collections)").fetchall()
    }
    ga4_col = ", COALESCE(col.ga4_sessions, 0) AS ga4_sessions" if "ga4_sessions" in coll_col_names else ", 0 AS ga4_sessions"
    collection_gaps = conn.execute(
        f"""
        SELECT col.handle, col.title,
               COALESCE(col.gsc_impressions, 0) AS gsc_impressions,
               COALESCE(col.gsc_clicks, 0)      AS gsc_clicks,
               COALESCE(col.gsc_position, 0.0)  AS gsc_position
               {ga4_col}
        FROM collections col
        WHERE COALESCE(col.gsc_impressions, 0) > 200
        AND NOT EXISTS (
            SELECT 1 FROM blog_articles ba
            WHERE LOWER(ba.title) LIKE '%' || LOWER(col.title) || '%'
               OR LOWER(ba.body)  LIKE '%' || col.handle || '%'
        )
        ORDER BY col.gsc_impressions DESC
        LIMIT 8
        """
    ).fetchall()

    # 3. Top informational GSC queries landing on non-article pages
    from shopifyseo.market_context import get_primary_country_code, country_display_name
    _mkt_country_lower = country_display_name(get_primary_country_code(conn)).lower()
    informational_query_gaps = conn.execute(
        """
        SELECT qr.query,
               SUM(qr.impressions) AS total_impressions,
               SUM(qr.clicks)      AS total_clicks,
               AVG(qr.position)    AS avg_position,
               qr.object_type
        FROM gsc_query_rows qr
        WHERE qr.object_type IN ('product', 'collection', 'page')
          AND (
               qr.query LIKE 'how%'
            OR qr.query LIKE 'best%'
            OR qr.query LIKE 'top%'
            OR qr.query LIKE 'what%'
            OR qr.query LIKE 'why%'
            OR qr.query LIKE 'guide%'
            OR qr.query LIKE 'review%'
            OR qr.query LIKE '%vs%'
            OR qr.query LIKE '%difference%'
            OR qr.query LIKE '%' || ? || '%'
          )
          AND NOT EXISTS (
              SELECT 1 FROM blog_articles ba
              WHERE LOWER(ba.title) LIKE '%' || LOWER(TRIM(qr.query)) || '%'
                 OR LOWER(ba.seo_title) LIKE '%' || LOWER(TRIM(qr.query)) || '%'
          )
        GROUP BY qr.query
        ORDER BY total_impressions DESC
        LIMIT 15
        """,
        (_mkt_country_lower,),
    ).fetchall()

    # 4. Existing article titles — to avoid suggesting duplicates
    existing_articles = conn.execute(
        """
        SELECT title, seo_title, blog_handle
        FROM blog_articles
        WHERE title IS NOT NULL AND TRIM(title) != ''
        ORDER BY published_at DESC NULLS LAST
        LIMIT 30
        """
    ).fetchall()

    # 5. Top collections for context (used to suggest internal link opportunities)
    top_collections = conn.execute(
        """
        SELECT handle, title,
               COALESCE(gsc_impressions, 0) AS gsc_impressions,
               COALESCE(gsc_clicks, 0)      AS gsc_clicks
        FROM collections
        ORDER BY gsc_impressions DESC
        LIMIT 10
        """
    ).fetchall()

    # 6. Competitor keyword gaps — omit keywords already covered by cluster gaps (dedupe noise)
    cluster_kw_norm: set[str] = set()
    for cg in cluster_gaps:
        pk = (cg.get("primary_keyword") or "").strip().lower()
        if pk:
            cluster_kw_norm.add(pk)
        for tk in cg.get("top_keywords") or []:
            k = (tk.get("keyword") or "").strip().lower()
            if k:
                cluster_kw_norm.add(k)

    competitor_gaps_dedupe_skipped = 0
    competitor_gaps: list[dict[str, Any]] = []
    try:
        _ckg_cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_keyword_gaps)").fetchall()}
        _ckg_pos_col = "ckg.competitor_position" if "competitor_position" in _ckg_cols else "NULL AS competitor_position"
        _ckg_url_col = "ckg.competitor_url" if "competitor_url" in _ckg_cols else "NULL AS competitor_url"
        competitor_gap_rows = conn.execute(
            f"""
            SELECT ckg.keyword, ckg.competitor_domain, ckg.volume,
                   ckg.difficulty, ckg.traffic_potential, ckg.gap_type,
                   km.content_format_hint, km.intent,
                   {_ckg_pos_col}, {_ckg_url_col}
            FROM competitor_keyword_gaps ckg
            LEFT JOIN keyword_metrics km ON LOWER(ckg.keyword) = LOWER(km.keyword)
            WHERE COALESCE(km.intent, 'informational') = 'informational'
              AND ckg.volume > 50
            ORDER BY ckg.volume DESC
            LIMIT 40
            """
        ).fetchall()
        raw_competitor = [
            {
                "keyword": r[0],
                "competitor_domain": r[1],
                "volume": int(r[2] or 0),
                "difficulty": int(r[3] or 0),
                "traffic_potential": int(r[4] or 0),
                "gap_type": r[5],
                "content_format_hint": r[6] or "",
                "intent": r[7] or "informational",
                "competitor_position": r[8],
                "competitor_url": r[9] or "",
            }
            for r in competitor_gap_rows
        ]
        for row in raw_competitor:
            kn = (row["keyword"] or "").strip().lower()
            if kn in cluster_kw_norm:
                competitor_gaps_dedupe_skipped += 1
                continue
            competitor_gaps.append(row)
            if len(competitor_gaps) >= 10:
                break
    except Exception:
        competitor_gaps = []

    # 7. Top competitor pages driving traffic (content landscape)
    try:
        _ctp_cols = {row[1] for row in conn.execute("PRAGMA table_info(competitor_top_pages)").fetchall()}
        _ctp_tv_col = "traffic_value" if "traffic_value" in _ctp_cols else "0 AS traffic_value"
        winning_content_rows = conn.execute(
            f"""
            SELECT competitor_domain, url, top_keyword, top_keyword_volume,
                   estimated_traffic, {_ctp_tv_col}, page_type
            FROM competitor_top_pages
            WHERE estimated_traffic > 0
            ORDER BY estimated_traffic DESC
            LIMIT 15
            """
        ).fetchall()
        competitor_winning_content = [
            {
                "competitor": r[0],
                "url_path": r[1].split("/", 3)[-1] if "/" in r[1] else r[1],
                "keyword": r[2],
                "volume": int(r[3] or 0),
                "traffic": int(r[4] or 0),
                "traffic_value": int(r[5] or 0),
                "page_type": r[6] or "",
            }
            for r in winning_content_rows
        ]
    except Exception:
        competitor_winning_content = []

    # 8. Vendor context: top brands by product count
    try:
        vendor_rows = conn.execute(
            """
            SELECT vendor, COUNT(*) AS product_count
            FROM products
            WHERE vendor IS NOT NULL AND TRIM(vendor) != ''
            GROUP BY vendor
            ORDER BY product_count DESC
            LIMIT 8
            """
        ).fetchall()
        vendor_context = [{"vendor": r[0], "product_count": int(r[1])} for r in vendor_rows]
    except Exception:
        vendor_context = []

    # 9. Top organic articles (by GSC clicks) — signals proven content categories
    try:
        top_article_rows = conn.execute(
            """
            SELECT title, blog_handle,
                   COALESCE(gsc_clicks, 0)      AS gsc_clicks,
                   COALESCE(gsc_impressions, 0) AS gsc_impressions
            FROM blog_articles
            WHERE gsc_clicks > 0 AND title IS NOT NULL
            ORDER BY gsc_clicks DESC
            LIMIT 5
            """
        ).fetchall()
        top_organic_articles = [
            {
                "title": r[0],
                "blog_handle": r[1],
                "gsc_clicks": int(r[2]),
                "gsc_impressions": int(r[3]),
            }
            for r in top_article_rows
        ]
    except Exception:
        top_organic_articles = []

    # 10. Geo/device signals from GSC dimensional rows
    try:
        country_rows = conn.execute(
            """
            SELECT dimension_value, SUM(impressions) AS total_impressions
            FROM gsc_query_dimension_rows
            WHERE dimension_kind = 'country'
            GROUP BY dimension_value
            ORDER BY total_impressions DESC
            LIMIT 5
            """
        ).fetchall()
        top_countries = [
            {"country": r[0], "impressions": int(r[1] or 0)} for r in country_rows
        ]
        device_rows = conn.execute(
            """
            SELECT dimension_value, SUM(impressions) AS total_impressions
            FROM gsc_query_dimension_rows
            WHERE dimension_kind = 'device'
            GROUP BY dimension_value
            ORDER BY total_impressions DESC
            """
        ).fetchall()
        device_split = [
            {"device": r[0], "impressions": int(r[1] or 0)} for r in device_rows
        ]
    except Exception:
        top_countries = []
        device_split = []

    # 11. Rejected ideas — avoid reprising dismissed topics
    try:
        rejected_rows = conn.execute(
            """
            SELECT suggested_title, primary_keyword
            FROM article_ideas
            WHERE status = 'rejected'
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        rejected_ideas = [
            {"title": r[0], "primary_keyword": r[1] or ""}
            for r in rejected_rows
        ]
    except Exception:
        rejected_ideas = []

    # 12. Queued ideas — avoid suggesting keywords already in any active stage of the pipeline
    try:
        queued_rows = conn.execute(
            """
            SELECT primary_keyword
            FROM article_ideas
            WHERE status IN ('idea', 'approved', 'published')
              AND primary_keyword != ''
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
        queued_keywords = [r[0] for r in queued_rows]
    except Exception:
        queued_keywords = []

    return {
        "cluster_gaps": cluster_gaps,
        "competitor_gaps": competitor_gaps,
        "competitor_gaps_dedupe_skipped": competitor_gaps_dedupe_skipped,
        "competitor_winning_content": competitor_winning_content,
        "collection_gaps": [
            {
                "handle": r[0],
                "title": r[1],
                "gsc_impressions": int(r[2]),
                "gsc_clicks": int(r[3]),
                "gsc_position": round(float(r[4] or 0), 1),
                "ga4_sessions": int(r[5] or 0),
            }
            for r in collection_gaps
        ],
        "informational_query_gaps": [
            {
                "query": r[0],
                "total_impressions": int(r[1] or 0),
                "total_clicks": int(r[2] or 0),
                "avg_position": round(float(r[3] or 0), 1),
                "object_type": r[4],
            }
            for r in informational_query_gaps
        ],
        "existing_article_titles": [
            {"title": r[0], "seo_title": r[1], "blog_handle": r[2]}
            for r in existing_articles
        ],
        "top_collections": [
            {"handle": r[0], "title": r[1], "gsc_impressions": int(r[2]), "gsc_clicks": int(r[3])}
            for r in top_collections
        ],
        "vendor_context": vendor_context,
        "top_organic_articles": top_organic_articles,
        "top_countries": top_countries,
        "device_split": device_split,
        "rejected_ideas": rejected_ideas,
        "queued_keywords": queued_keywords,
    }


def _lookup_object_title(
    conn: sqlite3.Connection, object_type: str, object_handle: str
) -> str:
    """Return the display title for a store object, or empty string if unknown."""
    h = (object_handle or "").strip()
    if not h:
        return ""
    try:
        if object_type == "collection":
            row = conn.execute(
                "SELECT title FROM collections WHERE handle = ?", (h,)
            ).fetchone()
        elif object_type == "product":
            row = conn.execute(
                "SELECT title FROM products WHERE handle = ?", (h,)
            ).fetchone()
        elif object_type == "page":
            row = conn.execute(
                "SELECT title FROM pages WHERE handle = ?", (h,)
            ).fetchone()
        elif object_type == "blog_article":
            if "/" not in h:
                return ""
            bh, ah = h.split("/", 1)
            row = conn.execute(
                "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                (bh, ah),
            ).fetchone()
        else:
            return ""
    except Exception:
        return ""
    if not row:
        return ""
    return (row[0] or "").strip()


def resolve_idea_targets(
    conn: sqlite3.Connection,
    cluster_meta: dict[str, Any],
    *,
    linked_collection_handle: str = "",
    linked_collection_title: str = "",
    max_secondary: int = 5,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compute primary + secondary interlink targets for an article idea.

    Primary (authority page): cluster's match_* → existing_page (best-ranking page
    from keyword_page_map) → linked_collection fallback. All sources are
    server-side, from data already loaded by fetch_article_idea_inputs.

    Secondary (up to max_secondary, deduped against primary): top-ranking page per
    cluster keyword from keyword_page_map; each entry carries `anchor_keyword`
    so draft generation can use proper SEO anchor text.

    Returns (primary_dict_or_empty, [secondary_dicts]). Primary may be an empty
    dict when no page can be resolved.
    """
    from . import dashboard_queries as _dq

    base_url = (_dq._base_store_url(conn) or "").strip().rstrip("/")

    def _entry(obj_type: str, handle: str, title: str, anchor: str = "", source: str = "") -> dict[str, Any]:
        url = _dq.object_url_with_base(base_url, obj_type, handle)
        return {
            "type": obj_type,
            "handle": handle,
            "title": title,
            "url": url,
            "anchor_keyword": anchor,
            "source": source,
        }

    # ── Primary: prefer explicit cluster match, then existing_page, then linked_collection.
    primary: dict[str, Any] = {}
    mt = (cluster_meta.get("match_type") or "").strip()
    mh = (cluster_meta.get("match_handle") or "").strip()
    if mt and mt != "new" and mh:
        title = (cluster_meta.get("match_title") or "").strip() or _lookup_object_title(conn, mt, mh) or mh
        primary = _entry(mt, mh, title, source="cluster_match")

    if not primary:
        ep = cluster_meta.get("existing_page")
        if ep and ep.get("object_type") and ep.get("object_handle"):
            etype = ep["object_type"]
            ehandle = ep["object_handle"]
            etitle = _lookup_object_title(conn, etype, ehandle) or ehandle
            primary = _entry(etype, ehandle, etitle, source="existing_page")

    if not primary and linked_collection_handle:
        ctitle = linked_collection_title or _lookup_object_title(conn, "collection", linked_collection_handle) or linked_collection_handle
        primary = _entry("collection", linked_collection_handle, ctitle, source="linked_collection")

    # ── Secondary: top-ranking page per cluster keyword from keyword_page_map.
    secondary: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if primary:
        seen.add((primary["type"], primary["handle"]))

    kpm_cols = {row[1] for row in conn.execute("PRAGMA table_info(keyword_page_map)").fetchall()}
    if not kpm_cols:
        return primary, secondary

    keywords_ordered: list[str] = []
    pk = (cluster_meta.get("primary_keyword") or "").strip()
    if pk:
        keywords_ordered.append(pk)
    for kw in cluster_meta.get("top_keywords") or []:
        k = (kw.get("keyword") or "").strip() if isinstance(kw, dict) else ""
        if k and k.lower() not in {x.lower() for x in keywords_ordered}:
            keywords_ordered.append(k)

    for kw in keywords_ordered:
        if len(secondary) >= max_secondary:
            break
        try:
            row = conn.execute(
                """
                SELECT object_type, object_handle
                FROM keyword_page_map
                WHERE LOWER(keyword) = LOWER(?)
                  AND object_type IN ('collection', 'product', 'page', 'blog_article')
                  AND object_handle IS NOT NULL AND TRIM(object_handle) != ''
                ORDER BY COALESCE(gsc_position, 999) ASC
                LIMIT 1
                """,
                (kw,),
            ).fetchone()
        except Exception:
            continue
        if not row:
            continue
        otype, ohandle = row[0], (row[1] or "").strip()
        if not otype or not ohandle:
            continue
        key = (otype, ohandle)
        if key in seen:
            continue
        title = _lookup_object_title(conn, otype, ohandle)
        if not title:
            continue
        seen.add(key)
        secondary.append(_entry(otype, ohandle, title, anchor=kw, source="keyword_page_map"))

    return primary, secondary


def _linked_keywords_json_for_db(value: Any) -> str:
    """Serialize linked keyword rows for SQLite (list or JSON string from AI pipeline)."""
    if value is None or value == "":
        return "[]"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def save_article_ideas(conn: sqlite3.Connection, ideas: list[dict[str, Any]]) -> list[int]:
    """Persist a list of article idea dicts and return their new IDs."""
    now = int(time.time())
    ids = []
    for idea in ideas:
        supporting = json.dumps(idea.get("supporting_keywords") or [], ensure_ascii=False)
        primary_target = idea.get("primary_target") or {}
        secondary_targets = idea.get("secondary_targets") or []
        aq = idea.get("audience_questions")
        audience_json = json.dumps(normalize_audience_questions_json(aq), ensure_ascii=False)
        trp = idea.get("top_ranking_pages")
        top_pages_json = json.dumps(normalize_top_ranking_pages_json(trp), ensure_ascii=False)
        aio = serialize_ai_overview_json(idea.get("ai_overview"))
        rs_json = json.dumps(
            normalize_related_searches_json(idea.get("related_searches")),
            ensure_ascii=False,
        )
        cur = conn.execute(
            """
            INSERT INTO article_ideas
                (suggested_title, brief, primary_keyword, supporting_keywords,
                 search_intent, linked_cluster_id, linked_cluster_name,
                 linked_collection_handle, linked_collection_title,
                 gap_reason, status, created_at,
                 content_format, estimated_monthly_traffic, source_type,
                 total_volume, avg_difficulty, opportunity_score,
                 dominant_serp_features, content_format_hints, linked_keywords_json,
                 primary_target_type, primary_target_handle, primary_target_title,
                 primary_target_url, secondary_targets_json, audience_questions_json,
                 top_ranking_pages_json, ai_overview_json, related_searches_json, paa_expansion_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idea', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idea.get("suggested_title", ""),
                idea.get("brief", ""),
                idea.get("primary_keyword", ""),
                supporting,
                idea.get("search_intent", "informational"),
                idea.get("linked_cluster_id"),
                idea.get("linked_cluster_name", ""),
                idea.get("linked_collection_handle", ""),
                idea.get("linked_collection_title", ""),
                idea.get("gap_reason", ""),
                now,
                idea.get("content_format", ""),
                int(idea.get("estimated_monthly_traffic") or 0),
                idea.get("source_type", "cluster_gap"),
                int(idea.get("total_volume") or 0),
                round(float(idea.get("avg_difficulty") or 0.0), 1),
                round(float(idea.get("opportunity_score") or 0.0), 1),
                idea.get("dominant_serp_features", ""),
                idea.get("content_format_hints", ""),
                _linked_keywords_json_for_db(idea.get("linked_keywords_json")),
                str(primary_target.get("type") or ""),
                str(primary_target.get("handle") or ""),
                str(primary_target.get("title") or ""),
                str(primary_target.get("url") or ""),
                json.dumps(secondary_targets, ensure_ascii=False),
                audience_json,
                top_pages_json,
                aio,
                rs_json,
                "[]",
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids


def fetch_article_ideas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all stored article ideas with aggregate article/GSC metrics, newest first."""
    _AGG_SQL = """
        SELECT ai.id, ai.suggested_title, ai.brief, ai.primary_keyword,
               ai.supporting_keywords,
               ai.search_intent, ai.linked_cluster_id, ai.linked_cluster_name,
               ai.linked_collection_handle, ai.linked_collection_title,
               ai.gap_reason, ai.status, ai.created_at,
               COALESCE(ai.content_format, '')              AS content_format,
               COALESCE(ai.estimated_monthly_traffic, 0)   AS estimated_monthly_traffic,
               COALESCE(ai.source_type, 'cluster_gap')     AS source_type,
               COALESCE(ai.total_volume, 0)                AS total_volume,
               COALESCE(ai.avg_difficulty, 0.0)            AS avg_difficulty,
               COALESCE(ai.opportunity_score, 0.0)         AS opportunity_score,
               COALESCE(ai.dominant_serp_features, '')     AS dominant_serp_features,
               COALESCE(ai.content_format_hints, '')       AS content_format_hints,
               COALESCE(ai.linked_keywords_json, '[]')     AS linked_keywords_json,
               COALESCE(ai.linked_article_handle, '')      AS linked_article_handle,
               COALESCE(ai.linked_blog_handle, '')         AS linked_blog_handle,
               COALESCE(ai.shopify_article_id, '')         AS shopify_article_id,
               COUNT(ia.id)                                AS article_count,
               SUM(COALESCE(ba.gsc_clicks, 0))            AS agg_gsc_clicks,
               SUM(COALESCE(ba.gsc_impressions, 0))       AS agg_gsc_impressions,
               COALESCE(ai.primary_target_type, '')        AS primary_target_type,
               COALESCE(ai.primary_target_handle, '')      AS primary_target_handle,
               COALESCE(ai.primary_target_title, '')       AS primary_target_title,
               COALESCE(ai.primary_target_url, '')         AS primary_target_url,
               COALESCE(ai.secondary_targets_json, '[]')   AS secondary_targets_json,
               COALESCE(ai.audience_questions_json, '[]')  AS audience_questions_json,
               COALESCE(ai.top_ranking_pages_json, '[]')   AS top_ranking_pages_json,
               COALESCE(ai.ai_overview_json, '{}')         AS ai_overview_json,
               COALESCE(ai.related_searches_json, '[]')    AS related_searches_json,
               COALESCE(ai.paa_expansion_json, '[]')       AS paa_expansion_json
        FROM article_ideas ai
        LEFT JOIN idea_articles ia ON ia.idea_id = ai.id
        LEFT JOIN blog_articles ba
               ON ba.handle      = ia.article_handle
              AND ba.blog_handle = ia.blog_handle
        GROUP BY ai.id
        ORDER BY ai.created_at DESC, ai.id DESC
    """
    _FALLBACK_SQL = """
        SELECT ai.id, ai.suggested_title, ai.brief, ai.primary_keyword,
               ai.supporting_keywords,
               ai.search_intent, ai.linked_cluster_id, ai.linked_cluster_name,
               ai.linked_collection_handle, ai.linked_collection_title,
               ai.gap_reason, ai.status, ai.created_at,
               COALESCE(ai.content_format, '')              AS content_format,
               COALESCE(ai.estimated_monthly_traffic, 0)   AS estimated_monthly_traffic,
               COALESCE(ai.source_type, 'cluster_gap')     AS source_type,
               COALESCE(ai.total_volume, 0)                AS total_volume,
               COALESCE(ai.avg_difficulty, 0.0)            AS avg_difficulty,
               COALESCE(ai.opportunity_score, 0.0)         AS opportunity_score,
               COALESCE(ai.dominant_serp_features, '')     AS dominant_serp_features,
               COALESCE(ai.content_format_hints, '')       AS content_format_hints,
               COALESCE(ai.linked_keywords_json, '[]')     AS linked_keywords_json,
               COALESCE(ai.linked_article_handle, '')      AS linked_article_handle,
               COALESCE(ai.linked_blog_handle, '')         AS linked_blog_handle,
               COALESCE(ai.shopify_article_id, '')         AS shopify_article_id,
               0  AS article_count,
               0  AS agg_gsc_clicks,
               0  AS agg_gsc_impressions,
               COALESCE(ai.primary_target_type, '')        AS primary_target_type,
               COALESCE(ai.primary_target_handle, '')      AS primary_target_handle,
               COALESCE(ai.primary_target_title, '')       AS primary_target_title,
               COALESCE(ai.primary_target_url, '')         AS primary_target_url,
               COALESCE(ai.secondary_targets_json, '[]')   AS secondary_targets_json,
               COALESCE(ai.audience_questions_json, '[]')  AS audience_questions_json,
               COALESCE(ai.top_ranking_pages_json, '[]')   AS top_ranking_pages_json,
               COALESCE(ai.ai_overview_json, '{}')         AS ai_overview_json,
               COALESCE(ai.related_searches_json, '[]')    AS related_searches_json,
               COALESCE(ai.paa_expansion_json, '[]')       AS paa_expansion_json
        FROM article_ideas ai
        ORDER BY ai.created_at DESC, ai.id DESC
    """
    try:
        rows = conn.execute(_AGG_SQL).fetchall()
    except Exception:
        rows = conn.execute(_FALLBACK_SQL).fetchall()
    result = []
    for r in rows:
        try:
            keywords = json.loads(r[4] or "[]")
        except (json.JSONDecodeError, TypeError):
            keywords = []
        raw_lkj = r[21] or "[]"
        try:
            linked_kw_rows = json.loads(raw_lkj) if isinstance(raw_lkj, str) else raw_lkj
        except (json.JSONDecodeError, TypeError):
            linked_kw_rows = []
        if not isinstance(linked_kw_rows, list):
            linked_kw_rows = []

        primary_target_type = (r[28] or "").strip()
        primary_target_handle = (r[29] or "").strip()
        primary_target_title = (r[30] or "").strip()
        primary_target_url = (r[31] or "").strip()
        primary_target: dict | None
        if primary_target_type and primary_target_handle:
            primary_target = {
                "type": primary_target_type,
                "handle": primary_target_handle,
                "title": primary_target_title,
                "url": primary_target_url,
            }
        else:
            primary_target = None

        raw_stj = r[32] or "[]"
        try:
            secondary_targets = json.loads(raw_stj) if isinstance(raw_stj, str) else raw_stj
        except (json.JSONDecodeError, TypeError):
            secondary_targets = []
        if not isinstance(secondary_targets, list):
            secondary_targets = []

        raw_aq = r[33] or "[]"
        try:
            raw_list = json.loads(raw_aq) if isinstance(raw_aq, str) else raw_aq
        except (json.JSONDecodeError, TypeError):
            raw_list = []
        audience_questions = normalize_audience_questions_json(raw_list)

        raw_trp = r[34] or "[]"
        try:
            raw_pages = json.loads(raw_trp) if isinstance(raw_trp, str) else raw_trp
        except (json.JSONDecodeError, TypeError):
            raw_pages = []
        top_ranking_pages = normalize_top_ranking_pages_json(raw_pages)

        raw_aio = r[35] or "{}"
        ai_overview = parse_ai_overview_json(raw_aio)

        raw_rs = r[36] or "[]"
        try:
            raw_rs_list = json.loads(raw_rs) if isinstance(raw_rs, str) else raw_rs
        except (json.JSONDecodeError, TypeError):
            raw_rs_list = []
        related_searches = normalize_related_searches_json(raw_rs_list)

        raw_paa_ex = r[37] or "[]"
        try:
            raw_paa_ex_list = json.loads(raw_paa_ex) if isinstance(raw_paa_ex, str) else raw_paa_ex
        except (json.JSONDecodeError, TypeError):
            raw_paa_ex_list = []
        paa_expansion = normalize_paa_expansion_json(raw_paa_ex_list)

        result.append(
            {
                "id": r[0],
                "suggested_title": r[1],
                "brief": r[2],
                "primary_keyword": r[3] or "",
                "supporting_keywords": keywords,
                "search_intent": r[5] or "informational",
                "linked_cluster_id": r[6],
                "linked_cluster_name": r[7] or "",
                "linked_collection_handle": r[8] or "",
                "linked_collection_title": r[9] or "",
                "gap_reason": r[10] or "",
                "status": r[11] or "idea",
                "created_at": r[12],
                "content_format": r[13] or "",
                "estimated_monthly_traffic": int(r[14] or 0),
                "source_type": r[15] or "cluster_gap",
                "total_volume": int(r[16] or 0),
                "avg_difficulty": round(float(r[17] or 0.0), 1),
                "opportunity_score": round(float(r[18] or 0.0), 1),
                "dominant_serp_features": r[19] or "",
                "content_format_hints": r[20] or "",
                "linked_keywords_json": linked_kw_rows,
                "linked_article_handle": r[22] or "",
                "linked_blog_handle": r[23] or "",
                "shopify_article_id": r[24] or "",
                "article_count": int(r[25] or 0),
                "agg_gsc_clicks": int(r[26] or 0),
                "agg_gsc_impressions": int(r[27] or 0),
                "primary_target": primary_target,
                "secondary_targets": secondary_targets,
                "audience_questions": audience_questions,
                "top_ranking_pages": top_ranking_pages,
                "ai_overview": ai_overview,
                "related_searches": related_searches,
                "paa_expansion": paa_expansion,
            }
        )
    return result


def refresh_article_idea_serp_snapshot(conn: sqlite3.Connection, idea_id: int) -> dict[str, Any]:
    """Run SerpAPI for the idea's primary keyword; overwrite PAA, organics, AI overview, related searches, and PAA expansion.

    PAA expansion uses ``google_related_questions`` (extra API calls) when the main result includes tokens.

    Raises:
        LookupError: No ``article_ideas`` row for ``idea_id``.
        ValueError: Missing primary keyword or SerpAPI API key in settings.
    """
    from shopifyseo import dashboard_google as dg
    from shopifyseo.audience_questions_api import fetch_serpapi_primary_keyword_snapshot

    row = conn.execute(
        "SELECT id, primary_keyword FROM article_ideas WHERE id = ?",
        (idea_id,),
    ).fetchone()
    if not row:
        raise LookupError("Article idea not found.")
    pk = (str(row[1] or "")).strip()
    if not pk:
        raise ValueError("This idea has no primary keyword — nothing to search on Google.")

    if not (dg.get_service_setting(conn, "serpapi_api_key") or "").strip():
        raise ValueError(
            "Add a SerpAPI API key under Settings → Integrations to refresh SERP snapshot data."
        )

    snap = fetch_serpapi_primary_keyword_snapshot(conn, pk, expand_paa=True)
    aq_json = json.dumps(normalize_audience_questions_json(snap["audience_questions"]), ensure_ascii=False)
    trp_json = json.dumps(normalize_top_ranking_pages_json(snap["top_ranking_pages"]), ensure_ascii=False)
    aio_json = serialize_ai_overview_json(snap.get("ai_overview"))
    rs_json = json.dumps(
        normalize_related_searches_json(snap.get("related_searches")),
        ensure_ascii=False,
    )
    paa_ex_json = json.dumps(normalize_paa_expansion_json(snap.get("paa_expansion")), ensure_ascii=False)
    cur = conn.execute(
        "UPDATE article_ideas SET audience_questions_json = ?, top_ranking_pages_json = ?, "
        "ai_overview_json = ?, related_searches_json = ?, paa_expansion_json = ? WHERE id = ?",
        (aq_json, trp_json, aio_json, rs_json, paa_ex_json, idea_id),
    )
    conn.commit()
    if cur.rowcount < 1:
        raise LookupError("Article idea not found.")

    for loaded in fetch_article_ideas(conn):
        if loaded["id"] == idea_id:
            return loaded
    raise LookupError("Article idea not found.")


def delete_article_idea(conn: sqlite3.Connection, idea_id: int) -> bool:
    """Delete an article idea by ID. Returns True if a row was deleted."""
    cur = conn.execute("DELETE FROM article_ideas WHERE id = ?", (idea_id,))
    conn.commit()
    return cur.rowcount > 0


def update_article_idea_status(conn: sqlite3.Connection, idea_id: int, status: str) -> bool:
    """Update the status of an article idea. Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE article_ideas SET status = ? WHERE id = ?",
        (status, idea_id),
    )
    conn.commit()
    return cur.rowcount > 0


def update_article_idea_targets(
    conn: sqlite3.Connection,
    idea_id: int,
    primary_target: dict[str, Any] | None,
    secondary_targets: list[dict[str, Any]],
    *,
    allowed_keys: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Update an idea's primary + secondary interlink targets.

    When ``allowed_keys`` is provided, every (type, handle) pair submitted must
    appear in it — otherwise we raise ``ValueError``. Targets with unknown
    (type, handle) would bypass the store internal-link allowlist and let the
    drafter emit invented URLs. Empty primary is allowed (clears the authority
    target). Returns the refreshed idea dict or ``None`` when the row is gone.
    """
    row = conn.execute(
        "SELECT status FROM article_ideas WHERE id = ?", (idea_id,)
    ).fetchone()
    if not row:
        return None
    status = (row[0] or "idea").strip()
    if status not in {"idea", "approved"}:
        raise ValueError(
            f"Cannot edit targets on an idea with status '{status}'. "
            "Only 'idea' or 'approved' ideas can be retargeted."
        )

    def _clean_entry(entry: dict[str, Any]) -> dict[str, str] | None:
        otype = str(entry.get("type") or "").strip()
        ohandle = str(entry.get("handle") or "").strip()
        if not otype or not ohandle:
            return None
        if allowed_keys is not None and (otype, ohandle) not in allowed_keys:
            raise ValueError(
                f"Target {otype}:{ohandle} is not a known store page. "
                "Only pages that exist in the store can be interlink targets."
            )
        title = str(entry.get("title") or "").strip() or _lookup_object_title(conn, otype, ohandle) or ohandle
        url = str(entry.get("url") or "").strip()
        if not url:
            from . import dashboard_queries as _dq
            base = (_dq._base_store_url(conn) or "").strip().rstrip("/")
            url = _dq.object_url_with_base(base, otype, ohandle)
        return {
            "type": otype,
            "handle": ohandle,
            "title": title,
            "url": url,
            "anchor_keyword": str(entry.get("anchor_keyword") or "").strip(),
            "source": str(entry.get("source") or "user_override").strip() or "user_override",
        }

    primary_clean: dict[str, str] | None = None
    if primary_target:
        primary_clean = _clean_entry(primary_target)

    secondary_clean: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if primary_clean:
        seen.add((primary_clean["type"], primary_clean["handle"]))
    for entry in secondary_targets or []:
        c = _clean_entry(entry)
        if not c:
            continue
        key = (c["type"], c["handle"])
        if key in seen:
            continue
        seen.add(key)
        secondary_clean.append(c)
        if len(secondary_clean) >= 5:
            break

    conn.execute(
        """UPDATE article_ideas
           SET primary_target_type = ?, primary_target_handle = ?,
               primary_target_title = ?, primary_target_url = ?,
               secondary_targets_json = ?
           WHERE id = ?""",
        (
            primary_clean["type"] if primary_clean else "",
            primary_clean["handle"] if primary_clean else "",
            primary_clean["title"] if primary_clean else "",
            primary_clean["url"] if primary_clean else "",
            json.dumps(secondary_clean, ensure_ascii=False),
            idea_id,
        ),
    )
    conn.commit()

    for loaded in fetch_article_ideas(conn):
        if loaded["id"] == idea_id:
            return loaded
    return None


def link_idea_to_article(
    conn: sqlite3.Connection,
    idea_id: int,
    article_handle: str,
    blog_handle: str,
    shopify_article_id: str,
    angle_label: str = "",
) -> bool:
    """Link an article idea to a Shopify article via the idea_articles junction table."""
    import time as _time
    conn.execute(
        """INSERT OR IGNORE INTO idea_articles
           (idea_id, blog_handle, article_handle, shopify_article_id, angle_label, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (idea_id, blog_handle, article_handle, shopify_article_id, angle_label, int(_time.time())),
    )
    # Legacy columns: keep first article for backward compat. Do not change status — approved ideas stay approved.
    conn.execute(
        """UPDATE article_ideas
           SET linked_article_handle = CASE WHEN linked_article_handle = '' THEN ? ELSE linked_article_handle END,
               linked_blog_handle = CASE WHEN linked_blog_handle = '' THEN ? ELSE linked_blog_handle END,
               shopify_article_id = CASE WHEN shopify_article_id = '' THEN ? ELSE shopify_article_id END
           WHERE id = ?""",
        (article_handle, blog_handle, shopify_article_id, idea_id),
    )
    conn.commit()
    return True


def save_article_target_keywords(
    conn: sqlite3.Connection,
    blog_handle: str,
    article_handle: str,
    primary_keyword: str,
    supporting_keywords: list[str],
) -> None:
    """Copy idea keywords into article_target_keywords at draft time."""
    if primary_keyword:
        conn.execute(
            """INSERT OR IGNORE INTO article_target_keywords
               (blog_handle, article_handle, keyword, is_primary, source)
               VALUES (?, ?, ?, 1, 'idea')""",
            (blog_handle, article_handle, primary_keyword.strip().lower()),
        )
    for kw in supporting_keywords:
        kw_clean = kw.strip().lower() if isinstance(kw, str) else ""
        if kw_clean and kw_clean != primary_keyword.strip().lower():
            conn.execute(
                """INSERT OR IGNORE INTO article_target_keywords
                   (blog_handle, article_handle, keyword, is_primary, source)
                   VALUES (?, ?, ?, 0, 'idea')""",
                (blog_handle, article_handle, kw_clean),
            )
    conn.commit()


def fetch_idea_articles(conn: sqlite3.Connection, idea_id: int) -> list[dict[str, Any]]:
    """Return all articles linked to an idea, with GSC performance from blog_articles."""
    try:
        rows = conn.execute(
            """
            SELECT ia.id, ia.blog_handle, ia.article_handle, ia.shopify_article_id,
                   ia.angle_label, ia.created_at,
                   ba.title AS article_title,
                   ba.is_published,
                   COALESCE(ba.gsc_clicks, 0) AS gsc_clicks,
                   COALESCE(ba.gsc_impressions, 0) AS gsc_impressions,
                   ba.gsc_position
            FROM idea_articles ia
            LEFT JOIN blog_articles ba
                   ON ba.handle = ia.article_handle AND ba.blog_handle = ia.blog_handle
            WHERE ia.idea_id = ?
            ORDER BY ia.created_at DESC
            """,
            (idea_id,),
        ).fetchall()
    except Exception:
        return []
    result = []
    for r in rows:
        result.append({
            "id": r[0],
            "blog_handle": r[1],
            "article_handle": r[2],
            "shopify_article_id": r[3] or "",
            "angle_label": r[4] or "",
            "created_at": r[5],
            "article_title": r[6] or "",
            "is_published": bool(r[7]) if r[7] is not None else False,
            "gsc_clicks": int(r[8] or 0),
            "gsc_impressions": int(r[9] or 0),
            "gsc_position": round(float(r[10]), 1) if r[10] is not None else None,
        })
    return result


def bulk_update_idea_status(conn: sqlite3.Connection, idea_ids: list[int], status: str) -> int:
    """Update status for multiple ideas at once. Returns number of rows updated."""
    if not idea_ids:
        return 0
    placeholders = ",".join("?" for _ in idea_ids)
    cur = conn.execute(
        f"UPDATE article_ideas SET status = ? WHERE id IN ({placeholders})",
        [status] + idea_ids,
    )
    conn.commit()
    return cur.rowcount


def bulk_delete_article_ideas(conn: sqlite3.Connection, idea_ids: list[int]) -> int:
    """Delete multiple article ideas by ID. Returns number of rows removed."""
    ids = sorted({int(i) for i in idea_ids if i is not None})
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(f"DELETE FROM article_ideas WHERE id IN ({placeholders})", ids)
    conn.commit()
    return cur.rowcount


def compute_keyword_coverage(
    conn: sqlite3.Connection,
    blog_handle: str,
    article_handle: str,
) -> dict[str, Any]:
    """Cross-reference article_target_keywords with gsc_query_rows to produce a coverage report."""
    targets = conn.execute(
        "SELECT keyword, is_primary FROM article_target_keywords WHERE blog_handle = ? AND article_handle = ?",
        (blog_handle, article_handle),
    ).fetchall()
    target_map: dict[str, bool] = {}
    for row in targets:
        target_map[row[0].strip().lower()] = bool(row[1])

    obj_handle = f"{blog_handle}/{article_handle}"
    gsc_rows = conn.execute(
        """SELECT query, COALESCE(clicks, 0), COALESCE(impressions, 0), position
           FROM gsc_query_rows
           WHERE object_type = 'blog_article' AND object_handle = ?""",
        (obj_handle,),
    ).fetchall()

    gsc_map: dict[str, dict] = {}
    for r in gsc_rows:
        gsc_map[r[0].strip().lower()] = {
            "query": r[0],
            "clicks": int(r[1]),
            "impressions": int(r[2]),
            "position": round(float(r[3]), 1) if r[3] is not None else None,
        }

    target_keywords = []
    for kw, is_primary in target_map.items():
        match = _fuzzy_gsc_match(kw, gsc_map)
        if match:
            target_keywords.append({
                "keyword": kw,
                "is_primary": is_primary,
                "gsc_clicks": match["clicks"],
                "gsc_impressions": match["impressions"],
                "gsc_position": match["position"],
                "status": "ranking",
            })
        else:
            target_keywords.append({
                "keyword": kw,
                "is_primary": is_primary,
                "gsc_clicks": 0,
                "gsc_impressions": 0,
                "gsc_position": None,
                "status": "not_ranking",
            })

    matched_queries = set()
    for kw in target_map:
        for gq in gsc_map:
            if kw in gq or gq in kw:
                matched_queries.add(gq)

    discovered = []
    for gq, data in gsc_map.items():
        if gq not in matched_queries:
            discovered.append({
                "query": data["query"],
                "clicks": data["clicks"],
                "impressions": data["impressions"],
                "position": data["position"],
            })
    discovered.sort(key=lambda x: x["impressions"], reverse=True)

    ranking_count = sum(1 for t in target_keywords if t["status"] == "ranking")
    total_targets = len(target_keywords)
    gap_count = total_targets - ranking_count
    coverage_pct = round(ranking_count / total_targets * 100, 1) if total_targets else 0.0

    return {
        "target_keywords": target_keywords,
        "discovered_keywords": discovered,
        "summary": {
            "total_targets": total_targets,
            "ranking_count": ranking_count,
            "gap_count": gap_count,
            "discovered_count": len(discovered),
            "coverage_pct": coverage_pct,
        },
    }


def _fuzzy_gsc_match(keyword: str, gsc_map: dict[str, dict]) -> dict | None:
    """Find the best matching GSC query for a target keyword."""
    kw_lower = keyword.strip().lower()
    if kw_lower in gsc_map:
        return gsc_map[kw_lower]
    for gq, data in gsc_map.items():
        if kw_lower in gq or gq in kw_lower:
            return data
    return None


def compute_idea_performance(conn: sqlite3.Connection, idea_id: int) -> dict[str, Any]:
    """Roll up performance across all articles linked to an idea."""
    articles = fetch_idea_articles(conn, idea_id)

    total_clicks = 0
    total_impressions = 0
    positions = []
    published_count = 0

    all_target_kws: dict[str, bool] = {}
    all_ranking_kws: set[str] = set()

    for art in articles:
        total_clicks += art.get("gsc_clicks", 0)
        total_impressions += art.get("gsc_impressions", 0)
        if art.get("gsc_position") is not None:
            positions.append(art["gsc_position"])
        if art.get("is_published"):
            published_count += 1

        try:
            coverage = compute_keyword_coverage(
                conn, art["blog_handle"], art["article_handle"]
            )
            for tk in coverage.get("target_keywords", []):
                kw = tk["keyword"]
                if kw not in all_target_kws:
                    all_target_kws[kw] = tk.get("is_primary", False)
                if tk["status"] == "ranking":
                    all_ranking_kws.add(kw)
        except Exception:
            pass

    total_targets = len(all_target_kws)
    ranking_across = len(all_ranking_kws)
    gap_count = total_targets - ranking_across
    coverage_pct = round(ranking_across / total_targets * 100, 1) if total_targets else 0.0

    return {
        "articles": articles,
        "aggregate": {
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "avg_position": round(sum(positions) / len(positions), 1) if positions else None,
            "article_count": len(articles),
            "published_count": published_count,
        },
        "keyword_coverage": {
            "total_targets": total_targets,
            "ranking_count": ranking_across,
            "gap_count": gap_count,
            "discovered_count": 0,
            "coverage_pct": coverage_pct,
        },
    }
