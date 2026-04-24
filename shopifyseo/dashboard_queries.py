"""Database query helpers used across the dashboard modules."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

from .dashboard_article_ideas import (  # noqa: F401
    bulk_delete_article_ideas,
    bulk_update_idea_status,
    compute_idea_performance,
    compute_keyword_coverage,
    delete_article_idea,
    fetch_article_idea_inputs,
    fetch_article_ideas,
    fetch_idea_articles,
    link_idea_to_article,
    refresh_article_idea_serp_snapshot,
    resolve_idea_targets,
    save_article_ideas,
    save_article_target_keywords,
    update_article_idea_status,
)
from .dashboard_insights import blended_opportunity, opportunity_priority
from .dashboard_status import index_status_info


# ---------------------------------------------------------------------------
# Store URL helpers
# ---------------------------------------------------------------------------

_BASE_URL_CACHE: str | None = None

# Tables that carry SEO signal columns (gsc_*, ga4_*, index_*, pagespeed_*).
_SEO_SIGNAL_TABLES: tuple[str, ...] = ("products", "collections", "pages", "blog_articles")

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


def _base_store_url(conn: sqlite3.Connection | None = None) -> str:
    """Return the canonical storefront base URL (no trailing slash).

    Single source of truth: the ``store_custom_domain`` setting on the
    Settings page (persisted in the DB, mirrored to SHOPIFY_STORE_URL env).
    Falls back to SHOPIFY_SHOP only when no custom domain is configured.
    """
    global _BASE_URL_CACHE
    if _BASE_URL_CACHE:
        return _BASE_URL_CACHE

    # 1. Check DB setting (the authoritative source)
    custom = ""
    try:
        from . import dashboard_google as _dg
        _c = conn
        _own = _c is None
        if _own:
            from .dashboard_store import db_connect
            _c = db_connect()
        try:
            custom = (_dg.get_service_setting(_c, "store_custom_domain") or "").strip().rstrip("/")
        finally:
            if _own:
                _c.close()
    except Exception:
        pass

    if not custom:
        custom = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")

    if custom:
        if not custom.startswith("http"):
            custom = f"https://{custom}"
        _BASE_URL_CACHE = custom
        return _BASE_URL_CACHE

    # Fallback: derive from SHOPIFY_SHOP env var
    shop = os.getenv("SHOPIFY_SHOP", "").strip().rstrip("/")
    if shop:
        shop = shop.replace("https://", "").replace("http://", "").rstrip("/")
        if not shop.endswith(".myshopify.com"):
            shop = f"{shop}.myshopify.com"
        _BASE_URL_CACHE = f"https://{shop}"
        return _BASE_URL_CACHE

    _BASE_URL_CACHE = ""
    return _BASE_URL_CACHE


_OBJECT_PATH_PREFIX: dict[str, str] = {
    "product": "/products",
    "collection": "/collections",
    "page": "/pages",
    "blog": "/blogs",
}


def blog_article_composite_handle(blog_handle: str, article_handle: str) -> str:
    return f"{blog_handle}/{article_handle}"


def object_url(object_type: str, handle: str) -> str:
    """Return the canonical URL for a store object."""
    base = _base_store_url()
    return object_url_with_base(base, object_type, handle)


def object_url_with_base(base_url: str, object_type: str, handle: str) -> str:
    """Return storefront URL for *handle* using explicit *base_url* (no trailing slash).

    *object_type* is ``collection``, ``product``, ``page``, or ``blog_article``.
    Blog handles use ``blog_handle/article_slug`` composite form.
    If *base_url* is empty, returns a root-relative path (starts with ``/``).
    """
    base = (base_url or "").strip().rstrip("/")
    if object_type == "blog_article":
        blog_h, sep, article_h = handle.partition("/")
        if sep and article_h:
            path = f"/blogs/{blog_h}/{article_h}"
        else:
            path = f"/blogs/{handle}"
        return f"{base}{path}" if base else path
    prefix = _OBJECT_PATH_PREFIX.get(object_type, "")
    if not prefix:
        return base or ""
    path = f"{prefix}/{handle}"
    return f"{base}{path}" if base else path


DEFAULT_INTERNAL_LINK_CAPS: dict[str, int] = {
    "collection": 24,
    "product": 40,
    "page": 20,
    "blog_article": 20,
}


def build_store_internal_link_allowlist(
    conn: sqlite3.Connection,
    base_url: str,
    *,
    rag_results: list[dict] | None = None,
    caps: dict[str, int] | None = None,
) -> tuple[list[dict], frozenset[str], frozenset[str]]:
    """Build canonical internal link targets for prompts and HTML sanitization.

    Returns ``(targets, allowed_full_urls, allowed_paths)`` where *targets* are
    dicts ``{"type", "handle", "title", "url"}`` sorted for prompt injection
    (RAG hits first per type, then alphabetical DB fill up to caps).

    *allowed_full_urls* includes every ``url`` plus alternate forms (e.g. with
    trailing slash stripped). *allowed_paths* is normalized path keys like
    ``/collections/foo`` (no trailing slash).
    """
    caps = {**DEFAULT_INTERNAL_LINK_CAPS, **(caps or {})}
    rag_results = rag_results or []
    base = (base_url or "").strip().rstrip("/")

    def _blog_composite(bh: str, ah: str) -> str:
        return f"{bh}/{ah}"

    collections: list[tuple[str, str]] = []
    products: list[tuple[str, str]] = []
    pages: list[tuple[str, str]] = []
    blogs: list[tuple[str, str, str]] = []
    try:
        for r in conn.execute(
            "SELECT handle, title FROM collections WHERE handle IS NOT NULL AND TRIM(handle) != '' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            h = (r[0] or "").strip()
            if h:
                collections.append((h, (r[1] or h).strip() or h))
        for r in conn.execute(
            "SELECT handle, title FROM products WHERE handle IS NOT NULL AND TRIM(handle) != '' "
            "AND (status IS NULL OR status = '' OR UPPER(status) = 'ACTIVE') "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            h = (r[0] or "").strip()
            if h:
                products.append((h, (r[1] or h).strip() or h))
        for r in conn.execute(
            "SELECT handle, title FROM pages WHERE handle IS NOT NULL AND TRIM(handle) != '' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            h = (r[0] or "").strip()
            if h:
                pages.append((h, (r[1] or h).strip() or h))
        for r in conn.execute(
            "SELECT blog_handle, handle, title FROM blog_articles "
            "WHERE blog_handle IS NOT NULL AND TRIM(blog_handle) != '' "
            "AND handle IS NOT NULL AND TRIM(handle) != '' "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall():
            bh = (r[0] or "").strip()
            ah = (r[1] or "").strip()
            if bh and ah:
                blogs.append((bh, ah, (r[2] or ah).strip() or ah))
    except Exception:
        collections, products, pages, blogs = [], [], [], []

    coll_map = {h: t for h, t in collections}
    prod_map = {h: t for h, t in products}
    page_map = {h: t for h, t in pages}
    blog_map = {_blog_composite(bh, ah): (bh, ah, title) for bh, ah, title in blogs}

    rag_priority: dict[str, list[str]] = {"collection": [], "product": [], "blog_article": []}
    for r in rag_results:
        ot = r.get("object_type")
        oh = (r.get("object_handle") or "").strip()
        if ot in rag_priority and oh and oh not in rag_priority[ot]:
            rag_priority[ot].append(oh)

    def _pick(
        kind: str,
        rag_handles: list[str],
        id_set: set[str],
        ordered_items: list[tuple],
        cap: int,
        url_type: str,
    ) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()

        def _add(handle_key: str, title: str) -> None:
            if handle_key in seen or len(out) >= cap:
                return
            if handle_key not in id_set:
                return
            seen.add(handle_key)
            url = object_url_with_base(base, url_type, handle_key)
            out.append({"type": url_type, "handle": handle_key, "title": title, "url": url})

        for h in rag_handles:
            if kind == "blog":
                if h in blog_map:
                    _bh, _ah, title = blog_map[h]
                    _add(h, title)
            elif h in id_set:
                title = (coll_map if kind == "coll" else prod_map if kind == "prod" else page_map).get(h, h)
                _add(h, title)

        for item in ordered_items:
            if len(out) >= cap:
                break
            if kind == "blog":
                bh, ah, title = item
                key = _blog_composite(bh, ah)
                _add(key, title)
            else:
                h, title = item
                _add(h, title)
        return out

    targets: list[dict] = []
    targets.extend(
        _pick(
            "coll",
            rag_priority["collection"],
            set(coll_map),
            collections,
            caps.get("collection", 24),
            "collection",
        )
    )
    targets.extend(
        _pick(
            "prod",
            rag_priority["product"],
            set(prod_map),
            products,
            caps.get("product", 40),
            "product",
        )
    )
    targets.extend(
        _pick("page", [], set(page_map), pages, caps.get("page", 20), "page")
    )
    targets.extend(
        _pick(
            "blog",
            rag_priority["blog_article"],
            set(blog_map),
            blogs,
            caps.get("blog_article", 20),
            "blog_article",
        )
    )

    allowed_full: set[str] = set()
    allowed_paths: set[str] = set()
    from urllib.parse import urlparse

    for t in targets:
        u = (t.get("url") or "").strip()
        if not u:
            continue
        allowed_full.add(u)
        allowed_full.add(u.rstrip("/"))
        if u.startswith("http"):
            p = urlparse(u).path or ""
            if p:
                allowed_paths.add(p.rstrip("/") or "/")
        elif u.startswith("/"):
            allowed_paths.add(u.rstrip("/") or "/")

    return targets, frozenset(allowed_full), frozenset(allowed_paths)


# ---------------------------------------------------------------------------
# Row factory helper
# ---------------------------------------------------------------------------

def _row_factory(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------

def fetch_all_products(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM products ORDER BY title").fetchall()


def fetch_all_collections(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM collections ORDER BY title").fetchall()


def fetch_all_pages(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM pages ORDER BY title").fetchall()


def fetch_all_blogs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM blogs ORDER BY title").fetchall()


def fetch_all_blog_articles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM blog_articles ORDER BY blog_handle, title"
    ).fetchall()


def fetch_all_blog_articles_enriched(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All articles with blog title for cross-blog listings."""
    return conn.execute(
        """
        SELECT a.*, b.title AS blog_title
        FROM blog_articles a
        LEFT JOIN blogs b ON b.handle = a.blog_handle
        ORDER BY COALESCE(a.published_at, a.updated_at, '') DESC, b.title, a.title
        """
    ).fetchall()


