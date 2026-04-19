"""Product domain: listing, detail, refresh, AI generation, and update."""
from __future__ import annotations

from typing import Any

from shopifyseo.dashboard_actions import (
    SYNC_LOCK,
    clear_ai_last_error,
    clear_last_error,
    record_last_error,
    refresh_object_signal_step,
    refresh_object_signals,
    start_ai_field_background,
    start_ai_object_background,
)
from shopifyseo.dashboard_live_updates import live_update_product
import shopifyseo.dashboard_queries as dq
import shopifyseo.dashboard_ai as dai
from shopifyseo.dashboard_status import index_status_bucket_from_strings
from shopifyseo.dashboard_store import DB_PATH, refresh_object_structured_seo_data
from backend.app.db import open_db_connection
from backend.app.schemas.dashboard import normalize_gsc_period_mode
from backend.app.services.object_signals import load_object_signals, parse_tags_json
from backend.app.services._catalog_helpers import (
    PRODUCT_SORTERS,
    _normalize_list_focus,
    _apply_list_focus,
    _attach_gsc_segment_flags,
    _detail_envelope,
    _signal_cards_for,
    gsc_queries_from_detail,
    serialize_opportunity,
    get_object_inspection_link,
)


def list_products(
    query: str = "",
    sort: str = "score",
    direction: str = "desc",
    limit: int | None = None,
    offset: int = 0,
    focus: str | None = None,
) -> dict[str, Any]:
    sort_key = sort if sort in PRODUCT_SORTERS else "score"
    sort_reverse = direction != "asc"
    focus_norm = _normalize_list_focus(focus, allow_thin_body=True)
    conn = open_db_connection()
    try:
        rows = {row["handle"]: dict(row) for row in dq.fetch_all_products(conn)}
        facts = dq.fetch_seo_facts(conn, "product")

        items: list[dict[str, Any]] = []
        for fact in facts:
            row = rows.get(fact["handle"])
            if row is None:
                continue
            item = {
                "handle": fact["handle"],
                "title": fact["title"],
                "vendor": row.get("vendor") or "",
                "status": row.get("status") or "",
                "updated_at": row.get("updated_at"),
                "score": int(fact["score"]),
                "priority": fact["priority"],
                "reasons": fact.get("reasons", []),
                "total_inventory": int(row.get("total_inventory") or 0),
                "body_length": int(fact.get("body_length") or 0),
                "seo_title": row.get("seo_title") or "",
                "seo_description": row.get("seo_description") or "",
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
            }
            items.append(item)

        if query:
            needle = query.strip().lower()
            items = [
                item for item in items
                if needle in item["title"].lower()
                or needle in item["handle"].lower()
                or needle in item["vendor"].lower()
                or needle in item["seo_title"].lower()
            ]
        items = _apply_list_focus(items, focus_norm, allow_thin_body=True)
        _attach_gsc_segment_flags(conn, "product", items)
    finally:
        conn.close()
    items.sort(key=PRODUCT_SORTERS[sort_key], reverse=sort_reverse)
    total = len(items)
    summary = {
        "visible_rows": total,
        "high_priority": sum(1 for item in items if item["priority"] == "High"),
        "index_issues": sum(
            1
            for item in items
            if index_status_bucket_from_strings(item["index_status"], item["index_coverage"]) != "indexed"
        ),
        "average_score": round(sum(item["score"] for item in items) / total) if total else 0,
    }
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
        "summary": summary,
    }


