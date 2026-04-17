"""Tests for GSC query × dimension rollups used on detail pages."""

from shopifyseo.dashboard_queries import build_gsc_segment_summary_from_rows


def test_build_gsc_segment_summary_empty():
    s = build_gsc_segment_summary_from_rows([])
    assert s["fetched_at"] is None
    assert s["device_mix"] == []
    assert s["top_countries"] == []
    assert s["search_appearances"] == []
    assert s["top_pairs"] == []


def test_build_gsc_segment_summary_rollups_and_pairs():
    rows = [
        {
            "query": "vape kit",
            "dimension_kind": "device",
            "dimension_value": "MOBILE",
            "clicks": 2,
            "impressions": 100,
            "ctr": 0.02,
            "position": 5.0,
            "fetched_at": 1700000000,
        },
        {
            "query": "vape kit",
            "dimension_kind": "device",
            "dimension_value": "DESKTOP",
            "clicks": 1,
            "impressions": 50,
            "ctr": 0.02,
            "position": 4.0,
            "fetched_at": 1700000000,
        },
        {
            "query": "e liquid",
            "dimension_kind": "country",
            "dimension_value": "nzl",
            "clicks": 0,
            "impressions": 30,
            "ctr": 0,
            "position": 10.0,
            "fetched_at": 1700000001,
        },
        {
            "query": "rich result",
            "dimension_kind": "searchAppearance",
            "dimension_value": "RICH_RESULT",
            "clicks": 5,
            "impressions": 200,
            "ctr": 0.025,
            "position": 3.0,
            "fetched_at": 1700000001,
        },
    ]
    s = build_gsc_segment_summary_from_rows(rows)
    assert s["fetched_at"] == 1700000001

    devices = {x["segment"]: x for x in s["device_mix"]}
    assert devices["MOBILE"]["impressions"] == 100
    assert devices["DESKTOP"]["impressions"] == 50
    assert abs(devices["MOBILE"]["share"] - 100 / 150) < 0.01

    assert len(s["top_countries"]) == 1
    assert s["top_countries"][0]["segment"] == "nzl"

    assert len(s["search_appearances"]) == 1
    assert s["search_appearances"][0]["impressions"] == 200

    assert s["top_pairs"][0]["impressions"] == 200
    assert s["top_pairs"][0]["query"] == "rich result"
    assert len(s["top_pairs"]) <= 20
