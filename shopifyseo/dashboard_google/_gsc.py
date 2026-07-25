"""Google Search Console analytics, URL inspection, and PageSpeed Insights.

All GSC-related data fetching, caching, and aggregation lives here, along with
PageSpeed Insights (which shares the same OAuth token) and the shared
``clear_google_caches`` utility.
"""

import logging
import os
import random
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlencode

from ..dashboard_http import HttpRequestError
from ..gsc_query_limits import GSC_CATALOG_PERIOD_MODE, GSC_PER_URL_QUERY_ROW_LIMIT
from ._cache import (
    CACHE_TTLS,
    GSC_PROPERTY_BREAKDOWN_ROW_CAP,
    GSC_PROPERTY_BREAKDOWN_SPECS,
    GSC_QUERY_PAGE_ROW_CAP,
    _load_cached_payload,
    _pct_delta,
    _write_cache_payload,
    ensure_google_cache_schema,
)
from ._auth import (
    get_google_access_token,
    get_service_setting,
    google_api_get,
    google_api_post,
    google_token_has_scope,
    set_service_setting,
)


logger = logging.getLogger(__name__)


def _pkg():
    """Return the shopifyseo.dashboard_google package namespace."""
    return sys.modules["shopifyseo.dashboard_google"]


def _default_acquire_pagespeed_http_rate_slot(cancel_check=None) -> None:
    """Import on first use so ``dashboard_store`` → ``dashboard_google`` load order avoids a circular import."""
    from ..dashboard_actions._state import acquire_pagespeed_http_rate_slot

    acquire_pagespeed_http_rate_slot(cancel_check)


# -- Cache key helpers --------------------------------------------------------

def _summary_cache_key(site_url: str) -> str:
    return f"search_console_summary::{site_url}"


def _url_detail_cache_key(site_url: str, url: str, period_mode: str = GSC_CATALOG_PERIOD_MODE) -> str:
    mode = _normalize_gsc_period_mode(period_mode)
    return f"search_console_url::{site_url}::{mode}::{url}"


def _per_url_memory_cache_key(period_mode: str, url: str) -> str:
    return f"{_normalize_gsc_period_mode(period_mode)}::{url}"


def _inspection_cache_key(site_url: str, url: str) -> str:
    return f"url_inspection::{site_url}::{url}"


def _pagespeed_cache_key(url: str, strategy: str) -> str:
    return f"pagespeed::{strategy}::{url}"


def _overview_cache_key(site_url: str, period_mode: str, anchor: str, url_segment: str = "all") -> str:
    seg = (url_segment or "all").strip().lower()
    return f"search_console_overview_v2::{period_mode}::{anchor}::{seg}::{site_url}"


def _property_breakdown_cache_key(
    site_url: str, period_mode: str, anchor_iso: str, dimension: str, *, prev: bool = False
) -> str:
    prefix = "gsc_property_breakdown_prev" if prev else "gsc_property_breakdown"
    return f"{prefix}::{dimension}::{period_mode}::{anchor_iso}::{site_url}"


def _query_page_table_cache_key(
    site_url: str, period_mode: str, anchor_iso: str, dimension: str, url_segment: str
) -> str:
    seg = (url_segment or "all").strip().lower()
    return f"gsc_qp_table::{dimension}::{period_mode}::{anchor_iso}::{seg}::{site_url}"


# -- Period/segment utilities -------------------------------------------------

def _normalize_gsc_period_mode(raw: str) -> str:
    k = (raw or "mtd").strip().lower()
    return k if k in ("mtd", "full_months", "since_2026_02_15", "rolling_30d", "last_16_months") else "mtd"


def _gsc_url_segment_page_contains(segment: str) -> str | None:
    """Shopify-style path contains filters for Search Analytics `page` dimension."""
    key = (segment or "all").strip().lower()
    mapping: dict[str, str | None] = {
        "all": None,
        "products": "/products/",
        "collections": "/collections/",
        "pages": "/pages/",
        "blogs": "/blogs/",
    }
    return mapping.get(key, None)


def gsc_url_report_window(period_mode: str = "mtd") -> tuple[date, date]:
    """Current GSC window aligned with Overview (dashboard TZ anchor, MTD or last full month)."""
    from backend.app.services.gsc_overview_calendar import (
        gsc_anchor_date_local,
        last_two_full_month_windows,
        mtd_matched_windows,
        overview_site_report_windows,
        rolling_thirty_day_windows,
    )

    mode = _normalize_gsc_period_mode(period_mode)
    anchor = gsc_anchor_date_local()
    if mode == "full_months":
        cur, _prev = last_two_full_month_windows(anchor)
    elif mode in ("since_2026_02_15", "last_16_months"):
        cur, _prev = overview_site_report_windows(anchor)
    elif mode == "rolling_30d":
        cur, _prev = rolling_thirty_day_windows(anchor)
    else:
        cur, _prev = mtd_matched_windows(anchor)
    return cur.start, cur.end


# -- Cache invalidation -------------------------------------------------------

def invalidate_pagespeed_memory_cache(url: str, strategy: str | None = None) -> None:
    """Drop in-process PageSpeed cache for this URL so the next read loads from SQLite.

    If ``strategy`` is None, evict both mobile and desktop entries for ``url``.
    """
    bucket = _pkg().GSC_CACHE["pagespeed"]
    if strategy:
        bucket.pop(f"{strategy}:{url}", None)
        return
    for strat in ("mobile", "desktop"):
        bucket.pop(f"{strat}:{url}", None)


def delete_search_console_overview_cache(conn: sqlite3.Connection) -> None:
    """Invalidate overview time-series and property-level dimensional caches (manual GSC refresh)."""
    ensure_google_cache_schema(conn)
    conn.execute("DELETE FROM google_api_cache WHERE cache_type = ?", ("search_console_overview",))
    conn.execute(
        "DELETE FROM google_api_cache WHERE cache_type IN (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "gsc_property_country",
            "gsc_property_country_prev",
            "gsc_property_device",
            "gsc_property_device_prev",
            "gsc_property_search_appearance",
            "gsc_property_search_appearance_prev",
            "gsc_property_query",
            "gsc_property_page",
        ),
    )
    conn.commit()


def delete_search_console_overview_timeseries_only(conn: sqlite3.Connection) -> None:
    """Drop only site-level GSC overview rollup rows (``search_console_overview``).

    Tier A property breakdown caches (``gsc_property_*``) are left intact.
    Used after a bulk Search Console sync so ``/api/summary`` Cache metadata
    reflects the run; bulk sync refreshes ``search_console_summary`` and URL
    caches but does not otherwise rewrite this cache type.
    """
    ensure_google_cache_schema(conn)
    conn.execute("DELETE FROM google_api_cache WHERE cache_type = ?", ("search_console_overview",))
    conn.commit()


