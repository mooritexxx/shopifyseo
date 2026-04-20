"""PageSpeed bulk sync: queue, workers, batching, and error handling."""
import json
import logging
import sqlite3
import threading
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

logger = logging.getLogger(__name__)

from .. import dashboard_google as dg
from ..dashboard_http import HttpRequestError
from ..dashboard_store import (
    refresh_object_pagespeed_signal_data,
    refresh_pagespeed_columns_from_cache_for_all_cached_objects,
)
from ._state import (
    PAGESPEED_RECENT_FETCH_WINDOW_SECONDS,
    PAGESPEED_SYNC_BATCH_PAUSE_SECONDS,
    PAGESPEED_SYNC_BATCH_SIZE,
    PAGESPEED_SYNC_WORKERS,
    SYNC_STATE,
    _db_connect_for_actions,
    _raise_if_sync_cancelled,
    _sync_current,
    append_sync_event,
    record_pagespeed_http_api_call,
)


def _resolve_all_object_targets():
    """Late-bound lookup so tests can monkeypatch ``_sync._all_object_targets`` / ``da._all_object_targets``."""
    from . import _sync

    return _sync._all_object_targets


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
    targets = _resolve_all_object_targets()(conn)
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


def _pagespeed_queue_row_key(kind: str, handle: str, strategy: str) -> str:
    return f"{kind}\x00{handle}\x00{strategy}"


def _pagespeed_queue_meta_map() -> dict[str, Any]:
    m = SYNC_STATE.get("pagespeed_queue_meta")
    if not isinstance(m, dict):
        m = {}
        SYNC_STATE["pagespeed_queue_meta"] = m
    return m


def _pagespeed_queue_clear_success(kind: str, handle: str, strategy: str) -> None:
    k = _pagespeed_queue_row_key(kind, handle, strategy)
    meta = SYNC_STATE.get("pagespeed_queue_meta")
    if isinstance(meta, dict) and k in meta:
        del meta[k]


def _pagespeed_queue_note_hint(
    kind: str, handle: str, url: str, strategy: str, *, code_hint: str, error: str = ""
) -> None:
    k = _pagespeed_queue_row_key(kind, handle, strategy)
    meta = _pagespeed_queue_meta_map()
    prev = meta.get(k) if isinstance(meta.get(k), dict) else {}
    seq = int(SYNC_STATE.get("pagespeed_error_seq") or 0) + 1
    SYNC_STATE["pagespeed_error_seq"] = seq
    meta[k] = {
        **prev,
        "seq": seq,
        "code_hint": code_hint,
        "error": error or str(prev.get("error") or ""),
        "url": url,
    }


def _record_pagespeed_error(
    kind: str,
    handle: str,
    url: str,
    exc: Exception,
    *,
    strategy: str = "",
    append_final_batch_retry_note: bool = False,
) -> None:
    error_text, http_extra = _pagespeed_error_detail_for_ui(exc)
    if append_final_batch_retry_note:
        note = " — scheduled for final batch retry"
        if note not in error_text:
            error_text = f"{error_text}{note}"
    seq = int(SYNC_STATE.get("pagespeed_error_seq") or 0) + 1
    SYNC_STATE["pagespeed_error_seq"] = seq
    k = _pagespeed_queue_row_key(kind, handle, strategy)
    meta = _pagespeed_queue_meta_map()
    prev = meta.get(k) if isinstance(meta.get(k), dict) else {}
    meta[k] = {
        **prev,
        "seq": seq,
        "error": error_text,
        "http_status": http_extra.get("http_status"),
        "response_body": http_extra.get("response_body"),
    }
    if http_extra.get("http_status") is not None:
        meta[k].pop("code_hint", None)


def _pagespeed_bulk_max_inflight() -> int:
    return PAGESPEED_SYNC_WORKERS


def _sleep_interruptible_seconds(total_seconds: float, cancel_check) -> None:
    """Sleep for up to ``total_seconds``, calling ``cancel_check`` at least once per second."""
    if total_seconds <= 0.0:
        return
    deadline = time.monotonic() + total_seconds
    while time.monotonic() < deadline:
        cancel_check()
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            break
        time.sleep(min(1.0, max(0.05, remaining)))


