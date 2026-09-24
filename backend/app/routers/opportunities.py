"""GSC Opportunity Inbox router.

Provides API endpoints for browsing and acting on GSC-derived SEO opportunities.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.db import open_db_connection
from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.opportunities import (
    CreateIdeaFromOpportunityRequest,
    OpportunitiesPayload,
    OpportunityStats,
)
from backend.app.services.opportunities_service import (
    fetch_opportunities,
    get_opportunity_stats,
)

router = APIRouter(prefix="/api", tags=["opportunities"])


@router.get("/opportunities", response_model=SuccessResponse[OpportunitiesPayload])
def list_opportunities(
    page_type: str | None = Query(None, description="Filter by page type (product, collection, page, blog_article)"),
    min_impressions: int = Query(10, ge=0, description="Minimum impressions"),
    min_position: float = Query(1.0, ge=0, description="Minimum position"),
    max_position: float = Query(50.0, ge=1, description="Maximum position"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    sort_by: str = Query("opportunity_score", description="Sort field"),
    sort_dir: str = Query("desc", description="Sort direction (asc/desc)"),
):
    """List GSC opportunities with scoring and filtering."""
    conn = open_db_connection()
    try:
        result = fetch_opportunities(
            conn,
            page_type=page_type,
            min_impressions=min_impressions,
            min_position=min_position,
            max_position=max_position,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        return success_response(OpportunitiesPayload.model_validate(result))
    finally:
        conn.close()


@router.get("/opportunities/stats", response_model=SuccessResponse[OpportunityStats])
def opportunities_stats():
    """Get summary statistics for opportunities."""
    conn = open_db_connection()
    try:
        stats = get_opportunity_stats(conn)
        return success_response(OpportunityStats.model_validate(stats))
    finally:
        conn.close()


@router.post("/opportunities/create-idea", response_model=SuccessResponse[dict])
def create_idea_from_opportunity(payload: CreateIdeaFromOpportunityRequest):
    """Create an article idea from an opportunity.
    
    This seeds an article idea with the opportunity query as the primary keyword.
    """
    conn = open_db_connection()
    try:
        from shopifyseo.dashboard_article_ideas import save_article_ideas
        
        content_type = payload.content_type or "Blog / Guide"
        
        idea_data = {
            "suggested_title": f"Guide: {payload.query.title()}",
            "primary_keyword": payload.query,
            "supporting_keywords": [],
            "brief": f"Content opportunity identified from GSC data for query: {payload.query}",
            "gap_reason": "GSC opportunity - high impressions or striking distance position",
            "search_intent": "informational",
            "source_type": "gsc_opportunity",
            "linked_cluster_id": None,
            "total_volume": 0,
            "avg_difficulty": 0,
            "opportunity_score": 0,
            "status": "idea",
        }
        
        ideas = save_article_ideas(conn, [idea_data])
        
        if ideas:
            return success_response({
                "message": "Article idea created from opportunity",
                "idea_id": ideas[0],
            })
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create article idea",
        )
    finally:
        conn.close()
