"""Blog/article domain: listing, detail, and update."""
from __future__ import annotations

import re
from typing import Any

from shopifyseo.dashboard_actions import (
    SYNC_LOCK,
    clear_last_error,
    record_last_error,
)
from shopifyseo.dashboard_live_updates import live_update_article
import shopifyseo.dashboard_queries as dq
from shopifyseo.dashboard_store import DB_PATH, refresh_object_structured_seo_data
from backend.app.db import open_db_connection
from backend.app.services.object_signals import load_object_signals
from backend.app.services._catalog_helpers import (
    _detail_envelope,
    _signal_cards_for,
    gsc_queries_from_detail,
    serialize_opportunity,
    get_object_inspection_link,
)


_ARTICLE_SIGNAL_DEFAULTS: dict[str, Any] = {
    "gsc_clicks": 0,
    "gsc_impressions": 0,
    "gsc_ctr": 0,
    "gsc_position": 0,
    "gsc_last_fetched_at": None,
    "ga4_sessions": 0,
    "ga4_views": 0,
    "ga4_avg_session_duration": 0,
    "ga4_last_fetched_at": None,
    "index_status": "",
    "index_coverage": "",
    "google_canonical": "",
    "index_last_fetched_at": None,
    "pagespeed_performance": None,
    "pagespeed_seo": None,
    "pagespeed_status": "",
    "pagespeed_last_fetched_at": None,
}


def _blog_body_preview(body: str | None, max_len: int = 200) -> str:
    if not body:
        return ""
    text = re.sub(r"<[^>]+>", " ", body)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _article_list_row(ad: dict[str, Any], blog_handle_fallback: str = "") -> dict[str, Any]:
    """Base fields shared between list_blog_articles and list_all_articles."""
    body = ad.get("body") or ""
    summary = ad.get("summary") or ""
    preview_src = summary if (summary and len(summary) < len(body)) else body
    return {
        "handle": ad.get("handle") or "",
        "title": ad.get("title") or "",
        "blog_handle": ad.get("blog_handle") or blog_handle_fallback,
        "published_at": ad.get("published_at"),
        "updated_at": ad.get("updated_at"),
        "is_published": bool(ad.get("is_published", 0)),
        "seo_title": ad.get("seo_title") or "",
        "seo_description": ad.get("seo_description") or "",
        "body_preview": _blog_body_preview(preview_src or body),
    }


def _article_current_payload(article: dict[str, Any], blog_handle: str, article_slug: str) -> dict[str, Any]:
    composite = dq.blog_article_composite_handle(blog_handle, article_slug)
    return {
        **_ARTICLE_SIGNAL_DEFAULTS,
        **article,
        "handle": composite,
        "blog_handle": blog_handle,
        "article_handle": article_slug,
    }


