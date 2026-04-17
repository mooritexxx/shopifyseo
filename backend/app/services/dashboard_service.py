"""Dashboard overview, sync/AI management, shared object operations, and sidekick chat.

Domain-specific services have been extracted to dedicated modules:
  - product_service.py  — product listing, detail, refresh, AI, update
  - article_service.py  — blog/article listing, detail, update
  - content_service.py  — collection/page listing, detail, update
  - _catalog_helpers.py — shared helpers used by the above
"""
from __future__ import annotations

import json
import os
from typing import Any

from shopifyseo.dashboard_actions import (
    AI_STATE,
    AI_JOBS,
    AI_JOBS_LOCK,
    SYNC_LOCK,
    SYNC_STATE,
    clear_ai_last_error,
    clear_last_error,
    record_last_error,
    refresh_object_signal_step,
    refresh_object_signals,
    request_ai_cancel,
    request_sync_cancel,
    start_ai_field_background,
    start_ai_object_background,
    start_sync_background,
)
from shopifyseo.dashboard_actions.sync_eta import compute_sync_eta_seconds
import shopifyseo.dashboard_queries as dq
import shopifyseo.dashboard_ai as dai
import shopifyseo.dashboard_google as dg
from backend.app.schemas.dashboard import normalize_gsc_period_mode
from backend.app.services.gsc_overview_calendar import DEFAULT_DASHBOARD_TZ, gsc_anchor_date_local
from backend.app.services.overview_metrics import summarize_ga4, summarize_gsc
from backend.app.services.catalog_completion import build_catalog_completion
from backend.app.services.indexing_rollup import build_indexing_rollup
from backend.app.services.google_signals_service import (
    _cache_payload,
    _empty_gsc_property_breakdowns_for_signals,
    gsc_matched_period_windows as _gsc_matched_period_windows,
    gsc_property_breakdowns_for_signals as _gsc_property_breakdowns_for_signals,
    get_google_signals_data,
    save_google_selection,
    refresh_google_summary,
)
from backend.app.services.settings_service import (
    filter_normalized_scopes_for_readiness,
    get_settings_data,
    get_shopify_shop_info,
    get_sync_scope_readiness,
    save_settings,
    test_ai_connection,
    test_image_model,
    test_vision_model,
    test_google_ads_connection,
    test_shopify_admin_connection,
    get_ollama_models,
    get_anthropic_models,
    get_gemini_models,
    get_openrouter_models,
)
from shopifyseo.dashboard_actions._sync import _normalize_sync_scopes
from shopifyseo.dashboard_store import DB_PATH
from shopifyseo.sidekick import run_sidekick_turn

from backend.app.db import open_db_connection


_ALLOWED_GSC_SEGMENTS = frozenset({"all", "products", "collections", "pages", "blogs"})


def normalize_gsc_url_segment(raw: str) -> str:
    key = (raw or "all").strip().lower()
    return key if key in _ALLOWED_GSC_SEGMENTS else "all"


def _resolve_gsc_site_url_for_breakdowns(conn) -> str:
    """Same property URL as GSC overview + Tier A breakdown writes (avoids cache key skew)."""
    try:
        site_url = (dg.get_service_setting(conn, "search_console_site") or "").strip()
        if site_url:
            return site_url
        if not dg.google_configured():
            return ""
        sites = dg.get_search_console_sites(conn)
        return (dg.preferred_site_url(conn, sites) or "").strip()
    except Exception:
        return ""


def _overview_goals_payload() -> dict[str, float | None]:
    """Optional daily chart goal lines from env (set on the dashboard host)."""

    def _f(name: str) -> float | None:
        raw = os.getenv(name, "").strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    return {
        "gsc_daily_clicks": _f("OVERVIEW_GOAL_GSC_DAILY_CLICKS"),
        "gsc_daily_impressions": _f("OVERVIEW_GOAL_GSC_DAILY_IMPRESSIONS"),
        "ga4_daily_sessions": _f("OVERVIEW_GOAL_GA4_DAILY_SESSIONS"),
        "ga4_daily_views": _f("OVERVIEW_GOAL_GA4_DAILY_VIEWS"),
    }


