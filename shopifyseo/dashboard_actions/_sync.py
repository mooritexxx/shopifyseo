"""Sync operations: Shopify catalog, Search Console, GA4, index status, PageSpeed."""
import json
import logging
import sqlite3
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import unquote, urljoin, urlparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
from ._rpm_limiter import AdaptiveMinuteRateLimiter, PerMinuteRateLimiter
from ._state import (
    GA4_SYNC_RATE_LIMIT_PER_MINUTE,
    GA4_SYNC_WORKERS,
    GSC_SYNC_RATE_LIMIT_PER_MINUTE,
    GSC_SYNC_WORKERS,
    IMAGE_CACHE_WORKERS,
    INDEX_SYNC_RATE_LIMIT_PER_MINUTE,
    INDEX_SYNC_WORKERS,
    PAGESPEED_ERROR_DETAILS_MAX,
    PAGESPEED_RECENT_FETCH_WINDOW_SECONDS,
    PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE,
    SYNC_LOCK,
    SYNC_STATE,
    _db_connect_for_actions,
    _raise_if_sync_cancelled,
    clear_last_error,
    append_sync_event,
    clear_pagespeed_http_call_tracker,
    record_pagespeed_http_api_call_at,
    record_last_error,
)
from ..dashboard_http import HttpRequestError
from ..exceptions import SyncCancelledError

# Canonical execution order (matches sidebar / sync UI). Custom selections are always reordered to this.
SYNC_PIPELINE_ORDER = ["shopify", "gsc", "ga4", "index", "pagespeed", "structured"]

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
        qt = int(state.get("pagespeed_queue_total") or 0)
        qc = int(state.get("pagespeed_queue_completed") or 0)
        if qt > 0:
            return qc, qt
    if _is_shopify_progress_state(state):
        return shopify_aggregate_progress(state)
    if active == "structured":
        st = int(state.get("structured_total") or 0)
        sd = int(state.get("structured_done") or 0)
        if st > 0:
            return sd, st
        return 0, 1
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


def _sync_current(message: str) -> None:
    """Set ``SYNC_STATE[\"current\"]`` and append a matching sync event line."""
    SYNC_STATE["current"] = message
    tag = (SYNC_STATE.get("active_scope") or "sync")[:12]
    append_sync_event(tag, message)


def _reset_sync_progress(scope: str, selected_scopes: list[str] | None = None) -> None:
    started_at = int(time.time())
    clear_pagespeed_http_call_tracker()
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
            "sync_events": [],
            "pagespeed_error_details": [],
            "pagespeed_error_seq": 0,
            "cancel_requested": False,
            "structured_total": 0,
            "structured_done": 0,
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

        stats = warm_product_image_cache(Path(db_path), max_workers=IMAGE_CACHE_WORKERS, progress_callback=_progress)
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
        access_token = dg.get_google_access_token(conn)
        rate_limiter = PerMinuteRateLimiter(GSC_SYNC_RATE_LIMIT_PER_MINUTE)
        progress_lock = threading.Lock()

        def _run_gsc_target(kind: str, handle: str, url: str) -> str:
            _raise_if_sync_cancelled()
            worker_conn = _db_connect_for_actions(db_path)
            try:
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

        rate_limiter = PerMinuteRateLimiter(GA4_SYNC_RATE_LIMIT_PER_MINUTE)

        def _run_ga4_target(kind: str, handle: str, url: str) -> str:
            _raise_if_sync_cancelled()
            worker_conn = _db_connect_for_actions(db_path)
            try:
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
                    logger.warning("GA4 per-URL fetch failed for %s %s: %s", kind or "(path)", handle or url, exc)
                    return "error"
                return "refreshed"
            finally:
                worker_conn.close()

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
        rate_limiter = PerMinuteRateLimiter(INDEX_SYNC_RATE_LIMIT_PER_MINUTE)
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


