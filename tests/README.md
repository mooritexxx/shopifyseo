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
ignores `path` when `DATABASE_URL` is Postgres and would dump every leftover
SQLite test onto one shared server. Pass `url=` (or use `testdb.connect()`)
until 7d has converted the leftover `sqlite3.connect` sites.

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
| `backend-postgres` | `TEST_DATABASE_URL` → service DB | All of `tests/` on the **`pgvector/pgvector:pg17`** image (PG17 + `vector`). Tests that still call `sqlite3.connect` stay on SQLite; fixture / `test_db_layer.py` Postgres cases actually hit PG |

The image matches the box (PostgreSQL 17 + pgvector). The job enables
`CREATE EXTENSION vector` and does **not** set `DATABASE_URL`.

## Plan 7b–d

Shared fixtures from 7a. Convert leftover `sqlite3.connect` sites to
`testdb` / `db_conn` / `db_connect` by area. Until a file is converted, the
Postgres CI job still opens a SQLite temp file for that test.

**7b (done): dashboard / GSC / API.** Converted 16 modules
(39 `sqlite3.connect` sites) onto the shared fixtures.

**7c (this PR, done): internal links + embeddings.** Converted 17 files
(37 `sqlite3.connect` sites) onto the shared fixtures:

- `tests/internal_links_support.py` (shared helper; callers including
  `test_ai_weave_schema.py` / `test_internal_links_html_equiv.py` now pass
  `testdb` / `db_conn`)
- `tests/test_internal_links_undo.py`
- `tests/test_internal_links_schema.py`
- `tests/test_internal_links_restore.py`
- `tests/test_internal_links_pipeline.py`
- `tests/test_internal_links_phases_b_to_e.py`
- `tests/test_internal_links_pagination.py`
- `tests/test_internal_links_apply.py`
- `tests/test_internal_links_manual_weave.py`
- `tests/test_internal_links_api.py`
- `tests/test_internal_links_locator.py`
- `tests/test_internal_links_ai_weave.py`
- `tests/test_internal_links_graph.py`
- `tests/test_internal_links_entity_anchor_spelling.py`
- `tests/test_embedding_sync.py`
- `tests/test_embedding_store.py`

SQLite-only CHECK-constraint rewrite / `PRAGMA database_list` cases skip on
Postgres (production already no-ops those paths via `backend_for_connection`).
`embedding_status` passes `backend=backend_for_connection(conn)` into
`group_concat` so `DATABASE_URL` can stay unset.

`testdb` Postgres connections rewrite SQLite-shaped DDL (`?`, `executescript`,
AUTOINCREMENT, `INTEGER`→`BIGINT`, `BLOB`→`BYTEA`, `REAL`→`DOUBLE PRECISION`,
`INSERT OR IGNORE` / `INSERT OR REPLACE`, `datetime('now')`, `PRAGMA table_info`)
so those tests hit Postgres while `DATABASE_URL` stays unset. Helpers such as
`table_columns` / `insert_returning_id` use `backend_for_connection(conn)`
rather than `DATABASE_URL`.

**Remaining** (`rg` AST `sqlite3.connect(`): **38 files / 70** call sites.

| Slice | Scope | Files | Calls |
| --- | --- | --- | --- |
| **7c** | Internal links + embeddings (done this PR) | 17 | 37 |
| **7d** | Keyword / rank / team tasks + leftover article, catalog, type-guard, and deliberate SQLite cases | 38 | 70 |

Until 7d lands, leftover tests still open SQLite files; they do **not** yet
prove those areas on Postgres.
