"""DataForSEO API v3 client — Labs + SERP for keyword and competitor research.

``parent_topic`` in our target-keyword / ``keyword_metrics`` objects is set from
DataForSEO ``keyword_properties.core_keyword`` (broad "core" topic for the phrase).
The DB column name predates the DataForSEO integration; it is not from Ahrefs.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from shopifyseo.dashboard_http import HttpRequestError, request_json

logger = logging.getLogger(__name__)

DATAFORSEO_BASE = "https://api.dataforseo.com/v3"

# Phase A — DataForSEO Labs “all other endpoints” bill per task + per item; tuned caps
# reduce per-item spend while keeping enough rows for scoring / gaps (see pricing docs).
DFS_LABS_KEYWORD_EXPANSION_LIMIT = 50  # related_keywords, keyword_suggestions, keyword_ideas
DFS_LABS_RANKED_KEYWORDS_LIMIT = 100
DFS_LABS_RELEVANT_PAGES_DEFAULT = 50

# competitors_domain: only request as many rows as we surface (avoid paying for unused items).
DFS_COMPETITORS_DOMAIN_DEFAULT_LIMIT = 10

# serp_competitors (seed-driven discovery): wider fetch pool, then bulk-traffic sort → slice to this many rows.
DFS_SERP_COMPETITORS_FETCH_LIMIT = 120
DFS_SERP_COMPETITORS_DISCOVERY_LIMIT = 50

# Labs: (location_name, language_name) per primary market country — human-readable; payloads use codes below.
_LABS_LOCALE: dict[str, tuple[str, str]] = {
    "CA": ("Canada", "English"),
    "US": ("United States", "English"),
    "GB": ("United Kingdom", "English"),
    "AU": ("Australia", "English"),
    "NZ": ("New Zealand", "English"),
    "IE": ("Ireland", "English"),
    "ZA": ("South Africa", "English"),
    "IN": ("India", "English"),
    "SG": ("Singapore", "English"),
    "AE": ("United Arab Emirates", "English"),
    "DE": ("Germany", "German"),
    "FR": ("France", "French"),
    "IT": ("Italy", "Italian"),
    "ES": ("Spain", "Spanish"),
    "NL": ("Netherlands", "Dutch"),
    "SE": ("Sweden", "Swedish"),
    "NO": ("Norway", "Norwegian"),
    "DK": ("Denmark", "Danish"),
    "FI": ("Finland", "Finnish"),
    "JP": ("Japan", "Japanese"),
    "BR": ("Brazil", "Portuguese"),
    "MX": ("Mexico", "Spanish"),
}

# Labs: (location_code, language_code) per ISO country — from DataForSEO Labs locations/languages CSV
# https://cdn.dataforseo.com/v3/locations/locations_and_languages_dataforseo_labs_2025_08_05.csv
# (NO uses language_name "Norwegian (Bokmål)" → language_code nb in that file.)
_LABS_LOCATION_LANGUAGE_CODES: dict[str, tuple[int, str]] = {
    "CA": (2124, "en"),
    "US": (2840, "en"),
    "GB": (2826, "en"),
    "AU": (2036, "en"),
    "NZ": (2554, "en"),
    "IE": (2372, "en"),
    "ZA": (2710, "en"),
    "IN": (2356, "en"),
    "SG": (2702, "en"),
    "AE": (2784, "en"),
    "DE": (2276, "de"),
    "FR": (2250, "fr"),
    "IT": (2380, "it"),
    "ES": (2724, "es"),
    "NL": (2528, "nl"),
    "SE": (2752, "sv"),
    "NO": (2578, "nb"),
    "DK": (2208, "da"),
    "FI": (2246, "fi"),
    "JP": (2392, "ja"),
    "BR": (2076, "pt"),
    "MX": (2484, "es"),
}
_DEFAULT_LABS_LOCATION_LANGUAGE: tuple[int, str] = (2124, "en")

def labs_locale_for_country(iso_country: str) -> tuple[str, str]:
    """Return (location_name, language_name) for display or legacy callers."""
    return _LABS_LOCALE.get(iso_country.upper(), ("Canada", "English"))


def labs_location_language_codes(iso_country: str) -> tuple[int, str]:
    """Return (location_code, language_code) for DataForSEO Labs (and SERP) POST tasks."""
    key = (iso_country or "CA").strip().upper()
    if len(key) != 2:
        key = "CA"
    return _LABS_LOCATION_LANGUAGE_CODES.get(key, _DEFAULT_LABS_LOCATION_LANGUAGE)


def _labs_geo_task_fields(iso_country: str) -> dict[str, int | str]:
    loc_code, lang_code = labs_location_language_codes(iso_country)
    return {"location_code": loc_code, "language_code": lang_code}


def _auth_header(login: str, password: str) -> str:
    raw = f"{login.strip()}:{password.strip()}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _norm_domain(raw: str) -> str:
    s = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    if s.startswith("www."):
        s = s[4:]
    return s.split("/")[0].strip()


def _response_cost(resp: dict) -> float:
    c = resp.get("cost")
    if c is not None:
        try:
            return float(c)
        except (TypeError, ValueError):
            pass
    total = 0.0
    for t in resp.get("tasks") or []:
        tc = t.get("cost")
        if tc is not None:
            try:
                total += float(tc)
            except (TypeError, ValueError):
                pass
    return total


def _dfs_post(login: str, password: str, path: str, payload: list[dict], *, timeout: int = 120) -> dict:
    url = f"{DATAFORSEO_BASE}{path}"
    try:
        resp = request_json(
            url,
            method="POST",
            headers={
                "Authorization": _auth_header(login, password),
            },
            payload=payload,
            timeout=timeout,
        )
    except HttpRequestError as exc:
        body = (exc.body or "")[:500]
        _dfs_http_error(exc.status, body)
    if not isinstance(resp, dict):
        raise RuntimeError("DataForSEO returned a non-object response.")
    sc = resp.get("status_code")
    if sc not in (20000, None):
        msg = resp.get("status_message", "Unknown error")
        raise RuntimeError(f"DataForSEO API: {msg} (code {sc})")
    try:
        from shopifyseo.api_usage import log_api_usage

        log_api_usage(
            provider="dataforseo",
            model=path,
            call_type="seo_api",
            stage="",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_override_usd=_response_cost(resp),
        )
    except Exception:
        logger.debug("Failed to log DataForSEO API usage", exc_info=True)
    return resp


def _dfs_http_error(status: int | None, body: str) -> None:
    if status == 401:
        raise RuntimeError(
            "DataForSEO: authentication failed (401). Check API login and password in Settings > Integrations."
        )
    if status == 403:
        raise RuntimeError(f"DataForSEO: forbidden (403). {body}")
    if status == 429:
        raise RuntimeError("DataForSEO: rate limit exceeded (429). Wait and try again.")
    raise RuntimeError(f"DataForSEO HTTP error ({status or '?'}): {body}") from None


def _check_tasks(resp: dict) -> None:
    for i, t in enumerate(resp.get("tasks") or []):
        tsc = t.get("status_code")
        if tsc is not None and tsc != 20000:
            msg = t.get("status_message", "task failed")
            raise RuntimeError(f"DataForSEO task {i}: {msg} (code {tsc})")


def _serp_item_types_to_feature_counts(types_val: Any) -> dict[str, int] | None:
    if not isinstance(types_val, list):
        return None
    return {str(t): 1 for t in types_val if t}


def _keyword_data_block_to_explorer_row(
    kw_data: dict,
    *,
    source_endpoint: str = "keywords_explorer",
    traffic_potential_override: int | None = None,
) -> dict:
    ki = kw_data.get("keyword_info") or {}
    kp = kw_data.get("keyword_properties") or {}
    sii = kw_data.get("search_intent_info") or {}
    main = (sii.get("main_intent") or "").lower()
    intents: dict[str, bool] = {
        "informational": main == "informational",
        "commercial": main == "commercial",
        "transactional": main == "transactional",
        "navigational": main == "navigational",
        "branded": False,
        "local": False,
    }
    for f in sii.get("foreign_intent") or []:
        fl = str(f).lower()
        if fl in intents:
            intents[fl] = True
    serp = kw_data.get("serp_info") or {}
    serp_features = _serp_item_types_to_feature_counts(serp.get("serp_item_types"))
    vol = int(ki.get("search_volume") or 0)
    kd_raw = kp.get("keyword_difficulty")
    difficulty = int(kd_raw) if kd_raw is not None else None
    tp = traffic_potential_override if traffic_potential_override is not None else vol
    return {
        "keyword": (kw_data.get("keyword") or "").strip(),
        "volume": vol,
        "difficulty": difficulty,
        "traffic_potential": tp,
        "intents": intents,
        # keyword_metrics / JSON key `parent_topic` — DataForSEO `keyword_properties.core_keyword`
        "parent_topic": kp.get("core_keyword"),
        "cpc": ki.get("cpc"),
        "global_volume": None,
        "cps": None,
        "serp_features": serp_features,
        "first_seen": None,
        "serp_last_update": serp.get("last_updated_time"),
        "source_endpoint": source_endpoint,
    }


def validate_dataforseo_access(login: str, password: str, *, country_iso: str = "CA") -> str | None:
    """Cheap Labs call. Returns None if OK, else error string."""
    if not login.strip() or not password.strip():
        return "DataForSEO API login and password are required. Add them in Settings > Integrations."
    iso = (country_iso or "CA").strip().upper()
    if len(iso) != 2:
        iso = "CA"
    payload = [
        {
            **_labs_geo_task_fields(iso),
            "keyword": "vape",
            "limit": 1,
            # keyword_suggestions/live expects top-level metric paths, not keyword_data.* (see API docs).
            "filters": [["keyword_info.search_volume", ">", 0]],
        }
    ]
    try:
        resp = _dfs_post(login, password, "/dataforseo_labs/google/keyword_suggestions/live", payload, timeout=30)
        _check_tasks(resp)
        return None
    except RuntimeError as exc:
        return str(exc)
    except Exception as exc:
        return f"DataForSEO connection error: {exc}"


def _volume_kd_filters_nested(min_volume: int, max_kd: int) -> list:
    """For related_keywords and ranked_keywords (Labs docs use keyword_data.* in filters)."""
    return [
        ["keyword_data.keyword_info.search_volume", ">=", min_volume],
        "and",
        ["keyword_data.keyword_properties.keyword_difficulty", "<=", max_kd],
    ]


def _volume_kd_filters_flat(min_volume: int, max_kd: int) -> list:
    """For keyword_suggestions and keyword_ideas (filters use keyword_info / keyword_properties)."""
    return [
        ["keyword_info.search_volume", ">=", min_volume],
        "and",
        ["keyword_properties.keyword_difficulty", "<=", max_kd],
    ]


def call_related_keywords(
    login: str,
    password: str,
    keywords: list[str],
    *,
    max_difficulty: int = 70,
    country_iso: str = "CA",
) -> tuple[list[dict], float]:
    """Labs related_keywords/live allows only one task per HTTP POST (status 40000 otherwise)."""
    rows: list[dict] = []
    total_cost = 0.0
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        payload = [
            {
                **_labs_geo_task_fields(country_iso),
                "keyword": kw,
                "depth": 2,
                "limit": DFS_LABS_KEYWORD_EXPANSION_LIMIT,
                "include_serp_info": True,
                "filters": _volume_kd_filters_nested(10, max_difficulty),
            }
        ]
        resp = _dfs_post(login, password, "/dataforseo_labs/google/related_keywords/live", payload)
        _check_tasks(resp)
        total_cost += _response_cost(resp)
        for t in resp.get("tasks") or []:
            for res in t.get("result") or []:
                for item in res.get("items") or []:
                    kd = item.get("keyword_data")
                    if not isinstance(kd, dict):
                        continue
                    row = _keyword_data_block_to_explorer_row(kd, source_endpoint="keywords_explorer")
                    if row["keyword"]:
                        rows.append(row)
    return rows, total_cost


def call_keyword_suggestions(
    login: str,
    password: str,
    keywords: list[str],
    *,
    max_difficulty: int = 70,
    min_volume: int = 10,
    country_iso: str = "CA",
) -> tuple[list[dict], float]:
    """Labs keyword_suggestions/live allows only one task per HTTP POST (status 40000 otherwise)."""
    rows: list[dict] = []
    total_cost = 0.0
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        payload = [
            {
                **_labs_geo_task_fields(country_iso),
                "keyword": kw,
                "limit": DFS_LABS_KEYWORD_EXPANSION_LIMIT,
                "include_serp_info": True,
                "filters": _volume_kd_filters_flat(min_volume, max_difficulty),
            }
        ]
        resp = _dfs_post(login, password, "/dataforseo_labs/google/keyword_suggestions/live", payload)
        _check_tasks(resp)
        total_cost += _response_cost(resp)
        for t in resp.get("tasks") or []:
            for res in t.get("result") or []:
                for item in res.get("items") or []:
                    kd = item.get("keyword_data")
                    if not isinstance(kd, dict):
                        continue
                    row = _keyword_data_block_to_explorer_row(kd, source_endpoint="keywords_explorer")
                    if row["keyword"]:
                        rows.append(row)
    return rows, total_cost


def call_keyword_overview(
    login: str,
    password: str,
    keywords: list[str],
    *,
    country_iso: str = "CA",
) -> tuple[list[dict], float]:
    """Fetch current metrics for exact keywords via Labs keyword_overview/live.

    Accepts up to 700 keywords per request. Returns the same normalized row
    structure as other ``call_*`` functions via ``_keyword_data_block_to_explorer_row``.
    """
    seeds = [k.strip() for k in keywords if (k or "").strip()]
    if not seeds:
        return [], 0.0
    all_rows: list[dict] = []
    total_cost = 0.0
    for i in range(0, len(seeds), 700):
        chunk = seeds[i : i + 700]
        payload = [
            {
                **_labs_geo_task_fields(country_iso),
                "keywords": chunk,
                "include_serp_info": True,
            }
        ]
        resp = _dfs_post(login, password, "/dataforseo_labs/google/keyword_overview/live", payload)
        _check_tasks(resp)
        total_cost += _response_cost(resp)
        for t in resp.get("tasks") or []:
            for res in t.get("result") or []:
                for item in res.get("items") or []:
                    # keyword_overview items ARE keyword_data blocks directly
                    row = _keyword_data_block_to_explorer_row(item, source_endpoint="keywords_explorer")
                    if row["keyword"]:
                        all_rows.append(row)
    return all_rows, total_cost


def call_keyword_ideas(
    login: str,
    password: str,
    keywords: list[str],
    *,
    max_difficulty: int = 70,
    min_volume: int = 10,
    country_iso: str = "CA",
) -> tuple[list[dict], float]:
    """Batch up to 200 seeds per request (Labs)."""
    seeds = [k.strip() for k in keywords if (k or "").strip()]
    if not seeds:
        return [], 0.0
    all_rows: list[dict] = []
    total_cost = 0.0
    for i in range(0, len(seeds), 200):
        chunk = seeds[i : i + 200]
        payload = [
            {
                **_labs_geo_task_fields(country_iso),
                "keywords": chunk,
                "limit": DFS_LABS_KEYWORD_EXPANSION_LIMIT,
                "closely_variants": False,
                "include_serp_info": True,
                "filters": _volume_kd_filters_flat(min_volume, max_difficulty),
            }
        ]
        resp = _dfs_post(login, password, "/dataforseo_labs/google/keyword_ideas/live", payload)
        _check_tasks(resp)
        total_cost += _response_cost(resp)
        for t in resp.get("tasks") or []:
            for res in t.get("result") or []:
                for item in res.get("items") or []:
                    kd = item.get("keyword_data")
                    if not isinstance(kd, dict):
                        continue
                    row = _keyword_data_block_to_explorer_row(kd, source_endpoint="keywords_explorer")
                    if row["keyword"]:
                        all_rows.append(row)
    return all_rows, total_cost


def call_google_autocomplete_suggestions(
    login: str,
    password: str,
    keywords: list[str],
    *,
    max_difficulty: int = 70,
    country_iso: str = "CA",
) -> tuple[list[dict], float]:
    """SERP Autocomplete strings, then Labs keyword_ideas (batched, min volume 5) for metrics.

    Autocomplete live/advanced follows the same one-task-per-POST rule as other Live endpoints.
    Enrichment uses keyword_ideas (up to 200 per call) instead of keyword_suggestions (1 per call)
    to avoid excessive per-keyword API costs.
    """
    cost = 0.0
    suggestion_strings: list[str] = []
    seen_s: set[str] = set()
    for kw in keywords:
        kw = (kw or "").strip()
        if not kw:
            continue
        payload = [
            {
                **_labs_geo_task_fields(country_iso),
                "keyword": kw,
            }
        ]
        resp = _dfs_post(
            login, password, "/serp/google/autocomplete/live/advanced", payload, timeout=90
        )
        _check_tasks(resp)
        cost += _response_cost(resp)
        for t in resp.get("tasks") or []:
            for res in t.get("result") or []:
                for it in res.get("items") or []:
                    if isinstance(it, dict):
                        s = (it.get("title") or it.get("suggestion") or it.get("keyword") or "").strip()
                    elif isinstance(it, str):
                        s = it.strip()
                    else:
                        s = ""
                    if not s:
                        continue
                    low = s.lower()
                    if low not in seen_s:
                        seen_s.add(low)
                        suggestion_strings.append(s)
    if not suggestion_strings:
        return [], cost
    to_enrich = suggestion_strings[:200]
    enriched, c2 = call_keyword_ideas(
        login,
        password,
        to_enrich,
        max_difficulty=max_difficulty,
        min_volume=5,
        country_iso=country_iso,
    )
    cost += c2
    by_kw = {r["keyword"].lower(): r for r in enriched}
    final: list[dict] = []
    for s in suggestion_strings:
        key = s.lower()
        r = by_kw.get(key)
        if r:
            final.append(dict(r))
        else:
            final.append(
                {
                    "keyword": s,
                    "volume": 0,
                    "difficulty": None,
                    "traffic_potential": 0,
                    "intents": {},
                    "parent_topic": None,
                    "cpc": None,
                    "global_volume": None,
                    "cps": None,
                    "serp_features": None,
                    "first_seen": None,
                    "serp_last_update": None,
                    "source_endpoint": "keywords_explorer",
                }
            )
    return final, cost


def call_bulk_traffic_estimation(
    login: str,
    password: str,
    domains: list[str],
    *,
    country_iso: str = "CA",
) -> tuple[dict[str, int], float]:
    """Domain-level estimated monthly organic traffic (ETV) per target — DataForSEO Labs bulk_traffic_estimation.

    Returns ``({normalized_domain: int_etv}, total_cost_usd)``. Up to 1000 targets per request.
    See https://docs.dataforseo.com/v3/dataforseo_labs-bulk_traffic_estimation-live/
    """
    seen: list[str] = []
    norm_set: set[str] = set()
    for raw in domains:
        d = _norm_domain(str(raw))
        if not d or d in norm_set:
            continue
        norm_set.add(d)
        seen.append(d)
    if not seen:
        return {}, 0.0
    out: dict[str, int] = {}
    total_cost = 0.0
    for i in range(0, len(seen), 1000):
        chunk = seen[i : i + 1000]
        payload = [
            {
                **_labs_geo_task_fields(country_iso),
                "targets": chunk,
                "item_types": ["organic"],
            }
        ]
        resp = _dfs_post(login, password, "/dataforseo_labs/bulk_traffic_estimation/live", payload)
        _check_tasks(resp)
        total_cost += _response_cost(resp)
        for t in resp.get("tasks") or []:
            for res in t.get("result") or []:
                for item in res.get("items") or []:
                    tgt = _norm_domain(str(item.get("target") or ""))
                    if not tgt:
                        continue
                    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
                    organic = metrics.get("organic") if isinstance(metrics.get("organic"), dict) else {}
                    etv_raw = organic.get("etv")
                    etv = 0
                    if etv_raw is not None:
                        try:
                            etv = int(max(0, round(float(etv_raw))))
                        except (TypeError, ValueError):
                            etv = 0
                    out[tgt] = etv
    return out, total_cost


def _organic_count(metrics: dict) -> int:
    org = metrics.get("organic") if isinstance(metrics, dict) else None
    if not isinstance(org, dict):
        return 0
    if "count" in org:
        try:
            return int(org["count"])
        except (TypeError, ValueError):
            pass
    total = 0
    for k, v in org.items():
        if k.startswith("pos_") and isinstance(v, (int, float)):
            total += int(v)
    return total


def call_serp_competitors(
    login: str,
    password: str,
    keywords: list[str],
    *,
    country_iso: str = "CA",
    limit: int = 50,
) -> tuple[list[dict], float]:
    """Labs ``serp_competitors``: domains that rank for the given keyword set (SERP / market peers).

    Up to 200 keywords per task (DataForSEO limit). See
    https://docs.dataforseo.com/v3/dataforseo_labs-google-serp_competitors-live/
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for raw in keywords:
        k = (raw or "").strip().lower()
        if not k or k in seen_set:
            continue
        seen_set.add(k)
        seen.append(k)
    if not seen:
        return [], 0.0
    if len(seen) > 200:
        seen = seen[:200]
    out_cap = max(1, min(int(limit), 1000))
    task_obj: dict[str, object] = {
        **_labs_geo_task_fields(country_iso),
        "keywords": seen,
        "item_types": ["organic"],
        "limit": out_cap,
        # Ask Labs to rank by seed-set ETV (default is ``rating``); otherwise ``limit`` truncates in
        # rating order and high-ETV domains can be missing from the slice we reorder client-side.
        "order_by": ["etv,desc"],
    }
    payload = [task_obj]
    timeout = 180 if len(seen) > 25 else 120
    resp = _dfs_post(login, password, "/dataforseo_labs/google/serp_competitors/live", payload, timeout=timeout)
    try:
        _check_tasks(resp)
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "40501" in str(exc) and "order_by" in msg:
            task_obj.pop("order_by", None)
            api_limit = min(100, max(out_cap * 2, 80))
            if api_limit > out_cap:
                task_obj["limit"] = api_limit
            resp = _dfs_post(login, password, "/dataforseo_labs/google/serp_competitors/live", [task_obj], timeout=timeout)
            _check_tasks(resp)
        else:
            raise
    cost = _response_cost(resp)
    rows: list[dict] = []
    for t in resp.get("tasks") or []:
        for res in t.get("result") or []:
            for item in res.get("items") or []:
                dom = (item.get("domain") or "").strip().lower()
                if not dom:
                    continue
                etv_raw = item.get("etv")
                etv = 0.0
                if etv_raw is not None:
                    try:
                        etv = float(etv_raw)
                    except (TypeError, ValueError):
                        etv = 0.0
                rows.append(
                    {
                        "domain": dom,
                        "etv": etv,
                        "rating": int(item.get("rating") or 0),
                        "visibility": float(item.get("visibility") or 0.0),
                        "keywords_count": int(item.get("keywords_count") or 0),
                        "avg_position": int(item.get("avg_position") or 0),
                        "median_position": int(item.get("median_position") or 0),
                    }
                )
    rows.sort(key=lambda r: r["etv"], reverse=True)
    return rows[:out_cap], cost


