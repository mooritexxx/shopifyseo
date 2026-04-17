"""Hybrid sync ETA: live throughput from the current run plus rolling historical pace.

Samples (seconds per work unit) persist in ``service_settings`` under
``sync_eta_seconds_per_unit_v1``. The API exposes ``eta_seconds`` only while
``running`` is true; the UI shows a placeholder when the value is absent.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import time
from typing import Any

from shopifyseo.dashboard_google._auth import get_service_setting, set_service_setting

logger = logging.getLogger(__name__)

ETA_HISTORY_KEY = "sync_eta_seconds_per_unit_v1"
MAX_SAMPLES = 18
MIN_SPU = 0.03
MAX_SPU = 180.0
MAX_ETA_CAP = 6 * 3600
MIN_ITEMS_LIVE = 4
MIN_SECONDS_LIVE = 4.0
MIN_BLEND_DONE = 12
TRIVIAL_DURATION = 1.25
TRIVIAL_UNITS = 3


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def load_eta_history(conn: sqlite3.Connection) -> dict[str, list[float]]:
    raw = (get_service_setting(conn, ETA_HISTORY_KEY) or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[float]] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, list):
            nums = [float(x) for x in v if isinstance(x, (int, float)) and x > 0]
            out[k] = nums[-MAX_SAMPLES:]
    return out


def save_eta_history(conn: sqlite3.Connection, data: dict[str, list[float]]) -> None:
    set_service_setting(conn, ETA_HISTORY_KEY, json.dumps(data))


def record_sync_eta_sample(db_path: str, bucket: str, duration_s: float, units: int) -> None:
    if not bucket or duration_s <= 0:
        return
    if duration_s < TRIVIAL_DURATION and units < TRIVIAL_UNITS:
        return
    units = max(int(units), 1)
    spu = float(duration_s) / float(units)
    spu = max(MIN_SPU, min(MAX_SPU, spu))
    try:
        conn = _connect(db_path)
        try:
            hist = load_eta_history(conn)
            lst = hist.get(bucket, [])
            lst.append(spu)
            hist[bucket] = lst[-MAX_SAMPLES:]
            save_eta_history(conn, hist)
        finally:
            conn.close()
    except Exception:
        logger.warning("sync ETA sample persist failed", exc_info=True)


def eta_bucket_from_state(state: dict[str, Any]) -> str | None:
    stage = (state.get("stage") or "").strip().lower()
    if stage in ("starting", "idle", "complete", "cancelled", "error", ""):
        return None
    if stage in ("syncing_shopify", "syncing_product_images"):
        return "shopify"
    if stage.startswith("syncing_"):
        return "shopify"
    if stage == "refreshing_gsc":
        return "gsc"
    if stage == "refreshing_ga4":
        return "ga4"
    if stage == "refreshing_index":
        return "index"
    if stage == "refreshing_pagespeed":
        return "pagespeed"
    if stage == "updating_structured_seo":
        return "structured"
    return None


def sync_progress_pair(state: dict[str, Any]) -> tuple[int, int]:
    """Return (done, total) matching dashboard progress bar semantics."""
    active = (state.get("active_scope") or "").strip().lower()
    phase = (state.get("pagespeed_phase") or "").strip().lower()
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
    if active == "pagespeed" and phase == "queueing":
        qt = int(state.get("pagespeed_queue_total") or 0)
        qc = int(state.get("pagespeed_queue_completed") or 0)
        if qt > 0:
            return qc, qt
    return done, total


def units_for_record(state: dict[str, Any], bucket: str) -> int:
    done, total = sync_progress_pair(state)
    if bucket == "shopify":
        agg = (
            int(state.get("products_total") or 0)
            + int(state.get("collections_total") or 0)
            + int(state.get("pages_total") or 0)
            + int(state.get("blogs_total") or 0)
            + int(state.get("blog_articles_total") or 0)
            + int(state.get("images_total") or 0)
        )
        return max(agg, total, done, 1)
    if bucket == "structured":
        return 1
    return max(total, done, 1)


def record_scope_eta_segment(db_path: str, state: dict[str, Any], bucket: str) -> None:
    t0 = int(state.get("eta_segment_started_at") or 0)
    if t0 <= 0:
        return
    dur = max(time.time() - float(t0), 0.05)
    units = units_for_record(state, bucket)
    record_sync_eta_sample(db_path, bucket, dur, units)


def _median(nums: list[float]) -> float | None:
    if not nums:
        return None
    return float(statistics.median(nums))


def compute_sync_eta_seconds(state: dict[str, Any], db_path: str) -> int | None:
    if not state.get("running"):
        return None
    bucket = eta_bucket_from_state(state)
    if not bucket:
        return None
    done, total = sync_progress_pair(state)
    if total <= 0:
        return None
    remaining = total - done
    if remaining <= 0:
        return 0

    now = time.time()
    seg = int(state.get("eta_segment_started_at") or 0)
    stg = int(state.get("stage_started_at") or 0)
    rst = int(state.get("started_at") or 0)
    start_ts = seg or stg or rst
    if start_ts <= 0:
        return None
    elapsed = max(now - float(start_ts), 0.001)

    live_eta: float | None = None
    if done >= MIN_ITEMS_LIVE and elapsed >= MIN_SECONDS_LIVE and done > 0:
        rate = float(done) / elapsed
        live_eta = float(remaining) / max(rate, 1e-9)

    hist_eta: float | None = None
    med: float | None = None
    try:
        conn = _connect(db_path)
        try:
            hist = load_eta_history(conn)
            med = _median(hist.get(bucket, []))
        finally:
            conn.close()
    except Exception:
        med = None
    if med is not None:
        hist_eta = float(remaining) * float(med)

    eta: float | None = None
    if live_eta is not None and hist_eta is not None:
        w = min(1.0, float(done) / float(MIN_BLEND_DONE))
        eta = w * live_eta + (1.0 - w) * hist_eta
    elif live_eta is not None:
        eta = live_eta
    elif hist_eta is not None:
        eta = hist_eta

    if eta is None:
        return None
    eta = min(max(eta, 0.0), float(MAX_ETA_CAP))
    return int(max(0, round(eta)))