def _gsc_query_page_tables_uncached(raw: dict[str, Any]) -> bool:
    for key in ("queries", "pages"):
        s = raw.get(key)
        if not isinstance(s, dict):
            return True
        meta = s.get("_cache") or {}
        if not meta.get("exists"):
            return True
    return False


def _gsc_site_overview_for_summary(
    conn, period_mode: str, gsc_segment: str = "all"
) -> dict[str, Any]:
    tz_name = DEFAULT_DASHBOARD_TZ
    anchor = gsc_anchor_date_local()
    if not dg.google_configured():
        return _gsc_site_overview_placeholder(
            tz_name, period_mode, anchor, "Google OAuth not configured", gsc_segment
        )
    site_url = ""
    try:
        site_url = dg.get_service_setting(conn, "search_console_site")
        if not site_url:
            sites = dg.get_search_console_sites(conn)
            site_url = dg.preferred_site_url(conn, sites)
    except Exception as exc:
        return _gsc_site_overview_placeholder(
            tz_name, period_mode, anchor, str(exc), gsc_segment
        )
    if not site_url:
        return _gsc_site_overview_placeholder(
            tz_name, period_mode, anchor, "No Search Console property selected", gsc_segment
        )

    anchor, mode, w_cur, w_prev = _gsc_matched_period_windows(period_mode)

    raw = dg.get_search_console_overview_cached(
        conn,
        site_url=site_url,
        period_mode=mode,
        timezone_name=tz_name,
        anchor=anchor,
        current_start=w_cur.start,
        current_end=w_cur.end,
        previous_start=w_prev.start,
        previous_end=w_prev.end,
        url_segment=gsc_segment,
        refresh=False,
    )
    cache = _cache_payload(raw.get("_cache"))
    out: dict[str, Any] = {k: v for k, v in raw.items() if k != "_cache"}
    out["cache"] = cache
    return out


def _gsc_site_overview_placeholder(
    tz_name: str, period_mode: str, anchor, error: str, url_segment: str = "all"
) -> dict[str, Any]:
    seg = (url_segment or "all").strip().lower()
    return {
        "available": False,
        "timezone": tz_name,
        "period_mode": period_mode,
        "url_segment": seg,
        "anchor_date": anchor.isoformat(),
        "error": error,
        "current": None,
        "previous": None,
        "deltas": {},
        "series": [],
        "cache": _cache_payload(None),
    }


def _ga4_site_overview_for_summary(conn, period_mode: str) -> dict[str, Any]:
    tz_name = DEFAULT_DASHBOARD_TZ
    anchor = gsc_anchor_date_local()
    property_id = (dg.get_service_setting(conn, "ga4_property_id") or "").strip()
    if not dg.google_configured():
        return _ga4_site_overview_placeholder(
            tz_name, period_mode, anchor, "Google OAuth not configured"
        )
    if not property_id:
        return _ga4_site_overview_placeholder(
            tz_name, period_mode, anchor, "GA4 property ID not configured"
        )
    try:
        dg.get_google_access_token(conn)
    except Exception as exc:
        return _ga4_site_overview_placeholder(tz_name, period_mode, anchor, str(exc))

    _, mode, w_cur, w_prev = _gsc_matched_period_windows(period_mode)

    raw = dg.get_ga4_property_overview_cached(
        conn,
        property_id=property_id,
        period_mode=mode,
        timezone_name=tz_name,
        anchor=anchor,
        current_start=w_cur.start,
        current_end=w_cur.end,
        previous_start=w_prev.start,
        previous_end=w_prev.end,
        refresh=False,
    )
    cache = _cache_payload(raw.get("_cache"))
    out: dict[str, Any] = {k: v for k, v in raw.items() if k != "_cache"}
    out["cache"] = cache
    return out


def _ga4_site_overview_placeholder(
    tz_name: str, period_mode: str, anchor, error: str
) -> dict[str, Any]:
    return {
        "available": False,
        "timezone": tz_name,
        "period_mode": period_mode,
        "anchor_date": anchor.isoformat(),
        "error": error,
        "current": None,
        "previous": None,
        "deltas": {},
        "series": [],
        "cache": _cache_payload(None),
    }


