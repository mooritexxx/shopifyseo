import json
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from . import dashboard_google as dg
from . import dashboard_queries as dq
from .dashboard_config import apply_runtime_settings
from .dashboard_status import index_status_info
from .gsc_query_limits import GSC_CATALOG_PERIOD_MODE, GSC_PER_URL_QUERY_ROW_LIMIT
from .sqlite_utf8 import configure_sqlite_text_decode
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


def _gsc_window_for_dimensional_fetch(gsc_detail: dict | None, gsc_period: str = GSC_CATALOG_PERIOD_MODE) -> tuple[date, date]:
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
    "pagespeed_desktop_performance": "INTEGER",
    "pagespeed_desktop_seo": "INTEGER",
    "pagespeed_desktop_status": "TEXT",
    "pagespeed_desktop_last_fetched_at": "INTEGER",
    "seo_signal_updated_at": "TEXT",
}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = _table_columns(conn, table)
    for name, col_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


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
            priority_score REAL NOT NULL DEFAULT 0.0,
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
            "priority_score": "REAL NOT NULL DEFAULT 0.0",
            "detected_entity": "TEXT NOT NULL DEFAULT ''",
            "cluster_intent": "TEXT NOT NULL DEFAULT ''",
            "cluster_role": "TEXT NOT NULL DEFAULT ''",
            "quality_score": "REAL NOT NULL DEFAULT 0.0",
            "core_keywords_json": "TEXT NOT NULL DEFAULT '[]'",
            "supporting_keywords_json": "TEXT NOT NULL DEFAULT '[]'",
            "extended_keywords_json": "TEXT NOT NULL DEFAULT '[]'",
            "cannibalization_risk": "TEXT NOT NULL DEFAULT 'none'",
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
    # Daily per-page Search Console history. The catalog columns hold only a current
    # snapshot, so trend ("is this page improving?") was unanswerable. Google retains
    # ~16 months, so this can be backfilled rather than accumulated.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gsc_page_daily (
          date TEXT NOT NULL,
          page_url TEXT NOT NULL,
          object_type TEXT NOT NULL DEFAULT '',
          object_handle TEXT NOT NULL DEFAULT '',
          clicks INTEGER NOT NULL DEFAULT 0,
          impressions INTEGER NOT NULL DEFAULT 0,
          ctr REAL NOT NULL DEFAULT 0,
          position REAL NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(date, page_url)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_gsc_page_daily_object
        ON gsc_page_daily(object_type, object_handle, date)
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
    # parent_topic: legacy column name; filled from DataForSEO keyword_properties.core_keyword in
    # backend keyword flows (see dataforseo_client). Not populated by Google Ads-only refresh.
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
            "ads_avg_monthly_searches": "INTEGER",
            "ads_competition": "TEXT",
            "ads_competition_index": "INTEGER",
        },
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
    # The keyword joins compare LOWER(keyword) on both sides, which no plain
    # index on `keyword` can serve. Expression indexes match those predicates
    # exactly, so results are unchanged while the planner stops doing a
    # three-way nested-loop scan. _fetch_competitor_gaps measured 4.4 s per
    # object before these and under 0.1 ms after.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword_metrics_keyword_lower"
        " ON keyword_metrics(LOWER(keyword))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword_page_map_keyword_lower"
        " ON keyword_page_map(LOWER(keyword))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_competitor_gaps_keyword_lower"
        " ON competitor_keyword_gaps(LOWER(keyword))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_keyword_page_map_object"
        " ON keyword_page_map(object_type, object_handle)"
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
    _ensure_columns(
        conn,
        "competitor_profiles",
        {
            "labs_visibility": "REAL DEFAULT 0",
            "labs_avg_position": "INTEGER DEFAULT 0",
            "labs_median_position": "INTEGER DEFAULT 0",
            "labs_seed_etv": "INTEGER DEFAULT 0",
            "labs_bulk_etv": "INTEGER DEFAULT 0",
            "labs_rating": "INTEGER DEFAULT 0",
            # Open PageRank domain authority. NULL = never fetched or the domain
            # is not in the index; 0.0 is a real score, so these must stay nullable.
            "authority_score": "REAL",
            "authority_rank": "INTEGER",
            "referring_domains": "INTEGER",
            "authority_updated_at": "INTEGER",
        },
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
    _ensure_columns(
        conn,
        "article_ideas",
        {
            "primary_target_type": "TEXT NOT NULL DEFAULT ''",
            "primary_target_handle": "TEXT NOT NULL DEFAULT ''",
            "primary_target_title": "TEXT NOT NULL DEFAULT ''",
            "primary_target_url": "TEXT NOT NULL DEFAULT ''",
            "secondary_targets_json": "TEXT NOT NULL DEFAULT '[]'",
            "audience_questions_json": "TEXT NOT NULL DEFAULT '[]'",
            "top_ranking_pages_json": "TEXT NOT NULL DEFAULT '[]'",
            "ai_overview_json": "TEXT NOT NULL DEFAULT '{}'",
            "related_searches_json": "TEXT NOT NULL DEFAULT '[]'",
            "paa_expansion_json": "TEXT NOT NULL DEFAULT '[]'",
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
        CREATE TABLE IF NOT EXISTS article_draft_runs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'running',
            current_step TEXT NOT NULL DEFAULT '',
            last_completed_step TEXT NOT NULL DEFAULT '',
            request_json TEXT NOT NULL DEFAULT '{}',
            seo_brief_json TEXT NOT NULL DEFAULT '{}',
            outline_json TEXT NOT NULL DEFAULT '{}',
            article_memory_json TEXT NOT NULL DEFAULT '{}',
            checkpoints_json TEXT NOT NULL DEFAULT '{}',
            title TEXT NOT NULL DEFAULT '',
            seo_title TEXT NOT NULL DEFAULT '',
            seo_description TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            image_payload_json TEXT NOT NULL DEFAULT '{}',
            shopify_article_id TEXT NOT NULL DEFAULT '',
            shopify_article_handle TEXT NOT NULL DEFAULT '',
            validation_summary_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
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


def _pagespeed_denormalized_fields(detail: dict | None) -> tuple[int | None, int | None, str, int | None]:
    """Lighthouse performance/SEO scores, fetch status label, and cache ``fetched_at`` for one strategy."""
    cats = (detail or {}).get("lighthouseResult", {}).get("categories", {}) or {}
    meta = (detail or {}).get("_cache") or {}
    return (
        _lighthouse_category_score_pct(cats, "performance"),
        _lighthouse_category_score_pct(cats, "seo"),
        _pagespeed_status(detail),
        meta.get("fetched_at"),
    )


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


GSC_TREND_WINDOW_DAYS = 30
GSC_TREND_SPARKLINE_POINTS = 30


def upsert_gsc_page_daily(
    conn: sqlite3.Connection,
    rows: list[dict],
    targets_by_url: dict[str, tuple[str, str]] | None = None,
    *,
    batch_size: int = 500,
) -> int:
    """Store daily per-page rows, tagging each with its catalog object when known."""
    by_url = targets_by_url or {}
    written = 0
    for i, row in enumerate(rows, 1):
        url = str(row.get("page_url") or "")
        day = str(row.get("date") or "")
        if not url or not day:
            continue
        kind, handle = by_url.get(url, ("", ""))
        conn.execute(
            """
            INSERT INTO gsc_page_daily(
              date, page_url, object_type, object_handle, clicks, impressions, ctr, position, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(date, page_url) DO UPDATE SET
              object_type = excluded.object_type,
              object_handle = excluded.object_handle,
              clicks = excluded.clicks,
              impressions = excluded.impressions,
              ctr = excluded.ctr,
              position = excluded.position,
              updated_at = CURRENT_TIMESTAMP
            """,
            (
                day,
                url,
                kind,
                handle,
                int(row.get("clicks") or 0),
                int(row.get("impressions") or 0),
                float(row.get("ctr") or 0.0),
                float(row.get("position") or 0.0),
            ),
        )
        written += 1
        if i % batch_size == 0:
            conn.commit()
    conn.commit()
    return written


def _pct_change(current: float, previous: float) -> float | None:
    """Percent change, or None when there is no baseline to compare against."""
    if previous <= 0:
        return None if current <= 0 else 100.0
    return round(((current - previous) / previous) * 100.0, 1)


def gsc_page_trend_map(
    conn: sqlite3.Connection,
    *,
    window_days: int = GSC_TREND_WINDOW_DAYS,
    today: date | None = None,
    keys: Sequence[tuple[str, str]] | None = None,
) -> dict[tuple[str, str], dict]:
    """Per catalog object: current vs prior window totals, plus a daily click series.

    Keyed by ``(object_type, object_handle)``. Objects with no history are absent, so
    callers should treat a miss as "no trend yet" rather than zero.

    ``keys`` restricts the aggregate to specific objects. Detail views want a
    single object and would otherwise aggregate the whole table (16k+ rows) to
    read one entry. Passing an empty sequence returns ``{}``.
    """
    anchor = today or date.today()
    cur_start = anchor - timedelta(days=window_days - 1)
    prev_start = cur_start - timedelta(days=window_days)

    key_clause = ""
    key_params: tuple[str, ...] = ()
    if keys is not None:
        if not keys:
            return {}
        uniq = list(dict.fromkeys(keys))
        key_clause = " AND (%s)" % " OR ".join(
            "(object_type = ? AND object_handle = ?)" for _ in uniq
        )
        key_params = tuple(part for pair in uniq for part in pair)

    out: dict[tuple[str, str], dict] = {}
    rows = conn.execute(
        f"""
        SELECT object_type, object_handle,
               SUM(CASE WHEN date >= ? THEN clicks ELSE 0 END)      AS cur_clicks,
               SUM(CASE WHEN date >= ? THEN impressions ELSE 0 END) AS cur_impr,
               SUM(CASE WHEN date <  ? THEN clicks ELSE 0 END)      AS prev_clicks,
               SUM(CASE WHEN date <  ? THEN impressions ELSE 0 END) AS prev_impr
        FROM gsc_page_daily
        WHERE object_type != '' AND object_handle != ''
          AND date >= ? AND date <= ?{key_clause}
        GROUP BY object_type, object_handle
        """,
        (
            cur_start.isoformat(),
            cur_start.isoformat(),
            cur_start.isoformat(),
            cur_start.isoformat(),
            prev_start.isoformat(),
            anchor.isoformat(),
            *key_params,
        ),
    ).fetchall()

    for r in rows:
        key = (r["object_type"], r["object_handle"])
        cur_clicks = int(r["cur_clicks"] or 0)
        prev_clicks = int(r["prev_clicks"] or 0)
        cur_impr = int(r["cur_impr"] or 0)
        prev_impr = int(r["prev_impr"] or 0)
        out[key] = {
            "clicks_current": cur_clicks,
            "clicks_previous": prev_clicks,
            "clicks_delta_pct": _pct_change(cur_clicks, prev_clicks),
            "impressions_current": cur_impr,
            "impressions_previous": prev_impr,
            "impressions_delta_pct": _pct_change(cur_impr, prev_impr),
            "series": [],
        }

    # Daily click series for the sparkline, zero-filled so gaps read as flat, not missing.
    spark_start = anchor - timedelta(days=GSC_TREND_SPARKLINE_POINTS - 1)
    series_rows = conn.execute(
        f"""
        SELECT object_type, object_handle, date, SUM(clicks) AS clicks
        FROM gsc_page_daily
        WHERE object_type != '' AND object_handle != ''
          AND date >= ? AND date <= ?{key_clause}
        GROUP BY object_type, object_handle, date
        """,
        (spark_start.isoformat(), anchor.isoformat(), *key_params),
    ).fetchall()

    by_day: dict[tuple[str, str], dict[str, int]] = {}
    for r in series_rows:
        by_day.setdefault((r["object_type"], r["object_handle"]), {})[r["date"]] = int(r["clicks"] or 0)

    days = [(spark_start + timedelta(days=i)).isoformat() for i in range(GSC_TREND_SPARKLINE_POINTS)]
    for key, entry in out.items():
        daily = by_day.get(key, {})
        entry["series"] = [daily.get(d, 0) for d in days]
    return out


# Denormalized signal columns, in the order every signal UPDATE writes them.
_SIGNAL_COLUMNS = (
    "gsc_clicks",
    "gsc_impressions",
    "gsc_ctr",
    "gsc_position",
    "gsc_last_fetched_at",
    "ga4_sessions",
    "ga4_views",
    "ga4_avg_session_duration",
    "ga4_last_fetched_at",
    "index_status",
    "index_coverage",
    "google_canonical",
    "index_last_fetched_at",
)


def _signal_values_preserving_known(
    conn: sqlite3.Connection,
    table: str,
    where_sql: str,
    where_params: tuple,
    *,
    gsc_row: dict | None,
    gsc_fetched_at: object,
    ga4_sessions: int | None,
    ga4_views: int | None,
    ga4_avg_dur: float | None,
    ga4_fetched_at: object,
    idx: dict,
    index_label: object,
    index_fetched_at: object,
) -> tuple:
    """Build the signal-column values, keeping stored data for any signal with nothing fresh.

    "No rows in the cached payload" means *we have nothing to say right now*, not *this
    object scored zero* — the GSC MTD window is legitimately empty for the first days of
    every month, and a failed or partial scope leaves objects without a payload. Writing
    NULL in those cases destroys the only stored copy of the number.

    Each signal is preserved as a group (values **and** their ``*_last_fetched_at``) so a
    preserved value never appears freshly fetched. A payload that genuinely reports zero
    still overwrites, because that is real data.
    """
    has_gsc = gsc_row is not None
    has_ga4 = ga4_sessions is not None or ga4_views is not None or ga4_avg_dur is not None
    has_index = bool(idx.get("indexingState") or idx.get("coverageState"))

    fresh = (
        int(gsc_row.get("clicks", 0)) if gsc_row else None,
        int(gsc_row.get("impressions", 0)) if gsc_row else None,
        float(gsc_row.get("ctr", 0)) if gsc_row else None,
        float(gsc_row.get("position", 0)) if gsc_row else None,
        gsc_fetched_at,
        ga4_sessions,
        ga4_views,
        ga4_avg_dur,
        ga4_fetched_at,
        index_label,
        idx.get("coverageState"),
        idx.get("googleCanonical"),
        index_fetched_at,
    )
    if has_gsc and has_ga4 and has_index:
        return fresh

    row = conn.execute(
        f"SELECT {', '.join(_SIGNAL_COLUMNS)} FROM {table} WHERE {where_sql}",
        where_params,
    ).fetchone()
    if row is None:
        return fresh

    # Positional access: callers may or may not set row_factory = sqlite3.Row.
    stored = tuple(row)
    merged = list(fresh)
    if not has_gsc:
        merged[0:5] = stored[0:5]
    if not has_ga4:
        merged[5:9] = stored[5:9]
    if not has_index:
        merged[9:13] = stored[9:13]
    return tuple(merged)


def _refresh_object_signals_into_table(
    conn: sqlite3.Connection,
    table: str,
    object_type: str,
    handle: str,
    *,
    include_query_dimensions: bool = False,
) -> None:
    """GSC, GA4, and URL inspection only.

    PageSpeed denormalized columns are updated only from PageSpeed sync paths
    (``_refresh_object_pagespeed_into_table`` and
    ``refresh_pagespeed_columns_from_cache_for_all_cached_objects``) so a missing
    ``google_api_cache`` entry cannot wipe prior scores.
    """
    url = dq.object_url(object_type, handle)
    gsc_detail = dg.get_search_console_url_detail(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    ga4_sessions, ga4_views, ga4_avg_dur, ga4_fetched_at = _resolve_ga4_metrics_for_url(
        conn, url, object_type, handle, ga4_refresh=False
    )

    gsc_row = (gsc_detail.get("page_rows") or [None])[0] if gsc_detail else None
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    inspection_meta = (inspection_detail or {}).get("_cache") or {}
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    index_label, _, _ = index_status_info(inspection_detail)

    values = _signal_values_preserving_known(
        conn,
        table,
        "handle = ?",
        (handle,),
        gsc_row=gsc_row,
        gsc_fetched_at=gsc_meta.get("fetched_at"),
        ga4_sessions=ga4_sessions,
        ga4_views=ga4_views,
        ga4_avg_dur=ga4_avg_dur,
        ga4_fetched_at=ga4_fetched_at,
        idx=idx,
        index_label=index_label,
        index_fetched_at=inspection_meta.get("fetched_at"),
    )

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
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (*values, handle),
    )

    _write_gsc_per_url_query_caches(
        conn,
        object_type,
        handle,
        url,
        gsc_detail,
        include_query_dimensions=include_query_dimensions,
    )


def _write_gsc_per_url_query_caches(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    url: str,
    gsc_detail: dict | None,
    *,
    include_query_dimensions: bool = False,
) -> None:
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    # exists=False is _cache_meta(None): we looked and found no cache row. That means "we
    # have nothing to say about this object", not "this object has no queries", so
    # rewriting from the empty payload would silently wipe rows a previous sync stored.
    # Expired-but-present payloads are still applied — they carry the last known data, and
    # _load_cached_payload returns them regardless of expiry.
    if gsc_meta.get("exists") is False:
        return

    conn.execute(
        "DELETE FROM gsc_query_rows WHERE object_type = ? AND object_handle = ?",
        (object_type, handle),
    )
    # The cached payload deliberately keeps a superset (see GSC_BULK_QUERY_ROWS_PER_PAGE)
    # so richer history survives, but only the rows readers actually consume are
    # materialised here — every consumer bounds its query by GSC_PER_URL_QUERY_ROW_LIMIT.
    query_rows = (gsc_detail or {}).get("query_rows", [])[:GSC_PER_URL_QUERY_ROW_LIMIT]
    for row in query_rows:
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

    if include_query_dimensions:
        _refresh_gsc_query_dimensions_into_table(
            conn,
            object_type,
            handle,
            url,
            fetched_at=gsc_meta.get("fetched_at"),
            gsc_detail=gsc_detail,
            gsc_period=(gsc_detail or {}).get("period_mode") or GSC_CATALOG_PERIOD_MODE,
        )


def _refresh_object_pagespeed_into_table(conn: sqlite3.Connection, table: str, object_type: str, handle: str) -> None:
    url = dq.object_url(object_type, handle)
    dg.invalidate_pagespeed_memory_cache(url)
    mobile = dg.get_pagespeed(conn, url, "mobile", refresh=False, object_type=object_type, object_handle=handle)
    desktop = dg.get_pagespeed(conn, url, "desktop", refresh=False, object_type=object_type, object_handle=handle)
    m_perf, m_seo, m_stat, m_ts = _pagespeed_denormalized_fields(mobile)
    d_perf, d_seo, d_stat, d_ts = _pagespeed_denormalized_fields(desktop)
    conn.execute(
        f"""
        UPDATE {table}
        SET pagespeed_performance = ?,
            pagespeed_seo = ?,
            pagespeed_status = ?,
            pagespeed_last_fetched_at = ?,
            pagespeed_desktop_performance = ?,
            pagespeed_desktop_seo = ?,
            pagespeed_desktop_status = ?,
            pagespeed_desktop_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE handle = ?
        """,
        (
            m_perf,
            m_seo,
            m_stat,
            m_ts,
            d_perf,
            d_seo,
            d_stat,
            d_ts,
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
    mobile = dg.get_pagespeed(conn, url, "mobile", refresh=False, object_type=object_type, object_handle=handle)
    desktop = dg.get_pagespeed(conn, url, "desktop", refresh=False, object_type=object_type, object_handle=handle)
    m_perf, m_seo, m_stat, m_ts = _pagespeed_denormalized_fields(mobile)
    d_perf, d_seo, d_stat, d_ts = _pagespeed_denormalized_fields(desktop)
    conn.execute(
        """
        UPDATE blog_articles
        SET pagespeed_performance = ?,
            pagespeed_seo = ?,
            pagespeed_status = ?,
            pagespeed_last_fetched_at = ?,
            pagespeed_desktop_performance = ?,
            pagespeed_desktop_seo = ?,
            pagespeed_desktop_status = ?,
            pagespeed_desktop_last_fetched_at = ?,
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (
            m_perf,
            m_seo,
            m_stat,
            m_ts,
            d_perf,
            d_seo,
            d_stat,
            d_ts,
            blog_h,
            art_h,
        ),
    )


def _refresh_gsc_query_dimensions_into_table(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    page_url: str,
    *,
    fetched_at: int | None,
    gsc_detail: dict | None = None,
    gsc_period: str = GSC_CATALOG_PERIOD_MODE,
) -> None:
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
    _write_gsc_per_url_query_caches(
        conn,
        object_type,
        handle,
        url,
        gsc_detail,
        include_query_dimensions=True,
    )


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


def _refresh_blog_article_signals_into_table(
    conn: sqlite3.Connection,
    composite_handle: str,
    *,
    include_query_dimensions: bool = False,
) -> None:
    """Same scope as :func:`_refresh_object_signals_into_table` (excludes PageSpeed)."""
    parts = _parse_blog_article_parts(composite_handle)
    if not parts:
        return
    blog_h, art_h = parts
    object_type = "blog_article"
    handle = composite_handle
    url = dq.object_url(object_type, handle)
    gsc_detail = dg.get_search_console_url_detail(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    inspection_detail = dg.get_url_inspection(conn, url, refresh=False, object_type=object_type, object_handle=handle)
    ga4_sessions, ga4_views, ga4_avg_dur, ga4_fetched_at = _resolve_ga4_metrics_for_url(
        conn, url, object_type, handle, ga4_refresh=False
    )

    gsc_row = (gsc_detail.get("page_rows") or [None])[0] if gsc_detail else None
    gsc_meta = (gsc_detail or {}).get("_cache") or {}
    inspection_meta = (inspection_detail or {}).get("_cache") or {}
    idx = (inspection_detail or {}).get("inspectionResult", {}).get("indexStatusResult", {}) or {}
    index_label, _, _ = index_status_info(inspection_detail)

    values = _signal_values_preserving_known(
        conn,
        "blog_articles",
        "blog_handle = ? AND handle = ?",
        (blog_h, art_h),
        gsc_row=gsc_row,
        gsc_fetched_at=gsc_meta.get("fetched_at"),
        ga4_sessions=ga4_sessions,
        ga4_views=ga4_views,
        ga4_avg_dur=ga4_avg_dur,
        ga4_fetched_at=ga4_fetched_at,
        idx=idx,
        index_label=index_label,
        index_fetched_at=inspection_meta.get("fetched_at"),
    )

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
            seo_signal_updated_at = CURRENT_TIMESTAMP
        WHERE blog_handle = ? AND handle = ?
        """,
        (*values, blog_h, art_h),
    )
    _write_gsc_per_url_query_caches(
        conn,
        object_type,
        handle,
        url,
        gsc_detail,
        include_query_dimensions=include_query_dimensions,
    )


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
    _write_gsc_per_url_query_caches(
        conn,
        object_type,
        handle,
        url,
        gsc_detail,
        include_query_dimensions=True,
    )


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


def refresh_gsc_signal_data_for_objects(
    conn: sqlite3.Connection,
    targets: list[tuple[str, str]],
    *,
    batch_size: int = 10,
    sync_query_embeddings: bool = True,
) -> None:
    ensure_dashboard_schema(conn)
    for i, (object_type, handle) in enumerate(targets, 1):
        if object_type == "blog_article":
            _refresh_object_gsc_into_blog_article(conn, handle)
        else:
            _refresh_object_gsc_into_table(conn, _table_for_object_type(object_type), object_type, handle)
        if i % batch_size == 0:
            conn.commit()
    conn.commit()
    if not sync_query_embeddings:
        return
    try:
        from .embedding_store import sync_embeddings
        sync_embeddings(conn, object_type="gsc_queries")
    except Exception:
        _LOG.warning("GSC embedding sync failed (non-fatal)", exc_info=True)


def _json_for_article_draft_run(value: object) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=True)


def _decode_article_draft_run_json(raw: object, fallback: object) -> object:
    if raw in (None, ""):
        return fallback
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return fallback


def article_draft_run_to_dict(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    out = dict(row)
    for key in (
        "request_json",
        "seo_brief_json",
        "outline_json",
        "article_memory_json",
        "checkpoints_json",
        "image_payload_json",
        "validation_summary_json",
    ):
        public_key = key[:-5] if key.endswith("_json") else key
        out[public_key] = _decode_article_draft_run_json(out.get(key), {})
    return out


def create_article_draft_run(conn: sqlite3.Connection, request_payload: dict) -> str:
    ensure_dashboard_schema(conn)
    run_id = uuid.uuid4().hex
    now_ts = int(time.time())
    conn.execute(
        """
        INSERT INTO article_draft_runs(
            id, status, request_json, created_at, updated_at
        ) VALUES(?, 'running', ?, ?, ?)
        """,
        (run_id, _json_for_article_draft_run(request_payload), now_ts, now_ts),
    )
    conn.commit()
    return run_id


def get_article_draft_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    ensure_dashboard_schema(conn)
    row = conn.execute("SELECT * FROM article_draft_runs WHERE id = ?", (run_id,)).fetchone()
    return article_draft_run_to_dict(row)


def update_article_draft_run(conn: sqlite3.Connection, run_id: str, **fields: object) -> None:
    if not run_id:
        return
    ensure_dashboard_schema(conn)
    allowed = {
        "status",
        "current_step",
        "last_completed_step",
        "request_json",
        "seo_brief_json",
        "outline_json",
        "article_memory_json",
        "checkpoints_json",
        "title",
        "seo_title",
        "seo_description",
        "body",
        "image_payload_json",
        "shopify_article_id",
        "shopify_article_handle",
        "validation_summary_json",
        "error_message",
    }
    assignments: list[str] = []
    values: list[object] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        if key.endswith("_json"):
            values.append(_json_for_article_draft_run(value))
        else:
            values.append("" if value is None else value)
    if not assignments:
        return
    assignments.append("updated_at = ?")
    values.append(int(time.time()))
    values.append(run_id)
    conn.execute(
        f"UPDATE article_draft_runs SET {', '.join(assignments)} WHERE id = ?",
        values,
    )
    conn.commit()


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
          AND (strategy IS NULL OR TRIM(strategy) = '' OR strategy IN ('mobile', 'desktop'))
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


def refresh_structured_seo_data(
    conn: sqlite3.Connection,
    *,
    batch_size: int = 10,
    progress_callback: Callable[[str, int, int], None] | None = None,
    include_query_dimensions: bool = False,
) -> None:
    """Denormalize GSC / GA4 / URL inspection from the local API cache onto all catalog rows.

    PageSpeed columns are *not* updated here; use the PageSpeed sync path or
    ``refresh_pagespeed_columns_from_cache_for_all_cached_objects``.

    (Name kept for callers; this is catalog *signal* reconciliation, not JSON-LD.)
    """
    ensure_dashboard_schema(conn)
    products = dq.fetch_all_products(conn)
    collections = dq.fetch_all_collections(conn)
    pages = dq.fetch_all_pages(conn)
    articles = dq.fetch_all_blog_articles(conn)
    total = len(products) + len(collections) + len(pages) + len(articles)
    counter = 0
    if progress_callback:
        progress_callback("products", counter, total)
    for row in products:
        _refresh_object_signals_into_table(
            conn,
            "products",
            "product",
            row["handle"],
            include_query_dimensions=include_query_dimensions,
        )
        counter += 1
        if progress_callback:
            progress_callback("products", counter, total)
        if counter % batch_size == 0:
            conn.commit()
    for row in collections:
        _refresh_object_signals_into_table(
            conn,
            "collections",
            "collection",
            row["handle"],
            include_query_dimensions=include_query_dimensions,
        )
        counter += 1
        if progress_callback:
            progress_callback("collections", counter, total)
        if counter % batch_size == 0:
            conn.commit()
    for row in pages:
        _refresh_object_signals_into_table(
            conn,
            "pages",
            "page",
            row["handle"],
            include_query_dimensions=include_query_dimensions,
        )
        counter += 1
        if progress_callback:
            progress_callback("pages", counter, total)
        if counter % batch_size == 0:
            conn.commit()
    for row in articles:
        ch = dq.blog_article_composite_handle(row["blog_handle"], row["handle"])
        _refresh_blog_article_signals_into_table(
            conn,
            ch,
            include_query_dimensions=include_query_dimensions,
        )
        counter += 1
        if progress_callback:
            progress_callback("articles", counter, total)
        if counter % batch_size == 0:
            conn.commit()
    conn.commit()


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    configure_sqlite_text_decode(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    ensure_dashboard_schema(conn)
    apply_runtime_settings(conn)
    return conn


def bootstrap_runtime_settings() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    configure_sqlite_text_decode(conn)
    try:
        ensure_dashboard_schema(conn)
        apply_runtime_settings(conn)
    finally:
        conn.close()
