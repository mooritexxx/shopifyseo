"""Article draft generation and HTML link utilities."""
import datetime
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
    from .serp_draft_context import MAX_PAA_QUESTIONS
    from .article_draft_compliance import (
        COMPLIANCE_BODY_LENGTH_RETRY_MARGIN,
        MIN_ARTICLE_BODY_HTML_CHARS,
        build_compliance_retry_user_message,
        collect_tier_related_queries,
        length_only_article_compliance_gaps,
        mixed_length_and_serp_compliance_gaps,
        validate_article_draft_compliance,
    )

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

    # Compute SEO keyword gaps from linked cluster (new article = empty content)
    _seo_gap_section = ""
    if linked_cluster_id is not None:
        try:
            from backend.app.services.keyword_clustering import compute_seo_gaps
            from shopifyseo.dashboard_google import get_service_setting as _get_ss

            kw_rows = conn.execute(
                "SELECT keyword FROM cluster_keywords WHERE cluster_id = ?",
                (linked_cluster_id,),
            ).fetchall()
            cluster_kws = [r[0] for r in kw_rows]

            pk_row = conn.execute(
                "SELECT primary_keyword FROM clusters WHERE id = ?",
                (linked_cluster_id,),
            ).fetchone()
            primary_kw = pk_row[0] if pk_row else ""

            target_raw = _get_ss(conn, "target_keywords", "{}")
            target_data = json.loads(target_raw) if target_raw else {}
            kw_map: dict[str, dict] = {}
            for item in target_data.get("items") or []:
                kw_map[item.get("keyword", "").lower()] = item

            if cluster_kws:
                gaps = compute_seo_gaps(
                    cluster_kws, {}, kw_map, "blog_article", primary_kw,
                )
                if gaps:
                    mc_lines = []
                    for mc in gaps["must_consider"]:
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
    _has_serp_paa = bool(isinstance(_paa_rows, list) and len(_paa_rows) > 0)
    _n_visible_paa_for_faq = (
        paa_shown_count
        if paa_shown_count > 0
        else (min(len(_paa_rows), MAX_PAA_QUESTIONS) if _has_serp_paa else 0)
    )
    _faq_pair_target = min(6, _n_visible_paa_for_faq) if _n_visible_paa_for_faq > 0 else 0
    _paa_faq_instruction = (
        "People Also Ask (PAA) signals are included below for this topic. Map those questions to explicit H2/H3 "
        "coverage where they fit the outline; address the highest-priority questions first. "
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

    _pk_checklist = (str((idea_serp_context or {}).get("primary_keyword") or "").strip() or _first_plain_keyword())

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
    if _pk_checklist:
        _pre_output_lines.append(
            f"- Primary keyword for this draft: include {_pk_checklist!r} naturally at least once in body text.\n"
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

    user_msg = (
        f"Write a complete SEO-optimised blog article for {_brand} on the following topic:\n\n"
        f"{topic.strip()}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
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
    if _pk_checklist:
        _outline_checklist_lines.append(
            f"- Primary keyword for this draft: plan natural inclusion of {_pk_checklist!r} in on-page copy.\n"
        )
    if _has_tier1_related_searches(idea_serp_context):
        _outline_checklist_lines.append(
            "- SERP tier 1–3 related searches: plan headings or body coverage for each position 1–3 query from the appendix.\n"
        )
    _outline_checklist = "".join(_outline_checklist_lines)

    user_outline_msg = (
        f"Plan a long-form SEO blog article for {_brand} on:\n\n"
        f"{topic.strip()}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
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
        f"{topic.strip()}{keyword_section}{_seo_gap_section}{_rag_reference_block}"
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

    def _emit(message: str, *, phase: str, state: str) -> None:
        if on_progress:
            on_progress({"message": message, "phase": phase, "state": state})

    secondary_urls_for_compliance = [n["url"] for n in secondary_normalized if (n.get("url") or "").strip()]
    primary_kw_for_compliance = (
        str((idea_serp_context or {}).get("primary_keyword") or "").strip() or None
    )
    require_faqpage_ld = bool(_is_faq or _has_serp_paa)
    _tier_queries = collect_tier_related_queries((idea_serp_context or {}).get("related_searches"), max_position=3)

    def _sanitize_body(raw_html: str) -> str:
        out = str(raw_html or "")
        if "<a " in out.lower():
            out = sanitize_article_internal_links(
                out, path_to_canonical=path_to_canonical, base_url=_base_url
            )
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
        gaps_local = _compliance_gaps(body_local)
        if gaps_local:
            messages_local.append({"role": "user", "content": build_compliance_retry_user_message(gaps_local)})
            _emit("Draft needs compliance fixes — automatic retry…", phase="content", state="waiting")
            try:
                res = _call_ai(
                    settings, provider, model, messages_local, timeout, json_schema=json_schema, stage="article_draft"
                )
            except AIProviderRequestError as exc:
                raise RuntimeError(str(exc)) from exc
            body_local = _sanitize_body(str(res.get("body") or ""))
            gaps_local = _compliance_gaps(body_local)
        if gaps_local and (
            length_only_article_compliance_gaps(gaps_local) or mixed_length_and_serp_compliance_gaps(gaps_local)
        ):
            messages_local.append({"role": "user", "content": build_compliance_retry_user_message(gaps_local)})
            _emit(
                "Draft still needs compliance fixes (body length and/or SERP related searches) — one more attempt…",
                phase="content",
                state="waiting",
            )
            try:
                res = _call_ai(
                    settings, provider, model, messages_local, timeout, json_schema=json_schema, stage="article_draft"
                )
            except AIProviderRequestError as exc:
                raise RuntimeError(str(exc)) from exc
            body_local = _sanitize_body(str(res.get("body") or ""))
            gaps_local = _compliance_gaps(body_local)
        if gaps_local:
            raise RuntimeError(
                "Article draft failed compliance after automatic retries: " + " | ".join(gaps_local)
            )
        _emit("Article content passed compliance checks…", phase="content", state="done")
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
        try:
            _emit("Generating article outline (phase 1)…", phase="content", state="waiting")
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
            logger.warning("Article phased outline failed: %s", exc)
            return None
        sections_raw = outline.get("sections")
        if not isinstance(sections_raw, list) or not (8 <= len(sections_raw) <= 14):
            logger.warning("Article phased outline had invalid sections count: %r", sections_raw)
            return None
        work_items: list[dict[str, str]] = [
            {"kind": "intro", "heading": "", "level": "", "beats": _intro_beats},
        ]
        for sec in sections_raw:
            if not isinstance(sec, dict):
                return None
            heading = str(sec.get("heading") or "").strip()
            level = str(sec.get("level") or "").strip().lower()
            beats = str(sec.get("beats") or "").strip()
            if level not in ("h2", "h3") or not heading or len(beats) < 10:
                logger.warning("Article phased outline section invalid: %r", sec)
                return None
            work_items.append({"kind": "section", "heading": heading, "level": level, "beats": beats})

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
        html_parts: list[str] = []
        n_batches = len(batches)
        for bi, batch in enumerate(batches):
            is_last = bi == n_batches - 1
            _emit(
                f"Writing article HTML — batch {bi + 1} of {n_batches}…",
                phase="content",
                state="waiting",
            )
            frag_lines: list[str] = [
                _shared_grounding,
                "\n\n=== Locked outline (Phase 1) ===\n",
                f"title: {outline_title}\n",
                f"sections_json: {outline_digest}\n",
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
                logger.warning("Article phased section batch %s failed: %s", bi + 1, exc)
                return None
            blocks = batch_out.get("html_blocks")
            if not isinstance(blocks, list) or len(blocks) != len(batch):
                logger.warning("Article phased batch %s html_blocks invalid: %r", bi + 1, blocks)
                return None
            for k, frag in enumerate(blocks):
                if not isinstance(frag, str) or not frag.strip():
                    logger.warning("Article phased batch %s fragment %s empty", bi + 1, k)
                    return None
            html_parts.extend(blocks)
        raw_body = "".join(html_parts)
        body_merged = _sanitize_body(raw_body)
        gaps_phased = _compliance_gaps(body_merged)
        if gaps_phased:
            logger.warning("Phased article assembly failed compliance; falling back to single-pass. Gaps: %s", gaps_phased)
            return None
        _emit("Phased article assembly passed compliance checks…", phase="content", state="done")
        meta = {
            "title": outline.get("title"),
            "seo_title": outline.get("seo_title"),
            "seo_description": outline.get("seo_description"),
        }
        return meta, body_merged

    use_phased = bool(settings.get("article_draft_phased", True))
    if use_phased:
        _emit("Using phased generation (outline + HTML batches)…", phase="content", state="start")
        phased_pair = _try_phased()
        if phased_pair is not None:
            result, body_out = phased_pair
        else:
            _emit(
                "Phased generation unavailable or did not pass checks — single-pass fallback…",
                phase="content",
                state="waiting",
            )
            result, body_out = _single_shot_with_retries()
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
