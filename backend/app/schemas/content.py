from typing import Any

from pydantic import BaseModel, Field

from backend.app.schemas.gsc_segments import GscSegmentFlagsPayload, GscSegmentSummaryPayload
from backend.app.schemas.product import MetafieldPayload, OpportunityPayload, RecommendationHistoryPayload


class ContentListItem(BaseModel):
    handle: str
    title: str
    updated_at: str | None = None
    score: int
    priority: str
    reasons: list[str]
    seo_title: str = ""
    seo_description: str = ""
    body_length: int = 0
    gsc_clicks: int = 0
    gsc_impressions: int = 0
    gsc_ctr: float = 0.0
    gsc_position: float = 0.0
    ga4_sessions: int = 0
    ga4_views: int = 0
    ga4_avg_session_duration: float = 0.0
    index_status: str = ""
    index_coverage: str = ""
    google_canonical: str = ""
    pagespeed_performance: int | None = None
    pagespeed_desktop_performance: int | None = None
    pagespeed_status: str = ""
    workflow_status: str = "Needs fix"
    workflow_notes: str = ""
    product_count: int = 0
    gsc_segment_flags: GscSegmentFlagsPayload = Field(default_factory=GscSegmentFlagsPayload)


class ContentListPayload(BaseModel):
    items: list[ContentListItem]
    total: int
    limit: int | None = None
    offset: int
    query: str = ""
    sort: str = "score"
    direction: str = "desc"
    focus: str | None = None


class ContentSignalMetric(BaseModel):
    label: str
    value: str
    sublabel: str = ""
    updated_at: str | int | None = None
    step: str
    action_label: str | None = None
    action_href: str | None = None


class WorkflowPayload(BaseModel):
    status: str = "Needs fix"
    notes: str = ""
    updated_at: str | None = None


class ContentDraftPayload(BaseModel):
    title: str = ""
    seo_title: str = ""
    seo_description: str = ""
    body_html: str = ""
    workflow_status: str = "Needs fix"
    workflow_notes: str = ""


class ContentRecommendation(BaseModel):
    summary: str = ""
    status: str = "not_generated"
    model: str = ""
    created_at: str | None = None
    error_message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ContentRelatedItemPayload(BaseModel):
    handle: str
    title: str
    type: str


class ContentDetailPayload(BaseModel):
    object_type: str
    current: dict[str, Any]
    draft: ContentDraftPayload
    workflow: WorkflowPayload
    recommendation: ContentRecommendation
    recommendation_history: list[RecommendationHistoryPayload]
    signal_cards: list[ContentSignalMetric]
    related_items: list[ContentRelatedItemPayload]
    metafields: list[MetafieldPayload]
    opportunity: OpportunityPayload
    gsc_segment_summary: GscSegmentSummaryPayload = Field(default_factory=GscSegmentSummaryPayload)
    gsc_queries: list[dict[str, Any]] = Field(default_factory=list)


class ContentUpdatePayload(ContentDraftPayload):
    pass
