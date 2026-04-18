"""Google Analytics 4 data fetching, caching, and aggregation."""

import sqlite3
import sys
from datetime import date, timedelta
from urllib.parse import urlparse

from ._cache import (
    CACHE_TTLS,
    _load_cached_payload,
    _pct_delta,
    _pct_delta_float,
    _write_cache_payload,
    ensure_google_cache_schema,
)
from ._auth import get_google_access_token, get_service_setting, google_api_get, google_api_post


def _pkg():
    """Return the shopifyseo.dashboard_google package namespace."""
    return sys.modules["shopifyseo.dashboard_google"]


# -- Cache keys ---------------------------------------------------------------

def _ga4_cache_key(property_id: str) -> str:
    return f"ga4_summary::{property_id}"


def _ga4_overview_cache_key(property_id: str, period_mode: str, anchor: str) -> str:
    return f"ga4_property_overview_v2::{period_mode}::{anchor}::{property_id}"


def delete_ga4_overview_cache(conn: sqlite3.Connection) -> None:
    """Invalidate GA4 property overview time-series (e.g. after a manual GA4 refresh)."""
    ensure_google_cache_schema(conn)
    conn.execute("DELETE FROM google_api_cache WHERE cache_type = ?", ("ga4_property_overview",))
    conn.commit()


# -- Data helpers -------------------------------------------------------------

def _normalize_ga4_date_string(value: str) -> str:
    raw = (value or "").strip()
    compact = raw.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return raw
    return ""


def _ga4_metric_values_int(metric_values: list, index: int) -> int:
    if index >= len(metric_values):
        return 0
    raw = metric_values[index].get("value", "0")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


def _ga4_metric_values_float(metric_values: list, index: int) -> float:
    if index >= len(metric_values):
        return 0.0
    raw = metric_values[index].get("value", "0")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


# -- Daily analytics fetching & rollup ----------------------------------------

def _fetch_ga4_daily_analytics(
    conn: sqlite3.Connection,
    property_id: str,
    start: date,
    end: date,
) -> list[dict]:
    access_token = get_google_access_token(conn)
    pid = (property_id or "").strip().removeprefix("properties/")
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport"
    resp = google_api_post(
        url,
        access_token,
        {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": "date"}],
            "metrics": [
                {"name": "sessions"},
                {"name": "screenPageViews"},
                {"name": "averageSessionDuration"},
                {"name": "newUsers"},
                {"name": "bounceRate"},
            ],
            "limit": 100000,
        },
    )
    out: list[dict] = []
    for row in resp.get("rows") or []:
        dims = row.get("dimensionValues") or []
        mets = row.get("metricValues") or []
        if not dims:
            continue
        ds = _normalize_ga4_date_string(dims[0].get("value") or "")
        if not ds:
            continue
        out.append({
            "date": ds,
            "sessions": _ga4_metric_values_int(mets, 0),
            "views": _ga4_metric_values_int(mets, 1),
            "avg_session_duration": _ga4_metric_values_float(mets, 2),
            "new_users": _ga4_metric_values_int(mets, 3),
            "bounce_rate": _ga4_metric_values_float(mets, 4),
        })
    return sorted(out, key=lambda r: r["date"])


def _rollup_ga4_window(
    rows_by_date: dict[str, dict],
    start: date,
    end: date,
) -> tuple[dict, list[dict]]:
    series: list[dict] = []
    sessions = 0
    views = 0
    new_users = 0
    duration_weighted = 0.0
    bounce_weighted = 0.0
    d = start
    while d <= end:
        ds = d.isoformat()
        row = rows_by_date.get(ds)
        if row:
            s = int(row["sessions"])
            v = int(row["views"])
            nu = int(row.get("new_users", 0))
            ad = float(row.get("avg_session_duration", 0) or 0)
            br = float(row.get("bounce_rate", 0) or 0)
            sessions += s
            views += v
            new_users += nu
            if s > 0:
                duration_weighted += ad * s
                bounce_weighted += br * s
            series.append({"date": ds, "sessions": s, "views": v})
        else:
            series.append({"date": ds, "sessions": 0, "views": 0})
        d += timedelta(days=1)
    avg_dur = round(duration_weighted / sessions, 2) if sessions > 0 else 0.0
    avg_bounce = round(bounce_weighted / sessions, 6) if sessions > 0 else 0.0
    totals = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "sessions": sessions,
        "views": views,
        "new_users": new_users,
        "avg_session_duration": avg_dur,
        "bounce_rate": avg_bounce,
    }
    return totals, series