def get_dashboard_summary(
    *,
    gsc_period: str = "rolling_30d",
    gsc_segment: str = "all",
) -> dict[str, Any]:
    gsc_segment = normalize_gsc_url_segment(gsc_segment)
    period = normalize_gsc_period_mode(gsc_period)
    articles_missing_meta = 0
    gsc_property_breakdowns = _empty_gsc_property_breakdowns_for_signals()
    gsc_queries: list[dict[str, Any]] = []
    gsc_pages: list[dict[str, Any]] = []
    gsc_performance_period: dict[str, str] = {"start_date": "", "end_date": ""}
    gsc_performance_error = ""
    last_dashboard_sync_at: str | None = None
    conn = open_db_connection()
    try:
        counts = dq.fetch_counts(conn)
        recent_runs = [dict(row) for row in dq.fetch_recent_runs(conn)]
        last_sync_raw = (dg.get_service_setting(conn, "last_dashboard_sync_finished_at") or "").strip()
        last_dashboard_sync_at = last_sync_raw or None
        overview = dq.fetch_overview_metrics(conn)
        articles_missing_meta = dq.count_blog_articles_missing_meta(conn)
        facts = dq.fetch_seo_facts(conn)
        top_pages = dq.fetch_top_organic_pages(conn)
        gsc_site = _gsc_site_overview_for_summary(conn, period, gsc_segment)
        ga4_site = _ga4_site_overview_for_summary(conn, period)
        indexing_rollup = build_indexing_rollup(facts)
        breakdown_site = _resolve_gsc_site_url_for_breakdowns(conn)
        gsc_property_breakdowns = _gsc_property_breakdowns_for_signals(conn, breakdown_site, period)
        if breakdown_site and gsc_site.get("available") and dg.google_configured():
            anchor_qp, mode_qp, w_cur_qp, _w_prev_qp = _gsc_matched_period_windows(period)
            gsc_qp_raw = dg.get_gsc_query_page_tables_cached(
                conn,
                site_url=breakdown_site,
                period_mode=mode_qp,
                anchor=anchor_qp,
                current_start=w_cur_qp.start,
                current_end=w_cur_qp.end,
                url_segment=gsc_segment,
                refresh=False,
            )
            if _gsc_query_page_tables_uncached(gsc_qp_raw):
                gsc_qp_raw = dg.get_gsc_query_page_tables_cached(
                    conn,
                    site_url=breakdown_site,
                    period_mode=mode_qp,
                    anchor=anchor_qp,
                    current_start=w_cur_qp.start,
                    current_end=w_cur_qp.end,
                    url_segment=gsc_segment,
                    refresh=True,
                )
            q_slice = gsc_qp_raw.get("queries") or {}
            p_slice = gsc_qp_raw.get("pages") or {}
            if isinstance(q_slice, dict):
                qr = q_slice.get("rows")
                if isinstance(qr, list):
                    gsc_queries = qr
            if isinstance(p_slice, dict):
                pr = p_slice.get("rows")
                if isinstance(pr, list):
                    gsc_pages = pr
            win = gsc_qp_raw.get("window")
            if isinstance(win, dict):
                gsc_performance_period = {
                    "start_date": win.get("start_date") or "",
                    "end_date": win.get("end_date") or "",
                }
            q_err = (q_slice.get("error") or "").strip() if isinstance(q_slice, dict) else ""
            p_err = (p_slice.get("error") or "").strip() if isinstance(p_slice, dict) else ""
            top_err = (gsc_qp_raw.get("error") or "").strip()
            gsc_performance_error = (
                top_err
                if top_err
                else "; ".join(part for part in (q_err, p_err) if part)
            )
    finally:
        conn.close()

    gsc = summarize_gsc(facts)
    ga4 = summarize_ga4(facts)
    metrics = {
        **overview,
        **gsc,
        **ga4,
    }
    catalog_completion = build_catalog_completion(
        counts,
        metrics,
        articles_missing_meta=articles_missing_meta,
    )
    return {
        "counts": counts,
        "metrics": metrics,
        "recent_runs": recent_runs,
        "last_dashboard_sync_at": last_dashboard_sync_at,
        "gsc_site": gsc_site,
        "ga4_site": ga4_site,
        "indexing_rollup": indexing_rollup,
        "catalog_completion": catalog_completion,
        "overview_goals": _overview_goals_payload(),
        "gsc_property_breakdowns": gsc_property_breakdowns,
        "top_pages": top_pages,
        "gsc_queries": gsc_queries,
        "gsc_pages": gsc_pages,
        "gsc_performance_period": gsc_performance_period,
        "gsc_performance_error": gsc_performance_error,
    }


