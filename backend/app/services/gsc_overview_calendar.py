"""Calendar windows for overview GSC: MTD, full months, and fixed-start overview range.

Dates are civil dates in the configured property timezone (default America/Vancouver).
The GSC anchor is **yesterday** in that zone so totals align with typical Search Console lag.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


DEFAULT_DASHBOARD_TZ = os.getenv("DASHBOARD_TZ", "America/Vancouver").strip() or "America/Vancouver"

# Site-level overview (`since_2026_02_15` API mode): no GSC/GA4 data before this; end is GSC anchor (yesterday).
OVERVIEW_REPORT_START_DATE = date(2026, 2, 15)


@dataclass(frozen=True)
class DateWindow:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("window end before start")


def dashboard_timezone() -> ZoneInfo:
    try:
        from backend.app.db import open_db_connection
        from shopifyseo.dashboard_google import get_service_setting
        conn = open_db_connection()
        try:
            db_tz = (get_service_setting(conn, "dashboard_timezone") or "").strip()
        finally:
            conn.close()
        if db_tz:
            return ZoneInfo(db_tz)
    except Exception:
        pass
    return ZoneInfo(DEFAULT_DASHBOARD_TZ)


def gsc_anchor_date_local(tz: ZoneInfo | None = None) -> date:
    """Last complete day for GSC rollups in the dashboard timezone (usually yesterday)."""
    z = tz or dashboard_timezone()
    now_local = datetime.now(z)
    return now_local.date() - timedelta(days=1)


def mtd_matched_windows(anchor: date) -> tuple[DateWindow, DateWindow]:
    """Current month-to-date vs same-length window in the previous month (trimmed if needed)."""
    cur_start = date(anchor.year, anchor.month, 1)
    n_desired = (anchor - cur_start).days + 1
    if n_desired < 1:
        n_desired = 1

    prev_month_last = cur_start - timedelta(days=1)
    prev_start = date(prev_month_last.year, prev_month_last.month, 1)
    days_in_prev = (prev_month_last - prev_start).days + 1
    n = min(n_desired, days_in_prev)

    current = DateWindow(start=cur_start, end=cur_start + timedelta(days=n - 1))
    previous = DateWindow(start=prev_start, end=prev_start + timedelta(days=n - 1))
    return current, previous


def last_two_full_month_windows(anchor: date) -> tuple[DateWindow, DateWindow]:
    """Previous completed calendar month vs the month before it (relative to anchor's month)."""
    first_of_anchor_month = date(anchor.year, anchor.month, 1)
    end_prev = first_of_anchor_month - timedelta(days=1)
    start_prev = date(end_prev.year, end_prev.month, 1)
    end_prior = start_prev - timedelta(days=1)
    start_prior = date(end_prior.year, end_prior.month, 1)
    return DateWindow(start=start_prev, end=end_prev), DateWindow(start=start_prior, end=end_prior)


def rolling_thirty_day_windows(anchor: date) -> tuple[DateWindow, DateWindow]:
    """Last 30 days ending ``anchor`` vs the immediately prior 30 days (inclusive day counts)."""
    current_end = anchor
    current_start = anchor - timedelta(days=29)
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=29)
    return DateWindow(start=current_start, end=current_end), DateWindow(
        start=previous_start, end=previous_end
    )


def overview_site_report_windows(anchor: date) -> tuple[DateWindow, DateWindow]:
    """Feb 15 2026 → ``anchor`` inclusive (when your property has data); prior window matches day count for deltas."""
    current_start = OVERVIEW_REPORT_START_DATE
    current_end = anchor
    if current_end < current_start:
        current_end = current_start
    n_days = (current_end - current_start).days + 1
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=n_days - 1)
    return DateWindow(start=current_start, end=current_end), DateWindow(
        start=previous_start, end=previous_end
    )


def union_window(*windows: DateWindow) -> DateWindow:
    starts = [w.start for w in windows]
    ends = [w.end for w in windows]
    return DateWindow(start=min(starts), end=max(ends))
