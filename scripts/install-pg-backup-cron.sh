#!/usr/bin/env bash
# Idempotent crontab install for scripts/pg-nightly-backup.sh.
#
# After a box reset, crontab is gone; run this again (scripts/start-app.sh
# calls it). Re-running does not add a duplicate line.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_SH="${SHOPIFYSEO_PG_BACKUP_SH:-$ROOT/scripts/pg-nightly-backup.sh}"
SCHEDULE="${SHOPIFYSEO_PG_BACKUP_CRON:-15 3 * * *}"
MARKER="# shopifyseo-pg-nightly-backup"

usage() {
  cat <<'EOF'
install-pg-backup-cron.sh — install the nightly pg_dump crontab once.

Usage: scripts/install-pg-backup-cron.sh [--help]

  Adds one crontab line for scripts/pg-nightly-backup.sh if it is not already
  present. Re-runs are a no-op (no duplicate lines).

  Cron itself is wiped on a box reset. scripts/start-app.sh calls this
  installer so the job comes back after restore.

Environment:
  SHOPIFYSEO_PG_BACKUP_CRON   schedule (default: 15 3 * * *)
  SHOPIFYSEO_PG_BACKUP_SH     script path (default: this repo's backup script)
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

if [[ ! -x "$BACKUP_SH" ]]; then
  echo "error: backup script is not executable: $BACKUP_SH" >&2
  exit 1
fi

LINE="${SCHEDULE} ${BACKUP_SH}"

existing=""
if existing="$(crontab -l 2>/dev/null)"; then
  :
else
  existing=""
fi

warn_if_cron_missing() {
  if ! pgrep -x cron >/dev/null 2>&1; then
    echo "warning: no cron daemon process found (pgrep -x cron); crontab may be installed but jobs will not run until cron is running" >&2
  fi
}

if printf '%s\n' "$existing" | grep -F "$MARKER" >/dev/null 2>&1; then
  echo "nightly pg_dump cron already installed"
  warn_if_cron_missing
  exit 0
fi
if printf '%s\n' "$existing" | grep -F "$BACKUP_SH" >/dev/null 2>&1; then
  echo "nightly pg_dump cron already installed"
  warn_if_cron_missing
  exit 0
fi

{
  if [[ -n "$existing" ]]; then
    printf '%s\n' "$existing"
  fi
  printf '%s\n' "$MARKER"
  printf '%s\n' "$LINE"
} | crontab -

echo "installed nightly pg_dump cron: $LINE"
warn_if_cron_missing
