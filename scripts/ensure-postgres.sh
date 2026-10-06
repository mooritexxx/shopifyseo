#!/usr/bin/env bash
# Reset-durable PostgreSQL 17 cluster under /home/box.
#
# After a box reset, apt packages and /var/lib/postgresql are wiped. Cluster
# data in $PGDATA_DIR (default /home/box/pgdata/17/main) survives.
#
# This script:
#   - installs postgresql-17 binaries if missing (apt; PGDG if needed)
#   - installs postgresql-17-pgvector when that package is available
#   - initdb ONLY when the data dir is missing or empty
#   - never overwrites or re-inits a non-empty data dir
#   - starts the cluster if it is not running; no-op if already running
#   - does NOT create/alter roles or databases unless --bootstrap
#   - never prints secrets
#
# Safe to run repeatedly and while Postgres is already running.
# Called by scripts/start-app.sh. See docs/pg-cutover.md.
set -euo pipefail

PGDATA_DIR="${PGDATA_DIR:-/home/box/pgdata/17/main}"
PGPORT="${PGPORT:-5432}"
BOOTSTRAP=0
NO_INSTALL=0
PG_BINDIR="${PG_BINDIR:-}"

usage() {
  cat <<'EOF'
ensure-postgres.sh — idempotent, reset-durable PostgreSQL 17 under /home/box.

Usage: scripts/ensure-postgres.sh [options]

  --help, -h          This help
  --pgdata DIR        Data directory (default: /home/box/pgdata/17/main,
                      or $PGDATA_DIR)
  --port N            Port (default: 5432, or $PGPORT)
  --bootstrap         Create role shopifyseo and database shopifyseo only if
                      they do not already exist. Never alters existing roles
                      or passwords. Off by default.
  --no-install        Do not apt-install packages (tests / already-present bins)

Environment:
  PGDATA_DIR, PGPORT, PG_BINDIR   same meaning as the flags
  SHOPIFYSEO_ENSURE_POSTGRES_NO_INSTALL=1   same as --no-install

Behavior:
  - initdb only when the data dir is missing or empty
  - refuses to init a non-empty directory (even if it is not a cluster)
  - does not delete an existing Debian cluster under /var/lib/postgresql
  - migrate /var/lib/postgresql/17/main into /home/box by hand if you still
    have data there (copy the directory, then point PGDATA_DIR at it, or
    rsync into the default path). This script will not do that for you.
  - never prints secrets
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --pgdata) PGDATA_DIR="$2"; shift 2 ;;
    --port) PGPORT="$2"; shift 2 ;;
    --bootstrap) BOOTSTRAP=1; shift ;;
    --no-install) NO_INSTALL=1; shift ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${SHOPIFYSEO_ENSURE_POSTGRES_NO_INSTALL:-}" == "1" ]]; then
  NO_INSTALL=1
fi

die() {
  echo "error: $*" >&2
  exit 1
}

run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1 && sudo -n true >/dev/null 2>&1; then
    sudo "$@"
  else
    die "PostgreSQL 17 binaries are missing; re-run as root (or with passwordless sudo) to install packages"
  fi
}

find_pg_bin() {
  local candidates=()
  if [[ -n "$PG_BINDIR" ]]; then
    candidates+=("$PG_BINDIR")
  fi
  local from_path=""
  if from_path="$(command -v pg_ctl 2>/dev/null)"; then
    candidates+=("$(dirname "$from_path")")
  fi
  candidates+=(
    /usr/lib/postgresql/17/bin
    /usr/pgsql-17/bin
    /usr/local/pgsql/bin
  )
  local dir
  for dir in "${candidates[@]}"; do
    if [[ -x "$dir/pg_ctl" && -x "$dir/initdb" ]]; then
      echo "$dir"
      return 0
    fi
  done
  return 1
}

