"""Article idea generation using AI and keyword gap analysis."""
import datetime
import logging
import sqlite3

logger = logging.getLogger(__name__)

from .providers import AIProviderRequestError, _call_ai
from .settings import ai_settings


def generate_article_ideas(conn: sqlite3.Connection) -> list[dict]:
    """Analyse content gaps and return 3 AI-generated article ideas.

    Each idea contains: suggested_title, brief, primary_keyword,
    supporting_keywords, search_intent, linked_cluster_id, linked_cluster_name,
    linked_collection_handle, linked_collection_title, gap_reason.
    Raises RuntimeError on AI failure.
    """
    import shopifyseo.dashboard_queries as dq

    settings = ai_settings(conn)
    provider = settings["generation_provider"]
    model = settings["generation_model"]
    timeout = settings["timeout"]

    from shopifyseo.market_context import (
        get_primary_country_code, country_display_name, spelling_variant,
        subnational_guidance, build_market_prompt_fragment, language_region_code,
    )
    _market_code = get_primary_country_code(conn)
    _market_name = country_display_name(_market_code)
    _market_fragment = build_market_prompt_fragment(conn)

    gap_data = dq.fetch_article_idea_inputs(conn)

    # ── Build context summary for the prompt ──────────────────────────────────
    # Clusters: sort so any with ranking opportunities come first for max impact
    def _cluster_sort_key(c: dict) -> tuple:
        ct = c.get("coverage_total", 0) or 1
        cf = c.get("coverage_found", 0)
        gap_pct = 1.0 - (cf / ct)
        return (gap_pct, c.get("has_ranking_opportunity", False), c.get("total_volume", 0))

    sorted_clusters = sorted(
        gap_data["cluster_gaps"][:10],
        key=_cluster_sort_key,
        reverse=True,
    )

    _ninety_days_ago = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()

    cluster_lines = []
    for c in sorted_clusters:
        vol = f"{c['total_volume']:,}" if c["total_volume"] else "?"
        opp_flag = " ⚡ RANKING OPPORTUNITY" if c.get("has_ranking_opportunity") else ""
        header = (
            f"- Cluster '{c['name']}' (id:{c['id']}) | {c.get('content_type', 'blog_post')} | "
            f"vol:{vol}/mo | avg KD:{c['avg_difficulty']} | avg opp:{c.get('avg_opportunity', 0):.0f}{opp_flag}"
        )
        cluster_lines.append(header)

        # content_brief — cluster's descriptive intent summary
        cb = (c.get("content_brief") or "").strip()
        if cb:
            cluster_lines.append(f"  Brief: {cb}")

        # existing_page — keyword_page_map coverage
        ep = c.get("existing_page")
        if ep:
            pos_str = f" pos:{ep['gsc_position']}" if ep.get("gsc_position") else ""
            cluster_lines.append(
                f"  Already ranking: {ep['object_type']} /{ep['object_handle']}{pos_str} — write as supporting editorial"
            )

        # match context — what existing store page this cluster maps to
        mt = c.get("match_type")
        if mt and mt != "new":
            cluster_lines.append(
                f"  Matched to: {mt} '{c.get('match_title', '')}' (/{c.get('match_handle', '')})"
            )

        agg_bits: list[str] = []
        dsf = (c.get("dominant_serp_features") or "").strip()
        if dsf:
            agg_bits.append(f"SERP mix: {dsf}")
        cfh_c = (c.get("content_format_hints") or "").strip()
        if cfh_c:
            agg_bits.append(f"suggested formats: {cfh_c}")
        ac_c = c.get("avg_cps")
        if ac_c is not None and float(ac_c) > 0:
            agg_bits.append(f"avg CPS: {float(ac_c):.2f}")
        if agg_bits:
            cluster_lines.append("  " + " | ".join(agg_bits))

        for kw in c.get("top_keywords", []):
            rs = kw["ranking_status"]
            if rs == "quick_win":
                badge = f"⚡ QUICK WIN pos:{kw['gsc_position']}"
            elif rs == "striking_distance":
                badge = f"📈 STRIKING DIST pos:{kw['gsc_position']}"
            elif rs == "ranking":
                badge = f"✅ RANKING pos:{kw['gsc_position']}"
            else:
                badge = "not ranking"
            cpc_val = kw.get("cpc") or 0.0
            cpc_str = f"CPC:${cpc_val:.2f}" if cpc_val else ""
            cpc_badge = " 💰 HIGH CPC" if float(cpc_val) >= 1.0 else ""
            parts = [
                f"  • {kw['keyword']}",
                f"vol:{kw['volume']:,}",
                f"KD:{kw['difficulty']}",
            ]
            if cpc_str:
                parts.append(f"{cpc_str}{cpc_badge}")
            parts.append(badge)
            kfmt = (kw.get("content_format_hint") or "").strip()
            if kfmt:
                parts.append(f"fmt:{kfmt[:70]}")
            kserp = (kw.get("serp_features_compact") or "").strip()
            if kserp:
                parts.append(f"serp:{kserp}")
            cps_kw = kw.get("cps")
            if cps_kw is not None and float(cps_kw) > 0:
                parts.append(f"CPS:{float(cps_kw):.2f}")
            clicks_kw = kw.get("clicks")
            if clicks_kw is not None and float(clicks_kw) > 0:
                parts.append(f"clicks:{int(clicks_kw)}/mo")
            tp = kw.get("traffic_potential")
            if tp:
                parts.append(f"tp:{tp:,}")
            wc = kw.get("word_count")
            if wc:
                parts.append(f"top-page-words:{wc}")
            gv = kw.get("global_volume") or 0
            lv = kw.get("volume") or 0
            if gv > lv * 3 and gv > 0:
                parts.append(f"global-vol:{gv:,}")
            fs = kw.get("first_seen") or ""
            # Truncate to date portion in case first_seen has a timestamp suffix
            if fs and fs[:10] >= _ninety_days_ago:
                parts.append("🆕 EMERGING")
            cluster_lines.append(" | ".join(parts))

    collection_lines = []
    for col in gap_data["collection_gaps"][:6]:
        ga4_str = f" | {col['ga4_sessions']:,} GA4 sessions" if col.get("ga4_sessions") else ""
        collection_lines.append(
            f"- Collection '{col['title']}' (handle: {col['handle']}) | "
            f"{col['gsc_impressions']:,} impressions/mo | avg pos {col['gsc_position']}{ga4_str}"
        )

    query_lines = []
    for q in gap_data["informational_query_gaps"][:12]:
        query_lines.append(
            f"- '{q['query']}' | {q['total_impressions']:,} impressions | "
            f"pos {q['avg_position']} | landing on {q['object_type']} page"
        )

    competitor_gap_lines = []
    for cg in gap_data.get("competitor_gaps", [])[:8]:
        hint = f" | format:{cg['content_format_hint']}" if cg.get("content_format_hint") else ""
        pos_str = f" | their pos:{cg['competitor_position']}" if cg.get("competitor_position") else ""
        url_str = f" | their url:{cg['competitor_url']}" if cg.get("competitor_url") else ""
        tp_str = f" | tp:{cg['traffic_potential']:,}" if cg.get("traffic_potential") else ""
        competitor_gap_lines.append(
            f"- '{cg['keyword']}' | vol:{cg['volume']:,} | KD:{cg['difficulty']}{tp_str} | "
            f"competitor: {cg['competitor_domain']}{pos_str}{url_str}{hint}"
        )
    dedupe_skipped = int(gap_data.get("competitor_gaps_dedupe_skipped") or 0)
    competitor_dedupe_note = ""
    if dedupe_skipped > 0:
        competitor_dedupe_note = (
            f"(Skipped {dedupe_skipped} competitor-gap keyword(s) that already appear in the "
            "keyword clusters above — avoid duplicating those topics.)"
        )

    winning_content_lines = []
    for wc in gap_data.get("competitor_winning_content", [])[:10]:
        tv_str = f" | value:${wc['traffic_value']:,}" if wc.get("traffic_value") else ""
        pt_str = f" | type:{wc['page_type']}" if wc.get("page_type") else ""
        winning_content_lines.append(
            f"- {wc['competitor']}: /{wc['url_path']} | kw:'{wc['keyword']}' | "
            f"vol:{wc['volume']:,} | traffic:{wc['traffic']:,}{tv_str}{pt_str}"
        )

    existing_titles = [a["title"] for a in gap_data["existing_article_titles"] if a["title"]]

    top_col_handles = [c["handle"] for c in gap_data["top_collections"][:6]]

    # Vendor context block
    vendor_lines = []
    for v in gap_data.get("vendor_context", [])[:8]:
        vendor_lines.append(f"- {v['vendor']}: {v['product_count']} products")

    # Top organic articles (proven categories)
    top_article_lines = []
    for a in gap_data.get("top_organic_articles", [])[:5]:
        top_article_lines.append(
            f"- '{a['title']}' | {a['gsc_clicks']:,} clicks/mo"
        )

    # Geo/device signals
    geo_lines = []
    for c in gap_data.get("top_countries", [])[:5]:
        geo_lines.append(f"  {c['country']}: {c['impressions']:,} impressions")
    device_lines = []
    for d in gap_data.get("device_split", []):
        device_lines.append(f"  {d['device']}: {d['impressions']:,} impressions")

    # Rejected ideas — do not repeat
    rejected_lines = [
        f"- '{r['title']}'" + (f" (kw: {r['primary_keyword']})" if r.get("primary_keyword") else "")
        for r in gap_data.get("rejected_ideas", [])
    ]

    # Queued ideas (already in pipeline, not yet published) — avoid keyword duplication
    queued_kw_lines = [f"- {kw}" for kw in gap_data.get("queued_keywords", [])]

    # ── RAG enhancements (all optional, try/except) ──────────────────────────
    rag_semantic_gaps_lines: list[str] = []
    rag_dedup_lines: list[str] = []
    rag_keyword_enrichment_lines: list[str] = []
    rag_competitive_signals_lines: list[str] = []

    try:
        from ..embedding_store import (
            _load_embedding_matrix,
            _cosine_similarity,
            _blob_to_array,
            find_semantic_keyword_matches,
            find_competitive_gaps as _find_competitive_gaps,
            find_similar_ideas,
        )

        # 1. Semantic content gap analysis: clusters vs existing articles
        cluster_matrix, cluster_meta = _load_embedding_matrix(conn, ["cluster"])
        article_matrix, article_meta = _load_embedding_matrix(conn, ["blog_article"])
        if cluster_matrix.shape[0] > 0 and article_matrix.shape[0] > 0:
            for ci, cm in enumerate(cluster_meta):
                c_vec = cluster_matrix[ci]
                sims = _cosine_similarity(c_vec, article_matrix)
                max_sim = float(sims.max()) if len(sims) > 0 else 0.0
                if max_sim < 0.6:
                    cid_str = cm["object_handle"]
                    rag_semantic_gaps_lines.append(
                        f"- Cluster '{cid_str}' has NO semantically similar article (max similarity: {max_sim:.2f}) — true content gap"
                    )

        # 2. Idea dedup via embeddings
        existing_idea_rows = conn.execute(
            "SELECT suggested_title, brief FROM article_ideas WHERE status != 'rejected'"
        ).fetchall()
        if existing_idea_rows:
            rag_dedup_lines.append("(These existing idea topics are already covered — avoid overlap:)")
            for ir in existing_idea_rows[:15]:
                rag_dedup_lines.append(f"- {ir['suggested_title']}")

        # 3. Semantic keyword enrichment per cluster gap
        for c in sorted_clusters[:5]:
            cid = c.get("id")
            if not cid:
                continue
            sem_kws = find_semantic_keyword_matches(conn, "cluster", str(cid), top_k=5)
            if sem_kws:
                kw_strs = [f"{k['keyword']} (vol:{k.get('volume', '?')})" for k in sem_kws]
                rag_keyword_enrichment_lines.append(
                    f"- Cluster '{c['name']}': semantically related keywords: {', '.join(kw_strs)}"
                )

        # 4. Competitive content signals per cluster gap
        for c in sorted_clusters[:5]:
            cid = c.get("id")
            if not cid:
                continue
            comp_gaps = _find_competitive_gaps(conn, "cluster", str(cid), top_k=3)
            if comp_gaps:
                gap_strs = [f"{g['competitor_domain']} ranks for '{g['top_keyword']}'" for g in comp_gaps]
                rag_competitive_signals_lines.append(
                    f"- Cluster '{c['name']}': competitors already covering: {'; '.join(gap_strs)}"
                )
    except Exception:
        logger.debug("RAG enhancements unavailable for article idea generation", exc_info=True)

    context_block = "\n".join(
        [
            "=== KEYWORD CLUSTER GAPS (blog/buying-guide clusters with no article coverage) ===",
            "(⚡ QUICK WIN = ranking pos 11-20, one good article could reach page 1; "
            "📈 STRIKING DIST = pos 21-50, strong growth opportunity). "
            "Cluster lines include SERP mix, suggested formats, CPS, SERP hints, "
            "content brief, matched store page, existing page ranking, and word count benchmarks. "
            "🆕 EMERGING = keyword first seen within 90 days. 💰 HIGH CPC = $1+ per click.",
            "\n".join(cluster_lines) if cluster_lines else "(no cluster data available)",
            "",
            "=== COMPETITOR KEYWORD GAPS (informational keywords where competitors rank but we don't) ===",
            competitor_dedupe_note,
            "\n".join(competitor_gap_lines) if competitor_gap_lines else "(no competitor gap data)",
            "",
            "=== COMPETITOR WINNING CONTENT (top pages driving traffic for competitors) ===",
            "(Use this to understand what topics competitors succeed with. Do NOT link to competitor pages.)",
            "\n".join(winning_content_lines) if winning_content_lines else "(no competitor page data)",
            "",
            "=== COLLECTION GAPS (high-impression collections with no supporting article) ===",
            "\n".join(collection_lines) if collection_lines else "(no collection gap data)",
            "",
            "=== INFORMATIONAL QUERY GAPS (search queries landing on non-article pages) ===",
            "\n".join(query_lines) if query_lines else "(no GSC query data available)",
            "",
            "=== TOP VENDOR BRANDS (products in catalogue — use for brand-specific article angles) ===",
            "\n".join(vendor_lines) if vendor_lines else "(no vendor data)",
            "",
            "=== PROVEN CONTENT CATEGORIES (existing articles driving GSC traffic) ===",
            "(Write adjacent/deeper articles in these categories — proven audience interest.)",
            "\n".join(top_article_lines) if top_article_lines else "(no article traffic data)",
            "",
            "=== AUDIENCE GEOGRAPHY & DEVICE ===",
            "Top countries by impressions:",
            "\n".join(geo_lines) if geo_lines else "  (no geo data)",
            "Device split:",
            "\n".join(device_lines) if device_lines else "  (no device data)",
            "",
            "=== EXISTING ARTICLES (do NOT suggest these topics again) ===",
            "\n".join(f"- {t}" for t in existing_titles[:20]) if existing_titles else "(none yet)",
            "",
            "=== REJECTED IDEAS (do NOT suggest similar topics) ===",
            "\n".join(rejected_lines) if rejected_lines else "(none rejected)",
            "",
            "=== QUEUED ARTICLE IDEAS (primary keywords already in the pipeline — do NOT duplicate) ===",
            "\n".join(queued_kw_lines) if queued_kw_lines else "(none queued)",
            "",
            "=== TOP COLLECTIONS FOR INTERNAL LINKS ===",
            ", ".join(top_col_handles) if top_col_handles else "(none)",
        ]
    )

    rag_sections: list[str] = []
    if rag_semantic_gaps_lines:
        rag_sections.append(
            "\n=== SEMANTIC CONTENT GAPS (clusters with no semantically similar article — highest priority) ===\n"
            + "\n".join(rag_semantic_gaps_lines)
        )
    if rag_dedup_lines:
        rag_sections.append(
            "\n=== EXISTING IDEA TOPICS (avoid overlap with these) ===\n"
            + "\n".join(rag_dedup_lines)
        )
    if rag_keyword_enrichment_lines:
        rag_sections.append(
            "\n=== SEMANTICALLY RELATED KEYWORDS PER CLUSTER (from embedding search) ===\n"
            + "\n".join(rag_keyword_enrichment_lines)
        )
    if rag_competitive_signals_lines:
        rag_sections.append(
            "\n=== COMPETITOR CONTENT SIGNALS PER CLUSTER (from embedding search) ===\n"
            + "\n".join(rag_competitive_signals_lines)
        )
    if rag_sections:
        context_block += "\n" + "\n".join(rag_sections)

    from .config import get_store_identity
    _store_name, _store_domain = get_store_identity(conn)
    _brand = _store_name or "the store"

    system_msg = (
        f"You are a senior SEO content strategist for {_brand}. "
        "Your job is to identify high-impact article opportunities based on real keyword gaps, "
        "collection search demand, and informational queries that are landing on the wrong pages. "
        "You create specific, data-driven article briefs — not generic content. "
        f"{spelling_variant(_market_code)} "
        "Every idea must be directly grounded in the gap data provided.\n"
        "Signal interpretation guide:\n"
        "- 'tp:N' = traffic potential (ETV) at #1 rank — use this (not raw volume) for traffic estimates.\n"
        "- 'top-page-words:N' = average word count of top-ranking pages — match content depth accordingly.\n"
        "- 'global-vol:N' = global search volume >> local volume — evergreen, established topic, low risk.\n"
        "- '🆕 EMERGING' = keyword first seen within 90 days — timeliness is a ranking advantage.\n"
        "- '💰 HIGH CPC' = $1+ per click — commercially valuable, prioritise if writing commercial content.\n"
        "- 'Already ranking: ...' = primary keyword already has a ranking page — this article should be "
        "a supporting editorial that links to that page, not a competing standalone.\n"
        f"- Matched to: ... = the cluster maps to an existing {_brand} page — this page should be the "
        "primary internal link target from the article.\n"
        "- Vendor brand data = use to suggest brand-specific buying guides and comparison articles.\n"
        "- Proven content categories = write adjacent or deeper articles in these categories.\n"
        f"- Audience geography = incorporate {subnational_guidance(_market_code)} or regional context when volume is there.\n"
        "- Device split = if mobile impressions dominate, suggest shorter scannable formats.\n"
        "When clusters list SERP mix, suggested formats, or per-keyword format/SERP hints, align the "
        "article angle and content format (e.g. guide vs comparison vs FAQ-style) with those signals. "
        "Do not repeat existing articles, rejected ideas, or queued keywords. Do not invent statistics. "
        "IMPORTANT: Competitor data is provided solely for identifying content opportunities and keyword gaps. "
        f"NEVER suggest linking to competitor websites in any article. Only link to {_brand}'s own pages."
    )

    user_msg = (
        f"Based on the gap analysis below, generate exactly 5 high-impact article ideas for {_brand}. "
        "Prioritise clusters marked ⚡ QUICK WIN or 📈 STRIKING DIST — these are keywords we already rank "
        "for on page 2/3 and a strong article could reach page 1 fast. "
        "Also consider competitor keyword gaps (informational keywords competitors rank for but we don't; "
        "these omit keywords already covered by clusters to avoid duplicate topics), "
        "collections with high impressions but no supporting editorial, "
        "and informational queries currently landing on product/collection pages.\n\n"
        "Use vendor brand data to suggest brand-specific buying guides. "
        "Use proven content categories to suggest adjacent/deeper articles. "
        f"Use audience geography to suggest {_market_name}-market angles (e.g. {subnational_guidance(_market_code).split('(')[0].strip()}-specific, shipping/legal context). "
        "Use device split: if mobile impressions dominate, suggest shorter, scannable formats.\n\n"
        "For each idea, produce:\n"
        f"- suggested_title: The H1 article headline (20–70 chars). Specific, keyword-led. {spelling_variant(_market_code)} "
        "No ALL CAPS. No vague parentheticals.\n"
        "- brief: 3–4 sentences. What the article covers, who it's for, what search intent it serves, "
        f"and how it links to {_brand}'s catalog. Be editorial and specific — not generic. "
        "If the cluster has a quick-win keyword, mention that targeting it could move us to page 1.\n"
        "- primary_keyword: The single most important keyword this article targets. "
        "Prefer a ⚡ QUICK WIN keyword if one exists in the cluster.\n"
        "- supporting_keywords: Array of 3–5 supporting keywords from the same cluster or query gap.\n"
        "- search_intent: One of: 'informational', 'commercial', 'navigational'.\n"
        "- content_format: The best content format for this article. One of: "
        "'how_to', 'buying_guide', 'listicle', 'faq', 'comparison', 'review'. "
        "Choose based on SERP mix and content format hints in the cluster data.\n"
        "- estimated_monthly_traffic: Your rough estimate of monthly organic visits if ranking in top 5 "
        "for the primary keyword (integer, e.g. 60 for 1,200/mo volume × 5% CTR).\n"
        "- linked_cluster_id: Integer ID of the most relevant cluster from the data (or null).\n"
        "- linked_cluster_name: Name of that cluster (or empty string).\n"
        f"- linked_collection_handle: The most relevant {_brand} collection handle this article should link to "
        "(use handles from Top Collections list — e.g. 'disposable-vapes', 'vape-kits'). Empty string if none.\n"
        "- linked_collection_title: The human-readable title of that collection (or empty string).\n"
        "- source_type: What type of gap triggered this idea. One of: "
        "'cluster_gap', 'competitor_gap', 'collection_gap', 'query_gap'.\n"
        "- gap_reason: One concise sentence explaining the opportunity — include search volume and "
        "ranking position if available (e.g. 'Ranking pos 14 for \"best disposable vapes canada\" (1,200/mo) "
        "— one strong article could reach page 1').\n\n"
        "Return a JSON object with a single key 'ideas' containing an array of exactly 5 objects.\n\n"
        f"Gap analysis data:\n{context_block}"
    )

    json_schema = {
        "name": "article_ideas",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "suggested_title": {"type": "string", "minLength": 20, "maxLength": 70},
                            "brief": {"type": "string", "minLength": 80},
                            "primary_keyword": {"type": "string"},
                            "supporting_keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "search_intent": {
                                "type": "string",
                                "enum": ["informational", "commercial", "navigational"],
                            },
                            "content_format": {
                                "type": "string",
                                "enum": ["how_to", "buying_guide", "listicle", "faq", "comparison", "review"],
                            },
                            "estimated_monthly_traffic": {"type": "integer"},
                            "linked_cluster_id": {"type": ["integer", "null"]},
                            "linked_cluster_name": {"type": "string"},
                            "linked_collection_handle": {"type": "string"},
                            "linked_collection_title": {"type": "string"},
                            "source_type": {
                                "type": "string",
                                "enum": ["cluster_gap", "competitor_gap", "collection_gap", "query_gap"],
                            },
                            "gap_reason": {"type": "string"},
                        },
                        "required": [
                            "suggested_title", "brief", "primary_keyword",
                            "supporting_keywords", "search_intent",
                            "content_format", "estimated_monthly_traffic",
                            "linked_cluster_id", "linked_cluster_name",
                            "linked_collection_handle", "linked_collection_title",
                            "source_type", "gap_reason",
                        ],
                        "additionalProperties": False,
                    },
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
            "required": ["ideas"],
            "additionalProperties": False,
        },
    }

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    try:
        result = _call_ai(settings, provider, model, messages, timeout, json_schema=json_schema, stage="article_ideas")
    except AIProviderRequestError as exc:
        raise RuntimeError(str(exc)) from exc

    ideas = result.get("ideas") or []
    # Build a lookup so we can snapshot cluster metrics into each idea
    cluster_lookup = {c["id"]: c for c in gap_data["cluster_gaps"]}

    cleaned = []
    for idea in ideas:
        cid = idea.get("linked_cluster_id")
        if isinstance(cid, str):
            try:
                cid = int(cid)
            except (ValueError, TypeError):
                cid = None

        # Snapshot cluster-level metrics from gap_data (not from AI output)
        cluster_meta = cluster_lookup.get(cid, {}) if cid else {}
        total_volume = int(cluster_meta.get("total_volume") or 0)
        avg_difficulty = round(float(cluster_meta.get("avg_difficulty") or 0.0), 1)
        # Opportunity score: avg_opportunity boosted by 50% if cluster has ranking opportunity
        raw_opp = float(cluster_meta.get("avg_opportunity") or 0.0)
        opportunity_score = round(raw_opp * 1.5 if cluster_meta.get("has_ranking_opportunity") else raw_opp, 1)
        dominant_serp_features = str(cluster_meta.get("dominant_serp_features") or "")
        content_format_hints = str(cluster_meta.get("content_format_hints") or "")
        import json as _json
        linked_keywords_json = _json.dumps(cluster_meta.get("top_keywords") or [])

        cleaned.append(
            {
                "suggested_title": str(idea.get("suggested_title") or ""),
                "brief": str(idea.get("brief") or ""),
                "primary_keyword": str(idea.get("primary_keyword") or ""),
                "supporting_keywords": [str(k) for k in (idea.get("supporting_keywords") or [])],
                "search_intent": str(idea.get("search_intent") or "informational"),
                "content_format": str(idea.get("content_format") or ""),
                "estimated_monthly_traffic": int(idea.get("estimated_monthly_traffic") or 0),
                "linked_cluster_id": cid,
                "linked_cluster_name": str(idea.get("linked_cluster_name") or ""),
                "linked_collection_handle": str(idea.get("linked_collection_handle") or ""),
                "linked_collection_title": str(idea.get("linked_collection_title") or ""),
                "source_type": str(idea.get("source_type") or "cluster_gap"),
                "gap_reason": str(idea.get("gap_reason") or ""),
                # Snapshotted from cluster at generation time
                "total_volume": total_volume,
                "avg_difficulty": avg_difficulty,
                "opportunity_score": opportunity_score,
                "dominant_serp_features": dominant_serp_features,
                "content_format_hints": content_format_hints,
                "linked_keywords_json": linked_keywords_json,
            }
        )
    return cleaned
