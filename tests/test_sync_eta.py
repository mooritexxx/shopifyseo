"""Tests for hybrid sync ETA helpers."""
import json
import sqlite3
import tempfile
import time
from pathlib import Path

import pytest

from shopifyseo.dashboard_actions.sync_eta import (
    compute_sync_eta_seconds,
    load_eta_history,
    record_sync_eta_sample,
    sync_progress_pair,
)
from shopifyseo.dashboard_google._auth import set_service_setting


def _db_with_settings() -> str:
    path = Path(tempfile.mkdtemp()) / "t.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE service_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()
    return str(path)


def test_sync_progress_pair_pagespeed_queueing():
    state = {
        "active_scope": "pagespeed",
        "pagespeed_phase": "queueing",
        "pagespeed_queue_total": 100,
        "pagespeed_queue_completed": 30,
        "total": 100,
        "done": 30,
    }
    assert sync_progress_pair(state) == (30, 100)


def test_compute_eta_historical_cold_start():
    db_path = _db_with_settings()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    hist = {"index": [2.0, 2.0, 2.0]}
    set_service_setting(conn, "sync_eta_seconds_per_unit_v1", json.dumps(hist))
    conn.close()

    now = int(time.time())
    state = {
        "running": True,
        "stage": "refreshing_index",
        "active_scope": "index",
        "pagespeed_phase": "",
        "total": 100,
        "done": 0,
        "started_at": now - 10,
        "stage_started_at": now - 10,
        "eta_segment_started_at": now - 10,
    }
    eta = compute_sync_eta_seconds(state, db_path)
    assert eta is not None
    assert 180 <= eta <= 220


def test_compute_eta_live_blend():
    db_path = _db_with_settings()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    set_service_setting(conn, "sync_eta_seconds_per_unit_v1", json.dumps({"index": [10.0]}))
    conn.close()

    now = int(time.time())
    state = {
        "running": True,
        "stage": "refreshing_index",
        "active_scope": "index",
        "pagespeed_phase": "",
        "total": 100,
        "done": 50,
        "started_at": now - 100,
        "stage_started_at": now - 100,
        "eta_segment_started_at": now - 100,
    }
    eta = compute_sync_eta_seconds(state, db_path)
    assert eta is not None
    assert eta < 400


def test_record_and_load_history_roundtrip():
    db_path = _db_with_settings()
    record_sync_eta_sample(db_path, "gsc", 60.0, 30)
    record_sync_eta_sample(db_path, "gsc", 90.0, 30)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    h = load_eta_history(conn)
    conn.close()
    assert "gsc" in h
    assert len(h["gsc"]) == 2


@pytest.mark.parametrize("running,expect_none", [(False, True), (True, False)])
def test_not_running_no_eta(running, expect_none):
    db_path = _db_with_settings()
    state = {
        "running": running,
        "stage": "refreshing_index" if running else "idle",
        "active_scope": "index" if running else "",
        "total": 10,
        "done": 5,
        "started_at": int(time.time()) - 50,
        "stage_started_at": int(time.time()) - 50,
        "eta_segment_started_at": int(time.time()) - 50,
    }
    eta = compute_sync_eta_seconds(state, db_path)
    if expect_none:
        assert eta is None
    else:
        assert eta is not None


def test_normalize_sync_scopes_follows_pipeline_order_not_ui_toggle_order():
    from shopifyseo.dashboard_actions._sync import SYNC_PIPELINE_ORDER, _normalize_sync_scopes

    scope, scopes = _normalize_sync_scopes("custom", ["structured", "shopify", "pagespeed", "gsc"])
    assert scope == "custom"
    assert scopes == ["shopify", "gsc", "pagespeed", "structured"]
    assert scopes == [s for s in SYNC_PIPELINE_ORDER if s in set(scopes)]
