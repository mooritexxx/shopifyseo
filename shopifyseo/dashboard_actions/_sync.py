"""Sync operations: Shopify catalog, Search Console, GA4, index status, PageSpeed."""
import logging
import sqlite3
import threading
import time
from collections import deque
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
    refresh_object_pagespeed_signal_data,
    refresh_pagespeed_columns_from_cache_for_all_cached_objects,
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
from ._state import (
    GA4_SYNC_RATE_LIMIT_PER_MINUTE,
    GA4_SYNC_WORKERS,
    GSC_SYNC_RATE_LIMIT_PER_MINUTE,
    GSC_SYNC_WORKERS,
    INDEX_SYNC_RATE_LIMIT_PER_MINUTE,
    INDEX_SYNC_WORKERS,
    PAGESPEED_RECENT_FETCH_WINDOW_SECONDS,
    PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE,
    PAGESPEED_SYNC_WORKERS,
    SYNC_LOCK,
    SYNC_STATE,
    _db_connect_for_actions,
    _raise_if_sync_cancelled,
    clear_last_error,
    record_last_error,
)
from .sync_eta import record_scope_eta_segment, record_shopify_kind_eta, record_sync_eta_sample, shopify_aggregate_progress

# Canonical execution order (matches sidebar / sync UI). Custom selections are always reordered to this.
SYNC_PIPELINE_ORDER = ["shopify", "gsc", "ga4", "index", "pagespeed", "structured"]

# ---------------------------------------------------------------------------
# Sync state helpers
# ---------------------------------------------------------------------------