def _pagespeed_target_counts(conn: sqlite3.Connection) -> tuple[int, list[tuple[str, str, str, str]]]:
    """Return (catalog object count, PageSpeed API jobs to run).

    Each job is ``(object_type, handle, url, strategy)`` for ``strategy`` in ``mobile`` / ``desktop``.
    A job is queued when there is **no** row for that strategy in ``google_api_cache``, when a
    previous PageSpeed rate-limit cooldown has expired, or when the cached ``fetched_at`` is older
    than ``PAGESPEED_RECENT_FETCH_WINDOW_SECONDS``. Fresh rows skip the API call;
    ``refresh_pagespeed_columns_from_cache_for_all_cached_objects`` still merges cache into catalog tables.
    """
    dg.ensure_google_cache_schema(conn)
    now_ts = int(time.time())
    cutoff_ts = now_ts - PAGESPEED_RECENT_FETCH_WINDOW_SECONDS
    targets = _all_object_targets(conn)
    total_targets = len(targets)
    rows = conn.execute(
        """
        SELECT object_type, object_handle, strategy, fetched_at, expires_at, payload_json
        FROM google_api_cache
        WHERE cache_type = 'pagespeed'
        """,
    ).fetchall()

    cache_rows: dict[tuple[str, str, str], sqlite3.Row] = {}
    for row in rows:
        object_type = str(row["object_type"] or "")
        object_handle = str(row["object_handle"] or "")
        if not object_type or not object_handle:
            continue
        strategy = str(row["strategy"] or "mobile")
        cache_rows[(object_type, object_handle, strategy)] = row

    def _needs_pagespeed_refresh(row: sqlite3.Row | None) -> bool:
        if row is None:
            return True
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        payload_meta = payload.get("_meta") if isinstance(payload, dict) else {}
        rate_limited = isinstance(payload_meta, dict) and bool(payload_meta.get("rate_limited"))
        if rate_limited:
            return int(row["expires_at"] or 0) <= now_ts
        return int(row["fetched_at"] or 0) < cutoff_ts

    queued_targets: list[tuple[str, str, str, str]] = []
    for object_type, handle, url in targets:
        for strategy in ("mobile", "desktop"):
            row = cache_rows.get((object_type, handle, strategy))
            if strategy == "mobile" and row is None:
                row = cache_rows.get((object_type, handle, ""))
            if _needs_pagespeed_refresh(row):
                queued_targets.append((object_type, handle, url, strategy))
    return int(total_targets or 0), queued_targets


def _pagespeed_error_detail_for_ui(exc: Exception) -> tuple[str, dict[str, Any]]:
    """Human-readable ``error`` plus optional HTTP fields for sync status / UI."""
    extra: dict[str, Any] = {}
    if not isinstance(exc, HttpRequestError):
        return str(exc), extra
    if exc.status is not None:
        extra["http_status"] = exc.status
    body = (exc.body or "").strip()
    if body:
        extra["response_body"] = body[:2000]
    summary = str(exc)
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            status_name = err.get("status")
            if isinstance(msg, str) and msg:
                suffix = msg + (f" ({status_name})" if isinstance(status_name, str) and status_name else "")
                return f"{summary} — {suffix}", extra
    if body:
        one_line = body.replace("\n", " ")[:240]
        if one_line:
            return f"{summary} — {one_line}", extra
    return summary, extra


def _record_pagespeed_error(kind: str, handle: str, url: str, exc: Exception, *, strategy: str = "") -> None:
    details = list(SYNC_STATE.get("pagespeed_error_details") or [])
    error_text, http_extra = _pagespeed_error_detail_for_ui(exc)
    seq = int(SYNC_STATE.get("pagespeed_error_seq") or 0) + 1
    SYNC_STATE["pagespeed_error_seq"] = seq
    row: dict[str, Any] = {
        "seq": seq,
        "object_type": kind,
        "handle": handle,
        "url": url,
        "strategy": strategy,
        "error": error_text,
        **http_extra,
    }
    details.append(row)
    SYNC_STATE["pagespeed_error_details"] = details[-PAGESPEED_ERROR_DETAILS_MAX:]


