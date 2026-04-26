from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field


class InterlinkTarget(BaseModel):
    type: str
    handle: str
    title: str = ""
    url: str = ""
    anchor_keyword: str = ""
    source: str = ""


class AudienceQuestionItem(BaseModel):
    question: str
    snippet: str = ""


def _coerce_audience_questions(v: Any) -> list[dict[str, str]]:
    if v is None:
        return []
    if not isinstance(v, list):
        return []
    out: list[dict[str, str]] = []
    for item in v:
        if isinstance(item, str):
            q = item.strip()
            if q:
                out.append({"question": q, "snippet": ""})
        elif isinstance(item, dict):
            q = str(item.get("question") or "").strip()
            if not q:
                continue
            sn = item.get("snippet") if item.get("snippet") is not None else item.get("answer")
            out.append({"question": q, "snippet": str(sn or "").strip()})
        if len(out) >= 80:
            break
    return out


AudienceQuestions = Annotated[list[AudienceQuestionItem], BeforeValidator(_coerce_audience_questions)]


class TopRankingPageItem(BaseModel):
    title: str
    url: str


def _coerce_top_ranking_pages(v: Any) -> list[dict[str, str]]:
    if v is None:
        return []
    if not isinstance(v, list):
        return []
    out: list[dict[str, str]] = []
    for item in v:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url:
            continue
        if not title:
            title = url if len(url) <= 120 else url[:117] + "…"
        out.append({"title": title, "url": url})
        if len(out) >= 20:
            break
    return out


TopRankingPages = Annotated[list[TopRankingPageItem], BeforeValidator(_coerce_top_ranking_pages)]


class RelatedSearchItem(BaseModel):
    query: str
    position: int = 0


def _coerce_related_searches(v: Any) -> list[dict[str, Any]]:
    if v is None:
        return []
    if not isinstance(v, list):
        return []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(v):
        if not isinstance(item, dict):
            continue
        q = str(item.get("query") or "").strip()
        if not q:
            continue
        pos_raw = item.get("position", i + 1)
        try:
            pos = int(pos_raw) if not isinstance(pos_raw, bool) else i + 1
        except (TypeError, ValueError):
            pos = i + 1
        out.append({"query": q, "position": pos})
        if len(out) >= 40:
            break
    return out


RelatedSearches = Annotated[list[RelatedSearchItem], BeforeValidator(_coerce_related_searches)]


def _coerce_paa_expansion(v: Any) -> list[dict[str, Any]]:
    if v is None:
        return []
    if not isinstance(v, list):
        return []
    out: list[dict[str, Any]] = []
    for item in v:
        if not isinstance(item, dict):
            continue
        pq = str(item.get("parent_question") or "").strip()
        children_raw = item.get("children")
        if not pq or not isinstance(children_raw, list):
            continue
        children: list[dict[str, str]] = []
        for ch in children_raw:
            if not isinstance(ch, dict):
                continue
            q = str(ch.get("question") or "").strip()
            if not q:
                continue
            sn = str(ch.get("snippet") or "").strip()
            children.append({"question": q, "snippet": sn})
            if len(children) >= 10:
                break
        if children:
            out.append({"parent_question": pq, "children": children})
    return out


class PaaExpansionLayer(BaseModel):
    """One top-level PAA question and its deeper ``google_related_questions`` children."""

    parent_question: str = ""
    children: AudienceQuestions = Field(default_factory=list)


PaaExpansion = Annotated[list[PaaExpansionLayer], BeforeValidator(_coerce_paa_expansion)]


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
    # Interlink targets for topical authority
    primary_target: InterlinkTarget | None = None
    secondary_targets: list[InterlinkTarget] = Field(default_factory=list)
    # SerpAPI Google Search related_questions: question + snippet when serpapi_api_key is set
    audience_questions: AudienceQuestions = Field(default_factory=list)
    # Organic titles + URLs from the same SerpAPI Google search (primary keyword)
    top_ranking_pages: TopRankingPages = Field(default_factory=list)
    # Google AI overview (SerpAPI ``ai_overview``): text_blocks + references when present
    ai_overview: dict[str, Any] | None = None
    # Google related searches (SerpAPI ``related_searches``): query + position when present
    related_searches: RelatedSearches = Field(default_factory=list)
    # Deeper PAA from SerpAPI ``engine=google_related_questions`` (Refresh SERP data on idea detail)
    paa_expansion: PaaExpansion = Field(default_factory=list)


class ArticleIdeasPayload(BaseModel):
    items: list[ArticleIdeaItem]
    total: int


class RefreshArticleIdeaSerpPayload(BaseModel):
    """Response from ``POST /article-ideas/{id}/refresh-serp``."""

    idea: ArticleIdeaItem
    message: str


class UpdateIdeaStatusRequest(BaseModel):
    new_status: str


class BulkStatusRequest(BaseModel):
    idea_ids: list[int]
    status: str


class BulkDeleteRequest(BaseModel):
    idea_ids: list[int] = Field(min_length=1)


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