def clear_google_caches(conn: sqlite3.Connection | None = None) -> None:
    gsc_cache = _pkg().GSC_CACHE
    gsc_cache["summary"] = None
    gsc_cache["ga4"] = None
    if gsc_cache.get("ga4_per_url") is not None:
        gsc_cache["ga4_per_url"].clear()
    gsc_cache["per_url"].clear()
    gsc_cache["inspection"].clear()
    gsc_cache["pagespeed"].clear()
    if conn is not None:
        ensure_google_cache_schema(conn)
        conn.execute("DELETE FROM google_api_cache")
        conn.commit()


# -- GSC data fetching & rollup -----------------------------------------------

def _fetch_gsc_daily_analytics(
    conn: sqlite3.Connection,
    site_url: str,
    start: date,
    end: date,
    *,
    page_contains: str | None = None,
) -> list[dict]:
    access_token = get_google_access_token(conn)
    body: dict = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["date"],
        "rowLimit": 25000,
    }
    if page_contains:
        body["dimensionFilterGroups"] = [
            {
                "groupType": "and",
                "filters": [
                    {
                        "dimension": "page",
                        "operator": "contains",
                        "expression": page_contains,
                    }
                ],
            }
        ]
    rows = google_api_post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
        access_token,
        body,
    ).get("rows", [])
    out: list[dict] = []
    for row in rows:
        keys = row.get("keys") or []
        if not keys:
            continue
        out.append({
            "date": keys[0],
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "ctr": float(row.get("ctr") or 0),
            "position": float(row.get("position") or 0),
        })
    return sorted(out, key=lambda r: r["date"])


def _rollup_gsc_window(
    rows_by_date: dict[str, dict],
    start: date,
    end: date,
) -> tuple[dict, list[dict]]:
    series: list[dict] = []
    clicks = 0
    impressions = 0
    pos_weight = 0.0
    d = start
    while d <= end:
        ds = d.isoformat()
        row = rows_by_date.get(ds)
        if row:
            c = int(row["clicks"])
            im = int(row["impressions"])
            clicks += c
            impressions += im
            pos_weight += float(row.get("position") or 0) * im
            ctr_pct = round((c / im) * 100.0, 3) if im else 0.0
            pos = float(row.get("position") or 0)
            daily_pos = round(pos, 2) if pos > 0 else None
            series.append({
                "date": ds,
                "clicks": c,
                "impressions": im,
                "ctr_pct": ctr_pct,
                "position": daily_pos,
            })
        else:
            series.append({
                "date": ds,
                "clicks": 0,
                "impressions": 0,
                "ctr_pct": 0.0,
                "position": None,
            })
        d += timedelta(days=1)
    ctr = (clicks / impressions) if impressions else 0.0
    position = (pos_weight / impressions) if impressions else None
    totals = {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(ctr, 6),
        "position": round(position, 4) if position is not None else None,
    }
    return totals, series


def _gsc_avg_position_improvement_pct(
    current: float | None, previous: float | None
) -> float | None:
    """Positive % = average position moved toward 1 (better). None if not comparable."""
    if current is None or previous is None:
        return None
    if previous <= 0 or current <= 0:
        return None
    return round((previous - current) / previous * 100.0, 2)


# -- GSC overview (time-series) -----------------------------------------------

def get_search_console_overview_cached(
    conn: sqlite3.Connection,
    *,
    site_url: str,
    period_mode: str,
    timezone_name: str,
    anchor: date,
    current_start: date,
    current_end: date,
    previous_start: date,
    previous_end: date,
    url_segment: str = "all",
    refresh: bool = False,
) -> dict:
    """Site-level GSC totals and daily series for two matched calendar windows (overview dashboard)."""
    seg_norm = (url_segment or "all").strip().lower()
    if _gsc_url_segment_page_contains(seg_norm) is None and seg_norm != "all":
        seg_norm = "all"
    page_filter = _gsc_url_segment_page_contains(seg_norm)
    cache_key = _overview_cache_key(site_url, period_mode, anchor.isoformat(), seg_norm)
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
            "url_segment": seg_norm,
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
        daily = _fetch_gsc_daily_analytics(
            conn, site_url, union_start, union_end, page_contains=page_filter
        )
    except Exception as exc:
        return _error_payload(str(exc))

    by_date = {r["date"]: r for r in daily}
    current_totals, series = _rollup_gsc_window(by_date, current_start, current_end)
    previous_totals, _ = _rollup_gsc_window(by_date, previous_start, previous_end)
    deltas = {
        "clicks_pct": _pct_delta(current_totals["clicks"], previous_totals["clicks"]),
        "impressions_pct": _pct_delta(current_totals["impressions"], previous_totals["impressions"]),
        "position_improvement_pct": _gsc_avg_position_improvement_pct(
            current_totals.get("position"),
            previous_totals.get("position"),
        ),
    }
    payload_body = {
        "available": True,
        "timezone": timezone_name,
        "period_mode": period_mode,
        "url_segment": seg_norm,
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
        cache_type="search_console_overview",
        payload=payload_body,
        ttl_seconds=CACHE_TTLS["search_console_overview"],
    )
    return {**payload_body, "_cache": meta}


# -- GSC property breakdowns (country / device / searchAppearance) ------------

_PROPERTY_BREAKDOWN_DIMS = frozenset({"country", "device", "searchAppearance"})


def _normalize_gsc_breakdown_rows(raw_rows: list) -> list[dict]:
    out: list[dict] = []
    for row in raw_rows:
        keys = row.get("keys") or []
        if not keys:
            continue
        out.append({
            "keys": keys,
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "ctr": float(row.get("ctr") or 0),
            "position": float(row.get("position") or 0),
        })
    return out


def _fetch_gsc_property_breakdown(
    conn: sqlite3.Connection,
    site_url: str,
    start: date,
    end: date,
    *,
    dimension: str,
    row_limit: int = GSC_PROPERTY_BREAKDOWN_ROW_CAP,
) -> tuple[list[dict], str | None]:
    """Single-dimension searchAnalytics/query; returns (rows, error_message)."""
    if dimension not in _PROPERTY_BREAKDOWN_DIMS:
        return [], f"unsupported dimension: {dimension}"
    access_token = get_google_access_token(conn)
    body: dict = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": [dimension],
        "rowLimit": row_limit,
        "orderBys": [{"metric": "IMPRESSIONS", "direction": "DESCENDING"}],
    }
    try:
        resp = google_api_post(
            f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
            access_token,
            body,
        )
    except HttpRequestError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], str(exc)
    rows = _normalize_gsc_breakdown_rows(resp.get("rows") or [])
    return rows, None


