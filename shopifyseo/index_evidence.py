"""URL Inspection evidence and Googlebot robots history (no inspection API calls).

Robots matching follows https://developers.google.com/crawling/docs/robots-txt/robots-txt-spec.
Snapshots describe observations, not proof of uninterrupted availability between fetches.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, quote
from zoneinfo import ZoneInfo

import requests

from .dashboard_status import index_status_info, index_status_bucket_from_strings

PT = ZoneInfo('America/Vancouver')
INDEX_FIELDS = (
    'index_status', 'index_coverage', 'google_canonical', 'index_last_fetched_at',
    'index_last_crawl_at', 'index_robots_state', 'index_page_fetch_state',
    'index_indexing_state', 'index_verdict',
)
INDEX_STORED_FIELDS = INDEX_FIELDS + ('index_flag', 'index_flag_reason')
TABLES = {'product': 'products', 'collection': 'collections', 'page': 'pages', 'blog_article': 'blog_articles'}
STALE_INSPECTION_DAYS = 7


def timestamp(value):
    if value is None or value == '':
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return float(value)
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def age_days(value, now=None):
    ts = timestamp(value)
    return max(0, int(((now if now is not None else time.time()) - ts) // 86400)) if ts is not None else None


def pt_date(value, *, short=False):
    ts = timestamp(value)
    if ts is None:
        return 'unknown'
    dt = datetime.fromtimestamp(ts, PT)
    return f'{dt:%b} {dt.day}' if short else f'{dt:%Y-%m-%d %H:%M} PT'


def extract_inspection_fields(payload, fetched_at=None):
    """Shared cache/catalog/history mapping; empty payloads are not fresh evidence."""
    payload = payload or {}
    idx = (payload.get('inspectionResult') or {}).get('indexStatusResult') or {}
    if not idx:
        return {}
    return dict(zip(INDEX_FIELDS, (
        index_status_info(payload)[0], idx.get('coverageState'), idx.get('googleCanonical'),
        fetched_at if fetched_at is not None else (payload.get('_cache') or {}).get('fetched_at'),
        idx.get('lastCrawlTime'), idx.get('robotsTxtState'), idx.get('pageFetchState'),
        idx.get('indexingState'), idx.get('verdict'),
    )))


def _robots_octets(value):
    # Decode unreserved percent escapes only; reserved escapes keep their meaning.
    value = re.sub(r'%([0-9a-fA-F]{2})', lambda m: chr(int(m[1], 16)) if chr(int(m[1], 16)) in
                   'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~' else '%' + m[1].upper(), value)
    return quote(value, safe="/%?=&:;,+!@[]()*$-._~'")


def _robots_path_matches(path, rule):
    """Wildcard prefix matching without an exponentially backtracking regex."""
    anchored = rule.endswith('$')
    pattern = rule[:-1] if anchored else rule
    i = j = 0
    star = -1
    retry = 0
    while i < len(path):
        if j == len(pattern) and not anchored:
            return True
        if j < len(pattern) and pattern[j] == '*':
            star, retry = j, i
            j += 1
        elif j < len(pattern) and pattern[j] == path[i]:
            i += 1
            j += 1
        elif star >= 0:
            retry += 1
            i, j = retry, star + 1
        else:
            return False
    return all(char == '*' for char in pattern[j:])


def robots_allows(body, url):
    """Combine Googlebot groups, otherwise *, then longest rule with Allow on ties."""
    groups = []
    agents, rules = [], []
    had_rule = False
    for line in body.lstrip('\ufeff').splitlines():
        field, sep, value = line.split('#', 1)[0].partition(':')
        if not sep:
            continue
        field, value = field.strip().lower(), value.strip()
        if field == 'user-agent':
            if had_rule:
                groups.append((agents, rules))
                agents, rules, had_rule = [], [], False
            token = re.match(r'[a-zA-Z_-]+|\*', value)
            agents.append(token[0].lower() if token else '')
        elif field in {'allow', 'disallow'} and agents:
            had_rule = True
            if value.startswith('/'):
                rules.append((field == 'allow', value))
    groups.append((agents, rules))
    selected = [r for a, rs in groups if 'googlebot' in a for r in rs]
    if not any('googlebot' in a for a, _ in groups):
        selected = [r for a, rs in groups if '*' in a for r in rs]
    parsed = urlsplit(url)
    path = _robots_octets((parsed.path or '/') + ('?' + parsed.query if parsed.query else ''))
    matches = []
    for allow, rule in selected:
        rule = _robots_octets(rule)
        if _robots_path_matches(path, rule):
            # Google's matcher uses the full matched rule length, including * and $.
            matches.append((len(rule.encode('utf-8')), allow))
    return max(matches)[1] if matches else True


def ensure_evidence_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS robots_snapshots (
        id INTEGER PRIMARY KEY, url TEXT NOT NULL, status_code INTEGER NOT NULL,
        byte_size INTEGER NOT NULL, sha256 TEXT NOT NULL, etag TEXT, body TEXT NOT NULL,
        first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS robots_snapshot_url ON robots_snapshots(url, id DESC)')
    columns = ', '.join(f'{key} {"INTEGER" if key == "index_last_fetched_at" else "TEXT"}' for key in INDEX_FIELDS)
    conn.execute(f'''CREATE TABLE IF NOT EXISTS index_status_history (
        url TEXT NOT NULL, object_type TEXT NOT NULL, handle TEXT NOT NULL, observed_date TEXT NOT NULL,
        {columns}, index_flag TEXT, robots_snapshot_id INTEGER,
        UNIQUE(url, observed_date))''')


def robots_url(url):
    parsed = urlsplit(url)
    return f'{parsed.scheme}://{parsed.netloc}/robots.txt'


def latest_snapshot(conn, url):
    row = conn.execute('SELECT * FROM robots_snapshots WHERE url=? ORDER BY id DESC LIMIT 1', (robots_url(url),)).fetchone()
    return dict(row) if row else None


def store_snapshot(conn, url, status_code, body, *, etag='', now=None, raw_body=None):
    now = int(now if now is not None else time.time())
    raw = raw_body if raw_body is not None else body.encode('utf-8')
    digest = hashlib.sha256(raw).hexdigest()
    latest = latest_snapshot(conn, url)
    if latest and (latest['status_code'], latest['sha256']) == (status_code, digest):
        conn.execute('UPDATE robots_snapshots SET last_seen_at=?, etag=? WHERE id=?', (now, etag, latest['id']))
        return latest['id']
    cursor = conn.execute('''INSERT INTO robots_snapshots
        (url,status_code,byte_size,sha256,etag,body,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?)''',
        (robots_url(url), status_code, len(raw), digest, etag, body, now, now))
    return cursor.lastrowid


def fetch_robots_snapshot(conn, storefront):
    url = robots_url(storefront)
    try:
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'}, timeout=10)
        snapshot_id = store_snapshot(conn, url, response.status_code, response.content.decode('utf-8', errors='replace'),
                                     etag=response.headers.get('ETag', ''), raw_body=response.content)
    except requests.RequestException:
        logging.getLogger(__name__).warning('robots.txt fetch failed for %s', url, exc_info=True)
        snapshot_id = store_snapshot(conn, url, 0, '')
    conn.commit()
    return snapshot_id


def derive_index_flag(fields, url, snapshot=None, *, crawl_snapshot=None, now=None):
    blocked = fields.get('index_robots_state') == 'DISALLOWED' or fields.get('index_page_fetch_state') == 'BLOCKED_ROBOTS_TXT'
    if blocked:
        if not snapshot or snapshot['status_code'] != 200:
            return '', ''
        if robots_allows(snapshot['body'], url):
            reason = (f"Google crawled on {pt_date(fields.get('index_last_crawl_at'))} and saw a robots.txt block. "
                      f"The current robots.txt (fetched {pt_date(snapshot['last_seen_at'])}) allows this URL.")
            if crawl_snapshot and crawl_snapshot['status_code'] == 200 and robots_allows(crawl_snapshot['body'], url):
                reason += " An allowing snapshot already existed at crawl time; Google's robots fetch at crawl time probably failed or differed."
            return 'stale_robots_block', reason
        return 'robots_block_current', 'P1 technical issue: Google reports a robots.txt block and the current robots.txt also disallows this URL.'
    age = age_days(fields.get('index_last_crawl_at'), now)
    bucket = index_status_bucket_from_strings(fields.get('index_status') or '', fields.get('index_coverage') or '')
    if (bucket == 'not_indexed' and age is not None and age > 21) or (age is None and (not fields.get('index_coverage') or 'unknown' in str(fields.get('index_coverage')).lower())):
        return 'stale_crawl', 'Google crawl evidence is older than 21 days or no crawl is available with unknown coverage.'
    return '', ''


def with_index_flag(conn, fields, url):
    if not fields:
        return fields
    snapshot = latest_snapshot(conn, url)
    crawl = timestamp(fields.get('index_last_crawl_at'))
    old = conn.execute('SELECT * FROM robots_snapshots WHERE url=? AND first_seen_at<=? ORDER BY id DESC LIMIT 1',
                       (robots_url(url), crawl)).fetchone() if crawl is not None else None
    flag, reason = derive_index_flag(fields, url, snapshot, crawl_snapshot=dict(old) if old else None)
    return {**fields, 'index_flag': flag, 'index_flag_reason': reason}


def write_inspection_history(conn, url, object_type, handle, payload, fetched_at):
    fields = with_index_flag(conn, extract_inspection_fields(payload, fetched_at), url)
    if not fields:
        return
    snapshot = latest_snapshot(conn, url)
    columns = ('url', 'object_type', 'handle', 'observed_date') + INDEX_FIELDS + ('index_flag', 'robots_snapshot_id')
    values = (url, object_type, handle, datetime.fromtimestamp(fetched_at, PT).date().isoformat(),
              *(fields[k] for k in INDEX_FIELDS), fields['index_flag'], snapshot['id'] if snapshot else None)
    conn.execute(f'''INSERT INTO index_status_history ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})
        ON CONFLICT(url, observed_date) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in columns[1:] if k != 'observed_date')}''', values)


def index_api_fields(row):
    row = dict(row)
    return {**{key: row.get(key) for key in INDEX_STORED_FIELDS if key not in {'index_status', 'index_coverage', 'google_canonical'}},
            'crawl_age_days': age_days(row.get('index_last_crawl_at')),
            'inspection_age_days': age_days(row.get('index_last_fetched_at')),
            'index_action_type': 'technical' if row.get('index_flag') in {'stale_robots_block', 'robots_block_current'} else None}


def index_sublabel(row):
    age = age_days(row.get('index_last_crawl_at'))
    crawl = pt_date(row.get('index_last_crawl_at'), short=True)
    return (f"{row.get('index_coverage') or 'No index detail'} · crawled {crawl}"
            + (f' ({age}d ago)' if age is not None else '')
            + f" · inspected {pt_date(row.get('index_last_fetched_at'), short=True)}")


def index_evidence_rollup(conn):
    counts = dict(stale_robots_block=0, robots_block_current=0, crawl_older_than_21d=0,
                  inspection_older_than_7d=0, inspection_total=0)
    stale_cutoff = time.time() - (STALE_INSPECTION_DAYS * 86400)
    for table in TABLES.values():
        for row in conn.execute(f'SELECT index_flag, index_last_crawl_at, index_last_fetched_at FROM {table}'):
            if row['index_flag'] in counts:
                counts[row['index_flag']] += 1
            crawl_age = age_days(row['index_last_crawl_at'])
            counts['crawl_older_than_21d'] += int(crawl_age is not None and crawl_age > 21)
            fetched_ts = timestamp(row['index_last_fetched_at'])
            if fetched_ts is not None:
                counts['inspection_total'] += 1
                if fetched_ts <= stale_cutoff:
                    counts['inspection_older_than_7d'] += 1
    alerts = []
    for row in conn.execute('SELECT * FROM robots_snapshots WHERE id IN (SELECT MAX(id) FROM robots_snapshots GROUP BY url)'):
        if row['status_code'] != 200:
            alerts.append(f"robots.txt fetch non-200 ({row['status_code']}): {row['url']}")
        if re.search(r'^\s*disallow\s*:\s*/\s*(?:#.*)?$', row['body'], re.I | re.M):
            alerts.append(f"robots.txt contains bare Disallow: /: {row['url']}")
        if row['byte_size'] < 500:
            alerts.append(f"robots.txt is under 500 bytes: {row['url']}")
        previous = conn.execute('SELECT byte_size FROM robots_snapshots WHERE url=? AND id<? ORDER BY id DESC LIMIT 1', (row['url'], row['id'])).fetchone()
        if previous and abs(row['byte_size'] - previous[0]) > max(previous[0], 1) * .5:
            alerts.append(f"robots.txt size changed by more than 50%: {row['url']}")
    if counts['robots_block_current']:
        alerts.append(f"P1: {counts['robots_block_current']} URLs currently blocked by robots.txt")
    return {**counts, 'robots_alerts': alerts}


def update_catalog_inspection(conn, kind, handle, payload, fetched_at=None, *, url):
    fields = with_index_flag(conn, extract_inspection_fields(payload, fetched_at), url)
    if not fields or kind not in TABLES:
        return False
    where, keys = 'handle=?', (handle,)
    if kind == 'blog_article':
        parts = handle.split('/', 1)
        if len(parts) != 2:
            return False
        where, keys = 'blog_handle=? AND handle=?', tuple(parts)
    conn.execute(f"UPDATE {TABLES[kind]} SET {', '.join(k + '=?' for k in INDEX_STORED_FIELDS)}, seo_signal_updated_at=CURRENT_TIMESTAMP WHERE {where}",
                 (*(fields[k] for k in INDEX_STORED_FIELDS), *keys))
    return True


def reconcile_index_cache(conn):
    """Backfill diagnostic fields from cache without refreshing any Google endpoint."""
    from .dashboard_queries._urls import object_url, blog_article_composite_handle
    targets = {}
    for kind, table in TABLES.items():
        cols = 'handle, blog_handle' if kind == 'blog_article' else 'handle'
        for row in conn.execute(f'SELECT {cols} FROM {table}'):
            handle = blog_article_composite_handle(row['blog_handle'], row['handle']) if kind == 'blog_article' else row['handle']
            targets[object_url(kind, handle)] = (kind, handle)
    n = 0
    # Newest observation wins if an older property cache also contains this URL.
    for row in conn.execute("SELECT url,payload_json,fetched_at FROM google_api_cache WHERE cache_type='url_inspection' ORDER BY fetched_at"):
        target = targets.get(row['url'])
        if target:
            try:
                payload = json.loads(row['payload_json'])
            except (ValueError, TypeError):
                continue
            n += int(update_catalog_inspection(conn, *target, payload, row['fetched_at'], url=row['url']))
    conn.commit()
    return n
