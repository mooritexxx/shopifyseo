"""Inspection evidence regressions; all Google requests mocked, robots fixture captured 2026-10-02."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shopifyseo import index_evidence as ie
from shopifyseo import dashboard_store as ds

URL = 'https://vapely.ca/products/beast-mode-max-2-kanzi'
NOW = ie.timestamp('2026-10-02T18:00:00Z')
CRAWL = '2026-09-15T19:00:00Z'


def payload(crawl=CRAWL):
    return {'inspectionResult': {'inspectionResultLink': 'https://search.google.com/search-console/inspect?test', 'indexStatusResult': {
        'coverageState': 'Blocked by robots.txt', 'googleCanonical': URL,
        'lastCrawlTime': crawl, 'robotsTxtState': 'DISALLOWED', 'pageFetchState': 'BLOCKED_ROBOTS_TXT',
        'indexingState': 'BLOCKED_BY_ROBOTS_TXT', 'verdict': 'FAIL'}}}


@pytest.fixture
def conn(db_conn):
    ds.ensure_dashboard_schema(db_conn)
    return db_conn


def insert_catalog(conn, kind='product', handle='x', **values):
    table = ie.TABLES[kind]
    row = dict(shopify_id=handle, title=handle, handle=handle, raw_json='{}', synced_at='')
    if kind in {'product', 'blog_article'}:
        row['tags_json'] = '[]'
    if kind == 'product':
        row['options_json'] = '[]'
    if kind == 'blog_article':
        conn.execute("INSERT INTO blogs(shopify_id,title,handle,tags_json,raw_json,synced_at) VALUES ('1','News','news','[]','{}','') ON CONFLICT DO NOTHING")
        row.update(blog_shopify_id='1', blog_handle=handle.split('/')[0], handle=handle.split('/')[1])
    row.update(values)
    conn.execute(f"INSERT INTO {table}({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))


@pytest.mark.parametrize('crawl', [CRAWL, None])
def test_extract_all_fields(crawl):
    assert ie.extract_inspection_fields(payload(crawl), NOW) == dict(
        index_status='Not Indexed', index_coverage='Blocked by robots.txt', google_canonical=URL,
        index_last_fetched_at=NOW, index_last_crawl_at=crawl, index_robots_state='DISALLOWED',
        index_page_fetch_state='BLOCKED_ROBOTS_TXT', index_indexing_state='BLOCKED_BY_ROBOTS_TXT', index_verdict='FAIL')
    assert ie.extract_inspection_fields({}, NOW) == {}


@pytest.mark.parametrize('path,allowed', [
    ('/products/beast-mode-max-2-kanzi', True), ('/collections/x?sort_by=price', False),
    ('/cart/123', False), ('/products/x?oseid=1', False),
])
def test_live_vapely_fixture(path, allowed):
    body = (Path(__file__).parent / 'fixtures/vapely-robots-2026-10-02.txt').read_text()
    assert ie.robots_allows(body, path) is allowed


@pytest.mark.parametrize('body,path,allowed', [
    ('User-agent: *\nDisallow: /\nAllow: /products', '/products/a', True),
    ('User-agent: *\nDisallow: /a\nAllow: /a', '/abc', True),
    ('User-agent: *\nAllow: /x\nDisallow: /x*', '/xyz', False),
    ('User-agent: Googlebot/2.1\nDisallow: /\nUser-agent: *\nAllow: /', '/abc', False),
    ('User-agent: *\nDisallow: /*.php$', '/a.php?x=1', True),
    ('User-agent: *\nDisallow: /*.php$', '/a.php', False),
    ('User-agent: *\nDisallow: /\nUser-agent: Googlebot\nAllow: /', '/abc', True),
    ('User-agent: Googlebot\nDisallow: /a\nUser-agent: googlebot\nAllow: /abc', '/abc', True),
    ('User-agent: Googlebot-News\nDisallow: /\nUser-agent: *\nAllow: /', '/abc', True),
    ('User-agent: *\nDisallow:\nSitemap: https://x/a', '/abc', True),
    ('User-agent: *\nDisallow: /caf%C3%A9', '/café', False),
    ('User-agent: *\nDisallow: /a%62c', '/abc', False),
    ('User-agent: *\nDisallow: /a%2Fb', '/a/b', True),
    ('User-agent: *\nDisallow: /cart\nAllow: /cart$', '/cart/1', False),
])
def test_google_robots_matching(body, path, allowed):
    assert ie.robots_allows(body, path) is allowed


def test_snapshots_flags_and_deduplication(conn):
    fields = ie.extract_inspection_fields(payload(), NOW)
    assert ie.derive_index_flag(fields, URL)[0] == ''
    first = ie.store_snapshot(conn, URL, 200, 'User-agent: *\nDisallow: /', now=NOW-100)
    assert ie.with_index_flag(conn, fields, URL)['index_flag'] == 'robots_block_current'
    assert ie.store_snapshot(conn, URL, 200, 'User-agent: *\nDisallow: /', now=NOW-50) == first
    assert conn.execute('SELECT COUNT(*) FROM robots_snapshots').fetchone()[0] == 1
    ie.store_snapshot(conn, URL, 200, 'User-agent: *\nAllow: /', now=NOW)
    result = ie.with_index_flag(conn, fields, URL)
    assert result['index_flag'] == 'stale_robots_block'
    assert '2026-09-15 12:00 PT' in result['index_flag_reason']
    assert 'probably failed' not in result['index_flag_reason']
    ie.store_snapshot(conn, URL, 503, 'unavailable', now=NOW+1)
    assert ie.with_index_flag(conn, fields, URL)['index_flag'] == ''


def test_allowing_snapshot_at_crawl_time_is_qualified(conn):
    ie.store_snapshot(conn, URL, 200, 'User-agent: *\nAllow: /', now=ie.timestamp(CRAWL)-1)
    result = ie.with_index_flag(conn, ie.extract_inspection_fields(payload(), NOW), URL)
    assert 'probably failed or differed' in result['index_flag_reason']


@pytest.mark.parametrize('status,coverage,crawl,expected', [
    ('Not Indexed', 'Discovered - currently not indexed', '2026-09-01T00:00:00Z', 'stale_crawl'),
    ('Not Indexed', 'Discovered - currently not indexed', '2026-10-01T00:00:00Z', ''),
    ('Indexed', 'Submitted and indexed', '2026-09-01T00:00:00Z', ''),
    ('Unknown', 'URL is unknown to Google', None, 'stale_crawl'),
])
def test_stale_crawl(status, coverage, crawl, expected):
    assert ie.derive_index_flag(dict(index_status=status, index_coverage=coverage, index_last_crawl_at=crawl), URL, now=NOW)[0] == expected


def test_cache_write_history_one_observation_per_pt_day(conn, monkeypatch):
    from shopifyseo.dashboard_google import _cache
    monkeypatch.setattr(_cache, '_now_ts', lambda: int(NOW))
    kwargs = dict(cache_key='inspect', cache_type='url_inspection', payload=payload(), ttl_seconds=1,
                  object_type='product', object_handle='x', url=URL)
    _cache._write_cache_payload(conn, **kwargs)
    _cache._write_cache_payload(conn, **kwargs)
    rows = conn.execute('SELECT * FROM index_status_history').fetchall()
    assert len(rows) == 1
    assert rows[0]['observed_date'] == '2026-10-02'
    assert rows[0]['index_last_crawl_at'] == CRAWL
    monkeypatch.setattr(_cache, '_now_ts', lambda: int(NOW + 86400))
    _cache._write_cache_payload(conn, **kwargs)
    assert conn.execute('SELECT COUNT(*) FROM index_status_history').fetchone()[0] == 2


@pytest.mark.parametrize('kind,table,handle', [
    ('product', 'products', 'x'), ('collection', 'collections', 'x'),
    ('page', 'pages', 'x'), ('blog_article', 'blog_articles', 'news/x'),
])
def test_all_catalog_writers_preserve_empty_and_reconcile_zero_calls(conn, monkeypatch, kind, table, handle):
    from shopifyseo.dashboard_queries import _urls
    from shopifyseo.dashboard_google import _cache
    monkeypatch.setattr(_urls, '_BASE_URL_CACHE', 'https://vapely.ca')
    insert_catalog(conn, kind, handle)
    url = _urls.object_url(kind, handle)
    _cache._write_cache_payload(conn, cache_key=kind, cache_type='url_inspection', payload=payload(), ttl_seconds=1,
                                object_type=kind, object_handle=handle, url=url)
    monkeypatch.setattr(ds.dg, 'google_api_post', lambda *a, **k: pytest.fail('Inspection API called'))
    assert ie.reconcile_index_cache(conn) == 1
    before = dict(conn.execute(f'SELECT * FROM {table}').fetchone())
    assert before['index_last_crawl_at'] == CRAWL
    assert not ie.update_catalog_inspection(conn, kind, handle, {}, url=url)
    after = dict(conn.execute(f'SELECT * FROM {table}').fetchone())
    assert after == before


def test_target_order_prioritizes_flags_then_crawl(conn):
    from shopifyseo.dashboard_actions._sync import _index_inspection_targets
    for handle, flag, crawl in [('old', '', '2020-01-01'), ('current', 'robots_block_current', '2026-01-01'),
                                ('stale', 'stale_robots_block', '2026-10-01'), ('new', '', '2026-10-01')]:
        insert_catalog(conn, handle=handle, index_status='Not Indexed', index_flag=flag, index_last_crawl_at=crawl)
    result = _index_inspection_targets(conn, force_refresh=True)
    assert [row[1] for row in result['targets']] == ['stale', 'current', 'old', 'new']


def test_alerts_and_failed_fetch_nonfatal(conn, monkeypatch):
    import requests
    ie.store_snapshot(conn, URL, 200, 'User-agent: *\nAllow: /\n' + '# padding\n' * 100, now=1)
    ie.store_snapshot(conn, URL, 200, 'User-agent: *\nDisallow: /', now=2)
    alerts = ie.index_evidence_rollup(conn)['robots_alerts']
    assert len(alerts) == 3
    def fail(*a, **k):
        assert k['timeout'] == 10 and 'Googlebot' in k['headers']['User-Agent']
        raise requests.Timeout()
    monkeypatch.setattr(ie.requests, 'get', fail)
    ie.fetch_robots_snapshot(conn, URL)
    assert any('non-200' in a for a in ie.index_evidence_rollup(conn)['robots_alerts'])


def test_recrawl_endpoint_order_and_response(conn, monkeypatch):
    from backend.app.main import app
    from backend.app.services import index_evidence as service
    class Borrow:
        def __getattr__(self, key):
            return getattr(conn, key)
        def close(self):
            pass
    monkeypatch.setattr(service, 'open_db_connection', Borrow)
    for handle, stock, impressions in [('out', 0, 1000), ('in-low', 1, 5), ('in-high', 1, 100)]:
        insert_catalog(conn, handle=handle, vendor='Brand', total_inventory=stock, gsc_impressions=impressions,
                       index_flag='stale_robots_block', index_last_crawl_at=CRAWL, index_last_fetched_at=int(NOW))
    response = TestClient(app).get('/api/index/recrawl-candidates')
    assert response.status_code == 200
    items = response.json()['data']
    assert [x['handle'] for x in items] == ['in-high', 'in-low', 'out']
    assert items[0]['crawled_at'] == CRAWL and items[0]['inspected_at'] == NOW
    assert set(items[0]) == {'url','handle','vendor','in_stock','coverage','crawled_at','inspected_at','inspect_href','flag_reason'}
    assert TestClient(app).get('/api/index/recrawl-candidates?flag=invalid').status_code == 422


@pytest.mark.parametrize('kind,handle', [('product','x'), ('collection','x'), ('page','x'), ('blog_article','news/x')])
def test_full_signal_writer_uses_same_fields_and_preserves_group(conn, monkeypatch, kind, handle):
    insert_catalog(conn, kind, handle)
    monkeypatch.setattr(ds.dg, 'get_search_console_url_detail', lambda *a, **k: {})
    monkeypatch.setattr(ds.dg, 'get_ga4_url_detail', lambda *a, **k: {})
    monkeypatch.setattr(ds.dg, 'get_url_inspection', lambda *a, **k: {**payload(), '_cache': {'fetched_at': int(NOW)}})
    # Call the catalog cache reconcile, covering all-signal and index-only paths.
    if kind == 'blog_article':
        ds._refresh_blog_article_signals_into_table(conn, handle)
    else:
        ds._refresh_object_signals_into_table(conn, ie.TABLES[kind], kind, handle)
    row = dict(conn.execute(f'SELECT * FROM {ie.TABLES[kind]}').fetchone())
    assert {k: row[k] for k in ie.INDEX_FIELDS} == ie.extract_inspection_fields(payload(), int(NOW))


@pytest.mark.parametrize('flag,reason', [('stale_robots_block', 'stale robots block: request recrawl'),
                                       ('robots_block_current', 'current robots block: P1 technical fix')])
def test_robots_scoring_and_ai_technical_action(flag, reason):
    from shopifyseo.dashboard_queries._seo_facts import build_seo_fact
    from shopifyseo.dashboard_ai_engine_parts.prompts import build_signal_narrative
    row = dict(handle='x', title='Valid product title', seo_title='A valid SEO title of good length',
               seo_description='A valid description of the product and its specifications for an informed purchase.',
               description_html='Good specific product information. ' * 25, index_status='Not Indexed',
               index_flag=flag, index_flag_reason='Technical evidence')
    fact = build_seo_fact('product', row, None)
    assert fact['score'] == 20 and fact['reasons'] == [reason]
    assert fact['index_action_type'] == 'technical'
    narrative = build_signal_narrative({'fact': fact})
    assert 'clearer answer-first copy' not in narrative
    assert 'technical robots/recrawl action' in narrative


@pytest.mark.parametrize('kind,path', [('product','/api/products'), ('collection','/api/collections'),
                                      ('page','/api/pages'), ('blog_article','/api/articles')])
def test_inspection_fields_survive_http_contract(conn, monkeypatch, kind, path):
    from backend.app.main import app
    from backend.app.services import product_service, content_service, article_service
    from shopifyseo.dashboard_queries import _urls
    monkeypatch.setattr(_urls, '_BASE_URL_CACHE', 'https://vapely.ca')
    class Borrow:
        def __getattr__(self, key):
            return getattr(conn, key)
        def close(self):
            pass
    for svc in (product_service, content_service, article_service):
        monkeypatch.setattr(svc, 'open_db_connection', Borrow)
    handle = 'news/x' if kind == 'blog_article' else 'x'
    insert_catalog(conn, kind, handle)
    ie.update_catalog_inspection(conn, kind, handle, payload(), int(NOW), url=_urls.object_url(kind, handle))
    response = TestClient(app).get(path)
    assert response.status_code == 200, response.text
    items = response.json()['data']['items']
    assert len(items) == 1
    expected = ie.extract_inspection_fields(payload(), int(NOW))
    for key, value in expected.items():
        assert items[0][key] == value, key
    assert isinstance(items[0]['crawl_age_days'], int)


def test_bulk_sync_fetches_snapshot_and_reconciles_before_targets(conn, monkeypatch):
    from shopifyseo.dashboard_actions import _sync
    class Borrow:
        def __getattr__(self, key):
            return getattr(conn, key)
        def close(self):
            pass
    monkeypatch.setattr(_sync, '_db_connect_for_actions', lambda _: Borrow())
    calls = []
    def snapshot(c, url):
        calls.append('snapshot')
        return ie.store_snapshot(c, url, 200, 'User-agent: *\nAllow: /')
    def reconcile(c):
        assert calls == ['snapshot']
        calls.append('reconcile')
        return 0
    def targets(c, **kwargs):
        assert calls == ['snapshot', 'reconcile']
        calls.append('targets')
        return {'targets': [], 'skipped_indexed': 0, 'stale_reinspect_selected': 0, 'stale_reinspect_deferred_budget': 0}
    monkeypatch.setattr(ie, 'fetch_robots_snapshot', snapshot)
    monkeypatch.setattr(ie, 'reconcile_index_cache', reconcile)
    monkeypatch.setattr(_sync, '_index_inspection_targets', targets)
    monkeypatch.setattr(_sync.dg, 'get_search_console_sites', lambda c: [])
    monkeypatch.setattr(_sync.dg, 'preferred_site_url', lambda *a: '')
    monkeypatch.setattr(_sync.dg, 'get_search_data_access_token', lambda c: '')
    result = _sync.bulk_refresh_index_status(':memory:')
    assert calls == ['snapshot', 'reconcile', 'targets']
    assert result['refreshed'] == 0
    assert result['stale_robots_block'] == 0


def test_rollup_counts_flags_and_crawl_age(conn):
    insert_catalog(conn, handle='stale', index_flag='stale_robots_block', index_last_crawl_at='2020-01-01T00:00:00Z')
    insert_catalog(conn, handle='current', index_flag='robots_block_current')
    result = ie.index_evidence_rollup(conn)
    assert result['stale_robots_block'] == 1
    assert result['robots_block_current'] == 1
    assert result['crawl_older_than_21d'] == 1
    assert any('P1' in alert for alert in result['robots_alerts'])


def test_rollup_inspection_stale_boundary(conn):
    """Test that rollup counts inspection_older_than_7d correctly at boundary."""
    import time
    now = time.time()
    exactly_7d = now - (7 * 86400)
    just_over_7d = now - (7 * 86400) - 60
    just_under_7d = now - (7 * 86400) + 60

    insert_catalog(conn, handle='exactly-7d', index_last_fetched_at=int(exactly_7d))
    insert_catalog(conn, handle='over-7d', index_last_fetched_at=int(just_over_7d))
    insert_catalog(conn, handle='under-7d', index_last_fetched_at=int(just_under_7d))

    result = ie.index_evidence_rollup(conn)
    assert result['inspection_older_than_7d'] == 2
    assert result['inspection_total'] == 3


def test_rollup_inspection_older_than_7d_in_sync_result(conn, monkeypatch):
    """Test that inspection_older_than_7d appears in the sync result via index_evidence_rollup."""
    import time
    from shopifyseo.dashboard_actions import _sync
    now = time.time()
    eight_days_ago = now - (8 * 86400)

    insert_catalog(conn, handle='stale', index_last_fetched_at=int(eight_days_ago))

    class Borrow:
        def __getattr__(self, key):
            return getattr(conn, key)
        def close(self):
            pass

    monkeypatch.setattr(_sync, '_db_connect_for_actions', lambda _: Borrow())
    monkeypatch.setattr(ie, 'fetch_robots_snapshot', lambda c, url: ie.store_snapshot(c, url, 200, 'User-agent: *\nAllow: /'))
    monkeypatch.setattr(ie, 'reconcile_index_cache', lambda c: 0)
    monkeypatch.setattr(_sync, '_index_inspection_targets', lambda c, **k: {'targets': [], 'skipped_indexed': 0, 'stale_reinspect_selected': 0, 'stale_reinspect_deferred_budget': 0})
    monkeypatch.setattr(_sync.dg, 'get_search_console_sites', lambda c: [])
    monkeypatch.setattr(_sync.dg, 'preferred_site_url', lambda *a: '')
    monkeypatch.setattr(_sync.dg, 'get_search_data_access_token', lambda c: '')

    result = _sync.bulk_refresh_index_status(':memory:')
    assert 'inspection_older_than_7d' in result
    assert result['inspection_older_than_7d'] == 1
    assert result['inspection_total'] == 1


def test_rollup_stale_inspection_in_summary_endpoint(conn, monkeypatch):
    """Test that inspection_older_than_7d appears in /api/summary indexing_rollup."""
    import time
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.services import dashboard_service

    now = time.time()
    eight_days_ago = now - (8 * 86400)
    two_days_ago = now - (2 * 86400)

    insert_catalog(conn, handle='stale', index_last_fetched_at=int(eight_days_ago))
    insert_catalog(conn, handle='fresh', index_last_fetched_at=int(two_days_ago))

    class Borrow:
        def __getattr__(self, key):
            return getattr(conn, key)
        def close(self):
            pass

    monkeypatch.setattr(dashboard_service, 'open_db_connection', Borrow)

    response = TestClient(app).get('/api/summary')
    assert response.status_code == 200
    idx = response.json()['data']['indexing_rollup']
    assert 'inspection_older_than_7d' in idx
    assert idx['inspection_older_than_7d'] == 1
    assert 'inspection_total' in idx
    assert idx['inspection_total'] == 2


def test_signal_card_crawl_and_inspection_times_are_distinct(conn):
    from backend.app.services._catalog_helpers import _signal_cards_for
    row = dict(handle='x', index_status='Not Indexed', index_coverage='Blocked by robots.txt', google_canonical='',
               index_last_crawl_at=CRAWL, index_last_fetched_at=int(NOW), index_flag='stale_robots_block',
               index_flag_reason='Current file allows URL.', gsc_last_fetched_at=None, ga4_last_fetched_at=None,
               pagespeed_last_fetched_at=None, pagespeed_desktop_last_fetched_at=None)
    # The other cards use optional metric values; reuse a complete catalog row.
    insert_catalog(conn, **{k:v for k,v in row.items() if k != 'handle'})
    current = dict(conn.execute('SELECT * FROM products').fetchone())
    cards = _signal_cards_for(conn, 'product', current, signals={'inspection_detail':payload()})
    index = cards[0]
    assert 'crawled Sep 15' in index['sublabel'] and 'inspected Oct 2' in index['sublabel']
    assert index['badge'] == 'Stale: crawl predates current robots.txt'
    assert index['action_href'] == payload()['inspectionResult']['inspectionResultLink']
