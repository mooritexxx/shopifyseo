# Shared listen_port rewrite for start-app.sh and pg-nightly-backup.sh.
# Source this file; do not execute it.
#
# After sourcing pg.env, honor the port ensure-postgres.sh actually bound.
# Rewrites process PGPORT / DATABASE_URL only when the URL host is loopback
# and there is no libpq host= query (unix-socket URLs stay untouched).
# Never prints the URL.

apply_listen_port_to_env() {
  local port_file="${SHOPIFYSEO_LISTEN_PORT_FILE:-}"
  if [[ -z "$port_file" ]]; then
    port_file="$(dirname "${PGDATA_DIR:-/home/box/pgdata/17/main}")/listen_port"
  fi
  if [[ ! -f "$port_file" ]]; then
    return 0
  fi
  local port
  port="$(tr -d ' \t\r\n' < "$port_file" 2>/dev/null || true)"
  if [[ ! "$port" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if [[ -n "${DATABASE_URL:-}" ]]; then
    if ! SHOPIFYSEO_LISTEN_PORT_CHECK_URL="$DATABASE_URL" python3 -c '
import os
from urllib.parse import parse_qs, urlparse
url = os.environ.get("SHOPIFYSEO_LISTEN_PORT_CHECK_URL", "")
if not url.strip():
    raise SystemExit(0)
u = urlparse(url)
qs = parse_qs(u.query, keep_blank_values=True)
if any(key.lower() == "host" for key in qs):
    raise SystemExit(1)
host = (u.hostname or "").lower()
if host not in ("127.0.0.1", "localhost", "::1"):
    raise SystemExit(1)
'; then
      return 0
    fi
  fi
  export PGPORT="$port"
  if [[ -n "${DATABASE_URL:-}" ]]; then
    DATABASE_URL="$(SHOPIFYSEO_LISTEN_PORT="$port" python3 -c '
import os
from urllib.parse import urlparse, urlunparse
url = os.environ.get("DATABASE_URL", "")
port = os.environ["SHOPIFYSEO_LISTEN_PORT"]
if not url.strip():
    raise SystemExit
u = urlparse(url)
host = u.hostname or "127.0.0.1"
auth = ""
if u.username:
    auth = u.username
    if u.password is not None:
        auth += ":" + u.password
    auth += "@"
print(urlunparse((u.scheme, f"{auth}{host}:{port}", u.path, u.params, u.query, u.fragment)), end="")
')"
    export DATABASE_URL
  fi
}
