"""Sync operations: Shopify catalog, Search Console, GA4, index status, PageSpeed.

PageSpeed bulk logic lives in :mod:`._sync_pagespeed` and is re-exported here
(``bulk_refresh_pagespeed`` / ``_pagespeed_error_detail_for_ui``) for callers
that resolve these symbols via the ``_sync`` module path (including tests).
"""
import logging
import sqlite3
import threading
import time
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logger = logging.getLogger(__name__)

from .. import dashboard_google as dg
from .. import dashboard_queries as dq
from ..dashboard_status import index_status_bucket_from_strings
from ..dashboard_store import (
    refresh_ga4_signal_data_for_objects,
    refresh_gsc_signal_data_for_objects,
    refresh_index_signal_data_for_objects,
    refresh_structured_seo_data,
)
from ..shopify_catalog_sync import (
    sync_blogs,
    sync_collections,
    sync_pages,
    sync_products,
)
from ..catalog_image_work import count_catalog_image_urls_discover
from ..shopify_catalog_sync.discovery import discover_shopify_catalog
from ..shopify_image_cache import count_catalog_images_for_cache, warm_product_image_cache
from ._rpm_limiter import PerMinuteRateLimiter
from ._state import (
    GA4_SYNC_RATE_LIMIT_PER_MINUTE,
    GA4_SYNC_WORKERS,
    GSC_SYNC_RATE_LIMIT_PER_MINUTE,
    GSC_SYNC_WORKERS,
    IMAGE_CACHE_WORKERS,
    INDEX_SYNC_RATE_LIMIT_PER_MINUTE,
    INDEX_SYNC_WORKERS,
    SYNC_LOCK,
    SYNC_STATE,
    _db_connect_for_actions,
    _raise_if_sync_cancelled,
    _sync_current,
    clear_last_error,
    append_sync_event,
    clear_pagespeed_http_call_tracker,
    clear_sync_rate_slot_trackers,
    record_last_error,
    record_sync_rate_slot,
)
from ._sync_pagespeed import (
    _pagespeed_bulk_max_inflight,
    _pagespeed_error_detail_for_ui,
    _pagespeed_target_counts,
    bulk_refresh_pagespeed,
)
from ._sync_queue import (
    catalog_sync_row_key,
    sync_queue_mark_done,
    sync_queue_mark_running,
    sync_queue_reset,
    sync_queue_reset_all,
    sync_queue_seed,
)
from ..exceptions import SyncCancelledError

# Canonical execution order (matches sidebar / sync UI). Custom selections are always reordered to this.
SYNC_PIPELINE_ORDER = ["shopify", "gsc", "ga4", "index", "pagespeed"]

SHOPIFY_ACTIVE_SCOPES = frozenset({"shopify", "products", "collections", "pages", "blogs"})


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


def shopify_aggregate_progress(state: dict[str, Any]) -> tuple[int, int]:
    """Sum catalog + image-cache units for Shopify progress bar."""
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


def sync_progress_pair(state: dict[str, Any]) -> tuple[int, int]:
    """Return (done, total) matching dashboard progress bar semantics (tests / diagnostics)."""
    active = (state.get("active_scope") or "").strip().lower()
    phase = (state.get("pagespeed_phase") or "").strip().lower()
    if active == "pagespeed" and phase == "queueing":
        baseline = int(state.get("pagespeed_queue_baseline") or 0)
        if baseline > 0:
            refreshed = int(state.get("pagespeed_refreshed") or 0)
            return min(refreshed, baseline), max(baseline, 1)
        qt = int(state.get("pagespeed_queue_total") or 0)
        qc = int(state.get("pagespeed_queue_completed") or 0)
        if qt > 0:
            return qc, qt
    if _is_shopify_progress_state(state):
        return shopify_aggregate_progress(state)
    if active == "gsc":
        gt = int(state.get("gsc_progress_total") or 0)
        gd = int(state.get("gsc_progress_done") or 0)
        if gt > 0:
            return gd, gt
        return 0, 0
    if active == "ga4":
        gt = int(state.get("ga4_progress_total") or 0)
        gd = int(state.get("ga4_progress_done") or 0)
        if gt > 0:
            return gd, gt
        return 0, 0
    if active == "index":
        it = int(state.get("index_progress_total") or 0)
        idn = int(state.get("index_progress_done") or 0)
        if it > 0:
            return idn, it
        return 0, 0
    return 0, 0

# ---------------------------------------------------------------------------
# Sync state helpers
# ---------------------------------------------------------------------------


def _reconcile_catalog_signal_columns_from_cache(db_path: str, *, after_scope: str) -> None:
    """Rewrite denormalized GSC / GA4 / index / PageSpeed columns for every catalog row from local cache."""
    _raise_if_sync_cancelled()
    label = {
        "shopify": "Shopify catalog",
        "gsc": "Search Console",
        "ga4": "GA4",
        "index": "index status",
        "pagespeed": "PageSpeed",
    }.get(after_scope, after_scope)
    _sync_current(f"Merging cached SEO signals into catalog (after {label})…")
    conn = _db_connect_for_actions(db_path)
    try:
        refresh_structured_seo_data(conn)
    finally:
        conn.close()


