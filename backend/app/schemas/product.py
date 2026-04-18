from typing import Any

from pydantic import BaseModel, Field

from backend.app.schemas.gsc_segments import GscSegmentFlagsPayload, GscSegmentSummaryPayload


class OpportunityPayload(BaseModel):
    object_type: str
    handle: str
    title: str
    priority: str
    score: int
    reasons: list[str]
    gsc_impressions: int = 0
    gsc_clicks: int = 0
    gsc_position: float = 0.0
    ga4_sessions: int = 0
    pagespeed_performance: int | None = None


class ProductListItem(BaseModel):
    handle: str
    title: str
    vendor: str = ""
    status: str = ""
    updated_at: str | None = None
    score: int
    priority: str
    reasons: list[str]
    total_inventory: int = 0
    body_length: int = 0
    seo_title: str = ""
    seo_description: str = ""
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
    pagespeed_status: str = ""
    workflow_status: str = "Needs fix"
    workflow_notes: str = ""
    gsc_segment_flags: GscSegmentFlagsPayload = Field(default_factory=GscSegmentFlagsPayload)
    battery_size: str = ""
    charging_port: str = ""
    coil: str = ""
    custom_collection: str = ""
    device_type: str = ""
    nicotine_strength: str = ""
    puff_count: str = ""
    size: str = ""
    battery_type_refs_json: str = ""
    coil_connection_refs_json: str = ""
    color_pattern_refs_json: str = ""
    vaporizer_style_refs_json: str = ""
    e_liquid_flavor_refs_json: str = ""
    vaping_style_refs_json: str = ""
    battery_type_labels_json: str = ""
    coil_connection_labels_json: str = ""
    color_pattern_labels_json: str = ""
    vaporizer_style_labels_json: str = ""
    e_liquid_flavor_labels_json: str = ""
    vaping_style_labels_json: str = ""


class ProductListSummary(BaseModel):
    visible_rows: int
    high_priority: int
    index_issues: int
    average_score: int


class ProductListPayload(BaseModel):
    items: list[ProductListItem]
    total: int
    limit: int | None = None
    offset: int
    query: str = ""
    sort: str = "score"
    direction: str = "desc"
    focus: str | None = None
    summary: ProductListSummary


class ProductSignalMetric(BaseModel):
    label: str
    value: str
    sublabel: str = ""
    updated_at: str | int | None = None
    step: str
    action_label: str | None = None
    action_href: str | None = None


class ProductRecommendation(BaseModel):
    summary: str = ""
    status: str = "not_generated"
    model: str = ""
    created_at: str | None = None
    error_message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ProductSummaryPayload(BaseModel):
    handle: str
    title: str


class ProductVariantPayload(BaseModel):
    shopify_id: str
    product_shopify_id: str
    legacy_resource_id: str | None = None
    title: str
    sku: str | None = None
    barcode: str | None = None
    price: str | None = None
    compare_at_price: str | None = None
    position: int | None = None
    inventory_policy: str | None = None
    inventory_quantity: int | None = None
    taxable: int | bool | None = None
    requires_shipping: bool | int | None = None
    selected_options_json: str | None = None
    image_json: str | None = None
    raw_json: str | None = None
    synced_at: str | None = None


class MetafieldPayload(BaseModel):
    namespace: str
    key: str
    type: str
    value: str | None = None


class ProductImagePayload(BaseModel):
    """Row from ``product_images`` / Shopify gallery (ordered by ``position``)."""

    shopify_id: str | None = None
    url: str
    alt_text: str = ""
    position: int | None = None


class RecommendationHistoryPayload(BaseModel):
    priority: str | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None
    source: str | None = None
    status: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    error_message: str | None = None


class WorkflowPayload(BaseModel):
    status: str = "Needs fix"
    notes: str = ""
    updated_at: str | None = None


class ProductDraftPayload(BaseModel):
    title: str = ""
    seo_title: str = ""
    seo_description: str = ""
    body_html: str = ""
    tags: str = ""
    workflow_status: str = "Needs fix"
    workflow_notes: str = ""


class ProductUpdatePayload(ProductDraftPayload):
    pass


class ProductInspectionLinkPayload(BaseModel):
    href: str


class ProductDetailPayload(BaseModel):
    product: dict[str, Any]
    draft: ProductDraftPayload
    workflow: WorkflowPayload
    recommendation: ProductRecommendation
    recommendation_history: list[RecommendationHistoryPayload]
    signal_cards: list[ProductSignalMetric]
    collections: list[ProductSummaryPayload]
    variants: list[ProductVariantPayload]
    metafields: list[MetafieldPayload]
    product_images: list[ProductImagePayload] = Field(default_factory=list)
    opportunity: OpportunityPayload
    gsc_segment_summary: GscSegmentSummaryPayload = Field(default_factory=GscSegmentSummaryPayload)
    gsc_queries: list[dict[str, Any]] = Field(default_factory=list)


class ProductRefreshRequest(BaseModel):
    step: str | None = None


class ProductActionResult(BaseModel):
    message: str
    state: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    steps: dict[str, Any] | None = None


class FieldRegenerateRequest(BaseModel):
    field: str
    accepted_fields: dict[str, str] = Field(default_factory=dict)


class FieldRegenerateResult(BaseModel):
    field: str
    value: str
    generation_model: str = ""
    review_model: str = ""
    review_action: str = ""
    generated_at: int = 0
