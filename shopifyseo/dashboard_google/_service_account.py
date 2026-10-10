"""Google service-account JWT bearer tokens for Search Console and GA4.

The private key is never logged. ``cryptography`` is imported only inside the
signer so a missing package cannot break app startup or module import.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from typing import Any

from shopifyseo.dashboard_http import HttpRequestError, request_json

logger = logging.getLogger(__name__)

DEFAULT_SERVICE_ACCOUNT_FILE = "/home/box/secrets/google-sa.json"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
JWT_BEARER_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:jwt-bearer"

SCOPE_WEBMASTERS_READONLY = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_ANALYTICS_READONLY = "https://www.googleapis.com/auth/analytics.readonly"
SEARCH_DATA_SCOPES = (SCOPE_WEBMASTERS_READONLY, SCOPE_ANALYTICS_READONLY)

_JWT_LIFETIME_SECONDS = 3600
_TOKEN_FRESHNESS_MARGIN_SECONDS = 300  # re-mint ~5 minutes before expiry

_CRYPTO_MISSING_WARNED = False
_TOKEN_CACHE_LOCK = threading.Lock()
_TOKEN_CACHE: dict[str, tuple[str, int]] = {}  # scope_key -> (access_token, expires_at_epoch)
_MINT_LOCKS: dict[str, threading.Lock] = {}
_MINT_LOCKS_GUARD = threading.Lock()


class ServiceAccountError(RuntimeError):
    """Key-file or mint failure. Message contains only the path and a generic reason."""


def service_account_file_path() -> str:
    raw = (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or "").strip()
    return raw or DEFAULT_SERVICE_ACCOUNT_FILE


def _scope_key(scopes: tuple[str, ...] | list[str]) -> str:
    return " ".join(sorted({s.strip() for s in scopes if (s or "").strip()}))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _warn_cryptography_once() -> None:
    global _CRYPTO_MISSING_WARNED
    if _CRYPTO_MISSING_WARNED:
        return
    _CRYPTO_MISSING_WARNED = True
    logger.warning(
        "Google service-account auth unavailable: cryptography is not importable; "
        "falling back to OAuth. Restart after `pip install -e .` (or installing cryptography)."
    )


def _import_cryptography():
    """Isolated so tests can simulate a missing package without touching sys.modules."""
    import cryptography

    return cryptography


def cryptography_available() -> bool:
    try:
        _import_cryptography()
    except Exception:
        return False
    return True


def reset_cryptography_warning_for_tests() -> None:
    global _CRYPTO_MISSING_WARNED
    _CRYPTO_MISSING_WARNED = False


def _mint_lock_for(scope_key: str) -> threading.Lock:
    with _MINT_LOCKS_GUARD:
        lock = _MINT_LOCKS.get(scope_key)
        if lock is None:
            lock = threading.Lock()
            _MINT_LOCKS[scope_key] = lock
        return lock


def invalidate_service_account_token_cache(scope_key: str | None = None) -> None:
    """Drop one or all cached service-account access tokens."""
    with _TOKEN_CACHE_LOCK:
        if scope_key is None:
            _TOKEN_CACHE.clear()
        else:
            _TOKEN_CACHE.pop(scope_key, None)


def _cache_get(scope_key: str, now_ts: int) -> str | None:
    with _TOKEN_CACHE_LOCK:
        entry = _TOKEN_CACHE.get(scope_key)
    if not entry:
        return None
    access_token, expires_at = entry
    if not access_token or expires_at <= now_ts + _TOKEN_FRESHNESS_MARGIN_SECONDS:
        return None
    return access_token


def _cache_put(scope_key: str, access_token: str, expires_at: int) -> None:
    with _TOKEN_CACHE_LOCK:
        if access_token and expires_at:
            _TOKEN_CACHE[scope_key] = (access_token, int(expires_at))
        else:
            _TOKEN_CACHE.pop(scope_key, None)


def _load_service_account_fields(path: str) -> tuple[str, str, str]:
    """Return (client_email, private_key, token_uri). Never include key material in errors."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ServiceAccountError(f"service-account file unreadable: {path} ({exc.strerror})") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raise ServiceAccountError(f"malformed service-account JSON: {path}") from None
    if not isinstance(data, dict):
        raise ServiceAccountError(f"malformed service-account JSON: {path}")
    email = str(data.get("client_email") or "").strip()
    private_key = str(data.get("private_key") or "")
    token_uri = str(data.get("token_uri") or "").strip() or DEFAULT_TOKEN_URI
    if not email or not private_key.strip():
        raise ServiceAccountError(f"service-account file missing required fields: {path}")
    return email, private_key, token_uri


