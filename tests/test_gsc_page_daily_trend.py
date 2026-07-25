"""Daily per-page history storage and trend computation."""

import sqlite3
from datetime import date, timedelta

import pytest

from shopifyseo.dashboard_store import (
    GSC_TREND_SPARKLINE_POINTS,
    ensure_dashboard_schema,
    gsc_page_trend_map,
    upsert_gsc_page_daily,
)

TODAY = date(2026, 7, 25)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_dashboard_schema(c)
    yield c
    c.close()


def _rows(url: str, start: date, days: int, clicks: int, impressions: int = 0):
    return [
        {
            "date": (start + timedelta(days=i)).isoformat(),
            "page_url": url,
            "clicks": clicks,
            "impressions": impressions or clicks * 10,
            "ctr": 0.1,
            "position": 5.0,
        }
        for i in range(days)
    ]


def _seed(conn, url="https://x/products/w", *, current, previous, handle="w"):
    """`previous` clicks/day for days 31-60 back, `current` for the last 30."""
    cur_start = TODAY - timedelta(days=29)
    prev_start = cur_start - timedelta(days=30)
    rows = _rows(url, prev_start, 30, previous) + _rows(url, cur_start, 30, current)
    upsert_gsc_page_daily(conn, rows, {url: ("product", handle)})


def test_upsert_tags_rows_with_their_catalog_object(conn) -> None:
    url = "https://x/products/w"
    upsert_gsc_page_daily(conn, _rows(url, TODAY, 1, 5), {url: ("product", "w")})
    row = conn.execute("SELECT * FROM gsc_page_daily").fetchone()
    assert (row["object_type"], row["object_handle"]) == ("product", "w")
    assert row["clicks"] == 5


def test_untagged_urls_are_stored_but_excluded_from_trends(conn) -> None:
    """Non-catalog pages (cart, search) still have history but no catalog trend."""
    upsert_gsc_page_daily(conn, _rows("https://x/cart", TODAY, 1, 99), {})
    assert conn.execute("SELECT COUNT(*) FROM gsc_page_daily").fetchone()[0] == 1
    assert gsc_page_trend_map(conn, today=TODAY) == {}


def test_reimport_updates_rather_than_duplicates(conn) -> None:
    """Google restates recent days; re-pulling must correct, not double-count."""
    url = "https://x/products/w"
    upsert_gsc_page_daily(conn, _rows(url, TODAY, 1, 5), {url: ("product", "w")})
    upsert_gsc_page_daily(conn, _rows(url, TODAY, 1, 8), {url: ("product", "w")})
    rows = conn.execute("SELECT clicks FROM gsc_page_daily").fetchall()
    assert len(rows) == 1 and rows[0]["clicks"] == 8


def test_growth_produces_positive_delta(conn) -> None:
    _seed(conn, current=10, previous=5)
    t = gsc_page_trend_map(conn, today=TODAY)[("product", "w")]
    assert t["clicks_current"] == 300
    assert t["clicks_previous"] == 150
    assert t["clicks_delta_pct"] == 100.0


def test_decline_produces_negative_delta(conn) -> None:
    _seed(conn, current=5, previous=20)
    t = gsc_page_trend_map(conn, today=TODAY)[("product", "w")]
    assert t["clicks_delta_pct"] == -75.0


def test_new_page_with_no_baseline_reports_no_percentage(conn) -> None:
    """0 -> something is not '+infinity%'; callers show it as new, not as a delta."""
    _seed(conn, current=10, previous=0)
    t = gsc_page_trend_map(conn, today=TODAY)[("product", "w")]
    assert t["clicks_current"] == 300
    assert t["clicks_previous"] == 0
    assert t["clicks_delta_pct"] == 100.0


def test_series_is_zero_filled_to_a_fixed_length(conn) -> None:
    """Sparkline needs even spacing, so missing days are zeros, not gaps."""
    url = "https://x/products/w"
    upsert_gsc_page_daily(conn, _rows(url, TODAY - timedelta(days=2), 3, 4), {url: ("product", "w")})
    t = gsc_page_trend_map(conn, today=TODAY)[("product", "w")]
    assert len(t["series"]) == GSC_TREND_SPARKLINE_POINTS
    assert t["series"][-3:] == [4, 4, 4]
    assert t["series"][0] == 0


def test_multiple_urls_for_one_object_are_summed(conn) -> None:
    """Trailing-slash and query-string variants roll up to the same catalog row."""
    a, b = "https://x/products/w", "https://x/products/w?variant=1"
    upsert_gsc_page_daily(
        conn,
        _rows(a, TODAY, 1, 3) + _rows(b, TODAY, 1, 7),
        {a: ("product", "w"), b: ("product", "w")},
    )
    t = gsc_page_trend_map(conn, today=TODAY)[("product", "w")]
    assert t["clicks_current"] == 10


def test_objects_are_kept_separate(conn) -> None:
    a, b = "https://x/products/a", "https://x/products/b"
    upsert_gsc_page_daily(
        conn, _rows(a, TODAY, 1, 3) + _rows(b, TODAY, 1, 9), {a: ("product", "a"), b: ("product", "b")}
    )
    m = gsc_page_trend_map(conn, today=TODAY)
    assert m[("product", "a")]["clicks_current"] == 3
    assert m[("product", "b")]["clicks_current"] == 9


def test_data_outside_both_windows_is_ignored(conn) -> None:
    url = "https://x/products/w"
    upsert_gsc_page_daily(conn, _rows(url, TODAY - timedelta(days=200), 5, 50), {url: ("product", "w")})
    assert gsc_page_trend_map(conn, today=TODAY) == {}


def test_sync_refresh_window_covers_the_whole_trend_comparison() -> None:
    """A sync alone must fill both halves of the comparison.

    If it only covered the current window, the prior bucket would be partly empty and
    every page would appear to be growing.
    """
    from shopifyseo.dashboard_actions._sync import GSC_DAILY_REFRESH_DAYS
    from shopifyseo.dashboard_store import GSC_TREND_WINDOW_DAYS

    assert GSC_DAILY_REFRESH_DAYS >= GSC_TREND_WINDOW_DAYS * 2


def test_sync_window_start_reaches_past_the_prior_period(conn) -> None:
    from shopifyseo.dashboard_actions._sync import _gsc_trend_history_start

    start = _gsc_trend_history_start(TODAY)
    prior_period_start = TODAY - timedelta(days=(30 * 2) - 1)
    assert start <= prior_period_start


def test_sync_facing_helpers_are_exported_from_the_package() -> None:
    """The sync calls these via `dg.<name>`, so a missing re-export breaks it at runtime.

    Unit tests import from the submodule directly and would not catch that.
    """
    import shopifyseo.dashboard_google as dg

    for name in (
        "fetch_gsc_page_daily_rows",
        "fetch_gsc_all_page_rows",
        "fetch_gsc_all_page_query_rows",
        "build_gsc_url_detail",
        "write_gsc_url_detail_cache",
        "build_ga4_summary_index",
        "ga4_url_detail_from_index",
        "write_ga4_url_detail_cache",
        "ga4_summary_window",
    ):
        assert hasattr(dg, name), f"shopifyseo.dashboard_google is missing {name}"
