"""Keyword CRUD, rank jobs, reservations and history. No Shopify writes."""
import csv
import hashlib
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.app.db import open_db_connection
from shopifyseo.rank_tracking.serp import (PROFILE, PROFILE_JSON, RankCancelled, RankError, check_term,
                                          clean_url, is_target, remaining_credits, url_identity)

TZ = ZoneInfo('America/Vancouver')


def now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def setting(conn, name, default=''):
    row = conn.execute('SELECT value FROM service_settings WHERE key=?', (name,)).fetchone()
    return str(row[0]) if row else default


def usage(conn):
    month = now()[:7]
    used = conn.execute('SELECT count(*) FROM rank_requests WHERE month=?', (month,)).fetchone()[0]
    used += conn.execute("SELECT coalesce(sum(searches_used),0) FROM rank_checks WHERE import_key IS NOT NULL AND substr(check_date,1,7)=?", (month,)).fetchone()[0]
    reserved = conn.execute("SELECT coalesce(sum(reserved),0) FROM rank_jobs WHERE status='running'").fetchone()[0]
    return dict(month_used=used, reserved=reserved,
                monthly_budget=int(setting(conn, 'serpapi_rank_monthly_budget', '250')))


def keywords(conn, ids=None):
    if ids is not None and not ids:
        raise RankError('Select at least one keyword.')
    query = 'SELECT id,term,target_url,grp FROM tracked_keywords WHERE active=1'
    args = []
    if ids is not None:
        ids = list(dict.fromkeys(ids))
        query += ' AND id IN (' + ','.join('?' for _ in ids) + ')'
        args = ids
    rows = [dict(r) for r in conn.execute(query + ' ORDER BY term', args)]
    if not rows or (ids is not None and len(rows) != len(ids)):
        raise RankError('Select active keywords to check; the list may have changed.')
    return rows


def save_keyword(conn, term, target_url=None, grp=None):
    term = ' '.join(term.lower().split())
    if not term or len(term) > 200:
        raise RankError('Keyword must contain 1–200 characters.')
    target_url = (target_url or '').strip()
    if target_url and (not clean_url(target_url) or not is_target(target_url)):
        raise RankError('Target URL must be an http(s) page on vapely.ca.')
    conn.execute('''INSERT INTO tracked_keywords(term,target_url,grp) VALUES (?,?,?)
        ON CONFLICT(term) DO UPDATE SET target_url=excluded.target_url, grp=excluded.grp,
        active=1, updated_at=CURRENT_TIMESTAMP''', (term, clean_url(target_url) or None, grp or None))
    conn.commit()
    return dict(conn.execute('SELECT * FROM tracked_keywords WHERE term=?', (term,)).fetchone())


def remove_keyword(conn, keyword_id):
    cur = conn.execute('UPDATE tracked_keywords SET active=0, updated_at=CURRENT_TIMESTAMP WHERE id=?', (keyword_id,))
    conn.commit()
    if not cur.rowcount:
        raise RankError('Keyword not found.')
    return {'removed': True}


def history(conn, keyword_id):
    return [dict(r) for r in conn.execute('SELECT * FROM rank_checks WHERE keyword_id=? ORDER BY checked_at DESC,id DESC', (keyword_id,))]


