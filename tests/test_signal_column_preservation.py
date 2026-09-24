"""Denormalized signal columns must not be nulled when a source has nothing fresh.

The GSC month-to-date window is legitimately empty for the first days of every month,
and a failed scope leaves objects without a payload. Neither should destroy the stored
value, which is the only copy.
"""

import sqlite3

import pytest

from shopifyseo.dashboard_store import _signal_values_preserving_known

SIGNAL_COLS = """
  gsc_clicks INTEGER, gsc_impressions INTEGER, gsc_ctr REAL, gsc_position REAL,
  gsc_last_fetched_at INTEGER, ga4_sessions INTEGER, ga4_views INTEGER,
  ga4_avg_session_duration REAL, ga4_last_fetched_at INTEGER, index_status TEXT,
  index_coverage TEXT, google_canonical TEXT, index_last_fetched_at INTEGER
"""

STORED = (4321, 99000, 0.044, 7.5, 111, 888, 1200, 61.5, 222, "Indexed", "Submitted and indexed", "https://x/w", 333)

FULL_IDX = {"indexingState": "INDEXING_ALLOWED", "coverageState": "Submitted and indexed", "googleCanonical": "https://x/w"}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(f"CREATE TABLE products (handle TEXT PRIMARY KEY, {SIGNAL_COLS})")
    c.execute(
        f"INSERT INTO products VALUES ('w', {', '.join('?' * 13)})",
        STORED,
    )
    c.commit()
    yield c
    c.close()


def _values(conn, **overrides):
    kwargs = dict(
        gsc_row={"clicks": 10, "impressions": 100, "ctr": 0.1, "position": 3.0},
        gsc_fetched_at=999,
        ga4_sessions=5,
        ga4_views=7,
        ga4_avg_dur=42.0,
        ga4_fetched_at=999,
        idx=dict(FULL_IDX),
        index_label="Indexed",
        index_fetched_at=999,
    )
    kwargs.update(overrides)
    return _signal_values_preserving_known(conn, "products", "handle = ?", ("w",), **kwargs)


def test_fresh_data_overwrites_everything(conn) -> None:
    v = _values(conn)
    assert v[0:5] == (10, 100, 0.1, 3.0, 999)
    assert v[5:9] == (5, 7, 42.0, 999)


def test_empty_gsc_payload_preserves_stored_gsc(conn) -> None:
    """The 1st-of-month case: MTD window returns no rows for any page."""
    v = _values(conn, gsc_row=None)
    assert v[0:5] == STORED[0:5], "GSC values and their timestamp must survive"
    assert v[5:9] == (5, 7, 42.0, 999), "GA4 is unaffected"


def test_missing_ga4_preserves_stored_ga4(conn) -> None:
    v = _values(conn, ga4_sessions=None, ga4_views=None, ga4_avg_dur=None, ga4_fetched_at=None)
    assert v[5:9] == STORED[5:9]
    assert v[0:5] == (10, 100, 0.1, 3.0, 999), "GSC is unaffected"


def test_missing_inspection_preserves_stored_index(conn) -> None:
    v = _values(conn, idx={}, index_label=None, index_fetched_at=None)
    assert v[9:13] == STORED[9:13]


def test_all_sources_empty_preserves_everything(conn) -> None:
    v = _values(
        conn,
        gsc_row=None,
        gsc_fetched_at=None,
        ga4_sessions=None,
        ga4_views=None,
        ga4_avg_dur=None,
        ga4_fetched_at=None,
        idx={},
        index_label=None,
        index_fetched_at=None,
    )
    assert v == STORED


def test_genuine_zero_still_overwrites(conn) -> None:
    """A payload that reports zero is real data, not absence — it must win."""
    v = _values(
        conn,
        gsc_row={"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0},
        ga4_sessions=0,
        ga4_views=0,
        ga4_avg_dur=0.0,
    )
    assert v[0:4] == (0, 0, 0.0, 0.0), "zero clicks must overwrite a stale 4321"
    assert v[5:8] == (0, 0, 0.0)


def test_preserved_value_keeps_its_original_timestamp(conn) -> None:
    """A preserved number must never look freshly fetched."""
    v = _values(conn, gsc_row=None, gsc_fetched_at=999)
    assert v[4] == STORED[4] == 111, "timestamp travels with the value it describes"


def test_partial_ga4_does_not_wipe_the_present_field(conn) -> None:
    """Sessions present but views missing still counts as having GA4 data."""
    v = _values(conn, ga4_sessions=12, ga4_views=None, ga4_avg_dur=None)
    assert v[5] == 12
    assert v[6] is None


def test_row_missing_from_table_falls_back_to_fresh(conn) -> None:
    v = _signal_values_preserving_known(
        conn,
        "products",
        "handle = ?",
        ("does-not-exist",),
        gsc_row=None,
        gsc_fetched_at=None,
        ga4_sessions=None,
        ga4_views=None,
        ga4_avg_dur=None,
        ga4_fetched_at=None,
        idx={},
        index_label=None,
        index_fetched_at=None,
    )
    assert v == (None,) * 13


def test_catalog_period_window_is_constant_length_across_month_boundary() -> None:
    """The per-URL catalog window must not shrink at the start of a month.

    Month-to-date collapsed to 1-2 days each month, which (before the preservation guard
    above) nulled every catalog row's search numbers.
    """
    import datetime

    from shopifyseo.dashboard_google import gsc_url_report_window
    from shopifyseo.gsc_query_limits import GSC_CATALOG_PERIOD_MODE

    assert GSC_CATALOG_PERIOD_MODE == "rolling_30d"

    lengths = set()
    for iso in ("2026-07-30", "2026-07-31", "2026-08-01", "2026-08-02", "2026-03-01"):
        anchor = datetime.date.fromisoformat(iso)
        # gsc_url_report_window anchors on today, so exercise the underlying calendar.
        from backend.app.services.gsc_overview_calendar import rolling_thirty_day_windows

        cur, _prev = rolling_thirty_day_windows(anchor)
        lengths.add((cur.end - cur.start).days + 1)
    assert lengths == {30}, f"window length must never change, got {lengths}"

    # The live helper resolves to the same mode.
    start, end = gsc_url_report_window(GSC_CATALOG_PERIOD_MODE)
    assert (end - start).days + 1 == 30
