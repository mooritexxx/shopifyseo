import json
import logging
import os
import sqlite3
import time
from datetime import date, datetime
from urllib.parse import urlparse

from . import dashboard_google as dg
from . import dashboard_queries as dq
from .dashboard_config import apply_runtime_settings
from .dashboard_status import index_status_info
from .shopify_catalog_sync import DEFAULT_DB_PATH, ensure_schema


DB_PATH = DEFAULT_DB_PATH

_LOG = logging.getLogger(__name__)

GSC_QUERY_DIMENSION_ROW_CAP = 50


def _parse_iso_date_only(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def _gsc_window_for_dimensional_fetch(gsc_detail: dict | None, gsc_period: str = "mtd") -> tuple[date, date]:
    gd = gsc_detail or {}
    s = _parse_iso_date_only(gd.get("start_date"))
    e = _parse_iso_date_only(gd.get("end_date"))
    if s and e and e >= s:
        return s, e
    return dg.gsc_url_report_window(gsc_period)


SEO_SIGNAL_COLUMNS = {
    "gsc_clicks": "INTEGER",
    "gsc_impressions": "INTEGER",
    "gsc_ctr": "REAL",
    "gsc_position": "REAL",
    "gsc_last_fetched_at": "INTEGER",
    "ga4_sessions": "INTEGER",
    "ga4_views": "INTEGER",
    "ga4_avg_session_duration": "REAL",
    "ga4_last_fetched_at": "INTEGER",
    "index_status": "TEXT",
    "index_coverage": "TEXT",
    "google_canonical": "TEXT",
    "index_last_fetched_at": "INTEGER",
    "pagespeed_performance": "INTEGER",
    "pagespeed_seo": "INTEGER",
    "pagespeed_status": "TEXT",
    "pagespeed_last_fetched_at": "INTEGER",
    "seo_signal_updated_at": "TEXT",
}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _table_columns(conn, table)
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


def _migrate_keyword_research_runs_table(conn: sqlite3.Connection) -> None:
    """Rename legacy keyword-research run table once if present under a historical name."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('ahrefs_research_runs', 'keyword_research_runs')"
    )
    present = {row[0] for row in cur.fetchall()}
    if "ahrefs_research_runs" in present and "keyword_research_runs" not in present:
        conn.execute("ALTER TABLE ahrefs_research_runs RENAME TO keyword_research_runs")


def ensure_dashboard_schema(conn: sqlite3.Connection) -> None:
    ensure_schema(conn)
    dg.ensure_google_cache_schema(conn)
    _ensure_columns(conn, "products", SEO_SIGNAL_COLUMNS)
    _ensure_columns(conn, "collections", SEO_SIGNAL_COLUMNS)
    _ensure_columns(conn, "pages", SEO_SIGNAL_COLUMNS)
    _ensure_columns(conn, "blog_articles", SEO_SIGNAL_COLUMNS)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_workflow_states (
          object_type TEXT NOT NULL,
          handle TEXT NOT NULL,
          status TEXT NOT NULL,
          notes TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(object_type, handle)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_tokens (
          service TEXT PRIMARY KEY,
          access_token TEXT,
          refresh_token TEXT,
          token_type TEXT,
          expires_at INTEGER,
          scope TEXT,
          raw_json TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS service_settings (
          key TEXT PRIMARY KEY,
          value TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            content_type TEXT NOT NULL,
            primary_keyword TEXT NOT NULL,
            content_brief TEXT NOT NULL,
            total_volume INTEGER NOT NULL DEFAULT 0,
            avg_difficulty REAL NOT NULL DEFAULT 0.0,
            avg_opportunity REAL NOT NULL DEFAULT 0.0,
            match_type TEXT,
            match_handle TEXT,
            match_title TEXT,
            generated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cluster_keywords (
            cluster_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            PRIMARY KEY (cluster_id, keyword),
            FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
        )
        """
    )
    _ensure_columns(
        conn,
        "clusters",
        {
            "dominant_serp_features": "TEXT",
            "content_format_hints": "TEXT",
            "avg_cps": "REAL",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gsc_query_rows (
          object_type TEXT NOT NULL,
          object_handle TEXT NOT NULL,
          url TEXT NOT NULL,
          query TEXT NOT NULL,
          clicks INTEGER,
          impressions INTEGER,
          ctr REAL,
          position REAL,
          fetched_at INTEGER,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(object_type, object_handle, query)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gsc_query_dimension_rows (
          object_type TEXT NOT NULL,
          object_handle TEXT NOT NULL,
          query TEXT NOT NULL,
          dimension_kind TEXT NOT NULL,
          dimension_value TEXT NOT NULL,
          clicks INTEGER,
          impressions INTEGER,
          ctr REAL,
          position REAL,
          fetched_at INTEGER,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(object_type, object_handle, query, dimension_kind, dimension_value)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gsc_query_dimension_lookup
        ON gsc_query_dimension_rows(object_type, object_handle, dimension_kind)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seo_recommendations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          object_type TEXT NOT NULL,
          object_handle TEXT NOT NULL,
          category TEXT NOT NULL,
          priority TEXT,
          summary TEXT NOT NULL,
          details_json TEXT,
          source TEXT NOT NULL DEFAULT 'dashboard',
          status TEXT NOT NULL DEFAULT 'success',
          model TEXT,
          prompt_version TEXT,
          error_message TEXT,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_columns(
        conn,
        "seo_recommendations",
        {
            "status": "TEXT NOT NULL DEFAULT 'success'",
            "model": "TEXT",
            "prompt_version": "TEXT",
            "error_message": "TEXT",
            "updated_at": "TEXT",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_metrics (
            keyword TEXT PRIMARY KEY,
            volume INTEGER,
            difficulty INTEGER,
            traffic_potential INTEGER,
            cpc REAL,
            intent TEXT,
            content_type_label TEXT,
            intent_raw TEXT NOT NULL DEFAULT '{}',
            parent_topic TEXT,
            opportunity REAL,
            seed_keywords TEXT NOT NULL DEFAULT '[]',
            ranking_status TEXT NOT NULL DEFAULT 'not_ranking',
            gsc_position REAL,
            gsc_clicks INTEGER,
            gsc_impressions INTEGER,
            status TEXT NOT NULL DEFAULT 'new',
            updated_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    _ensure_columns(
        conn,
        "keyword_metrics",
        {
            "global_volume": "INTEGER",
            "parent_volume": "INTEGER",
            "clicks": "REAL",
            "cps": "REAL",
            "serp_features": "TEXT",
            "word_count": "INTEGER",
            "first_seen": "TEXT",
            "serp_last_update": "TEXT",
            "source_endpoint": "TEXT",
            "competitor_domain": "TEXT",
            "competitor_position": "INTEGER",
            "competitor_url": "TEXT",
            "competitor_position_kind": "TEXT",
            "is_local": "INTEGER DEFAULT 0",
            "content_format_hint": "TEXT DEFAULT ''",
        },
    )
    _migrate_keyword_research_runs_table(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_research_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at INTEGER NOT NULL,
            finished_at INTEGER,
            endpoint TEXT NOT NULL,
            seed_or_domain TEXT NOT NULL,
            rows_returned INTEGER DEFAULT 0,
            rows_new INTEGER DEFAULT 0,
            rows_updated INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'running',
            error_message TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS keyword_page_map (
            keyword TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_handle TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gsc',
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0,
            gsc_position REAL,
            is_primary INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (keyword, object_type, object_handle)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS competitor_keyword_gaps (
            keyword TEXT NOT NULL,
            competitor_domain TEXT NOT NULL,
            competitor_position INTEGER,
            competitor_url TEXT,
            our_ranking_status TEXT NOT NULL DEFAULT 'not_ranking',
            our_gsc_position REAL,
            volume INTEGER DEFAULT 0,
            difficulty INTEGER DEFAULT 0,
            traffic_potential INTEGER DEFAULT 0,
            gap_type TEXT NOT NULL DEFAULT 'they_rank_we_dont',
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (keyword, competitor_domain)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS competitor_profiles (
            domain TEXT PRIMARY KEY,
            keywords_common INTEGER DEFAULT 0,
            keywords_they_have INTEGER DEFAULT 0,
            keywords_we_have INTEGER DEFAULT 0,
            share REAL DEFAULT 0.0,
            traffic INTEGER DEFAULT 0,
            is_manual INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS competitor_top_pages (
            competitor_domain TEXT NOT NULL,
            url TEXT NOT NULL,
            top_keyword TEXT DEFAULT '',
            top_keyword_volume INTEGER DEFAULT 0,
            top_keyword_position INTEGER DEFAULT 0,
            total_keywords INTEGER DEFAULT 0,
            estimated_traffic INTEGER DEFAULT 0,
            traffic_value INTEGER DEFAULT 0,
            page_type TEXT DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (competitor_domain, url)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggested_title TEXT NOT NULL,
            brief TEXT NOT NULL,
            primary_keyword TEXT NOT NULL DEFAULT '',
            supporting_keywords TEXT NOT NULL DEFAULT '[]',
            search_intent TEXT NOT NULL DEFAULT 'informational',
            linked_cluster_id INTEGER,
            linked_cluster_name TEXT NOT NULL DEFAULT '',
            linked_collection_handle TEXT NOT NULL DEFAULT '',
            linked_collection_title TEXT NOT NULL DEFAULT '',
            gap_reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'idea',
            created_at INTEGER NOT NULL
        )
        """
    )
    _ensure_columns(
        conn,
        "article_ideas",
        {
            "total_volume": "INTEGER NOT NULL DEFAULT 0",
            "avg_difficulty": "REAL NOT NULL DEFAULT 0.0",
            "opportunity_score": "REAL NOT NULL DEFAULT 0.0",
            "dominant_serp_features": "TEXT NOT NULL DEFAULT ''",
            "content_format_hints": "TEXT NOT NULL DEFAULT ''",
            "content_format": "TEXT NOT NULL DEFAULT ''",
            "source_type": "TEXT NOT NULL DEFAULT 'cluster_gap'",
            "linked_keywords_json": "TEXT NOT NULL DEFAULT '[]'",
            "estimated_monthly_traffic": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    _ensure_columns(
        conn,
        "article_ideas",
        {
            "linked_article_handle": "TEXT NOT NULL DEFAULT ''",
            "linked_blog_handle": "TEXT NOT NULL DEFAULT ''",
            "shopify_article_id": "TEXT NOT NULL DEFAULT ''",
        },
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS idea_articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            idea_id INTEGER NOT NULL REFERENCES article_ideas(id) ON DELETE CASCADE,
            blog_handle TEXT NOT NULL,
            article_handle TEXT NOT NULL,
            shopify_article_id TEXT NOT NULL DEFAULT '',
            angle_label TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            UNIQUE(idea_id, blog_handle, article_handle)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_target_keywords (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blog_handle TEXT NOT NULL,
            article_handle TEXT NOT NULL,
            keyword TEXT NOT NULL,
            is_primary INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'idea',
            UNIQUE(blog_handle, article_handle, keyword)
        )
        """
    )
    # Backfill idea_articles from legacy 1:1 link columns
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO idea_articles (idea_id, blog_handle, article_handle, shopify_article_id, angle_label, created_at)
            SELECT id, linked_blog_handle, linked_article_handle, shopify_article_id, '', created_at
            FROM article_ideas
            WHERE linked_article_handle != '' AND linked_blog_handle != ''
            """
        )
        conn.commit()
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            object_type       TEXT NOT NULL,
            object_handle     TEXT NOT NULL,
            chunk_index       INTEGER NOT NULL DEFAULT 0,
            text_hash         TEXT NOT NULL,
            model_version     TEXT NOT NULL,
            embedding         BLOB NOT NULL,
            source_text_preview TEXT,
            token_count       INTEGER,
            updated_at        TEXT NOT NULL,
            PRIMARY KEY (object_type, object_handle, chunk_index)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(object_type)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            call_type TEXT NOT NULL,
            stage TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_usd REAL DEFAULT 0.0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_api_usage_log_created ON api_usage_log(created_at)"
    )
    conn.commit()


def _pagespeed_status(payload: dict | None) -> str:
    if not payload:
        return "never_fetched"
    meta = payload.get("_cache") or {}
    if meta.get("rate_limited"):
        return "rate_limited"
    if not meta.get("exists"):
        return "never_fetched"
    if meta.get("stale"):
        return "stale"
    cats = payload.get("lighthouseResult", {}).get("categories", {})
    if cats.get("performance", {}).get("score") is not None:
        return "fresh"
    return "unknown"


def _lighthouse_category_score_pct(categories: dict | None, category: str) -> int | None:
    """Map Lighthouse category.score to 0–100. PSI uses 0–1 floats; some payloads use 0–100."""
    if not isinstance(categories, dict):
        return None
    raw = (categories.get(category) or {}).get("score")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 1.0:
        return int(round(v * 100))
    if v <= 100.0:
        return int(round(v))
    return int(round(max(0.0, min(100.0, v))))


def _ga4_find_row_for_path(rows: list, path: str) -> dict | None:
    for row in rows:
        values = row.get("dimensionValues") or [{"value": ""}]
        if values[0].get("value", "") == path:
            return row
    return None


def _ga4_landing_row_for_url(ga4_summary: dict | None, url: str) -> dict | None:
    if not ga4_summary:
        return None
    path = urlparse(url or "").path or "/"
    rows = ga4_summary.get("landing_rows") or ga4_summary.get("rows") or []
    return _ga4_find_row_for_path(rows, path)


def _ga4_pageview_row_for_url(ga4_summary: dict | None, url: str) -> dict | None:
    if not ga4_summary:
        return None
    path = urlparse(url or "").path or "/"
    return _ga4_find_row_for_path(ga4_summary.get("page_rows") or [], path)


def _resolve_ga4_metrics_for_url(
    conn: sqlite3.Connection,
    url: str,
    object_type: str,
    handle: str,
    *,
    ga4_refresh: bool = False,
) -> tuple[int | None, int | None, float | None, int | None]:
    """Prefer per-URL GA4 (get_ga4_url_detail); fall back to property summary rollup."""
    detail: dict | None = None
    try:
        detail = dg.get_ga4_url_detail(
            conn, url, refresh=ga4_refresh, object_type=object_type, object_handle=handle
        )
    except Exception as exc:
        _LOG.debug("GA4 URL detail failed for %s: %s", url, exc)
    meta = (detail or {}).get("_cache") or {}
    if detail is not None and meta.get("exists") and detail.get("views") is not None:
        return (
            int(detail.get("sessions") or 0),
            int(detail.get("views") or 0),
            float(detail.get("avg_session_duration") or 0.0),
            meta.get("fetched_at"),
        )
    try:
        ga4_summary = dg.get_ga4_summary(conn, refresh=False)
    except Exception:
        ga4_summary = None
    ga4_meta = (ga4_summary or {}).get("_cache") or {}
    ga4_landing_row = _ga4_landing_row_for_url(ga4_summary, url)
    ga4_landing_metrics = ga4_landing_row.get("metricValues", []) if ga4_landing_row else []
    ga4_pageview_row = _ga4_pageview_row_for_url(ga4_summary, url)
    ga4_pageview_metrics = ga4_pageview_row.get("metricValues", []) if ga4_pageview_row else []
    return (
        int(float(ga4_landing_metrics[0].get("value", 0))) if len(ga4_landing_metrics) > 0 else None,
        int(float(ga4_pageview_metrics[0].get("value", 0))) if len(ga4_pageview_metrics) > 0 else None,
        float(ga4_landing_metrics[1].get("value", 0)) if len(ga4_landing_metrics) > 1 else None,
        ga4_meta.get("fetched_at"),
    )


def _refresh_object_signals_into_table(conn: sqlite3.Connection, table: str, object_type: str, handle: str) -> None:
    url = dq.object_url(object_type, handle)
    gsc_detail = dg.get_search_console_url_detail(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    dg.invalidate_pagespeed_memory_cache(url)
    pagespeed_detail = dg.get_pagespeed(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    ga4_sessions, ga4_views, ga4_avg_dur, ga4_fetched_at = _resolve_ga4_metrics_for_url(
        conn, url, object_type, handle, ga4_refresh=False
    )

    gsc_row = (gsc_detail.get("page_rows") or [None])[0] if gsc_detail else None
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    inspection_meta = (inspection_detail or {}).get("_cache") or {}
    pagespeed_meta = (pagespeed_detail or {}).get("_cache") or {}
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    index_label, _, _ = index_status_info(inspection_detail)
    has_inspection_data = bool(idx.get("indexingState") or idx.get("coverageState"))

    if not has_inspection_data:
        existing = conn.execute(
            f"SELECT index_status, index_coverage, google_canonical, index_last_fetched_at FROM {table} WHERE handle = ?",
            (handle,),
        ).fetchone()
        if existing and (existing[0] or existing[1] or existing[2]):
            index_label = existing[0]
            idx = {"coverageState": existing[1], "googleCanonical": existing[2]}
            inspection_meta = {"fetched_at": existing[3]}

    cats = (pagespeed_detail or {}).get("lighthouseResult", {}).get("categories", {}) or {}
    conn.execute(
        f"""
        UPDATE {table}
        SET gsc_clicks = ?,
            gsc_impressions = ?,
            gsc_ctr = ?,
            gsc_position = ?,
            gsc_last_fetched_at = ?,
            ga4_sessions = ?,
            ga4_views = ?,
            ga4_avg_session_duration = ?,
            ga4_last_fetched_at = ?,
            index_status = ?,
            index_coverage = ?,
            google_canonical = ?,
            index_last_fetched_at = ?,
            pagespeed_performance = ?,
            pagespeed_seo = ?,
            pagespeed_status = ?,
            pagespeed_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (
            int(gsc_row.get("clicks", 0)) if gsc_row else None,
            int(gsc_row.get("impressions", 0)) if gsc_row else None,
            float(gsc_row.get("ctr", 0)) if gsc_row else None,
            float(gsc_row.get("position", 0)) if gsc_row else None,
            gsc_meta.get("fetched_at"),
            ga4_sessions,
            ga4_views,
            ga4_avg_dur,
            ga4_fetched_at,
            index_label,
            idx.get("coverageState"),
            idx.get("googleCanonical"),
            inspection_meta.get("fetched_at"),
            _lighthouse_category_score_pct(cats, "performance"),
            _lighthouse_category_score_pct(cats, "seo"),
            _pagespeed_status(pagespeed_detail),
            pagespeed_meta.get("fetched_at"),
            handle,
        ),
    )

    _write_gsc_per_url_query_caches(conn, object_type, handle, url, gsc_detail)


def _write_gsc_per_url_query_caches(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    url: str,
    gsc_detail: dict | None,
) -> None:
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    conn.execute(
        "DELETE FROM gsc_query_rows WHERE object_type = ? AND object_handle = ?",
        (object_type, handle),
    )
    for row in (gsc_detail or {}).get("query_rows", []):
        query = (row.get("keys") or [""])[0]
        if not query:
            continue
        conn.execute(
            """
            INSERT INTO gsc_query_rows(
              object_type, object_handle, url, query, clicks, impressions, ctr, position, fetched_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                object_type,
                handle,
                url,
                query,
                int(row.get("clicks", 0)),
                int(row.get("impressions", 0)),
                float(row.get("ctr", 0)),
                float(row.get("position", 0)),
                gsc_meta.get("fetched_at"),
            ),
        )

    _refresh_gsc_query_dimensions_into_table(
        conn,
        object_type,
        handle,
        url,
        fetched_at=gsc_meta.get("fetched_at"),
        gsc_detail=gsc_detail,
        gsc_period=(gsc_detail or {}).get("period_mode") or "mtd",
    )


def _refresh_object_pagespeed_into_table(conn: sqlite3.Connection, table: str, object_type: str, handle: str) -> None:
    url = dq.object_url(object_type, handle)
    dg.invalidate_pagespeed_memory_cache(url)
    pagespeed_detail = dg.get_pagespeed(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    pagespeed_meta = (pagespeed_detail or {}).get("_cache") or {}
    cats = (pagespeed_detail or {}).get("lighthouseResult", {}).get("categories", {}) or {}
    conn.execute(
        f"""
        UPDATE {table}
        SET pagespeed_performance = ?,
            pagespeed_seo = ?,
            pagespeed_status = ?,
            pagespeed_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (
            _lighthouse_category_score_pct(cats, "performance"),
            _lighthouse_category_score_pct(cats, "seo"),
            _pagespeed_status(pagespeed_detail),
            pagespeed_meta.get("fetched_at"),
            handle,
        ),
    )


def _refresh_object_pagespeed_into_blog_article(conn: sqlite3.Connection, composite_handle: str) -> None:
    parts = _parse_blog_article_parts(composite_handle)
    if not parts:
        return
    blog_h, art_h = parts
    object_type = "blog_article"
    handle = composite_handle
    url = dq.object_url(object_type, handle)
    dg.invalidate_pagespeed_memory_cache(url)
    pagespeed_detail = dg.get_pagespeed(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    pagespeed_meta = (pagespeed_detail or {}).get("_cache") or {}
    cats = (pagespeed_detail or {}).get("lighthouseResult", {}).get("categories", {}) or {}
    conn.execute(
        """
        UPDATE blog_articles
        SET pagespeed_performance = ?,
            pagespeed_seo = ?,
            pagespeed_status = ?,
            pagespeed_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (
            _lighthouse_category_score_pct(cats, "performance"),
            _lighthouse_category_score_pct(cats, "seo"),
            _pagespeed_status(pagespeed_detail),
            pagespeed_meta.get("fetched_at"),
            blog_h,
            art_h,
        ),
    )


def gsc_dimensional_fetch_enabled() -> bool:
    """When true, bulk/per-URL GSC refresh also fetches query×country/device/searchAppearance (3× API per URL)."""
    return (os.getenv("GSC_DIMENSIONAL_FETCH") or "").strip().lower() in ("1", "true", "yes", "on")


def _refresh_gsc_query_dimensions_into_table(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    page_url: str,
    *,
    fetched_at: int | None,
    gsc_detail: dict | None = None,
    gsc_period: str = "mtd",
) -> None:
    if not gsc_dimensional_fetch_enabled():
        return
    site_url = (dg.get_service_setting(conn, "search_console_site") or "").strip()
    if not site_url:
        return
    start, end = _gsc_window_for_dimensional_fetch(gsc_detail, gsc_period)
    ts = int(fetched_at or time.time())
    for second_dim in ("country", "device", "searchAppearance"):
        rows, err = dg.fetch_gsc_url_query_second_dimension(
            conn, site_url, page_url, start, end, second_dimension=second_dim
        )
        if err:
            _LOG.warning(
                "GSC dimensional fetch failed (%s %s %s): %s",
                object_type,
                handle,
                second_dim,
                err,
            )
            continue
        rows_sorted = sorted(rows, key=lambda r: int(r.get("impressions") or 0), reverse=True)[:GSC_QUERY_DIMENSION_ROW_CAP]
        conn.execute(
            """
            DELETE FROM gsc_query_dimension_rows
            WHERE object_type = ? AND object_handle = ? AND dimension_kind = ?
            """,
            (object_type, handle, second_dim),
        )
        for r in rows_sorted:
            q = (r.get("query") or "").strip()
            seg = (r.get("segment") or "").strip()
            if not q or not seg:
                continue
            conn.execute(
                """
                INSERT INTO gsc_query_dimension_rows(
                  object_type, object_handle, query, dimension_kind, dimension_value,
                  clicks, impressions, ctr, position, fetched_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    object_type,
                    handle,
                    q,
                    second_dim,
                    seg,
                    int(r.get("clicks") or 0),
                    int(r.get("impressions") or 0),
                    float(r.get("ctr") or 0),
                    float(r.get("position") or 0),
                    ts,
                ),
            )


def _refresh_object_gsc_into_table(conn: sqlite3.Connection, table: str, object_type: str, handle: str) -> None:
    url = dq.object_url(object_type, handle)
    gsc_detail = dg.get_search_console_url_detail(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    gsc_row = (gsc_detail.get("page_rows") or [None])[0] if gsc_detail else None
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    conn.execute(
        f"""
        UPDATE {table}
        SET gsc_clicks = ?,
            gsc_impressions = ?,
            gsc_ctr = ?,
            gsc_position = ?,
            gsc_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (
            int(gsc_row.get("clicks", 0)) if gsc_row else None,
            int(gsc_row.get("impressions", 0)) if gsc_row else None,
            float(gsc_row.get("ctr", 0)) if gsc_row else None,
            float(gsc_row.get("position", 0)) if gsc_row else None,
            gsc_meta.get("fetched_at"),
            handle,
        ),
    )
    _write_gsc_per_url_query_caches(conn, object_type, handle, url, gsc_detail)


def _refresh_object_index_into_table(conn: sqlite3.Connection, table: str, object_type: str, handle: str) -> None:
    url = dq.object_url(object_type, handle)
    inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    inspection_meta = (inspection_detail or {}).get("_cache") or {}
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    index_label, _, _ = index_status_info(inspection_detail)
    conn.execute(
        f"""
        UPDATE {table}
        SET index_status = ?,
            index_coverage = ?,
            google_canonical = ?,
            index_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (
            index_label,
            idx.get("coverageState"),
            idx.get("googleCanonical"),
            inspection_meta.get("fetched_at"),
            handle,
        ),
    )


def _refresh_object_index_into_blog_article(conn: sqlite3.Connection, composite_handle: str) -> None:
    parts = _parse_blog_article_parts(composite_handle)
    if not parts:
        return
    blog_h, art_h = parts
    object_type = "blog_article"
    handle = composite_handle
    url = dq.object_url(object_type, handle)
    inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    inspection_meta = (inspection_detail or {}).get("_cache") or {}
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    index_label, _, _ = index_status_info(inspection_detail)
    conn.execute(
        """
        UPDATE blog_articles
        SET index_status = ?,
            index_coverage = ?,
            google_canonical = ?,
            index_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (
            index_label,
            idx.get("coverageState"),
            idx.get("googleCanonical"),
            inspection_meta.get("fetched_at"),
            blog_h,
            art_h,
        ),
    )


def _refresh_object_ga4_into_table(
    conn: sqlite3.Connection, table: str, object_type: str, handle: str, *, ga4_refresh: bool = False
) -> None:
    url = dq.object_url(object_type, handle)
    ga4_sessions, ga4_views, ga4_avg_dur, ga4_fetched_at = _resolve_ga4_metrics_for_url(
        conn, url, object_type, handle, ga4_refresh=ga4_refresh
    )
    conn.execute(
        f"""
        UPDATE {table}
        SET ga4_sessions = ?,
            ga4_views = ?,
            ga4_avg_session_duration = ?,
            ga4_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (
            ga4_sessions,
            ga4_views,
            ga4_avg_dur,
            ga4_fetched_at,
            handle,
        ),
    )


def _refresh_object_ga4_into_blog_article(
    conn: sqlite3.Connection, composite_handle: str, *, ga4_refresh: bool = False
) -> None:
    parts = _parse_blog_article_parts(composite_handle)
    if not parts:
        return
    blog_h, art_h = parts
    object_type = "blog_article"
    handle = composite_handle
    url = dq.object_url(object_type, handle)
    ga4_sessions, ga4_views, ga4_avg_dur, ga4_fetched_at = _resolve_ga4_metrics_for_url(
        conn, url, object_type, handle, ga4_refresh=ga4_refresh
    )
    conn.execute(
        """
        UPDATE blog_articles
        SET ga4_sessions = ?,
            ga4_views = ?,
            ga4_avg_session_duration = ?,
            ga4_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (
            ga4_sessions,
            ga4_views,
            ga4_avg_dur,
            ga4_fetched_at,
            blog_h,
            art_h,
        ),
    )


def _parse_blog_article_parts(composite_handle: str) -> tuple[str, str] | None:
    blog_h, sep, art_h = composite_handle.partition("/")
    if not sep or not art_h:
        return None
    return blog_h, art_h


def _refresh_blog_article_signals_into_table(conn: sqlite3.Connection, composite_handle: str) -> None:
    parts = _parse_blog_article_parts(composite_handle)
    if not parts:
        return
    blog_h, art_h = parts
    object_type = "blog_article"
    handle = composite_handle
    url = dq.object_url(object_type, handle)
    gsc_detail = dg.get_search_console_url_detail(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    dg.invalidate_pagespeed_memory_cache(url)
    pagespeed_detail = dg.get_pagespeed(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    ga4_sessions, ga4_views, ga4_avg_dur, ga4_fetched_at = _resolve_ga4_metrics_for_url(
        conn, url, object_type, handle, ga4_refresh=False
    )

    gsc_row = (gsc_detail.get("page_rows") or [None])[0] if gsc_detail else None
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    inspection_meta = (inspection_detail or {}).get("_cache") or {}
    pagespeed_meta = (pagespeed_detail or {}).get("_cache") or {}
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    index_label, _, _ = index_status_info(inspection_detail)
    cats = (pagespeed_detail or {}).get("lighthouseResult", {}).get("categories", {}) or {}
    conn.execute(
        """
        UPDATE blog_articles
        SET gsc_clicks = ?,
            gsc_impressions = ?,
            gsc_ctr = ?,
            gsc_position = ?,
            gsc_last_fetched_at = ?,
            ga4_sessions = ?,
            ga4_views = ?,
            ga4_avg_session_duration = ?,
            ga4_last_fetched_at = ?,
            index_status = ?,
            index_coverage = ?,
            google_canonical = ?,
            index_last_fetched_at = ?,
            pagespeed_performance = ?,
            pagespeed_seo = ?,
            pagespeed_status = ?,
            pagespeed_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (
            int(gsc_row.get("clicks", 0)) if gsc_row else None,
            int(gsc_row.get("impressions", 0)) if gsc_row else None,
            float(gsc_row.get("ctr", 0)) if gsc_row else None,
            float(gsc_row.get("position", 0)) if gsc_row else None,
            gsc_meta.get("fetched_at"),
            ga4_sessions,
            ga4_views,
            ga4_avg_dur,
            ga4_fetched_at,
            index_label,
            idx.get("coverageState"),
            idx.get("googleCanonical"),
            inspection_meta.get("fetched_at"),
            _lighthouse_category_score_pct(cats, "performance"),
            _lighthouse_category_score_pct(cats, "seo"),
            _pagespeed_status(pagespeed_detail),
            pagespeed_meta.get("fetched_at"),
            blog_h,
            art_h,
        ),
    )
    _write_gsc_per_url_query_caches(conn, object_type, handle, url, gsc_detail)


def _refresh_object_gsc_into_blog_article(conn: sqlite3.Connection, composite_handle: str) -> None:
    parts = _parse_blog_article_parts(composite_handle)
    if not parts:
        return
    blog_h, art_h = parts
    object_type = "blog_article"
    handle = composite_handle
    url = dq.object_url(object_type, handle)
    gsc_detail = dg.get_search_console_url_detail(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    gsc_row = (gsc_detail.get("page_rows") or [None])[0] if gsc_detail else None
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    conn.execute(
        """
        UPDATE blog_articles
        SET gsc_clicks = ?,
            gsc_impressions = ?,
            gsc_ctr = ?,
            gsc_position = ?,
            gsc_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (
            int(gsc_row.get("clicks", 0)) if gsc_row else None,
            int(gsc_row.get("impressions", 0)) if gsc_row else None,
            float(gsc_row.get("ctr", 0)) if gsc_row else None,
            float(gsc_row.get("position", 0)) if gsc_row else None,
            gsc_meta.get("fetched_at"),
            blog_h,
            art_h,
        ),
    )
    _write_gsc_per_url_query_caches(conn, object_type, handle, url, gsc_detail)


def refresh_object_structured_seo_data(conn: sqlite3.Connection, object_type: str, handle: str, *, snapshot_recommendation: bool = False) -> None:
    ensure_dashboard_schema(conn)
    if object_type == "blog_article":
        _refresh_blog_article_signals_into_table(conn, handle)
        conn.commit()
        return
    table = {
        "product": "products",
        "collection": "collections",
        "page": "pages",
    }[object_type]
    _refresh_object_signals_into_table(conn, table, object_type, handle)
    conn.commit()


def refresh_object_pagespeed_signal_data(conn: sqlite3.Connection, object_type: str, handle: str) -> None:
    ensure_dashboard_schema(conn)
    if object_type == "blog_article":
        _refresh_object_pagespeed_into_blog_article(conn, handle)
    else:
        table = _table_for_object_type(object_type)
        _refresh_object_pagespeed_into_table(conn, table, object_type, handle)
    conn.commit()


def _table_for_object_type(object_type: str) -> str:
    return {
        "product": "products",
        "collection": "collections",
        "page": "pages",
    }[object_type]


def refresh_gsc_signal_data_for_objects(conn: sqlite3.Connection, targets: list[tuple[str, str]], *, batch_size: int = 10) -> None:
    ensure_dashboard_schema(conn)
    for i, (object_type, handle) in enumerate(targets, 1):
        if object_type == "blog_article":
            _refresh_object_gsc_into_blog_article(conn, handle)
        else:
            _refresh_object_gsc_into_table(conn, _table_for_object_type(object_type), object_type, handle)
        if i % batch_size == 0:
            conn.commit()
    conn.commit()
    try:
        from .embedding_store import sync_embeddings
        sync_embeddings(conn, object_type="gsc_queries")
    except Exception:
        _LOG.warning("GSC embedding sync failed (non-fatal)", exc_info=True)


def refresh_index_signal_data_for_objects(conn: sqlite3.Connection, targets: list[tuple[str, str]], *, batch_size: int = 10) -> None:
    ensure_dashboard_schema(conn)
    for i, (object_type, handle) in enumerate(targets, 1):
        if object_type == "blog_article":
            _refresh_object_index_into_blog_article(conn, handle)
        else:
            _refresh_object_index_into_table(conn, _table_for_object_type(object_type), object_type, handle)
        if i % batch_size == 0:
            conn.commit()
    conn.commit()


def refresh_pagespeed_signal_data_for_objects(conn: sqlite3.Connection, targets: list[tuple[str, str]], *, batch_size: int = 10) -> None:
    ensure_dashboard_schema(conn)
    for i, (object_type, handle) in enumerate(targets, 1):
        if object_type == "blog_article":
            _refresh_object_pagespeed_into_blog_article(conn, handle)
        else:
            _refresh_object_pagespeed_into_table(conn, _table_for_object_type(object_type), object_type, handle)
        if i % batch_size == 0:
            conn.commit()
    conn.commit()


def refresh_pagespeed_columns_from_cache_for_all_cached_objects(conn: sqlite3.Connection) -> int:
    """Copy PageSpeed scores from `google_api_cache` into catalog tables (`products`, `collections`, `pages`).

    The API stores Lighthouse payloads in `google_api_cache`; list/detail UIs read denormalized columns on those
    tables. Without this step, rows can show \"—\" / never_fetched even when cache has a valid score.

    Call after bulk PageSpeed sync finishes (including when the queue is empty or API calls failed for some URLs).
    """
    ensure_dashboard_schema(conn)
    dg.ensure_google_cache_schema(conn)
    rows = conn.execute(
        """
        SELECT DISTINCT object_type, object_handle
        FROM google_api_cache
        WHERE cache_type = 'pagespeed'
          AND object_type IN ('product', 'collection', 'page', 'blog_article')
          AND object_handle IS NOT NULL
          AND TRIM(object_handle) != ''
          AND (strategy IS NULL OR TRIM(strategy) = '' OR strategy = 'mobile')
        ORDER BY object_type, object_handle
        """
    ).fetchall()
    batch_size = 10
    for i, row in enumerate(rows, 1):
        object_type = row["object_type"]
        handle = row["object_handle"]
        if object_type == "blog_article":
            _refresh_object_pagespeed_into_blog_article(conn, handle)
        else:
            _refresh_object_pagespeed_into_table(conn, _table_for_object_type(object_type), object_type, handle)
        if i % batch_size == 0:
            conn.commit()
    conn.commit()
    return len(rows)


def refresh_ga4_signal_data_for_objects(conn: sqlite3.Connection, targets: list[tuple[str, str]], *, batch_size: int = 10) -> None:
    ensure_dashboard_schema(conn)
    for i, (object_type, handle) in enumerate(targets, 1):
        if object_type == "blog_article":
            _refresh_object_ga4_into_blog_article(conn, handle)
        else:
            _refresh_object_ga4_into_table(conn, _table_for_object_type(object_type), object_type, handle)
        if i % batch_size == 0:
            conn.commit()
    conn.commit()


def refresh_structured_seo_data(conn: sqlite3.Connection, *, batch_size: int = 10) -> None:
    ensure_dashboard_schema(conn)
    counter = 0
    for row in dq.fetch_all_products(conn):
        _refresh_object_signals_into_table(conn, "products", "product", row["handle"])
        counter += 1
        if counter % batch_size == 0:
            conn.commit()
    for row in dq.fetch_all_collections(conn):
        _refresh_object_signals_into_table(conn, "collections", "collection", row["handle"])
        counter += 1
        if counter % batch_size == 0:
            conn.commit()
    for row in dq.fetch_all_pages(conn):
        _refresh_object_signals_into_table(conn, "pages", "page", row["handle"])
        counter += 1
        if counter % batch_size == 0:
            conn.commit()
    for row in dq.fetch_all_blog_articles(conn):
        ch = dq.blog_article_composite_handle(row["blog_handle"], row["handle"])
        _refresh_blog_article_signals_into_table(conn, ch)
        counter += 1
        if counter % batch_size == 0:
            conn.commit()
    conn.commit()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    ensure_dashboard_schema(conn)
    apply_runtime_settings(conn)
    return conn


def bootstrap_runtime_settings() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        ensure_dashboard_schema(conn)
        apply_runtime_settings(conn)
    finally:
        conn.close()
