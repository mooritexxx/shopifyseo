#!/usr/bin/env bash
# Reset-durable PostgreSQL 17 cluster under /home/box.
#
# After a box reset, apt packages and /var/lib/postgresql are wiped. Cluster
# data in $PGDATA_DIR (default /home/box/pgdata/17/main) survives.
#
# This script:
#   - installs postgresql-17 binaries if missing (apt; PGDG if needed)
#   - writes create_main_cluster=false before apt so Debian does not create
#     or start 17/main on 5432
#   - installs postgresql-17-pgvector when that package is available
#   - initdb ONLY when the data dir is missing or empty
#   - never overwrites or re-inits a non-empty data dir
#   - starts the cluster with a box-writable unix_socket_directories
#   - does NOT create/alter roles or databases unless --bootstrap
#   - never prints secrets
#
# Safe to run repeatedly and while Postgres is already running.
# Called by scripts/start-app.sh. See docs/pg-cutover.md.
set -euo pipefail

PGDATA_DIR="${PGDATA_DIR:-/home/box/pgdata/17/main}"
PGPORT="${PGPORT:-5432}"
PGSOCKET_DIR="${PGSOCKET_DIR:-}"
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
  --socket-dir DIR    Unix socket directory (default: $(dirname $PGDATA_DIR)/run,
                      or $PGSOCKET_DIR). Created mode 0700.
  --bootstrap         Create role shopifyseo and database shopifyseo only if
                      they do not already exist. Never alters existing roles
                      or passwords. Off by default.
  --no-install        Do not apt-install packages (tests / already-present bins)

Environment:
  PGDATA_DIR, PGPORT, PGSOCKET_DIR, PG_BINDIR   same meaning as the flags
  SHOPIFYSEO_ENSURE_POSTGRES_NO_INSTALL=1   same as --no-install
  SHOPIFYSEO_PG_ENV   pg.env used by --bootstrap for a new-role password
  PGPASSWORD          preferred source for a new-role password (never printed)

Behavior:
  - initdb only when the data dir is missing or empty
  - refuses to init a non-empty directory (even if it is not a cluster)
  - refuses to initdb/start when the target port is already in use by
    another process (does not leave a new empty cluster behind)
  - starts with unix_socket_directories=$PGSOCKET_DIR (box-writable; not
    /var/run/postgresql)
  - does not delete or stop an existing Debian cluster under /var/lib/postgresql
  - apt install writes create_main_cluster=false so postgresql-17 does not
    auto-create 17/main on 5432
  - if a Debian 17/main cluster already owns 5432, use PGPORT=5433 or stop
    that cluster by hand after migrating (see docs/pg-cutover.md)
  - --bootstrap talks over the local unix socket (trust) with -w; sets a
    password only when the role is newly created
  - never prints secrets
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --pgdata) PGDATA_DIR="$2"; shift 2 ;;
    --port) PGPORT="$2"; shift 2 ;;
    --socket-dir) PGSOCKET_DIR="$2"; shift 2 ;;
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

if [[ -z "$PGSOCKET_DIR" ]]; then
  PGSOCKET_DIR="$(dirname "$PGDATA_DIR")/run"
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

write_create_main_cluster_false() {
  # Prevent apt's postgresql-17 postinst from pg_createcluster 17 main on 5432.
  # Never deletes or stops an existing /var/lib cluster.
  run_as_root mkdir -p /etc/postgresql-common /etc/postgresql-common/createcluster.d
  printf '%s\n' 'create_main_cluster = false' | run_as_root tee \
    /etc/postgresql-common/createcluster.d/00-shopifyseo-no-main.conf >/dev/null
  local conf=/etc/postgresql-common/createcluster.conf
  if run_as_root test -f "$conf"; then
    if run_as_root grep -qE '^[#[:space:]]*create_main_cluster' "$conf"; then
      run_as_root sed -i 's/^[#[:space:]]*create_main_cluster.*/create_main_cluster = false/' "$conf"
    else
      printf '%s\n' 'create_main_cluster = false' | run_as_root tee -a "$conf" >/dev/null
    fi
  else
    printf '%s\n' 'create_main_cluster = false' | run_as_root tee "$conf" >/dev/null
  fi
}