def list_blogs() -> dict[str, Any]:
    conn = open_db_connection()
    try:
        rows = dq.fetch_all_blogs(conn)
        article_counts: dict[str, int] = {}
        for ac_row in conn.execute(
            "SELECT blog_shopify_id, COUNT(*) FROM blog_articles GROUP BY blog_shopify_id"
        ).fetchall():
            article_counts[ac_row[0]] = ac_row[1]
        items: list[dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            bid = d.get("shopify_id") or ""
            items.append(
                {
                    "handle": d.get("handle") or "",
                    "title": d.get("title") or "",
                    "updated_at": d.get("updated_at"),
                    "article_count": article_counts.get(bid, 0),
                }
            )
        items.sort(key=lambda x: (x["title"] or "").lower())
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


def list_blog_articles(blog_handle: str) -> dict[str, Any] | None:
    conn = open_db_connection()
    try:
        row = dq.fetch_blog_by_handle(conn, blog_handle)
        if not row:
            return None
        blog = dict(row)
        articles = dq.fetch_articles_by_blog_handle(conn, blog_handle)
        items: list[dict[str, Any]] = []
        for a in articles:
            items.append(_article_list_row(dict(a), blog_handle))
        return {
            "blog": {
                "handle": blog.get("handle") or "",
                "title": blog.get("title") or "",
                "updated_at": blog.get("updated_at"),
                "article_count": len(items),
            },
            "items": items,
            "total": len(items),
        }
    finally:
        conn.close()


def list_all_articles() -> dict[str, Any]:
    conn = open_db_connection()
    try:
        facts_by_comp = {f["handle"]: f for f in dq.fetch_seo_facts(conn, "blog_article")}
        rows = dq.fetch_all_blog_articles_enriched(conn)
        items: list[dict[str, Any]] = []
        for a in rows:
            ad = dict(a)
            base = _article_list_row(ad)
            blog_handle = base["blog_handle"]
            art_handle = base["handle"]
            composite = dq.blog_article_composite_handle(blog_handle, art_handle)
            fact = facts_by_comp.get(composite) or {}
            wf = fact.get("workflow") or {}
            base.update({
                "blog_title": (ad.get("blog_title") or "").strip() or blog_handle,
                "score": int(fact.get("score") or 0),
                "priority": str(fact.get("priority") or ""),
                "reasons": list(fact.get("reasons") or []),
                "body_length": int(fact.get("body_length") or 0),
                "gsc_clicks": int(fact.get("gsc_clicks") or 0),
                "gsc_impressions": int(fact.get("gsc_impressions") or 0),
                "gsc_ctr": float(fact.get("gsc_ctr") or 0),
                "gsc_position": float(fact.get("gsc_position") or 0),
                "ga4_sessions": int(fact.get("ga4_sessions") or 0),
                "ga4_views": int(fact.get("ga4_views") or 0),
                "ga4_avg_session_duration": float(fact.get("ga4_avg_session_duration") or 0),
                "index_status": str(fact.get("index_status") or ""),
                "index_coverage": str(fact.get("index_coverage") or ""),
                "google_canonical": str(fact.get("google_canonical") or ""),
                "pagespeed_performance": fact.get("pagespeed_performance"),
                "pagespeed_status": str(fact.get("pagespeed_status") or ""),
                "workflow_status": str(wf.get("status") or "Needs fix"),
                "workflow_notes": str(wf.get("notes") or ""),
                "gsc_segment_flags": {"has_dimensional": False},
            })
            items.append(base)
        dim_keys = [
            ("blog_article", dq.blog_article_composite_handle(it["blog_handle"], it["handle"]))
            for it in items
        ]
        dim = dq.object_keys_with_dimensional_gsc(conn, dim_keys)
        for it in items:
            comp = dq.blog_article_composite_handle(it["blog_handle"], it["handle"])
            it["gsc_segment_flags"] = {"has_dimensional": ("blog_article", comp) in dim}
        return {"items": items, "total": len(items)}
    finally:
        conn.close()


def get_blog_article_detail(blog_handle: str, article_slug: str, gsc_period: str = "mtd") -> dict[str, Any] | None:
    from backend.app.schemas.dashboard import normalize_gsc_period_mode
    period = normalize_gsc_period_mode(gsc_period)
    conn = open_db_connection()
    try:
        detail = dq.fetch_blog_article_detail(conn, blog_handle, article_slug)
        if not detail:
            return None
        art = dict(detail["article"])
        current = _article_current_payload(art, blog_handle, article_slug)
        related_items = (
            [{"handle": row["handle"], "title": row["title"], "type": "collection"} for row in detail["related_collections"]]
            + [{"handle": row["handle"], "title": row["title"], "type": "product"} for row in detail["related_products"]]
            + [{"handle": row["handle"], "title": row["title"], "type": "page"} for row in detail.get("related_pages") or []]
        )
        composite = dq.blog_article_composite_handle(blog_handle, article_slug)
        fact_obj = {**art, "handle": composite}
        fact = dq.build_seo_fact("blog_article", fact_obj, detail.get("workflow"), detail.get("recommendation"))
        parts = _detail_envelope(detail, current, body_key="body")
        dim_rows = dq.fetch_gsc_query_dimension_rows(conn, "blog_article", composite)
        gsc_segment_summary = dq.build_gsc_segment_summary_from_rows(dim_rows)
        signals = load_object_signals("blog_article", current["handle"], conn=conn, gsc_period=period)
        return {
            "object_type": "blog_article",
            "current": current,
            "draft": parts["draft"],
            "workflow": parts["workflow"],
            "recommendation": parts["recommendation"],
            "recommendation_history": parts["recommendation_history"],
            "signal_cards": _signal_cards_for(conn, "blog_article", current, gsc_period=period, signals=signals),
            "related_items": related_items,
            "metafields": [],
            "opportunity": serialize_opportunity(fact),
            "gsc_segment_summary": gsc_segment_summary,
            "gsc_queries": gsc_queries_from_detail(signals.get("gsc_detail")),
        }
    finally:
        conn.close()


def get_blog_article_inspection_link(blog_handle: str, article_slug: str) -> tuple[bool, str]:
    composite = dq.blog_article_composite_handle(blog_handle, article_slug)
    return get_object_inspection_link("blog_article", composite)


def update_blog_article(blog_handle: str, article_slug: str, payload: dict[str, Any]) -> tuple[bool, str]:
    conn = open_db_connection()
    try:
        detail = dq.fetch_blog_article_detail(conn, blog_handle, article_slug)
        if not detail:
            return False, "Article not found"
        row = detail["article"]
        composite = dq.blog_article_composite_handle(blog_handle, article_slug)
        try:
            with SYNC_LOCK:
                live_update_article(
                    DB_PATH,
                    row["shopify_id"],
                    payload.get("title", ""),
                    payload.get("seo_title", ""),
                    payload.get("seo_description", ""),
                    payload.get("body_html", ""),
                )
                dq.apply_saved_blog_article_fields_from_editor(
                    conn,
                    row["shopify_id"],
                    title=str(payload.get("title") or ""),
                    seo_title=str(payload.get("seo_title") or ""),
                    seo_description=str(payload.get("seo_description") or ""),
                    body_html=str(payload.get("body_html") or ""),
                )
                dq.set_workflow_state(
                    conn,
                    "blog_article",
                    composite,
                    payload.get("workflow_status", "Needs fix"),
                    payload.get("workflow_notes", ""),
                )
                refresh_object_structured_seo_data(conn, "blog_article", composite)
            clear_last_error()
            return True, "Article saved"
        except (Exception, SystemExit) as exc:
            record_last_error(exc)
            return False, str(exc)
    finally:
        conn.close()
