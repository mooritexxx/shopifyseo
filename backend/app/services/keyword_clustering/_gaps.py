"""SEO gap computation and cluster coverage enrichment."""
import logging
import re
import sqlite3

import shopifyseo.dashboard_queries as dq

from ._helpers import (
    _check_keyword_coverage,
    _detect_vendor,
    _keyword_present_in_clean_text,
    _suggested_match_object_key,
)

logger = logging.getLogger(__name__)

_RANKING_BOOST_STATUSES = frozenset({"quick_win", "striking_distance"})
_RANKING_BOOST = 20
_GAP_CAP = 8


def compute_seo_gaps(
    cluster_keywords: list[str],
    content_fields: dict[str, str],
    keyword_metrics: dict[str, dict],
    object_type: str = "",
    primary_keyword: str = "",
) -> dict | None:
    """Partition cluster keywords into covered / missing, rank missing by opportunity.

    Returns None when every keyword is already present (no gaps).
    """
    combined = " ".join(v for v in content_fields.values() if v)
    clean = re.sub(r"<[^>]+>", " ", combined).lower()

    already_present: list[str] = []
    missing: list[str] = []
    for kw in cluster_keywords:
        if _keyword_present_in_clean_text(kw, clean):
            already_present.append(kw)
        else:
            missing.append(kw)

    if not missing:
        return None

    scored: list[dict] = []
    for kw in missing:
        metrics = keyword_metrics.get(kw.lower(), {})
        opp = metrics.get("opportunity", 0) or 0
        status = metrics.get("ranking_status", "not_ranking") or "not_ranking"
        boost = _RANKING_BOOST if status in _RANKING_BOOST_STATUSES else 0
        scored.append({
            "keyword": kw,
            "opportunity": opp,
            "ranking_status": status,
            "_sort_score": opp + boost,
        })

    scored.sort(key=lambda x: x["_sort_score"], reverse=True)

    primary_lower = (primary_keyword or "").strip().lower()
    if primary_lower:
        for i, item in enumerate(scored):
            if item["keyword"].lower() == primary_lower and i != 0:
                scored.insert(0, scored.pop(i))
                break

    must_consider = [
        {"keyword": s["keyword"], "opportunity": s["opportunity"], "ranking_status": s["ranking_status"]}
        for s in scored[:_GAP_CAP]
    ]

    total = len(cluster_keywords)
    found_count = len(already_present)
    coverage_ratio = f"{found_count}/{total}"

    logger.info(
        "SEO gaps for %s: %d must_consider, %d already_present (coverage %s)",
        object_type,
        len(must_consider),
        found_count,
        coverage_ratio,
    )

    return {
        "must_consider": must_consider,
        "already_present": already_present,
        "coverage_ratio": coverage_ratio,
        "primary_keyword": primary_keyword,
    }


def enrich_clusters_with_coverage(conn: sqlite3.Connection, data: dict) -> dict:
    """Add keyword_coverage and matched_vendor to each cluster.

    keyword_coverage: union coverage across all related content — suggested_match
    page (collection/page/blog_article), vendor products when matched_vendor is
    set, and collection_products when match_type is collection. Content is
    concatenated and checked once; a keyword counts if it appears in any source.
    Product rows are deduplicated by handle when they appear via both vendor and
    collection. Shape: {"found": N, "total": M} or None when there is no
    scannable content (e.g. match_type 'new' and no vendor products).

    matched_vendor: detects if cluster name/keywords match a product vendor/brand.
    """
    clusters = data.get("clusters") or []
    if not clusters:
        return data

    # Load vendor data once: {vendor_lower: {"name": vendor, "product_count": N}}
    vendor_rows = conn.execute(
        "SELECT vendor, COUNT(*) FROM products WHERE vendor IS NOT NULL AND vendor != '' GROUP BY vendor"
    ).fetchall()
    vendor_map: dict[str, dict] = {}
    for row in vendor_rows:
        vendor_map[row[0].lower()] = {"name": row[0], "product_count": row[1]}

    # Batch-load content for matched pages to avoid N+1 queries
    content_cache: dict[tuple[str, str], str] = {}

    for cluster in clusters:
        # --- Vendor detection ---
        matched_vendor = _detect_vendor(
            cluster.get("name", ""),
            cluster.get("keywords", []),
            vendor_map,
        )
        cluster["matched_vendor"] = matched_vendor

        # --- Keyword coverage ---
        sm = cluster.get("suggested_match")
        match_type = sm.get("match_type") if sm else None
        match_handle = sm.get("match_handle", "") if sm else ""

        all_content_parts: list[str] = []

        # 1. Suggested match page content (cached)
        if match_type and match_type not in (None, "new"):
            cache_key = (match_type, match_handle)
            if cache_key not in content_cache:
                row = None
                if match_type == "collection":
                    row = conn.execute(
                        "SELECT seo_title, seo_description, description_html FROM collections WHERE handle = ?",
                        (match_handle,),
                    ).fetchone()
                elif match_type == "page":
                    row = conn.execute(
                        "SELECT seo_title, seo_description, body FROM pages WHERE handle = ?",
                        (match_handle,),
                    ).fetchone()
                elif match_type == "blog_article":
                    parts = match_handle.split("/", 1)
                    if len(parts) == 2:
                        row = conn.execute(
                            "SELECT seo_title, seo_description, body FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                            (parts[0], parts[1]),
                        ).fetchone()
                content_cache[cache_key] = " ".join(row[i] or "" for i in range(3)) if row else ""
            page_content = content_cache[cache_key]
            if page_content:
                all_content_parts.append(page_content)

        # 2. Vendor products (if matched_vendor is set)
        seen_product_handles: set[str] = set()
        if matched_vendor:
            vendor_products = conn.execute(
                "SELECT handle, title, seo_title, seo_description, description_html FROM products WHERE LOWER(vendor) = ?",
                (matched_vendor["name"].lower(),),
            ).fetchall()
            for vp in vendor_products:
                handle = vp[0]
                if handle not in seen_product_handles:
                    seen_product_handles.add(handle)
                    content = " ".join(vp[i] or "" for i in range(1, 5))
                    if content.strip():
                        all_content_parts.append(content)

        # 3. Collection products (if match_type is collection)
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
                handle = cp[0]
                if handle not in seen_product_handles:
                    seen_product_handles.add(handle)
                    content = " ".join(cp[i] or "" for i in range(1, 5))
                    if content.strip():
                        all_content_parts.append(content)

        if not all_content_parts:
            cluster["keyword_coverage"] = None
            continue

        combined_content = " ".join(all_content_parts)
        found, total = _check_keyword_coverage(cluster.get("keywords", []), combined_content)
        cluster["keyword_coverage"] = {"found": found, "total": total}

    gsc_keys: list[tuple[str, str]] = []
    for cluster in clusters:
        sk = _suggested_match_object_key(cluster.get("suggested_match"))
        if sk:
            gsc_keys.append(sk)
    dim_set = dq.object_keys_with_dimensional_gsc(conn, gsc_keys)
    for cluster in clusters:
        sk = _suggested_match_object_key(cluster.get("suggested_match"))
        cluster["gsc_segment_flags"] = {"has_dimensional": bool(sk and sk in dim_set)}

    return data