def _top_bucket_impressions_pct_vs_prior(current_rows: list, previous_rows: list | None) -> float | None:
    """% change for the current window's #1 bucket vs the same dimension key in the prior window."""
    if previous_rows is None:
        return None
    if not current_rows:
        return None
    top = current_rows[0]
    keys = top.get("keys") or []
    if not keys:
        return None
    raw_key = str(keys[0] or "").strip()
    if not raw_key:
        return None
    cur_imp = int(top.get("impressions") or 0)
    prev_imp = 0
    for r in previous_rows:
        rk = r.get("keys") or []
        if rk and str(rk[0] or "").strip() == raw_key:
            prev_imp = int(r.get("impressions") or 0)
            break
    return _pct_delta(cur_imp, prev_imp)


def get_gsc_property_breakdowns_cached(
    conn: sqlite3.Connection,
    *,
    site_url: str,
    period_mode: str,
    anchor: date,
    current_start: date,
    current_end: date,
    previous_start: date | None = None,
    previous_end: date | None = None,
    refresh: bool = False,
) -> dict:
    """Property-level GSC breakdowns (country, device, searchAppearance) for the current overview window."""
    empty_cache = {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}
    anchor_iso = anchor.isoformat()
    window = {
        "start_date": current_start.isoformat(),
        "end_date": current_end.isoformat(),
    }

    def _empty_slice() -> dict:
        return {
            "rows": [],
            "error": "",
            "_cache": empty_cache,
            "top_bucket_impressions_pct_vs_prior": None,
        }

    if not site_url:
        return {
            "available": False,
            "period_mode": period_mode,
            "anchor_date": anchor_iso,
            "window": window,
            "error": "No Search Console property selected",
            "country": _empty_slice(),
            "device": _empty_slice(),
            "searchAppearance": _empty_slice(),
            "errors": [],
        }

    errors: list[dict] = []
    country = _empty_slice()
    device = _empty_slice()
    search_appearance = _empty_slice()
    slices_by_dim = {
        "country": country,
        "device": device,
        "searchAppearance": search_appearance,
    }
    want_prior = previous_start is not None and previous_end is not None

    for dimension, cache_type, cache_type_prev in GSC_PROPERTY_BREAKDOWN_SPECS:
        cache_key = _property_breakdown_cache_key(site_url, period_mode, anchor_iso, dimension, prev=False)
        slice_ref = slices_by_dim[dimension]
        if not refresh:
            cached_payload, meta = _load_cached_payload(conn, cache_key)
            if cached_payload and isinstance(cached_payload, dict):
                rows = cached_payload.get("rows")
                if isinstance(rows, list):
                    slice_ref["rows"] = rows
                slice_ref["error"] = (cached_payload.get("error") or "") or ""
                slice_ref["_cache"] = meta
        else:
            rows, err = _fetch_gsc_property_breakdown(
                conn, site_url, current_start, current_end, dimension=dimension
            )
            payload_body = {
                "dimension": dimension,
                "period_mode": period_mode,
                "anchor_date": anchor_iso,
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "rows": rows,
                "error": err or "",
            }
            meta = _write_cache_payload(
                conn,
                cache_key=cache_key,
                cache_type=cache_type,
                payload=payload_body,
                ttl_seconds=CACHE_TTLS[cache_type],
            )
            slice_ref["rows"] = rows
            slice_ref["error"] = err or ""
            slice_ref["_cache"] = meta
            if err:
                errors.append({"dimension": dimension, "message": err})

        prev_rows: list | None = None
        if want_prior:
            prev_key = _property_breakdown_cache_key(site_url, period_mode, anchor_iso, dimension, prev=True)
            if not refresh:
                cached_prev, _meta_p = _load_cached_payload(conn, prev_key)
                if cached_prev and isinstance(cached_prev, dict):
                    pr = cached_prev.get("rows")
                    prev_rows = pr if isinstance(pr, list) else []
                else:
                    prev_rows = None
            else:
                pr, perr = _fetch_gsc_property_breakdown(
                    conn, site_url, previous_start, previous_end, dimension=dimension
                )
                prev_window = {
                    "start_date": previous_start.isoformat(),
                    "end_date": previous_end.isoformat(),
                }
                payload_prev = {
                    "dimension": dimension,
                    "period_mode": period_mode,
                    "anchor_date": anchor_iso,
                    "start_date": prev_window["start_date"],
                    "end_date": prev_window["end_date"],
                    "rows": pr,
                    "error": perr or "",
                }
                _write_cache_payload(
                    conn,
                    cache_key=prev_key,
                    cache_type=cache_type_prev,
                    payload=payload_prev,
                    ttl_seconds=CACHE_TTLS[cache_type_prev],
                )
                prev_rows = pr
                if perr:
                    errors.append({"dimension": f"{dimension}_prior", "message": perr})

        slice_ref["top_bucket_impressions_pct_vs_prior"] = _top_bucket_impressions_pct_vs_prior(
            slice_ref["rows"], prev_rows
        )

    return {
        "available": True,
        "period_mode": period_mode,
        "anchor_date": anchor_iso,
        "window": window,
        "error": "",
        "country": country,
        "device": device,
        "searchAppearance": search_appearance,
        "errors": errors,
    }


# -- GSC query/page tables ----------------------------------------------------

def _fetch_gsc_query_page_dimension(
    conn: sqlite3.Connection,
    site_url: str,
    start: date,
    end: date,
    *,
    dimension: str,
    page_contains: str | None,
    row_limit: int,
) -> tuple[list[dict], str | None]:
    if dimension not in ("query", "page"):
        return [], f"unsupported dimension: {dimension}"
    access_token = get_google_access_token(conn)
    body: dict = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": [dimension],
        "rowLimit": row_limit,
        "orderBys": [{"metric": "CLICKS", "direction": "DESCENDING"}],
    }
    if page_contains:
        body["dimensionFilterGroups"] = [
            {
                "groupType": "and",
                "filters": [
                    {
                        "dimension": "page",
                        "operator": "contains",
                        "expression": page_contains,
                    }
                ],
            }
        ]
    try:
        resp = google_api_post(
            f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
            access_token,
            body,
        )
    except HttpRequestError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], str(exc)
    rows = _normalize_gsc_breakdown_rows(resp.get("rows") or [])
    return rows, None


