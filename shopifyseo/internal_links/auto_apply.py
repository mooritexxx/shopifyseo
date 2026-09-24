"""Selective auto-apply for high-confidence internal link suggestions.

Phase E: Auto-apply defaults OFF. Only phrase_wrap suggestions with strong anchors
and scores above threshold are auto-applied. Never auto-applies ai_woven.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Callable

from .apply import apply_suggestion, _log_suggestion_event
from .anchors import is_weak_anchor

logger = logging.getLogger(__name__)

# Settings keys for auto-apply (all stored in service_settings)
AUTO_APPLY_ENABLED_KEY = "internal_link_auto_apply_enabled"
AUTO_APPLY_MIN_SCORE_KEY = "internal_link_auto_apply_min_score"
AUTO_APPLY_MAX_PER_DAY_KEY = "internal_link_auto_apply_max_per_day"
AUTO_APPLY_KINDS_KEY = "internal_link_auto_apply_kinds"

# Default values
DEFAULT_AUTO_APPLY_ENABLED = False
DEFAULT_AUTO_APPLY_MIN_SCORE = 1.2
DEFAULT_AUTO_APPLY_MAX_PER_DAY = 10
DEFAULT_AUTO_APPLY_KINDS = "phrase_wrap"  # Never auto-apply ai_woven


def get_auto_apply_settings(conn: sqlite3.Connection) -> dict:
    """Get current auto-apply settings from service_settings."""
    from ..dashboard_google import get_service_setting
    
    def _get(key: str, default: str) -> str:
        try:
            val = get_service_setting(conn, key, "")
            return val if val else default
        except Exception:
            return default
    
    enabled_str = _get(AUTO_APPLY_ENABLED_KEY, "0")
    enabled = enabled_str.lower() in ("1", "true", "yes", "on")
    
    try:
        min_score = float(_get(AUTO_APPLY_MIN_SCORE_KEY, str(DEFAULT_AUTO_APPLY_MIN_SCORE)))
    except ValueError:
        min_score = DEFAULT_AUTO_APPLY_MIN_SCORE
    
    try:
        max_per_day = int(_get(AUTO_APPLY_MAX_PER_DAY_KEY, str(DEFAULT_AUTO_APPLY_MAX_PER_DAY)))
    except ValueError:
        max_per_day = DEFAULT_AUTO_APPLY_MAX_PER_DAY
    
    kinds_str = _get(AUTO_APPLY_KINDS_KEY, DEFAULT_AUTO_APPLY_KINDS)
    kinds = [k.strip() for k in kinds_str.split(",") if k.strip()]
    # Never allow ai_woven in auto-apply
    kinds = [k for k in kinds if k != "ai_woven"]
    if not kinds:
        kinds = ["phrase_wrap"]
    
    return {
        "enabled": enabled,
        "min_score": min_score,
        "max_per_day": max_per_day,
        "kinds": kinds,
    }


def get_auto_applied_today_count(conn: sqlite3.Connection) -> int:
    """Count how many suggestions were auto-applied today."""
    today_start = int(time.time()) - (int(time.time()) % 86400)
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM link_suggestion_events "
        "WHERE event_type = 'auto_apply' AND created_at >= ?",
        (today_start,),
    ).fetchone()
    return row["c"] if row else 0


def find_auto_apply_candidates(
    conn: sqlite3.Connection,
    settings: dict | None = None,
) -> list[sqlite3.Row]:
    """Find suggestions eligible for auto-apply.
    
    Criteria:
    - status = 'suggested'
    - kind in allowed kinds (phrase_wrap only by default)
    - NOT weak_anchor
    - score >= min_score
    - source and target are published and reachable
    """
    if settings is None:
        settings = get_auto_apply_settings(conn)
    
    if not settings["enabled"]:
        return []
    
    min_score = settings["min_score"]
    kinds = settings["kinds"]
    
    # Build query for eligible suggestions
    kind_placeholders = ",".join("?" for _ in kinds)
    query = f"""
        SELECT * FROM link_suggestions
        WHERE status = 'suggested'
          AND kind IN ({kind_placeholders})
          AND COALESCE(weak_anchor, 0) = 0
          AND score >= ?
        ORDER BY score DESC
        LIMIT 100
    """
    params = list(kinds) + [min_score]
    
    return conn.execute(query, params).fetchall()


def run_auto_apply(
    conn: sqlite3.Connection,
    base_url: str,
    push_fn: Callable | None = None,
    sanitize_fn: Callable | None = None,
    dry_run: bool = False,
) -> dict:
    """Run selective auto-apply for eligible suggestions.
    
    Args:
        conn: Database connection
        base_url: Store base URL
        push_fn: Optional push function (for testing)
        sanitize_fn: Optional sanitize function (for testing)
        dry_run: If True, don't actually apply (just return candidates)
        
    Returns:
        Dict with counts and details
    """
    settings = get_auto_apply_settings(conn)
    
    if not settings["enabled"]:
        return {"status": "disabled", "applied": 0, "skipped": 0, "errors": []}
    
    already_applied_today = get_auto_applied_today_count(conn)
    remaining_quota = settings["max_per_day"] - already_applied_today
    
    if remaining_quota <= 0:
        return {
            "status": "quota_reached",
            "applied": 0,
            "skipped": 0,
            "errors": [],
            "quota_used": already_applied_today,
            "quota_max": settings["max_per_day"],
        }
    
    candidates = find_auto_apply_candidates(conn, settings)
    
    if dry_run:
        return {
            "status": "dry_run",
            "candidates": len(candidates),
            "would_apply": min(len(candidates), remaining_quota),
            "settings": settings,
        }
    
    applied = 0
    skipped = 0
    errors = []
    
    for sug in candidates:
        if applied >= remaining_quota:
            skipped += len(candidates) - applied - skipped
            break
        
        try:
            result = apply_suggestion(
                conn,
                sug["id"],
                base_url=base_url,
                push_fn=push_fn,
                sanitize_fn=sanitize_fn,
            )
            
            if result.get("status") == "applied":
                # Log as auto_apply instead of apply
                conn.execute(
                    """
                    UPDATE link_suggestion_events 
                    SET event_type = 'auto_apply' 
                    WHERE suggestion_id = ? AND event_type = 'apply'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (sug["id"],),
                )
                conn.commit()
                applied += 1
            else:
                skipped += 1
        except Exception as e:
            logger.warning("Auto-apply failed for suggestion %d: %s", sug["id"], e)
            errors.append({"suggestion_id": sug["id"], "error": str(e)})
            skipped += 1
    
    return {
        "status": "completed",
        "applied": applied,
        "skipped": skipped,
        "errors": errors,
        "quota_used": already_applied_today + applied,
        "quota_max": settings["max_per_day"],
    }
