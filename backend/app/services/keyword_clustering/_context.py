"""Cluster context lookup — format matched clusters for LLM prompts."""
import sqlite3

_MIN_VENDOR_LENGTH = 3


def _tiered_cluster_keywords(cluster: dict, *, include_extended: bool = False) -> list[str]:
    """Return generation-safe cluster keywords, falling back to raw keywords."""
    out: list[str] = []
    for field in ("primary_keyword",):
        text = str(cluster.get(field) or "").strip()
        if text:
            out.append(text)
    tier_fields = ["core_keywords", "supporting_keywords"]
    if include_extended:
        tier_fields.append("extended_keywords")
    for field in tier_fields:
        for kw in cluster.get(field) or []:
            text = str(kw or "").strip()
            if text and text.lower() not in {x.lower() for x in out}:
                out.append(text)
    if len(out) <= 1:
        for kw in cluster.get("keywords", [])[:24]:
            text = str(kw or "").strip()
            if text and text.lower() not in {x.lower() for x in out}:
                out.append(text)
    return out


def _format_cluster_context(
    matched_clusters: list[dict],
    target_data: dict,
) -> str | None:
    """Format matched clusters into a context string for the LLM prompt.

    Builds a human-readable block per cluster listing primary keyword (with
    volume/difficulty), supporting keywords, content angle, and recommended
    content type.  Returns None when *matched_clusters* is empty.
    """
    if not matched_clusters:
        return None

    # Build keyword metrics lookup from target keywords
    kw_map: dict[str, dict] = {}
    for item in target_data.get("items") or []:
        kw_map[item.get("keyword", "").lower()] = item

    sections: list[str] = []
    for cluster in matched_clusters:
        primary_kw = cluster.get("primary_keyword", "")
        primary_metrics = kw_map.get(primary_kw.lower(), {})
        primary_vol = primary_metrics.get("volume", 0) or 0
        primary_diff = primary_metrics.get("difficulty", 0) or 0

        supporting = []
        for kw in _tiered_cluster_keywords(cluster):
            if kw.lower() == primary_kw.lower():
                continue
            m = kw_map.get(kw.lower(), {})
            vol = m.get("volume", 0) or 0
            diff = m.get("difficulty", 0) or 0
            supporting.append(f"{kw} (vol: {vol}, diff: {diff})")

        lines = [
            f'SEO Target Keywords (from cluster "{cluster.get("name", "")}"):',
            f'- Primary keyword: "{primary_kw}" (volume: {primary_vol}, difficulty: {primary_diff})',
        ]
        if supporting:
            lines.append(f"- Supporting keywords: {', '.join(supporting)}")
        lines.append(f"- Content angle: {cluster.get('content_brief', '')}")
        lines.append(f"- Recommended content type: {cluster.get('content_type', '')}")
        stats = cluster.get("stats") or {}
        if stats.get("dominant_serp_features"):
            lines.append(f"- Dominant SERP features: {stats['dominant_serp_features']}")
        if stats.get("content_format_hints"):
            lines.append(f"- Suggested content format: {stats['content_format_hints']}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _find_clusters_for_product(
    conn: sqlite3.Connection,
    product_handle: str,
    product_vendor: str,
    clusters_data: dict,
) -> list[dict]:
    """Reverse-lookup: find up to 3 clusters related to a product.

    Discovery paths (priority order):
    1. Vendor match — cluster name or keywords contain the product vendor name
    2. Collection membership — product belongs to a collection that a cluster
       points to via suggested_match

    Deduplicates by cluster id.  Vendor matches appear first (higher priority).
    """
    clusters = clusters_data.get("clusters") or []
    if not clusters:
        return []

    matched: list[dict] = []
    seen_ids: set[int] = set()

    # --- Path 1: Vendor match ---
    vendor_lower = product_vendor.strip().lower()
    if len(vendor_lower) >= _MIN_VENDOR_LENGTH:
        for cluster in clusters:
            if len(matched) >= 3:
                break
            cid = cluster.get("id")
            if cid in seen_ids:
                continue
            name_lower = cluster.get("name", "").lower()
            kws_lower = [kw.lower() for kw in cluster.get("keywords", [])]
            if vendor_lower in name_lower or any(vendor_lower in kw for kw in kws_lower):
                matched.append(cluster)
                seen_ids.add(cid)

    # --- Path 2: Collection membership ---
    if len(matched) < 3:
        collection_handles = {
            row[0]
            for row in conn.execute(
                """SELECT c.handle FROM collections c
                   JOIN collection_products cp ON c.shopify_id = cp.collection_shopify_id
                   JOIN products p ON p.shopify_id = cp.product_shopify_id
                   WHERE p.handle = ?""",
                (product_handle,),
            ).fetchall()
        }
        if collection_handles:
            for cluster in clusters:
                if len(matched) >= 3:
                    break
                cid = cluster.get("id")
                if cid in seen_ids:
                    continue
                sm = cluster.get("suggested_match")
                if not sm:
                    continue
                if sm.get("match_type") == "collection" and sm.get("match_handle") in collection_handles:
                    matched.append(cluster)
                    seen_ids.add(cid)

    return matched


def _load_cluster_context(
    clusters_data: dict,
    target_data: dict,
    object_type: str,
    handle: str,
) -> str | None:
    """Format matched cluster keywords as a context string for content generation.

    Takes pre-loaded data dicts (not a db connection) so the function is pure
    and testable. Returns None if no clusters match.
    """
    clusters = clusters_data.get("clusters") or []
    if not clusters:
        return None

    # Find matching clusters
    matched: list[dict] = []
    for cluster in clusters:
        sm = cluster.get("suggested_match")
        if not sm:
            continue
        if sm.get("match_handle") != handle:
            continue
        if sm.get("match_type") != object_type:
            continue
        matched.append(cluster)
        if len(matched) >= 3:
            break

    if not matched:
        return None

    return _format_cluster_context(matched, target_data)


def _get_matched_cluster_keywords(
    clusters_data: dict,
    target_data: dict,
    object_type: str,
    handle: str,
    conn: sqlite3.Connection | None = None,
    vendor: str = "",
) -> tuple[str | None, list[str], str, dict[str, dict]]:
    """Load cluster context and return raw keyword data alongside the formatted string.

    Returns (formatted_context, all_cluster_keywords, primary_keyword, kw_metrics_map).
    """
    clusters = clusters_data.get("clusters") or []
    if not clusters:
        return None, [], "", {}

    matched: list[dict] = []
    for cluster in clusters:
        sm = cluster.get("suggested_match")
        if not sm:
            continue
        if sm.get("match_handle") != handle:
            continue
        if sm.get("match_type") != object_type:
            continue
        matched.append(cluster)
        if len(matched) >= 3:
            break

    if not matched and object_type == "product" and conn is not None and vendor:
        matched = _find_clusters_for_product(conn, handle, vendor, clusters_data)

    if not matched:
        return None, [], "", {}

    kw_map: dict[str, dict] = {}
    for item in target_data.get("items") or []:
        kw_map[item.get("keyword", "").lower()] = item

    all_kws: list[str] = []
    primary_kw = ""
    seen: set[str] = set()
    for cluster in matched:
        pk = cluster.get("primary_keyword", "")
        if pk and not primary_kw:
            primary_kw = pk
        for kw in _tiered_cluster_keywords(cluster):
            kl = kw.lower()
            if kl not in seen:
                seen.add(kl)
                all_kws.append(kw)

    formatted = _format_cluster_context(matched, target_data)
    return formatted, all_kws, primary_kw, kw_map
