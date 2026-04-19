"""AI job management: background generation, field regeneration, object signal refresh."""
import logging
import queue
import sqlite3
import threading
import time
import uuid

logger = logging.getLogger(__name__)

from .. import dashboard_ai as dai
from .. import dashboard_google as dg
from .. import dashboard_queries as dq
from ..dashboard_http import HttpRequestError
from ..dashboard_store import (
    refresh_object_structured_seo_data,
)
from ._state import (
    AI_JOBS,
    AI_JOBS_LOCK,
    AI_JOB_QUEUES,
    AI_STATE,
    SYNC_LOCK,
    _ai_cancelled,
    _db_connect_for_actions,
    _raise_if_ai_cancelled,
    _snapshot_ai_state,
    _step_result,
    _sync_global_ai_state,
    clear_ai_last_error,
)
from ..exceptions import AICancelledError
from ..shopify_catalog_sync import sync_products

# ---------------------------------------------------------------------------
# AI job state helpers
# ---------------------------------------------------------------------------


def _new_ai_state(scope: str, job_id: str) -> dict:
    started_at = int(time.time())
    object_type = ""
    handle = ""
    field = ""
    mode = "full_generation"
    if scope.count(":") >= 2:
        object_type, handle, field = scope.split(":", 2)
        mode = "field_regeneration"
    elif ":" in scope:
        object_type, handle = scope.split(":", 1)
    return {
        "job_id": job_id,
        "running": True,
        "scope": scope,
        "mode": mode,
        "object_type": object_type,
        "handle": handle,
        "field": field,
        "started_at": started_at,
        "finished_at": 0,
        "stage": "starting",
        "stage_label": "Starting AI generation",
        "active_model": "",
        "step_index": 0,
        "step_total": 0,
        "total": 0,
        "done": 0,
        "current": "",
        "successes": 0,
        "failures": 0,
        "last_error": "",
        "last_result": None,
        "stage_started_at": started_at,
        "steps": [],
        "cancel_requested": False,
    }


def _register_ai_job(scope: str) -> tuple[str, dict]:
    job_id = str(uuid.uuid4())
    state = _new_ai_state(scope, job_id)
    with AI_JOBS_LOCK:
        AI_JOBS[job_id] = state
        AI_JOB_QUEUES[job_id] = queue.Queue()
        _sync_global_ai_state(state)
    return job_id, state


# ---------------------------------------------------------------------------
# AI timeline tracking
# ---------------------------------------------------------------------------


def _update_ai_timeline(state: dict, payload: dict) -> None:
    now = int(time.time())
    stage = str(payload.get("stage") or state.get("stage") or "idle")
    label = str(payload.get("message") or state.get("stage_label") or "")
    model = str(payload.get("model") or state.get("active_model") or "")
    step_index = int(payload.get("step_index") or state.get("step_index") or 0)
    step_total = int(payload.get("step_total") or state.get("step_total") or 0)

    previous_stage = str(state.get("stage") or "")
    previous_started = int(state.get("stage_started_at") or now)
    steps = list(state.get("steps") or [])
    if previous_stage and previous_stage != "idle" and previous_stage != stage:
        for step in reversed(steps):
            if step.get("stage") == previous_stage and not step.get("finished_at"):
                step["finished_at"] = now
                step["duration_seconds"] = max(0, now - previous_started)
                step["status"] = "completed"
                break

    if not steps or steps[-1].get("stage") != stage:
        steps.append(
            {
                "stage": stage,
                "label": label,
                "model": model,
                "started_at": now,
                "finished_at": 0,
                "duration_seconds": 0,
                "status": "running",
            }
        )
    else:
        steps[-1]["label"] = label
        steps[-1]["model"] = model
        steps[-1]["status"] = "running"

    state["steps"] = steps
    state["stage"] = stage
    state["stage_label"] = label
    state["active_model"] = model
    state["step_index"] = step_index
    state["step_total"] = step_total
    state["stage_started_at"] = now
    with AI_JOBS_LOCK:
        if state.get("job_id") in AI_JOBS:
            AI_JOBS[state["job_id"]] = state
        _sync_global_ai_state(state)
    _emit_job_event(state.get("job_id", ""), {
        "type": "progress",
        "stage": stage,
        "stage_label": label,
        "active_model": model,
        "step_index": step_index,
        "step_total": step_total,
    })


