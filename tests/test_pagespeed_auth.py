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


FAKE_PAGESPEED_KEY = "FAKE-KEY-123"
_PAGESPEED_RUN_URL = (
    "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed"
    f"?url=https%3A%2F%2Fexample.com%2Fp&strategy=mobile&key={FAKE_PAGESPEED_KEY}"
)


def _pagespeed_http_error_response(status: int):
    import requests

    class _Resp:
        status_code = status
        text = '{"error":{"message":"psi boom","status":"INTERNAL"}}'
        headers = {}
        url = _PAGESPEED_RUN_URL

        def raise_for_status(self):
            err = requests.HTTPError(f"{self.status_code} Server Error for url: {self.url}")
            err.response = self
            raise err

    return _Resp()


@pytest.mark.parametrize("status", [500, 429])
def test_request_text_redacts_pagespeed_key_on_http_status(monkeypatch, status):
    from shopifyseo.dashboard_actions._sync import _pagespeed_error_detail_for_ui
    from shopifyseo.dashboard_http import SESSION, request_text

    seen = {}

    def fake_request(**kwargs):
        seen["url"] = kwargs["url"]
        assert FAKE_PAGESPEED_KEY in kwargs["url"]
        return _pagespeed_http_error_response(status)

    monkeypatch.setattr(SESSION, "request", fake_request)
    with pytest.raises(HttpRequestError) as ei:
        request_text(_PAGESPEED_RUN_URL)
    exc = ei.value
    assert seen["url"] == _PAGESPEED_RUN_URL
    assert FAKE_PAGESPEED_KEY not in str(exc)
    assert FAKE_PAGESPEED_KEY not in (exc.reason or "")
    assert "runPagespeed" in str(exc)
    assert str(status) in str(exc)
    assert exc.status == status
    detail, _extra = _pagespeed_error_detail_for_ui(exc)
    assert FAKE_PAGESPEED_KEY not in detail


def test_request_text_redacts_pagespeed_key_on_connection_error(monkeypatch):
    import requests

    from shopifyseo.dashboard_actions._sync import _pagespeed_error_detail_for_ui
    from shopifyseo.dashboard_http import SESSION, request_text

    seen = {}

    def fake_request(**kwargs):
        seen["url"] = kwargs["url"]
        assert FAKE_PAGESPEED_KEY in kwargs["url"]
        raise requests.ConnectionError(f"failed for {_PAGESPEED_RUN_URL}")

    monkeypatch.setattr(SESSION, "request", fake_request)
    with pytest.raises(HttpRequestError) as ei:
        request_text(_PAGESPEED_RUN_URL)
    exc = ei.value
    assert seen["url"] == _PAGESPEED_RUN_URL
    assert FAKE_PAGESPEED_KEY not in str(exc)
    assert FAKE_PAGESPEED_KEY not in (exc.reason or "")
    assert "runPagespeed" in str(exc)
    assert "Connection error" in str(exc)
    detail, _extra = _pagespeed_error_detail_for_ui(exc)
    assert FAKE_PAGESPEED_KEY not in detail


def test_get_pagespeed_failure_does_not_log_api_key(monkeypatch, db_conn, caplog):
    from shopifyseo.dashboard_http import SESSION

    conn = _prep(db_conn)
    monkeypatch.setenv("PAGESPEED_API_KEY", FAKE_PAGESPEED_KEY)
    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: False)
    monkeypatch.setattr(_gsc.time, "sleep", lambda *_a, **_k: None)

    def fake_request(**kwargs):
        assert FAKE_PAGESPEED_KEY in kwargs["url"]
        return _pagespeed_http_error_response(500)

    monkeypatch.setattr(SESSION, "request", fake_request)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(HttpRequestError) as ei:
            _gsc.get_pagespeed(
                conn,
                "https://example.com/p",
                "mobile",
                refresh=True,
                before_each_run_pagespeed_http=lambda: None,
            )
    assert FAKE_PAGESPEED_KEY not in str(ei.value)
    assert FAKE_PAGESPEED_KEY not in (ei.value.reason or "")
    assert "runPagespeed" in str(ei.value)
    assert "500" in str(ei.value)
    assert FAKE_PAGESPEED_KEY not in caplog.text
    from shopifyseo.dashboard_actions._sync import _pagespeed_error_detail_for_ui

    detail, _extra = _pagespeed_error_detail_for_ui(ei.value)
    assert FAKE_PAGESPEED_KEY not in detail


def test_pagespeedonline_sources_do_not_use_search_data_token():
    from pathlib import Path

    roots = [Path("scripts"), Path("shopifyseo")]
    forbidden = ("get_search_data_access_token", "try_service_account_access_token")
    offenders: list[str] = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "pagespeedonline" not in text:
                continue
            if any(token in text for token in forbidden):
                offenders.append(str(path))
    assert offenders == []