# PageSpeed bulk job: (not_before_monotonic, object_type, handle, url, strategy, pagespeed_429_requeue_pass)
_PageSpeedQueuedJob = tuple[float, str, str, str, str, int]


def bulk_refresh_pagespeed(db_path: str, throttle_seconds: float = 0.4, force_refresh: bool = False) -> dict:
    """Bulk PageSpeed: primary work in bounded batches with pauses, then one final batch for deferred failures.

    During the **primary** phase, ``rate_limited`` responses, hybrid ``requeue_429`` markers, and worker
    exceptions enqueue one retry in ``deferred_failure_targets`` (no terminal outcome / second deferral).
    After all primary chunks finish, those jobs run once in a **final** phase where outcomes are applied
    normally. Bulk sync does not use the global per-minute PageSpeed HTTP limiter (workers + batch pauses only).
    """
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
            base = _resolve_all_object_targets()(conn)
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
        SYNC_STATE["pagespeed_queue_total"] = summary["queue_total"]
        SYNC_STATE["pagespeed_queue_completed"] = 0
        SYNC_STATE["pagespeed_queue_inflight"] = 0
        _sync_current(
            f"PageSpeed queue prepared: {summary['queue_total']} stale run(s) "
            f"across {pending_objects} object(s), {summary['skipped_recent']} fully fresh"
        )

        if not queued_targets:
            SYNC_STATE["pagespeed_phase"] = "complete"
            SYNC_STATE["pagespeed_queue_details"] = []
            SYNC_STATE["pagespeed_queue_meta"] = {}
            SYNC_STATE["pagespeed_queue_baseline"] = 0
            _sync_current("PageSpeed queue empty (cache fresh). Catalog scores updated from cache.")
            return summary

        all_queued: list[_PageSpeedQueuedJob] = list(queued_targets)
        SYNC_STATE["pagespeed_queue_baseline"] = len(all_queued)
        SYNC_STATE["pagespeed_queue_meta"] = {}
        SYNC_STATE["pagespeed_queue_details"] = []

        progress_lock = threading.Lock()
        max_inflight = _pagespeed_bulk_max_inflight()
        append_sync_event(
            "pagespeed",
            f"PageSpeed: {max_inflight} workers, batches of {PAGESPEED_SYNC_BATCH_SIZE} "
            f"(+{int(PAGESPEED_SYNC_BATCH_PAUSE_SECONDS)}s between batches); no per-minute HTTP cap",
        )

        def _run_pagespeed_target(
            kind: str,
            handle: str,
            url: str,
            strategy: str,
            r429_pass: int,
        ) -> dict:
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
                    # Count each runPagespeed HTTP attempt for the sidebar Speed readout without acquiring
                    # the per-minute gate (bulk uses workers + batch pauses only).
                    before_each_run_pagespeed_http=record_pagespeed_http_api_call,
                    hybrid_pagespeed_429_retry=True,
                    pagespeed_429_requeue_pass=r429_pass,
                    on_hybrid_429_slowdown=lambda _e: None,
                    hybrid_429_adaptive_wait_seconds=lambda: 0.0,
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
                    }
                return {"status": "refreshed"}
            finally:
                worker_conn.close()

        def _submit_target(executor: ThreadPoolExecutor, future_to_target: dict) -> bool:
            if not pending_targets:
                return False
            if len(future_to_target) >= max_inflight:
                return False
            if pending_targets[0][0] > time.monotonic():
                return False
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
            )
            future_to_target[future] = (kind, handle, url, strategy, r429_pass)
            _emit_ps_queue_snapshot()
            return True

        deferred_failure_targets: list[_PageSpeedQueuedJob] = []
        failure_final_batch_active = False

        def _emit_ps_queue_snapshot() -> None:
            now = time.monotonic()
            meta = _pagespeed_queue_meta_map()
            rank = {"running": 3, "deferred": 2, "queued": 1}
            by_key: dict[str, dict[str, Any]] = {}

            def touch(
                not_before: float,
                kind: str,
                handle: str,
                url: str,
                strategy: str,
                r429_pass: int,
                phase: str,
            ) -> None:
                k = _pagespeed_queue_row_key(kind, handle, strategy)
                cur = by_key.get(k)
                if cur is None or rank[phase] > rank.get(str(cur.get("_phase") or "queued"), 0):
                    by_key[k] = {
                        "object_type": kind,
                        "handle": handle,
                        "url": url,
                        "strategy": strategy,
                        "not_before": float(not_before),
                        "_phase": phase,
                        "r429_pass": int(r429_pass),
                    }

            for _fut, (kind, handle, url, strategy, r429_pass) in future_to_target.items():
                touch(0.0, kind, handle, url, strategy, r429_pass, "running")

            for nb, kind, handle, url, strategy, r429_pass in list(pending_targets):
                touch(nb, kind, handle, url, strategy, r429_pass, "queued")

            for nb, kind, handle, url, strategy, r429_pass in list(deferred_failure_targets):
                touch(nb, kind, handle, url, strategy, r429_pass, "deferred")

            active = set(by_key.keys())
            for mk in list(meta.keys()):
                if mk not in active:
                    del meta[mk]

            rows_out: list[dict[str, Any]] = []
            for k in sorted(
                by_key.keys(),
                key=lambda kk: (
                    str(by_key[kk].get("strategy") or ""),
                    str(by_key[kk].get("handle") or ""),
                    str(by_key[kk].get("object_type") or ""),
                ),
            ):
                base = by_key[k]
                phase = str(base.get("_phase") or "queued")
                mraw = meta.get(k)
                m = mraw if isinstance(mraw, dict) else {}
                http_st = m.get("http_status")
                code_hint = str(m.get("code_hint") or "").upper()
                err_text = str(m.get("error") or "")
                if http_st is not None:
                    try:
                        tag = f"HTTP {int(http_st)}"
                    except (TypeError, ValueError):
                        tag = "HTTP ?"
                elif code_hint == "RATE":
                    tag = "RATE"
                elif code_hint == "429":
                    tag = "429"
                elif err_text:
                    tag = "ERR"
                elif phase == "running":
                    tag = "RUN"
                elif phase == "deferred":
                    tag = "RETRY"
                else:
                    nb = float(base.get("not_before") or 0.0)
                    tag = "WAIT" if nb > now else "READY"

                seq = m.get("seq")
                if seq is None:
                    seq = abs(hash(k)) % (10**9 - 1) + 1

                row_d: dict[str, Any] = {
                    "seq": int(seq),
                    "object_type": base["object_type"],
                    "handle": base["handle"],
                    "url": base["url"],
                    "strategy": str(base.get("strategy") or ""),
                    "code": tag,
                    "state": phase,
                    "error": err_text,
                }
                if http_st is not None:
                    try:
                        row_d["http_status"] = int(http_st)
                    except (TypeError, ValueError):
                        pass
                rb = m.get("response_body")
                if isinstance(rb, str) and rb.strip():
                    row_d["response_body"] = rb
                rows_out.append(row_d)

            SYNC_STATE["pagespeed_queue_details"] = rows_out

        def _register_extra_queued_jobs(n: int) -> None:
            if n <= 0:
                return
            with progress_lock:
                summary["queue_total"] += n
                SYNC_STATE["pagespeed_queue_total"] = summary["queue_total"]

        def _defer_job_for_final_batch(kind: str, handle: str, url: str, strategy: str, r429_pass: int) -> None:
            deferred_failure_targets.append((0.0, kind, handle, url, strategy, r429_pass))

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
                try:
                    result = future.result()
                except Exception as exc:
                    summary["errors"] += 1
                    SYNC_STATE["pagespeed_errors"] = summary["errors"]
                    _record_pagespeed_error(
                        kind,
                        handle,
                        url,
                        exc,
                        strategy=strategy,
                        append_final_batch_retry_note=not failure_final_batch_active,
                    )
                    if not failure_final_batch_active:
                        _defer_job_for_final_batch(kind, handle, url, strategy, r429_pass)
                    _raise_if_sync_cancelled()
                    return

                status = result["status"]
                if status == "requeue_429":
                    if not failure_final_batch_active:
                        append_sync_event(
                            "pagespeed",
                            f"429 deferred to final batch ({strategy}) {kind}:{handle}",
                        )
                        _pagespeed_queue_note_hint(
                            kind,
                            handle,
                            url,
                            strategy,
                            code_hint="429",
                            error="HTTP 429 — scheduled for final batch retry",
                        )
                        _defer_job_for_final_batch(kind, handle, url, strategy, 1)
                    else:
                        summary["errors"] += 1
                        SYNC_STATE["pagespeed_errors"] = summary["errors"]
                        _record_pagespeed_error(
                            kind,
                            handle,
                            url,
                            RuntimeError("PageSpeed API returned HTTP 429 after hybrid retry (final batch)"),
                            strategy=strategy,
                        )
                elif status == "rate_limited":
                    if not failure_final_batch_active:
                        _pagespeed_queue_note_hint(
                            kind,
                            handle,
                            url,
                            strategy,
                            code_hint="RATE",
                            error="PageSpeed API rate limited — scheduled for final batch retry",
                        )
                        _defer_job_for_final_batch(kind, handle, url, strategy, r429_pass)
                    else:
                        summary["rate_limited"] += 1
                        SYNC_STATE["pagespeed_rate_limited"] = summary["rate_limited"]
                else:
                    _pagespeed_queue_clear_success(kind, handle, strategy)
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
                _raise_if_sync_cancelled()
            finally:
                _emit_ps_queue_snapshot()

        batch_size = max(1, int(PAGESPEED_SYNC_BATCH_SIZE))
        pause_s = max(0.0, float(PAGESPEED_SYNC_BATCH_PAUSE_SECONDS))

        def _drain_pagespeed_work() -> None:
            while pending_targets or future_to_target:
                _emit_ps_queue_snapshot()
                _raise_if_sync_cancelled()

                submitted = False
                while _submit_target(executor, future_to_target):
                    submitted = True
                    _raise_if_sync_cancelled()
                if submitted:
                    continue

                if future_to_target:
                    timeout = 1.0
                    if pending_targets and len(future_to_target) < max_inflight:
                        head_delay = max(0.0, pending_targets[0][0] - time.monotonic())
                        timeout = min(max(head_delay, 0.05), 1.0)
                    done, _ = wait(set(future_to_target), timeout=timeout, return_when=FIRST_COMPLETED)
                    for future in done:
                        _handle_finished_future(future, future_to_target)
                    continue

                if pending_targets:
                    head_delay = max(0.0, pending_targets[0][0] - time.monotonic())
                    time.sleep(min(max(head_delay, 0.05), 1.0))

        with ThreadPoolExecutor(max_workers=max_inflight) as executor:
            future_to_target: dict = {}
            n_jobs = len(all_queued)
            for batch_start in range(0, n_jobs, batch_size):
                if batch_start > 0:
                    append_sync_event(
                        "pagespeed",
                        f"PageSpeed batch pause: sleeping {int(pause_s)}s before next chunk "
                        f"({batch_start}/{n_jobs} job(s) done)",
                    )
                    _sleep_interruptible_seconds(pause_s, _raise_if_sync_cancelled)
                pending_targets = deque(all_queued[batch_start : batch_start + batch_size])
                _drain_pagespeed_work()

            if deferred_failure_targets:
                n_final = len(deferred_failure_targets)
                append_sync_event(
                    "pagespeed",
                    f"PageSpeed final batch: {n_final} job(s) deferred from earlier errors or rate limits",
                )
                failure_final_batch_active = True
                pending_targets = deque(deferred_failure_targets)
                deferred_failure_targets.clear()
                _register_extra_queued_jobs(n_final)
                _emit_ps_queue_snapshot()
                _drain_pagespeed_work()
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
