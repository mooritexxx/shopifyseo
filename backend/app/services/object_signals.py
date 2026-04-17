"""Thin re-exports from the canonical dashboard_detail_common module."""
from __future__ import annotations

from shopifyseo.dashboard_detail_common import (
    inspection_for_display,
    load_object_signals,
    parse_tags_json,
    search_console_inspect_href,
)

__all__ = [
    "inspection_for_display",
    "load_object_signals",
    "parse_tags_json",
    "search_console_inspect_href",
]
