"""Regression tests for the index-status penalty in `_seo_base_score`.

The check used to be `"indexed" not in index_status.lower()`, which is
polarity-inverted: `"indexed" in "not indexed"` is True, so genuinely
not-indexed rows escaped the penalty while ambiguous labels like
"Needs Review" were penalized instead.
"""
from shopifyseo.dashboard_queries._seo_facts import _seo_base_score

NOT_INDEXED_PENALTY = 20


def _score(index_status: str, index_coverage: str = "") -> tuple[int, list[str]]:
    """Score a product whose only possible issue is its index status."""
    return _seo_base_score(
        "product",
        {
            "title": "A perfectly fine product title",
            "seo_title": "A perfectly fine SEO title here",
            "seo_description": (
                "A meta description that is comfortably inside the length "
                "window so it contributes no deductions at all."
            ),
            "description_html": "<p>" + ("body copy " * 80) + "</p>",
            "index_status": index_status,
            "index_coverage": index_coverage,
        },
    )


def test_seo_base_score_baseline_has_no_deductions():
    """Guard the fixture: without index issues the row must score 0."""
    score, reasons = _score("Indexed")
    assert score == 0
    assert reasons == []


def test_seo_base_score_penalizes_not_indexed():
    score, reasons = _score("Not Indexed")
    assert score == NOT_INDEXED_PENALTY
    assert "not indexed" in reasons


def test_seo_base_score_penalizes_crawled_currently_not_indexed():
    score, reasons = _score("Crawled - currently not indexed")
    assert score == NOT_INDEXED_PENALTY
    assert "not indexed" in reasons


def test_seo_base_score_penalizes_discovered_currently_not_indexed():
    score, reasons = _score("Discovered - currently not indexed")
    assert score == NOT_INDEXED_PENALTY
    assert "not indexed" in reasons


def test_seo_base_score_penalizes_negative_marker_in_coverage_only():
    score, reasons = _score("URL is unknown to Google", "Excluded by 'noindex' tag")
    assert score == NOT_INDEXED_PENALTY
    assert "not indexed" in reasons


def test_seo_base_score_does_not_penalize_needs_review():
    """Ambiguous statuses are not a not-indexed signal (false-positive side)."""
    score, reasons = _score("Needs Review")
    assert score == 0
    assert "not indexed" not in reasons


def test_seo_base_score_does_not_penalize_unknown():
    score, reasons = _score("Unknown")
    assert score == 0
    assert "not indexed" not in reasons


def test_seo_base_score_does_not_penalize_indexed():
    score, reasons = _score("Indexed")
    assert score == 0
    assert "not indexed" not in reasons


def test_seo_base_score_does_not_penalize_missing_index_status():
    """Rows with no index data at all stay exempt, as before the fix."""
    score, reasons = _score("")
    assert score == 0
    assert "not indexed" not in reasons


def test_seo_base_score_missing_index_coverage_key_is_tolerated():
    """`_seo_base_score` must not require the caller to supply index_coverage."""
    score, reasons = _seo_base_score(
        "page",
        {
            "title": "Page",
            "seo_title": "A perfectly fine SEO title here",
            "seo_description": (
                "A meta description that is comfortably inside the length "
                "window so it contributes no deductions at all."
            ),
            "body": "<p>" + ("body copy " * 80) + "</p>",
            "index_status": "Not Indexed",
        },
    )
    assert score == NOT_INDEXED_PENALTY
    assert "not indexed" in reasons