def get_gsc_query_page_tables_cached(
    conn: sqlite3.Connection,
    *,
    site_url: str,
    period_mode: str,
    anchor: date,
    current_start: date,
    current_end: date,
    url_segment: str = "all",
    refresh: bool = False,
) -> dict[str, Any]:
    """Top queries and pages for the overview GSC window + URL segment (Tier A cache per dimension)."""
    empty_cache = {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}
    anchor_iso = anchor.isoformat()
    window = {
        "start_date": current_start.isoformat(),
        "end_date": current_end.isoformat(),
    }
    seg_norm = (url_segment or "all").strip().lower()
    page_filter = _gsc_url_segment_page_contains(seg_norm)
    if page_filter is None and seg_norm != "all":
        seg_norm = "all"
        page_filter = None

    def _empty_slice() -> dict:
        return {"rows": [], "error": "", "_cache": empty_cache}

    queries = _empty_slice()
    pages = _empty_slice()
    top_errors: list[str] = []

    if not (site_url or "").strip():
        return {
            "available": False,
            "period_mode": period_mode,
            "anchor_date": anchor_iso,
            "window": window,
            "url_segment": seg_norm,
            "queries": queries,
            "pages": pages,
            "error": "No Search Console property selected",
        }

    for dimension, slice_ref, cache_type in (
        ("query", queries, "gsc_property_query"),
        ("page", pages, "gsc_property_page"),
    ):
        cache_key = _query_page_table_cache_key(site_url, period_mode, anchor_iso, dimension, seg_norm)
        if not refresh:
            cached_payload, meta = _load_cached_payload(conn, cache_key)
            if cached_payload and isinstance(cached_payload, dict):
                rows = cached_payload.get("rows")
                if isinstance(rows, list):
                    slice_ref["rows"] = rows
                slice_ref["error"] = (cached_payload.get("error") or "") or ""
                slice_ref["_cache"] = meta
        else:
            rows, err = _fetch_gsc_query_page_dimension(
                conn,
                site_url,
                current_start,
                current_end,
                dimension=dimension,
                page_contains=page_filter,
                row_limit=GSC_QUERY_PAGE_ROW_CAP,
            )
            payload_body = {
                "dimension": dimension,
                "period_mode": period_mode,
                "anchor_date": anchor_iso,
                "url_segment": seg_norm,
                "start_date": window["start_date"],
                "end_date": window["end_date"],
                "rows": rows,
                "error": err or "",
            }
            meta = _write_cache_payload(
                conn,
                cache_key=cache_key,
                cache_type=cache_type,
                payload=payload_body,
                ttl_seconds=CACHE_TTLS[cache_type],
            )
            slice_ref["rows"] = rows
            slice_ref["error"] = err or ""
            slice_ref["_cache"] = meta
            if err:
                top_errors.append(f"{dimension}: {err}")

    return {
        "available": True,
        "period_mode": period_mode,
        "anchor_date": anchor_iso,
        "window": window,
        "url_segment": seg_norm,
        "queries": queries,
        "pages": pages,
        "error": "; ".join(top_errors) if top_errors else "",
    }


def refresh_gsc_property_breakdowns_for_site(conn: sqlite3.Connection, site_url: str) -> None:
    """Re-fetch and store Tier A property breakdowns for MTD, full months, and fixed-start overview mode."""
    url = (site_url or "").strip()
    if not url:
        return
    from backend.app.services.gsc_overview_calendar import (
        gsc_anchor_date_local,
        last_two_full_month_windows,
        mtd_matched_windows,
        overview_site_report_windows,
        rolling_thirty_day_windows,
    )

    anchor = gsc_anchor_date_local()
    for pm in ("mtd", "full_months", "since_2026_02_15", "rolling_30d"):
        mode = _normalize_gsc_period_mode(pm)
        if mode == "full_months":
            w_cur, w_prev = last_two_full_month_windows(anchor)
        elif mode == "since_2026_02_15":
            w_cur, w_prev = overview_site_report_windows(anchor)
        elif mode == "rolling_30d":
            w_cur, w_prev = rolling_thirty_day_windows(anchor)
        else:
            w_cur, w_prev = mtd_matched_windows(anchor)
        get_gsc_property_breakdowns_cached(
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


# -- Search Console sites & summary -------------------------------------------

def get_search_console_sites(conn: sqlite3.Connection) -> list[dict]:
    access_token = get_google_access_token(conn)
    payload = google_api_get("https://www.googleapis.com/webmasters/v3/sites", access_token)
    return payload.get("siteEntry", [])


def preferred_site_url(conn: sqlite3.Connection, sites: list[dict]) -> str:
    selected = get_service_setting(conn, "search_console_site")
    if selected:
        return selected
    shop = (get_service_setting(conn, "shopify_shop") or os.getenv("SHOPIFY_SHOP", "") or "").strip()
    if shop:
        domain = shop.removesuffix(".myshopify.com") if ".myshopify.com" in shop else shop
        preferred = [f"sc-domain:{domain}", f"https://www.{domain}/", f"http://www.{domain}/", f"https://{domain}/"]
    else:
        preferred = []
    site_urls = {row["siteUrl"] for row in sites}
    for candidate in preferred:
        if candidate in site_urls:
            set_service_setting(conn, "search_console_site", candidate)
            return candidate
    if sites:
        set_service_setting(conn, "search_console_site", sites[0]["siteUrl"])
        return sites[0]["siteUrl"]
    return ""


def fetch_search_console_summary(conn: sqlite3.Connection) -> dict:
    sites = get_search_console_sites(conn)
    site_url = preferred_site_url(conn, sites)
    if not site_url:
        return {"sites": sites, "site_url": "", "pages": [], "queries": []}
    access_token = get_google_access_token(conn)
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=27)
    page_rows = google_api_post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
        access_token,
        {"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "dimensions": ["page"], "rowLimit": 20},
    ).get("rows", [])
    query_rows = google_api_post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
        access_token,
        {"startDate": start_date.isoformat(), "endDate": end_date.isoformat(), "dimensions": ["query"], "rowLimit": 20},
    ).get("rows", [])
    return {
        "sites": sites,
        "site_url": site_url,
        "pages": page_rows,
        "queries": query_rows,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }


def get_search_console_summary_cached(conn: sqlite3.Connection, refresh: bool = False) -> dict:
    gsc_cache = _pkg().GSC_CACHE
    sites = []
    site_url = get_service_setting(conn, "search_console_site")
    if refresh or not site_url:
        sites = get_search_console_sites(conn)
        site_url = preferred_site_url(conn, sites)
    if not site_url:
        return {"sites": sites, "site_url": "", "pages": [], "queries": [], "_cache": {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}}
    cache_key = _summary_cache_key(site_url)
    if not refresh and gsc_cache["summary"] is not None and gsc_cache["summary"].get("site_url") == site_url:
        return gsc_cache["summary"]
    cached_payload, meta = _load_cached_payload(conn, cache_key)
    if cached_payload and not refresh:
        payload = {**cached_payload, "sites": sites, "_cache": meta}
        gsc_cache["summary"] = payload
        return payload
    if not refresh:
        return {
            "sites": sites,
            "site_url": site_url,
            "pages": [],
            "queries": [],
            "start_date": "",
            "end_date": "",
            "_cache": meta,
        }
    summary = fetch_search_console_summary(conn)
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="search_console_summary",
        payload=summary,
        ttl_seconds=CACHE_TTLS["search_console_summary"],
    )
    payload = {**summary, "_cache": meta}
    gsc_cache["summary"] = payload
    return payload


# -- Per-URL GSC detail -------------------------------------------------------


def gsc_url_detail_cache_meta_for_sync(
    conn: sqlite3.Connection,
    url: str,
    *,
    site_url: str,
    gsc_period: str = GSC_CATALOG_PERIOD_MODE,
) -> dict:
    """Return ``_cache``-style meta matching ``get_search_console_url_detail(..., refresh=False)`` (memory then SQLite), without network."""
    if not (site_url or "").strip():
        return {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}
    gsc_cache = _pkg().GSC_CACHE
    period_mode = _normalize_gsc_period_mode(gsc_period)
    mem_key = _per_url_memory_cache_key(period_mode, url)
    if mem_key in gsc_cache.get("per_url", {}):
        payload = gsc_cache["per_url"][mem_key]
        meta = payload.get("_cache")
        if isinstance(meta, dict):
            return {**meta}
    cache_key = _url_detail_cache_key(site_url, url, period_mode)
    _payload, meta = _load_cached_payload(conn, cache_key)
    if isinstance(meta, dict):
        return {**meta}
    return {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}


def gsc_url_detail_needs_refresh(
    conn: sqlite3.Connection,
    url: str,
    *,
    site_url: str,
    gsc_period: str = GSC_CATALOG_PERIOD_MODE,
) -> bool:
    """True unless per-URL GSC cache exists and is not stale (same rule as bulk sync skip when not forcing)."""
    meta = gsc_url_detail_cache_meta_for_sync(conn, url, site_url=site_url, gsc_period=gsc_period)
    return not (bool(meta.get("exists")) and not bool(meta.get("stale")))


def get_search_console_url_detail(
    conn: sqlite3.Connection,
    url: str,
    *,
    refresh: bool = False,
    object_type: str = "",
    object_handle: str = "",
    site_url_override: str = "",
    access_token_override: str = "",
    gsc_period: str = GSC_CATALOG_PERIOD_MODE,
) -> dict:
    gsc_cache = _pkg().GSC_CACHE
    site_url = site_url_override or get_service_setting(conn, "search_console_site")
    if refresh and not site_url_override:
        sites = get_search_console_sites(conn)
        site_url = preferred_site_url(conn, sites)
    if not site_url:
        return {"url": url, "page_rows": [], "query_rows": [], "site_url": "", "_cache": {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}}
    period_mode = _normalize_gsc_period_mode(gsc_period)
    cache_key = _url_detail_cache_key(site_url, url, period_mode)
    mem_key = _per_url_memory_cache_key(period_mode, url)
    if not refresh and mem_key in gsc_cache["per_url"]:
        return gsc_cache["per_url"][mem_key]
    cached_payload, meta = _load_cached_payload(conn, cache_key)
    if cached_payload and not refresh:
        payload = {**cached_payload, "_cache": meta}
        gsc_cache["per_url"][mem_key] = payload
        return payload
    if not refresh:
        return {"url": url, "page_rows": [], "query_rows": [], "site_url": site_url, "_cache": meta}
    access_token = access_token_override or get_google_access_token(conn)
    start_date, end_date = gsc_url_report_window(period_mode)
    page_rows = google_api_post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
        access_token,
        {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["page"],
            "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": url}]}],
            "rowLimit": 5,
        },
    ).get("rows", [])
    query_rows = google_api_post(
        f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
        access_token,
        {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["query"],
            "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": url}]}],
            "rowLimit": GSC_PER_URL_QUERY_ROW_LIMIT,
        },
    ).get("rows", [])
    payload = {
        "url": url,
        "site_url": site_url,
        "page_rows": page_rows,
        "query_rows": query_rows,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "period_mode": period_mode,
    }
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="search_console_url",
        payload=payload,
        ttl_seconds=CACHE_TTLS["search_console_url"],
        object_type=object_type,
        object_handle=object_handle,
        url=url,
    )
    payload = {**payload, "_cache": meta}
    gsc_cache["per_url"][mem_key] = payload
    return payload


# -- Bulk per-URL GSC via property-wide dimension pulls ------------------------
#
# ``get_search_console_url_detail`` spends two filtered ``searchAnalytics/query``
# calls per URL. Both datasets are retrievable property-wide: ``["page"]`` gives
# every page's totals and ``["page","query"]`` gives every page's query
# breakdown. Paginating those to exhaustion costs a handful of calls for the
# whole catalog and returns a strict superset of the per-URL responses (which
# were capped at ``GSC_PER_URL_QUERY_ROW_LIMIT`` = 20 rows each).

GSC_BULK_ROW_LIMIT = 25000
# Query rows retained per page. Well above the 20 the per-URL path stored, so the
# downstream top-20 slice is always contained; keeps payloads bounded on big sites.
GSC_BULK_QUERY_ROWS_PER_PAGE = 100
# Safety valve so a pathological property cannot page forever.
GSC_BULK_MAX_REQUESTS = 200


def _iter_gsc_bulk_rows(
    site_url: str,
    access_token: str,
    start: date,
    end: date,
    dimensions: list[str],
    *,
    cancel_check: Callable[[], None] | None = None,
    row_limit: int = GSC_BULK_ROW_LIMIT,
):
    """Yield ``searchAnalytics/query`` row batches, paging with ``startRow`` until exhausted."""
    endpoint = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query"
    start_row = 0
    for _ in range(GSC_BULK_MAX_REQUESTS):
        if cancel_check is not None:
            cancel_check()
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": start_row,
        }
        rows = google_api_post(endpoint, access_token, body).get("rows") or []
        if not rows:
            return
        yield rows
        if len(rows) < row_limit:
            return
        start_row += len(rows)
    logger.warning(
        "GSC bulk pull for dimensions=%s hit the %s-request cap; results may be truncated",
        dimensions,
        GSC_BULK_MAX_REQUESTS,
    )