def fetch_blog_by_handle(conn: sqlite3.Connection, handle: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM blogs WHERE handle = ?", (handle,)).fetchone()


def fetch_articles_by_blog_handle(conn: sqlite3.Connection, blog_handle: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM blog_articles
        WHERE blog_handle = ?
        ORDER BY COALESCE(published_at, updated_at, '') DESC, title
        """,
        (blog_handle,),
    ).fetchall()


def count_blog_articles_missing_meta(conn: sqlite3.Connection) -> int:
    """Articles missing SEO title or description (either field absent counts as incomplete)."""
    row = conn.execute(
        """
        SELECT COUNT(*) FROM blog_articles
        WHERE (seo_title IS NULL OR seo_title = '')
           OR (seo_description IS NULL OR seo_description = '')
        """
    ).fetchone()
    return int(row[0]) if row else 0


def fetch_counts(conn: sqlite3.Connection) -> dict[str, int]:
    def _count(table: str) -> int:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return row[0] if row else 0

    return {
        "products": _count("products"),
        "variants": _count("product_variants"),
        "images": _count("product_images"),
        "product_metafields": _count("product_metafields"),
        "collections": _count("collections"),
        "collection_metafields": _count("collection_metafields"),
        "collection_products": _count("collection_products"),
        "pages": _count("pages"),
        "blogs": _count("blogs"),
        "blog_articles": _count("blog_articles"),
    }


def fetch_recent_runs(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def fetch_overview_metrics(conn: sqlite3.Connection) -> dict[str, int]:
    """Return aggregated metrics matching the OverviewMetrics schema."""
    products_missing_meta = conn.execute(
        "SELECT COUNT(*) FROM products WHERE (seo_title IS NULL OR seo_title = '') OR (seo_description IS NULL OR seo_description = '')"
    ).fetchone()[0]
    products_thin_body = conn.execute(
        "SELECT COUNT(*) FROM products WHERE description_html IS NULL OR LENGTH(description_html) < 200"
    ).fetchone()[0]
    collections_missing_meta = conn.execute(
        "SELECT COUNT(*) FROM collections WHERE (seo_title IS NULL OR seo_title = '') OR (seo_description IS NULL OR seo_description = '')"
    ).fetchone()[0]
    pages_missing_meta = conn.execute(
        "SELECT COUNT(*) FROM pages WHERE (seo_title IS NULL OR seo_title = '') OR (seo_description IS NULL OR seo_description = '')"
    ).fetchone()[0]

    try:
        gsc_pages = conn.execute(
            "SELECT COUNT(DISTINCT object_handle) FROM gsc_query_rows"
        ).fetchone()[0]
        row = conn.execute(
            " UNION ALL ".join(
                f"SELECT COALESCE(SUM(gsc_clicks),0), COALESCE(SUM(gsc_impressions),0) FROM {t}"
                for t in _SEO_SIGNAL_TABLES
            )
        ).fetchall()
        gsc_clicks = sum(r[0] for r in row)
        gsc_impressions = sum(r[1] for r in row)
    except Exception:
        gsc_pages = gsc_clicks = gsc_impressions = 0

    try:
        ga4_row = conn.execute(
            " UNION ALL ".join(
                f"SELECT COUNT(CASE WHEN ga4_views>0 THEN 1 END), COALESCE(SUM(ga4_sessions),0), COALESCE(SUM(ga4_views),0) FROM {t}"
                for t in _SEO_SIGNAL_TABLES
            )
        ).fetchall()
        ga4_pages = sum(r[0] for r in ga4_row)
        ga4_sessions = sum(r[1] for r in ga4_row)
        ga4_views = sum(r[2] for r in ga4_row)
    except Exception:
        ga4_pages = ga4_sessions = ga4_views = 0

    return {
        "products_missing_meta": int(products_missing_meta),
        "products_thin_body": int(products_thin_body),
        "collections_missing_meta": int(collections_missing_meta),
        "pages_missing_meta": int(pages_missing_meta),
        "gsc_pages": int(gsc_pages),
        "gsc_clicks": int(gsc_clicks),
        "gsc_impressions": int(gsc_impressions),
        "ga4_pages": int(ga4_pages),
        "ga4_sessions": int(ga4_sessions),
        "ga4_views": int(ga4_views),
    }


def fetch_top_organic_pages(conn: sqlite3.Connection, limit: int = 10) -> list[dict[str, Any]]:
    """Return the top N entities ranked by GSC clicks across all entity types."""
    rows = conn.execute(
        """
        SELECT entity_type, handle, title,
               gsc_clicks, gsc_impressions, gsc_ctr, gsc_position
        FROM (
            SELECT 'product'      AS entity_type, handle, title,
                   COALESCE(gsc_clicks, 0)      AS gsc_clicks,
                   COALESCE(gsc_impressions, 0) AS gsc_impressions,
                   COALESCE(gsc_ctr, 0.0)       AS gsc_ctr,
                   gsc_position
            FROM products WHERE gsc_clicks > 0
            UNION ALL
            SELECT 'collection'   AS entity_type, handle, title,
                   COALESCE(gsc_clicks, 0),
                   COALESCE(gsc_impressions, 0),
                   COALESCE(gsc_ctr, 0.0),
                   gsc_position
            FROM collections WHERE gsc_clicks > 0
            UNION ALL
            SELECT 'page'         AS entity_type, handle, title,
                   COALESCE(gsc_clicks, 0),
                   COALESCE(gsc_impressions, 0),
                   COALESCE(gsc_ctr, 0.0),
                   gsc_position
            FROM pages WHERE gsc_clicks > 0
            UNION ALL
            SELECT 'blog_article' AS entity_type,
                   blog_handle || '/' || handle AS handle,
                   title,
                   COALESCE(gsc_clicks, 0),
                   COALESCE(gsc_impressions, 0),
                   COALESCE(gsc_ctr, 0.0),
                   gsc_position
            FROM blog_articles WHERE gsc_clicks > 0
        )
        ORDER BY gsc_clicks DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        entity_type = row[0]
        handle = row[1]
        result.append(
            {
                "entity_type": entity_type,
                "handle": handle,
                "title": row[2] or "",
                "gsc_clicks": int(row[3]),
                "gsc_impressions": int(row[4]),
                "gsc_ctr": float(row[5]),
                "gsc_position": float(row[6]) if row[6] is not None else None,
                "url": object_url(entity_type, handle),
            }
        )
    return result


# ---------------------------------------------------------------------------
# SEO scoring
# ---------------------------------------------------------------------------

def _seo_base_score(object_type: str, obj: dict[str, Any], product_count: int = 0) -> tuple[int, list[str]]:
    """Compute a base SEO score (0-100) where higher = more issues (opportunity score)."""
    reasons: list[str] = []
    deductions = 0

    title = (obj.get("title") or "").strip()
    seo_title = (obj.get("seo_title") or "").strip()
    seo_description = (obj.get("seo_description") or "").strip()

    if object_type in ("product", "collection"):
        body = (obj.get("description_html") or "").strip()
    else:
        body = (obj.get("body") or "").strip()

    if not seo_title:
        deductions += 30
        reasons.append("missing SEO title")
    elif len(seo_title) < 20:
        deductions += 15
        reasons.append("SEO title too short")
    elif len(seo_title) > 65:
        deductions += 10
        reasons.append("SEO title too long")

    if not seo_description:
        deductions += 25
        reasons.append("missing meta description")
    elif len(seo_description) < 50:
        deductions += 10
        reasons.append("meta description too short")
    elif len(seo_description) > 160:
        deductions += 5
        reasons.append("meta description too long")

    if not body:
        deductions += 25
        reasons.append("no body content")
    elif len(body) < 200:
        deductions += 15
        reasons.append("thin body content")
    elif len(body) < 500:
        deductions += 5
        reasons.append("body content could be expanded")

    if object_type == "collection" and product_count == 0:
        deductions += 10
        reasons.append("empty collection")

    index_status = (obj.get("index_status") or "").lower()
    if index_status and "indexed" not in index_status:
        deductions += 20
        reasons.append("not indexed")

    score = min(100, max(0, deductions))
    return score, reasons


def build_seo_fact(
    object_type: str,
    obj: Any,
    workflow: Any,
    recommendation: Any,
    product_count: int = 0,
) -> dict[str, Any]:
    """Build a single SEO fact dict for scoring/prioritization."""
    if isinstance(obj, sqlite3.Row):
        obj = dict(obj)
    if isinstance(workflow, sqlite3.Row):
        workflow = dict(workflow)
    if isinstance(recommendation, sqlite3.Row):
        recommendation = dict(recommendation)

    base_score, reasons = _seo_base_score(object_type, obj, product_count)
    handle = obj.get("handle", "")

    return {
        "object_type": object_type,
        "handle": handle,
        "url": object_url(object_type, handle),
        "title": obj.get("title") or "",
        "score": base_score,
        "priority": opportunity_priority(base_score),
        "reasons": reasons,
        "body_length": len((obj.get("description_html") or obj.get("body") or "")),
        "gsc_clicks": int(obj.get("gsc_clicks") or 0),
        "gsc_impressions": int(obj.get("gsc_impressions") or 0),
        "gsc_ctr": float(obj.get("gsc_ctr") or 0),
        "gsc_position": float(obj.get("gsc_position") or 0),
        "ga4_sessions": int(obj.get("ga4_sessions") or 0),
        "ga4_views": int(obj.get("ga4_views") or 0),
        "ga4_avg_session_duration": float(obj.get("ga4_avg_session_duration") or 0),
        "index_status": obj.get("index_status") or "",
        "index_coverage": obj.get("index_coverage") or "",
        "google_canonical": obj.get("google_canonical") or "",
        "pagespeed_performance": obj.get("pagespeed_performance"),
        "pagespeed_status": obj.get("pagespeed_status") or "",
        "pagespeed_desktop_performance": obj.get("pagespeed_desktop_performance"),
        "pagespeed_desktop_status": obj.get("pagespeed_desktop_status") or "",
        "workflow": dict(workflow) if workflow else {"status": "Needs fix", "notes": ""},
        "product_count": product_count,
    }


def fetch_seo_facts(conn: sqlite3.Connection, kind: str | None = None) -> list[dict[str, Any]]:
    """Return SEO facts for all objects (or a specific kind).

    kind: None = all, 'product', 'collection', 'page'
    """
    facts: list[dict[str, Any]] = []

    def _load_workflow(object_type: str) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT handle, status, notes FROM seo_workflow_states WHERE object_type = ?",
            (object_type,),
        ).fetchall()
        return {row["handle"]: {"status": row["status"], "notes": row["notes"]} for row in rows}

    def _load_recommendation(object_type: str) -> dict[str, Any]:
        rows = conn.execute(
            """
            SELECT object_handle, summary, details_json, status, model, created_at
            FROM seo_recommendations
            WHERE object_type = ?
            ORDER BY created_at DESC
            """,
            (object_type,),
        ).fetchall()
        seen: dict[str, dict] = {}
        for row in rows:
            h = row["object_handle"]
            if h not in seen:
                seen[h] = dict(row)
        return seen

    if kind is None or kind == "product":
        workflows = _load_workflow("product")
        recs = _load_recommendation("product")
        for row in fetch_all_products(conn):
            obj = dict(row)
            handle = obj["handle"]
            facts.append(build_seo_fact("product", obj, workflows.get(handle), recs.get(handle)))

    if kind is None or kind == "collection":
        workflows = _load_workflow("collection")
        recs = _load_recommendation("collection")
        product_counts: dict[str, int] = {}
        for pc_row in conn.execute(
            "SELECT c.handle, COUNT(cp.product_shopify_id) FROM collections c"
            " LEFT JOIN collection_products cp ON cp.collection_shopify_id = c.shopify_id"
            " GROUP BY c.handle"
        ).fetchall():
            product_counts[pc_row[0]] = pc_row[1]
        for row in fetch_all_collections(conn):
            obj = dict(row)
            handle = obj["handle"]
            facts.append(build_seo_fact("collection", obj, workflows.get(handle), recs.get(handle), product_count=product_counts.get(handle, 0)))

    if kind is None or kind == "page":
        workflows = _load_workflow("page")
        recs = _load_recommendation("page")
        for row in fetch_all_pages(conn):
            obj = dict(row)
            handle = obj["handle"]
            facts.append(build_seo_fact("page", obj, workflows.get(handle), recs.get(handle)))

    if kind is None or kind == "blog_article":
        workflows = _load_workflow("blog_article")
        recs = _load_recommendation("blog_article")
        for row in fetch_all_blog_articles(conn):
            obj = dict(row)
            blog_h = obj.get("blog_handle") or ""
            art_h = obj.get("handle") or ""
            composite = blog_article_composite_handle(blog_h, art_h)
            obj_for_fact = {**obj, "handle": composite}
            facts.append(build_seo_fact("blog_article", obj_for_fact, workflows.get(composite), recs.get(composite)))

    return facts


# ---------------------------------------------------------------------------
# Detail fetchers
# ---------------------------------------------------------------------------

def _recommendation_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a seo_recommendations row to a dict, parsing details_json."""
    d = dict(row)
    raw = d.pop("details_json", None)
    d["details"] = json.loads(raw) if raw else {}
    return d


def _fetch_recommendation(conn: sqlite3.Connection, object_type: str, handle: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, summary, details_json, status, model, prompt_version, error_message, created_at, source
        FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ? AND status = 'success'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (object_type, handle),
    ).fetchone()
    return _recommendation_row_to_dict(row) if row else None


def _fetch_recommendation_event(conn: sqlite3.Connection, object_type: str, handle: str) -> dict[str, Any] | None:
    """Fetch the latest recommendation event (including errors/pending)."""
    row = conn.execute(
        """
        SELECT id, summary, details_json, status, model, prompt_version, error_message, created_at, source
        FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (object_type, handle),
    ).fetchone()
    return _recommendation_row_to_dict(row) if row else None


