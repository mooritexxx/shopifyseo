"""Tests for index sync target selection (skip already-indexed URLs unless force refresh)."""

from shopifyseo import dashboard_actions as da
from shopifyseo import dashboard_store as ds
from shopifyseo.index_evidence import URL_INSPECTION_DAILY_BUDGET


class _Row(dict):
    def __getitem__(self, key: str):
        return self.get(key)


def test_index_inspection_targets_skips_indexed_when_not_force(monkeypatch, db_conn):
    conn = db_conn

    products = [
        _Row(handle="a", index_status="Indexed", index_coverage=""),
        _Row(handle="b", index_status="Needs Review", index_coverage=""),
        _Row(handle="c", index_status="Unknown", index_coverage=""),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    result = da._index_inspection_targets(conn, force_refresh=False)
    assert result['skipped_indexed'] == 1
    assert [t[1] for t in result['targets']] == ["b", "c"]


def test_index_inspection_targets_force_refresh_uses_all_targets(monkeypatch, db_conn):
    conn = db_conn
    all_targets = [
        ("product", "a", "https://example.com/products/a"),
        ("collection", "c", "https://example.com/collections/c"),
    ]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: [_Row(handle="a", index_status="Indexed")])
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [_Row(handle="c", index_status="Indexed")])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "object_url", lambda kind, handle: f"https://example.com/{kind}s/{handle}")

    result = da._index_inspection_targets(conn, force_refresh=True)
    assert result['skipped_indexed'] == 0
    assert result['targets'] == all_targets


def test_index_inspection_targets_blog_article_skips_indexed(monkeypatch, db_conn):
    conn = db_conn
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    articles = [
        _Row(blog_handle="news", handle="post-1", index_status="Indexed", index_coverage=""),
        _Row(blog_handle="news", handle="post-2", index_status="Not Indexed", index_coverage=""),
    ]
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: articles)

    result = da._index_inspection_targets(conn, force_refresh=False)
    assert result['skipped_indexed'] == 1
    assert len(result['targets']) == 1
    assert result['targets'][0][0] == "blog_article"
    assert result['targets'][0][1] == "news/post-2"


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


def test_stale_indexed_url_selected_fresh_indexed_skipped(monkeypatch, db_conn):
    """An indexed URL inspected 8 days ago is selected. One inspected 2 days ago is not."""
    import time
    conn = db_conn
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

    result = da._index_inspection_targets(conn, force_refresh=False)
    assert result['skipped_indexed'] == 1
    assert [t[1] for t in result['targets']] == ["stale"]


def test_not_indexed_comes_before_stale_indexed(monkeypatch, db_conn):
    """Not-indexed URLs still come before stale indexed URLs."""
    import time
    conn = db_conn
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

    result = da._index_inspection_targets(conn, force_refresh=False)
    handles = [t[1] for t in result['targets']]
    assert handles == ["not-indexed", "stale-indexed"]


def test_stale_indexed_ordered_oldest_first(monkeypatch, db_conn):
    """Stale indexed URLs are ordered oldest inspection first."""
    import time
    conn = db_conn
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

    result = da._index_inspection_targets(conn, force_refresh=False)
    handles = [t[1] for t in result['targets']]
    assert handles == ["stale-10d", "stale-9d", "stale-8d"]


def test_existing_priority_order_unchanged(monkeypatch, db_conn):
    """The existing priority order for not-indexed URLs is preserved exactly.

    Priority order: stale_robots_block (0), robots_block_current (1), then others (2).
    Within each priority, sorted by crawl time ascending.
    """
    import time
    conn = db_conn
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

    result = da._index_inspection_targets(conn, force_refresh=False)
    handles = [t[1] for t in result['targets']]
    assert handles == ["stale-block", "current-block", "normal-old", "normal-new", "stale-indexed"]


