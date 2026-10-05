import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.services import rank_tracking as svc
from shopifyseo.rank_tracking import serp
from shopifyseo.rank_tracking.store import ensure_schema


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / 'rank.sqlite3'
    def connect():
        c = sqlite3.connect(path, timeout=10)
        c.row_factory = sqlite3.Row
        return c
    conn = connect()
    conn.executescript('''CREATE TABLE service_settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE api_usage_log(id INTEGER PRIMARY KEY, provider TEXT,model TEXT,call_type TEXT,stage TEXT,
        input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,estimated_cost_usd REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP);''')
    ensure_schema(conn)
    monkeypatch.setattr(svc, 'open_db_connection', connect)
    monkeypatch.setattr(svc, 'remaining_credits', lambda _: 10000)
    yield conn, connect
    conn.close()


def pages():
    return [json.loads((Path(__file__).parent / 'fixtures' / 'rankings' / f'page-{i}.json').read_text()) for i in range(5)]


def check(monkeypatch, payloads, max_pages=5):
    mock = Mock(side_effect=payloads)
    monkeypatch.setattr(serp, 'request_page', mock)
    result = serp.check_term('term', max_pages, 'secret', lambda: None)
    return result, mock


@pytest.mark.parametrize('page,position,expected', [(0,1,1),(0,7,7),(2,7,27)])
def test_rank_uses_offsets(monkeypatch,page,position,expected):
    payloads = pages()
    payloads[page]['organic_results'][position-1]['link'] = 'https://vapely.ca/pages/brand?srsltid=tracking'
    result, mock = check(monkeypatch,payloads)
    assert result['position'] == expected
    assert result['ranking_url'] == 'https://vapely.ca/pages/brand'
    assert mock.call_count == page+1


def test_short_first_page_does_not_renumber_second_page(monkeypatch):
    payloads = pages()
    payloads[0]['organic_results'] = payloads[0]['organic_results'][:3]
    payloads[1]['organic_results'][3]['link'] = 'https://vapely.ca/pages/allo'
    result,_ = check(monkeypatch,payloads)
    assert result['position'] == 14  # old script incorrectly called this #7


def test_not_found_only_for_verified_depth(monkeypatch):
    result,_ = check(monkeypatch,pages())
    assert result['status'] == 'ok' and result['position'] is None and result['checked_depth'] == 50
    result,_ = check(monkeypatch,pages(),max_pages=2)
    assert result['checked_depth'] == 20


@pytest.mark.parametrize('kind', ['missing','offset','repeated','positions'])
def test_uncertain_results_are_errors(monkeypatch,kind):
    p = pages()
    if kind == 'missing': p[0].pop('organic_results')
    if kind == 'offset': p[1]['search_parameters']['start'] = 0
    if kind == 'repeated': p[1]['organic_results'] = p[0]['organic_results']
    if kind == 'positions': p[0]['organic_results'][0]['position'] = 42
    result,_ = check(monkeypatch,p)
    assert result['status'] == 'error' and result['position'] is None


def test_url_safety():
    assert serp.clean_url('https://www.vapely.ca/p?utm_source=x&srsltid=y&variant=2#x') == 'https://www.vapely.ca/p?variant=2'
    assert not serp.is_target('https://vapely.ca.evil.example/p')
    assert not serp.clean_url('javascript:alert(1)')
    assert serp.url_identity('https://www.vapely.ca/p/') == serp.url_identity('http://vapely.ca/p')


@pytest.mark.parametrize('first', [requests.Timeout('secret'), Mock(status_code=429)])
def test_retry_accounted_and_secret_not_exposed(monkeypatch,first):
    good = Mock(status_code=200); good.json.return_value=pages()[0]
    http = Mock(side_effect=[first,good])
    monkeypatch.setattr(serp.requests,'get',http)
    monkeypatch.setattr(serp.time,'sleep',lambda _:None)
    count=Mock()
    assert serp.request_page('secret',{},count) == pages()[0]
    assert count.call_count == 2


def test_no_retry_for_auth_or_payload_error(monkeypatch):
    http=Mock(return_value=Mock(status_code=401))
    monkeypatch.setattr(serp.requests,'get',http)
    with pytest.raises(serp.RankError,match='HTTP 401'):
        serp.request_page('secret',{},lambda:None)
    assert http.call_count == 1
    response=Mock(status_code=200);response.json.return_value={'error':'secret echoed'}
    http.return_value=response
    with pytest.raises(serp.RankError) as e:
        serp.request_page('secret',{},lambda:None)
    assert 'secret' not in str(e.value)