def call_competitors_domain(
    login: str,
    password: str,
    target_domain: str,
    *,
    country_iso: str = "CA",
    limit: int = DFS_COMPETITORS_DOMAIN_DEFAULT_LIMIT,
) -> tuple[list[dict], float]:
    target = _norm_domain(target_domain)
    if not target:
        return [], 0.0
    payload = [
        {
            **_labs_geo_task_fields(country_iso),
            "target": target,
            "exclude_top_domains": True,
            "limit": limit,
            "item_types": ["organic"],
            # Labs returns 40501 ``Invalid Field: 'order_by'`` if ``order_by`` is sent here; rank by
            # ``traffic`` (full-domain organic ETV) among the returned rows only.
        }
    ]
    resp = _dfs_post(login, password, "/dataforseo_labs/google/competitors_domain/live", payload)
    _check_tasks(resp)
    cost = _response_cost(resp)
    profiles: list[dict] = []
    for t in resp.get("tasks") or []:
        for res in t.get("result") or []:
            target_metrics = res.get("metrics") or {}
            target_organic = _organic_count(target_metrics)
            for item in res.get("items") or []:
                dom = (item.get("domain") or "").strip().lower()
                if not dom:
                    continue
                intersections = int(item.get("intersections") or 0)
                comp_metrics = item.get("metrics") or {}
                comp_full = item.get("full_domain_metrics") or {}
                comp_organic = _organic_count(comp_full if comp_full else comp_metrics)
                share = 0.0
                if comp_organic and intersections:
                    share = min(1.0, intersections / max(comp_organic, 1))
                # Full-domain organic ETV only (``metrics.organic`` is overlap vs target).
                etv = 0
                fd_org = comp_full.get("organic") if isinstance(comp_full, dict) else {}
                if isinstance(fd_org, dict) and fd_org.get("etv") is not None:
                    try:
                        etv = int(float(fd_org["etv"]))
                    except (TypeError, ValueError):
                        etv = 0
                profiles.append(
                    {
                        "competitor_domain": dom,
                        "keywords_common": intersections,
                        "keywords_competitor": comp_organic or intersections,
                        "keywords_target": target_organic,
                        "share": float(share),
                        "traffic": etv,
                    }
                )
    profiles.sort(key=lambda p: int(p.get("traffic") or 0), reverse=True)
    return profiles[:limit], cost