def _fetch_recommendation_history(conn: sqlite3.Connection, object_type: str, handle: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT summary, details_json, status, model, prompt_version, error_message, created_at, source
        FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (object_type, handle, limit),
    ).fetchall()
    result = []
    for row in rows:
        item = _recommendation_row_to_dict(row)
        item["priority"] = None
        result.append(item)
    return result


def _fetch_workflow(conn: sqlite3.Connection, object_type: str, handle: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT status, notes, updated_at FROM seo_workflow_states WHERE object_type = ? AND handle = ?",
        (object_type, handle),
    ).fetchone()
    return dict(row) if row else None


def fetch_product_detail(conn: sqlite3.Connection, handle: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM products WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    product = dict(row)

    variants = conn.execute(
        "SELECT * FROM product_variants WHERE product_shopify_id = ? ORDER BY position",
        (product["shopify_id"],),
    ).fetchall()
    metafields = conn.execute(
        "SELECT namespace, key, type, value FROM product_metafields WHERE product_shopify_id = ?",
        (product["shopify_id"],),
    ).fetchall()
    collections = conn.execute(
        """
        SELECT c.handle, c.title
        FROM collections c
        JOIN collection_products cp ON cp.collection_shopify_id = c.shopify_id
        WHERE cp.product_shopify_id = ?
        ORDER BY c.title
        """,
        (product["shopify_id"],),
    ).fetchall()
    product_images = conn.execute(
        """
        SELECT shopify_id, url, alt_text, position
        FROM product_images
        WHERE product_shopify_id = ?
        ORDER BY position ASC
        LIMIT 24
        """,
        (product["shopify_id"],),
    ).fetchall()

    return {
        "product": product,
        "variants": variants,
        "metafields": metafields,
        "collections": collections,
        "product_images": [dict(r) for r in product_images],
        "workflow": _fetch_workflow(conn, "product", handle),
        "recommendation": _fetch_recommendation(conn, "product", handle),
        "recommendation_event": _fetch_recommendation_event(conn, "product", handle),
        "recommendation_history": _fetch_recommendation_history(conn, "product", handle),
    }


def fetch_collection_detail(conn: sqlite3.Connection, handle: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM collections WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    collection = dict(row)

    products = conn.execute(
        """
        SELECT cp.product_handle, cp.product_title
        FROM collection_products cp
        WHERE cp.collection_shopify_id = ?
        ORDER BY cp.product_title
        """,
        (collection["shopify_id"],),
    ).fetchall()
    metafields = conn.execute(
        "SELECT namespace, key, type, value FROM collection_metafields WHERE collection_shopify_id = ?",
        (collection["shopify_id"],),
    ).fetchall()

    return {
        "collection": collection,
        "products": products,
        "metafields": metafields,
        "workflow": _fetch_workflow(conn, "collection", handle),
        "recommendation": _fetch_recommendation(conn, "collection", handle),
        "recommendation_event": _fetch_recommendation_event(conn, "collection", handle),
        "recommendation_history": _fetch_recommendation_history(conn, "collection", handle),
    }


def fetch_page_detail(conn: sqlite3.Connection, handle: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM pages WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    page = dict(row)

    page_tokens = _content_tokens_for_page(page)
    title_lower = (page.get("title") or "").lower()
    related_collections = _related_collections_by_token_overlap(
        conn, page_tokens, title_fallback_lower=title_lower, limit=10
    )
    related_products = _related_products_by_token_overlap(conn, page_tokens, limit=20)
    related_pages = _related_pages_by_token_overlap(
        conn, page_tokens, exclude_handle=handle, limit=10
    )

    return {
        "page": page,
        "related_collections": related_collections,
        "related_products": related_products,
        "related_pages": related_pages,
        "workflow": _fetch_workflow(conn, "page", handle),
        "recommendation": _fetch_recommendation(conn, "page", handle),
        "recommendation_event": _fetch_recommendation_event(conn, "page", handle),
        "recommendation_history": _fetch_recommendation_history(conn, "page", handle),
    }


def fetch_blog_article_detail(conn: sqlite3.Connection, blog_handle: str, article_handle: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM blog_articles WHERE blog_handle = ? AND handle = ?",
        (blog_handle, article_handle),
    ).fetchone()
    if not row:
        return None
    article = dict(row)
    composite = blog_article_composite_handle(blog_handle, article_handle)

    article_tokens = _content_tokens_for_blog_article(article)
    title_lower = (article.get("title") or "").lower()
    related_collections = _related_collections_by_token_overlap(
        conn, article_tokens, title_fallback_lower=title_lower, limit=10
    )
    related_products = _related_products_by_token_overlap(conn, article_tokens, limit=20)
    related_pages = _related_pages_by_token_overlap(conn, article_tokens, exclude_handle=None, limit=10)

    return {
        "article": article,
        "related_collections": related_collections,
        "related_products": related_products,
        "related_pages": related_pages,
        "workflow": _fetch_workflow(conn, "blog_article", composite),
        "recommendation": _fetch_recommendation(conn, "blog_article", composite),
        "recommendation_event": _fetch_recommendation_event(conn, "blog_article", composite),
        "recommendation_history": _fetch_recommendation_history(conn, "blog_article", composite),
    }


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def apply_saved_product_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    body_html: str = "",
    tags: str = "",
) -> None:
    """Update product fields in the local DB after an editor save."""
    tags_json_value = json.dumps([t.strip() for t in tags.split(",") if t.strip()]) if tags.strip() else ""
    conn.execute(
        """
        UPDATE products SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            description_html = CASE WHEN ? != '' THEN ? ELSE description_html END,
            tags_json = CASE WHEN ? != '' THEN ? ELSE tags_json END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            body_html, body_html,
            tags_json_value, tags_json_value,
            shopify_id,
        ),
    )
    conn.commit()


def apply_saved_collection_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    description_html: str = "",
) -> None:
    """Update collection fields in the local DB after an editor save."""
    conn.execute(
        """
        UPDATE collections SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            description_html = CASE WHEN ? != '' THEN ? ELSE description_html END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            description_html, description_html,
            shopify_id,
        ),
    )
    conn.commit()


def apply_saved_page_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    body_html: str = "",
) -> None:
    """Update page fields in the local DB after an editor save."""
    conn.execute(
        """
        UPDATE pages SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            body = CASE WHEN ? != '' THEN ? ELSE body END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            body_html, body_html,
            shopify_id,
        ),
    )
    conn.commit()


def apply_saved_blog_article_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    body_html: str = "",
) -> None:
    """Update blog article fields in the local DB after an editor save."""
    conn.execute(
        """
        UPDATE blog_articles SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            body = CASE WHEN ? != '' THEN ? ELSE body END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            body_html, body_html,
            shopify_id,
        ),
    )
    conn.commit()


