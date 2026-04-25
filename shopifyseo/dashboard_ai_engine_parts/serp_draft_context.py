"""Build capped SERP research appendix + retrieval boost terms for article drafts."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

# Total appendix budget (chars). Drop lowest-priority sections first when trimming.
DEFAULT_SERP_APPENDIX_MAX_CHARS = 7200
# PAA: cap count and per-row snippet so SERP data cannot dominate the prompt.
MAX_PAA_QUESTIONS = 18
MAX_PAA_SNIPPET_CHARS = 280
# AI overview: commodity radar, not full text to echo.
MAX_AIO_BULLETS = 14
MAX_AIO_SECTION_CHARS = 2200
# Related searches shown in appendix tiers.
MAX_RELATED_TIER_HIGH = 12
MAX_RELATED_TIER_LOW = 8
# Boost list for embedding query (separate from appendix).
MAX_BOOST_RELATED = 10
MAX_BOOST_PAA_STEMS = 5


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _corpus_from_topic_keywords(topic: str, keywords: list[str | dict] | None) -> set[str]:
    out: set[str] = set()
    t = _norm(topic)
    if t:
        out.add(t)
        if len(t) > 24:
            out.add(t[:120])
    if keywords:
        for raw in keywords:
            if isinstance(raw, dict):
                k = _norm(str(raw.get("keyword") or ""))
            else:
                k = _norm(str(raw))
            if k:
                out.add(k)
    return out


def _is_redundant_line(text: str, corpus: set[str]) -> bool:
    """Skip appendix anchors that duplicate topic / keyword lines already in user_msg."""
    n = _norm(text)
    if not n or len(n) < 3:
        return True
    if n in corpus:
        return True
    for c in corpus:
        if len(c) < 8:
            continue
        if n == c or (len(n) <= len(c) + 8 and (n in c or c in n)):
            return True
    return False


def _flatten_ai_overview_bullets(
    aio: dict[str, Any] | None,
    *,
    max_bullets: int = MAX_AIO_BULLETS,
    max_total_chars: int = MAX_AIO_SECTION_CHARS,
    include_reference_titles_only: bool = True,
) -> list[str]:
    """Flatten AI overview to short bullets; never emit reference URLs."""
    if not aio or not isinstance(aio, dict):
        return []
    bits: list[str] = []
    tbs = aio.get("text_blocks")
    if isinstance(tbs, list):
        for tb in tbs:
            if not isinstance(tb, dict):
                continue
            if tb.get("type") == "paragraph":
                sn = str(tb.get("snippet") or "").strip()
                if sn:
                    bits.append(sn)
            elif tb.get("type") == "list":
                lst = tb.get("list")
                if isinstance(lst, list):
                    for li in lst:
                        if not isinstance(li, dict):
                            continue
                        sn = str(li.get("snippet") or "").strip()
                        latex = li.get("snippet_latex")
                        if isinstance(latex, list):
                            sn = (sn + " " + " ".join(str(x) for x in latex if isinstance(x, str))).strip()
                        elif isinstance(latex, str) and latex.strip():
                            sn = (sn + " " + latex.strip()).strip()
                        if sn:
                            bits.append(sn)
    refs = aio.get("references")
    if include_reference_titles_only and isinstance(refs, list):
        for r in refs:
            if not isinstance(r, dict):
                continue
            t = str(r.get("title") or "").strip()
            sn = str(r.get("snippet") or "").strip()
            # Deliberately omit link/url — third-party sources, not for reproduction.
            chunk = " ".join(x for x in (t, sn) if x)
            if chunk:
                bits.append(f"(ref) {chunk}")
    out: list[str] = []
    used = 0
    for b in bits:
        if len(out) >= max_bullets:
            break
        line = b.replace("\n", " ").strip()
        if len(line) > 320:
            line = line[:317] + "…"
        if used + len(line) + 2 > max_total_chars:
            break
        out.append(f"- {line}")
        used += len(line) + 2
    return out


def _related_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    pos = item.get("position")
    try:
        pi = int(pos) if pos is not None else 999
    except (TypeError, ValueError):
        pi = 999
    q = str(item.get("query") or "").strip()
    return (pi, q.lower())


def _subsection_shape_hint(query: str) -> str:
    q = query.strip().lower()
    if not q:
        return ""
    if " vs " in f" {q} " or q.endswith(" vs") or "difference between" in q or "differences between" in q:
        return "comparison table or side-by-side"
    if q.startswith("how to ") or " how to " in q or q.startswith("how do "):
        return "numbered checklist or short procedure"
    if q.startswith("is ") or " safe" in q or "legal" in q or "allowed" in q:
        return "scope, caveats, and criteria"
    if "best " in q or q.startswith("top "):
        return "criteria-led picks or shortlist"
    return ""


def _paa_question_stem(q: str, max_words: int = 6) -> str:
    words = re.findall(r"[A-Za-z0-9']+", (q or "").lower())
    if not words:
        return ""
    return " ".join(words[:max_words])


STYLE_EXAMPLES_BLOCK = """=== SERP: Style examples (do not copy — adapt to your topic) ===
These illustrate diverse opening pivots only; they are not facts about this topic.
- Comparison shape: after establishing the main topic, narrow to the decision readers still face and contrast the leading options on criteria that matter for purchase or use.
- How-to shape: state the outcome, then step down into prerequisites, sequence, and common mistakes — each step scannable.
- Binary / safety shape: define scope (who/when/where), then deliver a direct answer with explicit caveats and what would change the conclusion.
"""


def build_serp_appendix_and_retrieval_boost(
    *,
    topic: str,
    keywords: list[str | dict] | None,
    idea_serp_context: dict[str, Any],
    max_appendix_chars: int = DEFAULT_SERP_APPENDIX_MAX_CHARS,
) -> tuple[str, list[str], int]:
    """Return (serp_appendix, retrieval_boost_terms, paa_shown_count) from normalized idea SERP fields.

    ``paa_shown_count`` is the number of PAA questions actually written into the appendix
    (same cap as ``MAX_PAA_QUESTIONS``), for FAQ JSON-LD pair targets in the draft prompt.
    """
    corpus = _corpus_from_topic_keywords(topic, keywords)

    suggested_title = str(idea_serp_context.get("suggested_title") or "").strip()
    brief = str(idea_serp_context.get("brief") or "").strip()
    primary_keyword = str(idea_serp_context.get("primary_keyword") or "").strip()
    gap_reason = str(idea_serp_context.get("gap_reason") or "").strip()
    dominant_serp_features = str(idea_serp_context.get("dominant_serp_features") or "").strip()
    content_format_hints = str(idea_serp_context.get("content_format_hints") or "").strip()

    audience_questions: list[dict[str, str]] = idea_serp_context.get("audience_questions") or []
    if not isinstance(audience_questions, list):
        audience_questions = []

    top_pages: list[dict[str, str]] = idea_serp_context.get("top_ranking_pages") or []
    if not isinstance(top_pages, list):
        top_pages = []

    related: list[dict[str, Any]] = idea_serp_context.get("related_searches") or []
    if not isinstance(related, list):
        related = []
    related_sorted = sorted(
        [x for x in related if isinstance(x, dict) and str(x.get("query") or "").strip()],
        key=_related_sort_key,
    )

    aio = idea_serp_context.get("ai_overview")
    if aio is not None and not isinstance(aio, dict):
        aio = None

    sections: list[str] = []

    # --- (1) Anchors: gap, brief, signals (deduped) ---
    anchor_lines: list[str] = []
    if primary_keyword and not _is_redundant_line(primary_keyword, corpus):
        anchor_lines.append(f"- Primary keyword (idea): {primary_keyword}")
    if brief and not _is_redundant_line(brief, corpus):
        b = brief if len(brief) <= 900 else brief[:897] + "…"
        anchor_lines.append(f"- Brief: {b}")
    if gap_reason and not _is_redundant_line(gap_reason, corpus):
        g = gap_reason if len(gap_reason) <= 700 else gap_reason[:697] + "…"
        anchor_lines.append(f"- Gap / angle: {g}")
    if suggested_title and _norm(suggested_title) != _norm(topic) and not _is_redundant_line(suggested_title, corpus):
        anchor_lines.append(f"- Working title (idea): {suggested_title}")

    serp_feat_bits: list[str] = []
    if dominant_serp_features and not _is_redundant_line(dominant_serp_features, corpus):
        d = dominant_serp_features if len(dominant_serp_features) <= 400 else dominant_serp_features[:397] + "…"
        serp_feat_bits.append(f"Dominant SERP features: {d}")
    if content_format_hints and not _is_redundant_line(content_format_hints, corpus):
        c = content_format_hints if len(content_format_hints) <= 400 else content_format_hints[:397] + "…"
        serp_feat_bits.append(f"Format hints: {c}")
    if anchor_lines or serp_feat_bits:
        block = "=== SERP: Idea anchors ===\n"
        block += "\n".join(anchor_lines)
        if serp_feat_bits:
            block += "\n" + "\n".join(f"- {s}" for s in serp_feat_bits)
        sections.append(block)

    # --- (2) PAA ---
    paa_lines: list[str] = []
    paa_shown_count = 0
    total_paa = len(audience_questions)
    for i, row in enumerate(audience_questions[:MAX_PAA_QUESTIONS], start=1):
        if not isinstance(row, dict):
            continue
        q = str(row.get("question") or "").strip()
        if not q:
            continue
        sn = str(row.get("snippet") or "").strip()
        if len(sn) > MAX_PAA_SNIPPET_CHARS:
            sn = sn[: MAX_PAA_SNIPPET_CHARS - 1] + "…"
        hint = f" (snippet hint — non-authoritative; paraphrase, do not cite as fact)" if sn else ""
        line = f"{i}. {q}{hint}"
        if sn:
            line += f"\n   Snippet: {sn}"
        paa_lines.append(line)
        paa_shown_count += 1
    if paa_lines:
        extra = total_paa - len(paa_lines)
        tail = f"\n(+ {extra} further PAA-style questions in cluster — cover as many as fit naturally.)" if extra > 0 else ""
        sections.append("=== SERP: People Also Ask (cover in H2/H3 where natural) ===\n" + "\n".join(paa_lines) + tail)

    # --- (3) Related searches (PASF tiers) ---
    tier_high: list[dict[str, Any]] = []
    tier_low: list[dict[str, Any]] = []
    for item in related_sorted:
        pos = item.get("position")
        try:
            pi = int(pos)
        except (TypeError, ValueError):
            pi = 99
        if pi <= 3:
            tier_high.append(item)
        else:
            tier_low.append(item)

    rel_blocks: list[str] = []
    if tier_high:
        lines_h: list[str] = []
        for item in tier_high[:MAX_RELATED_TIER_HIGH]:
            q = str(item.get("query") or "").strip()
            pos = item.get("position", "")
            hint = _subsection_shape_hint(q)
            hint_s = f" — suggested shape: {hint}" if hint else ""
            lines_h.append(f"- (position {pos}) {q}{hint_s}")
        rel_blocks.append(
            "=== SERP: Related searches — tiers 1–3 (strong refinements / close information gaps) ===\n"
            "Treat each as a candidate for a dedicated H2 or H3 whose title closely matches the query when it reads naturally.\n"
            "Prefer comparison tables, pros/cons, definitions with criteria, or short procedures when the query implies them.\n"
            "Each query with position 1, 2, or 3 above must have a matching H2 or H3 in your article (light paraphrase allowed for grammar); "
            "do not skip all tier-1 refinements without a heading that reflects them.\n"
            + "\n".join(lines_h)
        )
    if tier_low:
        lines_l: list[str] = []
        for item in tier_low[:MAX_RELATED_TIER_LOW]:
            q = str(item.get("query") or "").strip()
            pos = item.get("position", "")
            lines_l.append(f"- (position {pos}) {q}")
        rel_blocks.append(
            "=== SERP: Related searches — position 4+ (supporting long-tail) ===\n"
            "Weave as supporting phrases, glossary notes, or shorter FAQ-style answers — avoid keyword stuffing.\n"
            + "\n".join(lines_l)
        )
    if rel_blocks:
        sections.append("\n\n".join(rel_blocks))
        if tier_high:
            sections.append(
                "\n\n=== SERP: Learning-path continuity (principle) ===\n"
                "For top-tier related searches, each matching section should read as the next step in the reader's learning path, "
                "not a disconnected FAQ bolt-on."
            )
            sections.append("\n\n" + STYLE_EXAMPLES_BLOCK.strip())

    # --- (4) Top titles (no URLs) ---
    title_lines: list[str] = []
    for pg in top_pages[:14]:
        if not isinstance(pg, dict):
            continue
        title = str(pg.get("title") or "").strip()
        if title:
            title_lines.append(f"- {title}")
    if title_lines:
        sections.append(
            "=== SERP: Top ranking titles (differentiation set — no competitor URLs) ===\n"
            "Use only to infer common angles; do not copy phrasing. Provide distinct store-specific value.\n"
            + "\n".join(title_lines)
        )

    # --- (5) AI overview ---
    aio_bullets = _flatten_ai_overview_bullets(aio)
    if aio_bullets:
        sections.append(
            "=== SERP: AI overview (third-party / non-authoritative — synthesize, do not mirror) ===\n"
            "Treat as a commodity-coverage radar only. Do not reproduce verbatim; do not treat as fact.\n"
            + "\n".join(aio_bullets)
        )

    appendix = "\n\n".join(s for s in sections if s.strip()).strip()

    # Pack to budget: drop from end (lowest priority first: AIO → titles → low-tier related → style → PAA tail)
    def _trim_to_budget(text: str, budget: int) -> str:
        if len(text) <= budget:
            return text
        # Simple truncation with a marker (prefer dropping whole sections — approximate by slicing at paragraph breaks)
        cut = text[: budget - 80].rsplit("\n\n", 1)[0]
        return cut + "\n\n[… SERP appendix truncated for length …]"

    appendix = _trim_to_budget(appendix, max_appendix_chars)

    # Retrieval boost terms (deduped, stable order)
    boost: list[str] = []
    seen_lower: set[str] = set()

    def _add_boost(term: str) -> None:
        t = term.strip()
        if len(t) < 2 or len(t) > 200:
            return
        k = t.lower()
        if k in seen_lower:
            return
        seen_lower.add(k)
        boost.append(t)

    if primary_keyword:
        _add_boost(primary_keyword)
    for item in related_sorted:
        if len(boost) >= 1 + MAX_BOOST_RELATED:
            break
        q = str(item.get("query") or "").strip()
        if q:
            _add_boost(q)
    for row in audience_questions:
        if len(boost) >= 1 + MAX_BOOST_RELATED + MAX_BOOST_PAA_STEMS:
            break
        if not isinstance(row, dict):
            continue
        stem = _paa_question_stem(str(row.get("question") or ""))
        if stem:
            _add_boost(stem)

    return appendix, boost, paa_shown_count


def parse_idea_serp_row_from_db(
    row: tuple[Any, ...] | sqlite3.Row | None,
    *,
    column_names: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    """Map a wide article_ideas SELECT row into a dict for ``build_serp_appendix_and_retrieval_boost``."""
    if row is None:
        return None

    def _get(name: str) -> Any:
        if hasattr(row, "keys") and name in row.keys():  # type: ignore[operator]
            return row[name]  # type: ignore[index]
        if column_names:
            try:
                idx = column_names.index(name)
                return row[idx]
            except (ValueError, IndexError):
                return None
        return None

    from shopifyseo.dashboard_article_ideas import (
        normalize_audience_questions_json,
        normalize_paa_expansion_json,
        normalize_related_searches_json,
        normalize_top_ranking_pages_json,
        parse_ai_overview_json,
    )

    def _loads_json(val: Any) -> Any:
        if val is None or val == "":
            return None
        if isinstance(val, (list, dict)):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    sup_raw = _get("supporting_keywords")
    sup_list: list[str] = []
    parsed_sup = _loads_json(sup_raw)
    if isinstance(parsed_sup, list):
        sup_list = [str(x).strip() for x in parsed_sup if str(x).strip()]

    return {
        "suggested_title": str(_get("suggested_title") or "").strip(),
        "brief": str(_get("brief") or "").strip(),
        "primary_keyword": str(_get("primary_keyword") or "").strip(),
        "supporting_keywords": sup_list,
        "gap_reason": str(_get("gap_reason") or "").strip(),
        "dominant_serp_features": str(_get("dominant_serp_features") or "").strip(),
        "content_format_hints": str(_get("content_format_hints") or "").strip(),
        "audience_questions": normalize_audience_questions_json(_loads_json(_get("audience_questions_json"))),
        "top_ranking_pages": normalize_top_ranking_pages_json(_loads_json(_get("top_ranking_pages_json"))),
        "related_searches": normalize_related_searches_json(_loads_json(_get("related_searches_json"))),
        "ai_overview": parse_ai_overview_json(_get("ai_overview_json")),
        "paa_expansion": normalize_paa_expansion_json(_loads_json(_get("paa_expansion_json"))),
    }