def fetch_gsc_all_page_rows(
    site_url: str,
    access_token: str,
    start: date,
    end: date,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, dict]:
    """Map page URL → its ``dimensions=["page"]`` row (same shape the per-URL call returned)."""
    out: dict[str, dict] = {}
    for batch in _iter_gsc_bulk_rows(
        site_url, access_token, start, end, ["page"], cancel_check=cancel_check
    ):
        for row in batch:
            keys = row.get("keys") or []
            if not keys:
                continue
            out[str(keys[0])] = row
    return out


def _gsc_query_row_sort_key(row: dict) -> tuple[float, float]:
    return (-float(row.get("clicks") or 0), -float(row.get("impressions") or 0))


def fetch_gsc_all_page_query_rows(
    site_url: str,
    access_token: str,
    start: date,
    end: date,
    *,
    cancel_check: Callable[[], None] | None = None,
    rows_per_page: int = GSC_BULK_QUERY_ROWS_PER_PAGE,
) -> dict[str, list[dict]]:
    """Map page URL → its top query rows, reshaped to match ``dimensions=["query"]`` output.

    Rows are trimmed to ``rows_per_page`` per page as batches arrive, so memory stays
    proportional to pages × ``rows_per_page`` rather than to the property's total
    page × query cardinality.
    """
    grouped: dict[str, list[dict]] = {}
    for batch in _iter_gsc_bulk_rows(
        site_url, access_token, start, end, ["page", "query"], cancel_check=cancel_check
    ):
        touched: set[str] = set()
        for row in batch:
            keys = row.get("keys") or []
            if len(keys) < 2:
                continue
            page = str(keys[0])
            # Reshape to the single-dimension ["query"] form callers already parse.
            grouped.setdefault(page, []).append(
                {
                    "keys": [str(keys[1])],
                    "clicks": row.get("clicks", 0),
                    "impressions": row.get("impressions", 0),
                    "ctr": row.get("ctr", 0),
                    "position": row.get("position", 0),
                }
            )
            touched.add(page)
        for page in touched:
            rows = grouped[page]
            if len(rows) > rows_per_page:
                rows.sort(key=_gsc_query_row_sort_key)
                del rows[rows_per_page:]
    for rows in grouped.values():
        rows.sort(key=_gsc_query_row_sort_key)
    return grouped


def fetch_gsc_page_daily_rows(
    site_url: str,
    access_token: str,
    start: date,
    end: date,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> list[dict[str, Any]]:
    """Daily per-page rows via ``dimensions=["date","page"]``, paged to exhaustion.

    Same cost profile as the other bulk pulls — a handful of requests for the whole
    property — but returns history instead of a single aggregate per page.
    """
    out: list[dict[str, Any]] = []
    for batch in _iter_gsc_bulk_rows(
        site_url, access_token, start, end, ["date", "page"], cancel_check=cancel_check
    ):
        for row in batch:
            keys = row.get("keys") or []
            if len(keys) < 2:
                continue
            out.append(
                {
                    "date": str(keys[0]),
                    "page_url": str(keys[1]),
                    "clicks": int(row.get("clicks") or 0),
                    "impressions": int(row.get("impressions") or 0),
                    "ctr": float(row.get("ctr") or 0.0),
                    "position": float(row.get("position") or 0.0),
                }
            )
    return out


def build_gsc_url_detail(
    url: str,
    site_url: str,
    *,
    page_row: dict | None,
    query_rows: list[dict] | None,
    start: date,
    end: date,
    period_mode: str,
) -> dict:
    """Assemble the payload ``get_search_console_url_detail(refresh=True)`` would have produced."""
    return {
        "url": url,
        "site_url": site_url,
        "page_rows": [page_row] if page_row else [],
        "query_rows": list(query_rows or []),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "period_mode": period_mode,
    }


def write_gsc_url_detail_cache(
    conn: sqlite3.Connection,
    payload: dict,
    *,
    site_url: str,
    period_mode: str = GSC_CATALOG_PERIOD_MODE,
    object_type: str = "",
    object_handle: str = "",
) -> dict:
    """Persist a derived per-URL payload under the key ``get_search_console_url_detail`` uses."""
    url = payload.get("url") or ""
    cache_key = _url_detail_cache_key(site_url, url, period_mode)
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="search_console_url",
        payload=payload,
        ttl_seconds=CACHE_TTLS["search_console_url"],
        object_type=object_type,
        object_handle=object_handle,
        url=url,
    )
    out = {**payload, "_cache": meta}
    _pkg().GSC_CACHE["per_url"][_per_url_memory_cache_key(period_mode, url)] = out
    return out


# -- Per-URL query × second dimension -----------------------------------------

# Tier B: same date window as get_search_console_url_detail.
GSC_URL_QUERY_SECOND_DIMS = frozenset({"country", "device", "searchAppearance"})
GSC_URL_QUERY_SECOND_DIMENSION_ROW_LIMIT = 250


def fetch_gsc_url_query_second_dimension(
    conn: sqlite3.Connection,
    site_url: str,
    page_url: str,
    start: date,
    end: date,
    *,
    second_dimension: str,
    row_limit: int = GSC_URL_QUERY_SECOND_DIMENSION_ROW_LIMIT,
) -> tuple[list[dict[str, Any]], str | None]:
    """searchAnalytics/query with dimensions [query, second_dimension] and page equals filter."""
    if second_dimension not in GSC_URL_QUERY_SECOND_DIMS:
        return [], f"unsupported second dimension: {second_dimension}"
    access_token = get_google_access_token(conn)
    body: dict[str, Any] = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["query", second_dimension],
        "dimensionFilterGroups": [{"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}],
        "rowLimit": row_limit,
        "orderBys": [{"metric": "IMPRESSIONS", "direction": "DESCENDING"}],
    }
    try:
        resp = google_api_post(
            f"https://searchconsole.googleapis.com/webmasters/v3/sites/{quote(site_url, safe='')}/searchAnalytics/query",
            access_token,
            body,
        )
    except HttpRequestError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], str(exc)
    out: list[dict[str, Any]] = []
    for row in resp.get("rows") or []:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        out.append({
            "query": keys[0],
            "segment": keys[1],
            "clicks": int(row.get("clicks") or 0),
            "impressions": int(row.get("impressions") or 0),
            "ctr": float(row.get("ctr") or 0),
            "position": float(row.get("position") or 0),
        })
    return out, None


# -- URL inspection -----------------------------------------------------------

