#!/usr/bin/env bash
# Build the SPA and restart the FastAPI app on http://127.0.0.1:8000/app/
# Usage (from repo root):
#   ./scripts/dev-restart-local.sh           # npm run build + uvicorn (foreground)
#   ./scripts/dev-restart-local.sh --rebuild # clean Vite cache then build + uvicorn
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

set +e
python3 - <<'PY'
import socket
import sys

s = socket.socket()
try:
    s.bind(("127.0.0.1", 0))
except PermissionError:
    sys.exit(2)
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
bind_status=$?
set -e
if [[ "$bind_status" == "2" ]]; then
  echo "This shell is not allowed to bind localhost ports, so it cannot restart uvicorn safely."
  echo "Run this script from a normal Terminal session, or grant this agent local-network/server permissions."
  exit 1
fi

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
exec env PYTHONPATH=. python3 -m uvicorn backend.app.main:app --reload \
  --reload-delay 2 \
  --reload-exclude ".cursor/*" \
  --host 127.0.0.1 --port 8000