def _finalize_ai_timeline(state: dict, final_stage: str, final_label: str) -> None:
    now = int(time.time())
    previous_stage = str(state.get("stage") or "")
    previous_started = int(state.get("stage_started_at") or now)
    steps = list(state.get("steps") or [])
    if previous_stage and previous_stage != "idle":
        for step in reversed(steps):
            if step.get("stage") == previous_stage and not step.get("finished_at"):
                step["finished_at"] = now
                step["duration_seconds"] = max(0, now - previous_started)
                step["status"] = "completed"
                break
    steps.append(
        {
            "stage": final_stage,
            "label": final_label,
            "model": "",
            "started_at": now,
            "finished_at": now,
            "duration_seconds": 0,
            "status": "completed",
        }
    )
    state["steps"] = steps
    state["stage"] = final_stage
    state["stage_label"] = final_label
    state["active_model"] = ""
    state["stage_started_at"] = now
    state["finished_at"] = now
    with AI_JOBS_LOCK:
        if state.get("job_id") in AI_JOBS:
            AI_JOBS[state["job_id"]] = state
        _sync_global_ai_state(state)
    _emit_job_event(state.get("job_id", ""), {
        "type": "done" if final_stage == "complete" else final_stage,
        "stage": final_stage,
        "stage_label": final_label,
    })


# ---------------------------------------------------------------------------
# Job event queue (SSE)
# ---------------------------------------------------------------------------


def _emit_job_event(job_id: str, event: dict) -> None:
    """Push an event to the job's SSE queue (non-blocking)."""
    with AI_JOBS_LOCK:
        q = AI_JOB_QUEUES.get(job_id)
    if q is not None:
        try:
            q.put_nowait(event)
        except queue.Full:
            pass


def consume_job_events(job_id: str, timeout: float = 1.0) -> list[dict]:
    """Drain all pending events from a job's queue. Returns empty list if no events."""
    with AI_JOBS_LOCK:
        q = AI_JOB_QUEUES.get(job_id)
    if q is None:
        return []
    events = []
    try:
        while True:
            events.append(q.get_nowait())
    except queue.Empty:
        pass
    if not events:
        try:
            events.append(q.get(timeout=timeout))
        except queue.Empty:
            pass
    return events


# ---------------------------------------------------------------------------
# AI generation runners
# ---------------------------------------------------------------------------


def _targets_for_ai(conn: sqlite3.Connection, scope: str) -> list[tuple[str, str]]:
    if ":" in scope:
        object_type, handle = scope.split(":", 1)
        if object_type in {"product", "collection", "page", "blog_article"} and handle:
            return [(object_type, handle)]
    if scope == "product":
        return [("product", row["handle"]) for row in dq.fetch_all_products(conn)]
    if scope == "collection":
        return [("collection", row["handle"]) for row in dq.fetch_all_collections(conn)]
    if scope == "page":
        return [("page", row["handle"]) for row in dq.fetch_all_pages(conn)]
    return []


