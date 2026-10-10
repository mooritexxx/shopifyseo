"""PageSpeed Insights auth: OAuth openid bearer, public quota, API key. No SA token."""
from __future__ import annotations

import logging

import pytest

from shopifyseo.dashboard_google import _gsc
from shopifyseo.dashboard_google._cache import ensure_google_cache_schema
from shopifyseo.dashboard_http import HttpRequestError


SECRET_KEY = "psi-test-key-must-not-appear"


def _prep(db_conn):
    ensure_google_cache_schema(db_conn)
    return db_conn


def test_pagespeed_openid_oauth_sends_bearer(monkeypatch, db_conn, caplog):
    conn = _prep(db_conn)
    seen = {}

    def fake_get(api_url, access_token, **kwargs):
        seen["url"] = api_url
        seen["token"] = access_token
        return {"lighthouseResult": {"categories": {"performance": {"score": 0.9}}}}

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(_gsc, "get_oauth_access_token", lambda *_a, **_k: "oauth-psi-token")
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda *_a, **_k: "sa-must-not-be-used")
    monkeypatch.setattr(_gsc, "google_api_get", fake_get)
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    with caplog.at_level(logging.DEBUG):
        out = _gsc.get_pagespeed(conn, "https://example.com/p", "mobile", refresh=True)
    assert seen["token"] == "oauth-psi-token"
    assert "key=" not in seen["url"]
    assert out.get("lighthouseResult")
    assert "sa-must-not-be-used" not in caplog.text
    assert "oauth-psi-token" not in caplog.text


def test_pagespeed_without_openid_sends_no_authorization(monkeypatch, db_conn):
    conn = _prep(db_conn)
    seen = {}

    def fake_json(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return {"lighthouseResult": {"categories": {}}}

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: False)
    monkeypatch.setattr(_gsc, "request_json", fake_json)
    monkeypatch.setattr(
        _gsc,
        "google_api_get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("google_api_get must not run without a bearer")),
    )
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    _gsc.get_pagespeed(conn, "https://example.com/p", "mobile", refresh=True)
    assert "Authorization" not in seen["headers"]
    assert "key=" not in seen["url"]


def test_pagespeed_oauth_refresh_failure_falls_through(monkeypatch, db_conn):
    conn = _prep(db_conn)
    seen = {}

    def boom(_conn):
        raise RuntimeError("invalid_grant")

    def fake_json(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        return {"lighthouseResult": {"categories": {}}}

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(_gsc, "get_oauth_access_token", boom)
    monkeypatch.setattr(_gsc, "request_json", fake_json)
    out = _gsc.get_pagespeed(conn, "https://example.com/p", "mobile", refresh=True)
    assert "Authorization" not in seen["headers"]
    assert out.get("lighthouseResult") is not None


def test_pagespeed_sa_token_never_used_when_sa_mode_active(monkeypatch, db_conn):
    conn = _prep(db_conn)
    seen = {}

    def fake_json(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        seen["token_arg"] = None
        return {"lighthouseResult": {"categories": {}}}

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: False)
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda *_a, **_k: "sa-search-data-token")
    monkeypatch.setattr(_gsc, "request_json", fake_json)
    _gsc.get_pagespeed(conn, "https://example.com/p", "mobile", refresh=True)
    assert seen["headers"].get("Authorization") in (None, "")


def test_pagespeed_api_key_appended_when_set(monkeypatch, db_conn, caplog):
    conn = _prep(db_conn)
    seen = {}

    def fake_json(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        return {"lighthouseResult": {"categories": {}}}

    monkeypatch.setenv("PAGESPEED_API_KEY", SECRET_KEY)
    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: False)
    monkeypatch.setattr(_gsc, "request_json", fake_json)
    with caplog.at_level(logging.DEBUG):
        _gsc.get_pagespeed(conn, "https://example.com/p", "mobile", refresh=True)
    assert f"key={SECRET_KEY}" in seen["url"]
    assert SECRET_KEY not in caplog.text


def test_pagespeed_api_key_absent_when_unset(monkeypatch, db_conn):
    conn = _prep(db_conn)
    seen = {}

    def fake_get(api_url, access_token, **kwargs):
        seen["url"] = api_url
        return {"lighthouseResult": {"categories": {}}}

    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(_gsc, "get_oauth_access_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(_gsc, "google_api_get", fake_get)
    _gsc.get_pagespeed(conn, "https://example.com/p", "mobile", refresh=True)
    assert "key=" not in seen["url"]


def test_pagespeed_http_get_omits_authorization_when_token_empty(monkeypatch):
    seen = {}

    def fake_json(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        return {"ok": True}

    monkeypatch.setattr(_gsc, "request_json", fake_json)
    monkeypatch.setattr(
        _gsc,
        "google_api_get",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not send bearer")),
    )
    assert _gsc._pagespeed_http_get("https://pagespeedonline.googleapis.com/x", "") == {"ok": True}
    assert "Authorization" not in seen["headers"]


def test_fetch_run_pagespeed_still_skips_retry_on_429(monkeypatch):
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        raise HttpRequestError("HTTP 429 for https://pagespeedonline.googleapis.com/x", status=429)

    monkeypatch.setattr(_gsc, "google_api_get", fake_get)
    monkeypatch.setattr(_gsc.time, "sleep", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no sleep")))
    with pytest.raises(HttpRequestError):
        _gsc._fetch_run_pagespeed_with_retries("https://pagespeedonline.googleapis.com/x", "tok")
    assert calls["n"] == 1
