from pydantic import BaseModel, Field


class BlogListItem(BaseModel):
    handle: str
    title: str
    updated_at: str | None = None
    article_count: int = 0


class BlogListPayload(BaseModel):
    items: list[BlogListItem]
    total: int


class BlogArticleListItem(BaseModel):
    handle: str
    title: str
    blog_handle: str
    published_at: str | None = None
    updated_at: str | None = None
    is_published: bool = True
    seo_title: str = ""
    seo_description: str = ""
    body_preview: str = ""


class BlogArticlesPayload(BaseModel):
    blog: BlogListItem
    items: list[BlogArticleListItem]
    total: int


class AllArticleListItem(BlogArticleListItem):
    blog_title: str = ""
    score: int = 0
    priority: str = ""
    reasons: list[str] = Field(default_factory=list)
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
    gsc_segment_flags: dict = Field(default_factory=lambda: {"has_dimensional": False})


class AllArticlesPayload(BaseModel):
    items: list[AllArticleListItem]
    total: int


class ArticleCreateRequest(BaseModel):
    blog_id: str
    title: str
    body_html: str
    author_name: str = ""
    handle: str = ""
    summary: str = ""
    tags: list[str] = []
    is_published: bool = False


class ArticleCreateResult(BaseModel):
    id: str
    title: str
    handle: str
    blog_title: str = ""
    is_published: bool = False


class ArticleGenerateDraftRequest(BaseModel):
    blog_id: str
    """Shopify GID for the target blog (e.g. 'gid://shopify/Blog/123')."""
    blog_handle: str
    """Blog handle in the local DB (e.g. 'news') — used for redirect after creation."""
    topic: str
    """The topic or working title for the new article."""
    keywords: list[str] = []
    """Optional target keywords to weave into the article."""
    author_name: str = ""
    slug_hint: str = ""
    """Optional URL handle source (topic-style phrase). If empty, slug is derived from the AI headline."""
    idea_id: int | None = None
    """If set, link the generated article back to this article idea."""
    angle_label: str = ""
    """Optional angle label when generating multiple articles from one idea (e.g. 'listicle', 'how-to')."""
    regenerate_article_handle: str | None = None
    """If set, update this existing Shopify article (same handle/URL) instead of articleCreate."""


class ArticleGenerateDraftResult(BaseModel):
    id: str
    title: str
    handle: str
    blog_handle: str
    blog_title: str = ""
    is_published: bool = False
    seo_title: str = ""
    seo_description: str = ""
