from pydantic import BaseModel, Field


class ArticleIdeaItem(BaseModel):
    id: int
    suggested_title: str
    brief: str
    primary_keyword: str = ""
    supporting_keywords: list[str] = Field(default_factory=list)
    search_intent: str = "informational"
    content_format: str = ""
    estimated_monthly_traffic: int = 0
    linked_cluster_id: int | None = None
    linked_cluster_name: str = ""
    linked_collection_handle: str = ""
    linked_collection_title: str = ""
    source_type: str = "cluster_gap"
    gap_reason: str = ""
    status: str = "idea"
    created_at: int
    total_volume: int = 0
    avg_difficulty: float = 0.0
    opportunity_score: float = 0.0
    dominant_serp_features: str = ""
    content_format_hints: str = ""
    linked_keywords_json: list = Field(default_factory=list)
    linked_article_handle: str = ""
    linked_blog_handle: str = ""
    shopify_article_id: str = ""
    # Aggregate metrics from idea_articles junction
    article_count: int = 0
    agg_gsc_clicks: int = 0
    agg_gsc_impressions: int = 0
    coverage_pct: float | None = None


class ArticleIdeasPayload(BaseModel):
    items: list[ArticleIdeaItem]
    total: int


class UpdateIdeaStatusRequest(BaseModel):
    new_status: str


class BulkStatusRequest(BaseModel):
    idea_ids: list[int]
    status: str


class LinkedArticle(BaseModel):
    id: int
    blog_handle: str
    article_handle: str
    shopify_article_id: str = ""
    angle_label: str = ""
    created_at: int
    article_title: str = ""
    is_published: bool = False
    gsc_clicks: int = 0
    gsc_impressions: int = 0
    gsc_position: float | None = None


class TargetKeywordCoverage(BaseModel):
    keyword: str
    is_primary: bool = False
    gsc_clicks: int = 0
    gsc_impressions: int = 0
    gsc_position: float | None = None
    status: str = "not_ranking"


class DiscoveredKeyword(BaseModel):
    query: str
    clicks: int = 0
    impressions: int = 0
    position: float | None = None


class CoverageSummary(BaseModel):
    total_targets: int = 0
    ranking_count: int = 0
    gap_count: int = 0
    discovered_count: int = 0
    coverage_pct: float = 0.0


class KeywordCoveragePayload(BaseModel):
    target_keywords: list[TargetKeywordCoverage] = Field(default_factory=list)
    discovered_keywords: list[DiscoveredKeyword] = Field(default_factory=list)
    summary: CoverageSummary = Field(default_factory=CoverageSummary)


class IdeaPerformancePayload(BaseModel):
    articles: list[LinkedArticle] = Field(default_factory=list)
    aggregate: dict = Field(default_factory=dict)
    keyword_coverage: CoverageSummary = Field(default_factory=CoverageSummary)
