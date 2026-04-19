"""Shared state, locks, constants, and low-level utilities for dashboard_actions."""
import logging
import queue
import sqlite3
import threading
import time
import uuid
from collections import deque

from ..exceptions import AICancelledError, SyncCancelledError

from ._rpm_limiter import PerMinuteRateLimiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GSC_SYNC_THROTTLE_SECONDS = 0.1
GSC_SYNC_WORKERS = 5
GSC_SYNC_RATE_LIMIT_PER_MINUTE = 100
GA4_SYNC_WORKERS = 4
GA4_SYNC_RATE_LIMIT_PER_MINUTE = 120
PAGESPEED_SYNC_THROTTLE_SECONDS = 0.4
INDEX_SYNC_WORKERS = 5
INDEX_SYNC_RATE_LIMIT_PER_MINUTE = 55
# PageSpeed Insights often returns HTTP 500 when overloaded. Fewer concurrent calls + lower RPM reduce 5xx.
# Rolling cap on every runPagespeed HTTP request process-wide (bulk sync, per-object refresh, retries).
PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE = 240
PAGESPEED_SYNC_WORKERS = 40
# Primary PageSpeed queue is processed in slices of this many jobs, then a pause (bulk sync only).
PAGESPEED_SYNC_BATCH_SIZE = 400
PAGESPEED_SYNC_BATCH_PAUSE_SECONDS = 180.0
PAGESPEED_SYNC_MIN_INTERVAL_SECONDS = 60.0 / PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE
PAGESPEED_RECENT_FETCH_WINDOW_SECONDS = 30 * 24 * 60 * 60
# Cap in-memory PageSpeed error log during one sync (avoids huge payloads / memory).
PAGESPEED_ERROR_DETAILS_MAX = 500
# Rolling window for counting real runPagespeed HTTP attempts (monotonic timestamps).
PAGESPEED_HTTP_TRACK_WINDOW_SECONDS = 60
# Cap on in-memory sync event log; one bulk PageSpeed run easily appends thousands
# of HTTP/queue events that get serialized into every status response.
SYNC_EVENTS_MAX = 2000
IMAGE_CACHE_WORKERS = 6

# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

SYNC_LOCK = threading.Lock()
AI_LOCK = threading.Lock()
AI_JOBS_LOCK = threading.Lock()
_pagespeed_http_times_lock = threading.Lock()
SYNC_EVENTS_LOCK = threading.Lock()

# ---------------------------------------------------------------------------
# Global state dicts
# ---------------------------------------------------------------------------

SYNC_STATE = {
    "running": False,
    "last_result": None,
    "last_error": "",
    "scope": "",
    "started_at": 0,
    "finished_at": 0,
    "stage_started_at": 0,
    "force_refresh": False,
    "stage": "idle",
    "stage_label": "",
    "active_scope": "",
    "step_index": 0,
    "step_total": 0,
    "shopify_progress_done": 0,
    "shopify_progress_total": 0,
    "gsc_progress_done": 0,
    "gsc_progress_total": 0,
    "ga4_progress_done": 0,
    "ga4_progress_total": 0,
    "index_progress_done": 0,
    "index_progress_total": 0,
    "current": "",
    "products_synced": 0,
    "products_total": 0,
    "collections_synced": 0,
    "collections_total": 0,
    "pages_synced": 0,
    "pages_total": 0,
    "blogs_synced": 0,
    "blogs_total": 0,
    "blog_articles_synced": 0,
    "blog_articles_total": 0,
    "images_synced": 0,
    "images_total": 0,
    "gsc_refreshed": 0,
    "gsc_skipped": 0,
    "gsc_errors": 0,
    "gsc_eligible_total": 0,
    "gsc_precheck_skipped": 0,
    "gsc_summary_pages": 0,
    "gsc_summary_queries": 0,
    "ga4_rows": 0,
    "ga4_refreshed": 0,
    "ga4_precheck_skipped": 0,
    "ga4_url_errors": 0,
    "ga4_errors": 0,
    "index_refreshed": 0,
    "index_skipped": 0,
    "index_errors": 0,
    "pagespeed_refreshed": 0,
    "pagespeed_rate_limited": 0,
    "pagespeed_skipped": 0,
    "pagespeed_skipped_recent": 0,
    "pagespeed_errors": 0,
    "pagespeed_phase": "",
    "pagespeed_scanned": 0,
    "pagespeed_scan_total": 0,
    "pagespeed_queue_total": 0,
    "pagespeed_queue_completed": 0,
    "pagespeed_queue_inflight": 0,
    "pagespeed_http_calls_last_60s": 0,
    "sync_events": [],
    "pagespeed_error_details": [],
    "pagespeed_queue_details": [],
    "pagespeed_queue_meta": {},
    "pagespeed_queue_baseline": 0,
    "pagespeed_error_seq": 0,
    "cancel_requested": False,
    "selected_scopes": [],
}

# Monotonic timestamps of each granted runPagespeed HTTP attempt (shared rate gate).
_pagespeed_http_monotonic_times: deque[float] = deque()


def _trim_pagespeed_http_times_unlocked(now: float) -> int:
    """Drop timestamps at or before ``now - window`` (match ``_PerMinuteRateLimiter`` semantics)."""
    cutoff = now - PAGESPEED_HTTP_TRACK_WINDOW_SECONDS
    while _pagespeed_http_monotonic_times and _pagespeed_http_monotonic_times[0] <= cutoff:
        _pagespeed_http_monotonic_times.popleft()
    return len(_pagespeed_http_monotonic_times)


