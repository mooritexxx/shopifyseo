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


@pytest.mark.parametrize('manager', ['salar', 'chief_of_staff'])
def test_managers_can_edit_finish_reopen_and_drop_any_task(api, manager):
    task = create(api, owner='merchandiser')
    task = value(update(api, task, endpoint='', actor=manager, title='New scope', outcome='New outcome',
                        priority='P0', due_on='2026-10-10', check_by='2026-10-11',
                        measurement={'result': 'Changed by manager'}, authorization='Existing OK',
                        links=[{'kind':'url','url':'https://example.com'}], blocked_reason='Waiting'))
    assert task['measurement']['result'] == 'Changed by manager'
    task = value(update(api, task, actor=manager, status='in_progress'))
    assert update(api, task, actor=manager, status='done', proof='  ').status_code == 422
    task = value(update(api, task, actor=manager, status='done', proof='Verified report'))
    assert task['status'] == 'done' and task['completed_at']
    task = value(update(api, task, endpoint='', actor=manager, title='Edited after completion', proof='New proof'))
    task = value(update(api, task, actor=manager, status='blocked'))
    assert not task['proof'] and task['completed_at'] is None
    assert update(api, task, actor=manager, status='dropped').status_code == 422
    task = value(update(api, task, actor=manager, status='dropped', note='No longer needed'))
    assert task['status'] == 'dropped'
    assert value(update(api, task, actor=manager, status='in_progress'))['status'] == 'in_progress'


@pytest.mark.parametrize('actor', ['jimmy', 'merchandiser', 'blogger', 'social', 'price_analyst', 'code_improver'])
def test_agents_own_tasks_and_notes_only(api, actor):
    task = create(api, actor=actor, owner=actor)
    task = value(update(api, task, endpoint='', actor=actor, title='Edited by owner', priority='P0',
                        authorization='Standing instruction', measurement={'result':'Verified'}))
    assert task['priority'] == 'P0'
    task = value(update(api, task, actor=actor, status='in_progress'))
    task = value(update(api, task, actor=actor, status='done', proof='Checks passed'))
    assert task['status'] == 'done'
    other = create(api, owner='salar')
    assert update(api, other, endpoint='', actor=actor, title='No').status_code == 403
    assert update(api, other, actor=actor, status='done', proof='No').status_code == 403
    assert update(api, task, endpoint='decision', actor=actor, approved=True, note='No').status_code == 403
    assert update(api, task, actor=actor, status='dropped', note='No').status_code == 403
    other = value(update(api, other, endpoint='notes', actor=actor, note='I can help'))
    assert value(api[0]('GET', f"/{other['id']}/events"))['items'][0]['actor'] == actor


@pytest.mark.parametrize('risk', ['spending', 'external_send', 'deletion', 'live_prices'])
@pytest.mark.parametrize('manager', ['salar', 'chief_of_staff'])
def test_risky_tasks_require_manager_approval(api, risk, manager):
    task = create(api, owner='merchandiser', risks=[risk])
    assert task['status'] == 'waiting_on_salar' and 'requires_review' not in task
    for actor in ['merchandiser', 'salar', 'chief_of_staff']:
        for status in ['in_progress', 'done']:
            response = update(api, task, actor=actor, status=status, proof='Evidence')
            assert response.status_code == 409
            assert 'manager must approve' in response.text
    if manager == 'chief_of_staff':
        assert update(api, task, endpoint='decision', actor=manager, approved=True).status_code == 422
        assert update(api, task, endpoint='decision', actor=manager, approved=True, note='  ').status_code == 422
    task = value(update(api, task, endpoint='decision', actor=manager, approved=True, note='Salar approved in chat on Oct 2'))
    assert task['approval_status'] == 'approved' and task['approval_by'] == manager and task['approval_at']
    event = value(api[0]('GET', f"/{task['id']}/events"))['items'][0]
    assert event['actor_label'] == ('Chief of Staff (for Salar)' if manager == 'chief_of_staff' else 'Salar')
    task = value(update(api, task, actor=manager, status='done', proof='URL checked'))
    assert task['status'] == 'done'