def test_stale_reinspect_budget_enforcement(monkeypatch, db_conn):
    """Stale indexed URLs are capped by the daily budget; non-stale are always included.

    With many stale URLs, plus non-zero used_today, plus non-stale targets, the selected
    stale count equals exactly the remaining budget, and the deferred count is reported.
    """
    import time
    conn = db_conn
    now = time.time()
    eight_days_ago = now - (8 * 86400)

    non_stale_count = 50
    stale_count = 100
    used_today = 1900

    not_indexed = [_Row(handle=f"not-{i}", index_status="Not Indexed", index_coverage="") for i in range(non_stale_count)]
    stale_indexed = [_Row(handle=f"stale-{i}", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago - i * 3600)) for i in range(stale_count)]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: not_indexed + stale_indexed)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    from shopifyseo import index_evidence as ie
    monkeypatch.setattr(ie, "url_inspection_used_today", lambda c, now_fn=None: used_today)

    result = da._index_inspection_targets(conn, force_refresh=False)

    remaining_budget = URL_INSPECTION_DAILY_BUDGET - used_today - non_stale_count
    expected_stale_selected = remaining_budget
    expected_stale_deferred = stale_count - expected_stale_selected

    assert result['stale_reinspect_selected'] == expected_stale_selected
    assert result['stale_reinspect_deferred_budget'] == expected_stale_deferred
    assert len(result['targets']) == non_stale_count + expected_stale_selected

    non_stale_handles = [t[1] for t in result['targets'][:non_stale_count]]
    stale_handles = [t[1] for t in result['targets'][non_stale_count:]]
    assert all(h.startswith("not-") for h in non_stale_handles)
    assert all(h.startswith("stale-") for h in stale_handles)

    total_selected = non_stale_count + result['stale_reinspect_selected'] + used_today
    assert total_selected <= URL_INSPECTION_DAILY_BUDGET


def test_stale_reinspect_zero_remaining_budget(monkeypatch, db_conn):
    """When remaining budget is 0 or negative, 0 stale selected, non-stale list unchanged."""
    import time
    conn = db_conn
    now = time.time()
    eight_days_ago = now - (8 * 86400)

    non_stale_count = 50
    stale_count = 100
    used_today = URL_INSPECTION_DAILY_BUDGET

    not_indexed = [_Row(handle=f"not-{i}", index_status="Not Indexed", index_coverage="") for i in range(non_stale_count)]
    stale_indexed = [_Row(handle=f"stale-{i}", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago - i * 3600)) for i in range(stale_count)]
    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: not_indexed + stale_indexed)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    from shopifyseo import index_evidence as ie
    monkeypatch.setattr(ie, "url_inspection_used_today", lambda c, now_fn=None: used_today)

    result = da._index_inspection_targets(conn, force_refresh=False)

    assert result['stale_reinspect_selected'] == 0
    assert result['stale_reinspect_deferred_budget'] == stale_count
    assert len(result['targets']) == non_stale_count
    assert all(t[1].startswith("not-") for t in result['targets'])