def _url_inspect_lang_code(conn: sqlite3.Connection) -> str:
    try:
        from shopifyseo.market_context import get_primary_country_code, language_region_code
        return language_region_code(get_primary_country_code(conn))
    except Exception:
        return "en-US"


def get_url_inspection(
    conn: sqlite3.Connection,
    url: str,
    *,
    refresh: bool = False,
    object_type: str = "",
    object_handle: str = "",
    site_url_override: str = "",
    access_token_override: str = "",
) -> dict:
    gsc_cache = _pkg().GSC_CACHE
    site_url = site_url_override or get_service_setting(conn, "search_console_site")
    if refresh and not site_url_override:
        sites = get_search_console_sites(conn)
        site_url = preferred_site_url(conn, sites)
    if not site_url:
        return {"url": url, "site_url": "", "inspectionResult": {}, "_cache": {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}}
    cache_key = _inspection_cache_key(site_url, url)
    if not refresh and url in gsc_cache["inspection"]:
        return gsc_cache["inspection"][url]
    cached_payload, meta = _load_cached_payload(conn, cache_key)
    if cached_payload and not refresh:
        payload = {**cached_payload, "_cache": meta}
        gsc_cache["inspection"][url] = payload
        return payload
    if not refresh:
        return {"url": url, "site_url": site_url, "inspectionResult": {}, "_cache": meta}
    access_token = access_token_override or get_google_access_token(conn)
    payload = google_api_post(
        "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect",
        access_token,
        {"inspectionUrl": url, "siteUrl": site_url, "languageCode": _url_inspect_lang_code(conn)},
    )
    meta = _write_cache_payload(
        conn,
        cache_key=cache_key,
        cache_type="url_inspection",
        payload=payload,
        ttl_seconds=CACHE_TTLS["url_inspection"],
        object_type=object_type,
        object_handle=object_handle,
        url=url,
    )
    conn.commit()
    payload = {**payload, "_cache": meta}
    gsc_cache["inspection"][url] = payload
    return payload


# -- PageSpeed Insights -------------------------------------------------------

def _pagespeed_transient_http_error(exc: HttpRequestError) -> bool:
    """True if retrying the PageSpeed Insights request may succeed (server or network blip)."""
    status = exc.status
    if status is None:
        return True
    return status in (408, 500, 502, 503, 504)


def _pagespeed_retry_after_seconds(exc: HttpRequestError, default_seconds: int = 15 * 60) -> int:
    """Best-effort cooldown from Retry-After; falls back to a short safety pause."""
    raw = ""
    if exc.headers:
        raw = str(exc.headers.get("Retry-After") or exc.headers.get("retry-after") or "").strip()
    if not raw:
        return default_seconds
    try:
        return max(int(float(raw)), 1)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        delay = int((retry_at - datetime.now(timezone.utc)).total_seconds())
        return max(delay, 1)
    except Exception:
        return default_seconds


def _pagespeed_retry_after_header_float(exc: HttpRequestError) -> float:
    """Numeric Retry-After in seconds when present; otherwise 0."""
    if not exc.headers:
        return 0.0
    raw = exc.headers.get("Retry-After") or exc.headers.get("retry-after")
    if raw in (None, ""):
        return 0.0
    try:
        return max(float(raw), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _sleep_interruptible(seconds: float, cancel_check: Callable[[], None] | None) -> None:
    """Sleep up to ``seconds`` wall time, calling ``cancel_check`` periodically."""
    if seconds <= 0:
        if cancel_check is not None:
            cancel_check()
        return
    end = time.monotonic() + seconds
    while True:
        if cancel_check is not None:
            cancel_check()
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.25))


def _hybrid_429_post_slowdown_wait_seconds(
    exc: HttpRequestError, adaptive_wait_seconds: Callable[[], float] | None
) -> float:
    """Wait after adaptive slowdown: at least 5s, cap 120s, max(header Retry-After, limiter wait)."""
    hdr = _pagespeed_retry_after_header_float(exc)
    adaptive = adaptive_wait_seconds() if adaptive_wait_seconds is not None else 0.0
    return min(120.0, max(5.0, max(hdr, adaptive)))


# Audit fields worth keeping. Everything a Lighthouse audit reports as a *number* stays;
# what gets dropped is presentation data this app has no UI for — the `details` tables,
# the base64 full-page screenshot, i18n strings, timing traces and stack packs. A full
# PSI response is ~350 KB, almost all of it that presentation payload, and it was being
# stored verbatim for every URL and strategy.
_PAGESPEED_AUDIT_KEEP = ("id", "title", "score", "scoreDisplayMode", "numericValue", "numericUnit", "displayValue")
_PAGESPEED_LIGHTHOUSE_KEEP = (
    "requestedUrl",
    "finalUrl",
    "mainDocumentUrl",
    "fetchTime",
    "lighthouseVersion",
    "userAgent",
    "configSettings",
    "categories",
    "categoryGroups",
    "runWarnings",
    "runtimeError",
)


def trim_pagespeed_payload(payload: dict) -> dict:
    """Keep every PageSpeed number, drop the presentation payload.

    Preserves ``lighthouseResult.categories`` (what the catalog columns read), every
    audit's score/numericValue/displayValue, and the CrUX field data. Safe on partial or
    unexpected responses: anything it does not recognise is passed through untouched.
    """
    if not isinstance(payload, dict):
        return payload

    lh = payload.get("lighthouseResult")
    if not isinstance(lh, dict):
        # Nothing to trim (e.g. the rate-limited placeholder); hand it back untouched.
        return payload

    out = {k: v for k, v in payload.items() if k != "lighthouseResult"}

    slim_lh = {k: lh[k] for k in _PAGESPEED_LIGHTHOUSE_KEEP if k in lh}
    audits = lh.get("audits")
    if isinstance(audits, dict):
        slim_audits: dict = {}
        for audit_id, audit in audits.items():
            if not isinstance(audit, dict):
                continue
            # fullPageScreenshot is the single largest field in a PSI response.
            if audit_id in ("full-page-screenshot", "screenshot-thumbnails", "final-screenshot"):
                continue
            slim_audits[audit_id] = {k: audit[k] for k in _PAGESPEED_AUDIT_KEEP if k in audit}
        slim_lh["audits"] = slim_audits

    out["lighthouseResult"] = slim_lh
    return out