# ---------------------------------------------------------------------------
# Sync / AI status and control
# ---------------------------------------------------------------------------

def get_sync_status() -> dict[str, Any]:
    payload = dict(SYNC_STATE)
    if payload.get("running"):
        try:
            payload["eta_seconds"] = compute_sync_eta_seconds(payload, str(DB_PATH))
        except Exception:
            payload["eta_seconds"] = None
    else:
        payload["eta_seconds"] = None
    return payload


def get_ai_status(job_id: str = "") -> dict[str, Any]:
    if job_id:
        with AI_JOBS_LOCK:
            state = AI_JOBS.get(job_id)
            if state:
                return dict(state)
        return {
            "job_id": job_id,
            "running": False,
            "cancel_requested": False,
            "scope": "",
            "mode": "",
            "object_type": "",
            "handle": "",
            "field": "",
            "started_at": 0,
            "finished_at": 0,
            "stage": "idle",
            "stage_label": "",
            "active_model": "",
            "stage_started_at": 0,
            "step_index": 0,
            "step_total": 0,
            "total": 0,
            "done": 0,
            "current": "",
            "successes": 0,
            "failures": 0,
            "last_error": "",
            "last_result": None,
            "steps": [],
        }
    return dict(AI_STATE)


def start_sync(scope: str, selected_scopes: list[str] | None = None, force_refresh: bool = False) -> tuple[bool, str, dict[str, Any]]:
    if SYNC_STATE["running"]:
        return False, "Sync already running", dict(SYNC_STATE)
    conn = open_db_connection()
    try:
        readiness = get_sync_scope_readiness(conn)
    finally:
        conn.close()
    _, norm_selected = _normalize_sync_scopes(scope, selected_scopes)
    filtered = filter_normalized_scopes_for_readiness(norm_selected, readiness)
    if not filtered:
        return (
            False,
            "No sync steps are ready. Configure Shopify (Settings → Data sources), then connect Google "
            "and complete OAuth in Google Signals for Search Console, GA4, indexing, and PageSpeed.",
            dict(SYNC_STATE),
        )
    new_scope, new_selected = _normalize_sync_scopes("custom", filtered)
    started = start_sync_background(DB_PATH, new_scope, new_selected, force_refresh=force_refresh)
    if started:
        clear_last_error()
        return True, "Sync started", dict(SYNC_STATE)
    return False, "Sync already running", dict(SYNC_STATE)


def stop_sync() -> tuple[bool, str, dict[str, Any]]:
    if not SYNC_STATE["running"]:
        return False, "No sync is currently running", dict(SYNC_STATE)
    request_sync_cancel()
    return True, "Stop requested", dict(SYNC_STATE)


def stop_ai(job_id: str = "") -> tuple[bool, str, dict[str, Any]]:
    state = get_ai_status(job_id)
    if not state["running"]:
        return False, "No AI generation is currently running", dict(AI_STATE)
    effective_id = job_id or state.get("job_id") or ""
    request_ai_cancel(effective_id)
    return True, "AI stop requested", get_ai_status(effective_id)


# ---------------------------------------------------------------------------
# Generic object AI / refresh operations (used by blogs and content routers)
# ---------------------------------------------------------------------------

def start_object_ai(kind: str, handle: str) -> tuple[bool, str, dict[str, Any]]:
    started, state = start_ai_object_background(DB_PATH, kind, handle)
    if started:
        clear_ai_last_error()
        return True, "AI generation started", state
    return False, "AI generation already running", state


def regenerate_object_field(kind: str, handle: str, field: str, accepted_fields: dict) -> dict:
    conn = open_db_connection()
    try:
        return dai.generate_field_recommendation(conn, kind, handle, field, accepted_fields)
    finally:
        conn.close()


