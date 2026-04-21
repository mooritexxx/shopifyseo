"""Article-draft RAG: rich retrieval query + hybrid token re-ranking over embedding neighbors."""

from __future__ import annotations

import sqlite3
from typing import Any

from shopifyseo import dashboard_queries as dq


def _sqlite_row_to_dict(row: Any, columns: tuple[str, ...]) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {str(k): row[k] for k in row.keys()}
    return {columns[i]: row[i] for i in range(min(len(columns), len(row)))}


# Stay well under Gemini embed input limits (see embedding_store.MAX_INPUT_CHARS).
ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS = 8000
# Retrieve a wider pool from cosine similarity, then merge/rerank down.
_EMBEDDING_WIDE_K = 15
_TOKEN_BONUS = 0.15
# Synthetic score for strong token-only rows not returned by embeddings.
_TOKEN_ONLY_SCORE_BASE = 0.48
_TOKEN_ONLY_SCORE_STEP = 0.055


def build_article_draft_retrieval_query(
    *,
    topic: str,
    keywords: list[str | dict] | None = None,
    linked_cluster_id: int | None = None,
    conn: sqlite3.Connection | None = None,
    max_chars: int = ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS,
    extra_terms: list[str] | None = None,
) -> str:
    """Single string for ``retrieve_related`` (topic + keywords + optional cluster fields)."""
    parts: list[str] = []
    t = (topic or "").strip()
    if t:
        parts.append(t)
    if keywords:
        for raw in keywords[:24]:
            if isinstance(raw, dict):
                k = (raw.get("keyword") or "").strip()
            else:
                k = str(raw).strip()
            if k and k.lower() not in {p.lower() for p in parts}:
                parts.append(k)
    if linked_cluster_id is not None and conn is not None:
        row = conn.execute(
            "SELECT name, primary_keyword, content_brief FROM clusters WHERE id = ?",
            (linked_cluster_id,),
        ).fetchone()
        if row:
            r = _sqlite_row_to_dict(row, ("name", "primary_keyword", "content_brief"))
            for key in ("name", "primary_keyword", "content_brief"):
                v = (str(r.get(key) or "")).strip()
                if v and v.lower() not in {p.lower() for p in parts}:
                    parts.append(v)
    if extra_terms:
        for term in extra_terms:
            k = (term or "").strip()
            if k and k.lower() not in {p.lower() for p in parts}:
                parts.append(k)
    while len(parts) > 1 and len(" | ".join(parts)) > max_chars:
        parts.pop()
    blob = " | ".join(parts)
    if len(blob) <= max_chars:
        return blob
    return blob[:max_chars]