def run_ai_generation(db_path: str, scope: str, job_id: str) -> dict:
    with AI_JOBS_LOCK:
        state = AI_JOBS[job_id]
    clear_ai_last_error()
    conn = _db_connect_for_actions(db_path)
    try:
        targets = _targets_for_ai(conn, scope)
        target_type = targets[0][0] if targets else (scope.split(":", 1)[0] if ":" in scope else scope)
        state["stage"] = f"generating_{target_type}"
        state["total"] = len(targets)
        for object_type, handle in targets:
            _raise_if_ai_cancelled(job_id)
            state["current"] = f"{object_type}:{handle}"
            with AI_JOBS_LOCK:
                AI_JOBS[job_id] = state
                _sync_global_ai_state(state)
            def _progress_update(payload: dict) -> None:
                _raise_if_ai_cancelled(job_id)
                _update_ai_timeline(state, payload)
                if payload.get("field_complete"):
                    _emit_job_event(job_id, {
                        "type": "field_complete",
                        "field": payload["field_complete"],
                        "value": payload.get("field_value", ""),
                    })
            try:
                logger.info(
                    "Starting full generation: object_type=%s, handle=%s, job_id=%s, mode=%s",
                    object_type, handle, job_id, state.get("mode"),
                )
                dai.generate_recommendation(
                    conn,
                    object_type,
                    handle,
                    progress_callback=_progress_update,
                    cancel_callback=lambda: _ai_cancelled(job_id),
                )
                logger.info(
                    "Full generation completed: object_type=%s, handle=%s, job_id=%s",
                    object_type, handle, job_id,
                )
                state["successes"] += 1
            except AICancelledError:
                _finalize_ai_timeline(state, "cancelled", "AI generation cancelled")
                state["last_result"] = {
                    "job_id": job_id,
                    "scope": scope,
                    "total": state["total"],
                    "successes": state["successes"],
                    "failures": state["failures"],
                    "cancelled": True,
                }
                return dict(state["last_result"])
            except Exception as exc:
                state["failures"] += 1
                state["last_error"] = str(exc)
                logger.error(
                    "AI generation failed for %s/%s in job %s: %s",
                    object_type, handle, job_id, exc,
                    exc_info=True,
                    extra={
                        "job_id": job_id,
                        "object_type": object_type,
                        "handle": handle,
                        "scope": scope,
                    }
                )
            state["done"] += 1
        _raise_if_ai_cancelled(job_id)
        _finalize_ai_timeline(state, "complete", "AI generation complete")
        state["last_result"] = {
            "job_id": job_id,
            "scope": scope,
            "total": state["total"],
            "successes": state["successes"],
            "failures": state["failures"],
        }
        return dict(state["last_result"])
    finally:
        state["running"] = False
        with AI_JOBS_LOCK:
            AI_JOBS[job_id] = state
            AI_JOB_QUEUES.pop(job_id, None)
            _sync_global_ai_state(state)
        conn.close()


def start_ai_background(db_path: str, scope: str) -> tuple[bool, dict]:
    job_id, state = _register_ai_job(scope)

    def worker():
        try:
            run_ai_generation(db_path, scope, job_id)
        except Exception as exc:
            state["stage"] = "error"
            state["last_error"] = str(exc)
            state["running"] = False
            with AI_JOBS_LOCK:
                AI_JOBS[job_id] = state
                _sync_global_ai_state(state)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True, _snapshot_ai_state(state)


def start_ai_object_background(db_path: str, object_type: str, handle: str) -> tuple[bool, dict]:
    return start_ai_background(db_path, f"{object_type}:{handle}")