def start_object_field_regeneration(kind: str, handle: str, field: str, accepted_fields: dict[str, str]) -> tuple[bool, str, dict[str, Any]]:
    started, state = start_ai_field_background(DB_PATH, kind, handle, field, accepted_fields)
    if started:
        clear_ai_last_error()
        return True, f"{field.replace('_', ' ')} regeneration started", state
    return False, "AI generation already running", state


def refresh_object(kind: str, handle: str, step: str | None = None, gsc_period: str = "mtd") -> tuple[bool, dict[str, Any]]:
    period = normalize_gsc_period_mode(gsc_period)
    try:
        label = "Article" if kind == "blog_article" else kind.title()
        if step:
            result = refresh_object_signal_step(
                open_db_connection, kind, handle, step, db_path=DB_PATH, gsc_period=period
            )
            return result["status"] != "error", {"message": result["message"], "result": result}
        results = refresh_object_signals(open_db_connection, kind, handle, db_path=DB_PATH, gsc_period=period)
        clear_last_error()
        overall_ok = not any(item["status"] == "error" for item in results.values())
        return overall_ok, {
            "message": f"{label} refresh completed." if overall_ok else f"{label} refresh completed with issues.",
            "steps": results,
        }
    except Exception as exc:
        record_last_error(exc)
        return False, {"message": str(exc), "steps": {}}


# ---------------------------------------------------------------------------
# Sidekick chat
# ---------------------------------------------------------------------------

def sidekick_chat(
    resource_type: str,
    handle: str,
    messages: list[dict[str, str]],
    client_draft: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Sidekick: one chat turn with optional structured field suggestions for the current detail page."""
    from backend.app.services.product_service import get_product_detail
    from backend.app.services.article_service import get_blog_article_detail
    from backend.app.services.content_service import get_content_detail

    conn = open_db_connection()
    try:
        if not dai.ai_configured(conn):
            raise ValueError("AI is not configured. Add an API key in Settings.")
        rt = (resource_type or "").strip().lower()
        if rt == "product":
            detail = get_product_detail(handle)
        elif rt == "collection":
            detail = get_content_detail("collection", handle)
        elif rt == "page":
            detail = get_content_detail("page", handle)
        elif rt == "blog_article":
            blog_h, sep, art_h = handle.partition("/")
            if not sep or not art_h:
                raise ValueError("Invalid blog article handle")
            detail = get_blog_article_detail(blog_h, art_h)
        else:
            raise ValueError("Invalid resource type")
        if not detail:
            raise ValueError("Resource not found")

        try:
            from backend.app.services.keyword_clustering import (
                load_clusters, _get_matched_cluster_keywords, compute_seo_gaps,
            )
            clusters_data = load_clusters(conn)
            from shopifyseo.dashboard_google import get_service_setting as _get_ss
            target_raw = _get_ss(conn, "target_keywords", "{}")
            target_data = json.loads(target_raw) if target_raw else {}

            vendor = ""
            if rt == "product":
                vendor = ((detail.get("draft") or detail.get("product") or {}).get("vendor", ""))

            cluster_ctx, all_kws, primary_kw, kw_map = _get_matched_cluster_keywords(
                clusters_data, target_data, rt, handle, conn=conn, vendor=vendor,
            )
            if all_kws:
                d = detail.get("draft") or {}
                content_fields = {
                    "title": d.get("title", ""),
                    "seo_title": d.get("seo_title", ""),
                    "seo_description": d.get("seo_description", ""),
                    "body": d.get("body_html") or d.get("body") or d.get("description_html") or "",
                }
                gaps = compute_seo_gaps(all_kws, content_fields, kw_map, rt, primary_kw)
                if gaps:
                    detail["seo_keyword_gaps"] = gaps
        except Exception:
            pass

        draft = dict(client_draft) if client_draft else None
        if draft and rt != "product":
            draft.pop("tags", None)
        return run_sidekick_turn(
            conn,
            resource_type=rt,
            handle=handle,
            detail=detail,
            messages=messages,
            client_draft=draft,
        )
    finally:
        conn.close()
