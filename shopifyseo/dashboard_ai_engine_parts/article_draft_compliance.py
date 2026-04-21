"""Post-generation checks for article drafts (machine-validatable SEO rules)."""

from __future__ import annotations

import re
from urllib.parse import urlparse

_HREF_RE = re.compile(r"""href\s*=\s*(["'])(.*?)\1""", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")
_TAG_RE = re.compile(r"<[^>]+>")

MIN_ARTICLE_BODY_HTML_CHARS = 14000
PRIMARY_KEYWORD_EXACT_MAX_LEN = 80
PRIMARY_KEYWORD_SUBSTRING_LEN = 60


def strip_html_for_compliance_search(html: str) -> str:
    """Lowercase plain text for substring checks (scripts removed)."""
    s = _SCRIPT_RE.sub(" ", html or "")
    s = _TAG_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
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


def validate_article_draft_compliance(
    *,
    body_html: str,
    require_faqpage_ld: bool,
    secondary_urls: list[str],
    primary_keyword_for_body: str | None,
    path_to_canonical: dict[str, str],
) -> list[str]:
    """Return a list of human-readable gaps (empty if compliant)."""
    gaps: list[str] = []
    if require_faqpage_ld and not faqpage_ld_present(body_html):
        gaps.append("Body must include FAQPage JSON-LD in a script type application/ld+json block (PAA signals were provided).")
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
    if len(body_html or "") < MIN_ARTICLE_BODY_HTML_CHARS:
        gaps.append(
            f"Body HTML must be at least {MIN_ARTICLE_BODY_HTML_CHARS} characters (currently {len(body_html or '')})."
        )
    return gaps


def build_compliance_retry_user_message(gaps: list[str]) -> str:
    lines = "\n".join(f"- {g}" for g in gaps)
    return (
        "Your previous JSON response failed automated draft checks. Fix ALL of the following, then return the "
        "full JSON object again with the same four fields (title, seo_title, seo_description, body) and the same schema.\n"
        f"{lines}\n"
        "Keep editorial quality, tone, and factual discipline unchanged aside from these fixes."
    )