def list_rankings(conn):
    items = []
    for row in conn.execute('SELECT id,term,target_url,grp FROM tracked_keywords WHERE active=1 ORDER BY term'):
        item = dict(row)
        checks = [dict(r) for r in conn.execute('SELECT * FROM rank_checks WHERE keyword_id=? AND profile=? ORDER BY checked_at DESC,id DESC LIMIT 12', (row['id'], PROFILE_JSON))]
        current = checks[0] if checks else None
        previous = checks[1] if len(checks) > 1 else None
        change = None
        movement = 'new' if current and current['status'] == 'ok' and not previous else None
        if current and previous and current['status'] == previous['status'] == 'ok':
            a, b = current['position'], previous['position']
            if a is not None and b is not None:
                change = b - a
            elif a is not None and previous['checked_depth'] >= a:
                movement = 'entered'
            elif b is not None and current['checked_depth'] >= b:
                movement = 'left'
        item.update(latest=current, previous_position=previous['position'] if previous else None,
                    change=change, movement=movement, trend=list(reversed(checks)),
                    target_mismatch=bool(current and current['status'] == 'ok' and current['ranking_url'] and row['target_url'] and url_identity(current['ranking_url']) != url_identity(row['target_url'])),
                    top_competitor=next((current[k] for k in ('top1_domain','top2_domain','top3_domain') if current[k] and current[k] != PROFILE['domain'] and not current[k].endswith('.'+PROFILE['domain'])), None) if current else None)
        items.append(item)
    job = conn.execute('SELECT * FROM rank_jobs ORDER BY created_at DESC,rowid DESC LIMIT 1').fetchone()
    return dict(items=items, profile=PROFILE, **usage(conn), job=dict(job) if job else None)


def estimate(conn, ids, max_pages):
    terms = keywords(conn, ids)
    if not 1 <= max_pages <= 5:
        raise RankError('Check depth must be 1–5 pages.')
    remaining = remaining_credits(setting(conn, 'serpapi_api_key'))
    u = usage(conn)
    base = len(terms) * max_pages
    worst = base * 2  # one retry for every page; includes uncertain timeout charges
    reason = None
    if u['reserved'] or conn.execute("SELECT 1 FROM rank_jobs WHERE status='running'").fetchone():
        reason = 'A ranking check is already running.'
    elif u['month_used'] + u['reserved'] + worst > u['monthly_budget']:
        reason = 'This check exceeds the monthly ranking budget. Select fewer keywords or a shallower depth, or increase the budget in Settings.'
    elif worst > remaining:
        reason = 'SerpApi has insufficient remaining credits for this check including retries.'
    return dict(**u, keyword_ids=[t['id'] for t in terms], searches_base=base,
                searches_worst_case=worst, serpapi_remaining=remaining,
                allowed=reason is None, reason=reason, max_pages=max_pages)


