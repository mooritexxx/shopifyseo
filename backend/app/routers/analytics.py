"""Analytics API router for GSC-derived insights.

Provides endpoints for:
  - Query × page cannibalization report
  - Decay watch-list (pages with declining metrics)
  - Organic funnel flags
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.db import open_db_connection
from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.services.analytics_service import (
    fetch_cannibalization_queries,
    fetch_decay_watchlist,
    fetch_organic_funnel_flags,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/cannibalization", response_model=SuccessResponse[dict])
def get_cannibalization_report(
    min_impressions: int = Query(20, ge=1, description="Minimum impressions per URL"),
    limit: int = Query(100, ge=1, le=500, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Get query × page cannibalization report.

    Returns queries where 2+ of your URLs rank with meaningful impressions,
    indicating potential keyword cannibalization that may dilute rankings.
    """
    conn = open_db_connection()
    try:
        result = fetch_cannibalization_queries(
            conn,
            min_impressions=min_impressions,
            limit=limit,
            offset=offset,
        )
        return success_response(result)
    finally:
        conn.close()


@router.get("/decay-watchlist", response_model=SuccessResponse[dict])
def get_decay_watchlist(
    clicks_threshold: float = Query(0.20, ge=0.0, le=1.0, description="Clicks drop threshold (0.2 = 20%)"),
    impressions_threshold: float = Query(0.25, ge=0.0, le=1.0, description="Impressions drop threshold"),
    min_prior_impressions: int = Query(100, ge=1, description="Minimum prior window impressions"),
    limit: int = Query(100, ge=1, le=500, description="Results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Get decay watch-list: pages with declining GSC metrics.

    Compares last 28 days vs prior 28 days to identify pages losing traffic.
    Useful for catching ranking drops before they become severe.
    """
    conn = open_db_connection()
    try:
        result = fetch_decay_watchlist(
            conn,
            clicks_threshold=clicks_threshold,
            impressions_threshold=impressions_threshold,
            min_prior_impressions=min_prior_impressions,
            limit=limit,
            offset=offset,
        )
        return success_response(result)
    finally:
        conn.close()


@router.get("/organic-funnel-flags", response_model=SuccessResponse[dict])
def get_organic_funnel_flags(
    window_days: int = Query(28, description="Window size (28 or 90 days)"),
    high_sessions_threshold: int = Query(10, ge=1, description="Min sessions for high-sessions flag"),
    limit: int = Query(200, ge=1, le=500, description="Max results per flag type"),
):
    """Get organic funnel flags for catalog pages.

    Returns two flag types:
    - high_sessions_no_carts: Pages with organic sessions but zero add-to-cart events
    - checkout_assists: Pages that assisted checkout or purchase events
    """
    conn = open_db_connection()
    try:
        result = fetch_organic_funnel_flags(
            conn,
            window_days=window_days,
            high_sessions_threshold=high_sessions_threshold,
            limit=limit,
        )
        return success_response(result)
    finally:
        conn.close()