def test_stale_reinspect_stats_in_sync_status_via_real_sync(monkeypatch, db_connect):
    """Verify stale reinspect stats appear in /api/sync-status via the real sync code path.

    Uses _run_selected_sync_steps with "index" scope, which stores result["index"] and
    sets SYNC_STATE["last_result"]. We then hit GET /api/sync-status and verify that
    last_result.index contains non-zero stale stats.
    """
    import time
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from shopifyseo.dashboard_actions import _sync
    from shopifyseo.dashboard_actions._state import SYNC_STATE, SYNC_LOCK
    from shopifyseo import index_evidence as ie
    from shopifyseo.dashboard_google import _cache as google_cache

    conn = db_connect()
    ds.ensure_dashboard_schema(conn)
    google_cache.ensure_google_cache_schema(conn)
    monkeypatch.setattr(_sync, "_db_connect_for_actions", lambda _path: db_connect())

    now = time.time()
    eight_days_ago = now - (8 * 86400)
    nine_days_ago = now - (9 * 86400)
    two_days_ago = now - (2 * 86400)

    conn.execute(
        "INSERT INTO products(shopify_id,title,handle,tags_json,options_json,raw_json,synced_at,index_status,index_last_fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("1", "Stale1", "stale1", "[]", "[]", "{}", "", "Indexed", int(eight_days_ago))
    )
    conn.execute(
        "INSERT INTO products(shopify_id,title,handle,tags_json,options_json,raw_json,synced_at,index_status,index_last_fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("2", "Stale2", "stale2", "[]", "[]", "{}", "", "Indexed", int(nine_days_ago))
    )
    conn.execute(
        "INSERT INTO products(shopify_id,title,handle,tags_json,options_json,raw_json,synced_at,index_status,index_last_fetched_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("3", "Fresh", "fresh", "[]", "[]", "{}", "", "Indexed", int(two_days_ago))
    )
    conn.commit()

    used_today = URL_INSPECTION_DAILY_BUDGET - 1

    monkeypatch.setattr(ie, 'url_inspection_used_today', lambda c, now_fn=None: used_today)
    monkeypatch.setattr(ie, 'fetch_robots_snapshot', lambda c, url: ie.store_snapshot(c, url, 200, 'User-agent: *\nAllow: /'))
    monkeypatch.setattr(_sync.dg, 'get_search_console_sites', lambda c: [])
    monkeypatch.setattr(_sync.dg, 'preferred_site_url', lambda *a: '')
    monkeypatch.setattr(_sync.dg, 'get_search_data_access_token', lambda c: '')
    monkeypatch.setattr(_sync.dg, 'get_url_inspection', lambda *a, **k: {'inspectionResult': {'indexStatusResult': {'coverageState': 'Indexed', 'verdict': 'PASS'}}})

    old_state = dict(SYNC_STATE)
    try:
        with SYNC_LOCK:
            SYNC_STATE["running"] = False
            SYNC_STATE["last_result"] = None

        result = _sync.run_sync("unused.db", scope="index", selected_scopes=["index"], force_refresh=False)

        response = TestClient(app).get('/api/sync-status')
        assert response.status_code == 200
        data = response.json()['data']
        assert 'last_result' in data
        last_result = data['last_result']

        # M5 mutation: SYNC_STATE["last_result"] = result deleted -> last_result is None
        assert last_result is not None, "last_result must not be None (SYNC_STATE['last_result'] must be set)"
        
        # M4 mutation: result["index"] = {} -> index key missing or empty
        assert isinstance(last_result, dict), f"last_result must be a dict, got {type(last_result)}"
        assert 'index' in last_result, "last_result must contain 'index' key from real sync"
        index_result = last_result['index']
        assert isinstance(index_result, dict), f"index must be a dict, got {type(index_result)}"
        
        # M6 mutation: stale stats not copied -> values are 0 instead of actual counts
        # stale_reinspect_selected/deferred_budget come from target selection BEFORE refresh
        # 2 stale products (8d and 9d old), budget allows 1 selected, 1 deferred
        stale_selected = index_result.get('stale_reinspect_selected')
        stale_deferred = index_result.get('stale_reinspect_deferred_budget')
        assert stale_selected == 1, f"Expected 1 selected (budget=1), got {stale_selected}"
        assert stale_deferred == 1, f"Expected 1 deferred, got {stale_deferred}"
        
        # inspection_older_than_7d is computed AFTER refresh - one stale was refreshed, one remains
        insp_older = index_result.get('inspection_older_than_7d')
        assert insp_older == 1, f"Expected 1 stale (one was refreshed), got {insp_older}"
        
        # Verify all stale-related fields are non-zero integers
        assert isinstance(stale_selected, int), f"stale_reinspect_selected must be int, got {type(stale_selected)}"
        assert isinstance(stale_deferred, int), f"stale_reinspect_deferred_budget must be int, got {type(stale_deferred)}"
        assert stale_selected + stale_deferred == 2, "Total stale should be selected + deferred"
    finally:
        SYNC_STATE.clear()
        SYNC_STATE.update(old_state)


