# Backend tests

Pytest under `tests/`. Default backend is **SQLite** (temp files / the same
`sqlite3.connect` helpers the suite has always used). PostgreSQL is opt-in via
`TEST_DATABASE_URL` so plan 7b–d can migrate tests area-by-area.

## Dual-backend fixture (plan 7a)

Shared fixtures live in `tests/conftest.py` (`tests/db_support.py` for the
helpers). They open connections through `shopifyseo.db.get_connection` — the
same path as production `connect()` — so Postgres session `timezone=UTC` and
CURRENT_TIMESTAMP → naive UTC `YYYY-MM-DD HH:MM:SS` text (plan 6) apply.

| Fixture | Scope | Behaviour |
| --- | --- | --- |
| `testdb` / `db_conn` | function | Isolated SQLite temp file, or a unique Postgres schema |
| `testdb_module` / `db_conn_module` | module | Same isolation, shared for the module |
| `db_connect` | function | Extra connections to the same `testdb` |
| `pg_url` / `pg_conn` | function | Postgres **public** schema; **skips** unless `TEST_DATABASE_URL` is a Postgres URL. Used by `tests/test_db_layer.py` |

When `TEST_DATABASE_URL` is unset, `testdb` / `db_conn` use SQLite. When it is
`postgresql://…` or `postgres://…`, they use that database.

**Do not set `DATABASE_URL` for the whole suite.** `get_connection(path=tmp)`
ignores `path` when `DATABASE_URL` is Postgres and would dump leftover
SQLite-only constructors onto one shared server. Pass `url=` (or use
`testdb.connect()`). Intentional remaining `sqlite3.connect` sites are listed
below.

## Local Postgres (box operators)

CI uses a service container. On the box, keep the live cluster on **5432** and
put the test cluster on **5433**, database `shopifyseo_test`:

```bash
sudo pg_createcluster 17 test --port 5433 -- --auth-local=peer
# listen_addresses = localhost only (same as the live cluster)
sudo pg_ctlcluster 17 test start
sudo -u postgres psql -p 5433 -c "CREATE DATABASE shopifyseo_test;"
sudo -u postgres psql -p 5433 -d shopifyseo_test -c "CREATE EXTENSION IF NOT EXISTS vector;"

export TEST_DATABASE_URL=postgresql://shopifyseo@127.0.0.1:5433/shopifyseo_test
# peer/scram as you prefer; password goes in the URL, not in the repo
PYTHONPATH=. python -m pytest tests/test_db_layer.py tests/test_db_fixture.py -q
```

Live deploy stays on SQLite until Salar approves a `DATABASE_URL` cutover.
`TEST_DATABASE_URL` is tests-only and is not sourced from `/home/box/.config/shopifyseo/pg.env`.

## CI

| Job | Backend | What runs |
| --- | --- | --- |
| `backend-smoke` | SQLite (`TEST_DATABASE_URL` unset) | All of `tests/` (same deselects as before) |
| `backend-postgres` | `TEST_DATABASE_URL` → service DB | All of `tests/` on the **`pgvector/pgvector:pg17`** image (PG17 + `vector`). Converted modules hit Postgres; intentional `sqlite3.connect` sites (below) stay on SQLite. Fixture / `test_db_layer.py` Postgres cases use `pg_conn` |

The image matches the box (PostgreSQL 17 + pgvector). The job enables
`CREATE EXTENSION vector` and does **not** set `DATABASE_URL`.

## Plan 7b–d

Shared fixtures from 7a. Convert leftover `sqlite3.connect` sites to
`testdb` / `db_conn` / `db_connect` by area.

**7b (done): dashboard / GSC / API.** Converted 16 modules
(39 `sqlite3.connect` sites) onto the shared fixtures.

**7c (done): internal links + embeddings.** Converted 17 files
(37 `sqlite3.connect` sites) onto the shared fixtures. SQLite-only
CHECK-constraint rewrite / `PRAGMA database_list` cases skip on Postgres
(production already no-ops those paths via `backend_for_connection`).

**7d (this PR, done): keyword / rank / team tasks + leftover article,
catalog, type-guard, and deliberate SQLite cases.** Converted the leftover
application tests onto the shared fixtures so they hit Postgres when
`TEST_DATABASE_URL` is set:

- keyword / rank: `test_keyword_research.py`, `test_keyword_clustering.py`,
  `test_rank_tracking.py`, `test_open_page_rank.py`,
  `test_cannibalization_check.py`, `test_store_fit.py`,
  `test_market_context.py`, `test_approve_pending_competitor.py`
- team / opportunity tasks: `test_team_tasks.py`, `test_opportunity_tasks.py`
- leftover article: idea save/fetch/inputs/lifecycle/serp/targets/cluster,
  draft runs/retrieval/phased/serp prompt/primary link/grounding/linkable
  gate/final content filter, `test_article_partial_update.py`,
  `test_resolve_idea_targets.py`, `test_ensure_idea_serp_fresh.py`,
  `test_internal_link_allowlist.py`
- leftover catalog: `test_catalog_image_work.py`,
  `test_shopify_image_cache.py`, `test_product_seo_title_real_paths.py`
- type-guard table/schema cases: `test_column_exists_uses_table_columns`,
  `test_cluster_table_columns_via_helper`, and the three
  `test_remaining_areas_db_types.py` helpers now use `db_conn`

`testdb` Postgres connections rewrite SQLite-shaped SQL (`?`, `executescript`,
AUTOINCREMENT, `INTEGER`→`BIGINT`, `BLOB`→`BYTEA`, `REAL`→`DOUBLE PRECISION`,
`INSERT OR IGNORE` / `INSERT OR REPLACE`, `datetime('now')`→plan-6
`PG_NOW_TEXT_SQL`, `last_insert_rowid()`→`lastval()`, `PRAGMA table_info`)
so those tests hit Postgres while `DATABASE_URL` stays unset. Helpers such as
`table_columns` / `insert_returning_id` use `backend_for_connection(conn)`
rather than `DATABASE_URL`.

Team-task `RAISE(ABORT)` append-only triggers are skipped by the testdb
Postgres adapter (not valid PG); the matching test skips on Postgres.
Live SQLite still installs them.

**Intentional remaining `sqlite3.connect` (must stay on SQLite):**

| File | Why |
| --- | --- |
| `tests/test_dashboard_db_types.py` `_make_sqlite_row` + bare `row_factory` | Constructs a real `sqlite3.Row` / a connection with `row_factory is None` (testdb `get_connection` already installs a factory) |
| `tests/test_backend_db_types.py` | Same `sqlite3.Row` / bare-factory type guards |
| `tests/test_row_get_helper.py` `_make_sqlite_row` | Same `sqlite3.Row` constructor |
| `tests/test_db_layer.py` | `backend_for_connection(sqlite3)` and SQLite `BEGIN IMMEDIATE` lock contention |
| `tests/test_connection_sites.py` | File-DB seed for SQLite PRAGMA / `get_connection` kwargs |
| `tests/test_sqlite_utf8.py` | `ATTACH` + UTF-8 text factory |

These still run during `backend-postgres`; they open SQLite on purpose and do
not prove those paths on Postgres.

| Slice | Scope | Status |
| --- | --- | --- |
| **7b** | Dashboard / GSC / API | done |
| **7c** | Internal links + embeddings | done |
| **7d** | Keyword / rank / team + leftovers | done |
