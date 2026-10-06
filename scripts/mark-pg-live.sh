#!/usr/bin/env bash
# Write or remove the production live-cutover mark.
#
# Run this only after the post-flip sweep passes. scripts/start-app.sh treats
# this file (not tmp/pg-cutover-*/cutover_mark.json) as the live switch.
#
# Rollback: scripts/mark-pg-live.sh --remove && scripts/start-app.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_MARK="${SHOPIFYSEO_PG_LIVE_MARK:-/home/box/.config/shopifyseo/pg_live_cutover.json}"
PG_ENV="${SHOPIFYSEO_PG_ENV:-/home/box/.config/shopifyseo/pg.env}"
REMOVE=0
NOTES=""

usage() {
  cat <<'EOF'
mark-pg-live.sh — write or remove the live Postgres cutover mark.

Usage: scripts/mark-pg-live.sh [options]

  --help, -h     This help
  --remove       Delete the live mark (rollback to SQLite on next start-app.sh)
  --notes TEXT   Stored in the mark JSON (no secrets)

Environment:
  SHOPIFYSEO_PG_LIVE_MARK   mark path (default /home/box/.config/shopifyseo/pg_live_cutover.json)
  SHOPIFYSEO_PG_ENV         pg.env (default /home/box/.config/shopifyseo/pg.env)

Refuses to write the mark if pg.env or DATABASE_URL is missing, or if
Postgres is unreachable. The JSON stores timestamp, host, dbname, user, and
git SHA — never a password.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --remove) REMOVE=1; shift ;;
    --notes) NOTES="$2"; shift 2 ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$REMOVE" -eq 1 ]]; then
  if [[ -f "$LIVE_MARK" ]]; then
    rm -f "$LIVE_MARK"
    echo "removed live mark $LIVE_MARK"
  else
    echo "live mark already absent: $LIVE_MARK"
  fi
  exit 0
fi

if [[ ! -f "$PG_ENV" ]]; then
  echo "error: pg.env missing: $PG_ENV" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$PG_ENV"
set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "error: DATABASE_URL is missing or empty in pg.env; refusing to write the live mark" >&2
  exit 1
fi

parse_database_url() {
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

if ! parsed="$(parse_database_url)"; then
  echo "error: could not parse DATABASE_URL (value not printed)" >&2
  exit 1
fi
readarray -t _parsed <<< "$parsed"
HOST="${_parsed[0]:-127.0.0.1}"
PORT="${_parsed[1]:-5432}"
DBNAME="${_parsed[2]:-}"
USER_NAME="${_parsed[3]:-}"

if command -v pg_isready >/dev/null 2>&1; then
  if ! pg_isready -h "$HOST" -p "$PORT" -d "$DBNAME" -q; then
    echo "error: PostgreSQL is unreachable; refusing to write the live mark" >&2
    exit 1
  fi
else
  if ! python3 -c '
import os, sys
try:
    import psycopg
    conn = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3)
    conn.close()
except Exception:
    sys.exit(1)
'; then
    echo "error: PostgreSQL is unreachable; refusing to write the live mark" >&2
    exit 1
  fi
fi

GIT_SHA=""
if git -C "$ROOT" rev-parse HEAD >/dev/null 2>&1; then
  GIT_SHA="$(git -C "$ROOT" rev-parse HEAD)"
fi

mkdir -p "$(dirname "$LIVE_MARK")"
# Pass fields via env so a password never appears on the command line.
SHOPIFYSEO_MARK_PATH="$LIVE_MARK" \
SHOPIFYSEO_MARK_HOST="$HOST" \
SHOPIFYSEO_MARK_PORT="$PORT" \
SHOPIFYSEO_MARK_DBNAME="$DBNAME" \
SHOPIFYSEO_MARK_USER="$USER_NAME" \
SHOPIFYSEO_MARK_SHA="$GIT_SHA" \
SHOPIFYSEO_MARK_NOTES="$NOTES" \
python3 -c '
import json, os
from datetime import datetime, timezone
payload = {
    "marked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "host": os.environ.get("SHOPIFYSEO_MARK_HOST", ""),
    "port": int(os.environ.get("SHOPIFYSEO_MARK_PORT") or 5432),
    "dbname": os.environ.get("SHOPIFYSEO_MARK_DBNAME", ""),
    "user": os.environ.get("SHOPIFYSEO_MARK_USER", ""),
    "git_sha": os.environ.get("SHOPIFYSEO_MARK_SHA", ""),
    "notes": os.environ.get("SHOPIFYSEO_MARK_NOTES", ""),
}
path = os.environ["SHOPIFYSEO_MARK_PATH"]
with open(path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2)
    fh.write("\n")
print("wrote", path)
'
