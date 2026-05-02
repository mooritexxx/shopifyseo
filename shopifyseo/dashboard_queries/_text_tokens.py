"""Tokenization and related-row scoring helpers.

Pure-text utilities used by retrieval / RAG and the "Related items" UI.
No DB writes; queries are read-only against products / collections / pages.
"""
from __future__ import annotations

import re
import sqlite3
import json
from typing import Any


# Token overlap for article/page "Related items" (avoids alphabetical first-N products).
_RELATED_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "was", "one", "our", "out",
    "day", "get", "has", "him", "his", "how", "its", "may", "new", "now", "old", "see", "two",
    "who", "way", "use", "many", "some", "time", "very", "when", "come", "here", "just", "like",
    "long", "make", "more", "only", "over", "such", "take", "than", "them", "well", "will",
    "this", "that", "with", "from", "your", "have", "each", "about", "into", "also", "what",
    "their", "would", "there", "these", "been", "could", "other", "than", "then", "them",
})


def _strip_html_for_tokens(html: str | None, max_chars: int = 12000) -> str:
    if not html:
        return ""
    text = re.sub(r"<script[^>]*>[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[^>]*>[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def strip_html_for_retrieval(html: str | None, max_chars: int = 12000) -> str:
    """Strip HTML to plain text for retrieval / token overlap (public wrapper)."""
    return _strip_html_for_tokens(html, max_chars)


def _tokens_from_blob(blob: str, *, min_len: int = 3) -> frozenset[str]:
    if not blob:
        return frozenset()
    words = re.findall(r"[a-z0-9]+", blob.lower())
    return frozenset(w for w in words if len(w) >= min_len and w not in _RELATED_STOPWORDS)


def _tags_json_phrase_blob(tags_json: str | None) -> str:
    if not (tags_json or "").strip():
        return ""
    try:
        data = json.loads(tags_json)
        if isinstance(data, list):
            return " ".join(str(x) for x in data if x)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return ""


def tags_json_phrase_for_retrieval(tags_json: str | None) -> str:
    """Join JSON list tags into a phrase for retrieval overlap (public wrapper)."""
    return _tags_json_phrase_blob(tags_json)


def _content_tokens_for_blog_article(article: dict[str, Any]) -> frozenset[str]:
    parts = [
        article.get("title") or "",
        article.get("seo_title") or "",
        article.get("seo_description") or "",
        article.get("summary") or "",
        _strip_html_for_tokens(article.get("body") or ""),
        _tags_json_phrase_blob(article.get("tags_json")),
    ]
    return _tokens_from_blob(" ".join(parts))


def _content_tokens_for_page(page: dict[str, Any]) -> frozenset[str]:
    parts = [
        page.get("title") or "",
        page.get("seo_title") or "",
        page.get("seo_description") or "",
        _strip_html_for_tokens(page.get("body") or ""),
    ]
    return _tokens_from_blob(" ".join(parts))


def retrieval_tokens_from_text(blob: str, *, min_len: int = 3) -> frozenset[str]:
    """Public token set for retrieval / RAG overlap (same rules as related-items UI)."""
    return _tokens_from_blob(blob, min_len=min_len)


def collection_row_token_overlap(tokens: frozenset[str], row: dict[str, Any]) -> int:
    d = row
    hay = " ".join(
        [
            str(d.get("title") or ""),
            str(d.get("seo_title") or ""),
            _strip_html_for_tokens(d.get("description_html")),
        ]
    )
    return len(tokens & _tokens_from_blob(hay.lower()))


def blog_article_row_token_overlap(tokens: frozenset[str], row: dict[str, Any]) -> int:
    d = row
    hay = " ".join(
        [
            str(d.get("title") or ""),
            str(d.get("seo_title") or ""),
            str(d.get("seo_description") or ""),
            str(d.get("summary") or ""),
            _strip_html_for_tokens(d.get("body") or ""),
            _tags_json_phrase_blob(d.get("tags_json")),
        ]
    )
    return len(tokens & _tokens_from_blob(hay.lower()))


def _product_overlap_score(article_tokens: frozenset[str], row: dict[str, Any]) -> int:
    r = row
    hay = " ".join(
        [
            str(r.get("title") or ""),
            str(r.get("seo_title") or ""),
            str(r.get("vendor") or ""),
            str(r.get("product_type") or ""),
            _tags_json_phrase_blob(r.get("tags_json")),
        ]
    )
    hay_tokens = _tokens_from_blob(hay.lower())
    return len(article_tokens & hay_tokens)


def product_row_token_overlap(tokens: frozenset[str], row: dict[str, Any]) -> int:
    """Count of overlapping tokens between *tokens* and a product row's searchable text."""
    return _product_overlap_score(tokens, row)


def _related_products_by_token_overlap(
    conn: sqlite3.Connection,
    article_tokens: frozenset[str],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT handle, title, seo_title, vendor, product_type, tags_json
        FROM products
        WHERE status = 'ACTIVE'
        """
    ).fetchall()
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        d = dict(row)
        s = _product_overlap_score(article_tokens, d)
        scored.append((s, (d.get("title") or "").lower(), {"handle": d["handle"], "title": d["title"]}))
    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][0] if scored else 0
    if best <= 0:
        return [x[2] for x in sorted(scored, key=lambda x: x[1])[:limit]]
    return [x[2] for x in scored if x[0] > 0][:limit]


def _related_collections_by_token_overlap(
    conn: sqlite3.Connection,
    article_tokens: frozenset[str],
    *,
    title_fallback_lower: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT handle, title, seo_title, description_html
        FROM collections
        ORDER BY title
        """
    ).fetchall()
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        d = dict(row)
        hay = " ".join(
            [
                str(d.get("title") or ""),
                str(d.get("seo_title") or ""),
                _strip_html_for_tokens(d.get("description_html")),
            ]
        )
        ct = _tokens_from_blob(hay.lower())
        s = len(article_tokens & ct)
        scored.append((s, (d.get("title") or "").lower(), {"handle": d["handle"], "title": d["title"]}))
    scored.sort(key=lambda x: (-x[0], x[1]))
    positives = [x[2] for x in scored if x[0] > 0][:limit]
    if positives:
        return positives
    # Legacy: any collection title token (>3 chars) appears as substring in article title.
    all_collections = [x[2] for x in sorted(scored, key=lambda x: x[1])]
    return [
        c
        for c in all_collections
        if any(
            word in title_fallback_lower
            for word in (c["title"] or "").lower().split()
            if len(word) > 3
        )
    ][:limit]


def _related_pages_by_token_overlap(
    conn: sqlite3.Connection,
    article_tokens: frozenset[str],
    *,
    exclude_handle: str | None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if exclude_handle:
        rows = conn.execute(
            """
            SELECT handle, title, seo_title, seo_description, body
            FROM pages
            WHERE handle != ?
            """,
            (exclude_handle,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT handle, title, seo_title, seo_description, body
            FROM pages
            """
        ).fetchall()
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        d = dict(row)
        hay = " ".join(
            [
                str(d.get("title") or ""),
                str(d.get("seo_title") or ""),
                str(d.get("seo_description") or ""),
                _strip_html_for_tokens(d.get("body") or ""),
            ]
        )
        pt = _tokens_from_blob(hay.lower())
        s = len(article_tokens & pt)
        scored.append((s, (d.get("title") or "").lower(), {"handle": d["handle"], "title": d["title"]}))
    scored.sort(key=lambda x: (-x[0], x[1]))
    best = scored[0][0] if scored else 0
    if best <= 0:
        return [x[2] for x in sorted(scored, key=lambda x: x[1])[:limit]]
    return [x[2] for x in scored if x[0] > 0][:limit]