install_postgres_packages() {
  if [[ "$NO_INSTALL" -eq 1 ]]; then
    die "PostgreSQL 17 binaries not found and --no-install is set"
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    die "apt-get not found; install PostgreSQL 17 by hand"
  fi
  export DEBIAN_FRONTEND=noninteractive
  write_create_main_cluster_false
  run_as_root apt-get update -y
  run_as_root apt-get install -y postgresql-common || true
  write_create_main_cluster_false
  if run_as_root apt-get install -y postgresql-17 postgresql-contrib-17; then
    :
  else
    echo "note: postgresql-17 not in current apt sources; adding PGDG" >&2
    run_as_root apt-get install -y postgresql-common ca-certificates
    write_create_main_cluster_false
    if [[ -x /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh ]]; then
      run_as_root /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y
    else
      die "could not add the PGDG apt repo; postgresql-17 is unavailable"
    fi
    write_create_main_cluster_false
    run_as_root apt-get install -y postgresql-17 postgresql-contrib-17 \
      || die "failed to install postgresql-17 after adding PGDG"
  fi
  run_as_root apt-get install -y postgresql-17-pgvector || \
    echo "note: postgresql-17-pgvector is not available; continuing without it" >&2
}

cluster_running_here() {
  [[ -d "$PGDATA_DIR" ]] && "$PG_CTL" -D "$PGDATA_DIR" status >/dev/null 2>&1
}

port_held_by_other() {
  # True when 127.0.0.1:$PGPORT accepts connections or cannot be bound, and
  # this data dir is not the process that owns it.
  if cluster_running_here; then
    return 1
  fi
  python3 -c '
import socket, sys
port = int(sys.argv[1])
s = socket.socket()
s.settimeout(0.3)
try:
    s.connect(("127.0.0.1", port))
    s.close()
    sys.exit(0)
except OSError:
    try:
        s.close()
    except OSError:
        pass
s = socket.socket()
try:
    s.bind(("127.0.0.1", port))
except OSError:
    sys.exit(0)
finally:
    s.close()
sys.exit(1)
' "$PGPORT"
}

refuse_busy_port() {
  local action="$1"
  die "port $PGPORT already accepts connections or is in use by another process; refusing to ${action} $PGDATA_DIR. If a Debian 17/main cluster is on 5432, use PGPORT=5433 or stop that cluster by hand after migrating (see docs/pg-cutover.md). This script does not delete or stop /var/lib/postgresql."
}

pg_ctl_options() {
  printf '%s' "-p ${PGPORT} -c listen_addresses=localhost -c unix_socket_directories=${PGSOCKET_DIR}"
}

