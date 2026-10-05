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
until 7b–d have converted the `sqlite3.connect` sites.

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

## Plan 7b–d (out of this PR)

**70 files** (69 test modules plus `tests/internal_links_support.py`) still call
`sqlite3.connect` (**145** AST call sites). Convert them to `testdb` / `db_conn`
by area. Current list:

```bash
rg -l 'sqlite3\.connect\(' tests
```

Until those land, the Postgres CI job is green because leftover tests still
open SQLite files; they do **not** yet prove the app on Postgres.
