from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.schemas.operations import GscPropertyBreakdownsPayload, GscRowPayload, SummaryPeriodPayload


class CountSummary(BaseModel):
    products: int
    variants: int
    images: int
    product_metafields: int
    collections: int
    collection_metafields: int
    collection_products: int
    pages: int
    blogs: int
    blog_articles: int


class OverviewMetrics(BaseModel):
    collections_missing_meta: int
    pages_missing_meta: int
    products_missing_meta: int
    products_thin_body: int
    gsc_pages: int
    gsc_clicks: int
    gsc_impressions: int
    ga4_pages: int
    ga4_sessions: int
    ga4_views: int


class SyncRunSummary(BaseModel):
    id: int
    started_at: str | None = None
    finished_at: str | None = None
    status: str | None = None
    products_synced: int | None = None
    variants_synced: int | None = None
    images_synced: int | None = None
    metafields_synced: int | None = None
    collections_synced: int | None = None
    collection_metafields_synced: int | None = None
    collection_products_synced: int | None = None
    pages_synced: int | None = None
    blogs_synced: int | None = None
    blog_articles_synced: int | None = None
    error_message: str | None = None


class GscPeriodRollup(BaseModel):
    start_date: str
    end_date: str
    clicks: int = 0
    impressions: int = 0
    ctr: float = 0.0
    position: float | None = None


class GscDailyPoint(BaseModel):
    date: str
    clicks: int = 0
    impressions: int = 0
    ctr_pct: float = 0.0
    position: float | None = None


class OverviewGoals(BaseModel):
    """Optional daily targets for chart reference lines (from server env)."""

    gsc_daily_clicks: float | None = None
    gsc_daily_impressions: float | None = None
    ga4_daily_sessions: float | None = None
    ga4_daily_views: float | None = None


class GscSiteOverview(BaseModel):
    available: bool
    timezone: str = ""
    period_mode: str = ""
    url_segment: str = "all"
    anchor_date: str = ""
    error: str | None = None
    current: GscPeriodRollup | None = None
    previous: GscPeriodRollup | None = None
    deltas: dict[str, float | None] = Field(default_factory=dict)
    series: list[GscDailyPoint] = Field(default_factory=list)
    cache: dict[str, Any] = Field(default_factory=dict)


class Ga4PeriodRollup(BaseModel):
    start_date: str
    end_date: str
    sessions: int = 0
    views: int = 0
    new_users: int = 0
    avg_session_duration: float = 0.0
    bounce_rate: float = 0.0


class Ga4DailyPoint(BaseModel):
    date: str
    sessions: int = 0
    views: int = 0


class Ga4SiteOverview(BaseModel):
    available: bool
    timezone: str = ""
    period_mode: str = ""
    anchor_date: str = ""
    error: str | None = None
    current: Ga4PeriodRollup | None = None
    previous: Ga4PeriodRollup | None = None
    deltas: dict[str, float | None] = Field(default_factory=dict)
    series: list[Ga4DailyPoint] = Field(default_factory=list)
    cache: dict[str, Any] = Field(default_factory=dict)


GscPeriodMode = Literal["mtd", "full_months", "since_2026_02_15", "rolling_30d"]


def normalize_gsc_period_mode(raw: str) -> GscPeriodMode:
    k = (raw or "rolling_30d").strip().lower()
    if k == "last_16_months":
        k = "since_2026_02_15"
    if k in ("mtd", "full_months", "since_2026_02_15", "rolling_30d"):
        return k
    return "rolling_30d"


class CatalogSegment(BaseModel):
    total: int
    missing_meta: int
    meta_complete: int
    pct_meta_complete: float


class ProductCatalogSegment(CatalogSegment):
    thin_body: int = 0


class CatalogCompletion(BaseModel):
    products: ProductCatalogSegment
    collections: CatalogSegment
    pages: CatalogSegment
    articles: CatalogSegment


class IndexingTypeBuckets(BaseModel):
    total: int = 0
    indexed: int = 0
    not_indexed: int = 0
    needs_review: int = 0
    unknown: int = 0


class IndexingRollup(BaseModel):
    """Counts from stored URL Inspection fields on synced entities (not live Search Console API)."""

    total: int
    indexed: int
    not_indexed: int
    needs_review: int
    unknown: int
    by_type: dict[str, IndexingTypeBuckets]


class TopOrganicPage(BaseModel):
    entity_type: str
    handle: str
    title: str
    gsc_clicks: int = 0
    gsc_impressions: int = 0
    gsc_ctr: float = 0.0
    gsc_position: float | None = None
    url: str = ""


class DashboardSummary(BaseModel):
    counts: CountSummary
    metrics: OverviewMetrics
    recent_runs: list[SyncRunSummary]
    gsc_site: GscSiteOverview
    ga4_site: Ga4SiteOverview
    indexing_rollup: IndexingRollup
    catalog_completion: CatalogCompletion
    overview_goals: OverviewGoals
    gsc_property_breakdowns: GscPropertyBreakdownsPayload
    top_pages: list[TopOrganicPage] = Field(default_factory=list)
    gsc_queries: list[GscRowPayload] = Field(default_factory=list)
    gsc_pages: list[GscRowPayload] = Field(default_factory=list)
    gsc_performance_period: SummaryPeriodPayload = Field(default_factory=SummaryPeriodPayload)
    gsc_performance_error: str = ""