def test_seed_remove_restore(database):
    conn,_=database
    assert len(svc.list_rankings(conn)['items']) == 16
    k=svc.save_keyword(conn,'  NEW   Term ','https://vapely.ca/pages/new','brand')
    assert k['term']=='new term'
    svc.remove_keyword(conn,k['id'])
    ensure_schema(conn)
    assert len(svc.list_rankings(conn)['items']) == 16
    assert svc.save_keyword(conn,'new term')['id']==k['id']
    with pytest.raises(serp.RankError): svc.save_keyword(conn,'bad','https://evil.example/')


def test_list_rankings_latest_job_is_last_inserted_on_created_at_tie(database):
    """SQLite rowid tie-break matches main: last insert wins even when its uuid is smaller."""
    conn, _ = database
    stamp = '2026-10-01T00:00:00-07:00'
    conn.execute(
        '''INSERT INTO rank_jobs(id,request_key,weekly_date,status,keyword_ids,max_pages,reserved,created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        ('zzzz-later-lex', 'k1', None, 'done', '[]', 1, 0, stamp),
    )
    conn.execute(
        '''INSERT INTO rank_jobs(id,request_key,weekly_date,status,keyword_ids,max_pages,reserved,created_at)
           VALUES (?,?,?,?,?,?,?,?)''',
        ('aaaa-earlier-lex', 'k2', None, 'done', '[]', 1, 0, stamp),
    )
    conn.commit()
    assert svc.list_rankings(conn)['job']['id'] == 'aaaa-earlier-lex'


def test_estimate_includes_retries_and_budget(database,monkeypatch):
    conn,_=database
    assert svc.estimate(conn,None,5)['searches_worst_case']==160
    conn.execute("UPDATE service_settings SET value='159' WHERE key='serpapi_rank_monthly_budget'");conn.commit()
    assert not svc.estimate(conn,None,5)['allowed']
    with pytest.raises(serp.RankError):svc.start_job(conn,None,5,'over',launch=False)
    conn.execute("UPDATE service_settings SET value='250' WHERE key='serpapi_rank_monthly_budget'");conn.commit()
    monkeypatch.setattr(svc,'remaining_credits',lambda _:159)
    assert not svc.estimate(conn,None,5)['allowed']


def test_concurrent_jobs_cannot_double_reserve(database):
    conn,connect=database
    def start(n):
        c=connect()
        try:return svc.start_job(c,[1],1,str(n),launch=False)['job_id']
        except serp.RankError:return None
        finally:c.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(start,[1,2]))
    assert sum(r is not None for r in results)==1
    assert svc.usage(conn)['reserved']==2


def test_weekly_and_manual_idempotency_restart(database):
    conn,_=database
    job=svc.start_job(conn,[1],1,'first',weekly=True,launch=False)
    svc.record_request(job['job_id'],1)
    again=svc.start_job(conn,[1],1,'other',weekly=True,launch=False)
    assert again['skipped'] and again['job_id']==job['job_id']
    svc.recover_jobs(conn)
    u=svc.usage(conn)
    assert u['month_used']==1 and u['reserved']==0
    assert svc.history(conn,1)[0]['status']=='error'
    assert svc.start_job(conn,[1],1,'first',launch=False)['skipped']


def test_run_job_persists_errors_and_usage(database,monkeypatch):
    conn,_=database
    def fake(key,params,before):
        before()
        if params['q']=='vape shop canada': raise serp.RankError('Timeout')
        p=pages()[0];p['organic_results'][1]['link']='https://vapely.ca/p'
        return p
    monkeypatch.setattr(serp,'request_page',fake)
    job=svc.start_job(conn,[1,2],1,'run',launch=False)
    svc.run_job(job['job_id'],svc.keywords(conn,[1,2]),1,'secret')
    assert svc.history(conn,1)[0]['status']=='error'
    assert svc.history(conn,2)[0]['position']==2
    assert svc.usage(conn)['month_used']==2 and svc.usage(conn)['reserved']==0
    assert conn.execute('SELECT count(*) FROM api_usage_log').fetchone()[0]==2


def add_check(conn,position,status='ok',stamp='2026-09-30T12:00:00-07:00',depth=50):
    conn.execute('''INSERT INTO rank_checks(keyword_id,checked_at,check_date,position,ranking_url,source,status,profile,checked_depth)
        VALUES (1,?,'2026-09-30',?,'https://vapely.ca/p','serpapi',?,?,?)''',(stamp,position,status,serp.PROFILE_JSON,depth))
    conn.commit()


def test_change_math_and_unverified_gaps(database):
    conn,_=database
    add_check(conn,10,stamp='2026-09-29T12:00:00-07:00')
    add_check(conn,3)
    k=next(k for k in svc.list_rankings(conn)['items'] if k['id']==1)
    assert k['change']==7
    add_check(conn,None,'error','2026-10-01T12:00:00-07:00')
    k=next(k for k in svc.list_rankings(conn)['items'] if k['id']==1)
    assert k['change'] is None


def test_target_mismatch_and_new_rank(database):
    conn,_=database
    svc.save_keyword(conn,'vape shop canada','https://vapely.ca/expected')
    add_check(conn,1)
    k=next(k for k in svc.list_rankings(conn)['items'] if k['id']==1)
    assert k['target_mismatch'] and k['movement']=='new'


def test_csv_idempotent_and_not_promoted(database,tmp_path):
    conn,_=database
    path=tmp_path/'baseline.csv'
    path.write_text('date,term,vapely_position,vapely_url,top1_domain,top2_domain,top3_domain,pages_checked,searches_used,source,checked_at_pt\n2026-09-29,allo canada,7,https://vapely.ca/p?srsltid=x,a.ca,b.ca,c.ca,2,2,serpapi google.ca Toronto desktop,2026-09-29T20:39:49-07:00\n2026-09-29,abt vape,>50,,a.ca,b.ca,c.ca,5,5,serpapi google.ca Toronto desktop,2026-09-29T20:40:10-07:00\n')
    assert svc.import_baseline(conn,path)==2
    assert svc.import_baseline(conn,path)==0
    row=conn.execute("SELECT * FROM rank_checks WHERE reported_position=7").fetchone()
    assert row['position'] is None and row['status']=='unverified'
    assert row['ranking_url']=='https://vapely.ca/p'
    assert conn.execute('SELECT count(*) FROM rank_checks WHERE position IS NULL').fetchone()[0]==2


def test_api_contract_and_no_paid_call_without_check(database,monkeypatch):
    conn,connect=database
    from backend.app.routers import rankings
    from contextlib import contextmanager
    @contextmanager
    def db():
        c=connect()
        try:yield c
        finally:c.close()
    monkeypatch.setattr(rankings,'db_conn',db)
    app=FastAPI();app.include_router(rankings.router)
    client=TestClient(app)
    assert len(client.get('/api/rankings').json()['data']['items'])==16
    res=client.post('/api/rankings/keywords',json={'term':'new keyword'})
    key=res.json()['data']['id']
    assert client.get(f'/api/rankings/{key}/history').json()['data']==[]
    assert client.delete(f'/api/rankings/keywords/{key}').status_code==200
    assert client.post('/api/rankings/estimate',json={'max_pages':0}).status_code==422
    assert client.post('/api/rankings/estimate',json={'keyword_ids':[]}).status_code==400
    job = svc.start_job(conn, [1], 1, 'api-stop', launch=False)['job_id']
    assert client.post(f'/api/rankings/jobs/{job}/stop').json()['data']['status'] == 'stopping'
    assert client.get('/api/rankings').json()['data']['job']['cancel_requested'] == 1
    assert client.post('/api/rankings/jobs/missing/stop').status_code == 400
    svc.recover_jobs(conn)
    conn.execute("UPDATE service_settings SET value='0' WHERE key='serpapi_rank_monthly_budget'");conn.commit()
    assert client.post('/api/rankings/check',json={'request_key':'blocked'}).status_code==409


def test_budget_month_rollover_and_count_before_dispatch(database, monkeypatch):
    conn,_=database
    monkeypatch.setattr(svc,'now',lambda:'2026-09-30T23:59:59-07:00')
    job=svc.start_job(conn,[1],1,'cross-month',launch=False)
    svc.record_request(job['job_id'],1)
    assert svc.usage(conn)['month_used']==1
    monkeypatch.setattr(svc,'now',lambda:'2026-10-01T00:00:01-07:00')
    assert svc.usage(conn)['month_used']==0
    svc.record_request(job['job_id'],1)
    assert svc.usage(conn)['month_used']==1
    with pytest.raises(serp.RankError,match='reservation'):
        svc.record_request(job['job_id'],1)
    assert conn.execute('SELECT count(*) FROM rank_requests').fetchone()[0]==2


def test_credit_lookup_fails_closed_and_masks_errors(monkeypatch):
    monkeypatch.setattr(serp.requests,'get',Mock(side_effect=requests.ConnectionError('url?api_key=secret')))
    with pytest.raises(serp.RankError) as err:serp.remaining_credits('secret')
    assert 'secret' not in str(err.value)
    response=Mock();response.json.return_value={'plan_searches_left':200,'extra_credits':50}
    monkeypatch.setattr(serp.requests,'get',Mock(return_value=response))
    assert serp.remaining_credits('secret')==250


def test_api_weekly_calls_are_idempotent(database, monkeypatch):
    from contextlib import contextmanager
    from backend.app.routers import rankings
    conn,connect=database
    @contextmanager
    def db():
        c=connect()
        try:yield c
        finally:c.close()
    monkeypatch.setattr(rankings,'db_conn',db)
    monkeypatch.setattr(svc.threading,'Thread',Mock())
    app=FastAPI();app.include_router(rankings.router)
    client=TestClient(app)
    first=client.post('/api/rankings/weekly-run',json={}).json()['data']
    second=client.post('/api/rankings/weekly-run',json={}).json()['data']
    assert first['job_id']==second['job_id']
    assert second['skipped']
    assert conn.execute('SELECT count(*) FROM rank_jobs').fetchone()[0]==1


def test_short_pages_are_successful_without_claiming_full_coverage(monkeypatch):
    payloads = pages()
    payloads[1]['organic_results'] = payloads[1]['organic_results'][:8]
    result, http = check(monkeypatch, payloads)
    assert result['status'] == 'ok' and result['error'] is None
    assert result['position'] is None and not result['coverage_complete']
    assert result['pages_checked'] == 5 and http.call_count == 5
    assert result['checked_depth'] == 10  # only the uninterrupted complete range


def test_stop_before_start_preserves_history_and_blocks_new_job(database, monkeypatch):
    conn, _ = database
    add_check(conn, 3)
    job = svc.start_job(conn, [1, 2], 5, 'stop-before', launch=False)['job_id']
    http = Mock(side_effect=AssertionError('Must not spend a request'))
    monkeypatch.setattr(serp.requests, 'get', http)
    assert svc.stop_job(conn, job)['status'] == 'stopping'
    assert svc.stop_job(conn, job)['status'] == 'stopping'
    assert svc.usage(conn)['reserved'] == 20
    with pytest.raises(serp.RankError, match='already running'):
        svc.start_job(conn, [1], 1, 'too-soon', launch=False)
    svc.run_job(job, svc.keywords(conn, [1, 2]), 5, 'secret')
    assert not http.called
    assert svc.history(conn, 1)[0]['position'] == 3
    assert svc.history(conn, 2) == []
    assert svc.stop_job(conn, job)['status'] == 'cancelled'
    assert svc.usage(conn)['reserved'] == svc.usage(conn)['month_used'] == 0
    new = svc.start_job(conn, [1], 1, 'next', launch=False)['job_id']
    svc.stop_job(conn, job)  # stale tabs cannot cancel the new job
    assert conn.execute('SELECT cancel_requested FROM rank_jobs WHERE id=?', (new,)).fetchone()[0] == 0
    with pytest.raises(serp.RankError, match='not found'):
        svc.stop_job(conn, 'missing')


@pytest.mark.parametrize('hit', [False, True])
def test_stop_during_inflight_preserves_response_and_usage(database, monkeypatch, hit):
    from threading import Event, Thread
    conn, _ = database
    sent, release = Event(), Event()
    payload = pages()[0]
    if hit:
        payload['organic_results'][2]['link'] = 'https://vapely.ca/p'
    def search(*args, **kwargs):
        sent.set()
        assert release.wait(5)
        response = Mock(status_code=200)
        response.json.return_value = payload
        return response
    http = Mock(side_effect=search)
    monkeypatch.setattr(serp.requests, 'get', http)
    job = svc.start_job(conn, [1], 5, 'inflight', launch=False)['job_id']
    thread = Thread(target=svc.run_job, args=(job, svc.keywords(conn, [1]), 5, 'secret'))
    thread.start()
    try:
        assert sent.wait(5)
        assert svc.stop_job(conn, job)['status'] == 'stopping'
        assert svc.usage(conn)['reserved'] == 9
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and http.call_count == 1
    row = svc.history(conn, 1)[0]
    assert row['searches_used'] == 1 and row['pages_checked'] == 1
    assert row['position'] == (3 if hit else None)
    assert row['cancelled'] == (0 if hit else 1)
    assert row['status'] == ('ok' if hit else 'error')
    assert svc.usage(conn)['month_used'] == 1 and svc.usage(conn)['reserved'] == 0
    assert conn.execute('SELECT status FROM rank_jobs WHERE id=?', (job,)).fetchone()[0] == 'cancelled'


def test_stop_blocks_retry_and_queued_terms(database, monkeypatch):
    conn, connect = database
    job = svc.start_job(conn, None, 5, 'retry-stop', launch=False)['job_id']
    # Cancel atomically from the first dispatched request; other workers may have
    # already reserved requests, but neither retries nor later terms can dispatch.
    def search(*args, **kwargs):
        c = connect()
        try:
            svc.stop_job(c, job)
        finally:
            c.close()
        raise requests.Timeout()
    http = Mock(side_effect=search)
    monkeypatch.setattr(serp.requests, 'get', http)
    monkeypatch.setattr(serp.time, 'sleep', lambda _: None)
    svc.run_job(job, svc.keywords(conn), 5, 'secret')
    assert 1 <= http.call_count <= 4
    checks = conn.execute('SELECT * FROM rank_checks WHERE job_id=?', (job,)).fetchall()
    assert len(checks) == http.call_count
    assert all(c['cancelled'] and c['searches_used'] == 1 for c in checks)
    assert svc.usage(conn)['month_used'] == http.call_count
    assert svc.usage(conn)['reserved'] == 0


def test_restart_retains_stop_request(database):
    conn, _ = database
    job = svc.start_job(conn, [1, 2], 5, 'restart-stop', launch=False)['job_id']
    svc.record_request(job, 1)
    svc.stop_job(conn, job)
    svc.recover_jobs(conn)
    assert svc.history(conn, 1)[0]['cancelled'] == 1
    assert svc.history(conn, 2) == []
    assert svc.usage(conn)['reserved'] == 0 and svc.usage(conn)['month_used'] == 1
    assert conn.execute('SELECT status FROM rank_jobs WHERE id=?', (job,)).fetchone()[0] == 'cancelled'


@pytest.mark.parametrize('real_error', [False, True])
def test_migration_repairs_only_completed_short_page_errors(database, real_error):
    conn, _ = database
    job = svc.start_job(conn, [1, 2], 5, 'historical', launch=False)['job_id']
    svc.record_request(job, 1)
    short = 'Incomplete organic results pages; absence from the top range is unverified.'
    for keyword_id, error, checked in [(1, short, 5), (2, 'SerpApi returned HTTP 401.' if real_error else short, 5)]:
        conn.execute('''INSERT INTO rank_checks(keyword_id,job_id,checked_at,check_date,pages_checked,source,status,error,profile)
            VALUES (?,?,'2026-09-30','2026-09-30',?,'serpapi','error',?,?)''',
            (keyword_id, job, checked, error, serp.PROFILE_JSON))
    conn.execute("UPDATE rank_jobs SET status='error',reserved=0,error='2 keyword checks failed. See history.' WHERE id=?", (job,))
    conn.commit()
    before = svc.usage(conn)
    ensure_schema(conn)
    ensure_schema(conn)  # idempotent
    first = svc.history(conn, 1)[0]
    assert first['status'] == 'ok' and first['position'] is None
    assert first['coverage_complete'] == 0 and first['pages_checked'] == 5
    second = svc.history(conn, 2)[0]
    assert second['status'] == ('error' if real_error else 'ok')
    result = conn.execute('SELECT status,error FROM rank_jobs WHERE id=?', (job,)).fetchone()
    assert result['status'] == ('error' if real_error else 'complete')
    assert result['error'] == ('1 keyword checks failed. See history.' if real_error else None)
    assert svc.usage(conn) == before
