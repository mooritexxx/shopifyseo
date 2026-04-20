"""Live sync queue snapshots for GSC, GA4, Index, and Shopify (UX only; does not affect sync logic).

Queue rows for those scopes are emitted in full (no row-count cap) and successful rows are removed
when ``sync_queue_mark_done(..., pop_completed=True)`` runs. PageSpeed uses a separate snapshot path
in ``_sync.py`` but also emits the full active queue (no cap).
"""

from __future__ import annotations

import threading
from typing import Any

from ._state import PAGESPEED_ERROR_DETAILS_MAX, SYNC_STATE

_SYNC_QUEUE_LOCK = threading.Lock()

# scope -> row_key -> internal row (object_type, handle, url, strategy, seq, _phase, _error)
_catalog_rows: dict[str, dict[str, dict[str, Any]]] = {}

# Scopes whose queue snapshot lists every pending row (no slice cap); successes are popped elsewhere.
_UNCAPPED_QUEUE_SCOPES = frozenset({"shopify", "gsc", "ga4", "index"})


def sync_queue_row_key(object_type: str, handle: str) -> str:
    return f"{object_type}:{handle}"


def _emit_catalog_snapshot_locked(scope: str) -> None:
    rows = _catalog_rows.get(scope) or {}
    rows_out: list[dict[str, Any]] = []
    for rk in sorted(
        rows.keys(),
        key=lambda kk: (
            str(rows[kk].get("strategy") or ""),
            str(rows[kk].get("handle") or ""),
            str(rows[kk].get("object_type") or ""),
        ),
    ):
        base = rows[rk]
        phase = str(base.get("_phase") or "queued")
        err_text = str(base.get("_error") or "")
        if phase == "running":
            tag = "RUN"
        elif phase == "error":
            tag = "ERR"
        elif phase == "done":
            tag = "OK"
        else:
            tag = "READY"
        seq = int(base.get("seq") or 0) or (abs(hash(rk)) % (10**9 - 1) + 1)
        row_d: dict[str, Any] = {
            "seq": seq,
            "object_type": base["object_type"],
            "handle": base["handle"],
            "url": base.get("url") or "",
            "strategy": str(base.get("strategy") or ""),
            "code": tag,
            "state": phase,
            "error": err_text,
        }
        rows_out.append(row_d)
    cap = None if scope in _UNCAPPED_QUEUE_SCOPES else PAGESPEED_ERROR_DETAILS_MAX
    if cap is not None:
        rows_out = rows_out[:cap]
    SYNC_STATE[f"{scope}_queue_details"] = rows_out


def sync_queue_reset(scope: str) -> None:
    with _SYNC_QUEUE_LOCK:
        _catalog_rows.pop(scope, None)
        SYNC_STATE[f"{scope}_queue_details"] = []


def sync_queue_reset_all() -> None:
    for s in ("gsc", "ga4", "index", "shopify"):
        sync_queue_reset(s)


def catalog_sync_row_key(kind: str, handle: str, url: str) -> str:
    """Stable key when GA4/index may have empty kind/handle for path-only rows."""
    if (kind or "").strip() and (handle or "").strip():
        return sync_queue_row_key(kind, handle)
    return sync_queue_row_key((kind or "_path").strip() or "_path", (handle or url or "unknown")[:400])


def sync_queue_seed(scope: str, targets: list[tuple[str, str, str]]) -> None:
    """targets: (kind, handle, url) as in bulk_refresh_*."""
    with _SYNC_QUEUE_LOCK:
        rows: dict[str, dict[str, Any]] = {}
        for n, (kind, handle, url) in enumerate(targets, start=1):
            rk = catalog_sync_row_key(kind, handle, url)
            rows[rk] = {
                "object_type": kind,
                "handle": handle,
                "url": url or "",
                "strategy": "",
                "seq": n,
                "_phase": "queued",
                "_error": "",
            }
        _catalog_rows[scope] = rows
        _emit_catalog_snapshot_locked(scope)


def sync_queue_mark_running(scope: str, row_key: str) -> None:
    with _SYNC_QUEUE_LOCK:
        rows = _catalog_rows.get(scope)
        if not rows:
            return
        row = rows.get(row_key)
        if row is None:
            return
        row["_phase"] = "running"
        row["_error"] = ""
        _emit_catalog_snapshot_locked(scope)


def sync_queue_mark_done(
    scope: str,
    row_key: str,
    ok: bool,
    error: str | None = None,
    *,
    pop_completed: bool = False,
) -> None:
    with _SYNC_QUEUE_LOCK:
        rows = _catalog_rows.get(scope)
        if not rows:
            return
        row = rows.get(row_key)
        if row is None:
            return
        if ok and pop_completed:
            rows.pop(row_key, None)
        else:
            row["_phase"] = "done" if ok else "error"
            row["_error"] = (error or "").strip() if not ok else ""
        _emit_catalog_snapshot_locked(scope)


def sync_queue_emit(scope: str) -> None:
    """Re-emit current snapshot (e.g. after seed from caller)."""
    with _SYNC_QUEUE_LOCK:
        _emit_catalog_snapshot_locked(scope)
