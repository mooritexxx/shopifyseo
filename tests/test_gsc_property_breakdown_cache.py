import sqlite3

from shopifyseo import dashboard_google as dg


def test_top_bucket_impressions_pct_vs_prior_matches_same_key():
    cur = [{"keys": ["can"], "impressions": 120, "clicks": 0, "ctr": 0.0, "position": 0.0}]
    prev = [{"keys": ["can"], "impressions": 100, "clicks": 0, "ctr": 0.0, "position": 0.0}]
    assert dg._top_bucket_impressions_pct_vs_prior(cur, prev) == 20.0


def test_top_bucket_impressions_pct_vs_prior_none_without_prior_cache():
    cur = [{"keys": ["can"], "impressions": 120, "clicks": 0, "ctr": 0.0, "position": 0.0}]
    assert dg._top_bucket_impressions_pct_vs_prior(cur, None) is None


def test_delete_search_console_overview_cache_clears_tier_a_types():
    conn = sqlite3.connect(":memory:")
    dg.ensure_google_cache_schema(conn)
    types = (
        "search_console_overview",
        "gsc_property_country",
        "gsc_property_device",
        "gsc_property_search_appearance",
    )
    for ctype in types:
        conn.execute(
            """
            INSERT INTO google_api_cache (
              cache_key, cache_type, payload_json, fetched_at, expires_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (f"key-{ctype}", ctype, "{}", 1, 9_999_999_999),
        )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM google_api_cache").fetchone()[0] == len(types)

    dg.delete_search_console_overview_cache(conn)

    assert conn.execute("SELECT COUNT(*) FROM google_api_cache").fetchone()[0] == 0


def test_delete_search_console_overview_timeseries_only_keeps_tier_a_rows():
    conn = sqlite3.connect(":memory:")
    dg.ensure_google_cache_schema(conn)
    conn.execute(
        """
        INSERT INTO google_api_cache (
          cache_key, cache_type, payload_json, fetched_at, expires_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("ov-key", "search_console_overview", "{}", 1, 9_999_999_999),
    )
    conn.execute(
        """
        INSERT INTO google_api_cache (
          cache_key, cache_type, payload_json, fetched_at, expires_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("tier-key", "gsc_property_country", "{}", 1, 9_999_999_999),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM google_api_cache").fetchone()[0] == 2

    dg.delete_search_console_overview_timeseries_only(conn)

    assert conn.execute("SELECT COUNT(*) FROM google_api_cache").fetchone()[0] == 1
    row = conn.execute("SELECT cache_type FROM google_api_cache").fetchone()
    assert row[0] == "gsc_property_country"


def test_refresh_gsc_property_breakdowns_for_site_skips_empty_url(monkeypatch):
    calls: list[str] = []

    def fake(_conn, **kwargs):
        calls.append(kwargs.get("period_mode", ""))
        return {}

    monkeypatch.setattr(dg, "get_gsc_property_breakdowns_cached", fake)
    conn = sqlite3.connect(":memory:")
    dg.refresh_gsc_property_breakdowns_for_site(conn, "")
    dg.refresh_gsc_property_breakdowns_for_site(conn, "   ")
    assert calls == []


def test_refresh_gsc_property_breakdowns_refreshes_four_period_modes(monkeypatch):
    calls: list[str] = []

    def fake(_conn, *, site_url, period_mode, anchor, current_start, current_end, refresh=False, **kwargs):
        assert site_url == "https://example.com/"
        assert refresh is True
        calls.append(period_mode)
        return {}

    monkeypatch.setattr(dg, "get_gsc_property_breakdowns_cached", fake)
    conn = sqlite3.connect(":memory:")
    dg.refresh_gsc_property_breakdowns_for_site(conn, "https://example.com/")
    assert set(calls) == {"mtd", "full_months", "since_2026_02_15", "rolling_30d"}
    assert len(calls) == 4
