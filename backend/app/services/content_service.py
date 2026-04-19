"""Content domain (collections and pages): listing, detail, and update."""
from __future__ import annotations

from typing import Any

from shopifyseo.dashboard_actions import (
    SYNC_LOCK,
    clear_last_error,
    record_last_error,
)
from shopifyseo.dashboard_live_updates import live_update_collection, live_update_page
import shopifyseo.dashboard_queries as dq
from shopifyseo.dashboard_store import DB_PATH, refresh_object_structured_seo_data
from backend.app.db import open_db_connection
from backend.app.schemas.dashboard import normalize_gsc_period_mode
from backend.app.services.object_signals import load_object_signals
from backend.app.services._catalog_helpers import (
    CONTENT_SORTERS,
    _normalize_list_focus,
    _apply_list_focus,
    _attach_gsc_segment_flags,
    _detail_envelope,
    _signal_cards_for,
    gsc_queries_from_detail,
    serialize_opportunity,
    get_object_inspection_link,
)


def _build_content_item(kind: str, fact: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "handle": fact["handle"],
        "title": fact["title"],
        "updated_at": row.get("updated_at"),
        "score": int(fact["score"]),
        "priority": fact["priority"],
        "reasons": fact.get("reasons", []),
        "seo_title": row.get("seo_title") or "",
        "seo_description": row.get("seo_description") or "",
        "body_length": int(fact.get("body_length") or 0),
        "gsc_clicks": int(fact.get("gsc_clicks") or 0),
        "gsc_impressions": int(fact.get("gsc_impressions") or 0),
        "gsc_ctr": float(fact.get("gsc_ctr") or 0),
        "gsc_position": float(fact.get("gsc_position") or 0),
        "ga4_sessions": int(fact.get("ga4_sessions") or 0),
        "ga4_views": int(fact.get("ga4_views") or 0),
        "ga4_avg_session_duration": float(fact.get("ga4_avg_session_duration") or 0),
        "index_status": fact.get("index_status") or "",
        "index_coverage": fact.get("index_coverage") or "",
        "google_canonical": fact.get("google_canonical") or "",
        "pagespeed_performance": fact.get("pagespeed_performance"),
        "pagespeed_desktop_performance": fact.get("pagespeed_desktop_performance"),
        "pagespeed_status": fact.get("pagespeed_status") or "",
        "workflow_status": (fact.get("workflow") or {}).get("status") or "Needs fix",
        "workflow_notes": (fact.get("workflow") or {}).get("notes") or "",
        "product_count": int(fact.get("product_count") or 0),
    }


def list_content(
    kind: str,
    query: str = "",
    sort: str = "score",
    direction: str = "desc",
    limit: int | None = None,
    offset: int = 0,
    focus: str | None = None,
) -> dict[str, Any]:
    sort_key = sort if sort in CONTENT_SORTERS else "score"
    sort_reverse = direction != "asc"
    focus_norm = _normalize_list_focus(focus, allow_thin_body=False)
    conn = open_db_connection()
    try:
        if kind == "collection":
            rows = {row["handle"]: dict(row) for row in dq.fetch_all_collections(conn)}
        else:
            rows = {row["handle"]: dict(row) for row in dq.fetch_all_pages(conn)}
        facts = dq.fetch_seo_facts(conn, kind)

        items = [
            _build_content_item(kind, fact, rows[fact["handle"]])
            for fact in facts
            if fact["handle"] in rows
        ]
        if query:
            needle = query.strip().lower()
            items = [
                item for item in items
                if needle in item["title"].lower()
                or needle in item["handle"].lower()
                or needle in item["seo_title"].lower()
            ]
        items = _apply_list_focus(items, focus_norm, allow_thin_body=False)
        _attach_gsc_segment_flags(conn, kind, items)
    finally:
        conn.close()
    items.sort(key=CONTENT_SORTERS[sort_key], reverse=sort_reverse)
    total = len(items)
    paged_items = items[offset:] if limit is None else items[offset:offset + limit]
    return {
        "items": paged_items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "query": query,
        "sort": sort_key,
        "direction": "desc" if sort_reverse else "asc",
        "focus": focus_norm,
    }


