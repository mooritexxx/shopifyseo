#!/usr/bin/env bash
# Cron-free nightly pg_dump trigger for the box.
#
# This box often has no cron daemon. scripts/install-pg-backup-cron.sh is
# still useful when cron exists; this script is what actually runs the dump
# after start-app.sh when the live mark says postgres.
#
# --ensure (called from start-app.sh):
#   - if the newest successful dump is older than 24h (or missing), run
#     scripts/pg-nightly-backup.sh once
#   - start a sleep-loop daemon guarded by a pidfile (never twice)
#   - always exit 0 so app startup cannot fail
#
# The loop exits when the live mark disappears, on TERM/INT, or if the
# pidfile is no longer ours. Dumps keep the existing .partial-then-mv and
# newest-7 pruning in pg-nightly-backup.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIVE_MARK="${SHOPIFYSEO_PG_LIVE_MARK:-/home/box/.config/shopifyseo/pg_live_cutover.json}"
BACKUP_SH="${SHOPIFYSEO_PG_BACKUP_SH:-$ROOT/scripts/pg-nightly-backup.sh}"
BACKUP_DIR="${SHOPIFYSEO_PG_BACKUP_DIR:-/home/box/backups/pg}"
PIDFILE="${SHOPIFYSEO_PG_BACKUP_PIDFILE:-/home/box/logs/pg-backup-daemon.pid}"
LOG="${SHOPIFYSEO_PG_BACKUP_LOG:-/home/box/logs/pg-backup-daemon.log}"
MAX_AGE="${SHOPIFYSEO_PG_BACKUP_MAX_AGE_SECONDS:-86400}"
LOOP_SECONDS="${SHOPIFYSEO_PG_BACKUP_LOOP_SECONDS:-3600}"
MODE="ensure"

usage() {
  cat <<'EOF'
pg-backup-daemon.sh — run pg-nightly-backup.sh without a cron daemon.

Usage: scripts/pg-backup-daemon.sh [--ensure|--loop|--help]

  --ensure   Default. If the last successful dump is older than 24h, run
             one backup. Then start the sleep-loop if it is not already
             running. Always exits 0 (never blocks or fails app start).
  --loop     Internal: pidfile-guarded sleep loop. Exits when the live
             mark is gone or on TERM/INT.

Only meaningful when the live mark exists (postgres). Cron
(scripts/install-pg-backup-cron.sh) is used only if a cron daemon is
running; this hook is the path that works without cron.

Environment (all overridable for tests; defaults are box paths):
  SHOPIFYSEO_PG_LIVE_MARK
  SHOPIFYSEO_PG_BACKUP_SH
  SHOPIFYSEO_PG_BACKUP_DIR
  SHOPIFYSEO_PG_BACKUP_PIDFILE   default /home/box/logs/pg-backup-daemon.pid
  SHOPIFYSEO_PG_BACKUP_LOG       default /home/box/logs/pg-backup-daemon.log
  SHOPIFYSEO_PG_BACKUP_MAX_AGE_SECONDS   default 86400
  SHOPIFYSEO_PG_BACKUP_LOOP_SECONDS      default 3600
EOF
}

log() {
  local line
  line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
  mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
  printf '%s\n' "$line" >>"$LOG" 2>/dev/null || true
  printf '%s\n' "$line" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --ensure) MODE="ensure"; shift ;;
    --loop) MODE="loop"; shift ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

newest_dump_age_seconds() {
  python3 -c '
import os, sys, time
from pathlib import Path
backup_dir = Path(sys.argv[1])
if not backup_dir.is_dir():
    sys.exit(2)
dumps = sorted(backup_dir.glob("shopifyseo-*.dump"))
if not dumps:
    sys.exit(2)
newest = max(dumps, key=lambda p: p.stat().st_mtime)
age = int(time.time() - newest.stat().st_mtime)
print(age)
' "$BACKUP_DIR"
}

run_backup_if_stale() {
  if [[ ! -f "$LIVE_MARK" ]]; then
    return 0
  fi
  local age
  if age="$(newest_dump_age_seconds)"; then
    if [[ "$age" -lt "$MAX_AGE" ]]; then
      log "newest dump is ${age}s old (< ${MAX_AGE}s); skipping"
      return 0
    fi
  fi
  log "running $BACKUP_SH (last dump missing or older than ${MAX_AGE}s)"
  if ! "$BACKUP_SH"; then
    log "warning: pg-nightly-backup.sh failed (app start is unaffected)"
  fi
}

pidfile_running() {
  if [[ ! -f "$PIDFILE" ]]; then
    return 1
  fi
  local old
  old="$(tr -d '[:space:]' < "$PIDFILE" 2>/dev/null || true)"
  if [[ "$old" =~ ^[0-9]+$ ]] && kill -0 "$old" >/dev/null 2>&1; then
    return 0
  fi
  rm -f "$PIDFILE"
  return 1
}

start_loop_background() {
  if pidfile_running; then
    log "backup loop already running (pid $(cat "$PIDFILE"))"
    return 0
  fi
  mkdir -p "$(dirname "$PIDFILE")" "$(dirname "$LOG")" 2>/dev/null || true
  nohup "$0" --loop >>"$LOG" 2>&1 &
  log "started backup loop pid $!"
}

run_loop() {
  if [[ ! -f "$LIVE_MARK" ]]; then
    log "no live mark; loop exiting"
    exit 0
  fi
  mkdir -p "$(dirname "$PIDFILE")" 2>/dev/null || true
  if pidfile_running; then
    log "backup loop already running (pid $(cat "$PIDFILE")); exiting extra loop"
    exit 0
  fi
  printf '%s\n' "$$" > "$PIDFILE"
  cleanup() {
    if [[ -f "$PIDFILE" ]] && [[ "$(tr -d '[:space:]' < "$PIDFILE" 2>/dev/null || true)" == "$$" ]]; then
      rm -f "$PIDFILE"
    fi
  }
  trap cleanup EXIT INT TERM
  log "backup loop running pid $$ interval ${LOOP_SECONDS}s"
  while true; do
    sleep "$LOOP_SECONDS" || true
    if [[ ! -f "$LIVE_MARK" ]]; then
      log "live mark removed; loop exiting"
      exit 0
    fi
    if [[ -f "$PIDFILE" ]] && [[ "$(tr -d '[:space:]' < "$PIDFILE" 2>/dev/null || true)" != "$$" ]]; then
      log "pidfile no longer ours; loop exiting"
      exit 0
    fi
    run_backup_if_stale || true
  done
}

if [[ "$MODE" == "loop" ]]; then
  run_loop
  exit 0
fi

# --ensure: never fail the caller.
set +e
if [[ -f "$LIVE_MARK" ]]; then
  run_backup_if_stale
  start_loop_background
else
  log "no live mark; backup hook is a no-op"
fi
exit 0
