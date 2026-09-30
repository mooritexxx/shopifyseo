"""HTTP contracts, isolated from the real database and all external writes."""
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.routers import internal_links as router
from internal_links_support import BASE, OLD, Shopify, database
from shopifyseo.internal_links import shopify_io, ai_weave, pipeline


@pytest.fixture
def api(tmp_path,monkeypatch):
    path=tmp_path/'api.sqlite'; conn=database(path)
    def connect():
        c=sqlite3.connect(path); c.row_factory=sqlite3.Row; return c
    monkeypatch.setattr(router,'open_db_connection',connect)
    monkeypatch.setattr(router,'_base_url',lambda _:BASE)
    monkeypatch.setattr(pipeline,'generate_link_suggestions',Mock(return_value=0))
    live=Shopify()
    monkeypatch.setattr(shopify_io,'fetch_body',live.fetch)
    monkeypatch.setattr(shopify_io,'push_body',live.push)
    yield TestClient(app),conn,live
    conn.close()


def test_read_routes_and_dismiss_not_found(api):
    client,conn,live=api
    for route in ['summary','suggestions','orphans','settings','applied']:
        res=client.get('/api/internal-links/'+route)
        assert res.status_code==200 and res.json()['ok']
    assert client.post('/api/internal-links/suggestions/999999/dismiss').status_code==404
    live.push.assert_not_called()


def test_preview_confirm_and_undo_http(api):
    client,conn,live=api
    plan=client.post('/api/internal-links/suggestions/1/preview').json()['data']
    assert plan['allowed'] and plan['old_html']==OLD
    assert conn.execute('SELECT COUNT(*) FROM link_body_snapshots').fetchone()[0]==0
    assert client.post('/api/internal-links/suggestions/1/apply',json={}).status_code==409
    result=client.post('/api/internal-links/suggestions/1/apply',json={'preview_token':plan['preview_token']})
    assert result.status_code==200 and result.json()['data']['status']=='applied'
    applied=client.get('/api/internal-links/applied').json()['data'][0]
    assert applied['can_undo']
    assert client.post('/api/internal-links/suggestions/1/undo').status_code==200
    assert live.body==OLD


@pytest.mark.parametrize('revised', [
    '<p>Love ceramic tanks.</p><p>Rewritten unrelated sentence.</p>',
    '<p>Truncated.</p>',
])
def test_ai_rewrite_returns_409_and_diff_without_push(api,monkeypatch,revised):
    client,conn,live=api
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'"); conn.commit()
    monkeypatch.setattr(ai_weave,'_default_call_ai',lambda *_:{'revised_body':revised})
    result=client.post('/api/internal-links/suggestions/1/generate-anchor')
    assert result.status_code==409
    assert result.json()['error']['text_diff']
    assert conn.execute('SELECT ai_edit_json FROM link_suggestions').fetchone()[0] is None
    live.push.assert_not_called()


def test_legacy_ai_apply_returns_409_without_push(api):
    client,conn,live=api
    conn.execute("UPDATE link_suggestions SET kind='ai_woven',ai_anchor_html='<p>Rewrite</p>'");conn.commit()
    assert client.post('/api/internal-links/suggestions/1/apply',json={'preview_token':'legacy'}).status_code==409
    live.push.assert_not_called()


def test_live_change_after_preview_returns_409(api):
    client,conn,live=api
    plan=client.post('/api/internal-links/suggestions/1/preview').json()['data']
    live.body += '<p>New live work.</p>'
    assert client.post('/api/internal-links/suggestions/1/apply',json={'preview_token':plan['preview_token']}).status_code==409
    live.push.assert_not_called()


def test_ai_setting_defaults_and_empty_disable(api):
    client,conn,live=api
    assert client.get('/api/internal-links/settings').json()['data']['ai_woven_enabled_types']==['product','blog_article']
    assert client.put('/api/internal-links/settings?ai_woven_enabled_types=').status_code==200
    assert client.get('/api/internal-links/settings').json()['data']['ai_woven_enabled_types']==[]
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'");conn.commit()
    assert not client.post('/api/internal-links/suggestions/1/preview').json()['data']['allowed']
    assert client.post('/api/internal-links/suggestions/1/generate-anchor').status_code==409
    live.fetch.assert_not_called()


def test_pending_operation_survives_rebuild_and_dismiss_is_blocked(api):
    client,conn,live=api
    live.push.side_effect=TimeoutError()
    token=client.post('/api/internal-links/suggestions/1/preview').json()['data']['preview_token']
    assert client.post('/api/internal-links/suggestions/1/apply',json={'preview_token':token}).status_code==502
    assert client.post('/api/internal-links/suggestions/1/dismiss').status_code==409
    result=client.get('/api/internal-links/suggestions').json()['data'][0]
    assert result['pending_operation']=='needs_reconciliation'
    assert client.post('/api/internal-links/suggestions/1/reconcile').json()['data']['status']=='not_written'