# -- GA4 property overview (time-series) -------------------------------------

def get_ga4_property_overview_cached(
    conn: sqlite3.Connection,
    *,
    property_id: str,
    period_mode: str,
    timezone_name: str,
    anchor: date,
    current_start: date,
    current_end: date,
    previous_start: date,
    previous_end: date,
    refresh: bool = False,
) -> dict:
    """GA4 property totals and daily sessions/views for matched calendar windows (overview dashboard)."""
    pid_key = (property_id or "").strip().removeprefix("properties/")
    cache_key = _ga4_overview_cache_key(pid_key, period_mode, anchor.isoformat())
    if not refresh:
        cached_payload, meta = _load_cached_payload(conn, cache_key)
        if cached_payload and isinstance(cached_payload, dict):
            return {**cached_payload, "_cache": meta}

    empty_cache = {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}

    def _error_payload(message: str) -> dict:
        return {
            "available": False,
            "timezone": timezone_name,
            "period_mode": period_mode,
            "anchor_date": anchor.isoformat(),
            "error": message,
            "current": None,
            "previous": None,
            "deltas": {},
            "series": [],
            "_cache": empty_cache,
        }

    try:
        union_start = min(current_start, previous_start)
        union_end = max(current_end, previous_end)
        daily = _fetch_ga4_daily_analytics(conn, property_id, union_start, union_end)
    except Exception as exc:
        return _error_payload(str(exc))

    by_date = {r["date"]: r for r in daily}
    current_totals, series = _rollup_ga4_window(by_date, current_start, current_end)
    previous_totals, _ = _rollup_ga4_window(by_date, previous_start, previous_end)
    bounce_pp = None
    if current_totals["sessions"] > 0 and previous_totals["sessions"] > 0:
        bounce_pp = round(
            (current_totals["bounce_rate"] - previous_totals["bounce_rate"]) * 100.0,
            2,
        )
    deltas = {
        "sessions_pct": _pct_delta(current_totals["sessions"], previous_totals["sessions"]),
        "views_pct": _pct_delta(current_totals["views"], previous_totals["views"]),
        "new_users_pct": _pct_delta(current_totals["new_users"], previous_totals["new_users"]),
        "avg_session_duration_pct": _pct_delta_float(
            float(current_totals["avg_session_duration"]),
            float(previous_totals["avg_session_duration"]),
        ),
        "bounce_rate_pp": bounce_pp,
    }
    payload_body = {
        "available": True,
        "timezone": timezone_name,
        "period_mode": period_mode,
        "anchor_date": anchor.isoformat(),
        "error": None,
        "current": current_totals,
        "previous": previous_totals,
        "deltas": deltas,
        "series": series,
    }
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="ga4_property_overview",
        payload=payload_body,
        ttl_seconds=CACHE_TTLS["ga4_property_overview"],
    )
    return {**payload_body, "_cache": meta}


# -- GA4 properties list ------------------------------------------------------

def get_ga4_properties(conn: sqlite3.Connection) -> dict:
    """List GA4 properties via the Admin API. Returns {properties, error, activation_url}."""
    import json
    from ..dashboard_http import HttpRequestError

    access_token = get_google_access_token(conn)
    try:
        payload = google_api_get(
            "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
            access_token,
            timeout=15,
        )
    except HttpRequestError as exc:
        activation_url = ""
        if exc.status == 403 and exc.body:
            try:
                err_body = json.loads(exc.body)
                for detail in (err_body.get("error", {}).get("details") or []):
                    meta = detail.get("metadata") or {}
                    url = meta.get("activationUrl", "")
                    if url:
                        activation_url = url
                        break
                    for link in (detail.get("links") or []):
                        url = link.get("url", "")
                        if "console.developers.google.com" in url:
                            activation_url = url
                            break
            except (json.JSONDecodeError, AttributeError):
                pass
        return {"properties": [], "error": str(exc), "activation_url": activation_url}
    properties: list[dict] = []
    for account in payload.get("accountSummaries", []):
        account_name = account.get("displayName", "")
        for prop in account.get("propertySummaries", []):
            prop_resource = prop.get("property", "")
            prop_id = prop_resource.removeprefix("properties/")
            properties.append({
                "property_id": prop_id,
                "display_name": prop.get("displayName", prop_id),
                "account_name": account_name,
            })
    return {"properties": properties, "error": "", "activation_url": ""}


