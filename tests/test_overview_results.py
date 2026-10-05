from datetime import date, timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest
from backend.app.main import app
from backend.app.services.overview_results import change_results
from shopifyseo import opportunity_tasks as tasks


def make_db(db_conn):
    conn = db_conn
    tasks.ensure_schema(conn)
    conn.execute('CREATE TABLE gsc_page_daily(date TEXT,object_type TEXT,object_handle TEXT,clicks INTEGER,impressions INTEGER)')
    conn.execute("INSERT INTO seo_opportunity_tasks(id,object_type,object_handle,status,reviewed_json) VALUES(1,'product','test','reviewed','{\"seo_title\":\"Approved\"}')")
    return conn


def populate(conn):
    conn.execute("UPDATE seo_opportunity_tasks SET status='applied'")
    conn.execute("INSERT INTO seo_change_events VALUES(1,'2026-09-01 12:00:00')")
    for offset in range(-14,15):
        day = date(2026,9,1)+timedelta(days=offset)
        conn.execute('INSERT INTO gsc_page_daily VALUES(?,?,?,?,?)',(day.isoformat(),'product','test',1 if offset<0 else 2,100 if offset<0 else 200))
    conn.commit()


def test_confirmed_save_records_immutable_date_and_preserves_task_contract(db_conn):
    conn = make_db(db_conn)
    before = set(tasks.get_task(conn,1))
    tasks.record_applied(conn,'product','test',{'seo_title':'Wrong'})
    assert conn.execute('SELECT COUNT(*) FROM seo_change_events').fetchone()[0]==0
    tasks.record_applied(conn,'product','test',{'seo_title':'Approved'})
    timestamp = conn.execute('SELECT applied_at FROM seo_change_events').fetchone()[0]
    tasks.monitor(conn,1)
    tasks.record_applied(conn,'product','test',{'seo_title':'Approved'})
    assert conn.execute('SELECT applied_at FROM seo_change_events').fetchone()[0]==timestamp
    assert set(tasks.get_task(conn,1))==before


def test_equal_windows_exclude_save_day_and_wait_for_lag(db_conn):
    conn=make_db(db_conn); populate(conn)
    assert change_results(conn,date(2026,9,17))['items'][0]['state']=='waiting'
    row=change_results(conn,date(2026,9,18))['items'][0]
    assert row['state']=='ready'
    assert row['before']['clicks']==14 and row['after']['clicks']==28
    assert float(row['before']['ctr']) == pytest.approx(0.01)
    assert float(row['after']['ctr']) == pytest.approx(0.01)
    conn.execute("DELETE FROM gsc_page_daily WHERE date='2026-09-03'")
    assert change_results(conn,date(2026,9,18))['items'][0]['state']=='insufficient'


def test_old_task_date_not_inferred_and_zero_impressions_not_comparable(db_conn):
    conn=make_db(db_conn)
    conn.execute("UPDATE seo_opportunity_tasks SET status='monitoring'")
    assert change_results(conn)['items'][0]['state']=='unknown_date'
    populate(conn)
    conn.execute('UPDATE gsc_page_daily SET impressions=0')
    assert change_results(conn,date(2026,9,18))['items'][0]['state']=='insufficient'


def test_http_retains_comparison_fields(db_conn):
    conn=make_db(db_conn); populate(conn)
    # Endpoint owns connection lifetime; return a proxy that preserves the fixture.
    class Connection:
        def execute(self,*args): return conn.execute(*args)
        def close(self): pass
    with patch('backend.app.db.open_db_connection',return_value=Connection()):
        response=TestClient(app).get('/api/overview/change-results')
    assert response.status_code==200
    item=response.json()['data']['items'][0]
    assert item['detail_url']=='/products/test'
    assert item['before']['days']==14 and item['applied_at']=='2026-09-01 12:00:00'


def test_application_date_uses_search_console_pacific_day(db_conn):
    conn=make_db(db_conn); populate(conn)
    conn.execute("UPDATE seo_change_events SET applied_at='2026-09-02 01:00:00'")
    item=change_results(conn,date(2026,9,18))['items'][0]
    assert item['applied_date']=='2026-09-01'
    assert item['before']['end']=='2026-08-31'
    assert item['after']['start']=='2026-09-02'
