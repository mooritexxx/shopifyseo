"""One persistent, reviewable SEO fix per catalog page. Never publishes automatically."""
import json
import threading
from urllib.parse import quote

KINDS = {'product', 'collection', 'page', 'blog_article'}


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS seo_opportunity_tasks (
        id INTEGER PRIMARY KEY, object_type TEXT NOT NULL, object_handle TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'detected', evidence_json TEXT NOT NULL DEFAULT '{}',
        draft_json TEXT NOT NULL DEFAULT '{}', reviewed_json TEXT NOT NULL DEFAULT '{}',
        error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(object_type, object_handle))''')

    conn.execute('''CREATE TABLE IF NOT EXISTS seo_change_events (
        task_id INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')


def task_dict(row):
    t = dict(row)
    for key in ('evidence', 'draft', 'reviewed'):
        t[key] = json.loads(t.pop(key + '_json'))
    kind, handle = t['object_type'], t['object_handle']
    prefix = {'product': 'products', 'collection': 'collections', 'page': 'pages', 'blog_article': 'articles'}[kind]
    t['detail_url'] = '/' + prefix + '/' + quote(handle, safe='/' if kind == 'blog_article' else '')
    return t


def get_task(conn, task_id):
    row = conn.execute('SELECT * FROM seo_opportunity_tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        raise ValueError('Opportunity task not found')
    return task_dict(row)


def list_tasks(conn, kind=None, handle=None):
    if kind and handle:
        rows = conn.execute('SELECT * FROM seo_opportunity_tasks WHERE object_type=? AND object_handle=?', (kind, handle))
    else:
        # The inbox polls summaries; only the active editor loads potentially large bodies.
        rows = conn.execute("SELECT id,object_type,object_handle,status,evidence_json,'{}' AS draft_json,'{}' AS reviewed_json,error,updated_at FROM seo_opportunity_tasks ORDER BY updated_at DESC,id DESC")
    return [task_dict(r) for r in rows]


def prepare(conn, kind, handle, query):
    from backend.app.services.opportunities_service import _suggest_action
    if kind not in KINDS:
        raise ValueError('Unsupported page type')
    rows = [dict(r) for r in conn.execute('''SELECT query, clicks, impressions, ctr, position, fetched_at
        FROM gsc_query_rows WHERE object_type=? AND object_handle=? ORDER BY impressions DESC''', (kind, handle))]
    selected = next((r for r in rows if r['query'] == query), None)
    if not selected:
        raise ValueError('This query is no longer cached for the page. Refresh the inbox.')
    action = _suggest_action(selected['position'] or 0, selected['ctr'] or 0, selected['impressions'] or 0)
    evidence = {'primary_query': query, 'suggested_action': action, 'queries': [selected] + [r for r in rows if r['query'] != query][:19],
                'instruction': 'Address this opportunity on the existing page using confirmed facts. Preserve page intent; do not invent claims or force unrelated keywords.'}
    conn.execute('''INSERT INTO seo_opportunity_tasks(object_type,object_handle,evidence_json)
        VALUES(?,?,?) ON CONFLICT(object_type,object_handle) DO NOTHING''', (kind, handle, json.dumps(evidence)))
    conn.commit()
    row = conn.execute('SELECT id FROM seo_opportunity_tasks WHERE object_type=? AND object_handle=?', (kind, handle)).fetchone()
    return get_task(conn, row['id'])


def generate(conn, task_id):
    task = get_task(conn, task_id)
    # Atomic reservation prevents duplicate AI bills from repeated clicks/tabs.
    changed = conn.execute("UPDATE seo_opportunity_tasks SET status='preparing',error='',updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('detected','failed')", (task_id,)).rowcount
    conn.commit()
    if changed:
        thread = threading.Thread(target=_worker, args=(task_id,), daemon=True)
        thread.start()
    return get_task(conn, task_id)


def _worker(task_id):
    from backend.app.db import open_db_connection
    from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
    conn = open_db_connection()
    try:
        task = get_task(conn, task_id)
        fields = ['seo_title', 'seo_description']
        if 'content' in task['evidence']['suggested_action'].lower():
            fields.append('body')
        draft = {}
        for field in fields:
            result = generate_field_recommendation(conn, task['object_type'], task['object_handle'], field, draft,
                opportunity_context=task['evidence'])
            draft[field] = result['value']
        if 'body' in draft:
            draft['body_html'] = draft.pop('body')
        conn.execute("UPDATE seo_opportunity_tasks SET status='draft_ready',draft_json=?,error='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(draft), task_id))
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.execute("UPDATE seo_opportunity_tasks SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (str(exc), task_id))
        conn.commit()
    finally:
        conn.close()


def review(conn, task_id, fields):
    from .seo_quality import validate_metadata
    task = get_task(conn, task_id)
    if task['status'] not in ('draft_ready', 'reviewed'):
        raise ValueError('Prepare a draft before reviewing it')
    values = {key: str(fields.get(key, '')) for key in task['draft']}
    validate_metadata(task['object_type'], values)
    if 'body_html' in values and not values['body_html'].strip():
        raise ValueError('Reviewed content must not be empty')
    conn.execute("UPDATE seo_opportunity_tasks SET status='reviewed',reviewed_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(values), task_id))
    conn.commit()
    return get_task(conn, task_id)


def record_applied(conn, kind, handle, payload):
    row = conn.execute("SELECT id, reviewed_json FROM seo_opportunity_tasks WHERE object_type=? AND object_handle=? AND status='reviewed'", (kind, handle)).fetchone()
    if row:
        reviewed = json.loads(row['reviewed_json'])
        if reviewed and all(str(payload.get(k, '')).strip() == str(v).strip() for k, v in reviewed.items()):
            conn.execute("UPDATE seo_opportunity_tasks SET status='applied',updated_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
            conn.execute('INSERT INTO seo_change_events(task_id) VALUES(?) ON CONFLICT DO NOTHING', (row['id'],))
            conn.commit()


def monitor(conn, task_id):
    if get_task(conn, task_id)['status'] != 'applied':
        raise ValueError('Save the reviewed draft to Shopify before monitoring')
    conn.execute("UPDATE seo_opportunity_tasks SET status='monitoring',updated_at=CURRENT_TIMESTAMP WHERE id=?", (task_id,))
    conn.commit()
    return get_task(conn, task_id)


def recover(conn):
    conn.execute("UPDATE seo_opportunity_tasks SET status='failed',error='Draft preparation was interrupted. Retry to prepare it again.' WHERE status='preparing'")
    conn.commit()