def test_url_inspection_used_today_counts_real_cache_rows(db_conn):
    """Test url_inspection_used_today with real DB rows and injectable clock.

    Rows fetched today (LA time) count. Rows from before LA midnight do not.
    Cover the boundary: 23:59 LA yesterday vs 00:00 LA today.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from shopifyseo.index_evidence import url_inspection_used_today
    from shopifyseo.dashboard_google import _cache as google_cache

    LA = ZoneInfo('America/Los_Angeles')
    fake_now_la = datetime(2026, 10, 4, 12, 0, 0, tzinfo=LA)
    fake_now_epoch = fake_now_la.timestamp()

    la_midnight = fake_now_la.replace(hour=0, minute=0, second=0, microsecond=0)
    la_midnight_epoch = int(la_midnight.timestamp())
    one_minute_before_midnight = la_midnight_epoch - 60
    one_minute_after_midnight = la_midnight_epoch + 60

    conn = db_conn
    google_cache.ensure_google_cache_schema(conn)

    conn.execute(
        "INSERT INTO google_api_cache(cache_key, cache_type, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        ("key1", "url_inspection", "{}", one_minute_before_midnight, la_midnight_epoch + 86400)
    )
    conn.execute(
        "INSERT INTO google_api_cache(cache_key, cache_type, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        ("key2", "url_inspection", "{}", one_minute_after_midnight, la_midnight_epoch + 86400)
    )
    conn.execute(
        "INSERT INTO google_api_cache(cache_key, cache_type, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        ("key3", "url_inspection", "{}", int(fake_now_epoch) - 3600, la_midnight_epoch + 86400)
    )
    conn.execute(
        "INSERT INTO google_api_cache(cache_key, cache_type, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
        ("key4", "pagespeed", "{}", int(fake_now_epoch), la_midnight_epoch + 86400)
    )
    conn.commit()

    count = url_inspection_used_today(conn, now_fn=lambda: fake_now_epoch)

    assert count == 2, f"Expected 2 (key2 and key3 are today), got {count}"


def test_real_used_today_reduces_stale_selection(monkeypatch, db_conn):
    """Test that the REAL url_inspection_used_today helper (not a stub) reduces stale selection.

    Insert cache rows for today, then verify that the real helper counts them and
    the budget calculation defers some stale URLs.
    """
    import time
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from shopifyseo.dashboard_google import _cache as google_cache
    from shopifyseo import index_evidence as ie

    LA = ZoneInfo('America/Los_Angeles')
    now = time.time()
    la_now = datetime.fromtimestamp(now, LA)
    la_midnight = la_now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_today = int(la_midnight.timestamp())
    eight_days_ago = now - (8 * 86400)

    conn = db_conn
    ds.ensure_dashboard_schema(conn)
    google_cache.ensure_google_cache_schema(conn)

    for i in range(1995):
        conn.execute(
            "INSERT INTO google_api_cache(cache_key, cache_type, payload_json, fetched_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (f"used_{i}", "url_inspection", "{}", start_of_today + 60 + i, start_of_today + 86400)
        )

    stale_urls = [_Row(handle=f"stale-{i}", index_status="Indexed", index_coverage="", index_last_fetched_at=int(eight_days_ago - i * 3600)) for i in range(10)]
    not_indexed = [_Row(handle=f"not-{i}", index_status="Not Indexed", index_coverage="") for i in range(3)]

    monkeypatch.setattr(da.dq, "fetch_products_for_facts", lambda _c: not_indexed + stale_urls)
    monkeypatch.setattr(da.dq, "fetch_collections_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_pages_for_facts", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_blog_articles_for_facts", lambda _c: [])

    conn.commit()

    result = da._index_inspection_targets(conn, force_refresh=False, now_fn=lambda: now)

    used = ie.url_inspection_used_today(conn, now_fn=lambda: now)
    assert used == 1995, f"Real helper should count 1995 rows, got {used}"

    remaining = URL_INSPECTION_DAILY_BUDGET - used - 3
    assert remaining == 2, f"Remaining budget should be 2 (2000-1995-3), got {remaining}"
    assert result['stale_reinspect_selected'] == 2, f"Expected 2 stale selected (remaining budget), got {result['stale_reinspect_selected']}"
    assert result['stale_reinspect_deferred_budget'] == 8, f"Expected 8 stale deferred, got {result['stale_reinspect_deferred_budget']}"
