"""Test that all dg.X symbols used in _sync.py are re-exported from dashboard_google.

This ensures we don't regress the AttributeError seen in PR #88 where
_sync.py accessed symbols via `dg.X` but they were not re-exported
from dashboard_google/__init__.py.
"""
import shopifyseo.dashboard_google as dg


def test_ga4_symbols_reexported():
    """All GA4 functions used in _sync.py must be accessible via dg.X."""
    # Core GA4 functions
    assert callable(dg.get_ga4_summary_enhanced)
    assert callable(dg.build_ga4_summary_index_enhanced)
    assert callable(dg.ga4_url_detail_from_index_enhanced)
    assert callable(dg.get_ga4_organic_funnel_summary)

    # Helper functions also used in _sync.py
    assert callable(dg.ga4_report_page_path_from_row)
    assert callable(dg.ga4_summary_window)
    assert callable(dg.write_ga4_url_detail_cache)
    assert callable(dg.get_service_setting)

    # Index builders (may be used indirectly)
    assert callable(dg.build_ga4_organic_funnel_index)
    assert callable(dg.build_ga4_organic_sessions_index)
    assert callable(dg.build_ga4_engagement_index)


def test_gsc_symbols_reexported():
    """All GSC functions used in _sync.py must be accessible via dg.X."""
    assert callable(dg.snapshot_gsc_dimensional_warning_summary)
    assert callable(dg.get_search_console_sites)
    assert callable(dg.preferred_site_url)
    assert callable(dg.get_search_data_access_token)
    assert callable(dg.gsc_url_report_window)
    assert callable(dg.fetch_gsc_page_daily_rows)
    assert callable(dg.gsc_dimensional_run)
    assert callable(dg.get_search_console_summary_cached)
    assert callable(dg.refresh_gsc_property_breakdowns_for_site)
    assert callable(dg.fetch_gsc_all_page_rows)
    assert callable(dg.fetch_gsc_all_page_query_rows)
    assert callable(dg.build_gsc_url_detail)
    assert callable(dg.write_gsc_url_detail_cache)
    assert callable(dg.delete_search_console_overview_timeseries_only)
    assert callable(dg.get_url_inspection)
    assert callable(dg.set_service_setting)
