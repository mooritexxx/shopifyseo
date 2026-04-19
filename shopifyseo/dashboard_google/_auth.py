"""Google OAuth, service tokens, settings, and generic HTTP helpers.

Mutable package-level globals (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
GOOGLE_REDIRECT_URI, GOOGLE_AUTH_STATE) live in the package __init__.py so
that external code can reassign them via ``dg.GOOGLE_CLIENT_ID = …``.
Functions here that need those values use _pkg() to look them up at call time,
avoiding circular imports while always seeing the current value.
"""

import json
import secrets
import sqlite3
import sys
import threading
import time

from ..dashboard_http import HttpRequestError, request_json

# Single-flight refresh + in-process token cache. Without this, ~30 PageSpeed
# workers each do a SELECT on every HTTP call, and all race to POST to /token
# at the same instant when the access token expires (Google rate-limits that).
_TOKEN_REFRESH_LOCKS: dict[str, threading.Lock] = {}
_TOKEN_REFRESH_LOCKS_GUARD = threading.Lock()

_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, tuple[str, int]] = {}  # service -> (access_token, expires_at_epoch)
_TOKEN_FRESHNESS_MARGIN_SECONDS = 60


def _refresh_lock_for(service: str) -> threading.Lock:
    with _TOKEN_REFRESH_LOCKS_GUARD:
        lock = _TOKEN_REFRESH_LOCKS.get(service)
        if lock is None:
            lock = threading.Lock()
            _TOKEN_REFRESH_LOCKS[service] = lock
        return lock


def _token_cache_get(service: str, now_ts: int) -> str | None:
    with _TOKEN_CACHE_LOCK:
        entry = _TOKEN_CACHE.get(service)
    if not entry:
        return None
    access_token, expires_at = entry
    if not access_token or expires_at <= now_ts + _TOKEN_FRESHNESS_MARGIN_SECONDS:
        return None
    return access_token


def _token_cache_put(service: str, access_token: str, expires_at: int | None) -> None:
    with _TOKEN_CACHE_LOCK:
        if access_token and expires_at:
            _TOKEN_CACHE[service] = (access_token, int(expires_at))
        else:
            _TOKEN_CACHE.pop(service, None)


def invalidate_token_cache(service: str | None = None) -> None:
    """Drop one or all cached access tokens (tests, manual reconnect)."""
    with _TOKEN_CACHE_LOCK:
        if service is None:
            _TOKEN_CACHE.clear()
        else:
            _TOKEN_CACHE.pop(service, None)


def _pkg():
    """Return the shopifyseo.dashboard_google package namespace (the __init__ module)."""
    return sys.modules["shopifyseo.dashboard_google"]


# -- Service tokens & settings ------------------------------------------------

