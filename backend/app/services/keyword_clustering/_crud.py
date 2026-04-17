"""Cluster CRUD operations — match options, update match, and detail view."""
import logging
import sqlite3

import shopifyseo.dashboard_queries as dq

from ._helpers import _detect_vendor, _keyword_coverage_detail, _suggested_match_object_key
from ._storage import _cluster_stats_from_row, load_clusters

logger = logging.getLogger(__name__)


def get_match_options(conn: sqlite3.Connection) -> list[dict]:
    """Return flat list of available pages for the match override dropdown."""
    options: list[dict] = [
        {"match_type": "new", "match_handle": "", "match_title": "New content"},
        {"match_type": "none", "match_handle": "", "match_title": "No match"},
    ]

    collections = conn.execute("SELECT handle, title FROM collections ORDER BY title").fetchall()
    for row in collections:
        options.append({"match_type": "collection", "match_handle": row[0], "match_title": row[1]})

    pages = conn.execute("SELECT handle, title FROM pages ORDER BY title").fetchall()
    for row in pages:
        options.append({"match_type": "page", "match_handle": row[0], "match_title": row[1]})

    articles = conn.execute(
        "SELECT blog_handle, handle, title FROM blog_articles ORDER BY blog_handle, title"
    ).fetchall()
    for row in articles:
        options.append({
            "match_type": "blog_article",
            "match_handle": f"{row[0]}/{row[1]}",
            "match_title": row[2],
        })

    return options


