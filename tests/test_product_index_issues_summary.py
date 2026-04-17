"""Product list summary: index_issues must match index bucket semantics (not substring hacks)."""

from shopifyseo.dashboard_status import index_status_bucket_from_strings


def _index_issues_count(items: list[dict]) -> int:
    return sum(
        1
        for item in items
        if index_status_bucket_from_strings(item["index_status"], item["index_coverage"]) != "indexed"
    )


def test_index_issues_includes_not_indexed_label():
    items = [
        {"index_status": "Not indexed", "index_coverage": ""},
        {"index_status": "Indexed", "index_coverage": ""},
    ]
    assert _index_issues_count(items) == 1


def test_index_issues_counts_unknown_and_needs_review():
    items = [
        {"index_status": "", "index_coverage": ""},
        {"index_status": "Needs review", "index_coverage": ""},
        {"index_status": "Indexed", "index_coverage": ""},
    ]
    assert _index_issues_count(items) == 2