def _reset_sync_progress(scope: str, selected_scopes: list[str] | None = None) -> None:
    started_at = int(time.time())
    clear_pagespeed_http_call_tracker()
    clear_sync_rate_slot_trackers()
    sync_queue_reset_all()
    SYNC_STATE.update(
        {
            "running": True,
            "scope": scope,
            "selected_scopes": list(selected_scopes or []),
            "force_refresh": False,
            "started_at": started_at,
            "finished_at": 0,
            "stage_started_at": started_at,
            "stage": "starting",
            "stage_label": "Preparing sync",
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
            "gsc_queue_details": [],
            "ga4_queue_details": [],
            "index_queue_details": [],
            "shopify_queue_details": [],
            "gsc_sync_slots_last_60s": 0,
            "ga4_sync_slots_last_60s": 0,
            "index_sync_slots_last_60s": 0,
            "sync_events": [],
            "pagespeed_error_details": [],
            "pagespeed_queue_details": [],
            "pagespeed_queue_meta": {},
            "pagespeed_queue_baseline": 0,
            "pagespeed_error_seq": 0,
            "cancel_requested": False,
        }
    )


def _set_sync_stage(
    *,
    stage: str,
    label: str,
    active_scope: str = "",
    step_index: int = 0,
    step_total: int = 0,
    current: str | None = None,
) -> None:
    prev_stage = SYNC_STATE.get("stage")
    prev_scope = SYNC_STATE.get("active_scope")
    stage_changed = stage != prev_stage
    scope_changed = (active_scope or "") != (prev_scope or "")
    SYNC_STATE["stage"] = stage
    SYNC_STATE["stage_label"] = label
    SYNC_STATE["active_scope"] = active_scope
    SYNC_STATE["step_index"] = step_index
    SYNC_STATE["step_total"] = step_total
    if current is not None:
        _sync_current(current)
    elif stage_changed or scope_changed:
        append_sync_event((active_scope or "sync")[:12], label)
    if stage_changed or scope_changed:
        SYNC_STATE["stage_started_at"] = int(time.time())


def _recompute_shopify_scoped_progress() -> None:
    """Shopify phase progress (catalog + image cache); isolated from other pipeline steps."""
    done, total = shopify_aggregate_progress(SYNC_STATE)
    SYNC_STATE["shopify_progress_done"] = done
    SYNC_STATE["shopify_progress_total"] = total


def _warm_product_image_cache_safe(db_path: str) -> dict[str, int] | None:
    """Download catalog image files into shopify_image_cache/; non-fatal on failure."""
    try:
        _set_sync_stage(
            stage="syncing_shopify",
            label="Syncing Shopify",
            active_scope=SYNC_STATE.get("active_scope") or "",
            step_index=int(SYNC_STATE.get("step_index") or 0),
            step_total=int(SYNC_STATE.get("step_total") or 0),
            current="Shopify: caching catalog images (local)…",
        )

        def _progress(done: int, total: int) -> None:
            _raise_if_sync_cancelled()
            SYNC_STATE["images_synced"] = done
            SYNC_STATE["images_total"] = total
            _sync_current(f"Shopify: catalog images {done}/{total}")
            _recompute_shopify_scoped_progress()

        stats = warm_product_image_cache(
            Path(db_path),
            max_workers=IMAGE_CACHE_WORKERS,
            progress_callback=_progress,
            queue_scope="shopify",
            force_refresh=bool(SYNC_STATE.get("force_refresh")),
        )
        return {
            "downloaded": int(stats.get("downloaded") or 0),
            "skipped": int(stats.get("skipped") or 0),
            "errors": int(stats.get("errors") or 0),
            "pruned": int(stats.get("pruned") or 0),
        }
    except SyncCancelledError:
        raise
    except Exception:
        logger.warning("Product image cache warm failed", exc_info=True)
        return None


def _image_cache_summary_suffix(cache: dict[str, int] | None) -> str:
    if not cache:
        return ""
    d = int(cache.get("downloaded") or 0)
    s = int(cache.get("skipped") or 0)
    p = int(cache.get("pruned") or 0)
    e = int(cache.get("errors") or 0)
    tail = f"; images cached: {d} new, {s} unchanged, {p} pruned"
    if e:
        tail += f", {e} errors"
    return tail


def _normalize_sync_scopes(scope: str, selected_scopes: list[str] | None = None) -> tuple[str, list[str]]:
    pipeline_order = SYNC_PIPELINE_ORDER
    if selected_scopes:
        selected_set: set[str] = set()
        for item in selected_scopes:
            token = str(item or "").strip().lower()
            if token in pipeline_order:
                selected_set.add(token)
        # Always follow sidebar order, never the order the user toggled services in the UI.
        normalized = [s for s in pipeline_order if s in selected_set]
        return ("all" if normalized == pipeline_order else "custom"), normalized
    if scope in pipeline_order:
        return scope, [scope]
    if scope in {"products", "collections", "pages", "blogs"}:
        return scope, [scope]
    return "all", list(pipeline_order)


def _sync_label(scope: str, selected_scopes: list[str]) -> str:
    if scope in {"products", "collections", "pages", "blogs"}:
        return scope
    pipeline_order = SYNC_PIPELINE_ORDER
    if selected_scopes == pipeline_order:
        return "all"
    if selected_scopes:
        return "custom"
    return scope or "all"


# ---------------------------------------------------------------------------
# Target / catalog helpers
# ---------------------------------------------------------------------------