def run_ai_field_regeneration(db_path: str, object_type: str, handle: str, field: str, accepted_fields: dict[str, str], job_id: str) -> dict:
    with AI_JOBS_LOCK:
        state = AI_JOBS[job_id]
    clear_ai_last_error()
    conn = _db_connect_for_actions(db_path)
    try:
        state["current"] = f"{object_type}:{handle}:{field}"
        state["total"] = 1
        with AI_JOBS_LOCK:
            AI_JOBS[job_id] = state
            _sync_global_ai_state(state)

        def _progress_update(payload: dict) -> None:
            _raise_if_ai_cancelled(job_id)
            _update_ai_timeline(state, payload)
            if payload.get("field_complete"):
                _emit_job_event(job_id, {
                    "type": "field_complete",
                    "field": payload["field_complete"],
                    "value": payload.get("field_value", ""),
                })

        try:
            _raise_if_ai_cancelled(job_id)
            logger.info(
                "Starting field regeneration: object_type=%s, handle=%s, field=%s, job_id=%s, mode=%s",
                object_type, handle, field, job_id, state.get("mode"),
            )
            result = dai.generate_field_recommendation(
                conn,
                object_type,
                handle,
                field,
                accepted_fields,
                progress_callback=_progress_update,
                cancel_callback=lambda: _ai_cancelled(job_id),
            )
            logger.info(
                "Field regeneration completed: object_type=%s, handle=%s, field=%s, job_id=%s, result_field=%s",
                object_type, handle, field, job_id, result.get("field"),
            )
            state["successes"] = 1
            state["done"] = 1
            state["last_result"] = {
                **result,
                "job_id": job_id,
                "object_type": object_type,
                "handle": handle,
                "mode": "field_regeneration",
            }
            _finalize_ai_timeline(state, "complete", f"{field.replace('_', ' ')} regeneration complete")
            return dict(state["last_result"])
        except AICancelledError:
            state["last_result"] = {
                "job_id": job_id,
                "object_type": object_type,
                "handle": handle,
                "field": field,
                "mode": "field_regeneration",
                "cancelled": True,
            }
            _finalize_ai_timeline(state, "cancelled", f"{field.replace('_', ' ')} regeneration cancelled")
            return dict(state["last_result"])
        except Exception as exc:
            state["failures"] = 1
            state["done"] = 1
            state["last_error"] = str(exc)
            logger.error(
                "Field regeneration failed: object_type=%s, handle=%s, field=%s, job_id=%s: %s",
                object_type, handle, field, job_id, exc,
                exc_info=True,
                extra={
                    "job_id": job_id,
                    "object_type": object_type,
                    "handle": handle,
                    "field": field,
                    "mode": "field_regeneration",
                }
            )
            _finalize_ai_timeline(state, "error", f"{field.replace('_', ' ')} regeneration failed")
            raise
    finally:
        state["running"] = False
        with AI_JOBS_LOCK:
            AI_JOBS[job_id] = state
            AI_JOB_QUEUES.pop(job_id, None)
            _sync_global_ai_state(state)
        conn.close()


def start_ai_field_background(db_path: str, object_type: str, handle: str, field: str, accepted_fields: dict[str, str]) -> tuple[bool, dict]:
    job_id, state = _register_ai_job(f"{object_type}:{handle}:{field}")

    def worker():
        try:
            run_ai_field_regeneration(db_path, object_type, handle, field, accepted_fields, job_id)
        except Exception:
            pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return True, _snapshot_ai_state(state)


# ---------------------------------------------------------------------------
# Object-level operations
# ---------------------------------------------------------------------------


def generate_ai_for_object(db_connect, db_path: str, object_type: str, handle: str) -> dict:
    conn = db_connect()
    try:
        recommendation = dai.generate_recommendation(conn, object_type, handle)
        refresh_object_structured_seo_data(conn, object_type, handle)
        return recommendation
    finally:
        conn.close()