def get_product_detail(handle: str, gsc_period: str = "mtd") -> dict[str, Any] | None:
    period = normalize_gsc_period_mode(gsc_period)
    conn = open_db_connection()
    try:
        detail = dq.fetch_product_detail(conn, handle)
        if not detail:
            return None
        product = dict(detail["product"])
        parts = _detail_envelope(
            detail,
            product,
            body_key="description_html",
            extra_draft={"tags": parse_tags_json(product.get("tags_json"))},
        )
        opportunity = dq.build_seo_fact("product", detail["product"], parts["workflow"], detail.get("recommendation") or {})
        dim_rows = dq.fetch_gsc_query_dimension_rows(conn, "product", handle)
        gsc_segment_summary = dq.build_gsc_segment_summary_from_rows(dim_rows)
        signals = load_object_signals("product", handle, conn=conn, gsc_period=period)
        return {
            "product": product,
            "draft": parts["draft"],
            "workflow": parts["workflow"],
            "recommendation": parts["recommendation"],
            "recommendation_history": parts["recommendation_history"],
            "signal_cards": _signal_cards_for(conn, "product", product, gsc_period=period, signals=signals),
            "collections": [dict(row) for row in detail["collections"]],
            "variants": [dict(row) for row in detail["variants"]],
            "metafields": [dict(row) for row in detail["metafields"]],
            "product_images": [dict(row) for row in detail.get("product_images") or []],
            "opportunity": serialize_opportunity(opportunity),
            "gsc_segment_summary": gsc_segment_summary,
            "gsc_queries": gsc_queries_from_detail(signals.get("gsc_detail")),
        }
    finally:
        conn.close()


def start_product_ai(handle: str) -> tuple[bool, str, dict[str, Any]]:
    started, state = start_ai_object_background(DB_PATH, "product", handle)
    if started:
        clear_ai_last_error()
        return True, "AI generation started", state
    return False, "AI generation already running", state


def regenerate_product_field(handle: str, field: str, accepted_fields: dict) -> dict:
    conn = open_db_connection()
    try:
        return dai.generate_field_recommendation(conn, "product", handle, field, accepted_fields)
    finally:
        conn.close()


def start_product_field_regeneration(handle: str, field: str, accepted_fields: dict[str, str]) -> tuple[bool, str, dict[str, Any]]:
    started, state = start_ai_field_background(DB_PATH, "product", handle, field, accepted_fields)
    if started:
        clear_ai_last_error()
        return True, f"{field.replace('_', ' ')} regeneration started", state
    return False, "AI generation already running", state


def refresh_product(handle: str, step: str | None = None, gsc_period: str = "mtd") -> tuple[bool, dict[str, Any]]:
    period = normalize_gsc_period_mode(gsc_period)
    try:
        if step:
            result = refresh_object_signal_step(
                open_db_connection, "product", handle, step, db_path=DB_PATH, gsc_period=period
            )
            return result["status"] != "error", {"message": result["message"], "result": result}
        results = refresh_object_signals(open_db_connection, "product", handle, db_path=DB_PATH, gsc_period=period)
        clear_last_error()
        overall_ok = not any(item["status"] == "error" for item in results.values())
        return overall_ok, {
            "message": "Product refresh completed." if overall_ok else "Product refresh completed with issues.",
            "steps": results,
        }
    except Exception as exc:
        record_last_error(exc)
        return False, {"message": str(exc), "steps": {}}


def get_product_inspection_link(handle: str) -> tuple[bool, str]:
    return get_object_inspection_link("product", handle)


def update_product(handle: str, payload: dict[str, Any]) -> tuple[bool, str]:
    conn = open_db_connection()
    try:
        detail = dq.fetch_product_detail(conn, handle)
        if not detail:
            return False, "Product not found"
        product = detail["product"]
        try:
            with SYNC_LOCK:
                live_update_product(
                    DB_PATH,
                    product["shopify_id"],
                    payload.get("title", ""),
                    payload.get("seo_title", ""),
                    payload.get("seo_description", ""),
                    payload.get("body_html", ""),
                    payload.get("tags", ""),
                )
                dq.apply_saved_product_fields_from_editor(
                    conn,
                    product["shopify_id"],
                    title=str(payload.get("title") or ""),
                    seo_title=str(payload.get("seo_title") or ""),
                    seo_description=str(payload.get("seo_description") or ""),
                    body_html=str(payload.get("body_html") or ""),
                    tags=str(payload.get("tags") or ""),
                )
                dq.set_workflow_state(
                    conn,
                    "product",
                    handle,
                    payload.get("workflow_status", "Needs fix"),
                    payload.get("workflow_notes", ""),
                )
            clear_last_error()
            return True, "Product saved"
        except (Exception, SystemExit) as exc:
            record_last_error(exc)
            return False, str(exc)
    finally:
        conn.close()
