"""Team tasks: atomic versioned writes and an immutable event history."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from backend.app.services.task_identity import MANAGERS


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
    conn.commit()


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
    changes = {key: {'before': before.get(key) if before else None, 'after': value}
               for key, value in task.items() if key not in ('version', 'last_log_at') and (before is None or before.get(key) != value)}
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
    require(task['requires_review'] is None or actor in MANAGERS, 'Only Chief of Staff or Salar can set requires_review')
    task['requires_review'] = bool(task['risks']) or bool(task['requires_review'])
    task.update(requester=actor, status='todo', proof='', question='', options=[], latest_decision=None,
                approval_status='not_required', completed_at=None, created_at=now())
    if task['risks']:
        request_approval(task)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        cursor = conn.execute("INSERT INTO team_tasks(owner,status,priority,version,created_at,last_log_at,data_json) VALUES(?,?,?,0,?,?,'{}')",
                              (task['owner'], task['status'], task['priority'], task['created_at'], task['created_at']))
        task['id'] = cursor.lastrowid
        validate_references(conn, task)
        return persist(conn, None, task, actor, 'created', 'Task created')


def mutate(conn, task_id, actor, payload, action):
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        before = get_task(conn, task_id)
        require(before['version'] == payload.version, 'Task changed. Reload it and retry with the current version.', 409)
        task = json.loads(json.dumps(before))
        manager, owner = actor in MANAGERS, actor == task['owner']
        kind, note = action, getattr(payload, 'note', '')
        if action == 'note':
            pass
        elif action == 'patch':
            edits = payload.model_dump(mode='json', exclude_unset=True, exclude={'version', 'note'})
            require(bool(edits), 'No fields supplied', 422)
            require(owner or manager, 'Only the owner or a manager can edit this task')
            controlled = {'owner', 'priority', 'requires_review', 'risks', 'authorization', 'title', 'outcome'}
            changed_keys = {key for key, value in edits.items() if task.get(key) != value}
            require(manager or not controlled.intersection(changed_keys), 'Only Chief of Staff or Salar can change assignment, scope, priority or review policy')
            require(owner or not (changed_keys - controlled), 'Only the owner can change task progress')
            require(task['status'] not in ('done', 'dropped', 'review'), 'Reopen or return the task before editing it', 409)
            task.update(edits)
            require(not task['risks'] or task['requires_review'], 'Consequential tasks require review', 422)
            if task['risks'] and any(before.get(key) != task[key] for key in ('risks', 'authorization', 'title', 'outcome', 'owner', 'links')):
                request_approval(task)
            elif not task['risks']:
                task['approval_status'] = 'not_required'
            validate_references(conn, task)
        elif action == 'status':
            target = payload.status
            if target == 'dropped':
                require(manager, 'Only Chief of Staff or Salar can drop tasks')
                require(bool(note.strip()), 'Dropping a task requires a reason', 422)
            else:
                require(owner, 'Only the owner can change task status')
            require(target != task['status'], 'Task already has that status', 409)
            require(task['status'] != 'review' or target == 'dropped', 'Use the review endpoint to approve or return work', 409)
            if task['status'] in ('done', 'dropped'):
                require(target == 'todo', 'Reopen completed or dropped tasks as todo', 409)
                task.update(proof='', completed_at=None)
                if task['risks']:
                    request_approval(task)
                    target = 'waiting_on_salar'
            if before['status'] == 'waiting_on_salar':
                require(target in ('blocked', 'dropped'), 'Salar must answer the pending question before work resumes', 409)
            if task['risks'] and target in ('in_progress', 'review', 'done'):
                require(task['approval_status'] == 'approved', 'Salar must approve this action before work proceeds', 409)
            if target == 'blocked':
                require(bool(note.strip()) or bool(task['blocked_by']), 'Provide a blocked reason or dependency', 422)
                task['blocked_reason'] = note.strip() or task['blocked_reason']
            if target == 'waiting_on_salar':
                if task['risks'] and task['approval_status'] != 'approved':
                    request_approval(task)
                else:
                    require(bool(payload.question.strip()) and len(payload.options) >= 2, 'A question and at least two options are required', 422)
                    task.update(question=payload.question.strip(), options=payload.options)
            if target in ('done', 'review'):
                require(bool(payload.proof.strip()), 'Completion requires a proof note', 422)
                require(all(get_task(conn, dep)['status'] == 'done' for dep in task['blocked_by']), 'Dependencies must be done first', 409)
                task['proof'] = payload.proof.strip()
                target = 'review' if task['requires_review'] or target == 'review' else 'done'
            task['status'] = target
            if target == 'done':
                task['completed_at'] = now()
            if target != 'waiting_on_salar':
                task.update(question='', options=[])
        elif action == 'decision':
            require(actor == 'salar', 'Only Salar can answer these questions')
            require(task['status'] == 'waiting_on_salar', 'Task is not waiting for Salar', 409)
            consequential = task['risks'] and task['approval_status'] != 'approved'
            require(not consequential or payload.approved is not None, 'Explicit approval or rejection is required', 422)
            decision = {'actor': actor, 'at': now(), 'answer': payload.answer, 'question': task['question'], 'approved': payload.approved}
            task.update(latest_decision=decision, status='todo', question='', options=[])
            if consequential:
                task['approval_status'] = 'approved' if payload.approved else 'denied'
            note = payload.answer
        elif action == 'review':
            require(manager and (task['owner'] != 'chief_of_staff' or actor == 'salar'), 'Chief of Staff cannot approve or review their own work')
            require(task['status'] == 'review', 'Task is not awaiting review', 409)
            require(bool(task['proof'].strip()), 'Completion requires proof', 422)
            if payload.approve:
                require(all(get_task(conn, dep)['status'] == 'done' for dep in task['blocked_by']), 'Dependencies must be done first', 409)
            task['status'] = 'done' if payload.approve else 'todo'
            task['completed_at'] = now() if payload.approve else None
            kind = 'approved' if payload.approve else 'changes_requested'
        else:
            raise ValueError('Unknown task action')
        return persist(conn, before, task, actor, kind, note)
