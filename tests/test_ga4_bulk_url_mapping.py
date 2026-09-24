"""GA4 per-URL metrics derived from the summary reports (no live API).

These pin the fidelity contract: the derived payload must equal what
``get_ga4_url_detail(refresh=True)`` produced from its own filtered runReport calls.
"""

from datetime import date

from shopifyseo.dashboard_google._ga4 import (
    build_ga4_summary_index,
    ga4_summary_window,
    ga4_url_detail_from_index,
)


START = date(2026, 6, 26)
END = date(2026, 7, 23)


def _page_row(path: str, views: int) -> dict:
    return {"dimensionValues": [{"value": path}], "metricValues": [{"value": str(views)}]}


def _landing_row(path: str, sessions: int, avg_duration: float) -> dict:
    return {
        "dimensionValues": [{"value": path}],
        "metricValues": [{"value": str(sessions)}, {"value": str(avg_duration)}],
    }


def _detail(summary: dict, url: str) -> dict:
    return ga4_url_detail_from_index(
        build_ga4_summary_index(summary), url, start_date=START, end_date=END
    )


def test_views_aggregate_across_query_string_variants() -> None:
    """pagePath EXACT collapses query strings; the derived value must sum them."""
    summary = {
        "page_rows": [
            _page_row("/products/foo", 100),
            _page_row("/products/foo?variant=1", 25),
            _page_row("/products/foo?utm_source=ig", 7),
            _page_row("/products/other", 999),
        ],
        "landing_rows": [],
    }
    detail = _detail(summary, "https://shop.example.com/products/foo")
    assert detail["views"] == 132
    assert detail["path_used"] == "/products/foo"


def test_sessions_use_exact_landing_page_match_only() -> None:
    """landingPagePlusQueryString EXACT against a bare path ignores query-string rows."""
    summary = {
        "page_rows": [_page_row("/products/foo", 10)],
        "landing_rows": [
            _landing_row("/products/foo", 40, 61.5),
            _landing_row("/products/foo?utm_source=ig", 500, 12.0),
        ],
    }
    detail = _detail(summary, "https://shop.example.com/products/foo")
    assert detail["sessions"] == 40
    assert detail["avg_session_duration"] == 61.5


def test_trailing_slash_candidate_is_used_when_bare_path_has_no_data() -> None:
    summary = {
        "page_rows": [_page_row("/pages/about/", 12)],
        "landing_rows": [_landing_row("/pages/about/", 5, 30.0)],
    }
    detail = _detail(summary, "https://shop.example.com/pages/about")
    assert detail["path_used"] == "/pages/about/"
    assert detail["views"] == 12
    assert detail["sessions"] == 5


def test_url_absent_from_ga4_yields_zeroes_not_none() -> None:
    """Matches get_ga4_url_detail, whose filtered reports returned no rows -> 0."""
    summary = {"page_rows": [_page_row("/products/other", 3)], "landing_rows": []}
    detail = _detail(summary, "https://shop.example.com/products/missing")
    assert detail["views"] == 0
    assert detail["sessions"] == 0
    assert detail["avg_session_duration"] == 0.0
    assert detail["path_used"] == "/products/missing"


def test_payload_shape_matches_get_ga4_url_detail() -> None:
    summary = {
        "page_rows": [_page_row("/products/foo", 1)],
        "landing_rows": [_landing_row("/products/foo", 1, 2.0)],
    }
    detail = _detail(summary, "https://shop.example.com/products/foo")
    assert set(detail) == {
        "url",
        "path_used",
        "views",
        "sessions",
        "avg_session_duration",
        "start_date",
        "end_date",
    }
    assert detail["start_date"] == START.isoformat()
    assert detail["end_date"] == END.isoformat()


def test_empty_summary_does_not_raise() -> None:
    detail = _detail({}, "https://shop.example.com/products/foo")
    assert detail["views"] == 0
    assert detail["sessions"] == 0


def test_summary_window_is_28_days_ending_yesterday() -> None:
    start, end = ga4_summary_window()
    assert (end - start).days == 27
    assert (date.today() - end).days == 1
