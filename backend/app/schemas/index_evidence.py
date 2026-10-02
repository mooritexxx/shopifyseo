from pydantic import BaseModel


class IndexEvidenceFields(BaseModel):
    index_last_fetched_at: int | None = None
    index_last_crawl_at: str | None = None
    index_robots_state: str | None = None
    index_page_fetch_state: str | None = None
    index_indexing_state: str | None = None
    index_verdict: str | None = None
    index_flag: str | None = None
    index_flag_reason: str | None = None
    crawl_age_days: int | None = None
    inspection_age_days: int | None = None
    index_action_type: str | None = None


class RecrawlCandidate(BaseModel):
    url: str
    handle: str
    vendor: str
    in_stock: bool
    coverage: str
    crawled_at: str | None
    inspected_at: int | None
    inspect_href: str | None
    flag_reason: str
