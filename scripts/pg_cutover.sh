#!/usr/bin/env bash
# SQLite → PostgreSQL cutover runner (plan 8).
#
# backup → (optional) sqlite pre-fix → pgloader → fixups → optional orphan
# delete → NOT VALID constraints → identity resync → ANALYZE → verify
#
# This script does NOT:
#   - set or persist live DATABASE_URL
#   - source pg.env into the parent shell / uvicorn
#   - restart or reconfigure start_app.sh
#   - delete cluster_keywords orphans unless --delete-cluster-orphans
#   - VALIDATE FKs unless --validate-fks
#
# Live deploy stays on SQLite until Salar approves the app switch.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$ROOT/scripts/pg_cutover"
PYTHONPATH="${PYTHONPATH:-}:$ROOT"
export PYTHONPATH

MODE="dry-run"
DELETE_ORPHANS=0
VALIDATE_FKS=0
SKIP_PGLOADER=0
SKIP_VERIFY=0
FAIL_ON_ORPHANS=0
ENV_FILE="${PG_ENV_FILE:-$HOME/.config/shopifyseo/pg.env}"
SQLITE_PATH="${SQLITE_PATH:-}"
WORK_DIR=""
PGLOADER_BIN="${PGLOADER:-pgloader}"

usage() {
  cat <<'EOF'
Usage: scripts/pg_cutover.sh [options]

  --env-file PATH              CoS pg.env (default: $HOME/.config/shopifyseo/pg.env)
  --sqlite PATH                Live SQLite catalog (copied; never written)
  --work-dir PATH              Working directory (default: ./tmp/pg-cutover-<ts>)
  --dry-run                    Load into CUTOVER_DATABASE_URL / PGDATABASE (default)
  --apply-load                 Load into CUTOVER_LIVE_DATABASE (still no app flip)
  --delete-cluster-orphans     DELETE cluster_keywords rows with missing clusters.id
  --validate-fks               VALIDATE CONSTRAINT after adding FKs NOT VALID
  --fail-on-orphans            verify_values fails if orphans remain
  --skip-pgloader              Run fixups/verify only (pg already loaded)
  --skip-verify                Skip count/value verification
  --help

Env (from --env-file, never committed):
  CUTOVER_DATABASE_URL   psycopg URL for the dry-run database
  PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE
  SQLITE_PATH            default --sqlite
  CUTOVER_LIVE_DATABASE  database name for --apply-load (default: shopifyseo)
  PGLOADER               pgloader binary

This script never exports DATABASE_URL for uvicorn and never starts the app.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --sqlite) SQLITE_PATH="$2"; shift 2 ;;
    --work-dir) WORK_DIR="$2"; shift 2 ;;
    --dry-run) MODE="dry-run"; shift ;;
    --apply-load) MODE="apply-load"; shift ;;
    --delete-cluster-orphans) DELETE_ORPHANS=1; shift ;;
    --validate-fks) VALIDATE_FKS=1; shift ;;
    --fail-on-orphans) FAIL_ON_ORPHANS=1; shift ;;
    --skip-pgloader) SKIP_PGLOADER=1; shift ;;
    --skip-verify) SKIP_VERIFY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

redact_url() {
  echo "$1" | sed -E 's#://([^:/@]+):[^@]+@#://\1:***@#'
}

if [[ -f "$ENV_FILE" ]]; then
  echo "sourcing env file $ENV_FILE (this process only; not uvicorn)"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "note: env file $ENV_FILE not found; using current environment"
fi

# Refuse to treat DATABASE_URL as the live app switch. Prefer CUTOVER_*.
if [[ -n "${DATABASE_URL:-}" && -z "${CUTOVER_DATABASE_URL:-}" ]]; then
  echo "warning: DATABASE_URL is set in this process. The cutover script will use" >&2
  echo "warning: it only as the *load target*. It does not write it into start_app.sh." >&2
  CUTOVER_DATABASE_URL="$DATABASE_URL"
fi

if [[ -z "${CUTOVER_DATABASE_URL:-}" ]]; then
  if [[ -z "${PGHOST:-}" || -z "${PGUSER:-}" || -z "${PGDATABASE:-}" ]]; then
    echo "error: set CUTOVER_DATABASE_URL or PGHOST/PGUSER/PGDATABASE in pg.env" >&2
    exit 2
  fi
  if [[ -n "${PGPASSWORD:-}" ]]; then
    CUTOVER_DATABASE_URL="postgresql://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT:-5432}/${PGDATABASE}"
  else
    CUTOVER_DATABASE_URL="postgresql://${PGUSER}@${PGHOST}:${PGPORT:-5432}/${PGDATABASE}"
  fi
fi

if [[ "$MODE" == "apply-load" ]]; then
  LIVE_DB="${CUTOVER_LIVE_DATABASE:-shopifyseo}"
  CUTOVER_DATABASE_URL="$(echo "$CUTOVER_DATABASE_URL" | sed -E "s#/[^/?]+(\\?.*)?\$#/${LIVE_DB}\\1#")"
  echo "apply-load target database: $LIVE_DB (app DATABASE_URL is still not set by this script)"
else
  echo "dry-run load target: $(redact_url "$CUTOVER_DATABASE_URL")"
fi

if [[ -z "$SQLITE_PATH" ]]; then
  if [[ -f "$ROOT/shopify_catalog.sqlite3" ]]; then
    SQLITE_PATH="$ROOT/shopify_catalog.sqlite3"
  else
    echo "error: pass --sqlite PATH" >&2
    exit 2
  fi
fi
if [[ ! -f "$SQLITE_PATH" ]]; then
  echo "error: sqlite file not found: $SQLITE_PATH" >&2
  exit 2
fi

