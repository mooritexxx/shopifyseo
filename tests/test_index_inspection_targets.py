"""Tests for index sync target selection (skip already-indexed URLs unless force refresh)."""

import sqlite3

from shopifyseo import dashboard_actions as da


class _Row(dict):
    def __getitem__(self, key: str):
        return self.get(key)


def test_index_inspection_targets_skips_indexed_when_not_force(monkeypatch):
    conn = sqlite3.connect(":memory:")

    products = [
        _Row(handle="a", index_status="Indexed", index_coverage=""),
        _Row(handle="b", index_status="Needs Review", index_coverage=""),
        _Row(handle="c", index_status="Unknown", index_coverage=""),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    assert skipped == 1
    assert [t[1] for t in targets] == ["b", "c"]


def test_index_inspection_targets_force_refresh_uses_all_targets(monkeypatch):
    conn = sqlite3.connect(":memory:")
    all_targets = [
        ("product", "a", "https://example.com/products/a"),
        ("collection", "c", "https://example.com/collections/c"),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: [_Row(handle="a", index_status="Indexed")])
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [_Row(handle="c", index_status="Indexed")])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "object_url", lambda kind, handle: f"https://example.com/{kind}s/{handle}")

    targets, skipped = da._index_inspection_targets(conn, force_refresh=True)
    assert skipped == 0
    assert targets == all_targets


def test_index_inspection_targets_blog_article_skips_indexed(monkeypatch):
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    articles = [
        _Row(blog_handle="news", handle="post-1", index_status="Indexed", index_coverage=""),
        _Row(blog_handle="news", handle="post-2", index_status="Not Indexed", index_coverage=""),
    ]
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: articles)

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    assert skipped == 1
    assert len(targets) == 1
    assert targets[0][0] == "blog_article"
    assert targets[0][1] == "news/post-2"


def test_index_rate_cap_does_not_throttle_the_worker_pool() -> None:
    """The per-minute cap must sit above what the workers can actually produce.

    URL Inspection is latency-bound (~6.3s per call, measured), so real throughput is
    workers / latency. A cap below that silently re-introduces the old bottleneck, where
    5 workers and a 55/min cap made the sync take ~20 minutes for no reason.
    """
    from shopifyseo.dashboard_actions._state import (
        INDEX_SYNC_RATE_LIMIT_PER_MINUTE,
        INDEX_SYNC_WORKERS,
    )

    measured_latency_seconds = 6.3
    achievable_per_min = INDEX_SYNC_WORKERS * (60 / measured_latency_seconds)
    assert INDEX_SYNC_RATE_LIMIT_PER_MINUTE >= achievable_per_min, (
        f"cap {INDEX_SYNC_RATE_LIMIT_PER_MINUTE}/min throttles "
        f"{INDEX_SYNC_WORKERS} workers, which can do ~{achievable_per_min:.0f}/min"
    )
    # ...but stay clear of Google's documented 600/min ceiling.
    assert achievable_per_min <= 600, "worker pool could exceed the documented API rate limit"


def test_stale_indexed_url_selected_fresh_indexed_skipped(monkeypatch):
    """An indexed URL inspected 8 days ago is selected. One inspected 2 days ago is not."""
    import time
    conn = sqlite3.connect(":memory:")
    now = time.time()
    eight_days_ago = now - (8 * 86400)
    two_days_ago = now - (2 * 86400)

    products = [
        _Row(handle="stale", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago)),
        _Row(handle="fresh", index_status="Indexed", index_coverage="", index_last_fetched_at=int(two_days_ago)),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    assert skipped == 1
    assert [t[1] for t in targets] == ["stale"]


def test_not_indexed_comes_before_stale_indexed(monkeypatch):
    """Not-indexed URLs still come before stale indexed URLs."""
    import time
    conn = sqlite3.connect(":memory:")
    now = time.time()
    eight_days_ago = now - (8 * 86400)

    products = [
        _Row(handle="stale-indexed", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago)),
        _Row(handle="not-indexed", index_status="Not Indexed", index_coverage="", index_last_crawl_at="2026-01-01"),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    handles = [t[1] for t in targets]
    assert handles == ["not-indexed", "stale-indexed"]


def test_stale_indexed_ordered_oldest_first(monkeypatch):
    """Stale indexed URLs are ordered oldest inspection first."""
    import time
    conn = sqlite3.connect(":memory:")
    now = time.time()
    ten_days_ago = now - (10 * 86400)
    eight_days_ago = now - (8 * 86400)
    nine_days_ago = now - (9 * 86400)

    products = [
        _Row(handle="stale-8d", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago)),
        _Row(handle="stale-10d", index_status="Indexed", index_coverage="", index_last_fetched_at=int(ten_days_ago)),
        _Row(handle="stale-9d", index_status="Indexed", index_coverage="", index_last_fetched_at=int(nine_days_ago)),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    handles = [t[1] for t in targets]
    assert handles == ["stale-10d", "stale-9d", "stale-8d"]


def test_existing_priority_order_unchanged(monkeypatch):
    """The existing priority order for not-indexed URLs is preserved exactly.

    Priority order: stale_robots_block (0), robots_block_current (1), then others (2).
    Within each priority, sorted by crawl time ascending.
    """
    import time
    conn = sqlite3.connect(":memory:")
    now = time.time()
    eight_days_ago = now - (8 * 86400)

    products = [
        _Row(handle="normal-old", index_status="Not Indexed", index_coverage="", index_flag="", index_last_crawl_at="2020-01-01"),
        _Row(handle="current-block", index_status="Not Indexed", index_coverage="", index_flag="robots_block_current", index_last_crawl_at="2026-01-01"),
        _Row(handle="normal-new", index_status="Not Indexed", index_coverage="", index_flag="", index_last_crawl_at="2026-10-01"),
        _Row(handle="stale-block", index_status="Not Indexed", index_coverage="", index_flag="stale_robots_block", index_last_crawl_at="2026-10-01"),
        _Row(handle="stale-indexed", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago)),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    handles = [t[1] for t in targets]
    assert handles == ["stale-block", "current-block", "normal-old", "normal-new", "stale-indexed"]


def test_total_selected_includes_all_stale_no_artificial_cap(monkeypatch):
    """All not-indexed and stale indexed URLs are returned. No artificial cap on the list.

    The daily quota is enforced by the rate limiter and Google-side limits, not by
    truncating the target list.
    """
    import time
    conn = sqlite3.connect(":memory:")
    now = time.time()
    eight_days_ago = now - (8 * 86400)

    not_indexed = [_Row(handle=f"not-{i}", index_status="Not Indexed", index_coverage="") for i in range(50)]
    stale_indexed = [_Row(handle=f"stale-{i}", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago - i * 3600)) for i in range(100)]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: not_indexed + stale_indexed)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    assert len(targets) == 150
    assert skipped == 0
    not_indexed_handles = [t[1] for t in targets[:50]]
    stale_handles = [t[1] for t in targets[50:]]
    assert all(h.startswith("not-") for h in not_indexed_handles)
    assert all(h.startswith("stale-") for h in stale_handles)