def _reset_sync_progress(scope: str, selected_scopes: list[str] | None = None) -> None:
    started_at = int(time.time())
    SYNC_STATE.update(
        {
            "running": True,
            "scope": scope,
            "selected_scopes": list(selected_scopes or []),
            "force_refresh": False,
            "started_at": started_at,
            "finished_at": 0,
            "stage_started_at": started_at,
            "eta_segment_started_at": started_at,
            "stage": "starting",
            "stage_label": "Preparing sync",
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
    SYNC_STATE["stage"] = stage
    SYNC_STATE["stage_label"] = label
    SYNC_STATE["active_scope"] = active_scope
    SYNC_STATE["step_index"] = step_index
    SYNC_STATE["step_total"] = step_total
    if current is not None:
        SYNC_STATE["current"] = current
    if stage != prev_stage or (active_scope or "") != (prev_scope or ""):
        SYNC_STATE["stage_started_at"] = int(time.time())


def _recompute_shopify_scoped_progress() -> None:
    """Sidebar progress bar: sum catalog entity steps plus catalog image cache warm."""
    done, total = shopify_aggregate_progress(SYNC_STATE)
    SYNC_STATE["done"] = done
    SYNC_STATE["total"] = total


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
            SYNC_STATE["current"] = f"Shopify: catalog images {done}/{total}"
            _recompute_shopify_scoped_progress()

        stats = warm_product_image_cache(Path(db_path), max_workers=6, progress_callback=_progress)
        return {
            "downloaded": int(stats.get("downloaded") or 0),
            "skipped": int(stats.get("skipped") or 0),
            "errors": int(stats.get("errors") or 0),
            "pruned": int(stats.get("pruned") or 0),
        }
    except Exception as exc:
        if str(exc) == "Sync cancelled by user":
            raise
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
# Rate limiter
# ---------------------------------------------------------------------------


class _PerMinuteRateLimiter:
    def __init__(self, limit: int, period_seconds: int = 60) -> None:
        self.limit = max(int(limit or 0), 1)
        self.period_seconds = max(int(period_seconds or 0), 1)
        self._lock = threading.Lock()
        self._request_times: deque[float] = deque()

    def acquire(self, cancel_check=None) -> None:
        while True:
            if cancel_check is not None:
                cancel_check()
            with self._lock:
                now = time.monotonic()
                cutoff = now - self.period_seconds
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()
                if len(self._request_times) < self.limit:
                    self._request_times.append(now)
                    return
                wait_seconds = max(self.period_seconds - (now - self._request_times[0]), 0.05)
            time.sleep(wait_seconds)


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

        targets = _all_object_targets(conn)
        SYNC_STATE["total"] = len(targets)
        SYNC_STATE["done"] = 0
        access_token = dg.get_google_access_token(conn)
        rate_limiter = _PerMinuteRateLimiter(GSC_SYNC_RATE_LIMIT_PER_MINUTE)
        progress_lock = threading.Lock()

        def _run_gsc_target(kind: str, handle: str, url: str) -> str:
            _raise_if_sync_cancelled()
            worker_conn = _db_connect_for_actions(db_path)
            try:
                cached = dg.get_search_console_url_detail(worker_conn, url, refresh=False, object_type=kind, object_handle=handle, site_url_override=site_url)
                cache_meta = cached.get("_cache") or {}
                if not force_refresh and cache_meta.get("exists") and not cache_meta.get("stale"):
                    return "skipped"
                rate_limiter.acquire(_raise_if_sync_cancelled)
                _raise_if_sync_cancelled()
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
                return "refreshed"
            finally:
                worker_conn.close()

        with ThreadPoolExecutor(max_workers=GSC_SYNC_WORKERS) as executor:
            future_to_target = {
                executor.submit(_run_gsc_target, kind, handle, url): (kind, handle)
                for kind, handle, url in targets
            }

            for future in as_completed(future_to_target):
                kind, handle = future_to_target[future]
                _raise_if_sync_cancelled()
                summary["considered"] += 1
                touched_targets.append((kind, handle))
                SYNC_STATE["current"] = f"Search Console: {kind}:{handle}"
                try:
                    result = future.result()
                    if result == "skipped":
                        summary["skipped_fresh"] += 1
                        SYNC_STATE["gsc_skipped"] = summary["skipped_fresh"]
                    else:
                        summary["refreshed"] += 1
                        SYNC_STATE["gsc_refreshed"] = summary["refreshed"]
                except Exception:
                    summary["errors"] += 1
                    SYNC_STATE["gsc_errors"] = summary["errors"]
                with progress_lock:
                    SYNC_STATE["done"] = summary["considered"]
        _raise_if_sync_cancelled()
        if touched_targets:
            refresh_gsc_signal_data_for_objects(conn, touched_targets)
    finally:
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
    }
    try:
        payload = dg.get_ga4_summary(conn, refresh=force_refresh or True)
        summary["rows"] = len(payload.get("page_rows", []))
        SYNC_STATE["ga4_rows"] = summary["rows"]

        targets = _all_object_targets(conn)
        summary["considered"] = len(targets)
        SYNC_STATE["total"] = max(len(targets), 1)
        SYNC_STATE["done"] = 0

        rate_limiter = _PerMinuteRateLimiter(GA4_SYNC_RATE_LIMIT_PER_MINUTE)

        def _run_ga4_target(kind: str, handle: str, url: str) -> str:
            worker_conn = _db_connect_for_actions(db_path)
            try:
                if not force_refresh and not dg.ga4_url_cache_stale(worker_conn, url):
                    return "skipped"
                rate_limiter.acquire(_raise_if_sync_cancelled)
                _raise_if_sync_cancelled()
                try:
                    dg.get_ga4_url_detail(
                        worker_conn,
                        url,
                        refresh=True,
                        object_type=kind,
                        object_handle=handle,
                    )
                except Exception as exc:
                    logger.warning("GA4 per-URL fetch failed for %s %s: %s", kind, handle, exc)
                    return "error"
                return "refreshed"
            finally:
                worker_conn.close()

        with ThreadPoolExecutor(max_workers=GA4_SYNC_WORKERS) as executor:
            future_to_target = {
                executor.submit(_run_ga4_target, kind, handle, url): (kind, handle)
                for kind, handle, url in targets
            }

            done = 0
            for future in as_completed(future_to_target):
                _raise_if_sync_cancelled()
                done += 1
                SYNC_STATE["done"] = done
                try:
                    result = future.result()
                    if result == "skipped":
                        summary["skipped_fresh"] += 1
                    elif result == "refreshed":
                        summary["refreshed"] += 1
                    else:
                        summary["errors"] += 1
                except Exception as exc:
                    logger.warning("GA4 target worker failed: %s", exc)
                    summary["errors"] += 1

        refresh_ga4_signal_data_for_objects(conn, [(k, h) for k, h, _ in targets])
        summary["url_errors"] = summary["errors"]
        SYNC_STATE["ga4_url_errors"] = summary["errors"]
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
        SYNC_STATE["total"] = len(targets)
        SYNC_STATE["done"] = 0
        rate_limiter = _PerMinuteRateLimiter(INDEX_SYNC_RATE_LIMIT_PER_MINUTE)
        progress_lock = threading.Lock()

        def _run_index_target(kind: str, handle: str, url: str) -> None:
            _raise_if_sync_cancelled()
            rate_limiter.acquire(_raise_if_sync_cancelled)
            _raise_if_sync_cancelled()
            worker_conn = _db_connect_for_actions(db_path)
            try:
                dg.get_url_inspection(worker_conn, url, refresh=True, object_type=kind, object_handle=handle)
            finally:
                worker_conn.close()

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
                SYNC_STATE["current"] = f"Index: {kind}:{handle}"
                try:
                    future.result()
                    summary["refreshed"] += 1
                    SYNC_STATE["index_refreshed"] = summary["refreshed"]
                except Exception:
                    summary["errors"] += 1
                    SYNC_STATE["index_errors"] = summary["errors"]
                with progress_lock:
                    SYNC_STATE["done"] = summary["considered"]
        _raise_if_sync_cancelled()
        if touched_targets:
            refresh_index_signal_data_for_objects(conn, touched_targets)
    finally:
        conn.close()
    return summary


def _pagespeed_target_counts(conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str, str]]]:
    """Return (catalog object count, URLs to fetch).

    A URL is queued when there is **no** mobile PageSpeed row in `google_api_cache` for that object, **or**
    the cached `fetched_at` is older than ``PAGESPEED_RECENT_FETCH_WINDOW_SECONDS`` (30 days). Fresh cache
    skips the API call but still gets denormalized into catalog tables via
    ``refresh_pagespeed_columns_from_cache_for_all_cached_objects`` at the end of sync.
    """
    dg.ensure_google_cache_schema(conn)
    cutoff_ts = int(time.time()) - PAGESPEED_RECENT_FETCH_WINDOW_SECONDS
    total_targets = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM products) +
          (SELECT COUNT(*) FROM collections) +
          (SELECT COUNT(*) FROM pages) +
          (SELECT COUNT(*) FROM blog_articles)
        """
    ).fetchone()[0]
    rows = conn.execute(
        """
        WITH all_targets AS (
          SELECT 'product' AS object_type, handle FROM products
          UNION ALL
          SELECT 'collection' AS object_type, handle FROM collections
          UNION ALL
          SELECT 'page' AS object_type, handle FROM pages
          UNION ALL
          SELECT 'blog_article' AS object_type, (blog_handle || '/' || handle) AS handle FROM blog_articles
        )
        SELECT target.object_type, target.handle
        FROM all_targets AS target
        LEFT JOIN google_api_cache AS cache
          ON cache.cache_type = 'pagespeed'
         AND COALESCE(cache.strategy, 'mobile') = 'mobile'
         AND cache.object_type = target.object_type
         AND cache.object_handle = target.handle
        WHERE cache.fetched_at IS NULL OR cache.fetched_at < ?
        ORDER BY target.object_type, target.handle
        """,
        (cutoff_ts,),
    ).fetchall()
    queued_targets = [(row["object_type"], row["handle"], dq.object_url(row["object_type"], row["handle"])) for row in rows]
    return int(total_targets or 0), queued_targets


def _record_pagespeed_error(kind: str, handle: str, url: str, exc: Exception) -> None:
    details = list(SYNC_STATE.get("pagespeed_error_details") or [])
    details.append(
        {
            "object_type": kind,
            "handle": handle,
            "url": url,
            "error": str(exc),
        }
    )
    SYNC_STATE["pagespeed_error_details"] = details[-10:]


def bulk_refresh_pagespeed(db_path: str, throttle_seconds: float = 0.4, force_refresh: bool = False) -> dict:
    conn = _db_connect_for_actions(db_path)
    summary = {
        "considered": 0,
        "refreshed": 0,
        "rate_limited": 0,
        "errors": 0,
        "skipped_fresh": 0,
        "skipped_recent": 0,
        "queue_total": 0,
        "queue_completed": 0,
        "queue_inflight": 0,
    }
    try:
        total_targets, queued_targets = _pagespeed_target_counts(conn) if not force_refresh else (len(_all_object_targets(conn)), _all_object_targets(conn))
        summary["considered"] = total_targets
        summary["queue_total"] = len(queued_targets)
        summary["skipped_recent"] = max(total_targets - summary["queue_total"], 0)
        summary["skipped_fresh"] = summary["skipped_recent"]
        SYNC_STATE["pagespeed_phase"] = "queueing"
        SYNC_STATE["pagespeed_scan_total"] = total_targets
        SYNC_STATE["pagespeed_scanned"] = total_targets
        SYNC_STATE["pagespeed_skipped_recent"] = summary["skipped_recent"]
        SYNC_STATE["pagespeed_skipped"] = summary["skipped_fresh"]
        SYNC_STATE["pagespeed_phase"] = "queueing"
        SYNC_STATE["pagespeed_queue_total"] = summary["queue_total"]
        SYNC_STATE["pagespeed_queue_completed"] = 0
        SYNC_STATE["pagespeed_queue_inflight"] = 0
        SYNC_STATE["total"] = summary["queue_total"]
        SYNC_STATE["done"] = 0
        SYNC_STATE["current"] = f"PageSpeed queue prepared: {summary['queue_total']} stale URL(s), {summary['skipped_recent']} skipped"

        if not queued_targets:
            SYNC_STATE["pagespeed_phase"] = "complete"
            SYNC_STATE["current"] = "PageSpeed queue empty (cache fresh). Catalog scores updated from cache."
            return summary

        rate_limiter = _PerMinuteRateLimiter(PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE)
        progress_lock = threading.Lock()

        def _run_pagespeed_target(kind: str, handle: str, url: str) -> dict:
            _raise_if_sync_cancelled()
            rate_limiter.acquire(_raise_if_sync_cancelled)
            _raise_if_sync_cancelled()
            worker_conn = _db_connect_for_actions(db_path)
            try:
                refreshed = dg.get_pagespeed(
                    worker_conn,
                    url,
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                )
                refreshed_meta = refreshed.get("_cache") or {}
                if refreshed_meta.get("rate_limited"):
                    return {"status": "rate_limited"}
                return {"status": "refreshed"}
            finally:
                worker_conn.close()

        with ThreadPoolExecutor(max_workers=PAGESPEED_SYNC_WORKERS) as executor:
            future_to_target = {}
            for kind, handle, url in queued_targets:
                _raise_if_sync_cancelled()
                with progress_lock:
                    summary["queue_inflight"] += 1
                    SYNC_STATE["pagespeed_queue_inflight"] = summary["queue_inflight"]
                future = executor.submit(_run_pagespeed_target, kind, handle, url)
                future_to_target[future] = (kind, handle)

            for future in as_completed(future_to_target):
                kind, handle = future_to_target[future]
                with progress_lock:
                    summary["queue_inflight"] = max(summary["queue_inflight"] - 1, 0)
                    summary["queue_completed"] += 1
                    SYNC_STATE["pagespeed_phase"] = "queueing"
                    SYNC_STATE["pagespeed_queue_inflight"] = summary["queue_inflight"]
                    SYNC_STATE["pagespeed_queue_completed"] = summary["queue_completed"]
                    SYNC_STATE["done"] = summary["queue_completed"]
                    SYNC_STATE["current"] = f"PageSpeed: {kind}:{handle}"
                try:
                    result = future.result()
                    if result["status"] == "rate_limited":
                        summary["rate_limited"] += 1
                        SYNC_STATE["pagespeed_rate_limited"] = summary["rate_limited"]
                    else:
                        summary["refreshed"] += 1
                        SYNC_STATE["pagespeed_refreshed"] = summary["refreshed"]
                    try:
                        refresh_object_pagespeed_signal_data(conn, kind, handle)
                    except Exception:
                        logger.warning(
                            "Incremental PageSpeed denormalize failed (non-fatal)",
                            exc_info=True,
                            extra={"object_type": kind, "handle": handle},
                        )
                except Exception as exc:
                    summary["errors"] += 1
                    SYNC_STATE["pagespeed_errors"] = summary["errors"]
                    _record_pagespeed_error(kind, handle, dq.object_url(kind, handle), exc)
                _raise_if_sync_cancelled()
        _raise_if_sync_cancelled()
        SYNC_STATE["pagespeed_phase"] = "complete"
    finally:
        try:
            # Always merge google_api_cache → catalog tables so completed API writes survive
            # cancel, process kill, or exceptions before the normal end-of-phase path.
            refresh_pagespeed_columns_from_cache_for_all_cached_objects(conn)
        except Exception:
            logger.warning(
                "Pagespeed cache→catalog reconciliation failed (non-fatal)",
                exc_info=True,
            )
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
            SYNC_STATE["current"] = f"Shopify blog articles: {done}/{total}"
        else:
            SYNC_STATE["current"] = f"Shopify {prefix}: {done}/{total}"
        _recompute_shopify_scoped_progress()

    for index, selected_scope in enumerate(selected_scopes, start=1):
        _raise_if_sync_cancelled()
        SYNC_STATE["eta_segment_started_at"] = int(time.time())
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
            t0 = time.time()
            result["shopify"]["products"] = sync_products(
                db_path, 50, progress_callback=_shopify_progress, products=disc.products
            )
            record_shopify_kind_eta(
                db_path, "shopify_products", t0, int(result["shopify"]["products"].get("products_synced") or 0)
            )
            _raise_if_sync_cancelled()
            t0 = time.time()
            result["shopify"]["collections"] = sync_collections(
                db_path, 50, progress_callback=_shopify_progress, collections=disc.collections
            )
            record_shopify_kind_eta(
                db_path,
                "shopify_collections",
                t0,
                int(result["shopify"]["collections"].get("collections_synced") or 0),
            )
            _raise_if_sync_cancelled()
            t0 = time.time()
            result["shopify"]["pages"] = sync_pages(db_path, 50, progress_callback=_shopify_progress, pages=disc.pages)
            record_shopify_kind_eta(
                db_path, "shopify_pages", t0, int(result["shopify"]["pages"].get("pages_synced") or 0)
            )
            _raise_if_sync_cancelled()
            t0 = time.time()
            result["shopify"]["blogs"] = sync_blogs(
                db_path,
                50,
                progress_callback=_shopify_progress,
                blogs=disc.blogs,
                articles_by_blog_id=disc.articles_by_blog_id,
                blog_articles_total_hint=disc.blog_articles_total,
            )
            br = result["shopify"].get("blogs") or {}
            SYNC_STATE["blog_articles_synced"] = int(br.get("blog_articles_synced") or 0)
            SYNC_STATE["blog_articles_total"] = int(br.get("blog_articles_total") or br.get("blog_articles_synced") or 0)
            _recompute_shopify_scoped_progress()
            record_sync_eta_sample(
                db_path,
                "shopify_blogs",
                max(float(br.get("meta_duration_seconds") or 0.05), 1.3),
                max(1, int(br.get("blogs_synced") or 0)),
            )
            record_sync_eta_sample(
                db_path,
                "shopify_blog_articles",
                max(float(br.get("article_duration_seconds") or 0.05), 1.3),
                max(1, int(br.get("blog_articles_synced") or 0)),
            )
            SYNC_STATE["images_total"] = count_catalog_images_for_cache(Path(db_path))
            SYNC_STATE["images_synced"] = 0
            _recompute_shopify_scoped_progress()
            _raise_if_sync_cancelled()
            t0 = time.time()
            ic = _warm_product_image_cache_safe(db_path)
            if ic is not None:
                result["shopify"]["product_image_cache"] = ic
            record_shopify_kind_eta(
                db_path,
                "shopify_images",
                t0,
                max(int(SYNC_STATE.get("images_total") or 0), int(SYNC_STATE.get("images_synced") or 0), 1),
            )
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
            record_scope_eta_segment(db_path, SYNC_STATE, "gsc")
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
            record_scope_eta_segment(db_path, SYNC_STATE, "ga4")
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
            record_scope_eta_segment(db_path, SYNC_STATE, "index")
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
            record_scope_eta_segment(db_path, SYNC_STATE, "pagespeed")
        elif selected_scope == "structured":
            _set_sync_stage(
                stage="updating_structured_seo",
                label="Rebuilding structured SEO data",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Refreshing structured SEO records",
            )
            SYNC_STATE["total"] = 1
            SYNC_STATE["done"] = 0
            conn = _db_connect_for_actions(db_path)
            try:
                refresh_structured_seo_data(conn)
            finally:
                conn.close()
            SYNC_STATE["done"] = 1
            result["structured"] = {"updated": True}
            record_scope_eta_segment(db_path, SYNC_STATE, "structured")
    return result


def run_sync(db_path: str, scope: str, selected_scopes: list[str] | None = None, force_refresh: bool = False) -> dict:
    with SYNC_LOCK:
        normalized_scope, normalized_selected_scopes = _normalize_sync_scopes(scope, selected_scopes)
        _reset_sync_progress(_sync_label(normalized_scope, normalized_selected_scopes), normalized_selected_scopes)
        SYNC_STATE["force_refresh"] = bool(force_refresh)
        clear_last_error()
        try:
            _raise_if_sync_cancelled()
            if normalized_scope == "products":
                SYNC_STATE["eta_segment_started_at"] = int(time.time())
                _set_sync_stage(
                    stage="syncing_products",
                    label="Syncing products from Shopify",
                    active_scope="products",
                    step_index=1,
                    step_total=1,
                    current="Refreshing product catalog snapshot",
                )
                t0 = time.time()
                result = {"products": sync_products(db_path, 50, progress_callback=lambda kind, done, total: (
                    _raise_if_sync_cancelled(),
                    SYNC_STATE.__setitem__("products_synced", done),
                    SYNC_STATE.__setitem__("products_total", total),
                    SYNC_STATE.__setitem__("current", f"Shopify products: {done}/{total}"),
                    _recompute_shopify_scoped_progress(),
                ))}
                record_shopify_kind_eta(
                    db_path, "shopify_products", t0, int(result["products"].get("products_synced") or 0)
                )
                SYNC_STATE["images_total"] = count_catalog_images_for_cache(Path(db_path))
                SYNC_STATE["images_synced"] = 0
                _recompute_shopify_scoped_progress()
                _raise_if_sync_cancelled()
                t_img = time.time()
                ic = _warm_product_image_cache_safe(db_path)
                if ic is not None:
                    result["products"]["product_image_cache"] = ic
                record_shopify_kind_eta(
                    db_path,
                    "shopify_images",
                    t_img,
                    max(int(SYNC_STATE.get("images_total") or 0), int(SYNC_STATE.get("images_synced") or 0), 1),
                )
            elif normalized_scope == "collections":
                SYNC_STATE["eta_segment_started_at"] = int(time.time())
                _set_sync_stage(
                    stage="syncing_collections",
                    label="Syncing collections from Shopify",
                    active_scope="collections",
                    step_index=1,
                    step_total=1,
                    current="Refreshing collection snapshot",
                )
                t0 = time.time()
                result = {"collections": sync_collections(db_path, 50, progress_callback=lambda kind, done, total: (
                    _raise_if_sync_cancelled(),
                    SYNC_STATE.__setitem__("collections_synced", done),
                    SYNC_STATE.__setitem__("collections_total", total),
                    SYNC_STATE.__setitem__("current", f"Shopify collections: {done}/{total}"),
                    _recompute_shopify_scoped_progress(),
                ))}
                record_shopify_kind_eta(
                    db_path, "shopify_collections", t0, int(result["collections"].get("collections_synced") or 0)
                )
            elif normalized_scope == "pages":
                SYNC_STATE["eta_segment_started_at"] = int(time.time())
                _set_sync_stage(
                    stage="syncing_pages",
                    label="Syncing pages from Shopify",
                    active_scope="pages",
                    step_index=1,
                    step_total=1,
                    current="Refreshing page snapshot",
                )
                t0 = time.time()
                result = {"pages": sync_pages(db_path, 50, progress_callback=lambda kind, done, total: (
                    _raise_if_sync_cancelled(),
                    SYNC_STATE.__setitem__("pages_synced", done),
                    SYNC_STATE.__setitem__("pages_total", total),
                    SYNC_STATE.__setitem__("current", f"Shopify pages: {done}/{total}"),
                    _recompute_shopify_scoped_progress(),
                ))}
                record_shopify_kind_eta(db_path, "shopify_pages", t0, int(result["pages"].get("pages_synced") or 0))
            elif normalized_scope == "blogs":
                SYNC_STATE["eta_segment_started_at"] = int(time.time())
                _set_sync_stage(
                    stage="syncing_blogs",
                    label="Syncing blogs from Shopify",
                    active_scope="blogs",
                    step_index=1,
                    step_total=1,
                    current="Refreshing blogs and articles snapshot",
                )
                t0 = time.time()

                def _blogs_only_progress(kind: str, done: int, total: int) -> None:
                    _raise_if_sync_cancelled()
                    if kind == "blogs":
                        SYNC_STATE["blogs_synced"] = done
                        SYNC_STATE["blogs_total"] = total
                        SYNC_STATE["current"] = f"Shopify blogs: {done}/{total}"
                    elif kind == "blog_articles":
                        SYNC_STATE["blog_articles_synced"] = done
                        SYNC_STATE["blog_articles_total"] = total
                        SYNC_STATE["current"] = f"Shopify blog articles: {done}/{total}"
                    _recompute_shopify_scoped_progress()

                result = {"blogs": sync_blogs(db_path, 50, progress_callback=_blogs_only_progress)}
                br = result.get("blogs") or {}
                SYNC_STATE["blog_articles_synced"] = int(br.get("blog_articles_synced") or 0)
                SYNC_STATE["blog_articles_total"] = int(br.get("blog_articles_total") or br.get("blog_articles_synced") or 0)
                _recompute_shopify_scoped_progress()
                record_sync_eta_sample(
                    db_path,
                    "shopify_blogs",
                    max(float(br.get("meta_duration_seconds") or 0.05), 1.3),
                    max(1, int(br.get("blogs_synced") or 0)),
                )
                record_sync_eta_sample(
                    db_path,
                    "shopify_blog_articles",
                    max(float(br.get("article_duration_seconds") or 0.05), 1.3),
                    max(1, int(br.get("blog_articles_synced") or 0)),
                )
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
        except Exception as exc:
            if str(exc) == "Sync cancelled by user":
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
            else:
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
    if SYNC_STATE["running"]:
        return False

    def worker():
        try:
            run_sync(db_path, scope, selected_scopes, force_refresh=force_refresh)
        except Exception:
            pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True
