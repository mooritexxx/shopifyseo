#!/usr/bin/env bash
# Box-level PostgreSQL 17 + pgvector installer for CoS.
#
# Installs packages and a localhost-only cluster. It is NOT part of the live
# app start path. Do not call this from start_app.sh or scripts/dev-restart-local.sh.
# Live uvicorn stays on SQLite until Salar approves DATABASE_URL.
#
# Typical use (once, as a privileged operator):
#   sudo ./scripts/ensure-postgres.sh
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'EOF'
ensure-postgres.sh — install PostgreSQL 17 + pgvector on the box.

This script:
  - installs postgresql-17 and postgresql-17-pgvector (Debian/Ubuntu)
  - ensures a localhost cluster on 5432
  - creates role shopifyseo (peer/scram as available)
  - creates databases shopifyseo and shopifyseo_cutover_dryrun
  - CREATE EXTENSION vector
  - does NOT write DATABASE_URL, pg.env, or start uvicorn

Test cluster (optional, port 5433) is documented in tests/README.md and is
out of scope here so the live cluster stays on 5432.
EOF
  exit 0
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "error: run as root (sudo $0). This is box setup, not the app start path." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive

if command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y postgresql-17 postgresql-contrib-17 postgresql-17-pgvector || \
    apt-get install -y postgresql postgresql-contrib postgresql-17-pgvector || \
    apt-get install -y postgresql postgresql-contrib
else
  echo "error: apt-get not found. Install PostgreSQL 17 + pgvector by hand." >&2
  exit 2
fi

# Prefer the default 17 cluster on 5432. Do not rewrite listen_addresses to '*'.
PG_CONF="$(ls /etc/postgresql/17/*/postgresql.conf 2>/dev/null | head -n 1 || true)"
if [[ -n "$PG_CONF" ]]; then
  if grep -q "^#listen_addresses" "$PG_CONF"; then
    sed -i "s/^#listen_addresses.*/listen_addresses = 'localhost'/" "$PG_CONF" || true
  fi
fi

if command -v pg_lsclusters >/dev/null 2>&1; then
  if ! pg_lsclusters --no-header | awk '{print $1,$2,$3}' | grep -q '17 main online'; then
    pg_ctlcluster 17 main start || true
  fi
else
  service postgresql start || systemctl start postgresql || true
fi

sudo -u postgres psql -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shopifyseo') THEN
    CREATE ROLE shopifyseo LOGIN;
  END IF;
END
$$;

SELECT 'CREATE DATABASE shopifyseo OWNER shopifyseo'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'shopifyseo')\gexec

SELECT 'CREATE DATABASE shopifyseo_cutover_dryrun OWNER shopifyseo'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'shopifyseo_cutover_dryrun')\gexec
SQL

for db in shopifyseo shopifyseo_cutover_dryrun; do
  sudo -u postgres psql -d "$db" -v ON_ERROR_STOP=1 -c "CREATE EXTENSION IF NOT EXISTS vector;"
done

echo
echo "Postgres is installed. Live uvicorn was not started and DATABASE_URL was not set."
echo "Create /home/box/.config/shopifyseo/pg.env from scripts/pg_cutover/pg_env.example"
echo "and run scripts/pg_cutover.sh --dry-run when ready. See docs/pg-cutover.md."
