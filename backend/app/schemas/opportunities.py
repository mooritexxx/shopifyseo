"""Pydantic schemas for GSC Opportunity Inbox API."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OpportunityItem(BaseModel):
    """Single GSC opportunity item."""
    id: str
    query: str
    page_url: str
    page_type: str
    object_type: str
    object_handle: str
    impressions: int
    clicks: int
    ctr: float = Field(description="CTR as percentage (0-100)")
    position: float
    opportunity_score: float = Field(description="Opportunity score (0-100)")
    suggested_action: str
    content_type: str = Field(description="Content type hint based on query patterns")
    fetched_at: int | None = None


class OpportunitiesPayload(BaseModel):
    """Response payload for opportunities list."""
    items: list[OpportunityItem]
    total: int
    limit: int
    offset: int
    has_more: bool


class OpportunityStats(BaseModel):
    """Summary statistics for opportunities."""
    total_queries: int
    striking_distance: int
    quick_wins: int
    high_impressions_low_ctr: int
    by_page_type: dict[str, int]


class CreateIdeaFromOpportunityRequest(BaseModel):
    """Request to create an article idea from an opportunity."""
    query: str
    object_type: str
    object_handle: str
    content_type: str | None = None
