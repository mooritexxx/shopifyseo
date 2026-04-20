"""Tests for multi-service sync queue snapshots and rate-slot throughput (UX-only helpers)."""

import threading

import pytest

from shopifyseo.dashboard_actions._rpm_limiter import PerMinuteRateLimiter
from shopifyseo.dashboard_actions._state import (
    PAGESPEED_ERROR_DETAILS_MAX,
    SYNC_STATE,
    clear_sync_rate_slot_trackers,
    record_sync_rate_slot,
    refresh_sync_rate_slots_window,
)
from shopifyseo.dashboard_actions._sync_queue import sync_queue_reset, sync_queue_seed


def test_shopify_queue_snapshot_is_not_capped() -> None:
    """Shopify pending queue can exceed PAGESPEED_ERROR_DETAILS_MAX (large catalogs)."""
    sync_queue_reset("shopify")
    n = PAGESPEED_ERROR_DETAILS_MAX + 50
    targets = [("product", f"id{i}", f"h{i}") for i in range(n)]
    sync_queue_seed("shopify", targets)
    assert len(SYNC_STATE["shopify_queue_details"]) == n


def test_gsc_ga4_index_queue_snapshots_are_not_capped() -> None:
    """GSC / GA4 / index pending queues match Shopify: full row list, not PAGESPEED_ERROR_DETAILS_MAX."""
    n = PAGESPEED_ERROR_DETAILS_MAX + 50
    targets = [("product", f"h{i}", f"https://example.com/p/{i}") for i in range(n)]
    for scope, key in (
        ("gsc", "gsc_queue_details"),
        ("ga4", "ga4_queue_details"),
        ("index", "index_queue_details"),
    ):
        sync_queue_reset(scope)
        sync_queue_seed(scope, targets)
        assert len(SYNC_STATE[key]) == n


def test_record_sync_rate_slot_via_limiter_on_granted() -> None:
    clear_sync_rate_slot_trackers()
    lim = PerMinuteRateLimiter(
        500,
        on_granted=lambda ts: record_sync_rate_slot("gsc", ts),
    )
    lim.acquire()
    lim.acquire()
    refresh_sync_rate_slots_window()
    assert SYNC_STATE["gsc_sync_slots_last_60s"] == 2


def test_start_sync_background_sets_running_before_worker_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: POST /api/sync must see ``running=True`` so the SPA enables sync-status polling."""
    from shopifyseo.dashboard_actions._sync import SYNC_STATE, start_sync_background

    class HeldThread(threading.Thread):
        def start(self) -> None:  # noqa: D401
            """Do not run the worker; state after prepare should already be visible."""

    monkeypatch.setattr(threading, "Thread", HeldThread)
    SYNC_STATE["running"] = False
    assert start_sync_background("/tmp/seo-sync-queue-test.db", "custom", ["shopify"], force_refresh=False)
    assert SYNC_STATE["running"] is True
    SYNC_STATE["running"] = False
