"""Team tasks: atomic versioned writes and an immutable event history."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from backend.app.services.task_identity import ACTORS, MANAGERS


def ensure_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS team_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL, status TEXT NOT NULL, priority TEXT NOT NULL,
            version INTEGER NOT NULL, created_at TEXT NOT NULL,
            last_log_at TEXT NOT NULL, completed_at TEXT, data_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS team_tasks_owner_status ON team_tasks(owner, status);
        CREATE INDEX IF NOT EXISTS team_tasks_stale ON team_tasks(status, last_log_at);
        CREATE INDEX IF NOT EXISTS team_tasks_completed ON team_tasks(completed_at);
        CREATE TABLE IF NOT EXISTS team_task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
            actor TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
            version INTEGER NOT NULL, note TEXT NOT NULL, changes_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS team_task_events_task ON team_task_events(task_id, id);
        CREATE INDEX IF NOT EXISTS team_task_events_time ON team_task_events(at, id);
        CREATE TRIGGER IF NOT EXISTS team_events_no_update BEFORE UPDATE ON team_task_events
        BEGIN SELECT RAISE(ABORT, 'Task history is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS team_events_no_delete BEFORE DELETE ON team_task_events
        BEGIN SELECT RAISE(ABORT, 'Task history is append-only'); END;
    ''')
    # Idempotent, atomic conversion of the retired completion-review workflow.
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        for row in conn.execute('SELECT data_json FROM team_tasks').fetchall():
            before = json.loads(row[0])
            if 'requires_review' not in before and before['status'] != 'review' and before.get('approval_status') != 'denied':
                continue
            task = dict(before)
            task.pop('requires_review', None)
            if task['status'] == 'review':
                task['status'] = 'done' if str(task.get('proof') or '').strip() else 'todo'
                task['completed_at'] = (task.get('completed_at') or now()) if task['status'] == 'done' else None
            if task.get('approval_status') == 'denied':
                task['approval_status'] = 'declined'
            decision = task.get('latest_decision') or {}
            task['approval_by'] = decision.get('actor') if decision.get('approved') is not None else None
            task['approval_at'] = decision.get('at') if decision.get('approved') is not None else None
            persist(conn, before, task, 'system', 'workflow_migrated',
                    'Removed completion review; legacy review tasks with proof are done, otherwise todo.')


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def get_task(conn, task_id):
    row = conn.execute('SELECT data_json FROM team_tasks WHERE id=?', (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, 'Task not found')
    return json.loads(row[0])


def list_tasks(conn, owner=None, status=None, stale=False, done_this_week=False, limit=100, offset=0):
    clauses, params = [], []
    for key, value in (('owner', owner), ('status', status)):
        if value:
            clauses.append(f'{key}=?')
            params.append(value)
    if stale:
        clauses.append("status='in_progress' AND last_log_at<=?")
        params.append((datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec='microseconds'))
    if done_this_week:
        local = datetime.now(ZoneInfo('America/Vancouver'))
        monday = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        clauses.append("status='done' AND completed_at>=?")
        params.append(monday.astimezone(timezone.utc).isoformat(timespec='microseconds'))
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = conn.execute('SELECT COUNT(*) FROM team_tasks' + where, params).fetchone()[0]
    rows = conn.execute('SELECT data_json FROM team_tasks' + where + ' ORDER BY priority, id DESC LIMIT ? OFFSET ?', [*params, limit, offset])
    return {'items': [json.loads(row[0]) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


def events(conn, task_id=None, since=None, limit=100, offset=0):
    clauses, params = [], []
    if task_id is not None:
        get_task(conn, task_id)
        clauses.append('task_id=?')
        params.append(task_id)
    if since is not None:
        clauses.append('at>=?')
        params.append(since.astimezone(timezone.utc).isoformat(timespec='microseconds'))
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = conn.execute('SELECT COUNT(*) FROM team_task_events' + where, params).fetchone()[0]
    rows = conn.execute('SELECT id,task_id,actor,kind,at,version,note,changes_json FROM team_task_events' + where + ' ORDER BY id DESC LIMIT ? OFFSET ?', [*params, limit, offset])
    items = []
    for row in rows:
        item = dict(zip(('id', 'task_id', 'actor', 'kind', 'at', 'version', 'note', 'changes_json'), row))
        item['changes'] = json.loads(item.pop('changes_json'))
        decision = (item['changes'].get('latest_decision') or {}).get('after') or {}
        item['actor_label'] = (decision.get('actor_label') if decision.get('actor') == item['actor'] else None) or ACTORS.get(item['actor'], 'System migration')
        items.append(item)
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


def require(condition, message, code=403):
    if not condition:
        raise HTTPException(code, message)


def validate_references(conn, task):
    dependencies = task['blocked_by']
    require(len(set(dependencies)) == len(dependencies), 'Duplicate dependencies', 422)
    for dependency in dependencies:
        require(dependency != task['id'], 'A task cannot depend on itself', 422)
        get_task(conn, dependency)
        # Follow only the dependency graph, never scan catalog data.
        pending, seen = [dependency], set()
        while pending:
            current = pending.pop()
            require(current != task['id'], 'Task dependencies cannot form a cycle', 422)
            if current not in seen:
                seen.add(current)
                pending.extend(get_task(conn, current)['blocked_by'])
    for link in task['links']:
        if link['kind'] == 'task':
            get_task(conn, int(link['id']))
        elif link['kind'] == 'opportunity_task':
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seo_opportunity_tasks'").fetchone()
            require(exists and conn.execute('SELECT 1 FROM seo_opportunity_tasks WHERE id=?', (int(link['id']),)).fetchone(), 'Opportunity task not found', 422)


def persist(conn, before, task, actor, kind, note):
    timestamp = now()
    task['version'] = (before['version'] if before else 0) + 1
    task['last_log_at'] = timestamp
    changes = {key: {'before': (before or {}).get(key), 'after': task.get(key)}
               for key in sorted(set(task) | set(before or {}))
               if key not in ('version', 'last_log_at') and (before is None or before.get(key) != task.get(key))}
    conn.execute('UPDATE team_tasks SET owner=?,status=?,priority=?,version=?,last_log_at=?,completed_at=?,data_json=? WHERE id=?',
                 (task['owner'], task['status'], task['priority'], task['version'], timestamp, task['completed_at'], json.dumps(task), task['id']))
    conn.execute('INSERT INTO team_task_events(task_id,actor,kind,at,version,note,changes_json) VALUES(?,?,?,?,?,?,?)',
                 (task['id'], actor, kind, timestamp, task['version'], note, json.dumps(changes)))
    return task


def request_approval(task):
    task.update(status='waiting_on_salar', approval_status='pending',
                question='Approve this consequential action: ' + task['outcome'],
                options=['Approve', 'Decline'])


def create(conn, actor, payload):
    task = payload.model_dump(mode='json')
    task.update(requester=actor, status='todo', proof='', question='', options=[], latest_decision=None,
                approval_status='not_required', approval_by=None, approval_at=None, completed_at=None, created_at=now())
    if task['risks']:
        request_approval(task)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        cursor = conn.execute("INSERT INTO team_tasks(owner,status,priority,version,created_at,last_log_at,data_json) VALUES(?,?,?,0,?,?,'{}')",
                              (task['owner'], task['status'], task['priority'], task['created_at'], task['created_at']))
        task['id'] = cursor.lastrowid
        validate_references(conn, task)
        return persist(conn, None, task, actor, 'created', 'Task created')


def validate_completion(conn, task):
    require(bool(task['proof'].strip()), 'Completion requires a proof note', 422)
    require(all(get_task(conn, dep)['status'] == 'done' for dep in task['blocked_by']),
            'Dependencies must be done first', 409)


def require_risk_approval(task):
    if task['risks'] and task['status'] in ('in_progress', 'done'):
        require(task['approval_status'] == 'approved',
                'A manager must approve this risky task before it can move to in_progress or done.', 409)


def mutate(conn, task_id, actor, payload, action):
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        before = get_task(conn, task_id)
        require(before['version'] == payload.version, 'Task changed. Reload it and retry with the current version.', 409)
        task = json.loads(json.dumps(before))
        manager, owner = actor in MANAGERS, actor == task['owner']
        kind, note = action, getattr(payload, 'note', '').strip()
        if action == 'note':
            pass
        elif action == 'patch':
            require(owner or manager, 'Only the owner or a manager can edit this task')
            edits = payload.model_dump(mode='json', exclude_unset=True, exclude={'version', 'note'})
            require(bool(edits), 'No fields supplied', 422)
            task.update(edits)
            if before['risks'] != task['risks']:
                task.update(approval_by=None, approval_at=None)
                if task['risks']:
                    request_approval(task)
                    task['completed_at'] = None
                else:
                    task['approval_status'] = 'not_required'
            validate_references(conn, task)
            require_risk_approval(task)
            if task['status'] == 'done':
                validate_completion(conn, task)
        elif action == 'status':
            target = payload.status
            require(manager or owner, 'Only the owner or a manager can change task status')
            if target == 'dropped':
                require(manager, 'Only managers can drop tasks')
                require(bool(note), 'Dropping a task requires a reason', 422)
            require(target != task['status'], 'Task already has that status', 409)
            task['status'] = target
            require_risk_approval(task)
            if target == 'done':
                task['proof'] = payload.proof.strip()
                validate_completion(conn, task)
                task['completed_at'] = now()
            else:
                task['completed_at'] = None
                if before['status'] == 'done':
                    task['proof'] = ''
            if target == 'blocked':
                task['blocked_reason'] = note or task['blocked_reason']
            if target == 'waiting_on_salar':
                if task['risks'] and task['approval_status'] != 'approved':
                    request_approval(task)
                else:
                    task.update(question=payload.question.strip(), options=payload.options)
            else:
                task.update(question='', options=[])
        elif action == 'decision':
            require(manager, 'Only managers can approve, decline or answer task decisions')
            answer = payload.answer.strip()
            require(payload.approved is not None or bool(answer), 'Provide an approval choice or an answer', 422)
            require(not task['risks'] or payload.approved is not None, 'Explicit approval or decline is required for risky tasks', 422)
            if actor == 'chief_of_staff' and payload.approved is True:
                require(bool(note or answer), 'Chief of Staff approval requires a short note saying where Salar gave the OK.', 422)
            label = 'Chief of Staff (for Salar)' if actor == 'chief_of_staff' and payload.approved is True else ACTORS[actor]
            decision = {'actor': actor, 'actor_label': label, 'at': now(), 'answer': answer or note,
                        'note': note, 'question': task['question'], 'approved': payload.approved}
            task['latest_decision'] = decision
            if payload.approved is not None:
                task.update(approval_status='approved' if payload.approved else 'declined',
                            approval_by=actor, approval_at=decision['at'])
            # A declined risky task cannot remain executable or completed.
            if task['status'] == 'waiting_on_salar' or (task['risks'] and payload.approved is False and task['status'] in ('in_progress', 'done')):
                task.update(status='todo', completed_at=None)
            task.update(question='', options=[])
            note = note or answer or ('Approved' if payload.approved else 'Declined')
        else:
            raise ValueError('Unknown task action')
        return persist(conn, before, task, actor, kind, note)
