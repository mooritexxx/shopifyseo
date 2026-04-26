"""Keyword Planner historical metrics (Google Ads API) for target keywords.

Does not set ``parent_topic`` (that string comes from DataForSEO ``core_keyword``
on keyword overview / research rows; see ``dataforseo_client``).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable

import sqlite3

from backend.app.services.google_ads_lab_service import invoke_keyword_planning_rpc
from shopifyseo.dashboard_google import set_service_setting
from shopifyseo.market_context import get_primary_country_code

from .keyword_db import TARGET_KEY, load_target_keywords, sync_keyword_metrics_to_db

logger = logging.getLogger(__name__)

# KeywordPlanIdeaService — English (matches most merchant dashboards).
_PLANNER_LANGUAGE = "languageConstants/1000"

# GeoTargetConstant criterion IDs (country) — see Google Ads geo target reference.
_GEO_CRITERION_ID_BY_COUNTRY: dict[str, str] = {
    "US": "2840",
    "CA": "2124",
    "GB": "2826",
    "AU": "2036",
    "NZ": "2548",
    "IE": "2228",
    "ZA": "2710",
    "IN": "2356",
    "SG": "2702",
    "AE": "2790",
    "DE": "2276",
    "FR": "2250",
    "IT": "2384",
    "ES": "2724",
    "NL": "2652",
    "SE": "2756",
    "NO": "2784",
    "DK": "2084",
    "FI": "2242",
    "JP": "2392",
    "BR": "2076",
    "MX": "2484",
}

# Per request, ``keywords[]`` may include up to 10,000 entries (Google Ads API docs). We use 2000
# per call to limit payload size and ease throttling; long lists run as multiple sequential calls.
_PLANNER_BATCH_SIZE = 2000
_PLANNER_QPS_DELAY_SEC = 1.15


def _geo_target_constants(conn: sqlite3.Connection) -> list[str]:
    iso = (get_primary_country_code(conn) or "CA").strip().upper()
    crit = _GEO_CRITERION_ID_BY_COUNTRY.get(iso, "2124")
    return [f"geoTargetConstants/{crit}"]


def _as_int_opt(val: Any) -> int | None:
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip().replace(",", "")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _parse_planner_row(row: dict[str, Any]) -> tuple[str, int | None, str | None, int | None]:
    text = (row.get("text") or "").strip()
    metrics = row.get("keywordMetrics")
    if not isinstance(metrics, dict):
        return text, None, None, None
    avg = _as_int_opt(metrics.get("avgMonthlySearches"))
    comp = metrics.get("competition")
    comp_s = (str(comp).strip() if comp is not None else "") or None
    idx = _as_int_opt(metrics.get("competitionIndex"))
    return text, avg, comp_s, idx


def refresh_google_ads_planner_metrics(
    conn: sqlite3.Connection,
    keywords: list[str],
    *,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Call ``GenerateKeywordHistoricalMetrics`` in batches; update target JSON + ``keyword_metrics``.

    Each API request sends up to ``_PLANNER_BATCH_SIZE`` keywords (2000), under the documented
    10,000-keyword cap. Calls are throttled with ``_PLANNER_QPS_DELAY_SEC`` between requests.

    Uses primary-market country for geo and English for language (Keyword Planner).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in keywords:
        k = (raw or "").strip()
        if not k:
            continue
        low = k.lower()
        if low in seen:
            continue
        seen.add(low)
        ordered.append(k)

    if not ordered:
        raise RuntimeError("No keywords to look up.")

    data = load_target_keywords(conn)
    items = data.get("items") or []
    allowed = {str(i.get("keyword", "")).strip().lower() for i in items if isinstance(i, dict) and i.get("keyword")}
    unknown = [k for k in ordered if k.lower() not in allowed]
    if unknown:
        raise RuntimeError(
            f"{len(unknown)} keyword(s) are not in the current target list (e.g. {unknown[0]!r})."
        )

    geo_targets = _geo_target_constants(conn)
    body_base: dict[str, Any] = {
        "language": _PLANNER_LANGUAGE,
        "geoTargetConstants": geo_targets,
        "keywordPlanNetwork": "GOOGLE_SEARCH",
    }

    metrics_by_lower: dict[str, tuple[int | None, str | None, int | None]] = {}
    errors: list[str] = []
    total_batches = (len(ordered) + _PLANNER_BATCH_SIZE - 1) // _PLANNER_BATCH_SIZE

    for bi in range(total_batches):
        start = bi * _PLANNER_BATCH_SIZE
        chunk = ordered[start : start + _PLANNER_BATCH_SIZE]
        if on_progress:
            on_progress(
                f"Google Ads Keyword Planner (batch {bi + 1}/{total_batches}): {len(chunk)} keywords…"
            )
        body = {**body_base, "keywords": chunk}
        try:
            out = invoke_keyword_planning_rpc(
                rpc_method="generateKeywordHistoricalMetrics",
                body=body,
            )
        except Exception as exc:
            msg = str(exc)
            logger.warning("GenerateKeywordHistoricalMetrics batch %d failed: %s", bi + 1, msg)
            errors.append(f"Batch {bi + 1}: {msg}")
            continue

        results = (out.get("result") or {}).get("results") or []
        if not isinstance(results, list):
            errors.append(f"Batch {bi + 1}: unexpected response shape")
            continue

        for row in results:
            if not isinstance(row, dict):
                continue
            text, avg, comp, idx = _parse_planner_row(row)
            if not text:
                continue
            metrics_by_lower[text.lower()] = (avg, comp, idx)

        if bi + 1 < total_batches:
            time.sleep(_PLANNER_QPS_DELAY_SEC)

    updated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        kw = str(item.get("keyword", "")).strip()
        if not kw:
            continue
        tup = metrics_by_lower.get(kw.lower())
        if tup is None:
            continue
        avg, comp, idx = tup
        item["ads_avg_monthly_searches"] = avg
        item["ads_competition"] = comp
        item["ads_competition_index"] = idx
        updated += 1

    data["items"] = items
    data["total"] = len(items)
    data["ads_planner_refreshed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if errors:
        data["ads_planner_refresh_errors"] = errors
    else:
        data.pop("ads_planner_refresh_errors", None)

    set_service_setting(conn, TARGET_KEY, json.dumps(data, default=str))
    conn.commit()

    try:
        sync_keyword_metrics_to_db(conn)
    except Exception:
        logger.exception("sync_keyword_metrics_to_db after Ads planner refresh failed")

    return {
        "updated": updated,
        "requested": len(ordered),
        "planner_batches": total_batches,
        "planner_parts": total_batches,
        "matched_metrics": len(metrics_by_lower),
        "errors": errors,
        "items": items,
        "total": len(items),
        "last_run": data.get("last_run"),
        "unit_cost": data.get("unit_cost"),
    }
