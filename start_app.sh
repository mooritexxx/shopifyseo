#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Auto-activate virtual environment if it exists and isn't active
if [ -z "${VIRTUAL_ENV:-}" ]; then
  if [ -d "$ROOT_DIR/.venv" ]; then
    echo "Activating virtual environment (.venv)..."
    source "$ROOT_DIR/.venv/bin/activate"
  elif [ -d "$ROOT_DIR/venv" ]; then
    echo "Activating virtual environment (venv)..."
    source "$ROOT_DIR/venv/bin/activate"
  fi
fi

# Ensure Python dependencies are installed/up-to-date
echo "Verifying Python dependencies..."
python3 -m pip install -r "$ROOT_DIR/backend/requirements.txt"

cd "$ROOT_DIR/frontend"
npm run build

cd "$ROOT_DIR"
exec python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