install_postgres_packages() {
  if [[ "$NO_INSTALL" -eq 1 ]]; then
    die "PostgreSQL 17 binaries not found and --no-install is set"
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    die "apt-get not found; install PostgreSQL 17 by hand"
  fi
  export DEBIAN_FRONTEND=noninteractive
  run_as_root apt-get update -y
  if run_as_root apt-get install -y postgresql-17 postgresql-contrib-17; then
    :
  else
    echo "note: postgresql-17 not in current apt sources; adding PGDG" >&2
    run_as_root apt-get install -y postgresql-common ca-certificates
    if [[ -x /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh ]]; then
      run_as_root /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
    else
      die "could not add the PGDG apt repo; postgresql-17 is unavailable"
    fi
    run_as_root apt-get install -y postgresql-17 postgresql-contrib-17 \
      || die "failed to install postgresql-17 after adding PGDG"
  fi
  run_as_root apt-get install -y postgresql-17-pgvector || \
    echo "note: postgresql-17-pgvector is not available; continuing without it" >&2
}

PG_BIN="$(find_pg_bin || true)"
if [[ -z "$PG_BIN" ]]; then
  install_postgres_packages
  PG_BIN="$(find_pg_bin || true)"
fi
if [[ -z "$PG_BIN" ]]; then
  die "PostgreSQL 17 binaries still not found after install (pg_ctl/initdb)"
fi

export PATH="$PG_BIN:$PATH"
INITDB="$PG_BIN/initdb"
PG_CTL="$PG_BIN/pg_ctl"
PSQL_BIN="$PG_BIN/psql"
if [[ ! -x "$PSQL_BIN" ]] && command -v psql >/dev/null 2>&1; then
  PSQL_BIN="$(command -v psql)"
fi

if [[ -f /var/lib/postgresql/17/main/PG_VERSION ]]; then
  echo "note: existing cluster data found at /var/lib/postgresql/17/main; not deleting or migrating it." >&2
  echo "note: to use that data under /home/box, copy it into $PGDATA_DIR by hand (see docs/pg-cutover.md)." >&2
fi
if [[ -d /etc/postgresql/17/main ]]; then
  echo "note: Debian cluster config exists at /etc/postgresql/17/main; leaving it in place." >&2
fi

if [[ ! -d "$PGDATA_DIR" ]]; then
  mkdir -p "$PGDATA_DIR" || die "could not create data dir $PGDATA_DIR"
fi

if [[ -z "$(ls -A "$PGDATA_DIR" 2>/dev/null || true)" ]]; then
  echo "initializing new cluster in $PGDATA_DIR"
  "$INITDB" -D "$PGDATA_DIR" --encoding=UTF8 --locale=C \
    --auth-local=trust --auth-host=scram-sha-256 \
    || die "initdb failed for $PGDATA_DIR"
elif [[ -f "$PGDATA_DIR/PG_VERSION" ]]; then
  echo "using existing cluster in $PGDATA_DIR"
else
  die "data dir $PGDATA_DIR is not empty and is not a PostgreSQL cluster (no PG_VERSION). Refusing to initdb."
fi

if "$PG_CTL" -D "$PGDATA_DIR" status >/dev/null 2>&1; then
  echo "cluster already running ($PGDATA_DIR port $PGPORT)"
else
  echo "starting cluster ($PGDATA_DIR port $PGPORT)"
  mkdir -p "$PGDATA_DIR"
  "$PG_CTL" -D "$PGDATA_DIR" -l "$PGDATA_DIR/pg_ctl.log" \
    -o "-p ${PGPORT} -c listen_addresses=localhost" start \
    || die "failed to start PostgreSQL at $PGDATA_DIR on port $PGPORT"
fi

if [[ "$BOOTSTRAP" -eq 1 ]]; then
  if [[ ! -x "$PSQL_BIN" ]]; then
    die "--bootstrap requires psql"
  fi
  "$PSQL_BIN" -h 127.0.0.1 -p "$PGPORT" -d postgres -v ON_ERROR_STOP=1 <<'SQL'
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shopifyseo') THEN
    CREATE ROLE shopifyseo LOGIN;
  END IF;
END
$$;

SELECT 'CREATE DATABASE shopifyseo OWNER shopifyseo'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'shopifyseo')\gexec
SQL
  if "$PSQL_BIN" -h 127.0.0.1 -p "$PGPORT" -d shopifyseo -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
    echo "vector extension ready (or already present)"
  else
    echo "note: CREATE EXTENSION vector skipped (package or permission missing)" >&2
  fi
fi

echo "Postgres is ready (data dir $PGDATA_DIR, port $PGPORT)."
