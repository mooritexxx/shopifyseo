"""Ephemeral NDJSON logger for debug sessions (Cursor agent).

Writes to ``$SHOPIFYSEO_DEBUG_LOG`` when set, otherwise silently does nothing.
Designed so the import always succeeds (CI, fresh clones, non-Cursor users) while
still letting a local operator capture structured traces by setting the env var.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

_LOG_PATH = os.environ.get("SHOPIFYSEO_DEBUG_LOG") or ""
_SESSION = os.environ.get("SHOPIFYSEO_DEBUG_SESSION") or "local"


def agent_debug_log(*, hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    if not _LOG_PATH:
        return
    try:
        line = json.dumps(
            {
                "sessionId": _SESSION,
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data,
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