def test_decline_and_approve_are_available_on_any_task(api):
    task = create(api, actor='chief_of_staff', owner='chief_of_staff', risks=['spending'])
    task = value(update(api, task, endpoint='decision', actor='chief_of_staff', approved=False, note='Budget declined'))
    assert task['approval_status'] == 'declined' and task['approval_by'] == 'chief_of_staff'
    assert update(api, task, actor='chief_of_staff', status='in_progress').status_code == 409
    task = value(update(api, task, endpoint='decision', actor='chief_of_staff', approved=True, answer='Salar said OK in our chat'))
    task = value(update(api, task, actor='chief_of_staff', status='done', proof='Receipt saved'))
    task = value(update(api, task, endpoint='decision', actor='salar', approved=False))
    assert task['approval_status'] == 'declined' and task['status'] == 'todo'
    assert task['completed_at'] is None
    regular = create(api)
    assert value(update(api, regular, endpoint='decision', actor='salar', approved=True))['approval_status'] == 'approved'


def test_risk_changes_need_fresh_approval_but_normal_edits_do_not(api):
    task = create(api, risks=['live_prices'])
    task = value(update(api, task, endpoint='decision', actor='salar', approved=True))
    task = value(update(api, task, endpoint='', title='Edited title', check_by='2026-10-10'))
    assert task['approval_status'] == 'approved'
    task = value(update(api, task, endpoint='', risks=['live_prices', 'spending']))
    assert task['approval_status'] == 'pending' and task['approval_by'] is None
    assert update(api, task, status='in_progress').status_code == 409


def test_removed_review_contract_is_rejected(api):
    assert api[0]('POST', json={'title':'x','outcome':'y','owner':'jimmy','requires_review':False}).status_code == 422
    task = create(api)
    assert update(api, task, endpoint='', actor='salar', requires_review=False).status_code == 422
    assert update(api, task, status='review', proof='Evidence').status_code == 422
    assert api[0]('GET', '?status=review').status_code == 422
    assert update(api, task, endpoint='review', actor='salar', approve=True, note='Legacy').status_code == 404


def test_manager_can_answer_question_and_agent_can_resume_non_risky_work(api):
    task = create(api)
    task = value(update(api, task, status='waiting_on_salar', question='Which page?', options=['A','B']))
    task = value(update(api, task, endpoint='decision', actor='chief_of_staff', answer='A'))
    assert task['latest_decision']['question'] == 'Which page?' and task['status'] == 'todo'
    task = value(update(api, task, status='waiting_on_salar'))
    assert value(update(api, task, status='in_progress'))['status'] == 'in_progress'


@pytest.mark.parametrize('actor', ['jimmy','salar','chief_of_staff'])
def test_proof_dependencies_and_done_edits_are_enforced_for_every_role(api, actor):
    dependency = create(api)
    task = create(api, blocked_by=[dependency['id']])
    assert update(api, task, actor=actor, status='done').status_code == 422
    assert update(api, task, actor=actor, status='done', proof='Evidence').status_code == 409
    dependency = value(update(api, dependency, status='done', proof='Verified'))
    task = value(update(api, task, actor=actor, status='done', proof='Evidence'))
    assert update(api, task, actor=actor, endpoint='', proof=' ').status_code == 422
    unfinished = create(api)
    assert update(api, task, actor=actor, endpoint='', blocked_by=[unfinished['id']]).status_code == 409


@pytest.mark.parametrize('proof,expected', [('Report verified','done'), ('','todo'), ('   ','todo')])
def test_review_migration_is_atomic_audited_and_idempotent(api, proof, expected):
    task = create(api)
    legacy = {**task, 'status':'review', 'requires_review':True, 'proof':proof}
    with api[1]() as conn:
        conn.execute("UPDATE team_tasks SET status='review',data_json=? WHERE id=?", (json.dumps(legacy), task['id']))
    with api[1]() as conn:
        old_events = [tuple(row) for row in conn.execute('SELECT * FROM team_task_events')]
        service.ensure_schema(conn)
        migrated = service.get_task(conn, task['id'])
        assert migrated['status'] == expected and 'requires_review' not in migrated
        assert bool(migrated['completed_at']) == (expected == 'done')
        assert migrated['version'] == task['version'] + 1
        assert conn.execute("SELECT COUNT(*) FROM team_tasks WHERE status='review'").fetchone()[0] == 0
        assert [tuple(row) for row in conn.execute('SELECT * FROM team_task_events ORDER BY id')][:-1] == old_events
        service.ensure_schema(conn)
        assert service.get_task(conn, task['id']) == migrated
        assert service.events(conn, task['id'])['total'] == 2
    assert update(api, task, endpoint='notes', note='Stale version').status_code == 409


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