def _ranked_item_to_site_explorer_row(item: dict, domain: str) -> dict | None:
    kw_data = item.get("keyword_data")
    if not isinstance(kw_data, dict):
        return None
    kw = (kw_data.get("keyword") or "").strip()
    if not kw:
        return None
    ki = kw_data.get("keyword_info") or {}
    kp = kw_data.get("keyword_properties") or {}
    sii = kw_data.get("search_intent_info") or {}
    main = (sii.get("main_intent") or "").lower()
    intents = {
        "informational": main == "informational",
        "commercial": main == "commercial",
        "transactional": main == "transactional",
        "navigational": main == "navigational",
        "branded": False,
        "local": False,
    }
    for f in sii.get("foreign_intent") or []:
        fl = str(f).lower()
        if fl in intents:
            intents[fl] = True
    serp = kw_data.get("serp_info") or {}
    serp_features = _serp_item_types_to_feature_counts(serp.get("serp_item_types"))
    vol = int(ki.get("search_volume") or 0)
    kd_raw = kp.get("keyword_difficulty")
    difficulty = int(kd_raw) if kd_raw is not None else None
    rse = item.get("ranked_serp_element") or {}
    serp_item = rse.get("serp_item") if isinstance(rse, dict) else None
    rank_group = None
    url = None
    kind = None
    etv = vol
    if isinstance(serp_item, dict):
        rg = serp_item.get("rank_group")
        if rg is not None:
            try:
                rank_group = int(rg)
            except (TypeError, ValueError):
                rank_group = None
        url = serp_item.get("url") or serp_item.get("final_url")
        kind = serp_item.get("type")
        if serp_item.get("etv") is not None:
            try:
                etv = int(float(serp_item["etv"]))
            except (TypeError, ValueError):
                etv = vol
    return {
        "keyword": kw,
        "volume": vol,
        "difficulty": difficulty,
        "traffic_potential": etv,
        "cpc": ki.get("cpc"),
        "intents": intents,
        "parent_topic": kp.get("core_keyword"),  # see module docstring; maps to `keyword_metrics.parent_topic`
        "serp_features": serp_features,
        "word_count": len(kw.split()),
        "best_position": rank_group,
        "best_position_url": url,
        "best_position_kind": kind,
        "source_endpoint": "site_explorer",
        "competitor_domain": domain,
    }


