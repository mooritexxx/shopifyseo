"""Rank tracking schema. Called by the normal once-per-database bootstrap."""
TERMS = (
    'vape shop canada', 'online vape shop canada', 'best online vape shop canada',
    'buy vape online canada', 'canadian vape store', 'disposable vapes canada',
    'vape canada', 'stlth canada', 'elfbar canada', 'geek bar canada', 'fog formulas',
    'flavour beast canada', 'allo canada', 'kraze canada', 'abt vape', 'vfeel',
)


def ensure_schema(conn):
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
    # A marker prevents deleted/deactivated seed keywords reappearing on restart.
    seeded = conn.execute("SELECT value FROM service_settings WHERE key='rank_tracking_seeded'").fetchone()
    if not seeded:
        conn.executemany('INSERT OR IGNORE INTO tracked_keywords(term) VALUES (?)', [(t,) for t in TERMS])
        conn.execute("INSERT INTO service_settings(key,value) VALUES ('rank_tracking_seeded','1')")
    conn.execute("INSERT OR IGNORE INTO service_settings(key,value) VALUES ('serpapi_rank_monthly_budget','250')")
    conn.commit()
