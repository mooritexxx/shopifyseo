"""Cache infrastructure for Google API responses.

SQLite-backed persistent cache with in-memory TTL metadata. All cache schema
management and read/write helpers live here so other submodules can import them
without circular dependencies.
"""

import json
import sqlite3
import time


# -- Cache TTLs (seconds) -----------------------------------------------------

CACHE_TTLS: dict[str, int] = {
    "search_console_summary": 12 * 60 * 60,
    "search_console_url": 12 * 60 * 60,
    "search_console_overview": 60 * 60,
    "gsc_property_country": 60 * 60,
    "gsc_property_country_prev": 60 * 60,
    "gsc_property_device": 60 * 60,
    "gsc_property_device_prev": 60 * 60,
    "gsc_property_search_appearance": 60 * 60,
    "gsc_property_search_appearance_prev": 60 * 60,
    "gsc_property_query": 60 * 60,
    "gsc_property_page": 60 * 60,
    "ga4_property_overview": 60 * 60,
    "url_inspection": 7 * 24 * 60 * 60,
    "pagespeed": 7 * 24 * 60 * 60,
    "ga4_summary": 24 * 60 * 60,
    "ga4_url": 24 * 60 * 60,
}

# Single-dimension property reports for Google Signals (searchAnalytics/query).
# API dimension string must match https://developers.google.com/webmaster-tools/v1/searchanalytics/query
GSC_PROPERTY_BREAKDOWN_SPECS: tuple[tuple[str, str, str], ...] = (
    ("country", "gsc_property_country", "gsc_property_country_prev"),
    ("device", "gsc_property_device", "gsc_property_device_prev"),
    ("searchAppearance", "gsc_property_search_appearance", "gsc_property_search_appearance_prev"),
)
GSC_PROPERTY_BREAKDOWN_ROW_CAP = 500
GSC_QUERY_PAGE_ROW_CAP = 25000


# -- Schema -------------------------------------------------------------------

def ensure_google_cache_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS google_api_cache (
          cache_key TEXT PRIMARY KEY,
          cache_type TEXT NOT NULL,
          object_type TEXT,
          object_handle TEXT,
          url TEXT,
          strategy TEXT,
          payload_json TEXT NOT NULL,
          fetched_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_google_api_cache_type ON google_api_cache(cache_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_google_api_cache_url ON google_api_cache(url)"
    )
    conn.commit()


# -- Low-level read/write helpers ---------------------------------------------

def _now_ts() -> int:
    return int(time.time())


def _get_cache_row(conn: sqlite3.Connection, cache_key: str) -> sqlite3.Row | None:
    ensure_google_cache_schema(conn)
    return conn.execute(
        """
        SELECT cache_key, cache_type, object_type, object_handle, url, strategy, payload_json, fetched_at, expires_at
        FROM google_api_cache
        WHERE cache_key = ?
        """,
        (cache_key,),
    ).fetchone()


def _cache_meta(row: sqlite3.Row | None) -> dict:
    if not row:
        return {"exists": False, "stale": True, "fetched_at": None, "expires_at": None}
    now_ts = _now_ts()
    return {
        "exists": True,
        "stale": (row["expires_at"] or 0) <= now_ts,
        "fetched_at": row["fetched_at"],
        "expires_at": row["expires_at"],
    }


def _load_cached_payload(conn: sqlite3.Connection, cache_key: str) -> tuple[dict | None, dict]:
    row = _get_cache_row(conn, cache_key)
    if not row:
        return None, _cache_meta(None)
    try:
        payload = json.loads(row["payload_json"])
    except Exception:
        payload = None
    meta = _cache_meta(row)
    if isinstance(payload, dict):
        payload_meta = payload.get("_meta", {})
        if isinstance(payload_meta, dict):
            meta = {**meta, **payload_meta}
    return payload, meta


def _write_cache_payload(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    cache_type: str,
    payload: dict,
    ttl_seconds: int,
    object_type: str = "",
    object_handle: str = "",
    url: str = "",
    strategy: str = "",
) -> dict:
    ensure_google_cache_schema(conn)
    fetched_at = _now_ts()
    expires_at = fetched_at + ttl_seconds
    conn.execute(
        """
        INSERT INTO google_api_cache(
          cache_key, cache_type, object_type, object_handle, url, strategy, payload_json, fetched_at, expires_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cache_key) DO UPDATE SET
          cache_type = excluded.cache_type,
          object_type = excluded.object_type,
          object_handle = excluded.object_handle,
          url = excluded.url,
          strategy = excluded.strategy,
          payload_json = excluded.payload_json,
          fetched_at = excluded.fetched_at,
          expires_at = excluded.expires_at,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            cache_key,
            cache_type,
            object_type or None,
            object_handle or None,
            url or None,
            strategy or None,
            json.dumps(payload, ensure_ascii=True),
            fetched_at,
            expires_at,
        ),
    )
    conn.commit()
    return {"exists": True, "stale": False, "fetched_at": fetched_at, "expires_at": expires_at}


# -- Shared arithmetic helpers ------------------------------------------------

def _pct_delta(current: int, previous: int) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 2)


def _pct_delta_float(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 2)
