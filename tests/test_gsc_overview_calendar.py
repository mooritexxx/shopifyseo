from datetime import date, datetime, timedelta

from zoneinfo import ZoneInfo

from backend.app.services.gsc_overview_calendar import (
    DateWindow,
    gsc_anchor_date_local,
    last_two_full_month_windows,
    mtd_matched_windows,
    overview_site_report_windows,
    rolling_thirty_day_windows,
    union_window,
)


def test_mtd_matched_equal_length_trims_march_to_february():
    anchor = date(2026, 3, 31)
    cur, prev = mtd_matched_windows(anchor)
    assert cur.start == date(2026, 3, 1)
    assert cur.end == date(2026, 3, 28)
    assert prev.start == date(2026, 2, 1)
    assert prev.end == date(2026, 2, 28)
    assert (cur.end - cur.start).days == (prev.end - prev.start).days


def test_mtd_matched_mid_month():
    anchor = date(2026, 3, 15)
    cur, prev = mtd_matched_windows(anchor)
    assert cur == DateWindow(date(2026, 3, 1), date(2026, 3, 15))
    assert prev.start == date(2026, 2, 1)
    assert prev.end == date(2026, 2, 15)


def test_rolling_thirty_day_windows():
    anchor = date(2026, 4, 3)
    cur, prev = rolling_thirty_day_windows(anchor)
    assert cur == DateWindow(date(2026, 3, 5), date(2026, 4, 3))
    assert prev == DateWindow(date(2026, 2, 3), date(2026, 3, 4))
    assert prev.end + timedelta(days=1) == cur.start
    assert (cur.end - cur.start).days + 1 == 30
    assert (prev.end - prev.start).days + 1 == 30


def test_overview_site_report_windows_fixed_start_and_equal_length():
    anchor = date(2026, 4, 3)
    cur, prev = overview_site_report_windows(anchor)
    assert cur.end == anchor
    assert cur.start == date(2026, 2, 15)
    assert prev.end == date(2026, 2, 14)
    assert prev.start == date(2025, 12, 29)
    assert prev.end + timedelta(days=1) == cur.start
    cur_days = (cur.end - cur.start).days + 1
    prev_days = (prev.end - prev.start).days + 1
    assert cur_days == prev_days


def test_last_two_full_months_before_anchor_month():
    anchor = date(2026, 3, 20)
    recent_full, prior_full = last_two_full_month_windows(anchor)
    assert recent_full.start == date(2026, 2, 1)
    assert recent_full.end == date(2026, 2, 28)
    assert prior_full.start == date(2026, 1, 1)
    assert prior_full.end == date(2026, 1, 31)


def test_union_window():
    u = union_window(
        DateWindow(date(2026, 2, 1), date(2026, 2, 15)),
        DateWindow(date(2026, 3, 1), date(2026, 3, 10)),
    )
    assert u.start == date(2026, 2, 1)
    assert u.end == date(2026, 3, 10)


def test_mtd_matched_leap_year_february_trims_march():
    """March has 31 days; February 2024 has 29 — prior window must cap at 29 days."""
    anchor = date(2024, 3, 31)
    cur, prev = mtd_matched_windows(anchor)
    assert cur.end == date(2024, 3, 29)
    assert prev.end == date(2024, 2, 29)
    assert (cur.end - cur.start).days == (prev.end - prev.start).days


def test_mtd_first_day_of_month_single_day():
    anchor = date(2026, 3, 1)
    cur, prev = mtd_matched_windows(anchor)
    assert cur == DateWindow(date(2026, 3, 1), date(2026, 3, 1))
    assert prev == DateWindow(date(2026, 2, 1), date(2026, 2, 1))


def test_gsc_anchor_is_yesterday_in_property_tz(monkeypatch):
    """Anchor uses civil yesterday in the passed timezone (stable under DST if `now` is aware)."""

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            z = tz or ZoneInfo("America/Vancouver")
            return datetime(2026, 7, 15, 14, 30, 0, tzinfo=z)

    monkeypatch.setattr(
        "backend.app.services.gsc_overview_calendar.datetime",
        _FakeDateTime,
    )
    van = ZoneInfo("America/Vancouver")
    assert gsc_anchor_date_local(van) == date(2026, 7, 14)
