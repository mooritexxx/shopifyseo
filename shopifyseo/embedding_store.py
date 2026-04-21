"""Vector embedding store using gemini-embedding-2-preview.

Provides embed, store, retrieve, and prune operations for all catalog and
research entity types.  Retrieval is always optional — callers wrap in
try/except and fall back to empty results.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import struct
import threading
import time
from html.parser import HTMLParser
from typing import Any

import numpy as np

from . import dashboard_queries as dq
from .dashboard_ai_engine_parts.config import GEMINI_API_URL
from .dashboard_http import HttpRequestError, request_json
from .gsc_query_limits import GSC_PER_URL_QUERY_ROW_LIMIT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMBEDDING_MODEL = "gemini-embedding-2-preview"
EMBEDDING_DIMS = 3072
MAX_CHUNK_CHARS = 8000       # ~2 000 tokens at ~4 chars/token
CHUNK_OVERLAP_CHARS = 800    # ~200 tokens
MAX_INPUT_CHARS = 32_000     # ~8 000 tokens — Gemini input limit
BATCH_SIZE = 100             # Gemini batchEmbedContents limit
RAG_TOKEN_CAP_SLIM = 1_600   # ~400 tokens in chars
RAG_TOKEN_CAP_FULL = 3_200   # ~800 tokens in chars

EMBEDDABLE_TYPES = (
    "product", "collection", "page", "blog_article",
    "cluster", "gsc_queries", "keyword", "article_idea", "competitor_page",
)

# Custom metafield columns added to the products table for your store.
# Edit these lists to match your Shopify store's custom metafields.
# Any column listed here that doesn't exist in your DB will be silently skipped.
_PRODUCT_ATTR_COLS = (
    "device_type", "battery_size", "nicotine_strength", "puff_count",
    "charging_port", "coil", "size",
)
_PRODUCT_LABEL_COLS = (
    "e_liquid_flavor_labels_json", "vaporizer_style_labels_json",
    "vaping_style_labels_json", "battery_type_labels_json",
    "coil_connection_labels_json", "color_pattern_labels_json",
)

_sync_lock = threading.Lock()


# ---------------------------------------------------------------------------
# HTML stripping helper
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _strip_html(html: str | None) -> str:
    if not html:
        return ""
    s = _HTMLStripper()
    s.feed(html)
    return s.get_text().strip()


def _coalesce(val: Any) -> str:
    if val is None or (isinstance(val, str) and val.strip().lower() in ("", "null")):
        return ""
    return str(val).strip()


def _json_list_values(raw: Any) -> list[str]:
    """Extract string values from a JSON-encoded list or return []."""
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(raw, list):
        return [str(v) for v in raw if v]
    return []


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _embed_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_array(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32).copy()


# ---------------------------------------------------------------------------
# Text assembly per entity type
# ---------------------------------------------------------------------------

def _build_product_text(row: dict, conn: sqlite3.Connection) -> str:
    parts = [
        _coalesce(row.get("title")),
        _coalesce(row.get("seo_title")),
        _coalesce(row.get("seo_description")),
        _strip_html(row.get("description_html")),
    ]
    tags = _json_list_values(row.get("tags_json"))
    if tags:
        parts.append(", ".join(tags))

    attrs = [_coalesce(row.get(c)) for c in _PRODUCT_ATTR_COLS]
    attrs = [a for a in attrs if a]
    if attrs:
        parts.append(" | ".join(attrs))

    for col in _PRODUCT_LABEL_COLS:
        labels = _json_list_values(row.get(col))
        if labels:
            parts.append(", ".join(labels))

    product_id = row.get("shopify_id") or ""
    if product_id:
        alt_rows = conn.execute(
            "SELECT alt_text FROM product_images WHERE product_shopify_id = ? AND alt_text IS NOT NULL AND alt_text != ''",
            (product_id,),
        ).fetchall()
        alts = [r["alt_text"] for r in alt_rows]
        if alts:
            parts.append(" | ".join(alts))

    text = " | ".join(p for p in parts if p)
    if len(text) > MAX_INPUT_CHARS:
        text = _truncate_product_text(parts, row)
    return text


def _truncate_product_text(parts: list[str], row: dict) -> str:
    """Drop low-priority fields until text fits MAX_INPUT_CHARS."""
    always_keep = [
        _coalesce(row.get("title")),
        _coalesce(row.get("seo_title")),
        _coalesce(row.get("seo_description")),
    ]
    attrs = [_coalesce(row.get(c)) for c in _PRODUCT_ATTR_COLS]
    always_keep.extend(a for a in attrs if a)
    text = " | ".join(p for p in always_keep if p)
    return text[:MAX_INPUT_CHARS]


def _build_collection_text(row: dict, conn: sqlite3.Connection) -> str:
    parts = [
        _coalesce(row.get("title")),
        _coalesce(row.get("seo_title")),
        _coalesce(row.get("seo_description")),
        _strip_html(row.get("description_html")),
    ]
    coll_id = row.get("shopify_id") or ""
    if coll_id:
        prod_rows = conn.execute(
            "SELECT product_title FROM collection_products WHERE collection_shopify_id = ? LIMIT 30",
            (coll_id,),
        ).fetchall()
        titles = [r["product_title"] for r in prod_rows if r["product_title"]]
        if titles:
            parts.append(", ".join(titles))
    return " | ".join(p for p in parts if p)[:MAX_INPUT_CHARS]


def _build_page_text(row: dict) -> str:
    parts = [
        _coalesce(row.get("title")),
        _coalesce(row.get("seo_title")),
        _coalesce(row.get("seo_description")),
        _strip_html(row.get("body")),
    ]
    return " | ".join(p for p in parts if p)[:MAX_INPUT_CHARS]


def _build_article_text(row: dict) -> str | list[str]:
    header = " | ".join(p for p in [
        _coalesce(row.get("title")),
        _coalesce(row.get("seo_title")),
        _coalesce(row.get("seo_description")),
    ] if p)
    body = _strip_html(row.get("body"))
    full = f"{header} | {body}" if body else header

    if len(full) <= MAX_CHUNK_CHARS:
        return full[:MAX_INPUT_CHARS]

    paragraphs = re.split(r"\n\n|</p>|<br\s*/?>", row.get("body") or "")
    paragraphs = [_strip_html(p).strip() for p in paragraphs]
    paragraphs = [p for p in paragraphs if p]

    chunks: list[str] = []
    current = header
    overlap_buffer: list[str] = []

    for para in paragraphs:
        candidate = f"{current} | {para}" if current else para
        if len(candidate) > MAX_CHUNK_CHARS and current != header:
            chunks.append(current[:MAX_INPUT_CHARS])
            overlap_text = " | ".join(overlap_buffer)
            current = f"{header} | {overlap_text} | {para}" if overlap_text else f"{header} | {para}"
            overlap_buffer = []
        else:
            current = candidate

        overlap_buffer.append(para)
        total_overlap = sum(len(p) for p in overlap_buffer)
        while total_overlap > CHUNK_OVERLAP_CHARS and len(overlap_buffer) > 1:
            total_overlap -= len(overlap_buffer.pop(0))

    if current and current != header:
        chunks.append(current[:MAX_INPUT_CHARS])

    return chunks if len(chunks) > 1 else (chunks[0] if chunks else header[:MAX_INPUT_CHARS])


def _build_cluster_text(row: dict, conn: sqlite3.Connection) -> str:
    parts = [
        _coalesce(row.get("name")),
        _coalesce(row.get("primary_keyword")),
        _coalesce(row.get("content_brief")),
    ]
    cluster_id = row.get("id")
    if cluster_id:
        kw_rows = conn.execute(
            "SELECT keyword FROM cluster_keywords WHERE cluster_id = ? LIMIT 10",
            (cluster_id,),
        ).fetchall()
        kws = [r["keyword"] for r in kw_rows]
        if kws:
            parts.append(", ".join(kws))
    return " | ".join(p for p in parts if p)[:MAX_INPUT_CHARS]


def _catalog_title_for_gsc_bundle(conn: sqlite3.Connection, object_type: str, handle: str) -> str:
    """Resolve storefront title for embedding header (matches catalog object types in gsc_query_rows)."""
    if object_type == "product":
        row = conn.execute("SELECT title FROM products WHERE handle = ?", (handle,)).fetchone()
    elif object_type == "collection":
        row = conn.execute("SELECT title FROM collections WHERE handle = ?", (handle,)).fetchone()
    elif object_type == "page":
        row = conn.execute("SELECT title FROM pages WHERE handle = ?", (handle,)).fetchone()
    elif object_type == "blog_article":
        blog_h, sep, art_h = handle.partition("/")
        if sep and art_h:
            row = conn.execute(
                "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                (blog_h, art_h),
            ).fetchone()
        else:
            return ""
    else:
        return ""
    return _coalesce(row["title"] if row else None)


def _build_gsc_queries_text(handle: str, object_type_src: str, conn: sqlite3.Connection) -> str:
    """Text for `gsc_queries` embeddings: entity title + canonical URL + top queries (same row cap as API/context)."""
    lim = GSC_PER_URL_QUERY_ROW_LIMIT
    rows = conn.execute(
        """
        SELECT query FROM gsc_query_rows
        WHERE object_type = ? AND object_handle = ?
        ORDER BY impressions DESC, clicks DESC, query ASC
        LIMIT ?
        """,
        (object_type_src, handle, lim),
    ).fetchall()
    queries = [r["query"] for r in rows if r["query"]]
    queries_part = " | ".join(queries)
    title = _catalog_title_for_gsc_bundle(conn, object_type_src, handle)
    url = dq.object_url(object_type_src, handle)
    header_bits = [b for b in (title, url) if b]
    if header_bits:
        header = " | ".join(header_bits)
        combined = f"{header} | {queries_part}" if queries_part else header
    else:
        combined = queries_part
    return combined[:MAX_INPUT_CHARS] if combined else ""


def _build_keyword_text(row: dict) -> str:
    parts = [
        _coalesce(row.get("keyword")),
        _coalesce(row.get("parent_topic")),
        _coalesce(row.get("intent")),
        _coalesce(row.get("content_format_hint")),
    ]
    return " | ".join(p for p in parts if p)[:MAX_INPUT_CHARS]


def _build_article_idea_text(row: dict) -> str:
    parts = [
        _coalesce(row.get("suggested_title")),
        _coalesce(row.get("brief")),
        _coalesce(row.get("primary_keyword")),
        _coalesce(row.get("gap_reason")),
    ]
    supporting = _json_list_values(row.get("supporting_keywords"))
    if supporting:
        parts.append(", ".join(supporting))
    raw_q = row.get("audience_questions_json")
    if raw_q:
        try:
            qlist = json.loads(raw_q) if isinstance(raw_q, str) else raw_q
        except (json.JSONDecodeError, TypeError):
            qlist = []
        if isinstance(qlist, list) and qlist:
            bits: list[str] = []
            for q in qlist:
                if isinstance(q, dict):
                    qq = str(q.get("question") or "").strip()
                    sn = str(q.get("snippet") or q.get("answer") or "").strip()
                    if qq and sn:
                        bits.append(f"{qq} {sn}")
                    elif qq:
                        bits.append(qq)
                else:
                    s = str(q).strip()
                    if s:
                        bits.append(s)
            qs = " ".join(bits)
            if qs:
                parts.append(qs)
    raw_p = row.get("top_ranking_pages_json")
    if raw_p:
        try:
            plist = json.loads(raw_p) if isinstance(raw_p, str) else raw_p
        except (json.JSONDecodeError, TypeError):
            plist = []
        if isinstance(plist, list) and plist:
            obits: list[str] = []
            for p in plist:
                if isinstance(p, dict):
                    t = str(p.get("title") or "").strip()
                    u = str(p.get("url") or p.get("link") or "").strip()
                    if t and u:
                        obits.append(f"{t} {u}")
                    elif u:
                        obits.append(u)
                else:
                    s = str(p).strip()
                    if s:
                        obits.append(s)
            organic_txt = " ".join(obits)
            if organic_txt:
                parts.append(organic_txt)
    raw_rs = row.get("related_searches_json")
    if raw_rs:
        try:
            rslist = json.loads(raw_rs) if isinstance(raw_rs, str) else raw_rs
        except (json.JSONDecodeError, TypeError):
            rslist = []
        if isinstance(rslist, list) and rslist:
            rqbits: list[str] = []
            for x in rslist:
                if isinstance(x, dict):
                    q = str(x.get("query") or "").strip()
                    if q:
                        rqbits.append(q)
                elif isinstance(x, str) and x.strip():
                    rqbits.append(x.strip())
            rs_txt = " ".join(rqbits)
            if rs_txt:
                parts.append(rs_txt)
    raw_aio = row.get("ai_overview_json")
    if raw_aio:
        aio_txt = _flatten_ai_overview_json_for_embed(raw_aio)
        if aio_txt:
            parts.append(aio_txt)
    return " | ".join(p for p in parts if p)[:MAX_INPUT_CHARS]


def _flatten_ai_overview_json_for_embed(raw: Any) -> str:
    """Join AI overview snippets/titles for semantic search text."""
    if not raw:
        return ""
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(obj, dict):
        return ""
    bits: list[str] = []
    tbs = obj.get("text_blocks")
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
    refs = obj.get("references")
    if isinstance(refs, list):
        for r in refs:
            if not isinstance(r, dict):
                continue
            t = str(r.get("title") or "").strip()
            sn = str(r.get("snippet") or "").strip()
            chunk = " ".join(x for x in (t, sn) if x)
            if chunk:
                bits.append(chunk)
    return " ".join(bits)


def _build_competitor_page_text(row: dict) -> str:
    """Always produce non-empty text: domain + URL are required in DB but path-only URLs
    used to yield '' and those rows were skipped during embedding (coverage < 100%)."""
    domain = _coalesce(row.get("competitor_domain"))
    url = _coalesce(row.get("url"))
    path_slug = ""
    if url:
        path_slug = re.sub(r"^https?://[^/]+", "", url)
        path_slug = path_slug.strip("/").replace("/", " ")
    parts = [
        domain,
        _coalesce(row.get("top_keyword")),
        _coalesce(row.get("page_type")),
        path_slug,
    ]
    joined = " | ".join(p for p in parts if p)
    if not joined.strip():
        joined = f"{domain} {url}".strip()
    return joined[:MAX_INPUT_CHARS]


def build_embed_text(
    object_type: str, row: dict, conn: sqlite3.Connection | None = None,
) -> str | list[str]:
    """Assemble text to embed for a given entity type and row dict."""
    if object_type == "product":
        assert conn is not None
        return _build_product_text(row, conn)
    if object_type == "collection":
        assert conn is not None
        return _build_collection_text(row, conn)
    if object_type == "page":
        return _build_page_text(row)
    if object_type == "blog_article":
        return _build_article_text(row)
    if object_type == "cluster":
        assert conn is not None
        return _build_cluster_text(row, conn)
    if object_type == "keyword":
        return _build_keyword_text(row)
    if object_type == "article_idea":
        return _build_article_idea_text(row)
    if object_type == "competitor_page":
        return _build_competitor_page_text(row)
    raise ValueError(f"Unknown embed type: {object_type}")


# ---------------------------------------------------------------------------
# Gemini embedding API
# ---------------------------------------------------------------------------

def embed_batch(
    api_key: str,
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """Call Gemini batchEmbedContents. Returns one embedding per input text."""
    if not texts:
        return []
    all_embeddings: list[list[float]] = []
    model_path = f"models/{EMBEDDING_MODEL}"
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start : start + BATCH_SIZE]
        requests_payload = [
            {
                "model": model_path,
                "content": {"parts": [{"text": t}]},
                "taskType": task_type,
            }
            for t in batch
        ]
        resp = request_json(
            f"{GEMINI_API_URL}/{model_path}:batchEmbedContents",
            method="POST",
            headers={"x-goog-api-key": api_key},
            payload={"requests": requests_payload},
            timeout=120,
        )
        for emb in resp.get("embeddings", []):
            all_embeddings.append(emb["values"])
        try:
            from .api_usage import log_api_usage
            est_tokens = sum(len(t) // 4 for t in batch)
            log_api_usage(
                provider="gemini", model=EMBEDDING_MODEL, call_type="embedding",
                stage="embedding_sync", input_tokens=est_tokens, output_tokens=0,
            )
        except Exception:
            pass
    return all_embeddings


# ---------------------------------------------------------------------------
# Row loaders per type
# ---------------------------------------------------------------------------

def _load_rows(conn: sqlite3.Connection, object_type: str) -> list[dict]:
    """Load source rows for a given type, returning dicts with a stable handle key."""
    if object_type == "product":
        rows = conn.execute("SELECT * FROM products WHERE status = 'ACTIVE'").fetchall()
        return [dict(r) | {"_handle": r["handle"]} for r in rows]
    if object_type == "collection":
        rows = conn.execute("SELECT * FROM collections").fetchall()
        return [dict(r) | {"_handle": r["handle"]} for r in rows]
    if object_type == "page":
        rows = conn.execute("SELECT * FROM pages").fetchall()
        return [dict(r) | {"_handle": r["handle"]} for r in rows]
    if object_type == "blog_article":
        rows = conn.execute("SELECT * FROM blog_articles").fetchall()
        return [dict(r) | {"_handle": f"{r['blog_handle']}/{r['handle']}"} for r in rows]
    if object_type == "cluster":
        rows = conn.execute("SELECT * FROM clusters").fetchall()
        return [dict(r) | {"_handle": str(r["id"])} for r in rows]
    if object_type == "gsc_queries":
        keys = conn.execute(
            "SELECT DISTINCT object_type, object_handle FROM gsc_query_rows"
        ).fetchall()
        return [{"_handle": f"{r['object_type']}:{r['object_handle']}", "_src_type": r["object_type"], "_src_handle": r["object_handle"]} for r in keys]
    if object_type == "keyword":
        rows = conn.execute(
            "SELECT * FROM keyword_metrics WHERE status IN ('approved', 'new')"
        ).fetchall()
        return [dict(r) | {"_handle": r["keyword"]} for r in rows]
    if object_type == "article_idea":
        rows = conn.execute(
            "SELECT * FROM article_ideas WHERE status != 'rejected'"
        ).fetchall()
        return [dict(r) | {"_handle": str(r["id"])} for r in rows]
    if object_type == "competitor_page":
        rows = conn.execute("SELECT * FROM competitor_top_pages").fetchall()
        return [dict(r) | {"_handle": f"{r['competitor_domain']}:{r['url']}"} for r in rows]
    return []


def _source_table(object_type: str) -> str | None:
    return {
        "product": "products",
        "collection": "collections",
        "page": "pages",
        "blog_article": "blog_articles",
        "cluster": "clusters",
        "keyword": "keyword_metrics",
        "article_idea": "article_ideas",
        "competitor_page": "competitor_top_pages",
    }.get(object_type)


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------

def prune_stale_embeddings(conn: sqlite3.Connection, object_type: str | None = None) -> int:
    """Delete embeddings whose source object no longer exists. Returns count deleted."""
    types = [object_type] if object_type else list(EMBEDDABLE_TYPES)
    total = 0
    for t in types:
        if t == "gsc_queries":
            conn.execute("""
                DELETE FROM embeddings WHERE object_type = 'gsc_queries'
                AND object_handle NOT IN (
                    SELECT DISTINCT object_type || ':' || object_handle FROM gsc_query_rows
                )
            """)
        elif t == "blog_article":
            conn.execute("""
                DELETE FROM embeddings WHERE object_type = 'blog_article'
                AND object_handle NOT IN (
                    SELECT blog_handle || '/' || handle FROM blog_articles
                )
            """)
        elif t == "keyword":
            conn.execute("""
                DELETE FROM embeddings WHERE object_type = 'keyword'
                AND object_handle NOT IN (
                    SELECT keyword FROM keyword_metrics WHERE status IN ('approved', 'new')
                )
            """)
        elif t == "article_idea":
            conn.execute("""
                DELETE FROM embeddings WHERE object_type = 'article_idea'
                AND object_handle NOT IN (
                    SELECT CAST(id AS TEXT) FROM article_ideas WHERE status != 'rejected'
                )
            """)
        elif t == "competitor_page":
            conn.execute("""
                DELETE FROM embeddings WHERE object_type = 'competitor_page'
                AND object_handle NOT IN (
                    SELECT competitor_domain || ':' || url FROM competitor_top_pages
                )
            """)
        elif t == "cluster":
            conn.execute("""
                DELETE FROM embeddings WHERE object_type = 'cluster'
                AND object_handle NOT IN (SELECT CAST(id AS TEXT) FROM clusters)
            """)
        else:
            table = _source_table(t)
            if table:
                conn.execute(f"""
                    DELETE FROM embeddings WHERE object_type = ?
                    AND object_handle NOT IN (SELECT handle FROM {table})
                """, (t,))
        total += conn.execute("SELECT changes()").fetchone()[0]
    if total:
        conn.commit()
    return total


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

def _get_gemini_api_key(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM service_settings WHERE key = 'gemini_api_key'"
    ).fetchone()
    return (row["value"] if row else "").strip()


def sync_embeddings(
    conn: sqlite3.Connection,
    object_type: str | None = None,
) -> dict:
    """Embed changed/new rows for the given type (or all types). Thread-safe via _sync_lock."""
    if not _sync_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "sync_in_progress"}

    try:
        api_key = _get_gemini_api_key(conn)
        if not api_key:
            logger.warning("gemini_api_key not set — skipping embedding sync")
            return {"embedded": 0, "skipped": 0, "pruned": 0, "reason": "no_api_key"}

        types = [object_type] if object_type else list(EMBEDDABLE_TYPES)
        total_embedded = 0
        total_skipped = 0
        total_pruned = 0

        for t in types:
            pruned = prune_stale_embeddings(conn, t)
            total_pruned += pruned

            existing = {}
            for r in conn.execute(
                "SELECT object_handle, chunk_index, text_hash, model_version FROM embeddings WHERE object_type = ?",
                (t,),
            ).fetchall():
                existing[(r["object_handle"], r["chunk_index"])] = (r["text_hash"], r["model_version"])

            rows = _load_rows(conn, t)
            texts_to_embed: list[tuple[str, str, int]] = []  # (handle, text, chunk_index)

            for row in rows:
                handle = row["_handle"]
                if t == "gsc_queries":
                    text_or_chunks = _build_gsc_queries_text(row["_src_handle"], row["_src_type"], conn)
                else:
                    text_or_chunks = build_embed_text(t, row, conn)

                if isinstance(text_or_chunks, str):
                    text_or_chunks = [text_or_chunks]

                conn.execute(
                    "DELETE FROM embeddings WHERE object_type = ? AND object_handle = ? AND chunk_index >= ?",
                    (t, handle, len(text_or_chunks)),
                )

                for ci, chunk_text in enumerate(text_or_chunks):
                    if not chunk_text.strip():
                        continue
                    h = _md5(chunk_text)
                    prev = existing.get((handle, ci))
                    if prev and prev[0] == h and prev[1] == EMBEDDING_MODEL:
                        total_skipped += 1
                        continue
                    texts_to_embed.append((handle, chunk_text, ci))

            if not texts_to_embed:
                continue

            for batch_start in range(0, len(texts_to_embed), BATCH_SIZE):
                batch = texts_to_embed[batch_start : batch_start + BATCH_SIZE]
                batch_texts = [item[1] for item in batch]
                embeddings: list[list[float]] | None = None
                last_exc: BaseException | None = None
                for attempt in range(3):
                    try:
                        embeddings = embed_batch(api_key, batch_texts, task_type="RETRIEVAL_DOCUMENT")
                        if len(embeddings) != len(batch):
                            logger.warning(
                                "Embedding count mismatch for %s batch offset %s: got %d want %d",
                                t,
                                batch_start,
                                len(embeddings),
                                len(batch),
                            )
                            embeddings = None
                            raise RuntimeError("embedding count mismatch")
                        break
                    except (HttpRequestError, Exception) as exc:
                        last_exc = exc
                        logger.warning(
                            "Embedding API error for %s batch offset %s (attempt %d/3): %s",
                            t,
                            batch_start,
                            attempt + 1,
                            exc,
                        )
                        if attempt < 2:
                            time.sleep(2.0 * (attempt + 1))
                if embeddings is None:
                    logger.error(
                        "Skipping embedding batch for %s offset %s after retries: %s",
                        t,
                        batch_start,
                        last_exc,
                    )
                    continue

                for (handle, text, ci), vec in zip(batch, embeddings):
                    blob = _embed_to_blob(vec)
                    preview = text[:200]
                    token_est = len(text) // 4
                    conn.execute(
                        """
                        INSERT INTO embeddings (object_type, object_handle, chunk_index, text_hash, model_version, embedding, source_text_preview, token_count, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(object_type, object_handle, chunk_index)
                        DO UPDATE SET text_hash=excluded.text_hash, model_version=excluded.model_version,
                                      embedding=excluded.embedding, source_text_preview=excluded.source_text_preview,
                                      token_count=excluded.token_count, updated_at=excluded.updated_at
                        """,
                        (t, handle, ci, _md5(text), EMBEDDING_MODEL, blob, preview, token_est),
                    )
                    total_embedded += 1
                conn.commit()

        return {"embedded": total_embedded, "skipped": total_skipped, "pruned": total_pruned}
    finally:
        _sync_lock.release()


# ---------------------------------------------------------------------------
# Retrieval helpers
# ---------------------------------------------------------------------------

def _load_embedding_matrix(
    conn: sqlite3.Connection,
    object_types: list[str] | None = None,
    exclude: tuple[str, str] | None = None,
) -> tuple[np.ndarray, list[dict]]:
    """Load embeddings into a numpy matrix and metadata list."""
    if object_types:
        placeholders = ",".join("?" for _ in object_types)
        rows = conn.execute(
            f"SELECT object_type, object_handle, chunk_index, embedding, source_text_preview FROM embeddings WHERE object_type IN ({placeholders})",
            object_types,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT object_type, object_handle, chunk_index, embedding, source_text_preview FROM embeddings"
        ).fetchall()

    if not rows:
        return np.empty((0, EMBEDDING_DIMS), dtype=np.float32), []

    meta = []
    vecs = []
    for r in rows:
        if exclude and r["object_type"] == exclude[0] and r["object_handle"] == exclude[1]:
            continue
        vecs.append(_blob_to_array(r["embedding"]))
        meta.append({
            "object_type": r["object_type"],
            "object_handle": r["object_handle"],
            "chunk_index": r["chunk_index"],
            "source_text_preview": r["source_text_preview"],
        })

    if not vecs:
        return np.empty((0, EMBEDDING_DIMS), dtype=np.float32), []
    return np.vstack(vecs), meta


def _cosine_similarity(query: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if matrix.shape[0] == 0:
        return np.array([])
    query_norm = query / (np.linalg.norm(query) + 1e-10)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    matrix_norm = matrix / norms
    return matrix_norm @ query_norm


def _dedup_by_handle(scored: list[dict]) -> list[dict]:
    """Keep only the highest-scoring entry per (object_type, object_handle)."""
    seen: dict[tuple[str, str], dict] = {}
    for item in scored:
        key = (item["object_type"], item["object_handle"])
        if key not in seen or item["score"] > seen[key]["score"]:
            seen[key] = item
    return sorted(seen.values(), key=lambda x: x["score"], reverse=True)


def _apply_type_quotas(items: list[dict], type_quotas: dict[str, int]) -> list[dict]:
    """Select items respecting per-type quotas, preserving score order."""
    counts: dict[str, int] = {}
    result = []
    for item in items:
        t = item["object_type"]
        quota = type_quotas.get(t, 0)
        if counts.get(t, 0) < quota:
            result.append(item)
            counts[t] = counts.get(t, 0) + 1
    return result


def retrieve_related(
    conn: sqlite3.Connection,
    api_key: str,
    query_text: str,
    top_k: int = 5,
    object_types: list[str] | None = None,
    exclude: tuple[str, str] | None = None,
) -> list[dict]:
    """Embed query_text and return top-k similar objects."""
    vecs = embed_batch(api_key, [query_text], task_type="RETRIEVAL_QUERY")
    if not vecs:
        return []
    query_vec = np.array(vecs[0], dtype=np.float32)
    matrix, meta = _load_embedding_matrix(conn, object_types, exclude)
    if matrix.shape[0] == 0:
        return []

    scores = _cosine_similarity(query_vec, matrix)
    scored = [{**m, "score": float(scores[i])} for i, m in enumerate(meta)]
    scored.sort(key=lambda x: x["score"], reverse=True)
    scored = _dedup_by_handle(scored)
    return scored[:top_k]


def retrieve_related_by_handle(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    top_k: int = 5,
    type_quotas: dict[str, int] | None = None,
) -> list[dict]:
    """Find objects similar to a stored object (no API call needed)."""
    row = conn.execute(
        "SELECT embedding FROM embeddings WHERE object_type = ? AND object_handle = ? AND chunk_index = 0",
        (object_type, handle),
    ).fetchone()
    if not row:
        return []

    query_vec = _blob_to_array(row["embedding"])
    target_types = list(type_quotas.keys()) if type_quotas else None
    matrix, meta = _load_embedding_matrix(conn, target_types, exclude=(object_type, handle))
    if matrix.shape[0] == 0:
        return []

    scores = _cosine_similarity(query_vec, matrix)
    scored = [{**m, "score": float(scores[i])} for i, m in enumerate(meta)]
    scored.sort(key=lambda x: x["score"], reverse=True)
    scored = _dedup_by_handle(scored)

    if type_quotas:
        return _apply_type_quotas(scored, type_quotas)
    return scored[:top_k]


def find_semantic_keyword_matches(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    top_k: int = 10,
) -> list[dict]:
    """Find keywords from keyword_metrics semantically close to an object."""
    row = conn.execute(
        "SELECT embedding FROM embeddings WHERE object_type = ? AND object_handle = ? AND chunk_index = 0",
        (object_type, handle),
    ).fetchone()
    if not row:
        return []

    query_vec = _blob_to_array(row["embedding"])
    matrix, meta = _load_embedding_matrix(conn, ["keyword"])
    if matrix.shape[0] == 0:
        return []

    scores = _cosine_similarity(query_vec, matrix)
    scored = [{**m, "score": float(scores[i])} for i, m in enumerate(meta)]
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_k]

    result = []
    for item in top:
        kw = item["object_handle"]
        kw_row = conn.execute(
            "SELECT keyword, parent_topic, intent, volume, difficulty FROM keyword_metrics WHERE keyword = ?",
            (kw,),
        ).fetchone()
        if kw_row:
            result.append({
                "keyword": kw_row["keyword"],
                "parent_topic": kw_row["parent_topic"],
                "intent": kw_row["intent"],
                "volume": kw_row["volume"],
                "difficulty": kw_row["difficulty"],
                "score": item["score"],
            })
    return result


def find_similar_ideas(
    conn: sqlite3.Connection,
    api_key: str,
    idea_text: str,
    top_k: int = 5,
) -> list[dict]:
    """Find existing article ideas similar to a candidate idea text."""
    vecs = embed_batch(api_key, [idea_text], task_type="RETRIEVAL_QUERY")
    if not vecs:
        return []
    query_vec = np.array(vecs[0], dtype=np.float32)
    matrix, meta = _load_embedding_matrix(conn, ["article_idea"])
    if matrix.shape[0] == 0:
        return []

    scores = _cosine_similarity(query_vec, matrix)
    scored = [{**m, "score": float(scores[i])} for i, m in enumerate(meta)]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def find_competitive_gaps(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    top_k: int = 10,
) -> list[dict]:
    """Find competitor pages covering topics similar to an object."""
    row = conn.execute(
        "SELECT embedding FROM embeddings WHERE object_type = ? AND object_handle = ? AND chunk_index = 0",
        (object_type, handle),
    ).fetchone()
    if not row:
        return []

    query_vec = _blob_to_array(row["embedding"])
    matrix, meta = _load_embedding_matrix(conn, ["competitor_page"])
    if matrix.shape[0] == 0:
        return []

    scores = _cosine_similarity(query_vec, matrix)
    scored = [{**m, "score": float(scores[i])} for i, m in enumerate(meta)]
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:top_k]

    result = []
    for item in top:
        handle_parts = item["object_handle"].split(":", 1)
        if len(handle_parts) == 2:
            domain, url = handle_parts
            cp_row = conn.execute(
                "SELECT top_keyword, estimated_traffic, page_type FROM competitor_top_pages WHERE competitor_domain = ? AND url = ?",
                (domain, url),
            ).fetchone()
            if cp_row:
                result.append({
                    "competitor_domain": domain,
                    "top_keyword": cp_row["top_keyword"],
                    "estimated_traffic": cp_row["estimated_traffic"],
                    "page_type": cp_row["page_type"],
                    "score": item["score"],
                })
    return result


def find_cannibalization_candidates(
    conn: sqlite3.Connection,
    threshold: float = 0.85,
) -> list[dict]:
    """Find pairs of pages with high content AND query embedding similarity."""
    content_types = ["product", "collection", "page", "blog_article"]
    matrix, meta = _load_embedding_matrix(conn, content_types)
    if matrix.shape[0] < 2:
        return []

    norms = np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-10
    normed = matrix / norms
    sim_matrix = normed @ normed.T

    gsc_matrix, gsc_meta = _load_embedding_matrix(conn, ["gsc_queries"])
    gsc_lookup: dict[str, int] = {}
    for i, m in enumerate(gsc_meta):
        gsc_lookup[m["object_handle"]] = i

    gsc_normed = None
    if gsc_matrix.shape[0] > 0:
        gsc_norms = np.linalg.norm(gsc_matrix, axis=1, keepdims=True) + 1e-10
        gsc_normed = gsc_matrix / gsc_norms

    candidates = []
    n = len(meta)
    for i in range(n):
        for j in range(i + 1, n):
            content_sim = float(sim_matrix[i, j])
            if content_sim < threshold:
                continue

            a_key = f"{meta[i]['object_type']}:{meta[i]['object_handle']}"
            b_key = f"{meta[j]['object_type']}:{meta[j]['object_handle']}"
            query_sim = 0.0
            if gsc_normed is not None and a_key in gsc_lookup and b_key in gsc_lookup:
                ai, bi = gsc_lookup[a_key], gsc_lookup[b_key]
                query_sim = float(gsc_normed[ai] @ gsc_normed[bi])

            shared_queries = []
            if query_sim > 0.8:
                a_queries = {r["query"] for r in conn.execute(
                    "SELECT query FROM gsc_query_rows WHERE object_type = ? AND object_handle = ?",
                    (meta[i]["object_type"], meta[i]["object_handle"]),
                ).fetchall()}
                b_queries = {r["query"] for r in conn.execute(
                    "SELECT query FROM gsc_query_rows WHERE object_type = ? AND object_handle = ?",
                    (meta[j]["object_type"], meta[j]["object_handle"]),
                ).fetchall()}
                shared_queries = sorted(a_queries & b_queries)

            candidates.append({
                "object_a": {"type": meta[i]["object_type"], "handle": meta[i]["object_handle"]},
                "object_b": {"type": meta[j]["object_type"], "handle": meta[j]["object_handle"]},
                "content_similarity": round(content_sim, 4),
                "query_similarity": round(query_sim, 4),
                "shared_queries": shared_queries[:10],
            })

    candidates.sort(key=lambda x: x["content_similarity"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Weighted query for Sidekick (D5)
# ---------------------------------------------------------------------------

def build_sidekick_query_vector(
    conn: sqlite3.Connection,
    api_key: str,
    user_message: str,
    object_type: str,
    handle: str,
    object_title: str = "",
) -> np.ndarray | None:
    """Build a weighted query vector: 0.3*message + 0.7*object embedding."""
    msg_vecs = embed_batch(api_key, [user_message], task_type="RETRIEVAL_QUERY")
    if not msg_vecs:
        return None
    msg_vec = np.array(msg_vecs[0], dtype=np.float32)

    obj_row = conn.execute(
        "SELECT embedding FROM embeddings WHERE object_type = ? AND object_handle = ? AND chunk_index = 0",
        (object_type, handle),
    ).fetchone()

    if obj_row:
        obj_vec = _blob_to_array(obj_row["embedding"])
        return 0.3 * msg_vec + 0.7 * obj_vec

    fallback_text = f"{user_message} {object_title}".strip()
    fallback_vecs = embed_batch(api_key, [fallback_text], task_type="RETRIEVAL_QUERY")
    if fallback_vecs:
        return np.array(fallback_vecs[0], dtype=np.float32)
    return None


def embedding_status(conn: sqlite3.Connection) -> dict:
    """Return aggregate stats about the embedding table for the status page."""
    embed_rows = conn.execute("""
        SELECT object_type,
               COUNT(DISTINCT object_handle) AS object_count,
               COUNT(*) AS chunk_count,
               MAX(updated_at) AS last_updated,
               GROUP_CONCAT(DISTINCT model_version) AS model_versions
        FROM embeddings
        GROUP BY object_type
    """).fetchall()

    embed_by_type = {r["object_type"]: dict(r) for r in embed_rows}

    source_queries: dict[str, str] = {
        "product": "SELECT COUNT(*) FROM products WHERE status = 'ACTIVE'",
        "collection": "SELECT COUNT(*) FROM collections",
        "page": "SELECT COUNT(*) FROM pages",
        "blog_article": "SELECT COUNT(*) FROM blog_articles",
        "cluster": "SELECT COUNT(*) FROM clusters",
        "gsc_queries": "SELECT COUNT(DISTINCT object_type || ':' || object_handle) FROM gsc_query_rows",
        "keyword": "SELECT COUNT(*) FROM keyword_metrics WHERE status IN ('approved', 'new')",
        "article_idea": "SELECT COUNT(*) FROM article_ideas WHERE status != 'rejected'",
        "competitor_page": "SELECT COUNT(*) FROM competitor_top_pages",
    }

    types_list = []
    total_objects = 0
    total_chunks = 0
    global_last_updated = None

    for t in EMBEDDABLE_TYPES:
        try:
            source_count = conn.execute(source_queries[t]).fetchone()[0]
        except Exception:
            source_count = 0

        embed_info = embed_by_type.get(t, {})
        embedded = embed_info.get("object_count", 0)
        chunks = embed_info.get("chunk_count", 0)
        last_up = embed_info.get("last_updated")
        models = embed_info.get("model_versions", "")
        coverage = round(embedded / source_count * 100, 1) if source_count > 0 else 0.0

        total_objects += embedded
        total_chunks += chunks
        if last_up and (global_last_updated is None or last_up > global_last_updated):
            global_last_updated = last_up

        types_list.append({
            "type": t,
            "embedded_objects": embedded,
            "source_objects": source_count,
            "coverage_pct": coverage,
            "chunk_count": chunks,
            "last_updated": last_up,
            "model_versions": models,
        })

    api_key_row = conn.execute(
        "SELECT value FROM service_settings WHERE key = 'gemini_api_key'"
    ).fetchone()
    api_key_configured = bool(api_key_row and (api_key_row["value"] or "").strip())

    return {
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMS,
        "total_embeddings": total_objects,
        "total_chunks": total_chunks,
        "last_updated": global_last_updated,
        "api_key_configured": api_key_configured,
        "types": types_list,
    }


def retrieve_for_sidekick(
    conn: sqlite3.Connection,
    api_key: str,
    user_message: str,
    object_type: str,
    handle: str,
    object_title: str = "",
    top_k: int = 3,
) -> list[dict]:
    """Retrieve related content for Sidekick using weighted query vector."""
    query_vec = build_sidekick_query_vector(conn, api_key, user_message, object_type, handle, object_title)
    if query_vec is None:
        return []

    matrix, meta = _load_embedding_matrix(conn, exclude=(object_type, handle))
    if matrix.shape[0] == 0:
        return []

    scores = _cosine_similarity(query_vec, matrix)
    scored = [{**m, "score": float(scores[i])} for i, m in enumerate(meta)]
    scored.sort(key=lambda x: x["score"], reverse=True)
    scored = _dedup_by_handle(scored)
    return scored[:top_k]
