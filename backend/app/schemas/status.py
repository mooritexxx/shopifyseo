from typing import Any

from pydantic import BaseModel


class SyncStatusPayload(BaseModel):
    running: bool
    last_result: dict[str, Any] | None = None
    last_error: str = ""
    scope: str = ""
    selected_scopes: list[str] = []
    force_refresh: bool = False
    started_at: int = 0
    finished_at: int = 0
    stage: str = "idle"
    stage_label: str = ""
    active_scope: str = ""
    step_index: int = 0
    step_total: int = 0
    total: int = 0
    done: int = 0
    current: str = ""
    products_synced: int = 0
    products_total: int = 0
    collections_synced: int = 0
    collections_total: int = 0
    pages_synced: int = 0
    pages_total: int = 0
    blogs_synced: int = 0
    blogs_total: int = 0
    blog_articles_synced: int = 0
    blog_articles_total: int = 0
    images_synced: int = 0
    images_total: int = 0
    gsc_refreshed: int = 0
    gsc_skipped: int = 0
    gsc_errors: int = 0
    gsc_summary_pages: int = 0
    gsc_summary_queries: int = 0
    ga4_rows: int = 0
    ga4_url_errors: int = 0
    ga4_errors: int = 0
    index_refreshed: int = 0
    index_skipped: int = 0
    index_errors: int = 0
    pagespeed_refreshed: int = 0
    pagespeed_rate_limited: int = 0
    pagespeed_skipped: int = 0
    pagespeed_skipped_recent: int = 0
    pagespeed_errors: int = 0
    pagespeed_phase: str = ""
    pagespeed_scanned: int = 0
    pagespeed_scan_total: int = 0
    pagespeed_queue_total: int = 0
    pagespeed_queue_completed: int = 0
    pagespeed_queue_inflight: int = 0
    pagespeed_error_details: list[dict[str, Any]] = []
    cancel_requested: bool = False


class AiStatusPayload(BaseModel):
    job_id: str = ""
    running: bool
    cancel_requested: bool = False
    scope: str = ""
    mode: str = ""
    object_type: str = ""
    handle: str = ""
    field: str = ""
    started_at: int = 0
    finished_at: int = 0
    stage: str = "idle"
    stage_label: str = ""
    active_model: str = ""
    stage_started_at: int = 0
    step_index: int = 0
    step_total: int = 0
    total: int = 0
    done: int = 0
    current: str = ""
    successes: int = 0
    failures: int = 0
    last_error: str = ""
    last_result: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = []


class AiStopRequestPayload(BaseModel):
    job_id: str = ""


class StoreInfoPayload(BaseModel):
    store_url: str = ""
    store_name: str = ""
    primary_market_country: str = ""
    dashboard_timezone: str = ""
