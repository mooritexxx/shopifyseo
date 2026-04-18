"""Tests for sync ETA run history and prediction."""
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from shopifyseo.dashboard_actions.sync_eta import (
    append_completed_sync_eta_run,
    compute_shopify_eta_seconds_historical,
    compute_sync_eta_seconds,
    load_run_history,
    shopify_aggregate_progress,
    sync_progress_pair,
    weighted_spu_for_module,
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


def _set_runs(conn: sqlite3.Connection, runs: list) -> None:
    from shopifyseo.dashboard_actions.sync_eta import ETA_RUN_HISTORY_KEY

    set_service_setting(conn, ETA_RUN_HISTORY_KEY, json.dumps({"runs": runs}))


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


def test_weighted_spu_for_module():
    runs = [
        {"modules": {"index": {"duration_s": 60.0, "units": 30}}},
        {"modules": {"index": {"duration_s": 90.0, "units": 30}}},
    ]
    spu = weighted_spu_for_module(runs, "index")
    assert spu is not None
    assert abs(spu - 2.5) < 0.01  # 150/60


def test_compute_sync_eta_index_with_runs():
    db_path = _db_with_settings()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _set_runs(conn, [{"modules": {"index": {"duration_s": 100.0, "units": 50}}}])
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
    assert 180 <= eta <= 260


def test_not_running_no_eta():
    db_path = _db_with_settings()
    state = {
        "running": False,
        "stage": "idle",
        "active_scope": "",
        "total": 10,
        "done": 5,
    }
    assert compute_sync_eta_seconds(state, db_path) is None


def test_shopify_aggregate_progress_includes_blog_articles():
    state = {
        "products_total": 10,
        "collections_total": 5,
        "pages_total": 2,
        "blogs_total": 1,
        "blog_articles_total": 7,
        "images_total": 3,
        "products_synced": 10,
        "collections_synced": 5,
        "pages_synced": 2,
        "blogs_synced": 1,
        "blog_articles_synced": 7,
        "images_synced": 0,
    }
    assert shopify_aggregate_progress(state) == (25, 28)


def test_compute_shopify_eta_weighted_sum():
    db_path = _db_with_settings()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _set_runs(
        conn,
        [
            {
                "modules": {
                    "shopify_products": {"duration_s": 20.0, "units": 10},
                    "shopify_blog_articles": {"duration_s": 10.0, "units": 10},
                }
            }
        ],
    )
    conn.close()
    state = {
        "products_synced": 0,
        "products_total": 10,
        "blog_articles_synced": 0,
        "blog_articles_total": 5,
        "collections_synced": 0,
        "collections_total": 0,
        "pages_synced": 0,
        "pages_total": 0,
        "blogs_synced": 0,
        "blogs_total": 0,
        "images_synced": 0,
        "images_total": 0,
    }
    eta = compute_shopify_eta_seconds_historical(state, db_path)
    assert eta == 25


def test_append_trims_to_ten_runs():
    from shopifyseo.dashboard_actions._state import SYNC_STATE

    db_path = _db_with_settings()
    prev_scope = SYNC_STATE.get("scope")
    prev_scopes = list(SYNC_STATE.get("selected_scopes") or [])
    prev_modules = SYNC_STATE.get("eta_run_modules")
    try:
        SYNC_STATE["scope"] = "custom"
        SYNC_STATE["selected_scopes"] = ["shopify"]

        for i in range(12):
            SYNC_STATE["eta_run_modules"] = {"gsc": {"duration_s": float(i + 1), "units": 1}}
            append_completed_sync_eta_run(db_path)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        runs = load_run_history(conn)
        conn.close()
        assert len(runs) == 10
        assert runs[-1]["modules"]["gsc"]["duration_s"] == 12.0
        assert runs[0]["modules"]["gsc"]["duration_s"] == 3.0
    finally:
        SYNC_STATE["scope"] = prev_scope
        SYNC_STATE["selected_scopes"] = prev_scopes
        SYNC_STATE["eta_run_modules"] = prev_modules if isinstance(prev_modules, dict) else {}


def test_normalize_sync_scopes_follows_pipeline_order_not_ui_toggle_order():
    from shopifyseo.dashboard_actions._sync import SYNC_PIPELINE_ORDER, _normalize_sync_scopes

    scope, scopes = _normalize_sync_scopes("custom", ["structured", "shopify", "pagespeed", "gsc"])
    assert scope == "custom"
    assert scopes == ["shopify", "gsc", "pagespeed", "structured"]
    assert scopes == [s for s in SYNC_PIPELINE_ORDER if s in set(scopes)]