def get_content_detail(kind: str, handle: str, gsc_period: str = "mtd") -> dict[str, Any] | None:
    period = normalize_gsc_period_mode(gsc_period)
    conn = open_db_connection()
    try:
        if kind == "collection":
            detail = dq.fetch_collection_detail(conn, handle)
            if not detail:
                return None
            current = dict(detail["collection"])
            related_items = [{"handle": row["product_handle"], "title": row["product_title"], "type": "product"} for row in detail["products"]]
            metafields = [dict(row) for row in detail["metafields"]]
            product_count = len(detail["products"])
            fact = dq.build_seo_fact("collection", detail["collection"], detail.get("workflow"), detail.get("recommendation"), product_count=product_count)
            body_key = "description_html"
        else:
            detail = dq.fetch_page_detail(conn, handle)
            if not detail:
                return None
            current = dict(detail["page"])
            related_items = (
                [{"handle": row["handle"], "title": row["title"], "type": "collection"} for row in detail["related_collections"]]
                + [{"handle": row["handle"], "title": row["title"], "type": "product"} for row in detail["related_products"]]
                + [{"handle": row["handle"], "title": row["title"], "type": "page"} for row in detail.get("related_pages") or []]
            )
            metafields = []
            fact = dq.build_seo_fact("page", detail["page"], detail.get("workflow"), detail.get("recommendation"))
            body_key = "body"

        parts = _detail_envelope(detail, current, body_key=body_key)
        dim_rows = dq.fetch_gsc_query_dimension_rows(conn, kind, handle)
        gsc_segment_summary = dq.build_gsc_segment_summary_from_rows(dim_rows)
        signals = load_object_signals(kind, current["handle"], conn=conn, gsc_period=period)
        return {
            "object_type": kind,
            "current": current,
            "draft": parts["draft"],
            "workflow": parts["workflow"],
            "recommendation": parts["recommendation"],
            "recommendation_history": parts["recommendation_history"],
            "signal_cards": _signal_cards_for(conn, kind, current, gsc_period=period, signals=signals),
            "related_items": related_items,
            "metafields": metafields,
            "opportunity": serialize_opportunity(fact),
            "gsc_segment_summary": gsc_segment_summary,
            "gsc_queries": gsc_queries_from_detail(signals.get("gsc_detail")),
        }
    finally:
        conn.close()


def get_content_inspection_link(kind: str, handle: str) -> tuple[bool, str]:
    return get_object_inspection_link(kind, handle)


def update_content(kind: str, handle: str, payload: dict[str, Any]) -> tuple[bool, str]:
    conn = open_db_connection()
    try:
        if kind == "collection":
            detail = dq.fetch_collection_detail(conn, handle)
            if not detail:
                return False, "Collection not found"
            current = detail["collection"]
        else:
            detail = dq.fetch_page_detail(conn, handle)
            if not detail:
                return False, "Page not found"
            current = detail["page"]
        try:
            with SYNC_LOCK:
                if kind == "collection":
                    live_update_collection(
                        DB_PATH,
                        current["shopify_id"],
                        payload.get("title", ""),
                        payload.get("seo_title", ""),
                        payload.get("seo_description", ""),
                        payload.get("body_html", ""),
                    )
                    dq.apply_saved_collection_fields_from_editor(
                        conn,
                        current["shopify_id"],
                        title=str(payload.get("title") or ""),
                        seo_title=str(payload.get("seo_title") or ""),
                        seo_description=str(payload.get("seo_description") or ""),
                        description_html=str(payload.get("body_html") or ""),
                    )
                else:
                    live_update_page(
                        DB_PATH,
                        current["shopify_id"],
                        payload.get("title", ""),
                        payload.get("seo_title", ""),
                        payload.get("seo_description", ""),
                        payload.get("body_html", ""),
                    )
                    dq.apply_saved_page_fields_from_editor(
                        conn,
                        current["shopify_id"],
                        title=str(payload.get("title") or ""),
                        seo_title=str(payload.get("seo_title") or ""),
                        seo_description=str(payload.get("seo_description") or ""),
                        body_html=str(payload.get("body_html") or ""),
                    )
                dq.set_workflow_state(
                    conn,
                    kind,
                    handle,
                    payload.get("workflow_status", "Needs fix"),
                    payload.get("workflow_notes", ""),
                )
                refresh_object_structured_seo_data(conn, kind, handle)
            clear_last_error()
            return True, f"{kind.title()} saved"
        except (Exception, SystemExit) as exc:
            record_last_error(exc)
            return False, str(exc)
    finally:
        conn.close()


