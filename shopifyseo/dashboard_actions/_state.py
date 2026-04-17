"""Shared state, locks, constants, and low-level utilities for dashboard_actions."""
import logging
import queue
import sqlite3
import threading
import time
import traceback
import uuid
from collections import deque

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
PAGESPEED_SYNC_WORKERS = 12
PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE = 60
PAGESPEED_RECENT_FETCH_WINDOW_SECONDS = 30 * 24 * 60 * 60

# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

SYNC_LOCK = threading.Lock()
AI_LOCK = threading.Lock()
AI_JOBS_LOCK = threading.Lock()

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
    "force_refresh": False,
    "stage": "idle",
    "stage_label": "",
    "active_scope": "",
    "step_index": 0,
    "step_total": 0,
    "total": 0,
    "done": 0,
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
    "gsc_summary_pages": 0,
    "gsc_summary_queries": 0,
    "ga4_rows": 0,
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
    "pagespeed_error_details": [],
    "cancel_requested": False,
    "selected_scopes": [],
}

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
        SYNC_STATE["last_error"] = f"{exc}\n\n{traceback.format_exc()}"
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
        raise RuntimeError("AI generation cancelled by user")


def request_sync_cancel() -> None:
    SYNC_STATE["cancel_requested"] = True


def _sync_cancelled() -> bool:
    return bool(SYNC_STATE.get("cancel_requested"))


def _raise_if_sync_cancelled() -> None:
    if _sync_cancelled():
        raise RuntimeError("Sync cancelled by user")


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
