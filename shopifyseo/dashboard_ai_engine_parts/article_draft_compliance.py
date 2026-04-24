"""Post-generation checks for article drafts (machine-validatable SEO rules)."""

from __future__ import annotations

import html as html_module
import json
import re
from urllib.parse import urlparse

_HREF_RE = re.compile(r"""href\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")
_SCRIPT_OPEN_RE = re.compile(r"(?is)<script([^>]*)>")
_TAG_RE = re.compile(r"<[^>]+>")
_H2_H4_BLOCK_RE = re.compile(r"(?is)<h([234])\b[^>]*>(.*?)</h\1\s*>")

MIN_ARTICLE_BODY_HTML_CHARS = 14000
# Retry prompts ask the model to aim past the minimum so minor undershoots still pass after edits.
COMPLIANCE_BODY_LENGTH_RETRY_MARGIN = 600
PRIMARY_KEYWORD_EXACT_MAX_LEN = 80
PRIMARY_KEYWORD_SUBSTRING_LEN = 60

_LENGTH_GAP_PREFIX = "Body HTML must be at least "


def strip_html_for_compliance_search(html: str) -> str:
    """Lowercase plain text for substring checks (scripts removed)."""
    s = _SCRIPT_RE.sub(" ", html or "")
    s = _TAG_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    for ch in ("\u2019", "\u2018", "\u2032", "\u00b4"):
        s = s.replace(ch, "'")
    s = s.replace("`", "'")
    return s


def _path_key(url: str) -> str:
    p = (urlparse(url).path or "").strip()
    if not p or p == "/":
        return "/"
    return p.rstrip("/") or "/"


def collect_hrefs(html: str) -> list[str]:
    out: list[str] = []
    for m in _HREF_RE.finditer(html or ""):
        href = (m.group(2) or "").strip()
        if href and not href.lower().startswith(("#", "mailto:", "tel:", "javascript:")):
            out.append(href)
    return out


def secondary_target_url_in_body(
    body_html: str,
    target_url: str,
    *,
    path_to_canonical: dict[str, str],
) -> bool:
    """True if body contains an <a href> matching *target_url* by full string or storefront path."""
    tu = (target_url or "").strip()
    if not tu:
        return True
    want_paths: set[str] = set()
    pk = _path_key(tu)
    want_paths.add(pk)
    canon = path_to_canonical.get(pk)
    if canon:
        want_paths.add(_path_key(canon))
    for href in collect_hrefs(body_html):
        hl = href.strip()
        if not hl:
            continue
        if hl == tu:
            return True
        hp = _path_key(hl)
        if hp in want_paths:
            return True
    return False


def primary_keyword_in_body(
    body_html: str,
    primary_keyword: str,
) -> bool:
    """Exact phrase (<=80 chars) or first 60-char substring for longer phrases."""
    kw = (primary_keyword or "").strip()
    if not kw:
        return True
    blob = strip_html_for_compliance_search(body_html)
    if len(kw) <= PRIMARY_KEYWORD_EXACT_MAX_LEN:
        return kw.lower() in blob
    sub = kw[:PRIMARY_KEYWORD_SUBSTRING_LEN].strip().lower()
    return bool(sub) and sub in blob


def faqpage_ld_present(body_html: str) -> bool:
    bl = body_html or ""
    if "FAQPage" not in bl:
        return False
    if "application/ld+json" not in bl.lower():
        return False
    return True


def _is_ld_json_script_opening(attrs: str) -> bool:
    a = (attrs or "").lower()
    return "application/ld+json" in a and "type" in a


def _iter_ld_json_script_inner_html(body_html: str) -> list[str]:
    """Raw inner HTML of each ``<script type=application/ld+json>`` (best-effort split)."""
    html = body_html or ""
    out: list[str] = []
    pos = 0
    while True:
        m = _SCRIPT_OPEN_RE.search(html, pos)
        if not m:
            break
        attrs = m.group(1) or ""
        start = m.end()
        close = html.lower().find("</script>", start)
        if close == -1:
            break
        if _is_ld_json_script_opening(attrs):
            out.append(html[start:close])
        pos = close + len("</script>")
    return out


def _types_of(node: dict) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw}
    if isinstance(raw, list):
        return {str(x) for x in raw if isinstance(x, str)}
    return set()


def _collect_faqpage_question_names(data: object) -> list[str]:
    """Gather Question ``name`` strings from every FAQPage object in a JSON-LD tree."""
    names: list[str] = []

    def visit(obj: object) -> None:
        if isinstance(obj, dict):
            if "FAQPage" in _types_of(obj):
                me = obj.get("mainEntity") or obj.get("mainentity")
                if isinstance(me, list):
                    ents = me
                elif isinstance(me, dict):
                    ents = [me]
                else:
                    ents = []
                for ent in ents:
                    if not isinstance(ent, dict):
                        continue
                    if "Question" not in _types_of(ent):
                        continue
                    nm = ent.get("name")
                    if isinstance(nm, str) and nm.strip():
                        names.append(nm.strip())
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list):
            for it in obj:
                visit(it)

    visit(data)
    return names


def extract_faqpage_question_names_from_body(body_html: str) -> list[str]:
    """Parse FAQPage ``Question`` names from all JSON-LD script blocks in *body_html*."""
    collected: list[str] = []
    for inner in _iter_ld_json_script_inner_html(body_html):
        raw = (inner or "").strip()
        if "FAQPage" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        collected.extend(_collect_faqpage_question_names(data))
    return collected


def _normalize_faq_match_key(text: str) -> tuple[str, str]:
    """Return (strict lower collapsed, looser alnum-only) keys for substring checks."""
    t = html_module.unescape(text or "").strip()
    for ch in ("\u2019", "\u2018", "\u2032", "\u00b4"):
        t = t.replace(ch, "'")
    t = t.replace("`", "'")
    t = t.strip().lower()
    t = re.sub(r"\s+", " ", t)
    loose = re.sub(r"[^\w\s]", "", t)
    loose = re.sub(r"\s+", " ", loose).strip()
    return t, loose


def _faq_question_text_visible(question: str, visible_blob: str) -> bool:
    """True if the question (or a long-prefix / looser form) appears in *visible_blob*."""
    strict, loose = _normalize_faq_match_key(question)
    if not strict:
        return True
    if strict in visible_blob:
        return True
    if len(loose) >= 12 and loose in visible_blob:
        return True
    if len(strict) > 96:
        return strict[:96] in visible_blob
    return False


def faqpage_visible_alignment_gaps(body_html: str) -> list[str]:
    """When FAQPage JSON-LD is present, ensure each schema question appears in visible body text."""
    gaps: list[str] = []
    if not faqpage_ld_present(body_html):
        return gaps

    questions = extract_faqpage_question_names_from_body(body_html)
    if not questions:
        gaps.append(
            "FAQPage JSON-LD is present but has no usable Question entries with a `name` field (or JSON could not "
            "be parsed). Add at least one Question whose `name` matches an FAQ heading or sentence on the page, "
            "or fix invalid JSON in the script tag."
        )
        return gaps

    visible = strip_html_for_compliance_search(body_html)
    for q in questions:
        if not _faq_question_text_visible(q, visible):
            preview = q if len(q) <= 88 else q[:85] + "…"
            gaps.append(
                f"FAQPage schema lists a question that does not appear in the visible article text: {preview!r}. "
                "Repeat the same question text in an on-page FAQ heading or paragraph (light punctuation differences "
                "are OK)."
            )
    return gaps


def extract_h2_h4_heading_plain_texts(body_html: str) -> list[str]:
    """Plain-text heading lines from ``<h2>``–``<h4>`` tags (inner markup removed)."""
    out: list[str] = []
    for m in _H2_H4_BLOCK_RE.finditer(body_html or ""):
        inner = m.group(2) or ""
        inner = _TAG_RE.sub(" ", inner)
        inner = html_module.unescape(inner)
        inner = re.sub(r"\s+", " ", inner).strip()
        if inner:
            out.append(inner)
    return out


def _headings_match_blobs(body_html: str) -> tuple[str, str]:
    """``(strict_joined, loose_joined)`` for all H2–H4 text, lowercased for substring checks."""
    strict_parts: list[str] = []
    loose_parts: list[str] = []
    for t in extract_h2_h4_heading_plain_texts(body_html):
        s, lo = _normalize_faq_match_key(t)
        if s:
            strict_parts.append(s)
        if lo:
            loose_parts.append(lo)
    strict_blob = " ".join(strict_parts)
    loose_blob = " ".join(loose_parts)
    loose_blob = re.sub(r"\s+", " ", loose_blob).strip()
    return strict_blob, loose_blob


def collect_tier_related_queries(related_searches: object, *, max_position: int = 3) -> list[str]:
    """Return related-search ``query`` strings with ``position`` <= *max_position*, ordered by position."""
    if not isinstance(related_searches, list):
        return []
    ranked: list[tuple[int, str]] = []
    for x in related_searches:
        if not isinstance(x, dict):
            continue
        q = str(x.get("query") or "").strip()
        if not q:
            continue
        try:
            pos = int(x.get("position", 99))
        except (TypeError, ValueError):
            pos = 99
        if isinstance(pos, bool):
            pos = 99
        if pos <= max_position:
            ranked.append((pos, q))
    ranked.sort(key=lambda t: t[0])
    seen: set[str] = set()
    ordered: list[str] = []
    for _pos, q in ranked:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(q)
    return ordered


def _tier_query_matches_heading_blob(query: str, strict_blob: str, loose_headings_blob: str) -> bool:
    strict_q, loose_q = _normalize_faq_match_key(query)
    if not strict_q:
        return True
    if strict_q in strict_blob:
        return True
    if len(loose_q) >= 10 and loose_q in loose_headings_blob:
        return True
    if len(strict_q) > 64:
        return strict_q[:64] in strict_blob
    return False


def _tier_related_query_matches(body_html: str, query: str) -> bool:
    """True if *query* matches an H2–H4 heading or visible body text (scripts stripped)."""
    strict_h, loose_h = _headings_match_blobs(body_html)
    if _tier_query_matches_heading_blob(query, strict_h, loose_h):
        return True
    visible = strip_html_for_compliance_search(body_html)
    return _faq_question_text_visible(query, visible)


def tier1_related_search_heading_gaps(body_html: str, queries: list[str]) -> list[str]:
    """Each SERP related query (positions 1–3) must appear in H2–H4 or visible body text (light paraphrase OK)."""
    gaps: list[str] = []
    if not queries:
        return gaps
    for q in queries:
        if _tier_related_query_matches(body_html, q):
            continue
        preview = q if len(q) <= 72 else q[:69] + "…"
        gaps.append(
            "SERP position 1–3 related search must appear in an on-page <h2>, <h3>, <h4> heading or in visible "
            f"body text (light paraphrase OK): {preview!r}."
        )
    return gaps


def validate_article_draft_compliance(
    *,
    body_html: str,
    require_faqpage_ld: bool,
    secondary_urls: list[str],
    primary_keyword_for_body: str | None,
    path_to_canonical: dict[str, str],
    tier1_related_queries: list[str] | None = None,
) -> list[str]:
    """Return a list of human-readable gaps (empty if compliant)."""
    gaps: list[str] = []
    if require_faqpage_ld and not faqpage_ld_present(body_html):
        gaps.append("Body must include FAQPage JSON-LD in a script type application/ld+json block (PAA signals were provided).")
    if faqpage_ld_present(body_html):
        gaps.extend(faqpage_visible_alignment_gaps(body_html))
    for url in secondary_urls:
        u = (url or "").strip()
        if not u:
            continue
        if not secondary_target_url_in_body(body_html, u, path_to_canonical=path_to_canonical):
            gaps.append(f"Missing required secondary internal link (href) to: {u}")
    pk = (primary_keyword_for_body or "").strip()
    if pk and not primary_keyword_in_body(body_html, pk):
        if len(pk) <= PRIMARY_KEYWORD_EXACT_MAX_LEN:
            gaps.append(f"Body must include the primary keyword phrase naturally at least once: {pk!r}")
        else:
            gaps.append(
                f"Body must include a natural substring of the primary keyword (first {PRIMARY_KEYWORD_SUBSTRING_LEN} chars): {pk[:PRIMARY_KEYWORD_SUBSTRING_LEN]!r}…"
            )
    tq = tier1_related_queries or []
    if tq:
        gaps.extend(tier1_related_search_heading_gaps(body_html, tq))
    n = len(body_html or "")
    if n < MIN_ARTICLE_BODY_HTML_CHARS:
        deficit = MIN_ARTICLE_BODY_HTML_CHARS - n
        aim = MIN_ARTICLE_BODY_HTML_CHARS + COMPLIANCE_BODY_LENGTH_RETRY_MARGIN
        gaps.append(
            f"{_LENGTH_GAP_PREFIX}{MIN_ARTICLE_BODY_HTML_CHARS} characters (currently {n}). "
            f"The body is {deficit} characters short. Expand with substantive HTML (new H2/H3 sections, "
            f"paragraphs, lists, tables, or FAQ entries) until the raw `body` string is at least {aim} characters "
            "including every tag, quote, and whitespace — that length is measured exactly as Python `len(body)` "
            "on the full HTML string you return."
        )
    return gaps


def length_only_article_compliance_gaps(gaps: list[str]) -> bool:
    """True when every gap is the minimum-length rule (eligible for an extra model retry)."""
    if not gaps:
        return False
    return all(g.startswith(_LENGTH_GAP_PREFIX) for g in gaps)


def mixed_length_and_serp_compliance_gaps(gaps: list[str]) -> bool:
    """True when failures include both minimum length and SERP related-search coverage."""
    if not gaps:
        return False
    has_len = any(g.startswith(_LENGTH_GAP_PREFIX) for g in gaps)
    has_serp = any("SERP position 1–3" in g for g in gaps)
    return has_len and has_serp


def build_compliance_retry_user_message(gaps: list[str]) -> str:
    lines = "\n".join(f"- {g}" for g in gaps)
    extra = ""
    if length_only_article_compliance_gaps(gaps) and not any("SERP position 1–3" in g for g in gaps):
        extra = (
            "\nLength-specific rule: only the `body` field needs to grow. Do not shorten title, seo_title, or "
            "seo_description to compensate. Prefer adding one or two full sections with real detail over padding "
            "with empty markup.\n"
        )
    elif mixed_length_and_serp_compliance_gaps(gaps):
        extra = (
            "\nMixed fixes: grow `body` to the required HTML length AND place each SERP related-search phrase from "
            "the failures into a heading (h2–h4) or a normal paragraph so automated checks pass.\n"
        )
    return (
        "Your previous JSON response failed automated draft checks. Fix ALL of the following, then return the "
        "full JSON object again with the same four fields (title, seo_title, seo_description, body) and the same schema.\n"
        f"{lines}\n"
        f"{extra}"
        "Keep editorial quality, tone, and factual discipline unchanged aside from these fixes."
    )
