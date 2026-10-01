"""Authorization, decision/review policy, atomic history and concurrency contracts."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.services import team_tasks as service
from backend.app.services.task_identity import ACTORS


@pytest.fixture
def api(tmp_path, monkeypatch):
    path = tmp_path / 'team.db'
    def connect():
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    conn = connect()
    service.ensure_schema(conn)
    conn.execute('CREATE TABLE seo_opportunity_tasks(id INTEGER PRIMARY KEY)')
    conn.execute('INSERT INTO seo_opportunity_tasks VALUES(99)')
    conn.commit()
    conn.close()
    directory = tmp_path / 'tokens'
    directory.mkdir()
    for actor in ACTORS:
        (directory / f'{actor}.token').write_text(actor + '-secret-' + 'x' * 40)
    monkeypatch.setenv('TASK_MANAGER_TOKEN_DIR', str(directory))
    monkeypatch.setattr('backend.app.db.open_db_connection', connect)
    client = TestClient(app)
    def request(method, endpoint='', actor='jimmy', **kwargs):
        return client.request(method, '/api/tasks' + endpoint, headers={'X-Task-Token': actor + '-secret-' + 'x' * 40}, **kwargs)
    return request, connect, client


def create(api, actor='jimmy', **fields):
    response = api[0]('POST', actor=actor, json={'title': 'Improve product', 'outcome': 'Changes verified', 'owner': 'jimmy', **fields})
    assert response.status_code == 201, response.text
    return response.json()['data']


def update(api, task, endpoint='status', actor='jimmy', **payload):
    return api[0]('POST' if endpoint else 'PATCH', f"/{task['id']}" + (f'/{endpoint}' if endpoint else ''), actor=actor, json={'version': task['version'], **payload})


def value(response):
    assert response.status_code == 200, response.text
    return response.json()['data']


@pytest.mark.parametrize('actor', list(ACTORS))
def test_identity_and_attribution(api, actor):
    assert value(api[0]('GET', '/actors', actor))['current'] == actor
    task = create(api, actor)
    assert task['requester'] == actor
    log = value(api[0]('GET', f"/{task['id']}/events"))['items'][0]
    assert log['actor'] == actor and log['kind'] == 'created'


def test_no_unauthenticated_or_spoofed_access(api):
    assert api[2].get('/api/tasks').status_code == 401
    assert api[2].post('/api/tasks', json={}).status_code == 401
    assert api[0]('GET', actor='intruder').status_code == 401
    assert api[0]('POST', json={'title': 'x', 'outcome': 'y', 'owner': 'jimmy', 'actor': 'salar'}).status_code == 422


def test_progress_permissions_and_manager_controls(api):
    task = create(api, owner='blogger')
    assert update(api, task, status='in_progress').status_code == 403
    assert update(api, task, actor='chief_of_staff', status='in_progress').status_code == 403
    task = value(update(api, task, endpoint='notes', note='I can help'))
    assert update(api, task, endpoint='', owner='jimmy').status_code == 403
    assert update(api, task, endpoint='', actor='chief_of_staff', measurement={'result': 'Changed by manager'}).status_code == 403
    assert update(api, task, endpoint='', actor='blogger', priority='P0').status_code == 403
    task = value(update(api, task, endpoint='', actor='chief_of_staff', owner='jimmy', priority='P0', requires_review=True))
    assert task['priority'] == 'P0' and task['owner'] == 'jimmy'
    task = value(update(api, task, status='in_progress'))
    assert task['status'] == 'in_progress'
    assert update(api, task, status='dropped', note='cancel').status_code == 403
    assert update(api, task, actor='salar', status='dropped').status_code == 422
    assert value(update(api, task, actor='salar', status='dropped', note='No longer needed'))['status'] == 'dropped'


def test_proof_and_review_cannot_be_bypassed(api):
    task = create(api, actor='salar', requires_review=True)
    assert update(api, task, status='done', proof='  ').status_code == 422
    task = value(update(api, task, status='done', proof='PR #123, checks passed'))
    assert task['status'] == 'review' and task['completed_at'] is None
    assert update(api, task, status='done', proof='PR #123').status_code == 409
    assert update(api, task, endpoint='', actor='salar', requires_review=False).status_code == 409
    assert update(api, task, endpoint='review', approve=True, note='fine').status_code == 403
    task = value(update(api, task, endpoint='review', actor='chief_of_staff', approve=True, note='Verified PR'))
    assert task['status'] == 'done' and task['completed_at']
    assert value(api[0]('GET', f"/{task['id']}/events"))['items'][0]['actor'] == 'chief_of_staff'


def test_chief_cannot_review_own_work(api):
    task = create(api, actor='chief_of_staff', owner='chief_of_staff', requires_review=True)
    task = value(update(api, task, actor='chief_of_staff', status='done', proof='Report saved'))
    assert update(api, task, endpoint='review', actor='chief_of_staff', approve=True, note='done').status_code == 403
    task = value(update(api, task, endpoint='review', actor='salar', approve=False, note='Please check totals'))
    assert task['status'] == 'todo'
    task = value(update(api, task, actor='chief_of_staff', status='done', proof='Totals checked'))
    assert value(update(api, task, endpoint='review', actor='salar', approve=True, note='Verified'))['status'] == 'done'


def test_regular_completion_and_reopen(api):
    task = create(api, authorization='Salar requested this SEO edit')
    assert task['approval_status'] == 'not_required'
    task = value(update(api, task, status='done', proof='Product URL returns HTTP 200'))
    assert task['status'] == 'done'
    task = value(update(api, task, status='todo'))
    assert not task['proof'] and task['completed_at'] is None


def test_requires_review_flag_is_manager_only(api):
    assert api[0]('POST', json={'title': 'x', 'outcome': 'y', 'owner': 'jimmy', 'requires_review': False}).status_code == 403
    task = create(api)
    assert update(api, task, endpoint='', requires_review=True).status_code == 403


def test_salar_decision_is_durable(api):
    task = create(api)
    assert update(api, task, status='waiting_on_salar', question='Which page?').status_code == 422
    task = value(update(api, task, status='waiting_on_salar', question='Which page?', options=['A', 'B']))
    assert update(api, task, status='in_progress').status_code == 409
    assert update(api, task, endpoint='decision', actor='chief_of_staff', answer='A').status_code == 403
    task = value(update(api, task, endpoint='decision', actor='salar', answer='A'))
    assert task['status'] == 'todo' and task['latest_decision']['answer'] == 'A'
    assert task['latest_decision']['question'] == 'Which page?'
    assert value(api[0]('GET', f"/{task['id']}/events"))['items'][0]['kind'] == 'decision'


@pytest.mark.parametrize('risk', ['spending', 'external_send', 'deletion', 'live_prices'])
def test_consequential_actions_require_salar_first(api, risk):
    task = create(api, risks=[risk])
    assert task['requires_review'] and task['status'] == 'waiting_on_salar'
    assert update(api, task, status='done', proof='done').status_code == 409
    assert update(api, task, endpoint='decision', actor='salar', answer='okay').status_code == 422
    task = value(update(api, task, endpoint='decision', actor='salar', answer='No', approved=False))
    assert task['status'] == 'todo' and task['approval_status'] == 'denied'
    assert update(api, task, status='in_progress').status_code == 409
    task = value(update(api, task, status='waiting_on_salar'))
    task = value(update(api, task, endpoint='decision', actor='salar', answer='Approved', approved=True))
    task = value(update(api, task, status='in_progress'))
    task = value(update(api, task, status='done', proof='Evidence'))
    assert task['status'] == 'review'


def test_approval_invalidated_when_scope_changes(api):
    task = create(api, risks=['live_prices'])
    task = value(update(api, task, endpoint='decision', actor='salar', answer='Approved', approved=True))
    task = value(update(api, task, endpoint='', actor='chief_of_staff', outcome='Change another price'))
    assert task['approval_status'] == 'pending' and task['status'] == 'waiting_on_salar'
    assert update(api, task, endpoint='', actor='salar', requires_review=False).status_code == 422


def test_version_conflict_and_atomic_history(api):
    task = create(api)
    changed = value(update(api, task, endpoint='notes', actor='social', note='New evidence'))
    assert changed['version'] == task['version'] + 1
    assert update(api, task, status='in_progress').status_code == 409
    assert value(api[0]('GET', f"/{task['id']}/events"))['total'] == 2
    assert value(api[0]('GET', f"/{task['id']}"))['status'] == 'todo'


def test_concurrent_writes_only_one_succeeds(api):
    task = create(api)
    def write(note):
        return update(api, task, endpoint='notes', note=note).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(write, ['one', 'two']))
    assert sorted(statuses) == [200, 409]
    assert value(api[0]('GET', f"/{task['id']}/events"))['total'] == 2


def test_dependencies_links_and_rollback(api):
    first = create(api)
    second = create(api, blocked_by=[first['id']], links=[{'kind': 'opportunity_task', 'id': '99'}, {'kind': 'product', 'handle': 'example'}])
    assert update(api, first, endpoint='', blocked_by=[second['id']]).status_code == 422
    assert update(api, second, status='done', proof='Done').status_code == 409
    first = value(update(api, first, status='done', proof='verified'))
    assert value(update(api, second, status='done', proof='verified'))['status'] == 'done'
    assert api[0]('POST', json={'title': 'x', 'outcome': 'y', 'owner': 'jimmy', 'blocked_by': [999]}).status_code == 404
    assert value(api[0]('GET'))['total'] == 2
    assert value(api[0]('GET', '/events'))['total'] == 4


def test_invalid_inputs_and_unsafe_links(api):
    task = create(api)
    for fields in ({'owner': None}, {'outcome': ''}, {'risks': None}, {'unknown': 1}):
        assert update(api, task, endpoint='', actor='salar', **fields).status_code == 422
    assert update(api, task, endpoint='', links=[{'kind': 'url', 'url': 'javascript:alert(1)'}]).status_code == 422
    assert api[0]('GET', '?owner=unknown').status_code == 422
    assert api[0]('GET', '?limit=501').status_code == 422
    assert api[0]('GET', '/events?since=2026-01-01T00:00:00').status_code == 422


def test_stale_done_and_filtered_views(api):
    task = create(api)
    task = value(update(api, task, status='in_progress'))
    conn = api[1]()
    old = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat(timespec='microseconds')
    conn.execute('UPDATE team_tasks SET last_log_at=? WHERE id=?', (old, task['id']))
    conn.commit(); conn.close()
    assert value(api[0]('GET', '?stale=true'))['total'] == 1
    assert value(api[0]('GET', '?stale=true&owner=blogger'))['total'] == 0
    task = value(update(api, task, endpoint='notes', actor='blogger', note='Checked today'))
    assert value(api[0]('GET', '?stale=true'))['total'] == 0
    task = value(update(api, task, status='done', proof='Checked'))
    assert value(api[0]('GET', '?done_this_week=true'))['total'] == 1
    create(api, owner='blogger')
    assert value(api[0]('GET', '?owner=blogger&status=todo'))['total'] == 1
    assert len(value(api[0]('GET', '?limit=1&offset=1'))['items']) == 1
    assert value(api[0]('GET', '/events?since=2000-01-01T00:00:00Z&limit=1'))['total'] == 5


def test_history_is_append_only_even_at_database_layer(api):
    create(api)
    conn = api[1]()
    for sql in ('DELETE FROM team_task_events', "UPDATE team_task_events SET actor='salar'"):
        with pytest.raises(sqlite3.IntegrityError, match='append-only'):
            conn.execute(sql)
        conn.rollback()
    conn.close()


WEB_HEADERS = {'Sec-Fetch-Site': 'same-origin', 'Sec-Fetch-Mode': 'cors', 'X-Task-Web': '1', 'Origin': 'http://testserver'}


def test_web_salar_access_without_token_and_agent_api_stays_protected(api):
    client = api[2]
    response = client.get('/api/web/tasks/actors', headers=WEB_HEADERS)
    assert value(response)['current'] == 'salar'
    assert client.get('/api/tasks', headers=WEB_HEADERS).status_code == 401
    task = create(api, risks=['spending'])
    response = client.post(f"/api/web/tasks/{task['id']}/decision", headers=WEB_HEADERS,
                           json={'version': task['version'], 'answer': 'Approved from the web', 'approved': True})
    updated = value(response)
    assert updated['latest_decision']['actor'] == 'salar'
    assert updated['approval_status'] == 'approved'
    history = value(api[0]('GET', f"/{task['id']}/events"))
    assert history['items'][0]['actor'] == 'salar'


@pytest.mark.parametrize('headers', [
    {}, {**WEB_HEADERS, 'Sec-Fetch-Site': 'cross-site'},
    {**WEB_HEADERS, 'Sec-Fetch-Site': 'same-site'},
    {**WEB_HEADERS, 'Sec-Fetch-Mode': 'navigate'},
    {**WEB_HEADERS, 'Origin': 'https://unrelated.example'},
    {**WEB_HEADERS, 'Origin': 'null'},
    {**WEB_HEADERS, 'X-Task-Web': '0'},
])
def test_web_rejects_cross_site_or_missing_browser_headers(api, headers):
    assert api[2].get('/api/web/tasks', headers=headers).status_code == 403
    assert api[2].post('/api/web/tasks', headers=headers, json={'title': 'x', 'outcome': 'y', 'owner': 'salar'}).status_code == 403


def test_web_trusted_tailscale_origin_and_token_cannot_select_another_actor(api):
    headers = {**WEB_HEADERS, 'Host': 'vapely-seo-box-1.tail8bbcdb.ts.net',
               'Origin': 'https://vapely-seo-box-1.tail8bbcdb.ts.net',
               'X-Task-Token': 'jimmy-secret-' + 'x' * 40}
    assert value(api[2].get('/api/web/tasks/actors', headers=headers))['current'] == 'salar'
