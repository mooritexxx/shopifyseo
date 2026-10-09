#!/usr/bin/env bash
# Nightly custom-format pg_dump of the live Postgres catalog.
#
# No-op (exit 0) when the live mark is absent. Keeps the newest 7 dumps.
# Never prints DATABASE_URL.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_MARK="${SHOPIFYSEO_PG_LIVE_MARK:-/home/box/.config/shopifyseo/pg_live_cutover.json}"
PG_ENV="${SHOPIFYSEO_PG_ENV:-/home/box/.config/shopifyseo/pg.env}"
BACKUP_DIR="${SHOPIFYSEO_PG_BACKUP_DIR:-/home/box/backups/pg}"
KEEP="${SHOPIFYSEO_PG_BACKUP_KEEP:-7}"

usage() {
  cat <<'EOF'
pg-nightly-backup.sh — pg_dump -Fc when the live mark exists.

Usage: scripts/pg-nightly-backup.sh [--help]

  No live mark -> exit 0 without dumping.
  Live mark    -> source pg.env, dump to $SHOPIFYSEO_PG_BACKUP_DIR
                  (default /home/box/backups/pg), keep the newest 7 files.

Never prints DATABASE_URL. Installed by scripts/install-pg-backup-cron.sh.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ ! -f "$LIVE_MARK" ]]; then
  exit 0
fi

if [[ ! -f "$PG_ENV" ]]; then
  echo "error: live mark present but pg.env missing: $PG_ENV" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$PG_ENV"
set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "error: live mark present but DATABASE_URL is missing or empty" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$BACKUP_DIR/shopifyseo-${STAMP}.dump"
PARTIAL="$OUT.partial"
rm -f "$PARTIAL"

# Export libpq vars from DATABASE_URL without printing the URL or password.
eval "$(python3 -c '
import os, shlex
from urllib.parse import urlparse, unquote
u = urlparse(os.environ.get("DATABASE_URL", ""))
pairs = {
    "PGHOST": u.hostname or "127.0.0.1",
    "PGPORT": str(u.port or 5432),
    "PGDATABASE": (u.path or "").lstrip("/") or "shopifyseo",
    "PGUSER": u.username or "",
    "PGPASSWORD": unquote(u.password) if u.password else "",
}
for key, value in pairs.items():
    if value:
        print(f"export {key}={shlex.quote(value)}")
')"

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "error: pg_dump not found on PATH" >&2
  exit 1
fi

if ! pg_dump -Fc -f "$PARTIAL"; then
  rm -f "$PARTIAL"
  unset PGPASSWORD
  echo "error: pg_dump failed (DATABASE_URL not printed)" >&2
  exit 1
fi
unset PGPASSWORD

if [[ ! -s "$PARTIAL" ]]; then
  rm -f "$PARTIAL"
  echo "error: pg_dump wrote an empty file (DATABASE_URL not printed)" >&2
  exit 1
fi
mv "$PARTIAL" "$OUT"

# Keep the newest $KEEP completed dumps, ordered by the stamp in the filename.
mapfile -t dumps < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'shopifyseo-*.dump' | sort -r)
if [[ "${#dumps[@]}" -gt "$KEEP" ]]; then
  for stale in "${dumps[@]:$KEEP}"; do
    rm -f "$stale"
  done
fi

echo "wrote $OUT (kept up to $KEEP newest dumps)"