def build_regen_retrieval_query(conn: sqlite3.Connection, object_type: str, handle: str) -> str:
    """Text blob for token hybrid merge when regenerating an existing catalog object."""
    if object_type == "blog_article":
        blog_h, sep, art_h = handle.partition("/")
        if not sep or not art_h:
            return (handle or "").strip()[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
        row = conn.execute(
            """
            SELECT title, seo_title, seo_description, summary, body, tags_json
            FROM blog_articles WHERE blog_handle = ? AND handle = ?
            """,
            (blog_h, art_h),
        ).fetchone()
        if not row:
            return (handle or "").strip()[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
        d = _sqlite_row_to_dict(
            row,
            ("title", "seo_title", "seo_description", "summary", "body", "tags_json"),
        )
        blob = " | ".join(
            p
            for p in (
                d.get("title") or "",
                d.get("seo_title") or "",
                d.get("seo_description") or "",
                d.get("summary") or "",
                dq.strip_html_for_retrieval(d.get("body") or ""),
                dq.tags_json_phrase_for_retrieval(d.get("tags_json")),
            )
            if p
        )
        return blob[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
    if object_type == "page":
        row = conn.execute(
            "SELECT title, seo_title, seo_description, body FROM pages WHERE handle = ?",
            (handle,),
        ).fetchone()
        if not row:
            return (handle or "").strip()[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
        d = _sqlite_row_to_dict(row, ("title", "seo_title", "seo_description", "body"))
        blob = " | ".join(
            p
            for p in (
                d.get("title") or "",
                d.get("seo_title") or "",
                d.get("seo_description") or "",
                dq.strip_html_for_retrieval(d.get("body") or ""),
            )
            if p
        )
        return blob[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
    if object_type == "product":
        row = conn.execute(
            """
            SELECT title, seo_title, seo_description, description_html, tags_json, vendor, product_type
            FROM products WHERE handle = ?
            """,
            (handle,),
        ).fetchone()
        if not row:
            return (handle or "").strip()[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
        d = _sqlite_row_to_dict(
            row,
            ("title", "seo_title", "seo_description", "description_html", "tags_json", "vendor", "product_type"),
        )
        blob = " | ".join(
            p
            for p in (
                d.get("title") or "",
                d.get("seo_title") or "",
                d.get("seo_description") or "",
                dq.strip_html_for_retrieval(d.get("description_html") or ""),
                d.get("vendor") or "",
                d.get("product_type") or "",
                dq.tags_json_phrase_for_retrieval(d.get("tags_json")),
            )
            if p
        )
        return blob[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
    if object_type == "collection":
        row = conn.execute(
            "SELECT title, seo_title, seo_description, description_html FROM collections WHERE handle = ?",
            (handle,),
        ).fetchone()
        if not row:
            return (handle or "").strip()[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
        d = _sqlite_row_to_dict(
            row, ("title", "seo_title", "seo_description", "description_html")
        )
        blob = " | ".join(
            p
            for p in (
                d.get("title") or "",
                d.get("seo_title") or "",
                d.get("seo_description") or "",
                dq.strip_html_for_retrieval(d.get("description_html") or ""),
            )
            if p
        )
        return blob[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]
    return (handle or "").strip()[:ARTICLE_DRAFT_RETRIEVAL_MAX_CHARS]


def _embedding_row_token_overlap(query_tokens: frozenset[str], row: dict[str, Any]) -> int:
    blob = " ".join(
        [
            str(row.get("source_text_preview") or ""),
            str(row.get("object_handle") or "").replace("/", " ").replace("-", " "),
        ]
    )
    return len(query_tokens & dq.retrieval_tokens_from_text(blob))


def _token_only_synthetic_score(overlap: int) -> float:
    if overlap <= 0:
        return 0.0
    return min(0.94, _TOKEN_ONLY_SCORE_BASE + _TOKEN_ONLY_SCORE_STEP * min(overlap, 10))


def _gather_token_extra_candidates(
    conn: sqlite3.Connection,
    query_tokens: frozenset[str],
    *,
    seen: set[tuple[str, str]],
    min_overlap: int = 2,
    per_type_cap: int = 8,
) -> list[dict[str, Any]]:
    """High token-overlap catalog rows missing from embedding hits."""
    out: list[dict[str, Any]] = []

    rows = conn.execute(
        """
        SELECT handle, title, seo_title, vendor, product_type, tags_json
        FROM products WHERE status = 'ACTIVE'
        """
    ).fetchall()
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        d = dict(row)
        s = dq.product_row_token_overlap(query_tokens, d)
        if s >= min_overlap:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    for s, d in scored[:per_type_cap]:
        key = ("product", d["handle"])
        if key in seen:
            continue
        seen.add(key)
        preview = (d.get("title") or d["handle"])[:400]
        out.append(
            {
                "object_type": "product",
                "object_handle": d["handle"],
                "chunk_index": 0,
                "source_text_preview": preview,
                "score": _token_only_synthetic_score(s),
            }
        )

    rows = conn.execute(
        "SELECT handle, title, seo_title, description_html FROM collections ORDER BY title"
    ).fetchall()
    scored = []
    for row in rows:
        d = dict(row)
        s = dq.collection_row_token_overlap(query_tokens, d)
        if s >= min_overlap:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    for s, d in scored[:per_type_cap]:
        key = ("collection", d["handle"])
        if key in seen:
            continue
        seen.add(key)
        preview = (d.get("title") or d["handle"])[:400]
        out.append(
            {
                "object_type": "collection",
                "object_handle": d["handle"],
                "chunk_index": 0,
                "source_text_preview": preview,
                "score": _token_only_synthetic_score(s),
            }
        )

    rows = conn.execute(
        """
        SELECT blog_handle, handle, title, seo_title, seo_description, summary, body, tags_json
        FROM blog_articles
        """
    ).fetchall()
    scored = []
    for row in rows:
        d = dict(row)
        s = dq.blog_article_row_token_overlap(query_tokens, d)
        if s >= min_overlap:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    for s, d in scored[:per_type_cap]:
        ch = dq.blog_article_composite_handle(d["blog_handle"], d["handle"])
        key = ("blog_article", ch)
        if key in seen:
            continue
        seen.add(key)
        preview = (d.get("title") or d["handle"])[:400]
        out.append(
            {
                "object_type": "blog_article",
                "object_handle": ch,
                "chunk_index": 0,
                "source_text_preview": preview,
                "score": _token_only_synthetic_score(s),
            }
        )

    return out


def merge_embedding_rag_with_token_overlap(
    conn: sqlite3.Connection,
    retrieval_query: str,
    embedding_rows: list[dict[str, Any]],
    *,
    out_k: int | None = None,
) -> list[dict[str, Any]]:
    """Re-rank embedding neighbors with token overlap; add strong token-only rows."""
    if not embedding_rows:
        return []
    k = out_k if out_k is not None else len(embedding_rows)
    q_tokens = dq.retrieval_tokens_from_text(retrieval_query)
    if not q_tokens:
        return list(embedding_rows)[:k]

    seen: set[tuple[str, str]] = set()
    scored: list[tuple[float, dict[str, Any]]] = []

    for r in embedding_rows:
        ot = str(r.get("object_type") or "")
        oh = str(r.get("object_handle") or "")
        if not ot or not oh:
            continue
        key = (ot, oh)
        if key in seen:
            continue
        seen.add(key)
        overlap = _embedding_row_token_overlap(q_tokens, r)
        base = float(r.get("score") or 0.0)
        adj = base + _TOKEN_BONUS * overlap
        row = {**r, "score": adj}
        scored.append((adj, row))

    extras = _gather_token_extra_candidates(conn, q_tokens, seen=seen, min_overlap=2, per_type_cap=8)
    for row in extras:
        scored.append((float(row.get("score") or 0.0), row))

    scored.sort(key=lambda x: -x[0])
    deduped: list[dict[str, Any]] = []
    used: set[tuple[str, str]] = set()
    for _adj, row in scored:
        key = (str(row.get("object_type") or ""), str(row.get("object_handle") or ""))
        if not key[0] or not key[1] or key in used:
            continue
        used.add(key)
        deduped.append(row)
        if len(deduped) >= k:
            break
    return deduped


def run_article_draft_rag(
    conn: sqlite3.Connection,
    api_key: str,
    *,
    topic: str,
    keywords: list[str | dict] | None,
    linked_cluster_id: int | None,
    top_k: int = 5,
    retrieval_extra_terms: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Wide embedding retrieval on a rich query, then hybrid merge down to *top_k*."""
    from shopifyseo.embedding_store import retrieve_related

    q = build_article_draft_retrieval_query(
        topic=topic,
        keywords=keywords,
        linked_cluster_id=linked_cluster_id,
        conn=conn,
        extra_terms=retrieval_extra_terms,
    )
    if not (api_key or "").strip():
        return []
    wide = (
        retrieve_related(
            conn,
            api_key.strip(),
            q,
            top_k=_EMBEDDING_WIDE_K,
            object_types=["blog_article", "product", "collection"],
        )
        or []
    )
    if not wide:
        return []
    return merge_embedding_rag_with_token_overlap(conn, q, wide, out_k=top_k)