def refresh_object_signal_step(
    db_connect, kind: str, handle: str, step: str, db_path: str | None = None, *, gsc_period: str = "mtd"
) -> dict:
    conn = db_connect()
    try:
        url = dq.object_url(kind, handle)
        if step == "shopify":
            if kind != "product" or not db_path:
                return _step_result("skipped", "Shopify snapshot is only available for products.")
            try:
                sync_products(db_path, 50)
                return _step_result("success", "Shopify product snapshot refreshed.")
            except Exception:
                return _step_result("error", "Shopify sync failed. Existing snapshot kept.")
        if step == "gsc" or (isinstance(step, str) and step.startswith("gsc_")):
            try:
                gsc_payload = dg.get_search_console_url_detail(
                    conn,
                    url,
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                    gsc_period=gsc_period,
                )
                refresh_object_structured_seo_data(conn, kind, handle)
                row_count = len(gsc_payload.get("page_rows", []))
                return _step_result("success", f"Search Console refreshed. {row_count} page row(s) cached.")
            except Exception as exc:
                return _step_result("error", f"Search Console refresh failed: {exc}")
        if step == "index":
            try:
                inspection_payload = dg.get_url_inspection(
                    conn,
                    url,
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                )
                refresh_object_structured_seo_data(conn, kind, handle)
                idx = inspection_payload.get("inspectionResult", {}).get("indexStatusResult", {}) or {}
                label = idx.get("indexingState") or idx.get("coverageState") or "Inspection refreshed."
                return _step_result("success", label)
            except Exception as exc:
                return _step_result("error", f"Index status refresh failed: {exc}")
        if step == "speed":
            try:
                pagespeed_payload = dg.get_pagespeed(
                    conn,
                    url,
                    "mobile",
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                )
                refresh_object_structured_seo_data(conn, kind, handle)
                cache_meta = pagespeed_payload.get("_cache") or {}
                cats = pagespeed_payload.get("lighthouseResult", {}).get("categories", {})
                perf = cats.get("performance", {}).get("score")
                if cache_meta.get("rate_limited"):
                    return _step_result("warning", "PageSpeed rate limited. Stale cached data kept.")
                if perf is None:
                    return _step_result("warning", "PageSpeed refreshed, but no Lighthouse score was returned.")
                return _step_result("success", f"PageSpeed (mobile) refreshed. Performance {int(perf * 100)}.")
            except HttpRequestError as exc:
                return _step_result("error", f"PageSpeed refresh failed: {exc}")
            except Exception as exc:
                return _step_result("error", f"PageSpeed refresh failed: {exc}")
        if step == "speed_desktop":
            try:
                pagespeed_payload = dg.get_pagespeed(
                    conn,
                    url,
                    "desktop",
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                )
                refresh_object_structured_seo_data(conn, kind, handle)
                cache_meta = pagespeed_payload.get("_cache") or {}
                cats = pagespeed_payload.get("lighthouseResult", {}).get("categories", {})
                perf = cats.get("performance", {}).get("score")
                if cache_meta.get("rate_limited"):
                    return _step_result("warning", "PageSpeed rate limited. Stale cached data kept.")
                if perf is None:
                    return _step_result("warning", "PageSpeed refreshed, but no Lighthouse score was returned.")
                return _step_result("success", f"PageSpeed (desktop) refreshed. Performance {int(perf * 100)}.")
            except HttpRequestError as exc:
                return _step_result("error", f"PageSpeed refresh failed: {exc}")
            except Exception as exc:
                return _step_result("error", f"PageSpeed refresh failed: {exc}")
        if step == "ga4":
            try:
                dg.get_ga4_url_detail(
                    conn,
                    url,
                    refresh=True,
                    object_type=kind,
                    object_handle=handle,
                )
                refresh_object_structured_seo_data(conn, kind, handle)
                return _step_result("success", "GA4 refreshed for this URL.")
            except Exception as exc:
                return _step_result("error", f"GA4 refresh failed: {exc}")
        return _step_result("error", f"Unknown refresh step: {step}")
    finally:
        conn.close()


def refresh_object_signals(
    db_connect, kind: str, handle: str, db_path: str | None = None, *, gsc_period: str = "mtd"
) -> dict:
    ordered_steps = ("gsc", "index", "speed", "speed_desktop")
    return {
        step: refresh_object_signal_step(db_connect, kind, handle, step, db_path=db_path, gsc_period=gsc_period)
        for step in ordered_steps
    }


def refresh_and_get_inspection_link(db_connect, kind: str, handle: str) -> str:
    conn = db_connect()
    try:
        url = dq.object_url(kind, handle)
        payload = dg.get_url_inspection(
            conn,
            url,
            refresh=True,
            object_type=kind,
            object_handle=handle,
        )
        refresh_object_structured_seo_data(conn, kind, handle)
        link = ((payload.get("inspectionResult") or {}).get("inspectionResultLink") or "").strip()
        if not link:
            raise RuntimeError("Google did not return an inspection deep link for this URL.")
        return link
    finally:
        conn.close()
