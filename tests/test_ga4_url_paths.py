"""Unit tests for GA4 per-URL path helpers (no live API)."""

from shopifyseo.dashboard_google._ga4 import ga4_path_candidates


def test_ga4_path_candidates_adds_trailing_slash_variant() -> None:
    paths = ga4_path_candidates("https://example.com/products/foo")
    assert "/products/foo" in paths
    assert "/products/foo/" in paths


def test_ga4_path_candidates_root() -> None:
    paths = ga4_path_candidates("https://example.com/")
    assert paths == ["/"]