def update_cluster_match(
    conn: sqlite3.Connection,
    cluster_id: int,
    match_type: str,
    match_handle: str,
    match_title: str,
) -> dict:
    """Update suggested_match for a single cluster by ID. Returns updated clusters payload."""
    row = conn.execute("SELECT id FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    if not row:
        raise ValueError(f"Cluster with id {cluster_id} not found")

    if match_type == "none":
        conn.execute(
            "UPDATE clusters SET match_type = NULL, match_handle = NULL, match_title = NULL WHERE id = ?",
            (cluster_id,),
        )
    else:
        conn.execute(
            "UPDATE clusters SET match_type = ?, match_handle = ?, match_title = ? WHERE id = ?",
            (match_type, match_handle, match_title, cluster_id),
        )
    conn.commit()

    try:
        from shopifyseo.embedding_store import sync_embeddings
        sync_embeddings(conn, object_type="cluster")
    except Exception:
        logging.getLogger(__name__).warning("Cluster embedding sync failed", exc_info=True)

    return load_clusters(conn)


def get_cluster_detail(conn: sqlite3.Connection, cluster_id: int) -> dict:
    """Load a single cluster with all auto-discovered related URLs and coverage.

    Discovery chain (priority order for deduplication):
    1. Suggested match (collection/page/blog_article)
    2. Vendor products (via matched_vendor)
    3. Collection products (via collection_products join)

    Raises ValueError if cluster_id not found.
    """
    row = conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    if not row:
        raise ValueError(f"Cluster with id {cluster_id} not found")

    keywords = [
        r[0] for r in conn.execute(
            "SELECT keyword FROM cluster_keywords WHERE cluster_id = ?", (cluster_id,)
        ).fetchall()
    ]

    match_type = row["match_type"]
    match_handle = row["match_handle"] or ""
    match_title = row["match_title"] or ""

    if match_type is None:
        suggested_match = None
    elif match_type == "new":
        suggested_match = {"match_type": "new", "match_handle": "", "match_title": ""}
    else:
        suggested_match = {
            "match_type": match_type,
            "match_handle": match_handle,
            "match_title": match_title,
        }

    # Detect vendor
    vendor_rows = conn.execute(
        "SELECT vendor, COUNT(*) FROM products WHERE vendor IS NOT NULL AND vendor != '' GROUP BY vendor"
    ).fetchall()
    vendor_map: dict[str, dict] = {}
    for vr in vendor_rows:
        vendor_map[vr[0].lower()] = {"name": vr[0], "product_count": vr[1]}
    matched_vendor = _detect_vendor(row["name"], keywords, vendor_map)

    cluster = {
        "id": row["id"],
        "name": row["name"],
        "content_type": row["content_type"],
        "primary_keyword": row["primary_keyword"],
        "content_brief": row["content_brief"],
        "keywords": keywords,
        "keyword_count": len(keywords),
        "total_volume": row["total_volume"],
        "avg_difficulty": row["avg_difficulty"],
        "avg_opportunity": row["avg_opportunity"],
        "suggested_match": suggested_match,
        "matched_vendor": matched_vendor,
    }
    detail_stats = _cluster_stats_from_row(row)
    if detail_stats:
        cluster["stats"] = detail_stats
    sm_key = _suggested_match_object_key(suggested_match)
    dim_one = dq.object_keys_with_dimensional_gsc(conn, [sm_key]) if sm_key else set()
    cluster["gsc_segment_flags"] = {"has_dimensional": bool(sm_key and sm_key in dim_one)}

    # --- Discovery chain ---
    related: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add_url(url_type: str, handle: str, title: str, source: str, content: str) -> None:
        key = (url_type, handle)
        if key in seen:
            return
        seen.add(key)
        cov = _keyword_coverage_detail(keywords, content)
        related.append({
            "url_type": url_type,
            "handle": handle,
            "title": title,
            "source": source,
            "keyword_coverage": {
                "found": cov["found"],
                "total": cov["total"],
                "keywords_found": cov["keywords_found"],
                "keywords_missing": cov["keywords_missing"],
            },
        })

    # 1. Suggested match
    if match_type and match_type not in ("new", "none"):
        if match_type == "collection":
            r = conn.execute(
                "SELECT seo_title, seo_description, description_html FROM collections WHERE handle = ?",
                (match_handle,),
            ).fetchone()
            if r:
                content = " ".join(r[i] or "" for i in range(3))
                _add_url("collection", match_handle, match_title, "suggested_match", content)
        elif match_type == "page":
            r = conn.execute(
                "SELECT seo_title, seo_description, body FROM pages WHERE handle = ?",
                (match_handle,),
            ).fetchone()
            if r:
                content = " ".join(r[i] or "" for i in range(3))
                _add_url("page", match_handle, match_title, "suggested_match", content)
        elif match_type == "blog_article":
            parts = match_handle.split("/", 1)
            if len(parts) == 2:
                r = conn.execute(
                    "SELECT seo_title, seo_description, body FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                    (parts[0], parts[1]),
                ).fetchone()
                if r:
                    content = " ".join(r[i] or "" for i in range(3))
                    _add_url("blog_article", match_handle, match_title, "suggested_match", content)

    # 2. Vendor products
    if matched_vendor:
        vendor_products = conn.execute(
            "SELECT handle, title, seo_title, seo_description, description_html FROM products WHERE LOWER(vendor) = ?",
            (matched_vendor["name"].lower(),),
        ).fetchall()
        for vp in vendor_products:
            content = " ".join(vp[i] or "" for i in range(1, 5))  # title + seo_title + seo_description + description_html
            _add_url("product", vp[0], vp[1], "vendor", content)

    # 3. Collection products (if match is a collection)
    if match_type == "collection" and match_handle:
        cp_rows = conn.execute(
            """SELECT p.handle, p.title, p.seo_title, p.seo_description, p.description_html
               FROM products p
               JOIN collection_products cp ON p.shopify_id = cp.product_shopify_id
               JOIN collections c ON cp.collection_shopify_id = c.shopify_id
               WHERE c.handle = ?""",
            (match_handle,),
        ).fetchall()
        for cp in cp_rows:
            content = " ".join(cp[i] or "" for i in range(1, 5))
            _add_url("product", cp[0], cp[1], "collection_products", content)

    # Sort by coverage descending
    related.sort(key=lambda u: u["keyword_coverage"]["found"], reverse=True)

    return {"cluster": cluster, "related_urls": related}