read_bootstrap_password() {
  if [[ -n "${PGPASSWORD:-}" ]]; then
    printf '%s' "$PGPASSWORD"
    return 0
  fi
  local env_file="${SHOPIFYSEO_PG_ENV:-/home/box/.config/shopifyseo/pg.env}"
  if [[ ! -f "$env_file" ]]; then
    return 0
  fi
  SHOPIFYSEO_PG_ENV="$env_file" python3 -c '
import os
from urllib.parse import unquote, urlparse
path = os.environ["SHOPIFYSEO_PG_ENV"]
url = ""
with open(path, encoding="utf-8") as fh:
    for raw in fh:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip("\"'\''")
        if key == "PGPASSWORD" and value:
            print(value, end="")
            raise SystemExit
        if key == "DATABASE_URL":
            url = value
if url:
    password = urlparse(url).password
    if password:
        print(unquote(password), end="")
'
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

mkdir -p "$PGSOCKET_DIR" || die "could not create socket dir $PGSOCKET_DIR"
chmod 700 "$PGSOCKET_DIR" || die "could not chmod 700 $PGSOCKET_DIR"

if [[ -f /var/lib/postgresql/17/main/PG_VERSION ]]; then
  echo "note: existing cluster data found at /var/lib/postgresql/17/main; not deleting, stopping, or migrating it." >&2
  echo "note: that data is not reset-durable. To move it under /home/box see docs/pg-cutover.md." >&2
fi
if [[ -d /etc/postgresql/17/main ]]; then
  echo "note: Debian cluster config exists at /etc/postgresql/17/main; leaving it in place." >&2
fi

if [[ ! -d "$PGDATA_DIR" ]]; then
  mkdir -p "$PGDATA_DIR" || die "could not create data dir $PGDATA_DIR"
fi

if [[ -z "$(ls -A "$PGDATA_DIR" 2>/dev/null || true)" ]]; then
  if port_held_by_other; then
    refuse_busy_port "initdb"
  fi
  echo "initializing new cluster in $PGDATA_DIR"
  "$INITDB" -D "$PGDATA_DIR" --encoding=UTF8 --locale=C \
    --auth-local=trust --auth-host=scram-sha-256 \
    || die "initdb failed for $PGDATA_DIR"
elif [[ -f "$PGDATA_DIR/PG_VERSION" ]]; then
  echo "using existing cluster in $PGDATA_DIR"
else
  die "data dir $PGDATA_DIR is not empty and is not a PostgreSQL cluster (no PG_VERSION). Refusing to initdb."
fi

if cluster_running_here; then
  echo "cluster already running ($PGDATA_DIR port $PGPORT)"
else
  if port_held_by_other; then
    refuse_busy_port "start"
  fi
  echo "starting cluster ($PGDATA_DIR port $PGPORT socket $PGSOCKET_DIR)"
  "$PG_CTL" -D "$PGDATA_DIR" -l "$PGDATA_DIR/pg_ctl.log" \
    -o "$(pg_ctl_options)" start \
    || die "failed to start PostgreSQL at $PGDATA_DIR on port $PGPORT"
fi

if [[ "$BOOTSTRAP" -eq 1 ]]; then
  if [[ ! -x "$PSQL_BIN" ]]; then
    die "--bootstrap requires psql"
  fi
  SHOPIFYSEO_BOOTSTRAP_PASSWORD="$(read_bootstrap_password || true)"
  export SHOPIFYSEO_BOOTSTRAP_PASSWORD
  if ! "$PSQL_BIN" -h "$PGSOCKET_DIR" -p "$PGPORT" -d postgres -w -v ON_ERROR_STOP=1 <<'SQL'
\getenv pwd SHOPIFYSEO_BOOTSTRAP_PASSWORD
SELECT format('CREATE ROLE shopifyseo LOGIN PASSWORD %L', :'pwd')
WHERE COALESCE(:'pwd', '') <> ''
  AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shopifyseo')\gexec
SELECT 'CREATE ROLE shopifyseo LOGIN'
WHERE COALESCE(:'pwd', '') = ''
  AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'shopifyseo')\gexec
SELECT 'CREATE DATABASE shopifyseo OWNER shopifyseo'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'shopifyseo')\gexec
SQL
  then
    unset SHOPIFYSEO_BOOTSTRAP_PASSWORD
    die "--bootstrap failed (password not printed). Connect via the unix socket at $PGSOCKET_DIR."
  fi
  unset SHOPIFYSEO_BOOTSTRAP_PASSWORD
  if "$PSQL_BIN" -h "$PGSOCKET_DIR" -p "$PGPORT" -d shopifyseo -w -c "CREATE EXTENSION IF NOT EXISTS vector;" >/dev/null 2>&1; then
    echo "vector extension ready (or already present)"
  else
    echo "note: CREATE EXTENSION vector skipped (package or permission missing)" >&2
  fi
fi

echo "Postgres is ready (data dir $PGDATA_DIR, port $PGPORT, socket dir $PGSOCKET_DIR)."