def start_job(conn, ids, max_pages, request_key, weekly=False, launch=True):
    weekly_date = now()[:10] if weekly else None
    duplicate = conn.execute('SELECT * FROM rank_jobs WHERE request_key=? OR weekly_date=?', (request_key, weekly_date)).fetchone()
    if duplicate:
        return dict(job_id=duplicate['id'], status=duplicate['status'], skipped=True)
    details = estimate(conn, ids, max_pages)
    conn.execute('BEGIN IMMEDIATE')
    try:
        # Recheck inside the write lock: two tabs/schedulers cannot reserve the same budget.
        duplicate = conn.execute('SELECT * FROM rank_jobs WHERE request_key=? OR weekly_date=?', (request_key, weekly_date)).fetchone()
        if duplicate:
            conn.rollback()
            return dict(job_id=duplicate['id'], status=duplicate['status'], skipped=True)
        terms = keywords(conn, details['keyword_ids'])
        u = usage(conn)
        if not details['allowed']:
            raise RankError(details['reason'])
        if conn.execute("SELECT 1 FROM rank_jobs WHERE status='running'").fetchone():
            raise RankError('A ranking check is already running.')
        if u['month_used'] + u['reserved'] + details['searches_worst_case'] > u['monthly_budget']:
            raise RankError('Monthly budget changed; request a new estimate.')
        job_id = str(uuid.uuid4())
        conn.execute('''INSERT INTO rank_jobs(id,request_key,weekly_date,status,keyword_ids,max_pages,reserved,created_at)
            VALUES (?,?,?,'running',?,?,?,?)''', (job_id, request_key, weekly_date, json.dumps([t['id'] for t in terms]), max_pages, details['searches_worst_case'], now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if launch:
        try:
            threading.Thread(target=run_job, args=(job_id, terms, max_pages, setting(conn, 'serpapi_api_key')),
                             daemon=True, name='rank-check').start()
        except Exception:
            conn.execute("UPDATE rank_jobs SET status='error',reserved=0,error='Unable to start worker',finished_at=? WHERE id=?", (now(), job_id))
            conn.commit()
            raise RankError('Unable to start ranking worker.') from None
    return dict(job_id=job_id, status='running', skipped=False)


def stop_job(conn, job_id):
    # Keep the running reservation until in-flight requests have settled.
    conn.execute('BEGIN IMMEDIATE')
    try:
        job = conn.execute('SELECT status FROM rank_jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise RankError('Ranking job not found.')
        if job['status'] == 'running':
            conn.execute('UPDATE rank_jobs SET cancel_requested=1 WHERE id=?', (job_id,))
        conn.commit()
        return dict(job_id=job_id, status='stopping' if job['status'] == 'running' else job['status'])
    except Exception:
        conn.rollback()
        raise


def record_request(job_id, keyword_id):
    conn = open_db_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        job = conn.execute('SELECT status,reserved,cancel_requested FROM rank_jobs WHERE id=?', (job_id,)).fetchone()
        if job and job['cancel_requested']:
            raise RankCancelled('Check stopped by user.')
        if not job or job['status'] != 'running' or job['reserved'] < 1:
            raise RankError('Ranking job no longer has a request reservation.')
        current_usage = usage(conn)
        if current_usage['month_used'] >= current_usage['monthly_budget']:
            raise RankError('Monthly ranking budget reached.')
        stamp = now()
        conn.execute('INSERT INTO rank_requests(job_id,keyword_id,requested_at,month) VALUES (?,?,?,?)', (job_id, keyword_id, stamp, stamp[:7]))
        conn.execute('UPDATE rank_jobs SET reserved=reserved-1 WHERE id=?', (job_id,))
        # Use the existing API Usage ledger; cost is unknown under the subscription.
        conn.execute('''INSERT INTO api_usage_log(provider,model,call_type,stage,input_tokens,output_tokens,total_tokens,estimated_cost_usd)
            VALUES ('serpapi','google','rank_check','rank_tracking',0,0,0,0)''')
        conn.commit()
    finally:
        conn.close()


def run_job(job_id, terms, max_pages, key):
    def worker(term):
        result = check_term(term['term'], max_pages, key, lambda: record_request(job_id, term['id']))
        conn = open_db_connection()
        try:
            stamp = now()
            count = conn.execute('SELECT count(*) FROM rank_requests WHERE job_id=? AND keyword_id=?', (job_id,term['id'])).fetchone()[0]
            if result['cancelled'] and count == 0:
                return  # Unstarted terms retain their previous snapshot.
            conn.execute('''INSERT INTO rank_checks(keyword_id,job_id,checked_at,check_date,position,ranking_url,
                top1_domain,top2_domain,top3_domain,pages_checked,searches_used,checked_depth,source,status,error,profile,target_url,
                coverage_complete,cancelled) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (term['id'],job_id,stamp,stamp[:10],result['position'],result['ranking_url'],result['top1_domain'],result['top2_domain'],result['top3_domain'],result['pages_checked'],count,result['checked_depth'],'serpapi',result['status'],result['error'],PROFILE_JSON,term['target_url'],result['coverage_complete'],result['cancelled']))
            conn.execute('UPDATE rank_jobs SET completed=completed+1 WHERE id=?', (job_id,))
            conn.commit()
        finally:
            conn.close()
    error = None
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(worker, terms))
    except Exception:
        error = 'Ranking worker interrupted. Unfinished keywords have unknown results.'
    conn = open_db_connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        cancelled = conn.execute('SELECT cancel_requested FROM rank_jobs WHERE id=?', (job_id,)).fetchone()[0]
        # Also create unknown outcomes for keywords lost to a worker failure.
        for term in terms:
            if not conn.execute('SELECT 1 FROM rank_checks WHERE job_id=? AND keyword_id=?', (job_id,term['id'])).fetchone():
                insert_interrupted(conn,job_id,term['id'],cancelled=cancelled)
        failed = conn.execute("SELECT count(*) FROM rank_checks WHERE job_id=? AND status='error'", (job_id,)).fetchone()[0]
        conn.execute('UPDATE rank_jobs SET status=?,reserved=0,finished_at=?,error=? WHERE id=?',
                     ('cancelled' if cancelled else ('error' if error or failed else 'complete'), now(),
                      None if cancelled else error or (f'{failed} keyword checks failed. See history.' if failed else None), job_id))
        conn.commit()
    finally:
        conn.close()


def insert_interrupted(conn, job_id, keyword_id, cancelled=False):
    stamp = now()
    count = conn.execute('SELECT count(*) FROM rank_requests WHERE job_id=? AND keyword_id=?', (job_id,keyword_id)).fetchone()[0]
    if cancelled and not count:
        return
    message = 'Check stopped by user; rank is unknown.' if cancelled else 'Check interrupted; rank is unknown.'
    conn.execute('''INSERT OR IGNORE INTO rank_checks(keyword_id,job_id,checked_at,check_date,searches_used,source,status,error,profile,cancelled)
        VALUES (?,?,?,?,?,'serpapi','error',?,?,?)''', (keyword_id,job_id,stamp,stamp[:10],count,message,PROFILE_JSON,cancelled))


def recover_jobs(conn):
    """Single-process app startup: preserve dispatched usage, release unused reservations."""
    for job in conn.execute("SELECT id,keyword_ids,cancel_requested FROM rank_jobs WHERE status='running'").fetchall():
        for keyword_id in json.loads(job['keyword_ids']):
            insert_interrupted(conn,job['id'],keyword_id,cancelled=job['cancel_requested'])
        conn.execute('UPDATE rank_jobs SET status=?,reserved=0,finished_at=?,error=? WHERE id=?',
                     ('cancelled' if job['cancel_requested'] else 'error', now(),
                      None if job['cancel_requested'] else 'App restarted during ranking check.', job['id']))
    conn.commit()


def import_baseline(conn, path):
    """Import evidence without promoting legacy script results to verified ranks."""
    count = 0
    with open(path, newline='', encoding='utf-8-sig') as fh:
        for row in csv.DictReader(fh):
            if not row.get('source','').startswith('serpapi google.ca Toronto desktop'):
                raise RankError('Only the supplied SerpApi baseline format is supported; GSC is not a rank.')
            term = ' '.join(row['term'].lower().split())
            conn.execute('INSERT OR IGNORE INTO tracked_keywords(term) VALUES (?)', (term,))
            keyword_id = conn.execute('SELECT id FROM tracked_keywords WHERE term=?', (term,)).fetchone()[0]
            stamp = datetime.fromisoformat(row['checked_at_pt']).astimezone(TZ).isoformat(timespec='seconds')
            identity = hashlib.sha256(f"legacy-serpapi:{term}:{stamp}".encode()).hexdigest()
            reported = None if row['vapely_position'] == '>50' else int(row['vapely_position'])
            cur = conn.execute('''INSERT OR IGNORE INTO rank_checks(keyword_id,checked_at,check_date,reported_position,
                ranking_url,top1_domain,top2_domain,top3_domain,pages_checked,searches_used,checked_depth,
                source,status,error,profile,import_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,'legacy_csv','unverified',?,?,?)''',
                (keyword_id,stamp,stamp[:10],reported,clean_url(row['vapely_url']) or None,
                 row['top1_domain'],row['top2_domain'],row['top3_domain'],int(row['pages_checked']),int(row['searches_used']),int(row['pages_checked'])*10,
                 'Legacy script counted collected results; ranking and coverage need a fresh check.',PROFILE_JSON,identity))
            count += cur.rowcount
    conn.commit()
    return count
