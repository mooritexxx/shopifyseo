#!/usr/bin/env bash
# Build the SPA and restart the FastAPI app on http://127.0.0.1:8000/app/
# Usage (from repo root):
#   ./scripts/dev-restart-local.sh           # npm run build + uvicorn (foreground)
#   ./scripts/dev-restart-local.sh --rebuild # clean Vite cache then build + uvicorn
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if lsof -ti:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  kill -9 $(lsof -ti:8000 -sTCP:LISTEN) 2>/dev/null || true
  sleep 1
fi

cd "$ROOT/frontend"
if [[ "${1:-}" == "--rebuild" ]]; then
  npm run rebuild
else
  npm run build
fi

cd "$ROOT"
echo "Open http://127.0.0.1:8000/app/ — hard refresh (⌘⇧R) after UI changes."
# StatReload restarts kill the background sync thread. Exclude .cursor (debug NDJSON, etc.) and
# debounce rapid saves so long syncs are less likely to be interrupted during local dev.
exec env PYTHONPATH=. uvicorn backend.app.main:app --reload \
  --reload-delay 2 \
  --reload-exclude ".cursor/*" \
  --host 127.0.0.1 --port 8000
