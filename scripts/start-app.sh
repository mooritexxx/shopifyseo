#!/usr/bin/env bash
# Production app start for the box.
#
# Called by /home/box/bin/restore-tailscale.sh (outside this repo) and any
# reboot path. Dev-only alternatives: ./start_app.sh and
# scripts/dev-restart-local.sh (they do not honor the live mark).
#
# Decision:
#   - no live mark -> SQLite, DATABASE_URL explicitly unset. ensure-postgres
#     is best-effort only when $PGDATA_DIR already has PG_VERSION; otherwise
#     it is skipped so a Postgres problem cannot block SQLite.
#   - live mark present -> ensure-postgres is fatal; source pg.env; require
#     DATABASE_URL and a reachable Postgres; never fall back to SQLite
#   - if ensure-postgres wrote a listen_port (Debian 17/main kept 5432),
#     rewrite PGPORT / DATABASE_URL port after sourcing pg.env
#   - tmp/pg-cutover-*/cutover_mark.json is NOT a live mark
#   - when the decision is postgres, launch scripts/pg-backup-daemon.sh
#     (cron-free nightly dump). Never blocks or fails startup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_MARK="${SHOPIFYSEO_PG_LIVE_MARK:-/home/box/.config/shopifyseo/pg_live_cutover.json}"
PG_ENV="${SHOPIFYSEO_PG_ENV:-/home/box/.config/shopifyseo/pg.env}"
UVICORN_LOG="${SHOPIFYSEO_UVICORN_LOG:-/home/box/logs/shopifyseo-uvicorn.log}"
UVICORN_HOST="${SHOPIFYSEO_UVICORN_HOST:-127.0.0.1}"
UVICORN_PORT="${SHOPIFYSEO_UVICORN_PORT:-8000}"
DECIDE_ONLY=0
FOREGROUND=0
SKIP_ENSURE=0
SKIP_CRON=0
SKIP_BACKUP_DAEMON=0

usage() {
  cat <<'EOF'
start-app.sh — production uvicorn start (ensure-postgres + live mark).

Usage: scripts/start-app.sh [options]

  --help, -h              This help
  --decide-only, --dry-run
                          Print the decision (sqlite|postgres|error) and exit
                          without starting uvicorn. Does not run ensure-postgres.
  --foreground            exec uvicorn in the foreground (default: background)
  --skip-ensure-postgres  Skip scripts/ensure-postgres.sh (tests)

Environment (all overridable for tests; defaults are box paths):
  SHOPIFYSEO_PG_LIVE_MARK     live mark JSON (default /home/box/.config/shopifyseo/pg_live_cutover.json)
  SHOPIFYSEO_PG_ENV           pg.env (default /home/box/.config/shopifyseo/pg.env)
  SHOPIFYSEO_UVICORN_LOG      uvicorn log (default /home/box/logs/shopifyseo-uvicorn.log)
  SHOPIFYSEO_UVICORN_HOST     default 127.0.0.1
  SHOPIFYSEO_UVICORN_PORT     default 8000
  SHOPIFYSEO_ENSURE_POSTGRES_SH  override ensure-postgres.sh path (tests)
  SHOPIFYSEO_PYTHON           uvicorn interpreter (default $ROOT/.venv/bin/python3)
  PGDATA_DIR                  used to decide whether to skip ensure when no mark
  SHOPIFYSEO_SKIP_ENSURE_POSTGRES=1
  SHOPIFYSEO_SKIP_BACKUP_CRON=1
  SHOPIFYSEO_SKIP_BACKUP_DAEMON=1
  SHOPIFYSEO_LISTEN_PORT_FILE   port file written by ensure-postgres.sh
  SHOPIFYSEO_PG_BACKUP_DAEMON_SH  override pg-backup-daemon.sh (tests)
  SHOPIFYSEO_PG_BACKUP_LOG      default /home/box/logs/pg-backup-daemon.log

A stale apply-load file tmp/pg-cutover-*/cutover_mark.json does not count.
With a live mark, ensure-postgres failure, a missing DATABASE_URL, or
unreachable Postgres is an error — there is no silent SQLite fallback.
Without a live mark, ensure-postgres is skipped unless $PGDATA_DIR already
contains PG_VERSION; then it is best-effort (warn and continue on SQLite).
When the decision is postgres, a cron-free backup hook is launched unless
SHOPIFYSEO_SKIP_BACKUP_DAEMON=1. Cron install still runs, but cron is only
used if a cron daemon exists.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --decide-only|--dry-run) DECIDE_ONLY=1; shift ;;
    --foreground) FOREGROUND=1; shift ;;
    --skip-ensure-postgres) SKIP_ENSURE=1; shift ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${SHOPIFYSEO_SKIP_ENSURE_POSTGRES:-}" == "1" ]]; then
  SKIP_ENSURE=1
