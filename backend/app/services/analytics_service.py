"""Analytics service for GSC-derived insights.

Provides:
  - Query × page cannibalization detection (queries ranking 2+ URLs)
  - Decay watch-list (pages with declining GSC metrics over trailing 28d vs prior 28d)
  - Organic funnel flags (high sessions with zero carts, checkout assists)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from shopifyseo.db import DictRow

logger = logging.getLogger(__name__)


def _mapping_row_factory(cursor, row):
    """sqlite3 row_factory that produces DictRow (compatible with sqlite3.Row)."""
    return DictRow.from_values(tuple(col[0] for col in cursor.description), tuple(row))


def _ensure_row_factory(conn: Any) -> Any:
    """Ensure key-accessible rows without overwriting existing factory."""
    if getattr(conn, "row_factory", None) is not None:
        return conn
    conn.row_factory = _mapping_row_factory
    return conn


# -- Query × page cannibalization (Feature 3) ---------------------------------
#
# A query "cannibalizes" when 2+ of our URLs rank for it with meaningful
# impressions. This splits click potential and confuses Google about intent.

CANNIBALIZATION_MIN_IMPRESSIONS = 20


def fetch_cannibalization_queries(
    conn: Any,
    *,
    min_impressions: int = CANNIBALIZATION_MIN_IMPRESSIONS,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Find queries where 2+ of our URLs rank with meaningful impressions.

    Returns dict with:
      - queries: list of {query, urls: [{url, object_type, object_handle, clicks, impressions, ctr, position}]}
      - total: total number of cannibalized queries
    """
    _ensure_row_factory(conn)

    # Find queries appearing for multiple URLs with significant impressions
    count_row = conn.execute(
        """
        SELECT COUNT(DISTINCT query) as cnt
        FROM gsc_query_rows
        WHERE impressions >= ?
        AND query IN (
            SELECT query FROM gsc_query_rows
            WHERE impressions >= ?
            GROUP BY query
            HAVING COUNT(DISTINCT url) >= 2
        )
        """,
        (min_impressions, min_impressions),
    ).fetchone()
    total = count_row["cnt"] if count_row else 0

    rows = conn.execute(
        """
        SELECT query, url, object_type, object_handle, clicks, impressions, ctr, position
        FROM gsc_query_rows
        WHERE impressions >= ?
        AND query IN (
            SELECT query FROM gsc_query_rows
            WHERE impressions >= ?
            GROUP BY query
            HAVING COUNT(DISTINCT url) >= 2
        )
        ORDER BY query, impressions DESC
        """,
        (min_impressions, min_impressions),
    ).fetchall()

    # Group by query
    queries_map: dict[str, dict] = {}
    for row in rows:
        q = row["query"]
        if q not in queries_map:
            queries_map[q] = {
                "query": q,
                "urls": [],
                "total_impressions": 0,
                "total_clicks": 0,
            }
        queries_map[q]["urls"].append({
            "url": row["url"],
            "object_type": row["object_type"],
            "object_handle": row["object_handle"],
            "clicks": int(row["clicks"] or 0),
            "impressions": int(row["impressions"] or 0),
            "ctr": round(float(row["ctr"] or 0) * 100, 2),
            "position": round(float(row["position"] or 0), 1),
        })
        queries_map[q]["total_impressions"] += int(row["impressions"] or 0)
        queries_map[q]["total_clicks"] += int(row["clicks"] or 0)

    # Sort by total impressions and paginate
    sorted_queries = sorted(
        queries_map.values(),
        key=lambda x: (-x["total_impressions"], x["query"]),
    )
    paginated = sorted_queries[offset:offset + limit]

    return {
        "queries": paginated,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(sorted_queries),
    }


# -- Decay watch-list (Feature 4) ---------------------------------------------
#
# Pages whose last-28d clicks/impressions dropped materially vs prior 28d.
# This surfaces potential issues before they become severe traffic losses.

DECAY_CLICKS_DROP_THRESHOLD = 0.20  # 20% drop
DECAY_IMPRESSIONS_DROP_THRESHOLD = 0.25  # 25% drop
DECAY_MIN_PRIOR_IMPRESSIONS = 100  # Need baseline volume to compare