def get_service_token(conn: sqlite3.Connection, service: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM service_tokens WHERE service = ?", (service,)).fetchone()


def set_service_token(conn: sqlite3.Connection, service: str, payload: dict) -> None:
    # Refresh-token responses often omit `scope`; never wipe previously granted scopes.
    merged = dict(payload)
    existing = get_service_token(conn, service)
    if not (merged.get("scope") or "").strip() and existing and (existing["scope"] or "").strip():
        merged["scope"] = existing["scope"]
    payload = merged
    expires_in = int(payload.get("expires_in", 0) or 0)
    expires_at = int(time.time()) + expires_in if expires_in else None
    conn.execute(
        """
        INSERT INTO service_tokens(service, access_token, refresh_token, token_type, expires_at, scope, raw_json, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(service) DO UPDATE SET
          access_token = excluded.access_token,
          refresh_token = COALESCE(excluded.refresh_token, service_tokens.refresh_token),
          token_type = excluded.token_type,
          expires_at = excluded.expires_at,
          scope = excluded.scope,
          raw_json = excluded.raw_json,
          updated_at = CURRENT_TIMESTAMP
        """,
        (
            service,
            payload.get("access_token", ""),
            payload.get("refresh_token"),
            payload.get("token_type", ""),
            expires_at,
            payload.get("scope", ""),
            json.dumps(payload, ensure_ascii=True),
        ),
    )
    conn.commit()
    _token_cache_put(service, payload.get("access_token", ""), expires_at)


def get_service_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM service_settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    val = row["value"]
    # SQLite stores missing settings as no row; legacy rows may have NULL value.
    return default if val is None else val


def set_service_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO service_settings(key, value, updated_at)
        VALUES(?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
          value = excluded.value,
          updated_at = CURRENT_TIMESTAMP
        """,
        (key, value),
    )
    conn.commit()


# -- OAuth --------------------------------------------------------------------

def google_configured() -> bool:
    pkg = _pkg()
    return bool(pkg.GOOGLE_CLIENT_ID and pkg.GOOGLE_CLIENT_SECRET)


def new_oauth_state() -> str:
    pkg = _pkg()
    pkg.GOOGLE_AUTH_STATE["value"] = secrets.token_urlsafe(24)
    return pkg.GOOGLE_AUTH_STATE["value"]


def google_token_request(data: dict) -> dict:
    return request_json("https://oauth2.googleapis.com/token", method="POST", form=data)


def google_exchange_code(code: str) -> dict:
    pkg = _pkg()
    return google_token_request(
        {
            "code": code,
            "client_id": pkg.GOOGLE_CLIENT_ID,
            "client_secret": pkg.GOOGLE_CLIENT_SECRET,
            "redirect_uri": pkg.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        }
    )


def google_refresh_token(refresh_token: str) -> dict:
    pkg = _pkg()
    return google_token_request(
        {
            "refresh_token": refresh_token,
            "client_id": pkg.GOOGLE_CLIENT_ID,
            "client_secret": pkg.GOOGLE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        }
    )


def get_google_access_token(conn: sqlite3.Connection) -> str:
    service = "search_console"

    def _fresh_from_row(token: sqlite3.Row | None, now_ts: int) -> str | None:
        if not token or not token["access_token"] or not token["expires_at"]:
            return None
        if token["expires_at"] <= now_ts + _TOKEN_FRESHNESS_MARGIN_SECONDS:
            return None
        return token["access_token"]

    now_ts = int(time.time())
    cached = _token_cache_get(service, now_ts)
    if cached is not None:
        return cached

    with _refresh_lock_for(service):
        # Re-check the cache inside the lock — another worker may have just refreshed.
        now_ts = int(time.time())
        cached = _token_cache_get(service, now_ts)
        if cached is not None:
            return cached

        # Cache cold or stale: hydrate from DB before deciding to refresh, in case
        # an OAuth callback updated the row out-of-band.
        token = get_service_token(conn, service)
        if not token:
            raise RuntimeError("Search Console is not connected")
        fresh = _fresh_from_row(token, now_ts)
        if fresh is not None:
            _token_cache_put(service, fresh, token["expires_at"])
            return fresh
        if not token["refresh_token"]:
            raise RuntimeError("Search Console token expired and no refresh token is available")

        payload = google_refresh_token(token["refresh_token"])
        payload["refresh_token"] = token["refresh_token"]
        # set_service_token writes the DB and populates the in-process cache.
        set_service_token(conn, service, payload)
        return payload["access_token"]


def google_token_has_scope(conn: sqlite3.Connection, scope: str) -> bool:
    token = get_service_token(conn, "search_console")
    if not token:
        return False
    scope_str = (token["scope"] or "").strip()
    if not scope_str and token["raw_json"]:
        try:
            scope_str = (json.loads(token["raw_json"]).get("scope") or "").strip()
        except Exception:
            pass
    if not scope_str:
        return False
    scopes = set(scope_str.split())
    return scope in scopes


# -- Generic Google API helpers -----------------------------------------------

def google_api_get(url: str, access_token: str, *, timeout: int = 30) -> dict:
    return request_json(url, headers={"Authorization": f"Bearer {access_token}"}, method="GET", timeout=timeout)


def google_api_post(url: str, access_token: str, payload: dict) -> dict:
    return request_json(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {access_token}"},
        payload=payload,
    )
