"""GSC bulk dimension pulls and per-URL payload assembly (no live API).

These pin the fidelity contract: the payload assembled from property-wide
``["page"]`` / ``["page","query"]`` pulls must match the shape and ordering the
old per-URL filtered calls produced.
"""

from datetime import date

import pytest

from shopifyseo.dashboard_google import _gsc
from shopifyseo.dashboard_google._gsc import (
    GSC_BULK_ROW_LIMIT,
    build_gsc_url_detail,
    fetch_gsc_all_page_query_rows,
    fetch_gsc_all_page_rows,
)
from shopifyseo.gsc_query_limits import GSC_PER_URL_QUERY_ROW_LIMIT


SITE = "sc-domain:example.com"
START = date(2026, 7, 1)
END = date(2026, 7, 23)


def _page_row(url: str, clicks: int, impressions: int) -> dict:
    return {"keys": [url], "clicks": clicks, "impressions": impressions, "ctr": 0.1, "position": 4.2}


def _pq_row(url: str, query: str, clicks: int, impressions: int) -> dict:
    return {
        "keys": [url, query],
        "clicks": clicks,
        "impressions": impressions,
        "ctr": 0.2,
        "position": 3.1,
    }


@pytest.fixture
def capture_posts(monkeypatch):
    """Stub google_api_post with a scripted list of row batches; record request bodies."""
    calls: list[dict] = []

    def _install(batches: list[list[dict]]):
        queue = list(batches)

        def _fake_post(url: str, token: str, body: dict) -> dict:
            calls.append(body)
            return {"rows": queue.pop(0) if queue else []}

        monkeypatch.setattr(_gsc, "google_api_post", _fake_post)
        return calls

    return _install


def test_page_rows_paginate_until_short_batch(capture_posts) -> None:
    full = [_page_row(f"https://example.com/p/{i}", i, i * 10) for i in range(GSC_BULK_ROW_LIMIT)]
    tail = [_page_row("https://example.com/p/last", 1, 2)]
    calls = capture_posts([full, tail])

    out = fetch_gsc_all_page_rows(SITE, "tok", START, END)

    assert len(out) == GSC_BULK_ROW_LIMIT + 1
    assert out["https://example.com/p/last"]["clicks"] == 1
    assert [c["startRow"] for c in calls] == [0, GSC_BULK_ROW_LIMIT]
    assert calls[0]["dimensions"] == ["page"]


def test_page_rows_stops_on_empty_first_batch(capture_posts) -> None:
    calls = capture_posts([[]])
    assert fetch_gsc_all_page_rows(SITE, "tok", START, END) == {}
    assert len(calls) == 1


def test_query_rows_grouped_by_page_and_reshaped(capture_posts) -> None:
    capture_posts(
        [
            [
                _pq_row("https://example.com/a", "cheap widget", 5, 50),
                _pq_row("https://example.com/a", "widget", 9, 20),
                _pq_row("https://example.com/b", "gadget", 1, 3),
            ]
        ]
    )

    grouped = fetch_gsc_all_page_query_rows(SITE, "tok", START, END)

    # Reshaped to the single-dimension ["query"] form downstream code parses.
    assert [r["keys"] for r in grouped["https://example.com/a"]] == [["widget"], ["cheap widget"]]
    assert grouped["https://example.com/a"][0]["clicks"] == 9
    assert grouped["https://example.com/b"][0]["keys"] == ["gadget"]


def test_query_rows_sorted_by_clicks_desc_and_capped_per_page(capture_posts) -> None:
    rows = [_pq_row("https://example.com/a", f"q{i}", i, i) for i in range(250)]
    capture_posts([rows])

    grouped = fetch_gsc_all_page_query_rows(SITE, "tok", START, END, rows_per_page=10)
    page = grouped["https://example.com/a"]

    assert len(page) == 10
    assert [r["clicks"] for r in page] == list(range(249, 239, -1))


def test_retained_rows_are_a_superset_of_the_old_per_url_limit(capture_posts) -> None:
    rows = [_pq_row("https://example.com/a", f"q{i}", i, i) for i in range(500)]
    capture_posts([rows])

    grouped = fetch_gsc_all_page_query_rows(SITE, "tok", START, END)

    assert len(grouped["https://example.com/a"]) >= GSC_PER_URL_QUERY_ROW_LIMIT


def test_top_rows_survive_trimming_across_batches(capture_posts) -> None:
    """A high-click row arriving in a later batch must not be trimmed away."""
    first = [_pq_row("https://example.com/a", f"q{i}", 1, 1) for i in range(GSC_BULK_ROW_LIMIT)]
    second = [_pq_row("https://example.com/a", "winner", 9999, 9999)]
    capture_posts([first, second])

    grouped = fetch_gsc_all_page_query_rows(SITE, "tok", START, END, rows_per_page=5)
    page = grouped["https://example.com/a"]

    assert page[0]["keys"] == ["winner"]
    assert len(page) == 5


def test_build_url_detail_matches_per_url_payload_shape() -> None:
    row = _page_row("https://example.com/a", 7, 70)
    payload = build_gsc_url_detail(
        "https://example.com/a",
        SITE,
        page_row=row,
        query_rows=[{"keys": ["widget"], "clicks": 5, "impressions": 9, "ctr": 0.5, "position": 2.0}],
        start=START,
        end=END,
        period_mode="mtd",
    )

    assert set(payload) == {
        "url",
        "site_url",
        "page_rows",
        "query_rows",
        "start_date",
        "end_date",
        "period_mode",
    }
    # Downstream reads (page_rows or [None])[0] then .get("clicks").
    assert payload["page_rows"][0]["clicks"] == 7
    assert payload["query_rows"][0]["keys"][0] == "widget"
    assert payload["period_mode"] == "mtd"


def test_build_url_detail_for_page_with_no_gsc_data() -> None:
    payload = build_gsc_url_detail(
        "https://example.com/zero", SITE, page_row=None, query_rows=None, start=START, end=END, period_mode="mtd"
    )
    assert payload["page_rows"] == []
    assert payload["query_rows"] == []
