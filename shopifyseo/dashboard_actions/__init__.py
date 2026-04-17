"""dashboard_actions package — re-exports all public symbols for backward compatibility.

Internal layout:
  _state.py  — global state dicts, locks, constants, shared utilities
  _sync.py   — Shopify / GSC / GA4 / index / PageSpeed sync operations
  _ai.py     — AI background job management and object signal refresh
"""

from ._state import (
    AI_JOBS,
    AI_JOBS_LOCK,
    AI_JOB_QUEUES,
    AI_LOCK,
    AI_STATE,
    SYNC_LOCK,
    SYNC_STATE,
    clear_ai_last_error,
    clear_last_error,
    record_last_error,
    request_ai_cancel,
    request_sync_cancel,
)

from ._sync import (
    _all_object_targets,        # accessed directly in tests via monkeypatch
    _index_inspection_targets,  # accessed directly in tests
    bulk_refresh_index_status,
    bulk_refresh_pagespeed,
    bulk_refresh_search_console,
    refresh_ga4_summary,
    run_sync,
    start_sync_background,
)
from .. import dashboard_queries as dq  # tests monkeypatch da.dq

from ._ai import (
    consume_job_events,
    generate_ai_for_object,
    refresh_and_get_inspection_link,
    refresh_object_signal_step,
    refresh_object_signals,
    run_ai_field_regeneration,
    run_ai_generation,
    start_ai_background,
    start_ai_field_background,
    start_ai_object_background,
)

__all__ = [
    # state
    "AI_JOBS",
    "AI_JOBS_LOCK",
    "AI_JOB_QUEUES",
    "AI_LOCK",
    "AI_STATE",
    "SYNC_LOCK",
    "SYNC_STATE",
    "clear_ai_last_error",
    "clear_last_error",
    "record_last_error",
    "request_ai_cancel",
    "request_sync_cancel",
    # sync
    "_index_inspection_targets",
    "bulk_refresh_index_status",
    "bulk_refresh_pagespeed",
    "bulk_refresh_search_console",
    "refresh_ga4_summary",
    "run_sync",
    "start_sync_background",
    # ai
    "consume_job_events",
    "generate_ai_for_object",
    "refresh_and_get_inspection_link",
    "refresh_object_signal_step",
    "refresh_object_signals",
    "run_ai_field_regeneration",
    "run_ai_generation",
    "start_ai_background",
    "start_ai_field_background",
    "start_ai_object_background",
]