def _all_object_targets(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    targets: list[tuple[str, str, str]] = []
    for row in dq.fetch_all_products(conn):
        targets.append(("product", row["handle"], dq.object_url("product", row["handle"])))
    for row in dq.fetch_all_collections(conn):
        targets.append(("collection", row["handle"], dq.object_url("collection", row["handle"])))
    for row in dq.fetch_all_pages(conn):
        targets.append(("page", row["handle"], dq.object_url("page", row["handle"])))
    for row in dq.fetch_all_blog_articles(conn):
        ch = dq.blog_article_composite_handle(row["blog_handle"], row["handle"])
        targets.append(("blog_article", ch, dq.object_url("blog_article", ch)))
    return targets


def _normalize_catalog_path(path: str) -> str:
    """Canonical path segment for matching GA4 ``pagePathPlusQueryString`` to storefront URLs."""
    p = unquote((path or "").strip())
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    if p != "/" and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _ga4_dimension_path_keys(raw_dim: str) -> list[str]:
    """Candidate normalized paths from a GA dimension (may be path-only, full URL, or include query)."""
    raw = unquote((raw_dim or "").strip())
    if not raw:
        return []
    if raw.startswith("http://") or raw.startswith("https://"):
        path = urlparse(raw).path or "/"
    else:
        path = raw.split("?")[0].split("#")[0]
    norm = _normalize_catalog_path(path)
    if not norm:
        return []
    keys = [norm, norm + "/"]
    if norm != "/":
        stripped = norm.rstrip("/")
        if stripped and stripped != norm:
            keys.append(stripped)
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _catalog_targets_by_path(conn: sqlite3.Connection) -> dict[str, tuple[str, str, str]]:
    """Map normalized URL path → first catalog (object_type, handle, canonical_url)."""
    by_path: dict[str, tuple[str, str, str]] = {}
    for kind, handle, url in _all_object_targets(conn):
        norm = _normalize_catalog_path(urlparse(url).path)
        if not norm:
            continue
        for key in (norm, norm + "/", norm.rstrip("/") if norm != "/" else norm):
            if key and key not in by_path:
                by_path[key] = (kind, handle, url)
    return by_path


def _storefront_url_for_ga_dimension(base: str, raw_dim: str) -> str:
    """Build an absolute storefront URL from GA ``pagePathPlusQueryString`` (or full URL)."""
    raw = (raw_dim or "").strip()
    if not raw:
        return (base or "").rstrip("/") + "/" if base else ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    path = unquote(raw)
    if not path.startswith("/"):
        path = "/" + path
    base_clean = (base or "").strip().rstrip("/")
    if not base_clean:
        return path
    return urljoin(f"{base_clean}/", path.lstrip("/"))


def _catalog_row_index_bucket(index_status: str | None, index_coverage: str | None) -> str:
    return index_status_bucket_from_strings(
        (index_status or "").strip(),
        (index_coverage or "").strip(),
    )


def _index_inspection_targets(conn: sqlite3.Connection, *, force_refresh: bool) -> tuple[list[tuple[str, str, str]], int]:
    """URLs to run URL Inspection on, and how many were skipped as already indexed (only when not force_refresh)."""
    if force_refresh:
        # Resolve via package namespace so tests can monkeypatch da._all_object_targets.
        import sys as _sys
        _pkg = _sys.modules.get("shopifyseo.dashboard_actions")
        _aot = getattr(_pkg, "_all_object_targets", None) if _pkg else None
        return (_aot or _all_object_targets)(conn), 0
    skipped_indexed = 0
    out: list[tuple[str, str, str]] = []
    for row in dq.fetch_all_products(conn):
        if _catalog_row_index_bucket(row["index_status"], row["index_coverage"]) == "indexed":
            skipped_indexed += 1
            continue
        out.append(("product", row["handle"], dq.object_url("product", row["handle"])))
    for row in dq.fetch_all_collections(conn):
        if _catalog_row_index_bucket(row["index_status"], row["index_coverage"]) == "indexed":
            skipped_indexed += 1
            continue
        out.append(("collection", row["handle"], dq.object_url("collection", row["handle"])))
    for row in dq.fetch_all_pages(conn):
        if _catalog_row_index_bucket(row["index_status"], row["index_coverage"]) == "indexed":
            skipped_indexed += 1
            continue
        out.append(("page", row["handle"], dq.object_url("page", row["handle"])))
    for row in dq.fetch_all_blog_articles(conn):
        if _catalog_row_index_bucket(row["index_status"], row["index_coverage"]) == "indexed":
            skipped_indexed += 1
            continue
        ch = dq.blog_article_composite_handle(row["blog_handle"], row["handle"])
        out.append(("blog_article", ch, dq.object_url("blog_article", ch)))
    return out, skipped_indexed


# ---------------------------------------------------------------------------
# Bulk sync operations
# ---------------------------------------------------------------------------


def bulk_refresh_search_console(db_path: str, throttle_seconds: float = 0.1, force_refresh: bool = False) -> dict:
    conn = _db_connect_for_actions(db_path)
    summary = {
        "considered": 0,
        "refreshed": 0,
        "errors": 0,
        "skipped_fresh": 0,
        "summary_pages": 0,
        "summary_queries": 0,
        "eligible": 0,
        "queue_total": 0,
    }
    try:
        touched_targets: list[tuple[str, str]] = []
        summary_payload = dg.get_search_console_summary_cached(conn, refresh=True)
        summary["summary_pages"] = len(summary_payload.get("pages", []))
        summary["summary_queries"] = len(summary_payload.get("queries", []))
        SYNC_STATE["gsc_summary_pages"] = summary["summary_pages"]
        SYNC_STATE["gsc_summary_queries"] = summary["summary_queries"]

        site_url = (summary_payload.get("site_url") or dg.get_service_setting(conn, "search_console_site") or "").strip()
        if site_url:
            dg.refresh_gsc_property_breakdowns_for_site(conn, site_url)

        all_targets = _all_object_targets(conn)
        summary["eligible"] = len(all_targets)
        SYNC_STATE["gsc_eligible_total"] = len(all_targets)
        if force_refresh:
            queue = list(all_targets)
            precheck_skipped = 0
        else:
            queue = [
                (kind, handle, url)
                for kind, handle, url in all_targets
                if dg.gsc_url_detail_needs_refresh(conn, url, site_url=site_url, gsc_period="mtd")
            ]
            precheck_skipped = max(len(all_targets) - len(queue), 0)
        summary["skipped_fresh"] = precheck_skipped
        summary["queue_total"] = len(queue)
        SYNC_STATE["gsc_precheck_skipped"] = precheck_skipped
        SYNC_STATE["gsc_skipped"] = precheck_skipped
        gsc_pt = max(len(queue), 1)
        SYNC_STATE["gsc_progress_total"] = gsc_pt
        SYNC_STATE["gsc_progress_done"] = 0 if queue else gsc_pt
        sync_queue_reset("gsc")
        sync_queue_seed("gsc", queue)
        access_token = dg.get_google_access_token(conn)
        rate_limiter = PerMinuteRateLimiter(
            GSC_SYNC_RATE_LIMIT_PER_MINUTE,
            on_granted=lambda ts: record_sync_rate_slot("gsc", ts),
        )
        progress_lock = threading.Lock()

        def _run_gsc_target(kind: str, handle: str, url: str) -> str:
            _raise_if_sync_cancelled()
            rk = catalog_sync_row_key(kind, handle, url)
            ok = False
            err_msg: str | None = None
            worker_conn = _db_connect_for_actions(db_path)
            try:
                rate_limiter.acquire(_raise_if_sync_cancelled)
                _raise_if_sync_cancelled()
                sync_queue_mark_running("gsc", rk)
                dg.get_search_console_url_detail(
                    worker_conn,
                    url,
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                    site_url_override=site_url,
                    access_token_override=access_token,
                    gsc_period="mtd",
                )
                ok = True
                return "refreshed"
            except Exception as exc:
                err_msg = str(exc) or "request failed"
                raise
            finally:
                worker_conn.close()
                sync_queue_mark_done("gsc", rk, ok, err_msg, pop_completed=ok)

        with ThreadPoolExecutor(max_workers=GSC_SYNC_WORKERS) as executor:
            future_to_target = {
                executor.submit(_run_gsc_target, kind, handle, url): (kind, handle)
                for kind, handle, url in queue
            }

            for future in as_completed(future_to_target):
                kind, handle = future_to_target[future]
                _raise_if_sync_cancelled()
                summary["considered"] += 1
                _sync_current(f"Search Console: {kind}:{handle}")
                try:
                    future.result()
                    summary["refreshed"] += 1
                    SYNC_STATE["gsc_refreshed"] = summary["refreshed"]
                    touched_targets.append((kind, handle))
                except Exception:
                    summary["errors"] += 1
                    SYNC_STATE["gsc_errors"] = summary["errors"]
                    touched_targets.append((kind, handle))
                with progress_lock:
                    SYNC_STATE["gsc_progress_done"] = summary["refreshed"] + summary["errors"]
        _raise_if_sync_cancelled()
        if touched_targets:
            refresh_gsc_signal_data_for_objects(conn, touched_targets)
    finally:
        try:
            dg.delete_search_console_overview_timeseries_only(conn)
        except Exception:
            logger.exception("Failed to invalidate GSC overview timeseries cache after bulk GSC sync")
        conn.close()
    return summary


def refresh_ga4_summary(db_path: str, force_refresh: bool = False) -> dict:
    conn = _db_connect_for_actions(db_path)
    summary = {
        "considered": 0,
        "refreshed": 0,
        "errors": 0,
        "skipped_fresh": 0,
        "rows": 0,
        "url_errors": 0,
        "eligible": 0,
        "queue_total": 0,
    }
    try:
        payload = dg.get_ga4_summary(conn, refresh=force_refresh or True)
        page_rows: list = list(payload.get("page_rows") or [])
        summary["rows"] = len(page_rows)
        SYNC_STATE["ga4_rows"] = summary["rows"]

        base = dq._base_store_url(conn)
        by_path = _catalog_targets_by_path(conn)
        work: list[tuple[str, str, str]] = []
        for row in page_rows:
            raw_dim = dg.ga4_report_page_path_from_row(row)
            kind, handle, url = "", "", ""
            for key in _ga4_dimension_path_keys(raw_dim):
                hit = by_path.get(key)
                if hit:
                    kind, handle, url = hit
                    break
            if not url:
                url = _storefront_url_for_ga_dimension(base, raw_dim)
            work.append((kind, handle, url))

        n = len(work)
        summary["eligible"] = n
        if force_refresh:
            queue = list(work)
            precheck_skipped = 0
        else:
            queue = [(kind, handle, url) for kind, handle, url in work if dg.ga4_url_cache_stale(conn, url)]
            precheck_skipped = max(n - len(queue), 0)
        summary["skipped_fresh"] = precheck_skipped
        summary["queue_total"] = len(queue)
        SYNC_STATE["ga4_precheck_skipped"] = precheck_skipped
        qn = len(queue)
        ga4_pt = max(qn, 1)
        SYNC_STATE["ga4_progress_total"] = ga4_pt
        SYNC_STATE["ga4_progress_done"] = 0

        sync_queue_reset("ga4")
        sync_queue_seed("ga4", queue)
        rate_limiter = PerMinuteRateLimiter(
            GA4_SYNC_RATE_LIMIT_PER_MINUTE,
            on_granted=lambda ts: record_sync_rate_slot("ga4", ts),
        )

        def _run_ga4_target(kind: str, handle: str, url: str) -> str:
            _raise_if_sync_cancelled()
            rk = catalog_sync_row_key(kind, handle, url)
            ok = False
            err_msg: str | None = None
            worker_conn = _db_connect_for_actions(db_path)
            try:
                rate_limiter.acquire(_raise_if_sync_cancelled)
                _raise_if_sync_cancelled()
                sync_queue_mark_running("ga4", rk)
                try:
                    dg.get_ga4_url_detail(
                        worker_conn,
                        url,
                        refresh=True,
                        object_type=kind,
                        object_handle=handle,
                    )
                except Exception as exc:
                    logger.warning("GA4 per-URL fetch failed for %s %s: %s", kind or "(path)", handle or url, exc)
                    err_msg = str(exc) or "request failed"
                    return "error"
                ok = True
                return "refreshed"
            finally:
                worker_conn.close()
                sync_queue_mark_done("ga4", rk, ok, err_msg, pop_completed=ok)

        if qn == 0:
            SYNC_STATE["ga4_progress_done"] = 1
        else:
            with ThreadPoolExecutor(max_workers=GA4_SYNC_WORKERS) as executor:
                future_to_target = {
                    executor.submit(_run_ga4_target, kind, handle, url): (kind, handle)
                    for kind, handle, url in queue
                }

                for future in as_completed(future_to_target):
                    _raise_if_sync_cancelled()
                    summary["considered"] += 1
                    try:
                        result = future.result()
                        if result == "refreshed":
                            summary["refreshed"] += 1
                        else:
                            summary["errors"] += 1
                    except Exception as exc:
                        logger.warning("GA4 target worker failed: %s", exc)
                        summary["errors"] += 1
                    SYNC_STATE["ga4_progress_done"] = summary["refreshed"] + summary["errors"]

        signal_targets: list[tuple[str, str]] = []
        seen_sig: set[tuple[str, str]] = set()
        for kind, handle, _url in work:
            if kind and handle and (kind, handle) not in seen_sig:
                seen_sig.add((kind, handle))
                signal_targets.append((kind, handle))
        refresh_ga4_signal_data_for_objects(conn, signal_targets)
        summary["url_errors"] = summary["errors"]
        SYNC_STATE["ga4_url_errors"] = summary["errors"]
        SYNC_STATE["ga4_refreshed"] = summary["refreshed"]
        if summary["errors"]:
            logger.warning("GA4 per-URL sync completed with %s URL error(s)", summary["errors"])
        return summary
    except Exception:
        SYNC_STATE["ga4_errors"] += 1
        raise
    finally:
        conn.close()


def bulk_refresh_index_status(db_path: str, throttle_seconds: float = 0.1, force_refresh: bool = False) -> dict:
    conn = _db_connect_for_actions(db_path)
    summary = {
        "considered": 0,
        "refreshed": 0,
        "errors": 0,
        "skipped_fresh": 0,
        "skipped_indexed": 0,
    }
    try:
        targets, skipped_indexed = _index_inspection_targets(conn, force_refresh=force_refresh)
        summary["skipped_indexed"] = skipped_indexed
        SYNC_STATE["index_skipped"] = skipped_indexed
        touched_targets: list[tuple[str, str]] = []
        idx_pt = max(len(targets), 1)
        SYNC_STATE["index_progress_total"] = idx_pt
        SYNC_STATE["index_progress_done"] = 0
        sync_queue_reset("index")
        sync_queue_seed("index", list(targets))
        rate_limiter = PerMinuteRateLimiter(
            INDEX_SYNC_RATE_LIMIT_PER_MINUTE,
            on_granted=lambda ts: record_sync_rate_slot("index", ts),
        )
        progress_lock = threading.Lock()

        def _run_index_target(kind: str, handle: str, url: str) -> None:
            _raise_if_sync_cancelled()
            rk = catalog_sync_row_key(kind, handle, url)
            ok = False
            err_msg: str | None = None
            worker_conn: sqlite3.Connection | None = None
            rate_limiter.acquire(_raise_if_sync_cancelled)
            _raise_if_sync_cancelled()
            sync_queue_mark_running("index", rk)
            try:
                worker_conn = _db_connect_for_actions(db_path)
                dg.get_url_inspection(worker_conn, url, refresh=True, object_type=kind, object_handle=handle)
                ok = True
            except Exception as exc:
                err_msg = str(exc) or "request failed"
                raise
            finally:
                if worker_conn is not None:
                    worker_conn.close()
                sync_queue_mark_done("index", rk, ok, err_msg, pop_completed=ok)

        with ThreadPoolExecutor(max_workers=INDEX_SYNC_WORKERS) as executor:
            future_to_target = {
                executor.submit(_run_index_target, kind, handle, url): (kind, handle)
                for kind, handle, url in targets
            }

            for future in as_completed(future_to_target):
                kind, handle = future_to_target[future]
                _raise_if_sync_cancelled()
                summary["considered"] += 1
                touched_targets.append((kind, handle))
                _sync_current(f"Index: {kind}:{handle}")
                try:
                    future.result()
                    summary["refreshed"] += 1
                    SYNC_STATE["index_refreshed"] = summary["refreshed"]
                except Exception:
                    summary["errors"] += 1
                    SYNC_STATE["index_errors"] = summary["errors"]
                with progress_lock:
                    SYNC_STATE["index_progress_done"] = summary["considered"]
        _raise_if_sync_cancelled()
        if touched_targets:
            refresh_index_signal_data_for_objects(conn, touched_targets)
    finally:
        conn.close()
    return summary


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


def _run_selected_sync_steps(db_path: str, selected_scopes: list[str], force_refresh: bool = False) -> dict:
    result: dict = {}
    total_steps = len(selected_scopes)

    def _shopify_progress(kind: str, done: int, total: int) -> None:
        _raise_if_sync_cancelled()
        label_map = {
            "products": "products",
            "collections": "collections",
            "pages": "pages",
            "blogs": "blogs",
            "blog_articles": "blog_articles",
        }
        prefix = label_map.get(kind, kind)
        SYNC_STATE[f"{prefix}_synced"] = done
        SYNC_STATE[f"{prefix}_total"] = total
        if kind == "blog_articles":
            _sync_current(f"Shopify blog articles: {done}/{total}")
        else:
            _sync_current(f"Shopify {prefix}: {done}/{total}")
        _recompute_shopify_scoped_progress()

    for index, selected_scope in enumerate(selected_scopes, start=1):
        _raise_if_sync_cancelled()
        if selected_scope == "shopify":
            _set_sync_stage(
                stage="syncing_shopify",
                label="Discovering catalog…",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Counting products, collections, pages, and blogs…",
            )

            def _discovery_progress(kind: str, count: int) -> None:
                _raise_if_sync_cancelled()
                if kind == "products":
                    SYNC_STATE["products_total"] = count
                elif kind == "collections":
                    SYNC_STATE["collections_total"] = count
                elif kind == "pages":
                    SYNC_STATE["pages_total"] = count
                elif kind == "blogs":
                    SYNC_STATE["blogs_total"] = count
                elif kind == "blog_articles":
                    SYNC_STATE["blog_articles_total"] = count
                _recompute_shopify_scoped_progress()

            disc = discover_shopify_catalog(
                50, cancel_check=_raise_if_sync_cancelled, progress_callback=_discovery_progress
            )
            SYNC_STATE["products_total"] = len(disc.products)
            SYNC_STATE["collections_total"] = len(disc.collections)
            SYNC_STATE["pages_total"] = len(disc.pages)
            SYNC_STATE["blogs_total"] = len(disc.blogs)
            SYNC_STATE["blog_articles_total"] = int(disc.blog_articles_total)
            SYNC_STATE["products_synced"] = 0
            SYNC_STATE["collections_synced"] = 0
            SYNC_STATE["pages_synced"] = 0
            SYNC_STATE["blogs_synced"] = 0
            SYNC_STATE["blog_articles_synced"] = 0
            SYNC_STATE["images_synced"] = 0
            SYNC_STATE["images_total"] = count_catalog_image_urls_discover(
                disc.products, disc.collections, disc.pages, disc.articles_by_blog_id
            )
            _recompute_shopify_scoped_progress()
            _set_sync_stage(
                stage="syncing_shopify",
                label="Syncing Shopify",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Refreshing products from Shopify",
            )
            result["shopify"] = {}
            result["shopify"]["products"] = sync_products(
                db_path,
                50,
                progress_callback=_shopify_progress,
                products=disc.products,
                queue_scope="shopify",
            )
            _raise_if_sync_cancelled()
            result["shopify"]["collections"] = sync_collections(
                db_path,
                50,
                progress_callback=_shopify_progress,
                collections=disc.collections,
                queue_scope="shopify",
            )
            _raise_if_sync_cancelled()
            result["shopify"]["pages"] = sync_pages(
                db_path,
                50,
                progress_callback=_shopify_progress,
                pages=disc.pages,
                queue_scope="shopify",
            )
            _raise_if_sync_cancelled()
            result["shopify"]["blogs"] = sync_blogs(
                db_path,
                50,
                progress_callback=_shopify_progress,
                blogs=disc.blogs,
                articles_by_blog_id=disc.articles_by_blog_id,
                blog_articles_total_hint=disc.blog_articles_total,
                queue_scope="shopify",
            )
            br = result["shopify"].get("blogs") or {}
            SYNC_STATE["blog_articles_synced"] = int(br.get("blog_articles_synced") or 0)
            SYNC_STATE["blog_articles_total"] = int(br.get("blog_articles_total") or br.get("blog_articles_synced") or 0)
            _recompute_shopify_scoped_progress()
            SYNC_STATE["images_total"] = count_catalog_images_for_cache(Path(db_path))
            SYNC_STATE["images_synced"] = 0
            _recompute_shopify_scoped_progress()
            _raise_if_sync_cancelled()
            ic = _warm_product_image_cache_safe(db_path)
            if ic is not None:
                result["shopify"]["product_image_cache"] = ic
            _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="shopify")
        elif selected_scope == "gsc":
            _set_sync_stage(
                stage="refreshing_gsc",
                label="Refreshing Search Console data",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Loading Search Console summary",
            )
            result["gsc"] = bulk_refresh_search_console(db_path, force_refresh=force_refresh)
            _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="gsc")
        elif selected_scope == "ga4":
            _set_sync_stage(
                stage="refreshing_ga4",
                label="Refreshing GA4 data",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Refreshing GA4 summary and per-URL metrics",
            )
            result["ga4"] = refresh_ga4_summary(db_path, force_refresh=force_refresh)
            _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="ga4")
        elif selected_scope == "index":
            _set_sync_stage(
                stage="refreshing_index",
                label="Refreshing index status",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Inspecting URL index status",
            )
            result["index"] = bulk_refresh_index_status(db_path, force_refresh=force_refresh)
            _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="index")
        elif selected_scope == "pagespeed":
            _set_sync_stage(
                stage="refreshing_pagespeed",
                label="Refreshing PageSpeed metrics",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Collecting PageSpeed results",
            )
            result["pagespeed"] = bulk_refresh_pagespeed(db_path, force_refresh=force_refresh)
            _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="pagespeed")
    return result