def _finalize_pagespeed_rate_limited(
    conn: sqlite3.Connection,
    *,
    db_cache_key: str,
    cache_key: str,
    url: str,
    strategy: str,
    object_type: str,
    object_handle: str,
    exc: HttpRequestError,
    hybrid_429_final: bool = False,
) -> dict:
    """Persist rate-limited placeholder + return merged payload (non-hybrid / final hybrid pass)."""
    gsc_cache = _pkg().GSC_CACHE
    cooldown_seconds = _pagespeed_retry_after_seconds(exc)
    retry_after_at = int(time.time()) + cooldown_seconds
    stale_payload, stale_meta = _load_cached_payload(conn, db_cache_key)
    error_payload = {
        "error": "pagespeed_rate_limited",
        "status": 429,
        "message": "PageSpeed Insights rate limit hit. Use cached data or retry later.",
    }
    payload = stale_payload or {}
    payload = {
        **payload,
        "_error": error_payload,
        "_meta": {
            "rate_limited": True,
            "retry_after_at": retry_after_at,
        },
    }
    meta = {
        **stale_meta,
        "exists": True,
        "stale": True,
        "fetched_at": stale_meta.get("fetched_at"),
        "expires_at": retry_after_at,
        "rate_limited": True,
        "status": 429,
        "retry_after_at": retry_after_at,
        "hybrid_429_final": hybrid_429_final,
    }
    _write_cache_payload(
        conn,
        cache_key=db_cache_key,
        cache_type="pagespeed",
        payload=payload,
        ttl_seconds=cooldown_seconds,
        object_type=object_type,
        object_handle=object_handle,
        url=url,
        strategy=strategy,
    )
    merged = {**payload, "_cache": meta, "_error": error_payload}
    gsc_cache["pagespeed"][cache_key] = merged
    return merged


def _fetch_run_pagespeed_with_retries(
    api_url: str,
    access_token: str,
    *,
    before_each_http: Callable[[], None] | None = None,
) -> dict:
    """Call runPagespeed with backoff — PSI often returns 5xx under load or for slow URLs.

    ``before_each_http`` runs immediately before every HTTP attempt (including the first),
    so callers can rate-limit **total** API calls including retries.
    """
    max_attempts = 3
    last_exc: HttpRequestError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            if before_each_http is not None:
                before_each_http()
            return google_api_get(api_url, access_token, timeout=120)
        except HttpRequestError as exc:
            last_exc = exc
            if not _pagespeed_transient_http_error(exc) or attempt >= max_attempts:
                raise
            # Exponential backoff + jitter; respect Retry-After when present
            retry_after = 0.0
            if exc.headers:
                raw = exc.headers.get("Retry-After") or exc.headers.get("retry-after")
                if raw:
                    try:
                        retry_after = float(raw)
                    except ValueError:
                        retry_after = 0.0
            base = min(60.0, (2 ** (attempt - 1)) * 0.75 + random.uniform(0, 0.75))
            delay = max(base, min(120.0, retry_after))
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def get_pagespeed(
    conn: sqlite3.Connection,
    url: str,
    strategy: str = "mobile",
    *,
    refresh: bool = False,
    object_type: str = "",
    object_handle: str = "",
    before_each_run_pagespeed_http: Callable[[], None] | None = None,
    hybrid_pagespeed_429_retry: bool = False,
    pagespeed_429_requeue_pass: int = 0,
    on_hybrid_429_slowdown: Callable[[HttpRequestError], None] | None = None,
    hybrid_429_adaptive_wait_seconds: Callable[[], float] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    gsc_cache = _pkg().GSC_CACHE
    cache_key = f"{strategy}:{url}"
    db_cache_key = _pagespeed_cache_key(url, strategy)
    if not refresh and cache_key in gsc_cache["pagespeed"]:
        return gsc_cache["pagespeed"][cache_key]
    cached_payload, meta = _load_cached_payload(conn, db_cache_key)
    if cached_payload and not refresh:
        payload = {**cached_payload, "_cache": meta}
        gsc_cache["pagespeed"][cache_key] = payload
        return payload
    if not refresh:
        return {"_cache": meta}
    if not google_token_has_scope(conn, "openid"):
        raise RuntimeError("Reconnect Google so the token includes the openid scope for PageSpeed.")
    access_token = get_google_access_token(conn)
    api_url = (
        "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?"
        + urlencode({"url": url, "strategy": strategy, "category": ["PERFORMANCE"]}, doseq=True)
    )

    def _before_each_run_pagespeed_http_effective() -> None:
        if before_each_run_pagespeed_http is not None:
            before_each_run_pagespeed_http()
        else:
            _default_acquire_pagespeed_http_rate_slot(None)

    try:
        payload = _fetch_run_pagespeed_with_retries(
            api_url,
            access_token,
            before_each_http=_before_each_run_pagespeed_http_effective,
        )
    except HttpRequestError as exc:
        if exc.status == 429:
            if not hybrid_pagespeed_429_retry or on_hybrid_429_slowdown is None:
                return _finalize_pagespeed_rate_limited(
                    conn,
                    db_cache_key=db_cache_key,
                    cache_key=cache_key,
                    url=url,
                    strategy=strategy,
                    object_type=object_type,
                    object_handle=object_handle,
                    exc=exc,
                )
            # Bulk hybrid: slowdown first, one inline retry; then re-queue once or finalize.
            on_hybrid_429_slowdown(exc)
            wait_s = _hybrid_429_post_slowdown_wait_seconds(exc, hybrid_429_adaptive_wait_seconds)
            _sleep_interruptible(wait_s, cancel_check)
            try:
                _before_each_run_pagespeed_http_effective()
                payload = google_api_get(api_url, access_token, timeout=120)
            except HttpRequestError as exc2:
                if exc2.status == 429:
                    if pagespeed_429_requeue_pass == 0:
                        return {"_cache": {"requeue_429": True, "exists": False, "stale": True}}
                    return _finalize_pagespeed_rate_limited(
                        conn,
                        db_cache_key=db_cache_key,
                        cache_key=cache_key,
                        url=url,
                        strategy=strategy,
                        object_type=object_type,
                        object_handle=object_handle,
                        exc=exc2,
                        hybrid_429_final=True,
                    )
                raise
        else:
            raise
    # Store the numbers, not the screenshots. Also keeps the in-process cache small —
    # this dict is unbounded and was holding a full Lighthouse report per URL.
    payload = trim_pagespeed_payload(payload)
    meta = _write_cache_payload(
        conn,
        cache_key=db_cache_key,
        cache_type="pagespeed",
        payload=payload,
        ttl_seconds=CACHE_TTLS["pagespeed"],
        object_type=object_type,
        object_handle=object_handle,
        url=url,
        strategy=strategy,
    )
    payload = {**payload, "_cache": meta}
    gsc_cache["pagespeed"][cache_key] = payload
    return payload