def record_pagespeed_http_api_call_at(monotonic_ts: float) -> None:
    """Record one granted runPagespeed HTTP slot using the same instant the rate limiter used."""
    with _pagespeed_http_times_lock:
        _trim_pagespeed_http_times_unlocked(monotonic_ts)
        _pagespeed_http_monotonic_times.append(monotonic_ts)
        SYNC_STATE["pagespeed_http_calls_last_60s"] = len(_pagespeed_http_monotonic_times)


def record_pagespeed_http_api_call() -> None:
    """Record using current monotonic time (tests / ad-hoc); bulk sync uses ``record_pagespeed_http_api_call_at``."""
    record_pagespeed_http_api_call_at(time.monotonic())


def refresh_pagespeed_http_calls_window() -> None:
    """Expire old timestamps and refresh ``pagespeed_http_calls_last_60s`` (e.g. on each sync-status read)."""
    now = time.monotonic()
    with _pagespeed_http_times_lock:
        SYNC_STATE["pagespeed_http_calls_last_60s"] = _trim_pagespeed_http_times_unlocked(now)


def clear_pagespeed_http_call_tracker() -> None:
    with _pagespeed_http_times_lock:
        _pagespeed_http_monotonic_times.clear()
        SYNC_STATE["pagespeed_http_calls_last_60s"] = 0


def append_sync_event(tag: str, msg: str) -> None:
    """Append one row to the sync event log (API + UI). Trimmed to ``SYNC_EVENTS_MAX``."""
    t = (tag or "sync").strip() or "sync"
    m = (msg or "").strip()
    if not m:
        return
    row = {"at": int(time.time()), "tag": t[:48], "msg": m}
    with SYNC_EVENTS_LOCK:
        events = SYNC_STATE.get("sync_events")
        if not isinstance(events, list):
            events = []
            SYNC_STATE["sync_events"] = events
        events.append(row)
        if len(events) > SYNC_EVENTS_MAX:
            del events[: len(events) - SYNC_EVENTS_MAX]


AI_STATE = {
    "job_id": "",
    "running": False,
    "scope": "",
    "mode": "",
    "object_type": "",
    "handle": "",
    "field": "",
    "started_at": 0,
    "finished_at": 0,
    "stage": "idle",
    "stage_label": "",
    "active_model": "",
    "step_index": 0,
    "step_total": 0,
    "total": 0,
    "done": 0,
    "current": "",
    "successes": 0,
    "failures": 0,
    "last_error": "",
    "last_result": None,
    "stage_started_at": 0,
    "steps": [],
    "cancel_requested": False,
}

AI_JOBS: dict[str, dict] = {}
AI_JOB_QUEUES: dict[str, queue.Queue] = {}  # job_id -> event queue for SSE consumers

# ---------------------------------------------------------------------------
# Error utilities
# ---------------------------------------------------------------------------


def record_last_error(exc: Exception | str) -> None:
    if isinstance(exc, Exception):
        SYNC_STATE["last_error"] = str(exc)
        logger.error("Dashboard sync failed: %s", exc, exc_info=exc)
    else:
        SYNC_STATE["last_error"] = str(exc)


def clear_last_error() -> None:
    SYNC_STATE["last_error"] = ""


def clear_ai_last_error() -> None:
    AI_STATE["last_error"] = ""


# ---------------------------------------------------------------------------
# AI state sync helpers
# ---------------------------------------------------------------------------


def _sync_global_ai_state(state: dict) -> None:
    AI_STATE.clear()
    AI_STATE.update(dict(state))


def _snapshot_ai_state(state: dict) -> dict:
    return dict(state)


# ---------------------------------------------------------------------------
# Cancel utilities
# ---------------------------------------------------------------------------


def request_ai_cancel(job_id: str | None = None) -> None:
    with AI_JOBS_LOCK:
        if job_id and job_id in AI_JOBS:
            AI_JOBS[job_id]["cancel_requested"] = True
            _sync_global_ai_state(AI_JOBS[job_id])
            return
        if AI_STATE.get("job_id") and AI_STATE["job_id"] in AI_JOBS:
            AI_JOBS[AI_STATE["job_id"]]["cancel_requested"] = True
        AI_STATE["cancel_requested"] = True


def _ai_cancelled(job_id: str | None = None) -> bool:
    with AI_JOBS_LOCK:
        if job_id and job_id in AI_JOBS:
            return bool(AI_JOBS[job_id].get("cancel_requested"))
        return bool(AI_STATE.get("cancel_requested"))


def _raise_if_ai_cancelled(job_id: str | None = None) -> None:
    if _ai_cancelled(job_id):
        raise AICancelledError()


def request_sync_cancel() -> None:
    SYNC_STATE["cancel_requested"] = True


def _sync_cancelled() -> bool:
    return bool(SYNC_STATE.get("cancel_requested"))


def _raise_if_sync_cancelled() -> None:
    if _sync_cancelled():
        raise SyncCancelledError()


# ---------------------------------------------------------------------------
# Shared DB / result helpers
# ---------------------------------------------------------------------------


def _db_connect_for_actions(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def _step_result(status: str, message: str) -> dict:
    return {"status": status, "message": message}


# ---------------------------------------------------------------------------
# PageSpeed Insights — one shared RPM gate for every refresh=True caller
# ---------------------------------------------------------------------------

_PAGESPEED_HTTP_RATE_LIMITER = PerMinuteRateLimiter(
    PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE,
    on_granted=record_pagespeed_http_api_call_at,
    min_interval_seconds=PAGESPEED_SYNC_MIN_INTERVAL_SECONDS,
)


def acquire_pagespeed_http_rate_slot(cancel_check=None) -> None:
    """Block until a ``runPagespeed`` HTTP attempt may proceed; updates the rolling Speed counter."""
    _PAGESPEED_HTTP_RATE_LIMITER.acquire(cancel_check)