# -- GA4 summary (legacy 28-day landing/pageview report) ----------------------

# GA4 Data API allows up to 100k rows per request; paginate with offset for full exports.
_GA4_SUMMARY_REPORT_PAGE_SIZE = 10_000


def _ga4_collect_report_rows(
    property_id: str,
    access_token: str,
    *,
    start_date: date,
    end_date: date,
    dimensions: list[dict],
    metrics: list[dict],
    order_bys: list[dict],
) -> list[dict]:
    """Run ``runReport`` with pagination until all rows are returned."""
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    out: list[dict] = []
    offset = 0
    while True:
        body: dict = {
            "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
            "dimensions": dimensions,
            "metrics": metrics,
            "orderBys": order_bys,
            "limit": _GA4_SUMMARY_REPORT_PAGE_SIZE,
            "offset": offset,
        }
        resp = google_api_post(url, access_token, body)
        rows = resp.get("rows") or []
        out.extend(rows)
        if len(rows) < _GA4_SUMMARY_REPORT_PAGE_SIZE:
            break
        offset += _GA4_SUMMARY_REPORT_PAGE_SIZE
    return out


def ga4_report_page_path_from_row(row: dict) -> str:
    """First dimension value for a ``runReport`` row (e.g. pagePathPlusQueryString)."""
    dims = row.get("dimensionValues") or []
    if not dims:
        return ""
    return (dims[0].get("value") or "").strip()


def get_ga4_summary(conn: sqlite3.Connection, refresh: bool = False) -> dict:
    gsc_cache = _pkg().GSC_CACHE
    property_id = get_service_setting(conn, "ga4_property_id")
    if not property_id:
        raise RuntimeError("GA4 property ID is not configured")
    cache_key = _ga4_cache_key(property_id)
    if not refresh and gsc_cache["ga4"] is not None:
        return gsc_cache["ga4"]
    cached_payload, meta = _load_cached_payload(conn, cache_key)
    if cached_payload and not refresh:
        payload = {**cached_payload, "_cache": meta}
        gsc_cache["ga4"] = payload
        return payload
    if not refresh:
        return {"rows": [], "start_date": "", "end_date": "", "_cache": meta}
    access_token = get_google_access_token(conn)
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=27)
    landing_rows = _ga4_collect_report_rows(
        property_id,
        access_token,
        start_date=start_date,
        end_date=end_date,
        dimensions=[{"name": "landingPagePlusQueryString"}],
        metrics=[{"name": "sessions"}, {"name": "averageSessionDuration"}],
        order_bys=[{"metric": {"metricName": "sessions"}, "desc": True}],
    )
    page_rows = _ga4_collect_report_rows(
        property_id,
        access_token,
        start_date=start_date,
        end_date=end_date,
        dimensions=[{"name": "pagePathPlusQueryString"}],
        metrics=[{"name": "screenPageViews"}],
        order_bys=[{"metric": {"metricName": "screenPageViews"}, "desc": True}],
    )
    payload = {
        "rows": landing_rows,
        "landing_rows": landing_rows,
        "page_rows": page_rows,
    }
    payload["start_date"] = start_date.isoformat()
    payload["end_date"] = end_date.isoformat()
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="ga4_summary",
        payload=payload,
        ttl_seconds=CACHE_TTLS["ga4_summary"],
    )
    payload = {**payload, "_cache": meta}
    gsc_cache["ga4"] = payload
    return payload


# -- Per-URL GA4 (filtered runReport, same 28d window as get_ga4_summary) --------


def _ga4_property_id_numeric(property_id: str) -> str:
    return (property_id or "").strip().removeprefix("properties/")


def ga4_path_candidates(url: str) -> list[str]:
    """Paths to try when filtering GA4 (trailing slash variants)."""
    path = urlparse(url or "").path or "/"
    if not path.startswith("/"):
        path = "/" + path
    candidates = [path]
    if path != "/" and path.endswith("/"):
        alt = path.rstrip("/")
        if alt not in candidates:
            candidates.append(alt)
    elif path != "/":
        candidates.append(path + "/")
    out: list[str] = []
    seen: set[str] = set()
    for p in candidates:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _ga4_url_detail_cache_key(property_id: str, url: str, start: date, end: date) -> str:
    pid = _ga4_property_id_numeric(property_id)
    return f"ga4_url::{pid}::{start.isoformat()}_{end.isoformat()}::{url}"