def service_account_available() -> bool:
    """True when the key file exists, parses as a service-account JSON, and cryptography imports."""
    if not cryptography_available():
        _warn_cryptography_once()
        return False
    path = service_account_file_path()
    if not os.path.isfile(path):
        return False
    try:
        _load_service_account_fields(path)
    except ServiceAccountError:
        return False
    return True


def _sign_rs256(message: bytes, private_key_pem: str) -> bytes:
    """Sign with RS256. cryptography is imported here only."""
    try:
        _import_cryptography()
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ServiceAccountError:
        raise
    except Exception:
        _warn_cryptography_once()
        raise ServiceAccountError("cryptography is not importable") from None
    try:
        key = serialization.load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
        return key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    except ServiceAccountError:
        raise
    except Exception:
        path = service_account_file_path()
        raise ServiceAccountError(f"service-account key could not be used for signing: {path}") from None


def build_signed_jwt(
    *,
    client_email: str,
    scopes: tuple[str, ...] | list[str],
    token_uri: str,
    private_key_pem: str,
    iat: int,
    exp: int,
) -> str:
    """Build a signed JWT bearer assertion (iss/scope/aud/iat/exp)."""
    header = {"alg": "RS256", "typ": "JWT"}
    scope = _scope_key(scopes)
    claims = {
        "iss": client_email,
        "scope": scope,
        "aud": token_uri,
        "iat": int(iat),
        "exp": int(exp),
    }
    signing_input = (
        f"{_b64url(json.dumps(header, separators=(',', ':'), ensure_ascii=True).encode('utf-8'))}."
        f"{_b64url(json.dumps(claims, separators=(',', ':'), ensure_ascii=True).encode('utf-8'))}"
    )
    signature = _sign_rs256(signing_input.encode("ascii"), private_key_pem)
    return f"{signing_input}.{_b64url(signature)}"


def mint_service_account_access_token(
    scopes: tuple[str, ...] | list[str] = SEARCH_DATA_SCOPES,
    *,
    now_ts: int | None = None,
) -> dict[str, Any]:
    """POST a JWT bearer grant via ``request_json``. Returns the token endpoint payload."""
    path = service_account_file_path()
    email, private_key, token_uri = _load_service_account_fields(path)
    now = int(time.time() if now_ts is None else now_ts)
    assertion = build_signed_jwt(
        client_email=email,
        scopes=scopes,
        token_uri=token_uri,
        private_key_pem=private_key,
        iat=now,
        exp=now + _JWT_LIFETIME_SECONDS,
    )
    try:
        payload = request_json(
            token_uri,
            method="POST",
            form={
                "grant_type": JWT_BEARER_GRANT_TYPE,
                "assertion": assertion,
            },
        )
    except HttpRequestError as exc:
        status = exc.status if exc.status is not None else "error"
        raise ServiceAccountError(f"service-account token request failed ({status}): {path}") from None
    except Exception:
        raise ServiceAccountError(f"service-account token request failed: {path}") from None
    if not isinstance(payload, dict) or not (payload.get("access_token") or "").strip():
        raise ServiceAccountError(f"service-account token response missing access_token: {path}")
    return payload


def get_service_account_access_token(
    scopes: tuple[str, ...] | list[str] = SEARCH_DATA_SCOPES,
) -> str:
    """Return a cached service-account access token, minting when stale."""
    if not cryptography_available():
        _warn_cryptography_once()
        raise ServiceAccountError("cryptography is not importable")
    key = _scope_key(scopes)
    now_ts = int(time.time())
    cached = _cache_get(key, now_ts)
    if cached is not None:
        return cached
    with _mint_lock_for(key):
        now_ts = int(time.time())
        cached = _cache_get(key, now_ts)
        if cached is not None:
            return cached
        payload = mint_service_account_access_token(scopes, now_ts=now_ts)
        access_token = str(payload["access_token"])
        raw_expires = payload.get("expires_in")
        expires_in = _JWT_LIFETIME_SECONDS if raw_expires is None else int(raw_expires)
        expires_at = now_ts + max(expires_in, 0)
        _cache_put(key, access_token, expires_at)
        return access_token


def try_service_account_access_token(
    scopes: tuple[str, ...] | list[str] = SEARCH_DATA_SCOPES,
) -> str | None:
    """Mint or reuse an SA token. Returns None after a short non-secret warning on failure."""
    path = service_account_file_path()
    if not os.path.isfile(path):
        return None
    if not cryptography_available():
        _warn_cryptography_once()
        return None
    try:
        return get_service_account_access_token(scopes)
    except ServiceAccountError as exc:
        logger.warning("Google service-account token mint failed; falling back to OAuth: %s", exc)
        return None
    except Exception:
        logger.warning(
            "Google service-account token mint failed; falling back to OAuth: unexpected error (%s)",
            path,
        )
        return None
