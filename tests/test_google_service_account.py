"""Service-account JWT auth for GSC/GA4. No network; throwaway RSA keys only."""
from __future__ import annotations

import base64
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

pytest.importorskip("cryptography")
from fastapi.testclient import TestClient

import shopifyseo.dashboard_google as dg
from shopifyseo.dashboard_google import _ads, _auth, _gsc, _service_account as sa
from shopifyseo.dashboard_http import HttpRequestError

from backend.app.main import app
from backend.app.services import google_signals_service, settings_service


client = TestClient(app)

PRIVATE_KEY_MARKER = "THROWAY-PRIVATE-KEY-MUST-NOT-LEAK"


def _throwaway_rsa():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    return key, pem


def _write_sa_file(path, *, email="sa-test@example.iam.gserviceaccount.com", pem: str, extra=None):
    payload = {
        "type": "service_account",
        "client_email": email,
        "private_key": pem,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return email


def _b64url_decode(segment: str) -> bytes:
    pad = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + pad)


@pytest.fixture
def sa_env(tmp_path, monkeypatch):
    key, pem = _throwaway_rsa()
    path = tmp_path / "google-sa.json"
    email = _write_sa_file(path, pem=pem)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(path))
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(dg, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(dg, "GOOGLE_CLIENT_SECRET", "")
    sa.invalidate_service_account_token_cache()
    _auth.invalidate_token_cache()
    sa.reset_cryptography_warning_for_tests()
    yield {"path": path, "key": key, "pem": pem, "email": email}
    sa.invalidate_service_account_token_cache()
    _auth.invalidate_token_cache()
    sa.reset_cryptography_warning_for_tests()


def _patch_request_json(monkeypatch, handler):
    monkeypatch.setattr(sa, "request_json", handler)
    monkeypatch.setattr(_auth, "request_json", handler)
    monkeypatch.setattr("shopifyseo.dashboard_http.request_json", handler)


def test_jwt_assertion_iss_scope_aud_and_signature(sa_env, monkeypatch):
    seen: list[dict] = []

    def fake_request_json(url, **kwargs):
        seen.append({"url": url, **kwargs})
        form = kwargs.get("form") or {}
        assertion = form["assertion"]
        header_b64, payload_b64, sig_b64 = assertion.split(".")
        claims = json.loads(_b64url_decode(payload_b64))
        assert claims["iss"] == sa_env["email"]
        assert claims["aud"] == "https://oauth2.googleapis.com/token"
        assert sa.SCOPE_WEBMASTERS_READONLY in claims["scope"]
        assert sa.SCOPE_ANALYTICS_READONLY in claims["scope"]
        assert "webmasters" in claims["scope"]
        assert "webmasters.readonly" in claims["scope"]
        message = f"{header_b64}.{payload_b64}".encode("ascii")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        sa_env["key"].public_key().verify(
            _b64url_decode(sig_b64),
            message,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return {"access_token": "sa-tok-1", "expires_in": 3600, "token_type": "Bearer"}

    _patch_request_json(monkeypatch, fake_request_json)
    token = sa.get_service_account_access_token()
    assert token == "sa-tok-1"
    assert seen[0]["url"] == "https://oauth2.googleapis.com/token"
    assert seen[0]["form"]["grant_type"] == sa.JWT_BEARER_GRANT_TYPE


def test_access_token_cached_and_reused(sa_env, monkeypatch):
    calls = []

    def fake_request_json(url, **kwargs):
        calls.append(url)
        return {"access_token": f"sa-tok-{len(calls)}", "expires_in": 3600}

    _patch_request_json(monkeypatch, fake_request_json)
    assert sa.get_service_account_access_token() == "sa-tok-1"
    assert sa.get_service_account_access_token() == "sa-tok-1"
    assert len(calls) == 1


def test_expiry_triggers_re_mint(sa_env, monkeypatch):
    calls = []

    def fake_request_json(url, **kwargs):
        calls.append(url)
        return {"access_token": f"sa-tok-{len(calls)}", "expires_in": 0}

    _patch_request_json(monkeypatch, fake_request_json)
    assert sa.get_service_account_access_token() == "sa-tok-1"
    assert sa.get_service_account_access_token() == "sa-tok-2"
    assert len(calls) == 2


def test_missing_key_falls_back_to_oauth(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(missing))
    sa.invalidate_service_account_token_cache()
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    assert _auth.get_search_data_access_token(object()) == "oauth-token"


def test_malformed_key_falls_back_without_secret_in_logs(tmp_path, monkeypatch, caplog):
    path = tmp_path / "bad-sa.json"
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "sa-test@example.iam.gserviceaccount.com",
                "private_key": f"-----BEGIN PRIVATE KEY-----\n{PRIVATE_KEY_MARKER}\n-----END PRIVATE KEY-----\n",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(path))
    sa.invalidate_service_account_token_cache()
    sa.reset_cryptography_warning_for_tests()
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    with caplog.at_level(logging.WARNING):
        token = _auth.get_search_data_access_token(object())
    assert token == "oauth-token"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert PRIVATE_KEY_MARKER not in text
    assert "BEGIN PRIVATE KEY" not in text
    try:
        raise sa.ServiceAccountError("probe")
    except sa.ServiceAccountError as exc:
        assert PRIVATE_KEY_MARKER not in str(exc)


def test_mint_http_400_falls_back_to_oauth(sa_env, monkeypatch, caplog):
    def fake_request_json(url, **kwargs):
        raise HttpRequestError("HTTP 400 for token", status=400, body='{"error":"invalid_grant"}')

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    with caplog.at_level(logging.WARNING):
        token = _auth.get_search_data_access_token(object())
    assert token == "oauth-token"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert sa_env["pem"] not in text
    assert "BEGIN PRIVATE KEY" not in text


def test_cryptography_unavailable_falls_back(sa_env, monkeypatch, caplog):
    def boom():
        raise ImportError("cryptography unavailable")

    monkeypatch.setattr(sa, "_import_cryptography", boom)
    sa.reset_cryptography_warning_for_tests()
    sa.invalidate_service_account_token_cache()
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    with caplog.at_level(logging.WARNING):
        assert sa.service_account_available() is False
        token = _auth.get_search_data_access_token(object())
    assert token == "oauth-token"
    assert "cryptography" in "\n".join(r.getMessage() for r in caplog.records).lower()


def test_google_signals_service_account_mode_without_oauth(sa_env, monkeypatch):
    def fake_request_json(url, **kwargs):
        if "oauth2.googleapis.com/token" in url:
            return {"access_token": "sa-access-token", "expires_in": 3600}
        if url.rstrip("/").endswith("/webmasters/v3/sites"):
            return {"siteEntry": [{"siteUrl": "sc-domain:example.test"}]}
        return {}

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(dg, "get_search_console_summary_cached", lambda conn, refresh=False: {
        "start_date": "",
        "end_date": "",
        "pages": [],
        "queries": [],
        "_cache": None,
    })
    monkeypatch.setattr(
        google_signals_service,
        "gsc_property_breakdowns_for_signals",
        lambda *a, **k: google_signals_service._empty_gsc_property_breakdowns_for_signals(),
    )
    monkeypatch.setattr(dg, "get_service_setting", lambda conn, key, default="": "")
    monkeypatch.setattr(dg, "preferred_site_url", lambda conn, sites: sites[0]["siteUrl"] if sites else "")
    data = google_signals_service.get_google_signals_data()
    assert data["configured"] is True
    assert data["connected"] is True
    assert data["mode"] == "service_account"
    assert data["error"] != "Google OAuth is not configured in the dashboard process."


def test_google_signals_http_service_account_mode(sa_env, monkeypatch):
    def fake_request_json(url, **kwargs):
        if "oauth2.googleapis.com/token" in url:
            return {"access_token": "sa-access-token", "expires_in": 3600}
        if url.rstrip("/").endswith("/webmasters/v3/sites"):
            return {"siteEntry": []}
        return {}

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(dg, "get_search_console_summary_cached", lambda conn, refresh=False: {
        "start_date": "",
        "end_date": "",
        "pages": [],
        "queries": [],
        "_cache": None,
    })
    monkeypatch.setattr(
        google_signals_service,
        "gsc_property_breakdowns_for_signals",
        lambda *a, **k: google_signals_service._empty_gsc_property_breakdowns_for_signals(),
    )
    response = client.get("/api/google-signals")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["configured"] is True
    assert data["connected"] is True
    assert data["mode"] == "service_account"


def test_google_signals_sa_available_mint_fail_not_oauth_error(sa_env, monkeypatch):
    def fake_request_json(url, **kwargs):
        raise HttpRequestError("HTTP 400 for token", status=400, body="invalid")

    _patch_request_json(monkeypatch, fake_request_json)
    data = google_signals_service.get_google_signals_data()
    assert data["configured"] is True
    assert "Google OAuth is not configured" not in (data.get("error") or "")


def test_google_signals_oauth_only_reports_previous_values(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(dg, "GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr(dg, "GOOGLE_CLIENT_SECRET", "")
    sa.invalidate_service_account_token_cache()
    data = google_signals_service.get_google_signals_data()
    assert data["configured"] is False
    assert data["connected"] is False
    assert data.get("mode") in (None, "")
    assert "Google OAuth is not configured" in data["error"]


def test_ads_uses_oauth_token_never_sa(sa_env, monkeypatch):
    sa_calls = []

    def fake_sa_request(url, **kwargs):
        sa_calls.append(url)
        return {"access_token": "sa-must-not-reach-ads", "expires_in": 3600}

    _patch_request_json(monkeypatch, fake_sa_request)
    monkeypatch.setattr(_auth, "google_configured", lambda: True)
    monkeypatch.setattr(_ads, "google_configured", lambda: True)
    monkeypatch.setattr(_auth, "get_service_token", lambda conn, service: {"access_token": "oauth-row"})
    monkeypatch.setattr(_ads, "get_service_token", lambda conn, service: {"access_token": "oauth-row"})
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-ads-token")
    monkeypatch.setattr(_ads, "get_google_access_token", lambda conn: "oauth-ads-token")
    seen_headers = []

    def fake_ads_request(url, **kwargs):
        seen_headers.append(kwargs.get("headers") or {})
        return {"resourceNames": ["customers/123"]}

    monkeypatch.setattr(_ads, "request_json", fake_ads_request)
    result = _ads.test_google_ads_api(object(), "dev-token")
    assert result["ok"] is True
    assert seen_headers[0]["Authorization"] == "Bearer oauth-ads-token"
    assert "sa-must-not-reach-ads" not in json.dumps(seen_headers)


def test_pagespeed_never_uses_service_account_token(sa_env, monkeypatch):
    monkeypatch.setattr(_gsc, "try_service_account_access_token", lambda scopes=None: "sa-psi-token")
    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *a, **k: False)
    captured = {}

    def fake_fetch(api_url, access_token, **kwargs):
        captured["token"] = access_token
        return {"lighthouseResult": {"categories": {}}}

    monkeypatch.setattr(_gsc, "_fetch_run_pagespeed_with_retries", fake_fetch)
    monkeypatch.setattr(_gsc, "_load_cached_payload", lambda *a, **k: (None, {"exists": False}))
    monkeypatch.setattr(_gsc, "_write_cache_payload", lambda *a, **k: {"exists": True})
    monkeypatch.setattr(_gsc, "trim_pagespeed_payload", lambda payload: payload)
    monkeypatch.setattr(_gsc, "_pkg", lambda: type("P", (), {"GSC_CACHE": {"pagespeed": {}}})())
    payload = _gsc.get_pagespeed(object(), "https://example.test/", "mobile", refresh=True)
    assert captured["token"] == ""
    assert payload.get("lighthouseResult") is not None


def test_search_data_configured_true_with_sa_without_oauth(sa_env):
    assert dg.google_configured() is False
    assert dg.search_data_configured() is True
    assert dg.service_account_available() is True


def test_settings_treats_service_account_as_connected(sa_env, monkeypatch):
    def fake_request_json(url, **kwargs):
        if "oauth2.googleapis.com/token" in url:
            return {"access_token": "sa-access-token", "expires_in": 3600}
        return {}

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(settings_service, "open_db_connection", lambda: _DummyConn())
    monkeypatch.setattr(dg, "get_service_setting", lambda conn, key, default="": "")
    monkeypatch.setattr(dg, "get_search_console_sites", lambda conn: [{"siteUrl": "sc-domain:example.test"}])
    monkeypatch.setattr(dg, "get_ga4_properties", lambda conn: {"properties": [], "activation_url": ""})
    monkeypatch.setattr(dg, "list_google_ads_accessible_customers", lambda conn: [])
    from shopifyseo import dashboard_ai as dai

    monkeypatch.setattr(dai, "ai_configured", lambda conn: False)
    monkeypatch.setattr(settings_service, "runtime_setting", lambda conn, env_key, setting_key: ("", "env"))
    data = settings_service.get_settings_data()
    assert data["google_configured"] is True
    assert data["google_connected"] is True
    assert data["auth_url"] is None
    assert data["sync_scope_ready"]["gsc"] is True


def test_failed_mint_cools_down_to_one_request(sa_env, monkeypatch, caplog):
    calls: list[str] = []

    def fake_request_json(url, **kwargs):
        calls.append(url)
        raise HttpRequestError("HTTP 400 for token", status=400, body='{"error":"invalid_grant"}')

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    clock = {"t": 1_700_000_000}
    monkeypatch.setattr(sa, "_now", lambda: clock["t"])
    with caplog.at_level(logging.WARNING):
        tokens = [_auth.get_search_data_access_token(object()) for _ in range(20)]
    assert tokens == ["oauth-token"] * 20
    assert len(calls) == 1
    warnings = [r for r in caplog.records if "token mint failed" in r.getMessage()]
    assert len(warnings) == 1
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert sa_env["pem"] not in text
    assert "BEGIN PRIVATE KEY" not in text
    assert PRIVATE_KEY_MARKER not in text


def test_failed_mint_retries_after_cooldown(sa_env, monkeypatch):
    calls: list[str] = []

    def fake_request_json(url, **kwargs):
        calls.append(url)
        raise HttpRequestError("HTTP 503 for token", status=503, body="unavailable")

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    clock = {"t": 1_700_000_000}
    monkeypatch.setattr(sa, "_now", lambda: clock["t"])
    assert _auth.get_search_data_access_token(object()) == "oauth-token"
    assert len(calls) == 1
    clock["t"] += 10
    assert _auth.get_search_data_access_token(object()) == "oauth-token"
    assert len(calls) == 1
    clock["t"] += sa._MINT_FAILURE_COOLDOWN_SECONDS + 1
    assert _auth.get_search_data_access_token(object()) == "oauth-token"
    assert len(calls) == 2


def test_failed_mint_single_flight_across_threads(sa_env, monkeypatch):
    calls: list[str] = []
    started = threading.Event()
    release = threading.Event()

    def fake_request_json(url, **kwargs):
        calls.append(url)
        started.set()
        release.wait(timeout=2)
        raise HttpRequestError("HTTP 400 for token", status=400, body="invalid")

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")

    def _call():
        return _auth.get_search_data_access_token(object())

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(_call) for _ in range(16)]
        assert started.wait(timeout=2)
        release.set()
        tokens = [f.result() for f in futures]
    assert tokens == ["oauth-token"] * 16
    assert len(calls) == 1


def test_invalidate_clears_mint_failure_cooldown(sa_env, monkeypatch):
    calls: list[str] = []

    def fake_request_json(url, **kwargs):
        calls.append(url)
        raise HttpRequestError("HTTP 400 for token", status=400, body="invalid")

    _patch_request_json(monkeypatch, fake_request_json)
    monkeypatch.setattr(_auth, "get_google_access_token", lambda conn: "oauth-token")
    clock = {"t": 1_700_000_000}
    monkeypatch.setattr(sa, "_now", lambda: clock["t"])
    assert _auth.get_search_data_access_token(object()) == "oauth-token"
    assert len(calls) == 1
    sa.invalidate_service_account_token_cache()
    assert _auth.get_search_data_access_token(object()) == "oauth-token"
    assert len(calls) == 2
    _auth.invalidate_token_cache()
    assert _auth.get_search_data_access_token(object()) == "oauth-token"
    assert len(calls) == 3


class _DummyConn:
    def close(self):
        return None

    def execute(self, *a, **k):
        class _R:
            def fetchone(self):
                return None

        return _R()