fi
if [[ "${SHOPIFYSEO_SKIP_BACKUP_CRON:-}" == "1" ]]; then
  SKIP_CRON=1
fi
if [[ "${SHOPIFYSEO_SKIP_BACKUP_DAEMON:-}" == "1" ]]; then
  SKIP_BACKUP_DAEMON=1
fi

# --decide-only is for tests / operators; skip box setup.
if [[ "$DECIDE_ONLY" -eq 1 ]]; then
  SKIP_ENSURE=1
  SKIP_CRON=1
  SKIP_BACKUP_DAEMON=1
fi

parse_database_url() {
  # Prints host, port, dbname, user on four lines. Never prints the password.
  python3 -c '
import os, sys
from urllib.parse import urlparse
url = os.environ.get("DATABASE_URL", "")
if not url.strip():
    sys.exit(2)
u = urlparse(url)
print(u.hostname or "")
print(u.port or 5432)
print((u.path or "").lstrip("/") or "")
print(u.username or "")
'
}

pg_reachable() {
  local host port dbname parsed
  if ! parsed="$(parse_database_url)"; then
    return 1
  fi
  readarray -t _parsed <<< "$parsed"
  host="${_parsed[0]:-127.0.0.1}"
  port="${_parsed[1]:-5432}"
  dbname="${_parsed[2]:-postgres}"
  if command -v pg_isready >/dev/null 2>&1; then
    pg_isready -h "$host" -p "$port" -d "$dbname" -q
    return $?
  fi
  python3 -c '
import os, sys
url = os.environ.get("DATABASE_URL", "")
try:
    import psycopg
    conn = psycopg.connect(url, connect_timeout=3)
    conn.close()
except Exception:
    sys.exit(1)
'
}

# shellcheck source=pg-listen-port.sh
source "$ROOT/scripts/pg-listen-port.sh"

# Written by decide() in this shell (not a subshell) so sourced pg.env sticks.
DECISION=""

emit_decision() {
  DECISION="$1"
  if [[ "$DECISION" == "sqlite" ]]; then
    echo "decision=sqlite DATABASE_URL=unset" >&2
  elif [[ "$DECISION" == "postgres" ]]; then
    echo "decision=postgres DATABASE_URL=set" >&2
  else
    echo "decision=error DATABASE_URL=unset" >&2
  fi
}

decide() {
  DECISION=""
  if [[ ! -f "$LIVE_MARK" ]]; then
    unset DATABASE_URL
    export -n DATABASE_URL 2>/dev/null || true
    emit_decision sqlite
    return 0
  fi
  if [[ ! -f "$PG_ENV" ]]; then
    echo "error: live mark present ($LIVE_MARK) but pg.env is missing: $PG_ENV" >&2
    emit_decision error
    return 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "$PG_ENV"
  set +a
  apply_listen_port_to_env
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "error: live mark present but DATABASE_URL is missing or empty; not falling back to SQLite" >&2
    unset DATABASE_URL
    emit_decision error
    return 1
  fi
  if ! pg_reachable; then
    echo "error: live mark present but PostgreSQL is unreachable; not falling back to SQLite" >&2
    emit_decision error
    return 1
  fi
  emit_decision postgres
  return 0
}