def _gsc_daily_window_dates() -> tuple[date, date, date, date]:
    """Calculate current and prior 28-day windows for decay analysis."""
    today = date.today()
    # Current window: last 28 days (excluding today, 3-day reporting lag)
    current_end = today - timedelta(days=3)
    current_start = current_end - timedelta(days=27)
    # Prior window: 28 days before current window
    prior_end = current_start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=27)
    return current_start, current_end, prior_start, prior_end


def fetch_decay_watchlist(
    conn: Any,
    *,
    clicks_threshold: float = DECAY_CLICKS_DROP_THRESHOLD,
    impressions_threshold: float = DECAY_IMPRESSIONS_DROP_THRESHOLD,
    min_prior_impressions: int = DECAY_MIN_PRIOR_IMPRESSIONS,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Find pages with declining GSC metrics (clicks/impressions down vs prior 28d).

    Returns dict with:
      - pages: list of page decay info with before/after metrics
      - total: total pages in decay
    """
    _ensure_row_factory(conn)

    current_start, current_end, prior_start, prior_end = _gsc_daily_window_dates()

    # Aggregate metrics for both windows
    rows = conn.execute(
        """
        WITH current_window AS (
            SELECT
                page_url,
                object_type,
                object_handle,
                SUM(clicks) as clicks,
                SUM(impressions) as impressions,
                CASE WHEN SUM(impressions) > 0 
                     THEN CAST(SUM(clicks) AS REAL) / SUM(impressions) 
                     ELSE 0 END as ctr,
                CASE WHEN SUM(impressions) > 0 
                     THEN SUM(position * impressions) / SUM(impressions) 
                     ELSE 0 END as avg_position
            FROM gsc_page_daily
            WHERE date BETWEEN ? AND ?
            GROUP BY page_url
        ),
        prior_window AS (
            SELECT
                page_url,
                SUM(clicks) as clicks,
                SUM(impressions) as impressions,
                CASE WHEN SUM(impressions) > 0 
                     THEN CAST(SUM(clicks) AS REAL) / SUM(impressions) 
                     ELSE 0 END as ctr,
                CASE WHEN SUM(impressions) > 0 
                     THEN SUM(position * impressions) / SUM(impressions) 
                     ELSE 0 END as avg_position
            FROM gsc_page_daily
            WHERE date BETWEEN ? AND ?
            GROUP BY page_url
        )
        SELECT
            c.page_url,
            c.object_type,
            c.object_handle,
            c.clicks as current_clicks,
            c.impressions as current_impressions,
            c.ctr as current_ctr,
            c.avg_position as current_position,
            p.clicks as prior_clicks,
            p.impressions as prior_impressions,
            p.ctr as prior_ctr,
            p.avg_position as prior_position,
            CASE WHEN p.clicks > 0 
                 THEN (CAST(c.clicks AS REAL) - p.clicks) / p.clicks 
                 ELSE 0 END as clicks_change_pct,
            CASE WHEN p.impressions > 0 
                 THEN (CAST(c.impressions AS REAL) - p.impressions) / p.impressions 
                 ELSE 0 END as impressions_change_pct
        FROM current_window c
        INNER JOIN prior_window p ON c.page_url = p.page_url
        WHERE p.impressions >= ?
        AND (
            (p.clicks > 0 AND (CAST(c.clicks AS REAL) - p.clicks) / p.clicks <= -?)
            OR (CAST(c.impressions AS REAL) - p.impressions) / p.impressions <= -?
        )
        ORDER BY p.impressions DESC
        """,
        (
            current_start.isoformat(),
            current_end.isoformat(),
            prior_start.isoformat(),
            prior_end.isoformat(),
            min_prior_impressions,
            clicks_threshold,
            impressions_threshold,
        ),
    ).fetchall()

    pages = []
    for row in rows:
        clicks_pct = float(row["clicks_change_pct"] or 0) * 100
        impressions_pct = float(row["impressions_change_pct"] or 0) * 100

        decay_reasons = []
        if clicks_pct <= -clicks_threshold * 100:
            decay_reasons.append(f"clicks down {abs(clicks_pct):.0f}%")
        if impressions_pct <= -impressions_threshold * 100:
            decay_reasons.append(f"impressions down {abs(impressions_pct):.0f}%")

        pages.append({
            "page_url": row["page_url"],
            "object_type": row["object_type"] or "",
            "object_handle": row["object_handle"] or "",
            "current": {
                "clicks": int(row["current_clicks"] or 0),
                "impressions": int(row["current_impressions"] or 0),
                "ctr": round(float(row["current_ctr"] or 0) * 100, 2),
                "position": round(float(row["current_position"] or 0), 1),
            },
            "prior": {
                "clicks": int(row["prior_clicks"] or 0),
                "impressions": int(row["prior_impressions"] or 0),
                "ctr": round(float(row["prior_ctr"] or 0) * 100, 2),
                "position": round(float(row["prior_position"] or 0), 1),
            },
            "changes": {
                "clicks_pct": round(clicks_pct, 1),
                "impressions_pct": round(impressions_pct, 1),
            },
            "decay_reasons": decay_reasons,
            "window": {
                "current_start": current_start.isoformat(),
                "current_end": current_end.isoformat(),
                "prior_start": prior_start.isoformat(),
                "prior_end": prior_end.isoformat(),
            },
        })

    total = len(pages)
    paginated = pages[offset:offset + limit]

    return {
        "pages": paginated,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


# -- Organic funnel flags (Feature 1 UI signals) ------------------------------
#
# Surface signals on catalog rows: "high organic sessions, zero carts" and
# "assists checkout/purchase".

HIGH_SESSIONS_THRESHOLD = 10
ASSIST_MIN_EVENTS = 1


def fetch_organic_funnel_flags(
    conn: Any,
    *,
    window_days: int = 28,
    high_sessions_threshold: int = HIGH_SESSIONS_THRESHOLD,
    limit: int = 200,
) -> dict[str, Any]:
    """Get organic funnel flags for catalog pages.

    Returns dict with:
      - high_sessions_no_carts: pages with organic sessions but zero add_to_carts
      - checkout_assists: pages that assisted checkout or purchase events
    """
    _ensure_row_factory(conn)

    # High sessions with zero carts
    high_sessions_no_carts = conn.execute(
        """
        SELECT page_url, object_type, object_handle, sessions, add_to_carts, checkouts, purchases
        FROM ga4_organic_funnel
        WHERE window_days = ?
        AND sessions >= ?
        AND add_to_carts = 0
        ORDER BY sessions DESC
        LIMIT ?
        """,
        (window_days, high_sessions_threshold, limit),
    ).fetchall()

    # Checkout/purchase assists (has funnel events)
    checkout_assists = conn.execute(
        """
        SELECT page_url, object_type, object_handle, sessions, add_to_carts, checkouts, purchases, revenue
        FROM ga4_organic_funnel
        WHERE window_days = ?
        AND (checkouts >= ? OR purchases >= ?)
        ORDER BY purchases DESC, checkouts DESC, sessions DESC
        LIMIT ?
        """,
        (window_days, ASSIST_MIN_EVENTS, ASSIST_MIN_EVENTS, limit),
    ).fetchall()

    return {
        "high_sessions_no_carts": [
            {
                "page_url": row["page_url"],
                "object_type": row["object_type"] or "",
                "object_handle": row["object_handle"] or "",
                "sessions": int(row["sessions"] or 0),
                "flag": "high_organic_sessions_zero_carts",
            }
            for row in high_sessions_no_carts
        ],
        "checkout_assists": [
            {
                "page_url": row["page_url"],
                "object_type": row["object_type"] or "",
                "object_handle": row["object_handle"] or "",
                "sessions": int(row["sessions"] or 0),
                "add_to_carts": int(row["add_to_carts"] or 0),
                "checkouts": int(row["checkouts"] or 0),
                "purchases": int(row["purchases"] or 0),
                "revenue": float(row["revenue"] or 0),
                "flag": "assists_checkout_purchase",
            }
            for row in checkout_assists
        ],
        "window_days": window_days,
    }
