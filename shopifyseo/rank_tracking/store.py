"""Rank tracking schema. Called by the normal once-per-database bootstrap."""
from shopifyseo.db import executemany, sqlite_runtime_ddl, table_columns

TERMS = (
    'vape shop canada', 'online vape shop canada', 'best online vape shop canada',
    'buy vape online canada', 'canadian vape store', 'disposable vapes canada',
    'vape canada', 'stlth canada', 'elfbar canada', 'geek bar canada', 'fog formulas',
    'flavour beast canada', 'allo canada', 'kraze canada', 'abt vape', 'vfeel',
)


def ensure_schema(conn):
    if sqlite_runtime_ddl():
        conn.executescript('''
      CREATE TABLE IF NOT EXISTS tracked_keywords (
        id INTEGER PRIMARY KEY, term TEXT NOT NULL UNIQUE,
        target_url TEXT, grp TEXT, active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS rank_jobs (
        id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
        weekly_date TEXT UNIQUE, status TEXT NOT NULL,
        keyword_ids TEXT NOT NULL, max_pages INTEGER NOT NULL,
        reserved INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, finished_at TEXT, error TEXT
      );
      CREATE UNIQUE INDEX IF NOT EXISTS rank_one_running
        ON rank_jobs(status) WHERE status = 'running';
      CREATE TABLE IF NOT EXISTS rank_checks (
        id INTEGER PRIMARY KEY, keyword_id INTEGER NOT NULL REFERENCES tracked_keywords(id),
        job_id TEXT REFERENCES rank_jobs(id), checked_at TEXT NOT NULL, check_date TEXT NOT NULL,
        position INTEGER, reported_position INTEGER, ranking_url TEXT,
        top1_domain TEXT, top2_domain TEXT, top3_domain TEXT,
        pages_checked INTEGER NOT NULL DEFAULT 0, searches_used INTEGER NOT NULL DEFAULT 0,
        checked_depth INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('ok','error','unverified')),
        error TEXT, profile TEXT NOT NULL, target_url TEXT,
        import_key TEXT UNIQUE,
        UNIQUE(job_id, keyword_id)
      );
      CREATE INDEX IF NOT EXISTS rank_checks_history ON rank_checks(keyword_id, checked_at DESC);
      CREATE TABLE IF NOT EXISTS rank_requests (
        id INTEGER PRIMARY KEY, job_id TEXT REFERENCES rank_jobs(id),
        keyword_id INTEGER REFERENCES tracked_keywords(id), requested_at TEXT NOT NULL,
        month TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS rank_requests_month ON rank_requests(month);
    ''')
    for table, columns in {
        'rank_jobs': {'cancel_requested': 'INTEGER NOT NULL DEFAULT 0'},
        'rank_checks': {'coverage_complete': 'INTEGER NOT NULL DEFAULT 1',
                        'cancelled': 'INTEGER NOT NULL DEFAULT 0'},
    }.items():
        existing = table_columns(conn, table)
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    # Older code rejected otherwise valid searches solely for short organic pages.
    # Correct that specific outcome without claiming full top-N coverage or spending credits.
    affected = conn.execute('''SELECT DISTINCT job_id FROM rank_checks
        WHERE status='error' AND source='serpapi' AND position IS NULL
        AND error='Incomplete organic results pages; absence from the top range is unverified.'
        AND pages_checked=(SELECT max_pages FROM rank_jobs WHERE id=job_id)''').fetchall()
    conn.execute('''UPDATE rank_checks SET status='ok',error=NULL,coverage_complete=0
        WHERE status='error' AND source='serpapi' AND position IS NULL
        AND error='Incomplete organic results pages; absence from the top range is unverified.'
        AND pages_checked=(SELECT max_pages FROM rank_jobs WHERE id=job_id)''')
    for row in affected:
        failed = conn.execute("SELECT count(*) FROM rank_checks WHERE job_id=? AND status='error'", (row[0],)).fetchone()[0]
        conn.execute('''UPDATE rank_jobs SET status=?,error=? WHERE id=? AND status='error'
            AND substr(error, 1, 1) BETWEEN '0' AND '9'
            AND error LIKE '% keyword checks failed. See history.' ''',
            ('error' if failed else 'complete', f'{failed} keyword checks failed. See history.' if failed else None, row[0]))
    # A marker prevents deleted/deactivated seed keywords reappearing on restart.
    seeded = conn.execute("SELECT value FROM service_settings WHERE key='rank_tracking_seeded'").fetchone()
    if not seeded:
        executemany(conn, 'INSERT INTO tracked_keywords(term) VALUES (?) ON CONFLICT DO NOTHING', [(t,) for t in TERMS])
        conn.execute("INSERT INTO service_settings(key,value) VALUES ('rank_tracking_seeded','1')")
    conn.execute("INSERT INTO service_settings(key,value) VALUES ('serpapi_rank_monthly_budget','250') ON CONFLICT DO NOTHING")
    conn.commit()
