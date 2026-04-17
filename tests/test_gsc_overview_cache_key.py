from shopifyseo.dashboard_google import _overview_cache_key


def test_overview_cache_key_includes_url_segment():
    base = ("https://example.com/", "mtd", "2026-03-15")
    all_key = _overview_cache_key(*base, "all")
    prod_key = _overview_cache_key(*base, "products")
    assert all_key != prod_key
    assert "products" in prod_key
    assert "all" in all_key
    assert "search_console_overview_v2" in all_key
