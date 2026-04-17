from __future__ import annotations

from typing import Any

import shopifyseo.dashboard_google as dg
from backend.app.db import open_db_connection
from backend.app.schemas.dashboard import normalize_gsc_period_mode
from backend.app.services.gsc_overview_calendar import (
    DEFAULT_DASHBOARD_TZ,
    gsc_anchor_date_local,
    last_two_full_month_windows,
    mtd_matched_windows,
    overview_site_report_windows,
    rolling_thirty_day_windows,
)
from backend.app.services.index_status import cache_status_kind, cache_status_label, cache_status_text


def _cache_payload(meta: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "label": cache_status_label(meta),
        "kind": cache_status_kind(meta),
        "text": cache_status_text(meta),
        "meta": meta,
    }


def gsc_matched_period_windows(period_mode: str):
    """Anchor, normalized mode, and (current, previous) windows — same logic as GSC/GA4 overview."""
    anchor = gsc_anchor_date_local()
    mode = normalize_gsc_period_mode(period_mode)
    if mode == "full_months":
        w_cur, w_prev = last_two_full_month_windows(anchor)
    elif mode == "since_2026_02_15":
        w_cur, w_prev = overview_site_report_windows(anchor)
    elif mode == "rolling_30d":
        w_cur, w_prev = rolling_thirty_day_windows(anchor)
    else:
        w_cur, w_prev = mtd_matched_windows(anchor)
    return anchor, mode, w_cur, w_prev


def _empty_gsc_property_breakdowns_for_signals() -> dict[str, Any]:
    dead_cache = _cache_payload(None)
    empty_slice = {"rows": [], "error": "", "cache": dead_cache, "top_bucket_impressions_pct_vs_prior": None}
    return {
        "available": False,
        "period_mode": "mtd",
        "anchor_date": "",
        "window": {"start_date": "", "end_date": ""},
        "country": empty_slice.copy(),
        "device": empty_slice.copy(),
        "searchAppearance": empty_slice.copy(),
        "errors": [],
        "error": "",
    }


def _gsc_property_breakdown_slice(raw: dict[str, Any] | None, key: str) -> dict[str, Any]:
    s = (raw or {}).get(key) if isinstance(raw, dict) else None
    if not isinstance(s, dict):
        return {"rows": [], "error": "", "cache": _cache_payload(None)}
    rows = s.get("rows")
    if not isinstance(rows, list):
        rows = []
    pct = s.get("top_bucket_impressions_pct_vs_prior")
    return {
        "rows": rows,
        "error": s.get("error") or "",
        "cache": _cache_payload(s.get("_cache")),
        "top_bucket_impressions_pct_vs_prior": pct if isinstance(pct, (int, float)) else None,
    }


def _gsc_property_breakdown_slices_uncached(raw: dict[str, Any]) -> bool:
    """True when no sqlite Tier A row exists yet for any dimension (same as sync refresh gap)."""
    for key in ("country", "device", "searchAppearance"):
        s = raw.get(key)
        if not isinstance(s, dict):
            return True
        meta = s.get("_cache") or {}
        if not meta.get("exists"):
            return True
    return False


def gsc_property_breakdowns_for_signals(conn, site_url: str, period_mode: str = "mtd") -> dict[str, Any]:
    anchor, mode, w_cur, w_prev = gsc_matched_period_windows(period_mode)
    url = (site_url or "").strip()
    raw = dg.get_gsc_property_breakdowns_cached(
        conn,
        site_url=url,
        period_mode=mode,
        anchor=anchor,
        current_start=w_cur.start,
        current_end=w_cur.end,
        previous_start=w_prev.start,
        previous_end=w_prev.end,
        refresh=False,
    )
    if url and dg.google_configured() and _gsc_property_breakdown_slices_uncached(raw):
        raw = dg.get_gsc_property_breakdowns_cached(
            conn,
            site_url=url,
            period_mode=mode,
            anchor=anchor,
            current_start=w_cur.start,
            current_end=w_cur.end,
            previous_start=w_prev.start,
            previous_end=w_prev.end,
            refresh=True,
        )
    window = raw.get("window")
    if not isinstance(window, dict):
        window = {"start_date": "", "end_date": ""}
    errs = raw.get("errors")
    if not isinstance(errs, list):
        errs = []
    return {
        "available": bool(raw.get("available")),
        "period_mode": raw.get("period_mode") or "mtd",
        "anchor_date": raw.get("anchor_date") or "",
        "window": {
            "start_date": window.get("start_date") or "",
            "end_date": window.get("end_date") or "",
        },
        "country": _gsc_property_breakdown_slice(raw, "country"),
        "device": _gsc_property_breakdown_slice(raw, "device"),
        "searchAppearance": _gsc_property_breakdown_slice(raw, "searchAppearance"),
        "errors": errs,
        "error": raw.get("error") or "",
    }


