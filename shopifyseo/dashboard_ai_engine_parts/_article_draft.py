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
    if idea_serp_context is not None:
        from .serp_draft_context import build_serp_appendix_and_retrieval_boost

        serp_appendix, retrieval_boost_terms = build_serp_appendix_and_retrieval_boost(
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
    _paa_faq_instruction = (
        "People Also Ask (PAA) signals are included below for this topic. Map those questions to explicit H2/H3 "
        "coverage where they fit the outline; if the list is long, address at least the highest-priority questions "
        "first, then cover additional ones where they add reader value. "
        "At the end of the body, add FAQPage JSON-LD (Question + acceptedAnswer) aligned to the clearest reader "
        "questions you answered in the article (at least 6 pairs when the SERP appendix lists that many distinct questions).\n"
        if _has_serp_paa and not _is_faq
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
        _collection_link_block = (
            "\n\napproved_internal_link_targets (JSON array). "
            "MUST include 2–4 contextual internal links in the body where they help the reader. "
            "When this list is long, prefer destinations that also appear in the \"Reference content from your store\" "
            "section above (same product, collection, or post) when they fit the reader's context — avoid unrelated "
            "catalog items that only appear deeper in the JSON list. "
            "Every storefront <a href> MUST use the `url` value from one of these objects "
            "character-for-character (copy the full string — no edits, no other hosts, no invented paths). "
            "Each <a> MUST also include a descriptive title attribute for accessibility and SEO: "
            f"for type collection use 'Shop {{title}} — {_brand}', for product use '{{title}} — {_brand}', "
            f"for page use '{{title}} — {_brand}', for blog_article use 'Read {{title}} — {_brand}'. "
            "Use natural anchor text describing the destination — never use the raw handle as link text.\n"
            f"{_allowlist_json}\n"
        )
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

    system_msg = (
        f"You are an expert SEO content writer for {_brand}. "
        "Write high-quality, editorial blog content that ranks well on Google. "
        f"{spelling_variant(_market_code)} "
        "Do not fabricate statistics, specific study results, or invented data — if you need to reference evidence, "
        "use well-known industry patterns rather than invented figures. "
        "Write at a Grade 8–10 reading level. Be helpful, specific, and commercially relevant. "
        "When choosing internal links, prefer store destinations that clearly match the article topic and any "
        "reference list provided in the user message over unrelated catalog URLs. "
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
        "- body: Full article HTML. CRITICAL: target 2,000+ WORDS of actual content (at least 14,000 characters of HTML). "
        "Do not stop before 2,000 words — articles under 1,800 words will be rejected. Write thorough, detailed sections. "
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
                    "description": "Full article HTML, minimum 14000 characters (approx 2000+ words). Do not stop early.",
                    "minLength": 14000,
                },
            },
            "required": ["title", "seo_title", "seo_description", "body"],
            "additionalProperties": False,
        },
    }

    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]

    def _emit(message: str, *, phase: str, state: str) -> None:
        if on_progress:
            on_progress({"message": message, "phase": phase, "state": state})

    _emit("Preparing article prompt and keyword context…", phase="content", state="start")
    _emit("Sending request to AI — writing full article (often 1–3 minutes)…", phase="content", state="waiting")

    try:
        result = _call_ai(settings, provider, model, messages, timeout, json_schema=json_schema, stage="article_draft")
    except AIProviderRequestError as exc:
        raise RuntimeError(str(exc)) from exc

    _emit("Article content received — validating JSON fields…", phase="content", state="done")

    body_out = str(result.get("body") or "")
    if "<a " in body_out.lower():
        body_out = sanitize_article_internal_links(
            body_out, path_to_canonical=path_to_canonical, base_url=_base_url
        )

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