def run_sync(
    db_path: str,
    scope: str,
    selected_scopes: list[str] | None = None,
    force_refresh: bool = False,
    *,
    already_prepared: bool = False,
) -> dict:
    """Run a dashboard sync.

    ``already_prepared`` is used by the background worker: ``start_sync_background`` resets
    ``SYNC_STATE`` (including ``running=True``) on the request thread *before* spawning the
    worker so ``POST /api/sync`` can return a payload that already reflects an in-flight run.
    """
    with SYNC_LOCK:
        if not already_prepared:
            normalized_scope, normalized_selected_scopes = _normalize_sync_scopes(scope, selected_scopes)
            _reset_sync_progress(_sync_label(normalized_scope, normalized_selected_scopes), normalized_selected_scopes)
            SYNC_STATE["force_refresh"] = bool(force_refresh)
            clear_last_error()
        else:
            normalized_scope = scope
            normalized_selected_scopes = list(selected_scopes or [])
        try:
            _raise_if_sync_cancelled()
            if normalized_scope == "products":
                _set_sync_stage(
                    stage="syncing_products",
                    label="Syncing products from Shopify",
                    active_scope="products",
                    step_index=1,
                    step_total=1,
                    current="Refreshing product catalog snapshot",
                )
                result = {
                    "products": sync_products(
                        db_path,
                        50,
                        progress_callback=lambda kind, done, total: (
                            _raise_if_sync_cancelled(),
                            SYNC_STATE.__setitem__("products_synced", done),
                            SYNC_STATE.__setitem__("products_total", total),
                            SYNC_STATE.__setitem__("current", f"Shopify products: {done}/{total}"),
                            _recompute_shopify_scoped_progress(),
                        ),
                        queue_scope="shopify",
                    )
                }
                SYNC_STATE["images_total"] = count_catalog_images_for_cache(Path(db_path))
                SYNC_STATE["images_synced"] = 0
                _recompute_shopify_scoped_progress()
                _raise_if_sync_cancelled()
                ic = _warm_product_image_cache_safe(db_path)
                if ic is not None:
                    result["products"]["product_image_cache"] = ic
                _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="shopify")
            elif normalized_scope == "collections":
                _set_sync_stage(
                    stage="syncing_collections",
                    label="Syncing collections from Shopify",
                    active_scope="collections",
                    step_index=1,
                    step_total=1,
                    current="Refreshing collection snapshot",
                )
                result = {
                    "collections": sync_collections(
                        db_path,
                        50,
                        progress_callback=lambda kind, done, total: (
                            _raise_if_sync_cancelled(),
                            SYNC_STATE.__setitem__("collections_synced", done),
                            SYNC_STATE.__setitem__("collections_total", total),
                            SYNC_STATE.__setitem__("current", f"Shopify collections: {done}/{total}"),
                            _recompute_shopify_scoped_progress(),
                        ),
                        queue_scope="shopify",
                    )
                }
                _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="shopify")
            elif normalized_scope == "pages":
                _set_sync_stage(
                    stage="syncing_pages",
                    label="Syncing pages from Shopify",
                    active_scope="pages",
                    step_index=1,
                    step_total=1,
                    current="Refreshing page snapshot",
                )
                result = {
                    "pages": sync_pages(
                        db_path,
                        50,
                        progress_callback=lambda kind, done, total: (
                            _raise_if_sync_cancelled(),
                            SYNC_STATE.__setitem__("pages_synced", done),
                            SYNC_STATE.__setitem__("pages_total", total),
                            SYNC_STATE.__setitem__("current", f"Shopify pages: {done}/{total}"),
                            _recompute_shopify_scoped_progress(),
                        ),
                        queue_scope="shopify",
                    )
                }
                _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="shopify")
            elif normalized_scope == "blogs":
                _set_sync_stage(
                    stage="syncing_blogs",
                    label="Syncing blogs from Shopify",
                    active_scope="blogs",
                    step_index=1,
                    step_total=1,
                    current="Refreshing blogs and articles snapshot",
                )

                def _blogs_only_progress(kind: str, done: int, total: int) -> None:
                    _raise_if_sync_cancelled()
                    if kind == "blogs":
                        SYNC_STATE["blogs_synced"] = done
                        SYNC_STATE["blogs_total"] = total
                        _sync_current(f"Shopify blogs: {done}/{total}")
                    elif kind == "blog_articles":
                        SYNC_STATE["blog_articles_synced"] = done
                        SYNC_STATE["blog_articles_total"] = total
                        _sync_current(f"Shopify blog articles: {done}/{total}")
                    _recompute_shopify_scoped_progress()

                result = {
                    "blogs": sync_blogs(
                        db_path,
                        50,
                        progress_callback=_blogs_only_progress,
                        queue_scope="shopify",
                    )
                }
                br = result.get("blogs") or {}
                SYNC_STATE["blog_articles_synced"] = int(br.get("blog_articles_synced") or 0)
                SYNC_STATE["blog_articles_total"] = int(br.get("blog_articles_total") or br.get("blog_articles_synced") or 0)
                _recompute_shopify_scoped_progress()
                _reconcile_catalog_signal_columns_from_cache(db_path, after_scope="shopify")
            else:
                result = _run_selected_sync_steps(db_path, normalized_selected_scopes, force_refresh=force_refresh)
            _raise_if_sync_cancelled()
            shopify_summary = result.get("shopify") if isinstance(result, dict) else None
            summary_message = "All selected sync steps finished"
            if normalized_scope == "products" and isinstance(result.get("products"), dict):
                products = result["products"].get("products_synced", 0)
                ic = result["products"].get("product_image_cache")
                ic_d = ic if isinstance(ic, dict) else None
                summary_message = f"Products {products}{_image_cache_summary_suffix(ic_d)}"
            elif normalized_scope == "collections" and isinstance(result.get("collections"), dict):
                collections = result["collections"].get("collections_synced", 0)
                summary_message = f"Collections {collections}"
            elif normalized_scope == "pages" and isinstance(result.get("pages"), dict):
                pages = result["pages"].get("pages_synced", 0)
                summary_message = f"Pages {pages}"
            elif normalized_scope == "blogs" and isinstance(result.get("blogs"), dict):
                blogs_n = result["blogs"].get("blogs_synced", 0)
                articles_n = result["blogs"].get("blog_articles_synced", 0)
                summary_message = f"Blogs {blogs_n}, Articles {articles_n}"
            elif isinstance(shopify_summary, dict):
                products = shopify_summary.get("products", {}).get("products_synced", 0)
                collections = shopify_summary.get("collections", {}).get("collections_synced", 0)
                pages = shopify_summary.get("pages", {}).get("pages_synced", 0)
                blogs_n = shopify_summary.get("blogs", {}).get("blogs_synced", 0)
                articles_n = shopify_summary.get("blogs", {}).get("blog_articles_synced", 0)
                ic = shopify_summary.get("product_image_cache")
                ic_d = ic if isinstance(ic, dict) else None
                summary_message = (
                    f"Products {products}, Collections {collections}, Pages {pages}, "
                    f"Blogs {blogs_n}, Articles {articles_n}"
                    f"{_image_cache_summary_suffix(ic_d)}"
                )
            _set_sync_stage(
                stage="complete",
                label="Sync complete",
                active_scope=SYNC_STATE.get("active_scope") or "",
                step_index=SYNC_STATE.get("step_total") or SYNC_STATE.get("step_index") or 0,
                step_total=SYNC_STATE.get("step_total") or 0,
                current=summary_message,
            )
            SYNC_STATE["finished_at"] = int(time.time())
            SYNC_STATE["last_result"] = result
            conn = _db_connect_for_actions(db_path)
            try:
                dg.set_service_setting(conn, "last_dashboard_sync_finished_at", str(int(time.time())))
            finally:
                conn.close()
            return result
        except SyncCancelledError:
            _set_sync_stage(
                stage="cancelled",
                label="Sync cancelled",
                active_scope=SYNC_STATE.get("active_scope") or "",
                step_index=SYNC_STATE.get("step_index") or 0,
                step_total=SYNC_STATE.get("step_total") or 0,
                current="Sync cancelled before completion",
            )
            SYNC_STATE["finished_at"] = int(time.time())
            SYNC_STATE["last_result"] = {"cancelled": True}
            clear_last_error()
            raise
        except Exception as exc:
            _set_sync_stage(
                stage="error",
                label="Sync failed",
                active_scope=SYNC_STATE.get("active_scope") or "",
                step_index=SYNC_STATE.get("step_index") or 0,
                step_total=SYNC_STATE.get("step_total") or 0,
            )
            SYNC_STATE["finished_at"] = int(time.time())
            record_last_error(exc)
            raise
        finally:
            SYNC_STATE["running"] = False


def start_sync_background(db_path: str, scope: str, selected_scopes: list[str] | None = None, force_refresh: bool = False) -> bool:
    with SYNC_LOCK:
        if SYNC_STATE["running"]:
            return False
        normalized_scope, normalized_selected_scopes = _normalize_sync_scopes(scope, selected_scopes)
        _reset_sync_progress(_sync_label(normalized_scope, normalized_selected_scopes), normalized_selected_scopes)
        SYNC_STATE["force_refresh"] = bool(force_refresh)
        clear_last_error()

    def worker():
        try:
            run_sync(
                db_path,
                normalized_scope,
                normalized_selected_scopes,
                force_refresh,
                already_prepared=True,
            )
        except Exception:
            logger.exception("Background sync worker exited with an exception")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True