ENSURE_SH="${SHOPIFYSEO_ENSURE_POSTGRES_SH:-$ROOT/scripts/ensure-postgres.sh}"
BOX_PGDATA="${PGDATA_DIR:-/home/box/pgdata/17/main}"
MARK_PRESENT=0
if [[ -f "$LIVE_MARK" ]]; then
  MARK_PRESENT=1
fi

if [[ "$SKIP_ENSURE" -eq 0 ]]; then
  if [[ "$MARK_PRESENT" -eq 1 ]]; then
    "$ENSURE_SH" || exit $?
  elif [[ -f "$BOX_PGDATA/PG_VERSION" ]]; then
    if ! "$ENSURE_SH"; then
      echo "warning: ensure-postgres.sh failed; no live mark, continuing on SQLite" >&2
    fi
  else
    echo "note: no live mark and no existing cluster at $BOX_PGDATA; skipping ensure-postgres" >&2
  fi
fi
if [[ "$SKIP_CRON" -eq 0 ]]; then
  if ! "$ROOT/scripts/install-pg-backup-cron.sh"; then
    echo "warning: install-pg-backup-cron.sh failed; continuing" >&2
  fi
fi

set +e
decide
DECIDE_STATUS=$?
set -e

if [[ "$DECIDE_ONLY" -eq 1 ]]; then
  echo "$DECISION"
  exit "$DECIDE_STATUS"
fi

if [[ "$DECIDE_STATUS" -ne 0 ]]; then
  echo "$DECISION" >&2
  exit "$DECIDE_STATUS"
fi

if [[ "$DECISION" == "sqlite" ]]; then
  unset DATABASE_URL
  export -n DATABASE_URL 2>/dev/null || true
fi

if [[ "$DECISION" == "postgres" && "$SKIP_BACKUP_DAEMON" -eq 0 ]]; then
  BACKUP_DAEMON_SH="${SHOPIFYSEO_PG_BACKUP_DAEMON_SH:-$ROOT/scripts/pg-backup-daemon.sh}"
  BACKUP_DAEMON_LOG="${SHOPIFYSEO_PG_BACKUP_LOG:-/home/box/logs/pg-backup-daemon.log}"
  mkdir -p "$(dirname "$BACKUP_DAEMON_LOG")" 2>/dev/null || true
  if [[ -x "$BACKUP_DAEMON_SH" ]]; then
    # Never block or fail app startup. Cron is only used if a cron daemon exists.
    nohup "$BACKUP_DAEMON_SH" --ensure >>"$BACKUP_DAEMON_LOG" 2>&1 &
    echo "note: pg backup hook launched (pid $!; cron is used only if a cron daemon is running)" >&2
  else
    echo "warning: pg-backup-daemon.sh missing; continuing without nightly dump hook" >&2
  fi
fi

port_serving() {
  python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(0.4)
try:
    s.connect(('${UVICORN_HOST}', int('${UVICORN_PORT}')))
except OSError:
    sys.exit(1)
finally:
    s.close()
"
}

if port_serving; then
  echo "already serving on ${UVICORN_HOST}:${UVICORN_PORT}; not starting a second instance" >&2
  exit 0
fi

PYTHON_BIN="${SHOPIFYSEO_PYTHON:-$ROOT/.venv/bin/python3}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "error: $PYTHON_BIN is missing" >&2
  exit 1
fi

export PATH="$(dirname "$PYTHON_BIN"):$ROOT/.venv/bin:$PATH"
export PYTHONPATH="${ROOT}${PYTHONPATH:+:$PYTHONPATH}"
cd "$ROOT"
mkdir -p "$(dirname "$UVICORN_LOG")"

UVICORN_CMD=("$PYTHON_BIN" -m uvicorn backend.app.main:app --host "$UVICORN_HOST" --port "$UVICORN_PORT")

if [[ "$FOREGROUND" -eq 1 ]]; then
  exec "${UVICORN_CMD[@]}"
fi

nohup "${UVICORN_CMD[@]}" >>"$UVICORN_LOG" 2>&1 &
echo "started uvicorn pid $! log $UVICORN_LOG (backend=$DECISION)"
