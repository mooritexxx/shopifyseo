"""SEO scoring and fact-building.

The "fact" is the row-shape that drives the dashboard's prioritization /
opportunity views. Score is 0-100 where higher = more issues.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..dashboard_insights import opportunity_priority

from ._basic_fetchers import (
    fetch_blog_articles_for_facts,
    fetch_collections_for_facts,
    fetch_pages_for_facts,
    fetch_products_for_facts,
)
from ._urls import blog_article_composite_handle, object_url


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
    recommendation: Any = None,
    product_count: int = 0,
) -> dict[str, Any]:
    """Build a single SEO fact dict for scoring/prioritization.

    ``recommendation`` is accepted for call-site compatibility but does not
    affect the result -- no field below reads it. Do not add a query to supply
    it; ``fetch_seo_facts`` used to load every stored recommendation (4.4 MB of
    ``details_json`` for products alone) only to discard it here.
    """
    if isinstance(obj, sqlite3.Row):
        obj = dict(obj)
    if isinstance(workflow, sqlite3.Row):
        workflow = dict(workflow)

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


def fetch_seo_facts(
    conn: sqlite3.Connection,
    kind: str | None = None,
    *,
    rows: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Return SEO facts for all objects (or a specific kind).

    kind: None = all, 'product', 'collection', 'page', 'blog_article'
    rows: pre-fetched rows for a single ``kind``, so a caller that already read
          the table does not pay for a second scan. Must carry the columns in
          the matching ``*_FACT_COLUMNS`` tuple.
    """
    facts: list[dict[str, Any]] = []
    if rows is not None and kind is None:
        raise ValueError("rows= requires an explicit kind")

    def _load_workflow(object_type: str) -> dict[str, Any]:
        wf_rows = conn.execute(
            "SELECT handle, status, notes FROM seo_workflow_states WHERE object_type = ?",
            (object_type,),
        ).fetchall()
        return {row["handle"]: {"status": row["status"], "notes": row["notes"]} for row in wf_rows}

    if kind is None or kind == "product":
        workflows = _load_workflow("product")
        for row in rows if rows is not None else fetch_products_for_facts(conn):
            obj = dict(row)
            handle = obj["handle"]
            facts.append(build_seo_fact("product", obj, workflows.get(handle)))

    if kind is None or kind == "collection":
        workflows = _load_workflow("collection")
        product_counts: dict[str, int] = {}
        for pc_row in conn.execute(
            "SELECT c.handle, COUNT(cp.product_shopify_id) FROM collections c"
            " LEFT JOIN collection_products cp ON cp.collection_shopify_id = c.shopify_id"
            " GROUP BY c.handle"
        ).fetchall():
            product_counts[pc_row[0]] = pc_row[1]
        for row in rows if rows is not None else fetch_collections_for_facts(conn):
            obj = dict(row)
            handle = obj["handle"]
            facts.append(
                build_seo_fact(
                    "collection", obj, workflows.get(handle), product_count=product_counts.get(handle, 0)
                )
            )

    if kind is None or kind == "page":
        workflows = _load_workflow("page")
        for row in rows if rows is not None else fetch_pages_for_facts(conn):
            obj = dict(row)
            handle = obj["handle"]
            facts.append(build_seo_fact("page", obj, workflows.get(handle)))

    if kind is None or kind == "blog_article":
        workflows = _load_workflow("blog_article")
        for row in rows if rows is not None else fetch_blog_articles_for_facts(conn):
            obj = dict(row)
            blog_h = obj.get("blog_handle") or ""
            art_h = obj.get("handle") or ""
            composite = blog_article_composite_handle(blog_h, art_h)
            obj_for_fact = {**obj, "handle": composite}
            facts.append(build_seo_fact("blog_article", obj_for_fact, workflows.get(composite)))

    return facts
