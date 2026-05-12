"""Article draft generation and HTML link utilities."""
import datetime
import html as html_module
import json
import logging
import re
import sqlite3
from typing import Any, Callable

logger = logging.getLogger(__name__)

from .providers import AIProviderRequestError, _call_ai
from .qa import clamp_generated_seo_field
from .settings import ai_settings

_A_BODY_TAG_RE = re.compile(r"(?is)<a\s+([^>]+)>(.*?)</a>")


def sanitize_article_internal_links(
    body_html: str,
    *,
    path_to_canonical: dict[str, str],
    base_url: str,
) -> str:
    """Rewrite internal ``<a href>`` to canonical store URLs or unwrap unknown/external links.

    *path_to_canonical* maps normalized path keys (no trailing slash, except ``/``) to
    the preferred full ``https://custom-domain/...`` URL. Links whose path is not in
    the map are replaced with their inner HTML. External ``http(s)`` links are unwrapped
    so competitor URLs do not appear as hyperlinks.
    """
    if not body_html or "<a " not in body_html.lower():
        return body_html

    from urllib.parse import urlparse, urljoin

    base = (base_url or "").strip().rstrip("/")

    def _path_key(path: str) -> str:
        p = (path or "").strip()
        if not p or p == "/":
            return "/"
        return p.rstrip("/") or "/"

    def _repl(match: re.Match) -> str:
        attrs, inner = match.group(1), match.group(2)
        hm = re.search(r"""href\s*=\s*(["'])(.*?)\1""", attrs, re.I | re.DOTALL)
        if not hm:
            return match.group(0)
        href = (hm.group(2) or "").strip()
        hl = href.lower()
        if hl.startswith(("#", "mailto:", "tel:", "javascript:")):
            return match.group(0)

        pk: str | None = None
        if href.startswith("/"):
            pk = _path_key(urlparse(href).path or "/")
        elif href.startswith("http"):
            pk = _path_key(urlparse(href).path or "/")
        elif base:
            joined = urljoin(base + "/", href)
            pk = _path_key(urlparse(joined).path or "/")

        if pk and pk in path_to_canonical:
            canon = path_to_canonical[pk]
            quote = hm.group(1)
            if quote in canon:
                quote = "'" if quote == '"' else '"'
            new_attrs = attrs[: hm.start()] + f"href={quote}{canon}{quote}" + attrs[hm.end() :]
            return f"<a {new_attrs}>{inner}</a>"

        # Unknown or external link — unwrap to plain text
        if href.startswith("http"):
            return inner
        return match.group(0)

    return _A_BODY_TAG_RE.sub(_repl, body_html)


