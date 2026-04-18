"""Sync ETA: historical seconds-per-unit (per service / Shopify kind) plus non-Shopify hybrid.

Samples (seconds per work unit) persist in ``service_settings`` under
``sync_eta_seconds_per_unit_v1``. Shopify catalog uses per-kind keys
(``shopify_products``, …) and sums ``remaining_k × median_spu_k``. Other
scopes may blend live throughput with history. The API exposes ``eta_seconds``
only while ``running`` is true.
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
DEFAULT_SHOPIFY_FALLBACK_SPU = 3.0
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


def record_shopify_kind_eta(db_path: str, bucket: str, started_at: float, units: int) -> None:
    """Record seconds-per-unit for a Shopify sub-phase (wall clock since ``started_at``)."""
    dur = max(time.time() - float(started_at), 0.05)
    record_sync_eta_sample(db_path, bucket, dur, units)


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


SHOPIFY_ACTIVE_SCOPES = frozenset({"shopify", "products", "collections", "pages", "blogs"})

# (history bucket, synced key, total key)
SHOPIFY_ETA_KINDS: tuple[tuple[str, str, str], ...] = (
    ("shopify_products", "products_synced", "products_total"),
    ("shopify_collections", "collections_synced", "collections_total"),
    ("shopify_pages", "pages_synced", "pages_total"),
    ("shopify_blogs", "blogs_synced", "blogs_total"),
    ("shopify_blog_articles", "blog_articles_synced", "blog_articles_total"),
    ("shopify_images", "images_synced", "images_total"),
)


def shopify_aggregate_progress(state: dict[str, Any]) -> tuple[int, int]:
    """Sum catalog + image-cache units for Shopify progress bar and ETA."""
    pt = int(state.get("products_total") or 0)
    ct = int(state.get("collections_total") or 0)
    pgt = int(state.get("pages_total") or 0)
    bgt = int(state.get("blogs_total") or 0)
    bat = int(state.get("blog_articles_total") or 0)
    img_t = int(state.get("images_total") or 0)
    ps = int(state.get("products_synced") or 0)
    cs = int(state.get("collections_synced") or 0)
    pgs = int(state.get("pages_synced") or 0)
    bgs = int(state.get("blogs_synced") or 0)
    bas = int(state.get("blog_articles_synced") or 0)
    img_s = int(state.get("images_synced") or 0)
    total = pt + ct + pgt + bgt + bat + img_t
    done = ps + cs + pgs + bgs + bas + img_s
    return done, total


def _is_shopify_progress_state(state: dict[str, Any]) -> bool:
    active = (state.get("active_scope") or "").strip().lower()
    stage = (state.get("stage") or "").strip().lower()
    if active in SHOPIFY_ACTIVE_SCOPES:
        return True
    if stage in ("syncing_shopify", "syncing_product_images"):
        return True
    if stage.startswith("syncing_") and stage not in ("starting",):
        return True
    return False


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
    if active == "pagespeed" and phase == "queueing":
        qt = int(state.get("pagespeed_queue_total") or 0)
        qc = int(state.get("pagespeed_queue_completed") or 0)
        if qt > 0:
            return qc, qt
    if _is_shopify_progress_state(state):
        return shopify_aggregate_progress(state)
    total = int(state.get("total") or 0)
    done = int(state.get("done") or 0)
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


def _fallback_spu_from_history(hist: dict[str, list[float]]) -> float:
    all_vals = [x for xs in hist.values() for x in xs if isinstance(x, (int, float)) and x > 0]
    med = _median(all_vals)
    return float(med) if med is not None else DEFAULT_SHOPIFY_FALLBACK_SPU


def compute_shopify_eta_seconds_historical(state: dict[str, Any], db_path: str) -> int | None:
    """ETA ≈ sum_k remaining_k × median_spu_k for each Shopify kind."""
    try:
        conn = _connect(db_path)
        try:
            hist = load_eta_history(conn)
        finally:
            conn.close()
    except Exception:
        hist = {}

    fallback_spu = _fallback_spu_from_history(hist)
    total_eta = 0.0
    for bucket, sk, tk in SHOPIFY_ETA_KINDS:
        rem = max(0, int(state.get(tk) or 0) - int(state.get(sk) or 0))
        if rem <= 0:
            continue
        med = _median(hist.get(bucket, []))
        spu = float(med) if med is not None else fallback_spu
        spu = max(MIN_SPU, min(MAX_SPU, spu))
        total_eta += float(rem) * spu

    if total_eta <= 0:
        done0, total0 = shopify_aggregate_progress(state)
        if state.get("running") and total0 == 0 and done0 == 0:
            # Before discovery reports any totals: coarse legacy "shopify" samples from older runs.
            med = _median(hist.get("shopify", []))
            if med is not None:
                return int(min(MAX_ETA_CAP, max(45, med * 80)))
            return None
        return 0
    total_eta = min(total_eta, float(MAX_ETA_CAP))
    return int(max(0, round(total_eta)))


def compute_sync_eta_seconds(state: dict[str, Any], db_path: str) -> int | None:
    if not state.get("running"):
        return None
    bucket = eta_bucket_from_state(state)
    if not bucket:
        return None
    if bucket == "shopify":
        return compute_shopify_eta_seconds_historical(state, db_path)

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