def call_ranked_keywords(
    login: str,
    password: str,
    domain: str,
    *,
    max_difficulty: int = 70,
    country_iso: str = "CA",
) -> tuple[list[dict], float]:
    target = _norm_domain(domain)
    if not target:
        return [], 0.0
    payload = [
        {
            **_labs_geo_task_fields(country_iso),
            "target": target,
            "item_types": ["organic"],
            "limit": DFS_LABS_RANKED_KEYWORDS_LIMIT,
            "filters": [
                ["keyword_data.keyword_info.search_volume", ">=", 10],
                "and",
                ["keyword_data.keyword_properties.keyword_difficulty", "<=", max_difficulty],
            ],
        }
    ]
    resp = _dfs_post(login, password, "/dataforseo_labs/google/ranked_keywords/live", payload)
    _check_tasks(resp)
    cost = _response_cost(resp)
    rows: list[dict] = []
    for t in resp.get("tasks") or []:
        for res in t.get("result") or []:
            for item in res.get("items") or []:
                row = _ranked_item_to_site_explorer_row(item, target)
                if row:
                    rows.append(row)
    return rows, cost


def _relevant_page_to_top_page(item: dict) -> dict | None:
    page = item.get("page") if isinstance(item.get("page"), dict) else item
    if not isinstance(page, dict):
        return None
    url = (page.get("url") or "").strip()
    if not url:
        return None
    metrics = page.get("metrics") or {}
    org = metrics.get("organic") if isinstance(metrics, dict) else {}
    etv = 0
    if isinstance(org, dict) and org.get("etv") is not None:
        try:
            etv = int(float(org["etv"]))
        except (TypeError, ValueError):
            etv = 0
    kw_count = _organic_count(metrics) if metrics else 0
    top_kw = ""
    top_vol = 0
    top_pos = 0
    kps = page.get("keyword_positions") or item.get("keyword_positions")
    if isinstance(kps, list) and kps:
        first = kps[0]
        if isinstance(first, dict):
            top_kw = (first.get("keyword") or "").strip()
            ki = first.get("keyword_info") if isinstance(first.get("keyword_info"), dict) else {}
            top_vol = int(ki.get("search_volume") or 0)
            top_pos = int(first.get("rank_group") or first.get("rank_absolute") or 0)
    return {
        "url": url,
        "top_keyword": top_kw,
        "top_keyword_volume": top_vol,
        "top_keyword_best_position": top_pos,
        "keywords": kw_count,
        "sum_traffic": etv,
        "value": 0,
        "page_type": "",
    }


def call_relevant_pages(
    login: str,
    password: str,
    domain: str,
    *,
    country_iso: str = "CA",
    limit: int = DFS_LABS_RELEVANT_PAGES_DEFAULT,
) -> tuple[list[dict], float]:
    target = _norm_domain(domain)
    if not target:
        return [], 0.0
    payload = [
        {
            **_labs_geo_task_fields(country_iso),
            "target": target,
            "limit": limit,
        }
    ]
    resp = _dfs_post(login, password, "/dataforseo_labs/google/relevant_pages/live", payload)
    _check_tasks(resp)
    cost = _response_cost(resp)
    pages: list[dict] = []
    for t in resp.get("tasks") or []:
        for res in t.get("result") or []:
            for item in res.get("items") or []:
                p = _relevant_page_to_top_page(item)
                if p:
                    pages.append(p)
    pages.sort(key=lambda x: x.get("sum_traffic") or 0, reverse=True)
    return pages, cost
