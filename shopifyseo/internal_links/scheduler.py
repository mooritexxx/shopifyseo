"""Event-driven debounced refresh for internal link suggestions."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_COALESCE_SECONDS = 45
_PENDING_REFRESH: dict = {"scheduled_at": 0, "timer": None}
_LOCK = threading.Lock()


def schedule_internal_link_refresh(
    db_path: str,
    coalesce_seconds: float = DEFAULT_COALESCE_SECONDS,
    run_fn: Callable[[sqlite3.Connection], int] | None = None,
) -> bool:
    """Schedule a debounced internal link refresh.

    Multiple calls within `coalesce_seconds` are collapsed into a single refresh.
    Returns True if a new timer was scheduled, False if coalesced into existing.
    """
    from ..dashboard_actions._state import _db_connect_for_actions

    def _worker() -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = _db_connect_for_actions(db_path)
            if run_fn:
                run_fn(conn)
            else:
                from .pipeline import generate_link_suggestions

                generate_link_suggestions(conn)
        except Exception:
            logger.warning("Debounced internal link refresh failed", exc_info=True)
        finally:
            if conn is not None:
                conn.close()
            with _LOCK:
                _PENDING_REFRESH["scheduled_at"] = 0
                _PENDING_REFRESH["timer"] = None

    now = time.time()
    with _LOCK:
        existing_scheduled = _PENDING_REFRESH["scheduled_at"]
        if existing_scheduled and (existing_scheduled - now) > 0:
            return False
        if _PENDING_REFRESH["timer"] is not None:
            _PENDING_REFRESH["timer"].cancel()
        timer = threading.Timer(coalesce_seconds, _worker)
        timer.daemon = True
        _PENDING_REFRESH["scheduled_at"] = now + coalesce_seconds
        _PENDING_REFRESH["timer"] = timer
        timer.start()
        return True


def cancel_pending_refresh() -> bool:
    """Cancel any pending debounced refresh. Returns True if one was cancelled."""
    with _LOCK:
        timer = _PENDING_REFRESH.get("timer")
        if timer is not None:
            timer.cancel()
            _PENDING_REFRESH["timer"] = None
            _PENDING_REFRESH["scheduled_at"] = 0
            return True
        return False