def _ga4_first_row_int(resp: dict, idx: int) -> int:
    rows = resp.get("rows") or []
    if not rows:
        return 0
    mets = rows[0].get("metricValues") or []
    return _ga4_metric_values_int(mets, idx)


def _ga4_first_row_float(resp: dict, idx: int) -> float:
    rows = resp.get("rows") or []
    if not rows:
        return 0.0
    mets = rows[0].get("metricValues") or []
    return _ga4_metric_values_float(mets, idx)


def _ga4_run_filtered_report(
    access_token: str,
    pid: str,
    start: date,
    end: date,
    metrics: list[str],
    field_name: str,
    path: str,
    *,
    match_type: str = "EXACT",
) -> dict:
    body: dict = {
        "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
        "metrics": [{"name": m} for m in metrics],
        "dimensionFilter": {
            "filter": {
                "fieldName": field_name,
                "stringFilter": {"matchType": match_type, "value": path},
            }
        },
    }
    return google_api_post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
        access_token,
        body,
    )


def _ga4_pick_path_for_url(
    access_token: str,
    pid: str,
    start: date,
    end: date,
    candidates: list[str],
) -> str:
    """Prefer a path whose pagePath filter returns data (helps slash/query mismatches)."""
    for path in candidates:
        resp = _ga4_run_filtered_report(
            access_token, pid, start, end, ["screenPageViews"], "pagePath", path, match_type="EXACT"
        )
        rows = resp.get("rows") or []
        if rows:
            return path
    return candidates[0] if candidates else "/"


def get_ga4_url_detail(
    conn: sqlite3.Connection,
    url: str,
    *,
    refresh: bool = False,
    object_type: str = "",
    object_handle: str = "",
) -> dict:
    """Per-URL GA4 totals for the same 28-day window as get_ga4_summary."""
    gsc_cache = _pkg().GSC_CACHE
    ga4_mem: dict = gsc_cache["ga4_per_url"]
    property_id = get_service_setting(conn, "ga4_property_id")
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=27)

    if not property_id:
        return {
            "url": url,
            "path_used": "",
            "views": None,
            "sessions": None,
            "avg_session_duration": None,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "_cache": {"exists": False, "stale": True, "fetched_at": None, "expires_at": None},
        }

    cache_key = _ga4_url_detail_cache_key(property_id, url, start_date, end_date)
    if not refresh and cache_key in ga4_mem:
        return ga4_mem[cache_key]

    cached_payload, meta = _load_cached_payload(conn, cache_key)
    if cached_payload and not refresh:
        payload = {**cached_payload, "_cache": meta}
        ga4_mem[cache_key] = payload
        return payload
    if not refresh:
        empty = {
            "url": url,
            "path_used": "",
            "views": None,
            "sessions": None,
            "avg_session_duration": None,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        return {**empty, "_cache": meta}

    access_token = get_google_access_token(conn)
    pid = _ga4_property_id_numeric(property_id)
    candidates = ga4_path_candidates(url)
    path_used = _ga4_pick_path_for_url(access_token, pid, start_date, end_date, candidates)

    views_resp = _ga4_run_filtered_report(
        access_token, pid, start_date, end_date, ["screenPageViews"], "pagePath", path_used, match_type="EXACT"
    )
    views = _ga4_first_row_int(views_resp, 0)

    land_resp = _ga4_run_filtered_report(
        access_token,
        pid,
        start_date,
        end_date,
        ["sessions", "averageSessionDuration"],
        "landingPagePlusQueryString",
        path_used,
        match_type="EXACT",
    )
    sessions = _ga4_first_row_int(land_resp, 0)
    avg_dur = _ga4_first_row_float(land_resp, 1)

    payload = {
        "url": url,
        "path_used": path_used,
        "views": views,
        "sessions": sessions,
        "avg_session_duration": avg_dur,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="ga4_url",
        payload=payload,
        ttl_seconds=CACHE_TTLS["ga4_url"],
        object_type=object_type,
        object_handle=object_handle,
        url=url,
    )
    out = {**payload, "_cache": meta}
    ga4_mem[cache_key] = out
    return out


def ga4_url_cache_stale(conn: sqlite3.Connection, url: str) -> bool:
    """True when there is no non-expired ga4_url cache for this URL (current 28-day window)."""
    property_id = get_service_setting(conn, "ga4_property_id")
    if not property_id:
        return True
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=27)
    cache_key = _ga4_url_detail_cache_key(property_id, url, start_date, end_date)
    _payload, meta = _load_cached_payload(conn, cache_key)
    return bool(meta.get("stale", True))