def save_all_collection_meta_to_shopify() -> tuple[bool, str, dict[str, Any]]:
    conn = open_db_connection()
    try:
        collections = [dict(row) for row in dq.fetch_all_collections(conn)]
        saved = 0
        skipped = 0
        skipped_handles: list[str] = []

        try:
            with SYNC_LOCK:
                for collection in collections:
                    handle = collection["handle"]
                    seo_title = str(collection.get("seo_title") or "").strip()
                    seo_description = str(collection.get("seo_description") or "").strip()

                    if not seo_title and not seo_description:
                        skipped += 1
                        skipped_handles.append(handle)
                        continue

                    live_update_collection(
                        DB_PATH,
                        collection["shopify_id"],
                        "",
                        seo_title,
                        seo_description,
                        str(collection.get("description_html") or ""),
                    )
                    dq.apply_saved_collection_fields_from_editor(
                        conn,
                        collection["shopify_id"],
                        title=str(collection.get("title") or ""),
                        seo_title=seo_title,
                        seo_description=seo_description,
                        description_html=str(collection.get("description_html") or ""),
                    )
                    refresh_object_structured_seo_data(conn, "collection", handle)
                    saved += 1

            clear_last_error()
            return True, f"Saved collection SEO content for {saved} collections", {
                "saved": saved,
                "skipped": skipped,
                "total": len(collections),
                "skipped_handles": skipped_handles[:25],
            }
        except (Exception, SystemExit) as exc:
            record_last_error(exc)
            return False, str(exc), {"saved": saved, "skipped": skipped, "total": len(collections)}
    finally:
        conn.close()


def save_all_page_meta_to_shopify() -> tuple[bool, str, dict[str, Any]]:
    conn = open_db_connection()
    try:
        pages = [dict(row) for row in dq.fetch_all_pages(conn)]
        saved = 0
        skipped = 0
        skipped_handles: list[str] = []

        try:
            with SYNC_LOCK:
                for page in pages:
                    handle = page["handle"]
                    seo_title = str(page.get("seo_title") or "").strip()
                    seo_description = str(page.get("seo_description") or "").strip()
                    body_html = str(page.get("body") or "")

                    if not seo_title and not seo_description and not body_html.strip():
                        skipped += 1
                        skipped_handles.append(handle)
                        continue

                    live_update_page(
                        DB_PATH,
                        page["shopify_id"],
                        "",
                        seo_title,
                        seo_description,
                        body_html,
                    )
                    dq.apply_saved_page_fields_from_editor(
                        conn,
                        page["shopify_id"],
                        title=str(page.get("title") or ""),
                        seo_title=seo_title,
                        seo_description=seo_description,
                        body_html=body_html,
                    )
                    refresh_object_structured_seo_data(conn, "page", handle)
                    saved += 1

            clear_last_error()
            return True, f"Saved page SEO content for {saved} pages", {
                "saved": saved,
                "skipped": skipped,
                "total": len(pages),
                "skipped_handles": skipped_handles[:25],
            }
        except (Exception, SystemExit) as exc:
            record_last_error(exc)
            return False, str(exc), {"saved": saved, "skipped": skipped, "total": len(pages)}
    finally:
        conn.close()