def _pagespeed_bulk_max_inflight(limit_per_minute: int) -> int:
    """Enough concurrency to keep the limiter busy without making workers the throttle."""
    cap = max(int(limit_per_minute or 0), 1)
    return max(16, min(64, (cap + 3) // 4))


def _pagespeed_adaptive_floor(limit_per_minute: int) -> int:
    cap = max(int(limit_per_minute or 0), 1)
    return max(60, cap // 2)


def _record_pagespeed_limit_change(reason: str, limit_per_minute: int) -> None:
    append_sync_event("pagespeed", f"Adaptive PageSpeed limit now {limit_per_minute}/min after {reason}")


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
        if not force_refresh:
            total_targets, raw_queued = _pagespeed_target_counts(conn)
            queued_targets = [(0.0, k, h, u, s, 0) for k, h, u, s in raw_queued]
        else:
            base = _all_object_targets(conn)
            total_targets = len(base)
            queued_targets = []
            for kind, handle, url in base:
                queued_targets.append((0.0, kind, handle, url, "mobile", 0))
                queued_targets.append((0.0, kind, handle, url, "desktop", 0))
        summary["considered"] = total_targets
        summary["queue_total"] = len(queued_targets)
        pending_objects = len({(k, h) for _, k, h, _, _, _ in queued_targets})
        summary["skipped_recent"] = max(total_targets - pending_objects, 0)
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
        _sync_current(
            f"PageSpeed queue prepared: {summary['queue_total']} stale run(s) "
            f"across {pending_objects} object(s), {summary['skipped_recent']} fully fresh"
        )

        if not queued_targets:
            SYNC_STATE["pagespeed_phase"] = "complete"
            _sync_current("PageSpeed queue empty (cache fresh). Catalog scores updated from cache.")
            return summary

        progress_lock = threading.Lock()
        adaptive_limiter = AdaptiveMinuteRateLimiter(
            PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE,
            minimum_limit=_pagespeed_adaptive_floor(PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE),
            maximum_limit=PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE,
            on_granted=record_pagespeed_http_api_call_at,
        )
        max_inflight = _pagespeed_bulk_max_inflight(PAGESPEED_SYNC_RATE_LIMIT_PER_MINUTE)
        
        def _current_max_inflight() -> int:
            return min(max_inflight, _pagespeed_bulk_max_inflight(adaptive_limiter.current_limit))

        append_sync_event(
            "pagespeed",
            f"PageSpeed scheduler armed at {adaptive_limiter.current_limit}/min with up to {max_inflight} in flight",
        )

        def _on_hybrid_429_slowdown(exc: HttpRequestError) -> None:
            retry_after: float | None = None
            try:
                raw = (exc.headers or {}).get("Retry-After") or (exc.headers or {}).get("retry-after")
                if raw not in (None, ""):
                    retry_after = float(max(float(raw), 1.0))
            except (TypeError, ValueError):
                retry_after = None
            adaptive_limiter.note_rate_limited(retry_after)

        def _run_pagespeed_target(
            kind: str,
            handle: str,
            url: str,
            strategy: str,
            r429_pass: int,
            *,
            initial_slot_reserved: bool,
        ) -> dict:
            first_http = bool(initial_slot_reserved)

            def _before_each_psi_http() -> None:
                nonlocal first_http
                if first_http:
                    first_http = False
                else:
                    adaptive_limiter.acquire(_raise_if_sync_cancelled)
                append_sync_event("pagespeed", f"HTTP {strategy} {kind}:{handle}")

            _raise_if_sync_cancelled()
            worker_conn = _db_connect_for_actions(db_path)
            try:
                refreshed = dg.get_pagespeed(
                    worker_conn,
                    url,
                    strategy,
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                    before_each_run_pagespeed_http=_before_each_psi_http,
                    hybrid_pagespeed_429_retry=True,
                    pagespeed_429_requeue_pass=r429_pass,
                    on_hybrid_429_slowdown=_on_hybrid_429_slowdown,
                    hybrid_429_adaptive_wait_seconds=adaptive_limiter.wait_seconds,
                    cancel_check=_raise_if_sync_cancelled,
                )
                refreshed_meta = refreshed.get("_cache") or {}
                if refreshed_meta.get("requeue_429"):
                    return {"status": "requeue_429"}
                if refreshed_meta.get("rate_limited"):
                    retry_after_seconds = None
                    retry_after_at = refreshed_meta.get("retry_after_at")
                    try:
                        if retry_after_at not in (None, ""):
                            retry_after_seconds = max(int(retry_after_at) - int(time.time()), 0)
                    except (TypeError, ValueError):
                        retry_after_seconds = None
                    return {
                        "status": "rate_limited",
                        "retry_after_seconds": retry_after_seconds,
                        "skip_adaptive_note": bool(refreshed_meta.get("hybrid_429_final")),
                    }
                return {"status": "refreshed"}
            finally:
                worker_conn.close()

        def _submit_target(executor: ThreadPoolExecutor, future_to_target: dict) -> bool:
            if not pending_targets:
                return False
            if len(future_to_target) >= _current_max_inflight():
                return False
            wait_seconds = adaptive_limiter.wait_seconds()
            if wait_seconds > 0.0:
                return False
            if pending_targets[0][0] > time.monotonic():
                return False
            adaptive_limiter.acquire(_raise_if_sync_cancelled)
            _, kind, handle, url, strategy, r429_pass = pending_targets.popleft()
            with progress_lock:
                summary["queue_inflight"] += 1
                SYNC_STATE["pagespeed_queue_inflight"] = summary["queue_inflight"]
            future = executor.submit(
                _run_pagespeed_target,
                kind,
                handle,
                url,
                strategy,
                r429_pass,
                initial_slot_reserved=True,
            )
            future_to_target[future] = (kind, handle, url, strategy, r429_pass)
            return True

        def _handle_finished_future(future, future_to_target: dict) -> None:
            kind, handle, url, strategy, r429_pass = future_to_target.pop(future)
            with progress_lock:
                summary["queue_inflight"] = max(summary["queue_inflight"] - 1, 0)
                summary["queue_completed"] += 1
                SYNC_STATE["pagespeed_phase"] = "queueing"
                SYNC_STATE["pagespeed_queue_inflight"] = summary["queue_inflight"]
                SYNC_STATE["pagespeed_queue_completed"] = summary["queue_completed"]
                _sync_current(f"PageSpeed ({strategy}): {kind}:{handle}")
            try:
                result = future.result()
                if result["status"] == "requeue_429":
                    append_sync_event(
                        "pagespeed",
                        f"429 re-queue scheduled ({strategy}) {kind}:{handle}",
                    )
                    with progress_lock:
                        summary["queue_total"] += 1
                        SYNC_STATE["pagespeed_queue_total"] = summary["queue_total"]
                    delay_seconds = min(max(adaptive_limiter.wait_seconds(), 5.0), 30.0)
                    available_at = time.monotonic() + delay_seconds
                    pending_targets.append((available_at, kind, handle, url, strategy, 1))
                elif result["status"] == "rate_limited":
                    summary["rate_limited"] += 1
                    SYNC_STATE["pagespeed_rate_limited"] = summary["rate_limited"]
                    if not result.get("skip_adaptive_note"):
                        changed, new_limit = adaptive_limiter.note_rate_limited(result.get("retry_after_seconds"))
                        if changed:
                            _record_pagespeed_limit_change("rate limit", new_limit)
                else:
                    summary["refreshed"] += 1
                    SYNC_STATE["pagespeed_refreshed"] = summary["refreshed"]
                    changed, new_limit = adaptive_limiter.note_success()
                    if changed:
                        _record_pagespeed_limit_change("healthy responses", new_limit)
                if result["status"] not in ("requeue_429",):
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
                _record_pagespeed_error(kind, handle, url, exc, strategy=strategy)
            _raise_if_sync_cancelled()

        pending_targets = deque(queued_targets)
        with ThreadPoolExecutor(max_workers=max_inflight) as executor:
            future_to_target = {}
            while pending_targets or future_to_target:
                _raise_if_sync_cancelled()

                submitted = False
                while _submit_target(executor, future_to_target):
                    submitted = True
                    _raise_if_sync_cancelled()
                if submitted:
                    continue

                if future_to_target:
                    timeout = 1.0
                    if pending_targets and len(future_to_target) < _current_max_inflight():
                        head_delay = max(0.0, pending_targets[0][0] - time.monotonic())
                        timeout = min(max(adaptive_limiter.wait_seconds(), head_delay, 0.05), 1.0)
                    done, _ = wait(set(future_to_target), timeout=timeout, return_when=FIRST_COMPLETED)
                    for future in done:
                        _handle_finished_future(future, future_to_target)
                    continue

                if pending_targets:
                    head_delay = max(0.0, pending_targets[0][0] - time.monotonic())
                    time.sleep(min(max(adaptive_limiter.wait_seconds(), head_delay, 0.05), 1.0))
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
                db_path, 50, progress_callback=_shopify_progress, products=disc.products
            )
            _raise_if_sync_cancelled()
            result["shopify"]["collections"] = sync_collections(
                db_path, 50, progress_callback=_shopify_progress, collections=disc.collections
            )
            _raise_if_sync_cancelled()
            result["shopify"]["pages"] = sync_pages(db_path, 50, progress_callback=_shopify_progress, pages=disc.pages)
            _raise_if_sync_cancelled()
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
            SYNC_STATE["images_total"] = count_catalog_images_for_cache(Path(db_path))
            SYNC_STATE["images_synced"] = 0
            _recompute_shopify_scoped_progress()
            _raise_if_sync_cancelled()
            ic = _warm_product_image_cache_safe(db_path)
            if ic is not None:
                result["shopify"]["product_image_cache"] = ic
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
        elif selected_scope == "structured":
            _set_sync_stage(
                stage="updating_structured_seo",
                label="Rebuilding structured SEO data",
                active_scope=selected_scope,
                step_index=index,
                step_total=total_steps,
                current="Refreshing structured SEO records",
            )
            # Use dedicated fields so we do not clobber global total/done (still used to
            # render completed Search Console + Index counts in the pipeline UI).
            SYNC_STATE["structured_total"] = 1
            SYNC_STATE["structured_done"] = 0
            conn = _db_connect_for_actions(db_path)
            try:
                refresh_structured_seo_data(conn)
            finally:
                conn.close()
            SYNC_STATE["structured_done"] = 1
            result["structured"] = {"updated": True}
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
                _set_sync_stage(
                    stage="syncing_products",
                    label="Syncing products from Shopify",
                    active_scope="products",
                    step_index=1,
                    step_total=1,
                    current="Refreshing product catalog snapshot",
                )
                result = {"products": sync_products(db_path, 50, progress_callback=lambda kind, done, total: (
                    _raise_if_sync_cancelled(),
                    SYNC_STATE.__setitem__("products_synced", done),
                    SYNC_STATE.__setitem__("products_total", total),
                    SYNC_STATE.__setitem__("current", f"Shopify products: {done}/{total}"),
                    _recompute_shopify_scoped_progress(),
                ))}
                SYNC_STATE["images_total"] = count_catalog_images_for_cache(Path(db_path))
                SYNC_STATE["images_synced"] = 0
                _recompute_shopify_scoped_progress()
                _raise_if_sync_cancelled()
                ic = _warm_product_image_cache_safe(db_path)
                if ic is not None:
                    result["products"]["product_image_cache"] = ic
            elif normalized_scope == "collections":
                _set_sync_stage(
                    stage="syncing_collections",
                    label="Syncing collections from Shopify",
                    active_scope="collections",
                    step_index=1,
                    step_total=1,
                    current="Refreshing collection snapshot",
                )
                result = {"collections": sync_collections(db_path, 50, progress_callback=lambda kind, done, total: (
                    _raise_if_sync_cancelled(),
                    SYNC_STATE.__setitem__("collections_synced", done),
                    SYNC_STATE.__setitem__("collections_total", total),
                    SYNC_STATE.__setitem__("current", f"Shopify collections: {done}/{total}"),
                    _recompute_shopify_scoped_progress(),
                ))}
            elif normalized_scope == "pages":
                _set_sync_stage(
                    stage="syncing_pages",
                    label="Syncing pages from Shopify",
                    active_scope="pages",
                    step_index=1,
                    step_total=1,
                    current="Refreshing page snapshot",
                )
                result = {"pages": sync_pages(db_path, 50, progress_callback=lambda kind, done, total: (
                    _raise_if_sync_cancelled(),
                    SYNC_STATE.__setitem__("pages_synced", done),
                    SYNC_STATE.__setitem__("pages_total", total),
                    SYNC_STATE.__setitem__("current", f"Shopify pages: {done}/{total}"),
                    _recompute_shopify_scoped_progress(),
                ))}
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

                result = {"blogs": sync_blogs(db_path, 50, progress_callback=_blogs_only_progress)}
                br = result.get("blogs") or {}
                SYNC_STATE["blog_articles_synced"] = int(br.get("blog_articles_synced") or 0)
                SYNC_STATE["blog_articles_total"] = int(br.get("blog_articles_total") or br.get("blog_articles_synced") or 0)
                _recompute_shopify_scoped_progress()
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
    if SYNC_STATE["running"]:
        return False

    def worker():
        try:
            run_sync(db_path, scope, selected_scopes, force_refresh=force_refresh)
        except Exception:
            logger.exception("Background sync worker exited with an exception")

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True
