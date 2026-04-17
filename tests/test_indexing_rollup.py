from backend.app.services.indexing_rollup import build_indexing_rollup
from shopifyseo.dashboard_status import (
    index_status_bucket_from_strings,
    index_status_info,
    inspection_for_catalog_index_display,
)


def test_index_status_bucket_unknown():
    assert index_status_bucket_from_strings("", "") == "unknown"


def test_index_status_bucket_stored_unknown_label():
    assert index_status_bucket_from_strings("Unknown", "") == "unknown"


def test_index_status_bucket_stored_needs_review_label():
    assert index_status_bucket_from_strings("Needs Review", "") == "needs_review"


def test_index_status_bucket_indexed():
    """DB stores the Index panel label (see dashboard_store), not raw indexingState."""
    assert index_status_bucket_from_strings("Indexed", "") == "indexed"


def test_index_status_bucket_not_indexed_phrase():
    assert index_status_bucket_from_strings("Discovered - currently not indexed", "") == "not_indexed"


def test_index_status_bucket_negative_before_substring_indexed():
    """'not indexed' must win over naive 'indexed' substring checks."""
    assert index_status_bucket_from_strings("Crawled - currently not indexed", "") == "not_indexed"


def test_inspection_for_catalog_index_display_prefers_row_over_api_cache():
    api = {
        "inspectionResult": {
            "indexStatusResult": {
                "indexingState": "INDEXING_ALLOWED",
                "coverageState": "Submitted and indexed",
            }
        }
    }
    row = {"index_status": "Needs Review", "index_coverage": "Something ambiguous", "google_canonical": ""}
    merged = inspection_for_catalog_index_display(api, row)
    label, _, _ = index_status_info(merged)
    assert label == "Needs Review"


def test_inspection_for_catalog_index_display_falls_back_when_row_empty():
    api = {
        "inspectionResult": {
            "indexStatusResult": {
                "indexingState": "INDEXING_ALLOWED",
                "coverageState": "Submitted and indexed",
            }
        }
    }
    row = {"index_status": "", "index_coverage": "", "google_canonical": ""}
    merged = inspection_for_catalog_index_display(api, row)
    label, _, _ = index_status_info(merged)
    assert label == "Indexed"


def test_build_indexing_rollup_counts():
    facts = [
        {
            "object_type": "product",
            "index_status": "Indexed",
            "index_coverage": "",
        },
        {
            "object_type": "product",
            "index_status": "Excluded by robots.txt",
            "index_coverage": "",
        },
        {
            "object_type": "collection",
            "index_status": "",
            "index_coverage": "",
        },
    ]
    r = build_indexing_rollup(facts)
    assert r["total"] == 3
    assert r["indexed"] == 1
    assert r["not_indexed"] == 1
    assert r["unknown"] == 1
    assert r["by_type"]["product"]["total"] == 2
    assert r["by_type"]["collection"]["unknown"] == 1