def generate_article_draft(
    conn: sqlite3.Connection,
    topic: str,
    keywords: list[str | dict] | None = None,
    author_name: str = "",
    *,
    linked_cluster_id: int | None = None,
    primary_target: dict | None = None,
    secondary_targets: list[dict] | None = None,
    idea_serp_context: dict[str, Any] | None = None,
    idea_linked_keywords: list[dict] | None = None,
    request_context: dict[str, Any] | None = None,
    regeneration_context: dict[str, Any] | None = None,
    draft_run_id: str = "",
    resume_run: dict | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Generate a brand-new blog article draft using AI.

    keywords can be plain strings or dicts with keys like
    {keyword, volume, difficulty, cpc, content_format_hint}.
    At most 5 keyword entries are included in the prompt.

    Returns a dict with keys: title, seo_title, seo_description, body.
    Raises RuntimeError on AI failure.
    """
    settings = ai_settings(conn)
    from .serp_draft_context import (
        MAX_PAA_QUESTIONS,
        build_paa_question_hierarchy,
        select_required_paa_questions_for_draft,
    )
    from .article_draft_compliance import (
        COMPLIANCE_BODY_LENGTH_RETRY_MARGIN,
        MIN_ARTICLE_BODY_HTML_CHARS,
        append_server_generated_faqpage_jsonld,
        build_compliance_retry_user_message,
        collect_hrefs,
        collect_tier_related_queries,
        extract_visible_faq_items,
        length_only_article_compliance_gaps,
        mixed_length_and_serp_compliance_gaps,
        strip_faqpage_jsonld_blocks,
        strip_html_for_compliance_search,
        validate_article_draft_compliance,
    )
    from shopifyseo.dashboard_store import update_article_draft_run

    provider = settings["generation_provider"]
    model = settings["generation_model"]
    timeout = settings["timeout"]

    from shopifyseo.market_context import (
        get_primary_country_code, country_display_name, spelling_variant,
        language_region_code, shipping_cue,
    )
    _market_code = get_primary_country_code(conn)
    _market_name = country_display_name(_market_code)
    _ship_phrase, _avail_phrase = shipping_cue(_market_code)
    _lang_code = language_region_code(_market_code)

    cluster_meta: dict[str, Any] = {}
    cluster_kws: list[str] = []
    cluster_kw_metrics: list[dict[str, Any]] = []
    target_items: list[dict[str, Any]] = []
    target_metrics_by_keyword: dict[str, dict[str, Any]] = {}
    seo_gap_items: list[dict[str, Any]] = []
    linked_keyword_rows: list[dict[str, Any]] = []
    for raw_linked in idea_linked_keywords or []:
        if isinstance(raw_linked, dict):
            kw = str(raw_linked.get("keyword") or raw_linked.get("query") or "").strip()
            if kw:
                linked_keyword_rows.append({**raw_linked, "keyword": kw})
        else:
            kw = str(raw_linked or "").strip()
            if kw:
                linked_keyword_rows.append({"keyword": kw})

    try:
        from shopifyseo.dashboard_google import get_service_setting as _get_ss

        target_raw = _get_ss(conn, "target_keywords", "{}")
        target_data = json.loads(target_raw) if target_raw else {}
        for item in target_data.get("items") or []:
            if not isinstance(item, dict):
                continue
            kw = str(item.get("keyword") or "").strip()
            if not kw:
                continue
            target_items.append(item)
            target_metrics_by_keyword[kw.lower()] = item
    except Exception:
        logger.debug("Failed to load target keyword metrics for article draft; proceeding without them")

    # Compute SEO keyword gaps from linked cluster (new article = empty content)
    _seo_gap_section = ""
    if linked_cluster_id is not None:
        try:
            from backend.app.services.keyword_clustering import compute_seo_gaps

            cluster_cols = {row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()}
            tier_select = (
                ", core_keywords_json, supporting_keywords_json, extended_keywords_json, cluster_role, cluster_intent"
                if "core_keywords_json" in cluster_cols
                else ", '[]' AS core_keywords_json, '[]' AS supporting_keywords_json, '[]' AS extended_keywords_json, '' AS cluster_role, '' AS cluster_intent"
            )
            cluster_row = conn.execute(
                """
                SELECT id, name, primary_keyword, content_brief, dominant_serp_features, content_format_hints
                       {tier_select}
                FROM clusters WHERE id = ?
                """.format(tier_select=tier_select),
                (linked_cluster_id,),
            ).fetchone()
            if cluster_row:
                from backend.app.services.keyword_clustering import parse_keyword_tier

                cluster_meta = {
                    "id": linked_cluster_id,
                    "name": cluster_row["name"] if "name" in cluster_row.keys() else cluster_row[1],
                    "primary_keyword": cluster_row["primary_keyword"] if "primary_keyword" in cluster_row.keys() else cluster_row[2],
                    "content_brief": cluster_row["content_brief"] if "content_brief" in cluster_row.keys() else cluster_row[3],
                    "dominant_serp_features": cluster_row["dominant_serp_features"] if "dominant_serp_features" in cluster_row.keys() else "",
                    "content_format_hints": cluster_row["content_format_hints"] if "content_format_hints" in cluster_row.keys() else "",
                    "core_keywords": parse_keyword_tier(cluster_row["core_keywords_json"] if "core_keywords_json" in cluster_row.keys() else "[]"),
                    "supporting_keywords": parse_keyword_tier(cluster_row["supporting_keywords_json"] if "supporting_keywords_json" in cluster_row.keys() else "[]"),
                    "extended_keywords": parse_keyword_tier(cluster_row["extended_keywords_json"] if "extended_keywords_json" in cluster_row.keys() else "[]"),
                    "cluster_role": cluster_row["cluster_role"] if "cluster_role" in cluster_row.keys() else "",
                    "cluster_intent": cluster_row["cluster_intent"] if "cluster_intent" in cluster_row.keys() else "",
                }

            kw_rows = conn.execute(
                "SELECT keyword FROM cluster_keywords WHERE cluster_id = ?",
                (linked_cluster_id,),
            ).fetchall()
            tiered_kws = []
            for field in ("primary_keyword", "core_keywords", "supporting_keywords"):
                value = cluster_meta.get(field)
                raw_values = [value] if isinstance(value, str) else (value or [])
                for raw_kw in raw_values:
                    kw = str(raw_kw or "").strip()
                    if kw and kw.lower() not in {x.lower() for x in tiered_kws}:
                        tiered_kws.append(kw)
            if tiered_kws:
                cluster_kws = tiered_kws[:24]
            else:
                cluster_kws = [str(r[0]).strip() for r in kw_rows if str(r[0] or "").strip()][:24]
            primary_kw = str(cluster_meta.get("primary_keyword") or "")

            # Join cluster_keywords with keyword_metrics so the writer sees volume / KD /
            # intent / ranking_status / position for each cluster keyword, not just strings.
            # ORDER: opportunity DESC (with NULLs last) keeps striking-distance / quick-win
            # entries near the top of the prompt block.
            try:
                cluster_kw_metric_rows = conn.execute(
                    """
                    SELECT ck.keyword,
                           COALESCE(km.volume, 0)                     AS volume,
                           COALESCE(km.difficulty, 0)                 AS difficulty,
                           COALESCE(km.cpc, 0.0)                      AS cpc,
                           COALESCE(km.intent, '')                    AS intent,
                           COALESCE(km.ranking_status, 'not_ranking') AS ranking_status,
                           km.gsc_position,
                           COALESCE(km.opportunity, 0.0)              AS opportunity,
                           COALESCE(km.content_format_hint, '')       AS content_format_hint
                    FROM cluster_keywords ck
                    LEFT JOIN keyword_metrics km ON LOWER(km.keyword) = LOWER(ck.keyword)
                    WHERE ck.cluster_id = ?
                    ORDER BY km.opportunity DESC NULLS LAST, ck.keyword ASC
                    LIMIT 30
                    """,
                    (linked_cluster_id,),
                ).fetchall()
                cluster_kw_metrics = [
                    {
                        "keyword": (row["keyword"] if hasattr(row, "keys") else row[0]),
                        "volume": int((row["volume"] if hasattr(row, "keys") else row[1]) or 0),
                        "difficulty": int((row["difficulty"] if hasattr(row, "keys") else row[2]) or 0),
                        "cpc": round(float((row["cpc"] if hasattr(row, "keys") else row[3]) or 0.0), 2),
                        "intent": (row["intent"] if hasattr(row, "keys") else row[4]) or "",
                        "ranking_status": (row["ranking_status"] if hasattr(row, "keys") else row[5]) or "not_ranking",
                        "gsc_position": (row["gsc_position"] if hasattr(row, "keys") else row[6]),
                        "opportunity": round(
                            float((row["opportunity"] if hasattr(row, "keys") else row[7]) or 0.0), 1
                        ),
                        "content_format_hint": (
                            row["content_format_hint"] if hasattr(row, "keys") else row[8]
                        ) or "",
                    }
                    for row in cluster_kw_metric_rows
                ]
            except Exception:
                logger.debug("Failed to join cluster keywords with metrics; proceeding without table", exc_info=True)
                cluster_kw_metrics = []

            if cluster_kws:
                gaps = compute_seo_gaps(
                    cluster_kws, {}, target_metrics_by_keyword, "blog_article", primary_kw,
                )
                if gaps:
                    seo_gap_items = list(gaps.get("must_consider") or [])
                    mc_lines = []
                    for mc in seo_gap_items:
                        mc_lines.append(
                            f"  - \"{mc['keyword']}\" (opportunity: {mc['opportunity']}, ranking: {mc['ranking_status']})"
                        )
                    _seo_gap_section = (
                        "\n\nSEO keyword gaps — these cluster keywords are NOT yet covered and should be woven "
                        "into the article (sorted by priority, highest first):\n"
                        + "\n".join(mc_lines)
                        + "\n- seo_title: incorporate the 1–2 highest-opportunity keywords naturally\n"
                        "- seo_description: work in 2–3 of these phrases\n"
                        "- body: weave in as many as fit naturally; prioritise those listed first\n"
                        "Readability and conversion always take priority over keyword density. "
                        "Do not force phrases where they break the reading flow.\n"
                    )
        except Exception:
            logger.debug("Failed to compute SEO gaps for article draft; proceeding without them")

    # Cluster strategic brief — surface the clustering-time narrative so the writer treats this
    # article as part of a larger topical authority play, not a one-off post.
    _cluster_brief_section = ""
    if cluster_meta:
        _cb_lines: list[str] = []
        _cb_name = (cluster_meta.get("name") or "").strip()
        _cb_brief = (cluster_meta.get("content_brief") or "").strip()
        _cb_intent = (cluster_meta.get("cluster_intent") or "").strip()
        _cb_role = (cluster_meta.get("cluster_role") or "").strip()
        if _cb_name or _cb_brief or _cb_intent or _cb_role:
            _cb_lines.append("\n\nCluster strategy — this article supports a topical authority cluster:")
            if _cb_name:
                _cb_lines.append(f"- Cluster: {_cb_name}")
            if _cb_role:
                _cb_lines.append(f"- Article role in cluster: {_cb_role}")
            if _cb_intent:
                _cb_lines.append(f"- Cluster intent: {_cb_intent}")
            if _cb_brief:
                _cb_brief_trim = _cb_brief if len(_cb_brief) <= 1200 else _cb_brief[:1197] + "…"
                _cb_lines.append(f"- Strategic brief: {_cb_brief_trim}")
            _cb_lines.append(
                "Align the article structure with this brief; reinforce the primary authority page and "
                "naturally link to related cluster pages where the reader benefits."
            )
            _cluster_brief_section = "\n".join(_cb_lines)

    # Cluster keyword table (D) — surface per-keyword metrics so the writer can prioritise
    # striking-distance / quick-win terms over net-new ones. Ordered by opportunity desc.
    _cluster_kw_table_section = ""
    if cluster_kw_metrics:
        _kt_lines: list[str] = [
            "\n\nCluster keyword strategy table — prioritise terms with a ranking_status of "
            "'quick_win' or 'striking_distance' (already close to page 1) before net-new terms. "
            "Treat the table as a coverage checklist for headings and body text:",
        ]
        for row in cluster_kw_metrics[:30]:
            kw = str(row.get("keyword") or "").strip()
            if not kw:
                continue
            vol = int(row.get("volume") or 0)
            kd = int(row.get("difficulty") or 0)
            intent = str(row.get("intent") or "").strip()
            rs = str(row.get("ranking_status") or "").strip() or "not_ranking"
            pos = row.get("gsc_position")
            opp = row.get("opportunity")
            cph = str(row.get("content_format_hint") or "").strip()
            bits: list[str] = []
            if vol:
                bits.append(f"vol:{vol}")
            if kd:
                bits.append(f"KD:{kd}")
            if intent:
                bits.append(f"intent:{intent}")
            if rs:
                bits.append(f"status:{rs}")
            if pos is not None:
                try:
                    pos_f = float(pos)
                    if pos_f > 0 and pos_f < 900:
                        bits.append(f"pos:{pos_f:.1f}")
                except (TypeError, ValueError):
                    pass
            if opp is not None:
                try:
                    if float(opp) > 0:
                        bits.append(f"opp:{float(opp):.1f}")
                except (TypeError, ValueError):
                    pass
            if cph:
                bits.append(f"format:{cph}")
            tail = " — " + " ".join(bits) if bits else ""
            prefix = ""
            if rs in ("quick_win", "striking_distance"):
                prefix = "  ★ "  # visual cue for opportunity-rich terms
            else:
                prefix = "  - "
            _kt_lines.append(f"{prefix}{kw}{tail}")
        _cluster_kw_table_section = "\n".join(_kt_lines)

    # Regeneration grounding — when rewriting an existing article, surface what's already
    # ranking + the current body skeleton so the rewrite preserves earning traffic patterns
    # instead of starting from a blank page.
    _regeneration_section = ""
    if regeneration_context:
        from .article_draft_compliance import strip_html_for_compliance_search

        _rg_lines: list[str] = []
        _rg_title = str(regeneration_context.get("existing_title") or "").strip()
        _rg_position = regeneration_context.get("existing_gsc_position")
        _rg_queries = regeneration_context.get("existing_gsc_queries") or []
        _rg_body = str(regeneration_context.get("existing_body_html") or "")

        if _rg_title or _rg_queries or _rg_body:
            _rg_lines.append("\n\nRegenerating an existing article — preserve what works, fix what doesn't:")
        if _rg_title:
            _rg_lines.append(f"- Current title: {_rg_title}")
        if _rg_position is not None:
            try:
                _rg_lines.append(f"- Current average position (GSC): {float(_rg_position):.1f}")
            except (TypeError, ValueError):
                pass

        if isinstance(_rg_queries, list) and _rg_queries:
            ranking_lines: list[str] = []
            ranking_lines.append(
                "- Currently ranks for these GSC queries (sorted by clicks, then impressions). "
                "These already earn traffic — preserve their semantic coverage and the on-page wording "
                "that resonates with each, even if you reorganise the structure:"
            )
            sorted_q = sorted(
                [q for q in _rg_queries if isinstance(q, dict)],
                key=lambda q: (
                    -int(q.get("clicks") or 0),
                    -int(q.get("impressions") or 0),
                ),
            )[:25]
            for q in sorted_q:
                kw = str(q.get("query") or "").strip()
                if not kw:
                    continue
                clicks = int(q.get("clicks") or 0)
                impressions = int(q.get("impressions") or 0)
                pos_raw = q.get("position")
                pos_part = ""
                try:
                    if pos_raw is not None:
                        pos_part = f" pos={float(pos_raw):.1f}"
                except (TypeError, ValueError):
                    pos_part = ""
                ranking_lines.append(
                    f"  - {kw!r} (clicks={clicks}, impressions={impressions}{pos_part})"
                )
            _rg_lines.append("\n".join(ranking_lines))

        if _rg_body:
            # Outline of the current article: keep h2/h3 headings so the model can reuse strong structure.
            headings: list[str] = []
            for m in re.finditer(r"<h([23])[^>]*>(.*?)</h\1>", _rg_body, re.IGNORECASE | re.DOTALL):
                level = m.group(1)
                txt = re.sub(r"<[^>]+>", " ", m.group(2) or "").strip()
                if txt:
                    headings.append(f"  - h{level}: {txt}")
                if len(headings) >= 30:
                    break
            if headings:
                _rg_lines.append(
                    "- Current article outline (h2/h3 headings already present — keep the ones with proven coverage, "
                    "merge or replace weak ones, add new headings to close keyword gaps):\n" + "\n".join(headings)
                )
            visible_text = strip_html_for_compliance_search(_rg_body)
            if visible_text:
                visible_trim = visible_text if len(visible_text) <= 3500 else visible_text[:3497] + "…"
                _rg_lines.append(
                    "- Current article body (visible text excerpt — paraphrase or extend; do not copy verbatim "
                    "back into the output):\n  " + visible_trim.replace("\n", " ")
                )
            _rg_lines.append(
                "Rewrite guidance: keep sections that already earn clicks for the GSC queries above, refresh stale or "
                "thin sections, and add new sections for the keyword gaps listed below. Aim for a stronger version of "
                "this article — not a parallel one."
            )

        if _rg_lines:
            _regeneration_section = "\n".join(_rg_lines)

    # Idea-level structural directive — pulled from article_ideas.content_format and source_type.
    # The clustering pipeline classifies each idea (buying_guide / comparison / how-to / review /
    # listicle / faq) and tags the gap origin (cluster_gap / competitor_gap / collection_gap /
    # query_gap). Translate that into a short prose directive so the writer adopts the right
    # structure and angle from the start instead of inferring it from fuzzy SERP hints alone.
    _format_directives: dict[str, str] = {
        "buying_guide": (
            "Article format: buying guide. Open with a 1–2 sentence direct answer, then a quick "
            "comparison table or shortlist of top picks, then a per-pick breakdown with pros / cons / "
            "who-it's-for. Close with selection criteria and a CTA back to the primary collection."
        ),
        "comparison": (
            "Article format: comparison. Use a clear A-vs-B (or A/B/C) structure with parallel sections "
            "for each contender, an at-a-glance comparison table near the top, and a verdict section "
            "addressing different reader profiles (budget, beginner, advanced)."
        ),
        "how_to": (
            "Article format: how-to. Structure as a numbered or step-by-step procedure with each step "
            "as an H3 under a few thematic H2s. Include a 'what you'll need' list near the top and "
            "troubleshooting / common mistakes near the end."
        ),
        "how-to": (
            "Article format: how-to. Structure as a numbered or step-by-step procedure with each step "
            "as an H3 under a few thematic H2s. Include a 'what you'll need' list near the top and "
            "troubleshooting / common mistakes near the end."
        ),
        "listicle": (
            "Article format: listicle. Each item is a clearly numbered H2 (e.g. 'Best for beginners: …'). "
            "Keep item descriptions parallel in length and depth. End with a short selection-criteria section."
        ),
        "review": (
            "Article format: review. Use H2s for verdict, build quality, performance/flavour, value, and "
            "pros/cons. Include a final 'Who should buy this' section and link to the product page."
        ),
        "faq": (
            "Article format: FAQ. Each question is an `<h3>` heading followed by a 2–4 sentence answer. "
            "Open with a 1–2 sentence intro (no H2 for the intro). Add FAQPage JSON-LD at the end."
        ),
        "guide": (
            "Article format: pillar guide. Use a deep, well-scaffolded outline with multiple H2s covering "
            "background, criteria, options, and decision guidance. Heavier interlinking to supporting pages."
        ),
    }
    _source_angle_directives: dict[str, str] = {
        "competitor_gap": (
            "Angle: competitor-gap. Competitors rank but the store doesn't — write the most useful, "
            "trustworthy answer on this topic. Lead with concrete differentiation (selection, compliance, "
            "shipping, expertise) without naming competitors; let the depth speak."
        ),
        "collection_gap": (
            "Angle: collection-gap. A store collection earns impressions but lacks supporting content. "
            "Frame the article as the primer that points readers to that collection with confidence."
        ),
        "query_gap": (
            "Angle: query-gap. Real users are landing on the store for this query but on the wrong page. "
            "Make the article the unambiguously correct destination — direct answer high up, clear next steps."
        ),
        "cluster_gap": (
            "Angle: cluster-gap. This article fills a planned slot in the topic cluster — write it to "
            "support the primary authority page, not to compete with it."
        ),
    }
    _structural_section = ""
    _content_format_raw = ""
    _source_type_raw = ""
    if idea_serp_context:
        _content_format_raw = str(idea_serp_context.get("content_format") or "").strip()
        _source_type_raw = str(idea_serp_context.get("source_type") or "").strip()
    _fmt_key = _content_format_raw.lower().replace(" ", "_") if _content_format_raw else ""
    _src_key = _source_type_raw.lower() if _source_type_raw else ""
    _fmt_directive = _format_directives.get(_fmt_key) if _fmt_key else None
    _src_directive = _source_angle_directives.get(_src_key) if _src_key else None
    if _fmt_directive or _src_directive or _content_format_raw or _source_type_raw:
        _struct_lines: list[str] = ["\n\nStructural directives (idea-level — keep front of mind):"]
        if _fmt_directive:
            _struct_lines.append(f"- {_fmt_directive}")
        elif _content_format_raw:
            # Format value is set but not in our dictionary — surface it raw so the writer at least sees it.
            _struct_lines.append(
                f"- Article format: {_content_format_raw}. Match this structure throughout."
            )
        if _src_directive:
            _struct_lines.append(f"- {_src_directive}")
        elif _source_type_raw:
            _struct_lines.append(f"- Source angle: {_source_type_raw}.")
        _structural_section = "\n".join(_struct_lines)

    keyword_section = ""
    if keywords:
        trimmed = keywords[:5]
        lines = []
        for kw in trimmed:
            if isinstance(kw, dict):
                parts = [kw.get("keyword", "")]
                if kw.get("volume"):
                    parts.append(f"vol:{kw['volume']}")
                if kw.get("difficulty"):
                    parts.append(f"KD:{kw['difficulty']}")
                if kw.get("content_format_hint"):
                    parts.append(f"format:{kw['content_format_hint']}")
                lines.append(" | ".join(parts))
            else:
                lines.append(str(kw))
        keyword_section = (
            "\n\nTarget keywords to naturally incorporate (do not force or stuff — readability first):\n"
            + "\n".join(f"- {line}" for line in lines)
        )

    serp_appendix = ""
    retrieval_boost_terms: list[str] = []
    paa_shown_count = 0
    if idea_serp_context is not None:
        from .serp_draft_context import build_serp_appendix_and_retrieval_boost

        serp_appendix, retrieval_boost_terms, paa_shown_count = build_serp_appendix_and_retrieval_boost(
            topic=topic,
            keywords=keywords,
            idea_serp_context=idea_serp_context,
        )

    # Detect if this is an FAQ-style article so we can enforce the correct structure
    _topic_lower = topic.lower()
    _is_faq = any(w in _topic_lower for w in ("faq", "questions", "q&a", "q & a"))

    _faq_instruction = (
        "This is an FAQ article. Structure the body as explicit questions and answers: "
        f"each question must be an <h3> heading (e.g. <h3>Is ZYN legal in {_market_name}?</h3>), "
        "followed by a 2–4 sentence answer paragraph. Include at least 6 FAQ pairs. "
        "Open with a short 1–2 sentence intro before the first question — do not use an H2 for the intro. "
        "Add FAQ structured data as a JSON-LD <script> block at the end of the body (inside the HTML), "
        "using the @type FAQPage schema with a Question/acceptedAnswer pair for each H3 question. "
        "Do not use a generic H2 like 'Frequently Asked Questions' — let the H3 questions carry the structure.\n"
        if _is_faq
        else ""
    )

    _paa_rows = (idea_serp_context or {}).get("audience_questions") or []
    _paa_hierarchy = build_paa_question_hierarchy(idea_serp_context or {})
    required_questions = select_required_paa_questions_for_draft(idea_serp_context or {})
    _has_serp_paa = bool(
        (isinstance(_paa_rows, list) and len(_paa_rows) > 0)
        or _paa_hierarchy
        or required_questions
    )
    _n_visible_paa_for_faq = (
        len(required_questions)
        if required_questions
        else paa_shown_count
        if paa_shown_count > 0
        else (min(len(_paa_rows), MAX_PAA_QUESTIONS) if _has_serp_paa else 0)
    )
    _faq_pair_target = min(6, _n_visible_paa_for_faq) if _n_visible_paa_for_faq > 0 else 0
    _paa_faq_instruction = (
        "People Also Ask (PAA) signals are included below for this topic. Use parent PAA as section intent and "
        "expanded child PAA as depth inside the matching section; address the highest-priority questions first. "
        "At the end of the body, add FAQPage JSON-LD (Question + acceptedAnswer) aligned to the reader questions "
        f"you answer in the article — include **{_faq_pair_target}** Question/acceptedAnswer pairs (match the "
        "wording of your `<h3>` FAQ-style questions in the body; do not invent questions you did not cover).\n"
        if _has_serp_paa and not _is_faq and _faq_pair_target > 0
        else ""
    )

    # ── RAG context + approved internal URLs (custom domain base, DB-backed paths only)
    from urllib.parse import urlparse

    from .config import get_store_identity
    from .. import dashboard_queries as _dq

    _rag_reference_lines: list[str] = []
    rag_results: list[dict] = []
    try:
        from ..article_draft_retrieval import run_article_draft_rag

        api_key = settings.get("gemini_api_key") or ""
        if api_key:
            rag_results = run_article_draft_rag(
                conn,
                api_key,
                topic=topic,
                keywords=keywords,
                linked_cluster_id=linked_cluster_id,
                top_k=5,
                retrieval_extra_terms=retrieval_boost_terms or None,
            ) or []
        if rag_results:
            for r in rag_results:
                _rag_reference_lines.append(
                    f"- [{r['object_type']}] {r['object_handle']} — {(r.get('source_text_preview') or '')[:100]}"
                )
    except Exception:
        logger.debug("RAG retrieval for article draft internal links failed", exc_info=True)

    _rag_reference_block = ""
    if _rag_reference_lines:
        _rag_reference_block = (
            "\n\nReference content from your store (use for context only — internal links must use approved URLs below):\n"
            + "\n".join(_rag_reference_lines)
            + "\n"
        )

    _store_name, _store_domain = get_store_identity(conn)
    _brand = _store_name or "the store"
    _base_url = (_dq._base_store_url(conn) or "").strip().rstrip("/")

    link_targets, _, _ = _dq.build_store_internal_link_allowlist(conn, _base_url, rag_results=rag_results)
    try:
        conn.close()
    except Exception:
        logger.debug("Failed to close article draft setup DB connection", exc_info=True)

    # Widen allowlist with primary/secondary interlink targets so sanitizer keeps them.
    def _normalize_target_entry(t: dict) -> dict | None:
        tt = (t.get("type") or "").strip()
        th = (t.get("handle") or "").strip()
        if not tt or not th:
            return None
        title = (t.get("title") or th).strip()
        url = (t.get("url") or "").strip()
        if not url:
            url = _dq.object_url_with_base(_base_url, tt, th)
        return {"type": tt, "handle": th, "title": title, "url": url}

    _existing_keys = {(t.get("type"), t.get("handle")) for t in link_targets}
    primary_normalized = _normalize_target_entry(primary_target) if primary_target else None
    if primary_normalized and (primary_normalized["type"], primary_normalized["handle"]) not in _existing_keys:
        link_targets.insert(0, primary_normalized)
        _existing_keys.add((primary_normalized["type"], primary_normalized["handle"]))

    secondary_normalized: list[dict] = []
    for s in secondary_targets or []:
        n = _normalize_target_entry(s)
        if not n:
            continue
        if (n["type"], n["handle"]) in _existing_keys:
            # Preserve anchor_keyword for the prompt even if already in allowlist.
            n_with_anchor = dict(n)
            n_with_anchor["anchor_keyword"] = (s.get("anchor_keyword") or "").strip()
            secondary_normalized.append(n_with_anchor)
            continue
        link_targets.append(n)
        _existing_keys.add((n["type"], n["handle"]))
        n_with_anchor = dict(n)
        n_with_anchor["anchor_keyword"] = (s.get("anchor_keyword") or "").strip()
        secondary_normalized.append(n_with_anchor)

    path_to_canonical: dict[str, str] = {}
    for t in link_targets:
        u = (t.get("url") or "").strip()
        if not u.startswith("http"):
            continue
        pk = (urlparse(u).path or "").rstrip("/") or "/"
        path_to_canonical.setdefault(pk, u)

    _domain = ""
    if _base_url:
        _domain = urlparse(_base_url).netloc or ""
    if not _domain and (_store_domain or "").strip():
        _domain = (_store_domain or "").strip()

    if _base_url and _domain:
        _link_scope = (
            f"NEVER link to competitor websites. For this store, internal links must use the exact `url` strings "
            f"from approved_internal_link_targets below (hostname {_domain}, base {_base_url}). "
            "NEVER use placeholder domains such as example.com or your-store.com."
        )
    elif _base_url:
        _link_scope = (
            f"NEVER link to competitor websites. Internal storefront links must match approved_internal_link_targets "
            f"exactly (base {_base_url}). Never invent another hostname."
        )
    else:
        _link_scope = (
            "NEVER link to competitor websites. Internal storefront links must match approved_internal_link_targets "
            "exactly (root-relative urls in the list). Never invent domains or paths not listed."
        )

    # Primary + secondary interlink directive (authority page + related pages).
    _authority_link_block = ""
    if primary_normalized and primary_normalized.get("url"):
        _primary_anchor_hint = (primary_normalized.get("title") or primary_normalized["handle"]).strip()
        secondary_json_rows = [
            {
                "url": n["url"],
                "type": n["type"],
                "title": n["title"],
                "anchor_keyword": n.get("anchor_keyword") or "",
            }
            for n in secondary_normalized
            if n.get("url")
        ]
        _secondary_block = ""
        if secondary_json_rows:
            _secondary_block = (
                "SECONDARY RELATED LINKS — include EACH of the following at least once in the body "
                "with natural, keyword-rich SEO anchor text. When an 'anchor_keyword' is provided, "
                "use it (or a close variation that fits the sentence) as the visible link text. "
                "These reinforce topical relevance for the primary authority page.\n"
                f"{json.dumps(secondary_json_rows, ensure_ascii=True)}\n"
            )
        _authority_link_block = (
            "\n\nINTERLINK STRATEGY — topical authority building:\n"
            f"PRIMARY AUTHORITY LINK (REQUIRED — the article MUST include exactly one prominent link to "
            f"this URL within the opening section or first H2, using natural anchor text such as "
            f"'{_primary_anchor_hint}' or a close variation): {primary_normalized['url']}\n"
            f"{_secondary_block}"
            "Use the canonical `url` strings verbatim — do not edit paths. "
            "Spread interlinks naturally across the article; never cluster them in a single paragraph.\n"
        )

    if link_targets:
        _allowlist_json = json.dumps(link_targets, ensure_ascii=True)
        if secondary_normalized:
            _internal_link_instruction = (
                "\n\napproved_internal_link_targets (JSON array). "
                "You MUST already satisfy the primary + EACH secondary URL from INTERLINK STRATEGY above. "
                "You MAY add up to **two** more internal links from this list when they clearly help the reader "
                '(prefer handles that also appear in "Reference content from your store"). '
                "Target **at most eight** total storefront `<a href>` links including primary and all secondaries; "
                "do not pad links only to increase count. "
                "Every storefront <a href> MUST use the `url` value from one of these objects "
                "character-for-character (copy the full string — no edits, no other hosts, no invented paths). "
                "Each <a> MUST also include a descriptive title attribute for accessibility and SEO: "
                f"for type collection use 'Shop {{title}} — {_brand}', for product use '{{title}} — {_brand}', "
                f"for page use '{{title}} — {_brand}', for blog_article use 'Read {{title}} — {_brand}'. "
                "Use natural anchor text describing the destination — never use the raw handle as link text.\n"
            )
        else:
            _internal_link_instruction = (
                "\n\napproved_internal_link_targets (JSON array). "
                "MUST include 2–4 contextual internal links in the body where they help the reader. "
                "When this list is long, prefer destinations that also appear in the \"Reference content from your store\" "
                "section above (same product, collection, or post) when they fit the reader's context — avoid unrelated "
                "catalog items that only appear deeper in the JSON list. "
                "Target at most eight total storefront `<a href>` links unless the article truly needs more for clarity. "
                "Every storefront <a href> MUST use the `url` value from one of these objects "
                "character-for-character (copy the full string — no edits, no other hosts, no invented paths). "
                "Each <a> MUST also include a descriptive title attribute for accessibility and SEO: "
                f"for type collection use 'Shop {{title}} — {_brand}', for product use '{{title}} — {_brand}', "
                f"for page use '{{title}} — {_brand}', for blog_article use 'Read {{title}} — {_brand}'. "
                "Use natural anchor text describing the destination — never use the raw handle as link text.\n"
            )
        _collection_link_block = _internal_link_instruction + f"{_allowlist_json}\n"
    else:
        _collection_link_block = (
            "\n\napproved_internal_link_targets is empty (catalog not synced or no handles). "
            "Do NOT add <a href> links to storefront collections, products, pages, or blog articles — "
            "you would be inventing URLs. Mention categories in plain text only.\n"
        )

    _publisher_ld = (
        f"\"publisher\":{{\"@type\":\"Organization\",\"name\":\"{_brand}\",\"url\":\"{_base_url}\"}}"
        if _base_url
        else f"\"publisher\":{{\"@type\":\"Organization\",\"name\":\"{_brand}\"}}"
    )

    def _first_plain_keyword() -> str:
        if not keywords:
            return ""
        raw = keywords[0]
        if isinstance(raw, dict):
            return str(raw.get("keyword") or "").strip()
        return str(raw).strip()

    def _has_tier1_related_searches(ctx: dict[str, Any] | None) -> bool:
        if not ctx:
            return False
        rel = ctx.get("related_searches") or []
        if not isinstance(rel, list):
            return False
        for x in rel:
            if not isinstance(x, dict):
                continue
            if not str(x.get("query") or "").strip():
                continue
            try:
                pos = int(x.get("position", 99))
            except (TypeError, ValueError):
                continue
            if pos <= 3:
                return True
        return False

    # Build the list of required body keywords once — both the prompt checklist and the
    # post-draft compliance validator consume this so they stay in sync.
    _required_body_keywords: list[str] = []
    _serp_pk_str = str((idea_serp_context or {}).get("primary_keyword") or "").strip()
    if _serp_pk_str:
        _required_body_keywords.append(_serp_pk_str)
    _first_manual = _first_plain_keyword()
    if _first_manual and _first_manual.lower() not in {x.lower() for x in _required_body_keywords}:
        _required_body_keywords.append(_first_manual)
    _pk_checklist_label = (
        ", ".join(repr(k) for k in _required_body_keywords)
        if _required_body_keywords
        else ""
    )
    _pk_checklist = _required_body_keywords[0] if _required_body_keywords else ""

    _pre_output_lines = [
        "\n\n=== Pre-output compliance (verify before you return JSON) ===\n",
        f"- Body HTML length is at least {MIN_ARTICLE_BODY_HTML_CHARS:,} characters (excluding this checklist). "
        "The server counts the full JSON `body` string with Python `len(body)` after normalizing internal store links "
        "(same rule as automated checks).\n",
    ]
    if _is_faq or _has_serp_paa:
        _pre_output_lines.append(
            "- FAQPage: include a `<script type=\"application/ld+json\">` block with @type FAQPage; "
            "each Question `name` must match visible on-page FAQ text (same wording in an `<h3>` or paragraph — "
            "validators reject schema-only questions).\n"
        )
    if secondary_normalized:
        _pre_output_lines.append(
            "- Every secondary URL from INTERLINK STRATEGY must appear verbatim as an `<a href>` in the body.\n"
        )
    if _required_body_keywords:
        if len(_required_body_keywords) == 1:
            _pre_output_lines.append(
                f"- Primary keyword for this draft: include {_pk_checklist_label} naturally at least once in body text.\n"
            )
        else:
            _pre_output_lines.append(
                f"- Required keywords for this draft: include EACH of these naturally at least once in body text: "
                f"{_pk_checklist_label}.\n"
            )
    if _has_tier1_related_searches(idea_serp_context):
        _pre_output_lines.append(
            "- SERP tier 1–3 related searches: each position 1–3 query from the SERP appendix must appear in an "
            "on-page <h2>, <h3>, <h4> heading or in normal paragraph text (light paraphrase allowed for grammar) — "
            "automated checks validate this.\n"
        )
    _pre_output_checklist = "".join(_pre_output_lines)

    _serp_system_extra = ""
    if idea_serp_context is not None:
        _serp_system_extra = (
            " When a SERP research appendix is present, prioritize information gain: synthesize and add net-new "
            "value (buyer criteria, checklists, comparison tables, compliance where relevant, store-specific guidance) "
            "rather than repeating or lightly rephrasing third-party overview angles. "
            "Treat top-position related refinements as strong gap-closure targets (dedicated H2/H3 when feasible) "
            "with natural learning-path continuity—avoid mechanical bridge formulas. "
            "Do not copy AI-overview-style cues verbatim; use them only to find a distinct store angle. "
            "Do not add competitor hyperlinks or reproduce competitor URLs."
        )

    _body_aim_chars = MIN_ARTICLE_BODY_HTML_CHARS + COMPLIANCE_BODY_LENGTH_RETRY_MARGIN

    system_msg = (
        f"You are an expert SEO content writer for {_brand}. "
        "Write high-quality, editorial blog content that ranks well on Google. "
        f"A machine validator rejects drafts unless the JSON `body` string is at least "
        f"{MIN_ARTICLE_BODY_HTML_CHARS:,} characters (Python `len(body)` including every HTML tag and space); "
        f"aim for {_body_aim_chars:,}+ so edits still pass. "
        f"{spelling_variant(_market_code)} "
        "Do not fabricate statistics, specific study results, or invented data — if you need to reference evidence, "
        "use well-known industry patterns rather than invented figures. "
        "Write at a Grade 8–10 reading level. Be helpful, specific, and commercially relevant. "
        "When choosing internal links, prefer store destinations that clearly match the article topic and any "
        "reference list provided in the user message over unrelated catalog URLs. "
        f"{_link_scope}"
        f"{_serp_system_extra}"
    )

    system_outline = (
        f"You are an expert SEO content strategist for {_brand}. "
        "Phase 1 returns JSON only: article title, meta fields, and a detailed section outline (headings + beats). "
        "Later phases write full HTML in batches from your outline — beats must be actionable for a writer. "
        f"{spelling_variant(_market_code)} "
        "Do not fabricate statistics, specific study results, or invented data. "
        f"{_link_scope}"
        f"{_serp_system_extra}"
    )

    system_section = (
        f"You are an expert SEO content writer for {_brand}. "
        "The article is produced in phased HTML batches. Each response must be valid JSON with a `html_blocks` array "
        "of HTML fragment strings that the server concatenates in order with prior batches. "
        "Do not wrap fragments in `<html>`, `<head>`, or `<body>`. Do not output an `<h1>` (the article title is separate). "
        "Do not repeat or rewrite sections that earlier batches already shipped. "
        f"The assembled article must eventually clear ~{_body_aim_chars:,}+ characters of HTML before automated checks; "
        "write each assigned fragment generously with multiple `<p>` paragraphs and optional lists or small tables. "
        f"{spelling_variant(_market_code)} "
        "Do not fabricate statistics, specific study results, or invented data. "
        "Write at a Grade 8–10 reading level. "
        f"{_link_scope}"
        f"{_serp_system_extra}"
    )

    _serp_user_block = ""
    if serp_appendix.strip():
        _serp_user_block = (
            "\n\nSERP-informed research for this article (structured excerpt; third-party snippets are not "
            "authoritative facts—paraphrase and add your own analysis):\n\n"
            + serp_appendix.strip()
            + "\n"
        )

    seo_brief_block = ""
    user_msg = (
        f"Write a complete SEO-optimised blog article for {_brand} on the following topic:\n\n"
        f"{topic.strip()}{_cluster_brief_section}{_cluster_kw_table_section}{_structural_section}{_regeneration_section}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
        f"{seo_brief_block}"
        f"{_serp_user_block}\n\n"
        f"{_faq_instruction}{_paa_faq_instruction}"
        f"{_pre_output_checklist}"
        "\n**Length plan (mandatory):** Plan for a long first draft. Use **at least 9–12 `<h2>` sections**; "
        "each section should include **at least two `<p>` paragraphs** of substantive prose (not one-liners) "
        "plus optional `<ul>`/`<ol>` lists or small tables where they help. "
        f"Target **`body` ≥ {_body_aim_chars:,} characters** so the final HTML clears the "
        f"{MIN_ARTICLE_BODY_HTML_CHARS:,}-character floor after headings, lists, and JSON-LD blocks. "
        "If you are unsure, add another full H2 section with buyer-focused detail rather than tightening copy.\n\n"
        "Return a JSON object with exactly these four fields:\n"
        f"- title: The H1 article headline (20–70 characters). Keyword-led, specific. {spelling_variant(_market_code)} "
        "No ALL CAPS. No filler suffixes like 'A Simple FAQ', 'A Complete Guide', '(2026 Update)', or '(Full Guide)'. "
        "Do not repeat the primary keyword verbatim twice in the title. "
        "NEVER start with vague filler like 'Understanding the…', 'Exploring the…', 'A Comprehensive Look at…', "
        "'Discovering the…', 'Everything You Need to Know About…', or 'The Ultimate Guide to…'. "
        "Start directly with the product, brand, or topic keyword.\n"
        "- seo_title: Meta title tag (45–65 characters). Distinct from the H1 — lead with the primary keyword, "
        f"reflect the search intent, end with ' | {_brand}' when it fits within 65 characters.\n"
        "- seo_description: Meta description (135–155 characters). Open with a concrete fact, benefit, or direct answer — "
        "do NOT start with 'Discover', 'Explore', 'Learn', 'Find out', or any other generic CTA verb. "
        f"Include a {_market_name}-relevant signal (e.g. '{_avail_phrase}', '{_ship_phrase}', or a region name "
        "if the topic is geographically specific). End with a specific click incentive tied to the article content — "
        "NEVER use generic trailing CTAs like 'today', 'now', 'Upgrade your experience today', 'Shop now', or 'Order today'. "
        "Instead end with something concrete like 'See our top 5 picks' or 'Compare specs and prices'. "
        "Count every character including spaces.\n"
        f"- body: Full article HTML. CRITICAL: target **2,400+ words** of reader-visible text across the article "
        f"(excluding HTML tags) **and** at least {_body_aim_chars:,} characters in the raw `body` string "
        f"(including tags) so automated checks on the {MIN_ARTICLE_BODY_HTML_CHARS:,}-character minimum pass reliably. "
        "Do not stop at a 'complete' short article — keep adding sections until the length plan is clearly satisfied. "
        "Structure with H2 section headings and H3 sub-headings — every major section should have at least one H3 "
        "sub-heading to create a clear hierarchy. "
        "Open the first paragraph with the direct answer to the main question — no throat-clearing or generic intros. "
        f"{_authority_link_block}"
        f"{_collection_link_block}"
        "At the very end of the body, add a complete Article JSON-LD structured data block using this template "
        "(replace the ALL-CAPS placeholders with actual values): "
        "<script type=\"application/ld+json\">{\"@context\":\"https://schema.org\",\"@type\":\"Article\","
        "\"headline\":\"TITLE\","
        "\"description\":\"SEO_DESCRIPTION\","
        f"\"author\":{{\"@type\":\"Organization\",\"name\":\"{_brand}\"}},"
        f"{_publisher_ld},"
        f"\"inLanguage\":\"{_lang_code}\","
        "\"datePublished\":\"DATE_ISO8601\"}"
        f"</script> — TITLE = the article headline, SEO_DESCRIPTION = the meta description, DATE_ISO8601 = today's date which is {datetime.date.today().isoformat()}."
    )

    _article_ld_instruction = (
        "At the very end of the **last** HTML fragment in the final batch, add a complete Article JSON-LD structured data block using this template "
        "(replace the ALL-CAPS placeholders with actual values): "
        "<script type=\"application/ld+json\">{\"@context\":\"https://schema.org\",\"@type\":\"Article\","
        "\"headline\":\"TITLE\","
        "\"description\":\"SEO_DESCRIPTION\","
        f"\"author\":{{\"@type\":\"Organization\",\"name\":\"{_brand}\"}},"
        f"{_publisher_ld},"
        f"\"inLanguage\":\"{_lang_code}\","
        "\"datePublished\":\"DATE_ISO8601\"}"
        f"</script> — TITLE = the article headline, SEO_DESCRIPTION = the meta description, DATE_ISO8601 = today's date which is {datetime.date.today().isoformat()}."
    )

    _outline_checklist_lines = [
        "\n\n=== Pre-output compliance (outline — verify before JSON) ===\n",
        f"- Plan for a final merged HTML body of at least {MIN_ARTICLE_BODY_HTML_CHARS:,} characters (Python len on the full HTML string).\n",
        f"- Aim the written phases at {_body_aim_chars:,}+ so small undershoots still pass.\n",
    ]
    if _is_faq or _has_serp_paa:
        _outline_checklist_lines.append(
            "- FAQPage: later batches must include visible `<h3>` FAQ-style questions and a matching FAQPage JSON-LD block.\n"
        )
    if secondary_normalized:
        _outline_checklist_lines.append(
            "- Every secondary URL from INTERLINK STRATEGY must be coverable across the planned sections.\n"
        )
    if _required_body_keywords:
        if len(_required_body_keywords) == 1:
            _outline_checklist_lines.append(
                f"- Primary keyword for this draft: plan natural inclusion of {_pk_checklist_label} in on-page copy.\n"
            )
        else:
            _outline_checklist_lines.append(
                f"- Required keywords for this draft: plan natural inclusion of each: {_pk_checklist_label}.\n"
            )
    if _has_tier1_related_searches(idea_serp_context):
        _outline_checklist_lines.append(
            "- SERP tier 1–3 related searches: plan headings or body coverage for each position 1–3 query from the appendix.\n"
        )
    _outline_checklist = "".join(_outline_checklist_lines)

    user_outline_msg = (
        f"Plan a long-form SEO blog article for {_brand} on:\n\n"
        f"{topic.strip()}{_cluster_brief_section}{_cluster_kw_table_section}{_structural_section}{_regeneration_section}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
        f"{seo_brief_block}"
        f"{_serp_user_block}\n\n"
        f"{_faq_instruction}{_paa_faq_instruction}"
        f"{_outline_checklist}"
        f"{_authority_link_block}"
        f"{_collection_link_block}"
        "\n\nReturn JSON for **Phase 1 (outline + meta only)** with exactly these fields:\n"
        "- `title`: Same headline rules as full-article generation (20–70 characters, keyword-led, no banned prefixes).\n"
        f"- `seo_title`: Meta title tag (45–65 characters), distinct from the H1, end with ' | {_brand}' when it fits.\n"
        "- `seo_description`: Meta description (135–155 characters) with the same opening/ending rules as full-article generation.\n"
        "- `sections`: **8–14** objects in reading order. Each object has `heading` (plain text), `level` (`h2` or `h3`), "
        "and `beats` (3–10 sentences: subtopics, proof points, comparisons, objections, where internal links should appear — name destinations, do not invent URLs).\n"
        "The first planned section after the opening HTML batch should usually be the first major `h2`. "
        "Do not output full article HTML in this response.\n"
    )

    _shared_grounding = (
        f"Topic and research inputs for {_brand}:\n\n"
        f"{topic.strip()}{_cluster_brief_section}{_cluster_kw_table_section}{_structural_section}{_regeneration_section}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
        f"{seo_brief_block}"
        f"{_serp_user_block}\n\n"
        f"{_faq_instruction}{_paa_faq_instruction}"
        f"{_authority_link_block}"
        f"{_collection_link_block}"
        f"\n\n**Merged-body length target:** All HTML batches concatenated must reach **{_body_aim_chars:,}+** characters "
        f"(hard floor {MIN_ARTICLE_BODY_HTML_CHARS:,} after validation). Write each fragment with generous depth.\n"
    )

    json_schema = {
        "name": "article_draft",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": f"H1 article headline, 20–70 characters, keyword-led. {spelling_variant(_market_code)}",
                    "minLength": 20,
                    "maxLength": 70,
                },
                "seo_title": {
                    "type": "string",
                    "description": "Meta title tag, 45–65 characters.",
                    "minLength": 45,
                    "maxLength": 65,
                },
                "seo_description": {
                    "type": "string",
                    "description": "Meta description, 135–155 characters.",
                    "minLength": 135,
                    "maxLength": 155,
                },
                "body": {
                    "type": "string",
                    "description": (
                        f"Full article HTML. HARD REQUIREMENT (validated server-side): this string's length must be "
                        f">= {MIN_ARTICLE_BODY_HTML_CHARS} characters — count every character in the JSON value "
                        f"(all HTML tags, attributes, whitespace, scripts). Aim for >= {_body_aim_chars} so small "
                        "losses still pass. Failure mode: models often stop at ~12–13k characters; avoid that by "
                        "planning 9+ H2 sections each with 2+ substantive <p> paragraphs plus lists or tables where "
                        "helpful, FAQPage JSON-LD if required by the user prompt, and the Article JSON-LD block."
                    ),
                    "minLength": MIN_ARTICLE_BODY_HTML_CHARS,
                },
            },
            "required": ["title", "seo_title", "seo_description", "body"],
            "additionalProperties": False,
        },
    }

    outline_schema = {
        "name": "article_draft_outline",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": f"H1 article headline, 20–70 characters, keyword-led. {spelling_variant(_market_code)}",
                    "minLength": 20,
                    "maxLength": 70,
                },
                "seo_title": {
                    "type": "string",
                    "description": "Meta title tag, 45–65 characters.",
                    "minLength": 45,
                    "maxLength": 65,
                },
                "seo_description": {
                    "type": "string",
                    "description": "Meta description, 135–155 characters.",
                    "minLength": 135,
                    "maxLength": 155,
                },
                "sections": {
                    "type": "array",
                    "minItems": 8,
                    "maxItems": 14,
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string", "minLength": 4, "maxLength": 200},
                            "level": {"type": "string", "enum": ["h2", "h3"]},
                            "beats": {"type": "string", "minLength": 30, "maxLength": 1600},
                        },
                        "required": ["heading", "level", "beats"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "seo_title", "seo_description", "sections"],
            "additionalProperties": False,
        },
    }

    def _run_update(**fields: object) -> None:
        if not draft_run_id:
            return
        update_conn = None
        try:
            from shopifyseo.dashboard_store import db_connect

            update_conn = db_connect()
            update_article_draft_run(update_conn, draft_run_id, **fields)
        except Exception:
            logger.debug("Failed to persist article draft run update", exc_info=True)
        finally:
            if update_conn is not None:
                update_conn.close()

    def _emit(
        message: str,
        *,
        phase: str,
        state: str,
        step_key: str = "",
        step_label: str = "",
        step_index: int | None = None,
        step_total: int | None = None,
        item_done: int | None = None,
        item_total: int | None = None,
        result_summary: str = "",
    ) -> None:
        if draft_run_id and step_key:
            fields: dict[str, object] = {"current_step": step_key, "status": "running", "error_message": ""}
            if state == "done":
                fields["last_completed_step"] = step_key
            _run_update(**fields)
        if on_progress:
            payload: dict[str, object] = {"message": message, "phase": phase, "state": state}
            if draft_run_id:
                payload["run_id"] = draft_run_id
            if step_key:
                payload["step_key"] = step_key
            if step_label:
                payload["step_label"] = step_label
            if step_index is not None:
                payload["step_index"] = step_index
            if step_total is not None:
                payload["step_total"] = step_total
            if item_done is not None:
                payload["item_done"] = item_done
            if item_total is not None:
                payload["item_total"] = item_total
            if result_summary:
                payload["result_summary"] = result_summary
            on_progress(payload)

    secondary_urls_for_compliance = [n["url"] for n in secondary_normalized if (n.get("url") or "").strip()]

    # Compliance enforces every required body keyword: SERP-derived primary + first manual keyword.
    primary_kw_for_compliance = list(_required_body_keywords) or None
    require_faqpage_ld = bool(_is_faq or _has_serp_paa)
    _tier_queries = collect_tier_related_queries((idea_serp_context or {}).get("related_searches"), max_position=3)

    def _keyword_texts(raw_keywords: list[str | dict] | None) -> list[str]:
        out: list[str] = []
        for raw in raw_keywords or []:
            if isinstance(raw, dict):
                k = str(raw.get("keyword") or "").strip()
            else:
                k = str(raw or "").strip()
            if k and k.lower() not in {x.lower() for x in out}:
                out.append(k)
        return out

    manual_keyword_texts = _keyword_texts(keywords)
    idea_supporting_keywords = [
        str(x).strip()
        for x in ((idea_serp_context or {}).get("supporting_keywords") or [])
        if str(x).strip()
    ]
    all_signal_keywords: list[str] = []
    for bucket in (
        [str((idea_serp_context or {}).get("primary_keyword") or "").strip()],
        idea_supporting_keywords,
        manual_keyword_texts,
        [str(cluster_meta.get("primary_keyword") or "").strip()],
        cluster_kws,
        [str(x.get("keyword") or "").strip() for x in linked_keyword_rows],
        [str(x.get("keyword") or "").strip() for x in seo_gap_items],
    ):
        for kw in bucket:
            if kw and kw.lower() not in {x.lower() for x in all_signal_keywords}:
                all_signal_keywords.append(kw)

    target_metric_keywords = {
        kw.lower()
        for kw in all_signal_keywords
    }
    matching_target_metrics = [
        {
            "keyword": item.get("keyword") or "",
            "volume": item.get("volume") or item.get("search_volume") or 0,
            "difficulty": item.get("difficulty") or item.get("keyword_difficulty") or item.get("kd") or 0,
            "cpc": item.get("cpc") or 0,
            "opportunity": item.get("opportunity") or item.get("opportunity_score") or 0,
            "ranking_status": item.get("ranking_status") or "",
            "status": item.get("status") or "",
            "source_endpoint": item.get("source_endpoint") or "",
            "content_format_hint": item.get("content_format_hint") or item.get("format_hint") or "",
            "parent_topic": item.get("parent_topic") or "",
        }
        for item in target_items
        if str(item.get("keyword") or "").strip().lower() in target_metric_keywords
    ][:40]

    def _top_ranking_page_titles_only(raw_pages: object) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(raw_pages, list):
            return out
        for idx, page in enumerate(raw_pages[:10], start=1):
            if not isinstance(page, dict):
                continue
            title = str(page.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "position": page.get("position") or idx,
                "title": title,
            })
        return out

    seo_brief: dict[str, Any] = {
        "topic": topic.strip(),
        "intent": (idea_serp_context or {}).get("content_format_hints") or "",
        "request": dict(request_context or {}),
        "manual_target_keywords": manual_keyword_texts,
        "idea_keywords": {
            "primary_keyword": str((idea_serp_context or {}).get("primary_keyword") or "").strip(),
            "supporting_keywords": idea_supporting_keywords,
            "linked_keywords_json": linked_keyword_rows,
        },
        "cluster": {
            "id": linked_cluster_id,
            "meta": cluster_meta,
            "keywords": cluster_kws,
            "keyword_metrics": cluster_kw_metrics,
        },
        "target_keyword_metrics": matching_target_metrics,
        "seo_gap_keywords": seo_gap_items,
        "regeneration": (
            {
                "existing_title": str(regeneration_context.get("existing_title") or "").strip(),
                "existing_gsc_position": regeneration_context.get("existing_gsc_position"),
                "existing_gsc_queries": [
                    {
                        "query": str(q.get("query") or "").strip(),
                        "clicks": int(q.get("clicks") or 0),
                        "impressions": int(q.get("impressions") or 0),
                        "position": q.get("position"),
                    }
                    for q in (regeneration_context.get("existing_gsc_queries") or [])
                    if isinstance(q, dict) and str(q.get("query") or "").strip()
                ][:25],
            }
            if regeneration_context
            else {}
        ),
        "serp": {
            "suggested_title": (idea_serp_context or {}).get("suggested_title") or "",
            "brief": (idea_serp_context or {}).get("brief") or "",
            "gap_reason": (idea_serp_context or {}).get("gap_reason") or "",
            "dominant_serp_features": (idea_serp_context or {}).get("dominant_serp_features") or "",
            "content_format_hints": (idea_serp_context or {}).get("content_format_hints") or "",
            "audience_questions": _paa_rows if isinstance(_paa_rows, list) else [],
            "paa_hierarchy": _paa_hierarchy,
            "required_faq_questions": required_questions,
            "related_searches": (idea_serp_context or {}).get("related_searches") or [],
            "tier_1_3_related_searches": _tier_queries,
            "top_ranking_pages": _top_ranking_page_titles_only((idea_serp_context or {}).get("top_ranking_pages")),
            "ai_overview": (idea_serp_context or {}).get("ai_overview") or {},
        },
        "internal_links": {
            "primary": primary_normalized or {},
            "secondary": secondary_normalized,
            "approved_targets": link_targets,
        },
        "store_references": rag_results,
        "market": {
            "brand": _brand,
            "store_domain": _store_domain,
            "base_url": _base_url,
            "country_code": _market_code,
            "country_name": _market_name,
            "language": _lang_code,
            "shipping_phrase": _ship_phrase,
            "availability_phrase": _avail_phrase,
        },
        "required_coverage": {
            "primary_keyword": (_required_body_keywords[0] if _required_body_keywords else _pk_checklist),
            "required_keywords_in_body": list(_required_body_keywords),
            "keywords": all_signal_keywords[:60],
            "faq_questions": required_questions,
            "related_searches": _tier_queries,
            "primary_link": primary_normalized.get("url") if primary_normalized else "",
            "secondary_links": secondary_urls_for_compliance,
            "information_gain": ["comparison table", "buyer checklist", "troubleshooting/decision guidance"],
            "body_length_min": MIN_ARTICLE_BODY_HTML_CHARS,
            "body_length_target": _body_aim_chars,
        },
    }
    _run_update(
        seo_brief_json=seo_brief,
        current_step="prepare_brief",
        last_completed_step="prepare_brief",
    )
    _emit(
        "SEO brief prepared from keywords, cluster, SERP, links, store context, and market signals.",
        phase="content",
        state="done",
        step_key="prepare_brief",
        step_label="Prepare SEO brief",
        step_index=1,
        step_total=11,
        result_summary=(
            f"Signals: {len(all_signal_keywords)} keywords, {len(required_questions)} FAQ/PAA, "
            f"{(1 if primary_normalized else 0) + len(secondary_urls_for_compliance)} required links"
        ),
    )

    seo_brief_block = (
        "\n\nCANONICAL SEO BRIEF (source of truth for every draft step):\n"
        + json.dumps(seo_brief, ensure_ascii=True)[:18000]
        + "\n"
    )
    _grounding_anchor = f"{topic.strip()}{_cluster_brief_section}{_cluster_kw_table_section}{_structural_section}{_regeneration_section}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
    if _grounding_anchor:
        user_msg = user_msg.replace(_grounding_anchor, _grounding_anchor + seo_brief_block, 1)
        user_outline_msg = user_outline_msg.replace(_grounding_anchor, _grounding_anchor + seo_brief_block, 1)
        _shared_grounding = _shared_grounding.replace(_grounding_anchor, _grounding_anchor + seo_brief_block, 1)

    def _sanitize_body(raw_html: str) -> str:
        out = str(raw_html or "")
        if "<a " in out.lower():
            out = sanitize_article_internal_links(
                out, path_to_canonical=path_to_canonical, base_url=_base_url
            )
        out = strip_faqpage_jsonld_blocks(out)
        return out

    def _compliance_gaps(body_html: str) -> list[str]:
        return validate_article_draft_compliance(
            body_html=body_html,
            require_faqpage_ld=require_faqpage_ld,
            secondary_urls=secondary_urls_for_compliance,
            primary_keyword_for_body=primary_kw_for_compliance,
            path_to_canonical=path_to_canonical,
            tier1_related_queries=_tier_queries,
        )

    resume_checkpoints = resume_run.get("checkpoints") if isinstance(resume_run, dict) else {}
    if not isinstance(resume_checkpoints, dict):
        resume_checkpoints = {}

    def _norm_loose(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()

    def _article_memory(body_html: str, html_parts: list[str] | None = None) -> dict[str, Any]:
        visible = strip_html_for_compliance_search(body_html)
        covered_keywords = [kw for kw in all_signal_keywords if kw and kw.lower() in visible]
        covered_questions = [q for q in required_questions if _norm_loose(q) and _norm_loose(q) in _norm_loose(visible)]
        covered_related = [q for q in _tier_queries if _norm_loose(q) and _norm_loose(q) in _norm_loose(visible)]
        hrefs = collect_hrefs(body_html)
        faq_items = extract_visible_faq_items(body_html, required_questions=required_questions)
        summaries: list[str] = []
        for idx, part in enumerate((html_parts or [])[-8:], start=max(1, len(html_parts or []) - 7)):
            txt = strip_html_for_compliance_search(part)
            if txt:
                summaries.append(f"part {idx}: {txt[:240]}")
        return {
            "body_chars": len(body_html or ""),
            "visible_word_estimate": len(re.findall(r"[a-z0-9']+", visible)),
            "covered_keywords": covered_keywords,
            "covered_questions": covered_questions,
            "covered_related_searches": covered_related,
            "used_links": hrefs,
            "faq_candidates": faq_items,
            "section_summaries": summaries,
        }

    def _save_memory(body_html: str, html_parts: list[str] | None = None) -> dict[str, Any]:
        memory = _article_memory(body_html, html_parts)
        _run_update(article_memory_json=memory)
        return memory

    def _remaining_requirements(body_html: str) -> dict[str, Any]:
        memory = _article_memory(body_html)
        covered_kw = {str(x).lower() for x in (memory.get("covered_keywords") or [])}
        covered_q = {_norm_loose(str(x)) for x in (memory.get("covered_questions") or [])}
        covered_rel = {_norm_loose(str(x)) for x in (memory.get("covered_related_searches") or [])}
        hrefs = collect_hrefs(body_html)

        def _has_url(url: str) -> bool:
            u = (url or "").strip()
            if not u:
                return True
            path = (urlparse(u).path or "").rstrip("/") or "/"
            return any(h == u or ((urlparse(h).path or "").rstrip("/") or "/") == path for h in hrefs)

        return {
            "keywords": [kw for kw in all_signal_keywords if kw.lower() not in covered_kw][:40],
            "faq_questions": [q for q in required_questions if _norm_loose(q) not in covered_q],
            "related_searches": [q for q in _tier_queries if _norm_loose(q) not in covered_rel],
            "primary_link": (
                primary_normalized.get("url")
                if primary_normalized and not _has_url(primary_normalized.get("url") or "")
                else ""
            ),
            "secondary_links": [url for url in secondary_urls_for_compliance if not _has_url(url)],
            "body_chars_remaining_to_target": max(0, _body_aim_chars - len(body_html or "")),
        }

    def _render_article_jsonld(title: str, seo_description: str) -> str:
        payload: dict[str, Any] = {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": title,
            "description": seo_description,
            "author": {"@type": "Organization", "name": _brand},
            "publisher": {"@type": "Organization", "name": _brand},
            "inLanguage": _lang_code,
            "datePublished": datetime.date.today().isoformat(),
        }
        if _base_url:
            payload["publisher"]["url"] = _base_url
        return '<script type="application/ld+json">' + json.dumps(payload, ensure_ascii=False) + "</script>"

    def _ensure_article_jsonld(body_html: str, title: str, seo_description: str) -> str:
        body = body_html or ""
        if "application/ld+json" in body.lower() and '"Article"' in body:
            return body
        return body.rstrip() + "\n" + _render_article_jsonld(title, seo_description)

    def _link_html(url: str, label: str) -> str:
        safe_url = html_module.escape(url or "", quote=True)
        safe_label = html_module.escape(label or url or "related resource")
        title = html_module.escape(f"{label or 'Related resource'} — {_brand}", quote=True)
        return f'<a href="{safe_url}" title="{title}">{safe_label}</a>'

    def _ensure_required_links(body_html: str) -> str:
        body = body_html or ""
        hrefs = collect_hrefs(body)
        href_blob = " ".join(hrefs)
        inserts: list[str] = []
        if primary_normalized and primary_normalized.get("url"):
            p_url = primary_normalized["url"]
            p_path = (urlparse(p_url).path or "").rstrip("/") or "/"
            has_primary = any(
                h == p_url or ((urlparse(h).path or "").rstrip("/") or "/") == p_path
                for h in hrefs
            )
            if not has_primary:
                label = primary_normalized.get("title") or primary_normalized.get("handle") or "main buying guide"
                para = (
                    f'<p>For the main product or category context, start with {_link_html(p_url, label)} '
                    "before comparing options in detail.</p>"
                )
                if "</p>" in body.lower():
                    body = re.sub(r"(?is)</p>", "</p>\n" + para, body, count=1)
                else:
                    body = para + body
                hrefs = collect_hrefs(body)
                href_blob = " ".join(hrefs)
        for n in secondary_normalized:
            u = (n.get("url") or "").strip()
            if not u or u in href_blob:
                continue
            path = (urlparse(u).path or "").rstrip("/") or "/"
            if any(((urlparse(h).path or "").rstrip("/") or "/") == path for h in hrefs):
                continue
            label = n.get("anchor_keyword") or n.get("title") or n.get("handle") or "related option"
            inserts.append(f"<li>{_link_html(u, label)}</li>")
        if inserts:
            body = body.rstrip() + "\n<h2>Related resources for this topic</h2><ul>" + "".join(inserts) + "</ul>"
        return body

    def _questions_missing_from_body(body_html: str, questions: list[str]) -> list[str]:
        visible = _norm_loose(strip_html_for_compliance_search(body_html))
        missing: list[str] = []
        for q in questions:
            key = _norm_loose(q)
            if key and key not in visible:
                missing.append(q)
        return missing

    # Build a normalized question -> snippet map from PAA top-level + hierarchy children.
    # This grounds FAQ repair in the SerpAPI snippets that prompted those questions instead
    # of letting the repair AI guess answers in a vacuum.
    _paa_snippet_by_question_norm: dict[str, str] = {}

    def _record_paa_snippet(q: object, sn: object) -> None:
        q_str = str(q or "").strip()
        sn_str = str(sn or "").strip()
        if not q_str or not sn_str:
            return
        key = _norm_loose(q_str)
        if not key or key in _paa_snippet_by_question_norm:
            return
        _paa_snippet_by_question_norm[key] = sn_str

    for _row in _paa_rows if isinstance(_paa_rows, list) else []:
        if isinstance(_row, dict):
            _record_paa_snippet(_row.get("question"), _row.get("snippet"))
    for _layer in _paa_hierarchy or []:
        if not isinstance(_layer, dict):
            continue
        _record_paa_snippet(_layer.get("parent_question"), _layer.get("snippet"))
        for _child in _layer.get("children") or []:
            if isinstance(_child, dict):
                _record_paa_snippet(_child.get("question"), _child.get("snippet"))

    def _append_faq_answers(body_html: str) -> str:
        missing = _questions_missing_from_body(body_html, required_questions)
        if not missing:
            return body_html
        schema = {
            "name": "article_draft_faq_repair",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "answers": {
                        "type": "array",
                        "minItems": len(missing),
                        "maxItems": len(missing),
                        "items": {"type": "string", "minLength": 80, "maxLength": 900},
                    }
                },
                "required": ["answers"],
                "additionalProperties": False,
            },
        }
        # Pair each missing question with its SerpAPI snippet (when we have one) so the
        # repair AI can ground its answer in what Google's PAA already surfaces.
        missing_with_snippets: list[dict[str, str]] = []
        for q in missing:
            snippet = _paa_snippet_by_question_norm.get(_norm_loose(q), "")
            missing_with_snippets.append({"question": q, "snippet": snippet})

        snippets_grounding_lines: list[str] = []
        for entry in missing_with_snippets:
            sn = entry["snippet"]
            if not sn:
                continue
            snippets_grounding_lines.append(
                f"- Question: {entry['question']}\n  SerpAPI snippet (Google PAA — non-authoritative; paraphrase, do not cite as fact): {sn}"
            )
        snippets_grounding_block = ""
        if snippets_grounding_lines:
            snippets_grounding_block = (
                "\n\nPAA snippets to paraphrase (synthesise into a richer answer; do NOT copy verbatim "
                "and do NOT claim the snippet as a source):\n"
                + "\n".join(snippets_grounding_lines)
            )

        answers: list[str] = []
        try:
            out = _call_ai(
                settings,
                provider,
                model,
                [
                    {"role": "system", "content": system_section},
                    {
                        "role": "user",
                        "content": (
                            "Write concise FAQ answers for the exact questions below, in the same order. "
                            "Use the canonical SEO brief and the article context. When a SerpAPI snippet is provided "
                            "for a question, treat it as a non-authoritative starting point — paraphrase, expand with "
                            "store-specific value, and do not cite the snippet as a source. Return only JSON. "
                            "Questions (each is `{question, snippet}` — snippet may be empty):\n"
                            + json.dumps(missing_with_snippets, ensure_ascii=True)
                            + snippets_grounding_block
                            + "\n\nCanonical SEO brief:\n"
                            + json.dumps(seo_brief, ensure_ascii=True)[:12000]
                        ),
                    },
                ],
                timeout,
                json_schema=schema,
                stage="article_draft_faq_repair",
            )
            raw_answers = out.get("answers") if isinstance(out, dict) else []
            answers = [str(x).strip() for x in raw_answers if str(x).strip()]
        except Exception:
            logger.warning("FAQ repair AI failed; using deterministic fallback answers", exc_info=True)
        while len(answers) < len(missing):
            answers.append(
                "The best answer depends on your device, preferences, budget, and local availability. "
                "Use the criteria in this guide to compare options carefully before choosing."
            )
        block = "\n<h2>Helpful questions before you choose</h2>"
        for q, ans in zip(missing, answers):
            block += f"\n<h3>{html_module.escape(q)}</h3><p>{html_module.escape(ans)}</p>"
        return (body_html or "").rstrip() + block

    def _append_repair_html(body_html: str, gaps: list[str], title: str) -> str:
        deficit = max(0, _body_aim_chars - len(body_html or ""))
        min_len = max(700, min(5000, deficit + 300))
        schema = {
            "name": "article_draft_append_repair",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "append_html": {
                        "type": "string",
                        "minLength": min_len,
                        "maxLength": max(2500, min_len + 5000),
                    }
                },
                "required": ["append_html"],
                "additionalProperties": False,
            },
        }
        out = _call_ai(
            settings,
            provider,
            model,
            [
                {"role": "system", "content": system_section},
                {
                    "role": "user",
                    "content": (
                        "Append only new HTML that fixes these validation gaps. Do not repeat existing sections. "
                        "Use the canonical SEO brief, the locked article title, and the current article memory. "
                        "Return JSON with append_html only.\n\n"
                        f"Title: {title}\n"
                        f"Gaps: {json.dumps(gaps, ensure_ascii=True)}\n"
                        f"Current body chars: {len(body_html or '')}\n"
                        f"Canonical SEO brief:\n{json.dumps(seo_brief, ensure_ascii=True)[:12000]}\n"
                        f"Article memory:\n{json.dumps(_article_memory(body_html), ensure_ascii=True)[:8000]}\n"
                    ),
                },
            ],
            timeout,
            json_schema=schema,
            stage="article_draft_append_repair",
        )
        return (body_html or "").rstrip() + "\n" + str(out.get("append_html") or "")

    def _finalize_and_repair_body(result_local: dict, body_html: str, html_parts: list[str] | None = None) -> tuple[str, dict[str, Any]]:
        title = str(result_local.get("title") or "")
        seo_desc = str(result_local.get("seo_description") or "")
        body = _sanitize_body(body_html)
        _emit(
            "Building FAQ/schema from visible article text…",
            phase="content",
            state="start",
            step_key="faq_schema",
            step_label="Build FAQ/schema",
            step_index=4,
            step_total=11,
        )
        if not require_faqpage_ld:
            _emit(
                "FAQ schema skipped; this draft has no required PAA/FAQ signals.",
                phase="content",
                state="skipped",
                step_key="faq_schema",
                step_label="Build FAQ/schema",
                step_index=4,
                step_total=11,
            )
        for attempt in range(3):
            body = _ensure_required_links(body)
            if require_faqpage_ld:
                body = _append_faq_answers(body)
                body, _faq_items = append_server_generated_faqpage_jsonld(
                    body,
                    required_questions=required_questions,
                )
                _emit(
                    "FAQPage schema generated from visible FAQ questions.",
                    phase="content",
                    state="done",
                    step_key="faq_schema",
                    step_label="Build FAQ/schema",
                    step_index=4,
                    step_total=11,
                    result_summary=f"FAQ {len(_faq_items)}/{max(len(required_questions), len(_faq_items))}",
                )
            body = _ensure_article_jsonld(body, title, seo_desc)
            _emit(
                "Validating article and applying targeted repairs…",
                phase="content",
                state="waiting" if attempt == 0 else "running",
                step_key="validate_repair",
                step_label="Validate and repair",
                step_index=5,
                step_total=11,
                result_summary=f"Attempt {attempt + 1}/3 · Body {len(body):,} chars",
            )
            gaps = _compliance_gaps(body)
            if not gaps:
                memory = _save_memory(body, html_parts)
                validation = {
                    "ok": True,
                    "body_chars": len(body),
                    "faq_items": len(extract_visible_faq_items(body, required_questions=required_questions)),
                    "links": len(collect_hrefs(body)),
                    "covered_keywords": len(memory.get("covered_keywords") or []),
                    "repairs": attempt,
                }
                _run_update(validation_summary_json=validation)
                _emit(
                    "Article passed validation after targeted checks.",
                    phase="content",
                    state="done",
                    step_key="validate_repair",
                    step_label="Validate and repair",
                    step_index=5,
                    step_total=11,
                    result_summary=(
                        f"Body {len(body):,} chars · FAQ {validation['faq_items']} · "
                        f"Links {validation['links']} · Repairs {attempt}"
                    ),
                )
                return body, validation
            if attempt >= 2:
                break
            _emit(
                "Repairing only the missing validation items.",
                phase="content",
                state="running",
                step_key="validate_repair",
                step_label="Validate and repair",
                step_index=5,
                step_total=11,
                result_summary=f"{len(gaps)} gap{'s' if len(gaps) != 1 else ''} found",
            )
            body = _append_repair_html(body, gaps, title)
            body = _sanitize_body(body)
        final_gaps = _compliance_gaps(body)
        if final_gaps:
            raise RuntimeError(
                "Article draft failed compliance after targeted repairs: " + " | ".join(final_gaps)
            )
        validation = {"ok": True, "body_chars": len(body), "repairs": 3}
        _run_update(validation_summary_json=validation)
        return body, validation

    def _persist_content_checkpoint(result_local: dict, body_html: str, validation: dict[str, Any], html_parts: list[str] | None = None) -> None:
        checkpoints = dict(resume_checkpoints)
        checkpoints["content"] = {
            "saved": True,
            "body_chars": len(body_html or ""),
            "validation": validation,
        }
        if html_parts is not None:
            checkpoints["html_parts"] = html_parts
            checkpoints["completed_batches"] = checkpoints.get("completed_batches") or 0
        _run_update(
            current_step="content_checkpoint",
            last_completed_step="content_checkpoint",
            checkpoints_json=checkpoints,
            title=str(result_local.get("title") or ""),
            seo_title=str(result_local.get("seo_title") or ""),
            seo_description=str(result_local.get("seo_description") or ""),
            body=body_html or "",
            validation_summary_json=validation,
        )
        _emit(
            "Passed article content saved before image work.",
            phase="content",
            state="done",
            step_key="content_checkpoint",
            step_label="Save content checkpoint",
            step_index=6,
            step_total=11,
            result_summary=f"Checkpoint saved · Body {len(body_html or ''):,} chars",
        )

    def _single_shot_with_retries() -> tuple[dict, str]:
        messages_local: list[dict] = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ]
        _emit("Preparing article prompt and keyword context…", phase="content", state="start")
        _emit("Sending request to AI — writing full article (often 1–3 minutes)…", phase="content", state="waiting")
        try:
            res = _call_ai(
                settings, provider, model, messages_local, timeout, json_schema=json_schema, stage="article_draft"
            )
        except AIProviderRequestError as exc:
            raise RuntimeError(str(exc)) from exc
        _emit("Article content received — validating JSON fields…", phase="content", state="done")
        body_local = _sanitize_body(str(res.get("body") or ""))
        body_local, validation = _finalize_and_repair_body(res, body_local, [body_local])
        _persist_content_checkpoint(res, body_local, validation, [body_local])
        return res, body_local

    def _section_batch_schema(n_items: int, min_len: int) -> dict:
        max_len = min(24000, max(8000, min_len * 24))
        return {
            "name": "article_draft_section_batch",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "html_blocks": {
                        "type": "array",
                        "minItems": n_items,
                        "maxItems": n_items,
                        "items": {"type": "string", "minLength": min_len, "maxLength": max_len},
                    },
                },
                "required": ["html_blocks"],
                "additionalProperties": False,
            },
        }

    def _try_phased() -> tuple[dict, str] | None:
        _intro_beats = (
            "Opening only: 1–2 sentences that directly answer the reader's main question, then 2–4 `<p>` paragraphs "
            f"with practical context for {_market_name}. Include **exactly one** prominent `<a href>` to the PRIMARY "
            "authority URL from INTERLINK STRATEGY with natural anchor text in this opening fragment. "
            "Do not include an `<h2>` here — section headings start in later fragments."
        )
        outline: dict[str, Any]
        resume_outline = resume_run.get("outline") if isinstance(resume_run, dict) else None
        if isinstance(resume_outline, dict) and resume_outline.get("sections"):
            outline = dict(resume_outline)
            _emit(
                "Using saved outline checkpoint.",
                phase="content",
                state="done",
                step_key="outline",
                step_label="Generate outline",
                step_index=2,
                step_total=11,
                result_summary=f"{len(outline.get('sections') or [])} sections",
            )
        else:
            try:
                _emit(
                    "Generating article outline from canonical SEO brief…",
                    phase="content",
                    state="waiting",
                    step_key="outline",
                    step_label="Generate outline",
                    step_index=2,
                    step_total=11,
                )
                outline_messages = [
                    {"role": "system", "content": system_outline},
                    {"role": "user", "content": user_outline_msg},
                ]
                outline = _call_ai(
                    settings,
                    provider,
                    model,
                    outline_messages,
                    timeout,
                    json_schema=outline_schema,
                    stage="article_draft_outline",
                )
            except AIProviderRequestError as exc:
                raise RuntimeError(str(exc)) from exc
        sections_raw = outline.get("sections")
        if not isinstance(sections_raw, list) or not (8 <= len(sections_raw) <= 14):
            raise RuntimeError("Article outline failed validation: expected 8–14 planned sections.")
        work_items: list[dict[str, str]] = [
            {"kind": "intro", "heading": "", "level": "", "beats": _intro_beats},
        ]
        for sec in sections_raw:
            if not isinstance(sec, dict):
                raise RuntimeError("Article outline failed validation: section item was invalid.")
            heading = str(sec.get("heading") or "").strip()
            level = str(sec.get("level") or "").strip().lower()
            beats = str(sec.get("beats") or "").strip()
            if level not in ("h2", "h3") or not heading or len(beats) < 10:
                raise RuntimeError("Article outline failed validation: each section needs a heading, level, and useful beats.")
            work_items.append({"kind": "section", "heading": heading, "level": level, "beats": beats})
        _run_update(outline_json=outline, last_completed_step="outline", current_step="outline")
        _emit(
            "Outline locked and mapped to SEO signals.",
            phase="content",
            state="done",
            step_key="outline",
            step_label="Generate outline",
            step_index=2,
            step_total=11,
            result_summary=f"{len(sections_raw)} sections · {len(all_signal_keywords)} keywords available",
        )

        n_work = len(work_items)
        min_frag = max(450, min(1400, _body_aim_chars // max(n_work, 6)))
        batch_size = 3
        batches: list[list[dict[str, str]]] = [
            work_items[i : i + batch_size] for i in range(0, n_work, batch_size)
        ]
        outline_title = str(outline.get("title") or "").strip()
        outline_digest = json.dumps(
            [{"heading": it.get("heading") or "(intro)", "level": it.get("level") or "", "beats": it.get("beats")} for it in work_items],
            ensure_ascii=True,
        )
        raw_saved_parts = resume_checkpoints.get("html_parts")
        html_parts: list[str] = [str(x) for x in raw_saved_parts] if isinstance(raw_saved_parts, list) else []
        completed_batches = int(resume_checkpoints.get("completed_batches") or 0)
        completed_fragments = min(len(html_parts), completed_batches * batch_size)
        if completed_fragments < len(html_parts):
            html_parts = html_parts[:completed_fragments]
        start_batch = min(max(0, completed_batches), len(batches))
        if start_batch:
            _emit(
                f"Resuming section writing from batch {start_batch + 1}.",
                phase="content",
                state="running",
                step_key="write_sections",
                step_label="Write section batches",
                step_index=3,
                step_total=11,
                item_done=start_batch,
                item_total=len(batches),
                result_summary=f"Loaded {len(html_parts)} saved fragments",
            )
        n_batches = len(batches)
        for bi, batch in enumerate(batches[start_batch:], start=start_batch):
            is_last = bi == n_batches - 1
            _emit(
                f"Writing article HTML — batch {bi + 1} of {n_batches}…",
                phase="content",
                state="waiting",
                step_key="write_sections",
                step_label="Write section batches",
                step_index=3,
                step_total=11,
                item_done=bi,
                item_total=n_batches,
                result_summary=f"Body so far {len(''.join(html_parts)):,} chars",
            )
            body_so_far = "".join(html_parts)
            memory = _article_memory(body_so_far, html_parts)
            remaining = _remaining_requirements(body_so_far)
            frag_lines: list[str] = [
                _shared_grounding,
                "\n\n=== Locked outline (Phase 1) ===\n",
                f"title: {outline_title}\n",
                f"sections_json: {outline_digest}\n",
                "\n=== Current article memory ===\n",
                json.dumps(memory, ensure_ascii=True)[:8000],
                "\n\n=== Remaining required coverage ===\n",
                json.dumps(remaining, ensure_ascii=True)[:8000],
                f"\n=== Batch {bi + 1} of {n_batches} ===\n",
                f"Return JSON with `html_blocks` array of exactly **{len(batch)}** HTML strings in this order:\n",
            ]
            for j, it in enumerate(batch):
                frag_lines.append(f"\n--- Fragment {j + 1} of {len(batch)} in this batch ---\n")
                if it.get("kind") == "intro":
                    frag_lines.append(it["beats"] + "\n")
                else:
                    frag_lines.append(
                        f"Render a `{it['level']}` heading with text: {it['heading']!r} and full section HTML per beats:\n"
                        f"{it['beats']}\n"
                    )
            if is_last:
                frag_lines.append(
                    "\n**Final batch — last `html_blocks` element must end with:**\n"
                    "- All required FAQPage JSON-LD (if the topic rules require it), aligned to visible FAQ `<h3>` "
                    "wording from the merged article.\n"
                    f"- {_article_ld_instruction}\n"
                )
            user_batch = "".join(frag_lines)
            batch_messages = [
                {"role": "system", "content": system_section},
                {"role": "user", "content": user_batch},
            ]
            schema_b = _section_batch_schema(len(batch), min_frag)
            try:
                batch_out = _call_ai(
                    settings,
                    provider,
                    model,
                    batch_messages,
                    timeout,
                    json_schema=schema_b,
                    stage="article_draft_section",
                )
            except AIProviderRequestError as exc:
                raise RuntimeError(str(exc)) from exc
            blocks = batch_out.get("html_blocks")
            if not isinstance(blocks, list) or len(blocks) != len(batch):
                raise RuntimeError(f"Article section batch {bi + 1} returned invalid HTML fragments.")
            for k, frag in enumerate(blocks):
                if not isinstance(frag, str) or not frag.strip():
                    raise RuntimeError(f"Article section batch {bi + 1} returned an empty fragment.")
            html_parts.extend(blocks)
            memory = _save_memory("".join(html_parts), html_parts)
            checkpoints = dict(resume_checkpoints)
            checkpoints["html_parts"] = html_parts
            checkpoints["completed_batches"] = bi + 1
            checkpoints["section_memory"] = memory
            _run_update(
                checkpoints_json=checkpoints,
                current_step="write_sections",
                last_completed_step="write_sections" if bi == n_batches - 1 else "outline",
            )
            resume_checkpoints.update(checkpoints)
            _emit(
                f"Batch {bi + 1} of {n_batches} saved.",
                phase="content",
                state="done" if bi == n_batches - 1 else "running",
                step_key="write_sections",
                step_label="Write section batches",
                step_index=3,
                step_total=11,
                item_done=bi + 1,
                item_total=n_batches,
                result_summary=f"Body {len(''.join(html_parts)):,} chars · {len(memory.get('covered_keywords') or [])} keywords covered",
            )
        raw_body = "".join(html_parts)
        meta = {
            "title": outline.get("title"),
            "seo_title": outline.get("seo_title"),
            "seo_description": outline.get("seo_description"),
        }
        body_merged, validation = _finalize_and_repair_body(meta, raw_body, html_parts)
        _persist_content_checkpoint(meta, body_merged, validation, html_parts)
        return meta, body_merged

    use_phased = bool(settings.get("article_draft_phased", True))
    if use_phased:
        _emit("Using phased generation (outline + HTML batches)…", phase="content", state="start")
        phased_pair = _try_phased()
        if phased_pair is None:
            raise RuntimeError("Article phased generation did not return a draft.")
        result, body_out = phased_pair
    else:
        result, body_out = _single_shot_with_retries()

    # Hard-fail if a primary authority target was supplied but absent from the body.
    if primary_normalized and primary_normalized.get("url"):
        primary_url = primary_normalized["url"]
        primary_path = (urlparse(primary_url).path or "").rstrip("/") or "/"
        body_lower = body_out.lower()
        has_link = False
        for m in _A_BODY_TAG_RE.finditer(body_out):
            attrs = m.group(1) or ""
            hm = re.search(r"""href\s*=\s*(["'])(.*?)\1""", attrs, re.I | re.DOTALL)
            if not hm:
                continue
            href = (hm.group(2) or "").strip()
            if href == primary_url:
                has_link = True
                break
            href_path = (urlparse(href).path or "").rstrip("/") or "/"
            if primary_path != "/" and href_path == primary_path:
                has_link = True
                break
        if not has_link:
            raise RuntimeError(
                f"Draft missing required primary authority link to {primary_url}. "
                f"Article must contextually link back to the cluster's target page."
            )

    return {
        "title": str(result.get("title") or ""),
        "seo_title": clamp_generated_seo_field("seo_title", str(result.get("seo_title") or "")),
        "seo_description": clamp_generated_seo_field("seo_description", str(result.get("seo_description") or "")),
        "body": body_out,
    }


def ensure_link_titles(body_html: str, conn: sqlite3.Connection) -> str:
    """Post-process article body HTML to fill in missing ``title`` attributes on ``<a>`` tags.

    Matches each link's href against collections, products, pages, and blog articles
    in the local DB and sets ``title`` to the page title when missing.
    """
    if not body_html or "<a " not in body_html:
        return body_html

    import html as _html

    url_to_title: dict[str, str] = {}
    try:
        from .. import dashboard_queries as _dq
        from .config import get_store_identity
        _sname, _ = get_store_identity(conn)
        _brand_suffix = f" — {_sname}" if _sname else ""

        base_url = _dq._base_store_url(conn)

        _type_prefix = {
            "collections": "Shop {title}{brand}",
            "products": "{title}{brand}",
            "pages": "{title}{brand}",
        }
        for table, path_prefix in [
            ("collections", "/collections"),
            ("products", "/products"),
            ("pages", "/pages"),
        ]:
            tmpl = _type_prefix[table]
            rows = conn.execute(f"SELECT handle, title FROM {table}").fetchall()
            for r in rows:
                handle = r["handle"] or r[0]
                title = r["title"] or r[1]
                if handle and title:
                    seo_title = tmpl.format(title=title, brand=_brand_suffix)
                    rel = f"{path_prefix}/{handle}"
                    url_to_title[rel] = seo_title
                    if base_url:
                        url_to_title[f"{base_url}{rel}"] = seo_title

        blog_rows = conn.execute("SELECT blog_handle, handle, title FROM blog_articles").fetchall()
        for r in blog_rows:
            bh = r["blog_handle"] or r[0]
            ah = r["handle"] or r[1]
            title = r["title"] or r[2]
            if bh and ah and title:
                seo_title = f"Read {title}{_brand_suffix}"
                rel = f"/blogs/{bh}/{ah}"
                url_to_title[rel] = seo_title
                if base_url:
                    url_to_title[f"{base_url}{rel}"] = seo_title
    except Exception:
        logger.debug("ensure_link_titles: failed to build URL→title map", exc_info=True)
        return body_html

    if not url_to_title:
        return body_html

    _A_PATTERN = re.compile(r"<a\b([^>]*)>", re.IGNORECASE)
    _HREF_PATTERN = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
    _TITLE_PATTERN = re.compile(r"""title\s*=\s*["']""", re.IGNORECASE)

    def _add_title(match: re.Match) -> str:
        tag = match.group(0)
        attrs = match.group(1)
        if _TITLE_PATTERN.search(attrs):
            return tag
        href_match = _HREF_PATTERN.search(attrs)
        if not href_match:
            return tag
        href = href_match.group(1).rstrip("/")
        title = url_to_title.get(href)
        if not title:
            href_no_trailing = href.split("?")[0].split("#")[0].rstrip("/")
            title = url_to_title.get(href_no_trailing)
        if not title:
            for url, t in url_to_title.items():
                if href.endswith(url) or url.endswith(href.lstrip("/")):
                    title = t
                    break
        if not title:
            return tag
        safe_title = _html.escape(title, quote=True)
        return tag[:-1] + f' title="{safe_title}">'

    return _A_PATTERN.sub(_add_title, body_html)