def set_workflow_state(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    status: str = "Needs fix",
    notes: str = "",
) -> None:
    """Upsert the workflow state for an object."""
    conn.execute(
        """
        INSERT INTO seo_workflow_states (object_type, handle, status, notes, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(object_type, handle) DO UPDATE SET
            status = excluded.status,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (object_type, handle, status or "Needs fix", notes or ""),
    )
    conn.commit()


def fetch_gsc_query_dimension_rows(
    conn: sqlite3.Connection, object_type: str, object_handle: str
) -> list[dict[str, Any]]:
    """Rows from gsc_query_dimension_rows (query × country | device | searchAppearance)."""
    cur = _row_factory(conn).execute(
        """
        SELECT query, dimension_kind, dimension_value, clicks, impressions, ctr, position, fetched_at
        FROM gsc_query_dimension_rows
        WHERE object_type = ? AND object_handle = ?
        ORDER BY impressions DESC
        """,
        (object_type, object_handle),
    )
    return [dict(row) for row in cur.fetchall()]


def object_keys_with_dimensional_gsc(
    conn: sqlite3.Connection,
    keys: list[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Return (object_type, object_handle) pairs that have at least one Tier B dimensional row."""
    if not keys:
        return set()
    out: set[tuple[str, str]] = set()
    by_type: dict[str, list[str]] = {}
    for ot, h in keys:
        h = (h or "").strip()
        if not h:
            continue
        by_type.setdefault(ot, []).append(h)
    chunk_size = 400
    conn_rf = _row_factory(conn)
    try:
        for ot, handles in by_type.items():
            uniq = list(dict.fromkeys(handles))
            for i in range(0, len(uniq), chunk_size):
                chunk = uniq[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cur = conn_rf.execute(
                    f"""
                    SELECT DISTINCT object_handle FROM gsc_query_dimension_rows
                    WHERE object_type = ? AND object_handle IN ({placeholders})
                    """,
                    (ot, *chunk),
                )
                for row in cur.fetchall():
                    out.add((ot, row["object_handle"]))
    except sqlite3.OperationalError:
        return set()
    return out


def build_gsc_segment_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up cached dimensional GSC rows for product/content detail API + UI."""
    empty: dict[str, Any] = {
        "fetched_at": None,
        "device_mix": [],
        "top_countries": [],
        "search_appearances": [],
        "top_pairs": [],
    }
    if not rows:
        return empty

    fts = [int(r["fetched_at"]) for r in rows if r.get("fetched_at") is not None]
    fetched_at = max(fts) if fts else None

    def rollup_for_kind(kind: str, limit: int) -> tuple[list[dict[str, Any]], int]:
        acc: dict[str, dict[str, int]] = {}
        for r in rows:
            if (r.get("dimension_kind") or "") != kind:
                continue
            v = (r.get("dimension_value") or "").strip()
            if not v:
                continue
            if v not in acc:
                acc[v] = {"clicks": 0, "impressions": 0}
            acc[v]["clicks"] += int(r.get("clicks") or 0)
            acc[v]["impressions"] += int(r.get("impressions") or 0)
        total_imp = sum(x["impressions"] for x in acc.values()) or 1
        items = [
            {
                "segment": k,
                "clicks": v["clicks"],
                "impressions": v["impressions"],
                "share": round(v["impressions"] / total_imp, 4),
            }
            for k, v in acc.items()
        ]
        items.sort(key=lambda x: x["impressions"], reverse=True)
        return items[:limit], total_imp

    device_mix, _ = rollup_for_kind("device", 10)
    top_countries, _ = rollup_for_kind("country", 12)
    search_appearances, _ = rollup_for_kind("searchAppearance", 12)

    sorted_rows = sorted(rows, key=lambda x: int(x.get("impressions") or 0), reverse=True)
    top_pairs = [
        {
            "query": r.get("query") or "",
            "dimension_kind": r.get("dimension_kind") or "",
            "dimension_value": r.get("dimension_value") or "",
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "position": float(r.get("position") or 0),
        }
        for r in sorted_rows[:20]
    ]

    return {
        "fetched_at": fetched_at,
        "device_mix": device_mix,
        "top_countries": top_countries,
        "search_appearances": search_appearances,
        "top_pairs": top_pairs,
    }