def get_google_signals_data() -> dict[str, Any]:
    conn = open_db_connection()
    try:
        configured = dg.google_configured()
        auth_url = "/auth/google/start" if configured else None
        if not configured:
            return {
                "configured": False,
                "connected": False,
                "auth_url": auth_url,
                "selected_site": "",
                "available_sites": [],
                "ga4_property_id": dg.get_service_setting(conn, "ga4_property_id"),
                "summary_period": {"start_date": "", "end_date": ""},
                "gsc_pages": [],
                "gsc_queries": [],
                "ga4_rows": [],
                "gsc_cache": _cache_payload(None),
                "ga4_cache": _cache_payload(None),
                "gsc_property_breakdowns": _empty_gsc_property_breakdowns_for_signals(),
                "error": "Google OAuth is not configured in the dashboard process.",
            }
        try:
            sites = dg.get_search_console_sites(conn)
            selected_site = dg.preferred_site_url(conn, sites)
            summary = dg.get_search_console_summary_cached(conn, refresh=False)
            ga4_property_id = dg.get_service_setting(conn, "ga4_property_id")
            ga4 = None
            ga4_error = ""
            if ga4_property_id:
                try:
                    ga4 = dg.get_ga4_summary(conn, refresh=False)
                except Exception as exc:
                    ga4_error = str(exc)
            return {
                "configured": True,
                "connected": True,
                "auth_url": auth_url,
                "selected_site": selected_site,
                "available_sites": [site["siteUrl"] for site in sites],
                "ga4_property_id": ga4_property_id,
                "summary_period": {
                    "start_date": summary.get("start_date", ""),
                    "end_date": summary.get("end_date", ""),
                },
                "gsc_pages": summary.get("pages", []),
                "gsc_queries": summary.get("queries", []),
                "ga4_rows": (ga4 or {}).get("rows", []),
                "gsc_cache": _cache_payload(summary.get("_cache")),
                "ga4_cache": _cache_payload((ga4 or {}).get("_cache")),
                "gsc_property_breakdowns": gsc_property_breakdowns_for_signals(conn, selected_site or ""),
                "error": ga4_error,
            }
        except Exception as exc:
            return {
                "configured": True,
                "connected": False,
                "auth_url": auth_url,
                "selected_site": dg.get_service_setting(conn, "search_console_site"),
                "available_sites": [],
                "ga4_property_id": dg.get_service_setting(conn, "ga4_property_id"),
                "summary_period": {"start_date": "", "end_date": ""},
                "gsc_pages": [],
                "gsc_queries": [],
                "ga4_rows": [],
                "gsc_cache": _cache_payload(None),
                "ga4_cache": _cache_payload(None),
                "gsc_property_breakdowns": _empty_gsc_property_breakdowns_for_signals(),
                "error": str(exc),
            }
    finally:
        conn.close()


def save_google_selection(site_url: str, ga4_property_id: str) -> str:
    conn = open_db_connection()
    try:
        dg.set_service_setting(conn, "search_console_site", site_url)
        dg.set_service_setting(conn, "ga4_property_id", ga4_property_id)
        dg.clear_google_caches(conn)
        return "Google settings saved"
    finally:
        conn.close()


def refresh_google_summary(scope: str) -> str:
    conn = open_db_connection()
    try:
        if scope == "ga4_summary":
            dg.delete_ga4_overview_cache(conn)
            dg.get_ga4_summary(conn, refresh=True)
            return "GA4 summary refreshed"
        dg.delete_search_console_overview_cache(conn)
        summary_payload = dg.get_search_console_summary_cached(conn, refresh=True)
        site_url = (summary_payload.get("site_url") or "").strip()
        if site_url:
            dg.refresh_gsc_property_breakdowns_for_site(conn, site_url)
        return "Search Console summary refreshed"
    finally:
        conn.close()
