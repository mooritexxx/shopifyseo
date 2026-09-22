"""GSC Opportunity Inbox service.

Scores GSC query×page combinations into SEO opportunities. The scoring heuristic
favors striking-distance positions (4-20), high impressions, and CTR below the
expected curve for position.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# Expected CTR by position (based on industry benchmarks)
EXPECTED_CTR_BY_POSITION = {
    1: 0.28,
    2: 0.15,
    3: 0.11,
    4: 0.08,
    5: 0.07,
    6: 0.05,
    7: 0.04,
    8: 0.03,
    9: 0.03,
    10: 0.02,
}

# Page type mapping from object_type
PAGE_TYPE_MAP = {
    "product": "Product",
    "collection": "Collection",
    "page": "Page",
    "blog_article": "Blog Article",
}

# Content type hints based on query patterns
CONTENT_TYPE_PATTERNS = [
    (["how to", "guide", "tutorial", "what is", "why"], "Blog / Guide"),
    (["best", "top", "vs", "comparison", "review"], "Comparison / Buying guide"),
    (["buy", "shop", "price", "deal", "discount", "sale"], "Product / Collection page"),
    (["near me", "location", "store"], "Local landing page"),
]


def _expected_ctr(position: float) -> float:
    """Estimate expected CTR for a given position."""
    if position <= 0:
        return 0.01
    if position <= 10:
        idx = max(1, min(10, int(round(position))))
        return EXPECTED_CTR_BY_POSITION.get(idx, 0.02)
    if position <= 20:
        return 0.015 - (position - 10) * 0.001
    return 0.005


def _ctr_gap_score(actual_ctr: float, position: float) -> float:
    """Score based on CTR gap (actual vs expected).
    
    Positive gap = under-performing → high opportunity.
    Returns 0-100 where higher = more opportunity.
    """
    expected = _expected_ctr(position)
    if expected <= 0:
        return 50.0
    gap = expected - actual_ctr
    gap_pct = gap / expected
    score = 50.0 + min(50.0, max(-50.0, gap_pct * 100.0))
    return score


def _position_opportunity_score(position: float) -> float:
    """Score based on position — striking distance is best.
    
    Positions 4-20 are "striking distance" with highest scores.
    Already ranking #1-3 = lower opportunity (already winning).
    >20 = harder to move up, lower score.
    """
    if position <= 0:
        return 30.0
    if position <= 3:
        return 40.0 + (3 - position) * 5
    if position <= 10:
        return 85.0 + (10 - position) * 1.5
    if position <= 20:
        return 70.0 + (20 - position) * 1.0
    if position <= 50:
        return 50.0 - (position - 20) * 0.5
    return 20.0


def _impressions_score(impressions: int) -> float:
    """Log-scaled impressions score (0-100)."""
    if impressions <= 0:
        return 0.0
    return min(100.0, math.log1p(impressions) / math.log1p(10000) * 100.0)


def _suggest_action(position: float, ctr: float, impressions: int) -> str:
    """Suggest an action based on opportunity metrics."""
    expected_ctr = _expected_ctr(position)
    
    if position <= 3:
        if ctr < expected_ctr * 0.7:
            return "Improve title/meta for CTR"
        return "Maintain position, expand content"
    
    if position <= 10:
        if ctr < expected_ctr * 0.7:
            return "Improve title/meta + add content"
        return "Add depth to reach top 3"
    
    if position <= 20:
        if impressions > 100:
            return "High-value quick win: optimize content"
        return "Quick win: add relevant content"
    
    if position <= 50:
        return "Build authority with comprehensive content"
    
    return "Create dedicated content for this query"


def _infer_content_type(query: str) -> str:
    """Infer content type hint from query patterns."""
    q_lower = query.lower()
    for patterns, content_type in CONTENT_TYPE_PATTERNS:
        if any(p in q_lower for p in patterns):
            return content_type
    return "Blog / Guide"


def compute_opportunity_score(
    position: float,
    impressions: int,
    clicks: int,
    ctr: float | None = None,
) -> float:
    """Compute overall opportunity score (0-100).
    
    Weighting:
    - Position opportunity: 40%
    - Impressions: 30%
    - CTR gap: 30%
    """
    if ctr is None:
        ctr = clicks / impressions if impressions > 0 else 0.0
    
    pos_score = _position_opportunity_score(position)
    imp_score = _impressions_score(impressions)
    ctr_score = _ctr_gap_score(ctr, position)
    
    return round(
        pos_score * 0.40 + imp_score * 0.30 + ctr_score * 0.30,
        2
    )


def fetch_opportunities(
    conn: sqlite3.Connection,
    *,
    page_type: str | None = None,
    min_impressions: int = 10,
    min_position: float = 1.0,
    max_position: float = 50.0,
    limit: int = 100,
    offset: int = 0,
    sort_by: str = "opportunity_score",
    sort_dir: str = "desc",
) -> dict[str, Any]:
    """Fetch and score GSC opportunities.
    
    Returns dict with items, total, and pagination info.
    """
    conn.row_factory = sqlite3.Row
    
    where_clauses = [
        "impressions >= ?",
        "position >= ?",
        "position <= ?",
    ]
    params: list[Any] = [min_impressions, min_position, max_position]
    
    if page_type and page_type != "all":
        where_clauses.append("object_type = ?")
        params.append(page_type)
    
    where_sql = " AND ".join(where_clauses)
    
    count_row = conn.execute(
        f"""
        SELECT COUNT(*) as cnt
        FROM gsc_query_rows
        WHERE {where_sql}
        """,
        params,
    ).fetchone()
    total = count_row["cnt"] if count_row else 0
    
    rows = conn.execute(
        f"""
        SELECT 
            object_type,
            object_handle,
            url,
            query,
            clicks,
            impressions,
            ctr,
            position,
            fetched_at
        FROM gsc_query_rows
        WHERE {where_sql}
        ORDER BY impressions DESC, position ASC
        LIMIT 500
        """,
        params,
    ).fetchall()
    
    items: list[dict[str, Any]] = []
    for row in rows:
        ctr_val = float(row["ctr"]) if row["ctr"] is not None else (
            row["clicks"] / row["impressions"] if row["impressions"] > 0 else 0.0
        )
        opp_score = compute_opportunity_score(
            position=float(row["position"]) if row["position"] else 0.0,
            impressions=int(row["impressions"]) if row["impressions"] else 0,
            clicks=int(row["clicks"]) if row["clicks"] else 0,
            ctr=ctr_val,
        )
        
        items.append({
            "id": f"{row['object_type']}:{row['object_handle']}:{row['query'][:50]}",
            "query": row["query"],
            "page_url": row["url"],
            "page_type": PAGE_TYPE_MAP.get(row["object_type"], row["object_type"]),
            "object_type": row["object_type"],
            "object_handle": row["object_handle"],
            "impressions": int(row["impressions"]) if row["impressions"] else 0,
            "clicks": int(row["clicks"]) if row["clicks"] else 0,
            "ctr": round(ctr_val * 100, 2),
            "position": round(float(row["position"]), 1) if row["position"] else 0.0,
            "opportunity_score": opp_score,
            "suggested_action": _suggest_action(
                float(row["position"]) if row["position"] else 0.0,
                ctr_val,
                int(row["impressions"]) if row["impressions"] else 0,
            ),
            "content_type": _infer_content_type(row["query"]),
            "fetched_at": row["fetched_at"],
        })
    
    valid_sorts = {"opportunity_score", "impressions", "clicks", "position", "ctr"}
    if sort_by not in valid_sorts:
        sort_by = "opportunity_score"
    
    reverse = sort_dir.lower() != "asc"
    if sort_by == "position":
        reverse = not reverse
    
    items.sort(key=lambda x: x.get(sort_by, 0) or 0, reverse=reverse)
    
    paginated = items[offset:offset + limit]
    
    return {
        "items": paginated,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < len(items),
    }


def get_opportunity_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Get summary statistics for opportunities."""
    conn.row_factory = sqlite3.Row
    
    stats: dict[str, Any] = {
        "total_queries": 0,
        "striking_distance": 0,
        "quick_wins": 0,
        "high_impressions_low_ctr": 0,
        "by_page_type": {},
    }
    
    count_row = conn.execute("SELECT COUNT(*) as cnt FROM gsc_query_rows").fetchone()
    stats["total_queries"] = count_row["cnt"] if count_row else 0
    
    sd_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM gsc_query_rows WHERE position BETWEEN 4 AND 20"
    ).fetchone()
    stats["striking_distance"] = sd_row["cnt"] if sd_row else 0
    
    qw_row = conn.execute(
        "SELECT COUNT(*) as cnt FROM gsc_query_rows WHERE position BETWEEN 11 AND 20 AND impressions >= 50"
    ).fetchone()
    stats["quick_wins"] = qw_row["cnt"] if qw_row else 0
    
    type_rows = conn.execute(
        """
        SELECT object_type, COUNT(*) as cnt
        FROM gsc_query_rows
        GROUP BY object_type
        """
    ).fetchall()
    stats["by_page_type"] = {
        PAGE_TYPE_MAP.get(r["object_type"], r["object_type"]): r["cnt"]
        for r in type_rows
    }
    
    return stats
