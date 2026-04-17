"""Shared helpers used across product, article, and content service modules."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from shopifyseo.dashboard_google import gsc_url_report_window
from shopifyseo.dashboard_actions import (
    record_last_error,
    refresh_and_get_inspection_link,
)
from shopifyseo.dashboard_http import HttpRequestError
import shopifyseo.dashboard_queries as dq
from backend.app.db import open_db_connection
from backend.app.schemas.dashboard import normalize_gsc_period_mode
from backend.app.services.index_status import index_status_info, inspection_for_catalog_index_display
from backend.app.services.object_signals import load_object_signals, search_console_inspect_href


PRODUCT_SORTERS: dict[str, Any] = {
    "score": lambda item: item["score"],
    "title": lambda item: item["title"].lower(),
    "updated_at": lambda item: item["updated_at"] or "",
    "content_status": lambda item: (
        1 if (item.get("seo_title") or "").strip()
            and (item.get("seo_description") or "").strip()
            and (item.get("body_length") or 0) > 0
        else 0
    ),
    "gsc_segments": lambda item: 1 if (item.get("gsc_segment_flags") or {}).get("has_dimensional") else 0,
    "index_status": lambda item: (item["index_status"] or "").lower(),
    "gsc_impressions": lambda item: item["gsc_impressions"],
    "gsc_clicks": lambda item: item["gsc_clicks"],
    "gsc_ctr": lambda item: item["gsc_ctr"],
    "gsc_position": lambda item: item["gsc_position"] or 999.0,
    "ga4_sessions": lambda item: item["ga4_sessions"],
    "ga4_views": lambda item: item["ga4_views"],
    "body_length": lambda item: item["body_length"],
    "pagespeed_performance": lambda item: item["pagespeed_performance"] if item["pagespeed_performance"] is not None else -1,
}

CONTENT_SORTERS = PRODUCT_SORTERS


def _entity_missing_meta(seo_title: str, seo_description: str) -> bool:
    return not (seo_title or "").strip() or not (seo_description or "").strip()


def _normalize_list_focus(raw: str | None, *, allow_thin_body: bool) -> str | None:
    key = (raw or "").strip().lower()
    if key == "missing_meta":
        return "missing_meta"
    if allow_thin_body and key == "thin_body":
        return "thin_body"
    return None


def _apply_list_focus(
    items: list[dict[str, Any]],
    focus: str | None,
    *,
    allow_thin_body: bool,
) -> list[dict[str, Any]]:
    if not focus:
        return items
    if focus == "missing_meta":
        return [i for i in items if _entity_missing_meta(i.get("seo_title") or "", i.get("seo_description") or "")]
    if focus == "thin_body" and allow_thin_body:
        return [i for i in items if int(i.get("body_length") or 0) < 200]
    return items


def _resolve_recommendation_model(record: dict[str, Any] | None) -> str:
    if not record:
        return ""
    direct_model = (record.get("model") or "").strip()
    if direct_model:
        return direct_model
    details = record.get("details")
    if not isinstance(details, dict):
        details = {}
    meta = details.get("_meta")
    if isinstance(meta, dict):
        combined_model = (meta.get("model") or "").strip()
        if combined_model:
            return combined_model
        review_model = (meta.get("review_model") or "").strip()
        generation_model = (meta.get("generation_model") or "").strip()
        if generation_model and review_model and generation_model != review_model:
            return f"{generation_model} + {review_model}"
        if review_model:
            return review_model
        if generation_model:
            return generation_model
    prompt_version = (record.get("prompt_version") or "").strip()
    if prompt_version:
        return f"Prompt {prompt_version}"
    source = (record.get("source") or "").strip()
    if source == "dashboard":
        return "Saved recommendation"
    return ""


def _detail_envelope(
    detail: dict[str, Any],
    current: dict[str, Any],
    *,
    body_key: str,
    extra_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow = dict(detail["workflow"]) if detail.get("workflow") else {"status": "Needs fix", "notes": "", "updated_at": None}
    recommendation_record = detail.get("recommendation") or {}
    recommendation_event = detail.get("recommendation_event") or {}
    recommendation = recommendation_record.get("details") or {}
    recommendation_model = _resolve_recommendation_model(recommendation_record) or _resolve_recommendation_model(
        recommendation_event
    )
    recommendation_history = [
        {**row, "model": _resolve_recommendation_model(row)} for row in detail.get("recommendation_history", [])
    ]
    extra = extra_draft or {}
    draft: dict[str, Any] = {
        "title": current.get("title") or "",
        "seo_title": current.get("seo_title") or "",
        "seo_description": current.get("seo_description") or "",
        "body_html": current.get(body_key) or "",
        "workflow_status": workflow.get("status") or "Needs fix",
        "workflow_notes": workflow.get("notes") or "",
        **extra,
    }
    recommendation_api = {
        "summary": recommendation_record.get("summary") or "No AI recommendation generated yet.",
        "status": recommendation_event.get("status") or "not_generated",
        "model": recommendation_model,
        "created_at": recommendation_record.get("created_at") or recommendation_event.get("created_at"),
        "error_message": recommendation_event.get("error_message") or "",
        "details": recommendation,
    }
    return {
        "workflow": workflow,
        "recommendation": recommendation_api,
        "recommendation_history": recommendation_history,
        "draft": draft,
    }


def serialize_opportunity(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_type": item["object_type"],
        "handle": item["handle"],
        "title": item["title"],
        "priority": item["priority"],
        "score": int(item["score"]),
        "reasons": list(item.get("reasons", [])),
        "gsc_impressions": int(item.get("gsc_impressions") or 0),
        "gsc_clicks": int(item.get("gsc_clicks") or 0),
        "gsc_position": float(item.get("gsc_position") or 0),
        "ga4_sessions": int(item.get("ga4_sessions") or 0),
        "pagespeed_performance": item.get("pagespeed_performance"),
    }


def _attach_gsc_segment_flags(conn: sqlite3.Connection, object_type: str, items: list[dict[str, Any]]) -> None:
    if not items:
        return
    keys = [(object_type, it["handle"]) for it in items]
    dim = dq.object_keys_with_dimensional_gsc(conn, keys)
    for it in items:
        it["gsc_segment_flags"] = {"has_dimensional": (object_type, it["handle"]) in dim}


def _user_facing_http_error(exc: BaseException) -> str:
    if isinstance(exc, HttpRequestError) and exc.body:
        try:
            payload = json.loads(exc.body)
            detail = payload.get("error_description") or payload.get("error")
            if detail:
                return f"{exc} ({detail})"
        except json.JSONDecodeError:
            pass
    return str(exc)


def _gsc_date_range_label(gsc_period: str) -> str:
    gs, ge = gsc_url_report_window(normalize_gsc_period_mode(gsc_period))
    return f"{gs.isoformat()}–{ge.isoformat()}"


def _ga4_date_range_label() -> str:
    end_d = date.today() - timedelta(days=1)
    start_d = end_d - timedelta(days=27)
    return f"{start_d.isoformat()}–{end_d.isoformat()} · last 28 days (GA4)"


def get_object_inspection_link(object_type: str, handle: str) -> tuple[bool, str]:
    try:
        href = refresh_and_get_inspection_link(open_db_connection, object_type, handle)
        return True, href
    except Exception as exc:
        record_last_error(exc)
        return False, _user_facing_http_error(exc)


def _signal_cards_for(
    conn: sqlite3.Connection, kind: str, current: dict[str, Any], *, gsc_period: str = "mtd"
) -> list[dict[str, Any]]:
    signals = load_object_signals(
        kind, current["handle"], conn=conn, gsc_period=normalize_gsc_period_mode(gsc_period)
    )
    inspection_display = inspection_for_catalog_index_display(signals["inspection_detail"], current)
    index_label, _, index_reason = index_status_info(inspection_display)
    inspect_href = search_console_inspect_href(
        current.get("url") or dq.object_url(kind, current["handle"]),
        signals.get("site_url") or "",
        signals.get("inspection_detail"),
    )
    gsc_dates = _gsc_date_range_label(gsc_period)
    ga4_dates = _ga4_date_range_label()
    gsc_has = bool(current.get("gsc_impressions"))
    return [
        {
            "label": "Index",
            "value": index_label,
            "sublabel": index_reason or "No index detail",
            "updated_at": current["index_last_fetched_at"],
            "step": "index",
            "action_label": "Request indexing",
            "action_href": inspect_href,
        },
        {
            "label": "GSC clicks",
            "value": str(int(current["gsc_clicks"] or 0)),
            "sublabel": f"{gsc_dates} · Google Search" + ("" if gsc_has else " · No GSC data"),
            "updated_at": current["gsc_last_fetched_at"],
            "step": "gsc_clicks",
        },
        {
            "label": "GSC impressions",
            "value": str(int(current["gsc_impressions"] or 0)),
            "sublabel": f"{gsc_dates} · Google Search",
            "updated_at": current["gsc_last_fetched_at"],
            "step": "gsc_impressions",
        },
        {
            "label": "GSC CTR",
            "value": f"{float(current['gsc_ctr'] or 0) * 100:.2f}%",
            "sublabel": f"{gsc_dates} · Click-through rate (search)",
            "updated_at": current["gsc_last_fetched_at"],
            "step": "gsc_ctr",
        },
        {
            "label": "Avg. position (GSC)",
            "value": f"{float(current['gsc_position'] or 0):.1f}",
            "sublabel": f"{gsc_dates} · Average position in Search",
            "updated_at": current["gsc_last_fetched_at"],
            "step": "gsc_position",
        },
        {
            "label": "GA4",
            "value": f"{int(current['ga4_views'] or 0)} views",
            "sublabel": (
                f"{ga4_dates} · {int(current['ga4_sessions'] or 0)} sessions · {float(current['ga4_avg_session_duration'] or 0):.0f}s avg"
                if (current["ga4_views"] or current["ga4_sessions"]) else f"{ga4_dates} · No GA4 data"
            ),
            "updated_at": current["ga4_last_fetched_at"],
            "step": "ga4",
        },
        {
            "label": "PageSpeed",
            "value": f"{int(current['pagespeed_performance'] or 0)} perf" if current["pagespeed_performance"] is not None else "No score",
            "sublabel": current["pagespeed_status"] or "Never fetched",
            "updated_at": current["pagespeed_last_fetched_at"],
            "step": "speed",
        },
    ]