if [[ -z "$WORK_DIR" ]]; then
  WORK_DIR="$ROOT/tmp/pg-cutover-$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$WORK_DIR"
echo "work dir: $WORK_DIR"

echo "==> checkpoint + copy SQLite (live file is not modified after this)"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$SQLITE_PATH" "PRAGMA wal_checkpoint(FULL);" >/dev/null || true
fi
SQLITE_COPY="$WORK_DIR/catalog.copy.sqlite3"
cp -p "$SQLITE_PATH" "$SQLITE_COPY"
if [[ -f "${SQLITE_PATH}-wal" ]]; then
  cp -p "${SQLITE_PATH}-wal" "${SQLITE_COPY}-wal"
fi
if [[ -f "${SQLITE_PATH}-shm" ]]; then
  cp -p "${SQLITE_PATH}-shm" "${SQLITE_COPY}-shm"
fi
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$SQLITE_COPY" "PRAGMA wal_checkpoint(FULL);" >/dev/null || true
fi

echo "==> pre-load SQLite mixed-type / empty-string fixes (working copy only)"
python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "$ROOT")
from shopifyseo.cutover.sqlite_pre_fix import fix_sqlite_copy
report = fix_sqlite_copy("$SQLITE_COPY")
print("pre-fix empty_numeric:", report["empty_numeric"] or "{}")
print("pre-fix epoch_text:", report["epoch_text"] or "{}")
PY

if [[ "$SKIP_PGLOADER" -eq 0 ]]; then
  if ! command -v "$PGLOADER_BIN" >/dev/null 2>&1; then
    echo "error: $PGLOADER_BIN not found. Install pgloader or pass --skip-pgloader." >&2
    exit 2
  fi
  LOAD_FILE="$WORK_DIR/shopifyseo.load"
  SQLITE_URI="sqlite:///${SQLITE_COPY}"
  python3 - <<PY
from pathlib import Path
src = Path("$ASSETS/shopifyseo.load").read_text()
src = src.replace("__SQLITE_URI__", "$SQLITE_URI")
src = src.replace("__POSTGRES_URI__", "$CUTOVER_DATABASE_URL")
Path("$LOAD_FILE").write_text(src)
print("wrote $LOAD_FILE")
PY
  echo "==> pgloader"
  "$PGLOADER_BIN" "$LOAD_FILE"
else
  echo "==> skipping pgloader (--skip-pgloader)"
fi

psql_url() {
  psql "$CUTOVER_DATABASE_URL" -v ON_ERROR_STOP=1 "$@"
}

echo "==> post_load_fixups.sql"
psql_url -f "$ASSETS/post_load_fixups.sql"

echo "==> cluster_keywords orphan report"
ORPHANS="$(psql_url -Atc "SELECT COUNT(*) FROM cluster_keywords ck WHERE NOT EXISTS (SELECT 1 FROM clusters c WHERE c.id = ck.cluster_id);" 2>/dev/null || echo "0")"
echo "cluster_keywords orphans: $ORPHANS (kept unless --delete-cluster-orphans)"
if [[ "$DELETE_ORPHANS" -eq 1 ]]; then
  echo "==> deleting cluster_keywords orphans (--delete-cluster-orphans)"
  psql_url -c "DELETE FROM cluster_keywords ck WHERE NOT EXISTS (SELECT 1 FROM clusters c WHERE c.id = ck.cluster_id);"
else
  echo "orphans retained; FK stays NOT VALID"
fi

echo "==> post_load_constraints.sql (FKs NOT VALID)"
psql_url -f "$ASSETS/post_load_constraints.sql"

if [[ "$VALIDATE_FKS" -eq 1 ]]; then
  echo "==> VALIDATE CONSTRAINT (will fail if orphans remain)"
  psql_url -c "ALTER TABLE cluster_keywords VALIDATE CONSTRAINT cluster_keywords_cluster_id_fkey;"
fi

echo "==> identity / sequence reset"
python3 "$ASSETS/resync_sequences.py" --postgres-url "$CUTOVER_DATABASE_URL"

echo "==> ANALYZE"
psql_url -c "ANALYZE;"

if [[ "$SKIP_VERIFY" -eq 0 ]]; then
  echo "==> verify_counts"
  python3 "$ASSETS/verify_counts.py" --sqlite "$SQLITE_COPY" --postgres-url "$CUTOVER_DATABASE_URL"
  echo "==> verify_values"
  VERIFY_ARGS=(--postgres-url "$CUTOVER_DATABASE_URL" --sqlite "$SQLITE_COPY")
  if [[ "$FAIL_ON_ORPHANS" -eq 1 ]]; then
    VERIFY_ARGS+=(--fail-on-orphans)
  fi
  python3 "$ASSETS/verify_values.py" "${VERIFY_ARGS[@]}"
fi

python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "$ROOT")
from shopifyseo.cutover.delta import write_cutover_mark
write_cutover_mark(
    Path("$WORK_DIR") / "cutover_mark.json",
    sqlite_backup="$SQLITE_COPY",
    postgres_target="$(redact_url "$CUTOVER_DATABASE_URL")",
    extra={"mode": "$MODE", "delete_cluster_orphans": $DELETE_ORPHANS, "validate_fks": $VALIDATE_FKS},
)
print("wrote $WORK_DIR/cutover_mark.json")
PY

echo
echo "Done ($MODE). Live uvicorn was not restarted and DATABASE_URL was not persisted."
echo "Rollback helper: PYTHONPATH=. python3 scripts/pg_to_sqlite_delta.py --mark $WORK_DIR/cutover_mark.json --sqlite-copy /path/to/copy.sqlite3 --create-copy-from $SQLITE_COPY --postgres-url \$CUTOVER_DATABASE_URL"
echo "See docs/pg-cutover.md"
