"""PageSpeed rate-limit behavior tests."""

import json
import sqlite3

import pytest

import shopifyseo.dashboard_actions._rpm_limiter as rpm
from shopifyseo.dashboard_actions import _sync
from shopifyseo.dashboard_google import _gsc
from shopifyseo.dashboard_google._cache import ensure_google_cache_schema
from shopifyseo.dashboard_http import HttpRequestError


def _memory_cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_google_cache_schema(conn)
    return conn


def _insert_pagespeed_cache_row(
    conn: sqlite3.Connection,
    *,
    cache_key: str,
    object_type: str,
    object_handle: str,
    url: str,
    strategy: str,
    payload: dict,
    fetched_at: int,
    expires_at: int,
) -> None:
    conn.execute(
        """
        INSERT INTO google_api_cache(
          cache_key, cache_type, object_type, object_handle, url, strategy, payload_json, fetched_at, expires_at, updated_at
        ) VALUES(?, 'pagespeed', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            cache_key,
            object_type,
            object_handle,
            url,
            strategy,
            json.dumps(payload, ensure_ascii=True),
            fetched_at,
            expires_at,
        ),
    )
    conn.commit()


def test_pagespeed_target_counts_waits_for_rate_limit_cooldown_then_requeues(monkeypatch):
    conn = _memory_cache_conn()
    now_ts = 2_000_000_000
    url = "https://example.com/products/widget"
    monkeypatch.setattr(_sync.time, "time", lambda: now_ts)
    monkeypatch.setattr(_sync, "_all_object_targets", lambda _conn: [("product", "widget", url)])

    _insert_pagespeed_cache_row(
        conn,
        cache_key=f"pagespeed::mobile::{url}",
        object_type="product",
        object_handle="widget",
        url=url,
        strategy="mobile",
        payload={"_meta": {"rate_limited": True}},
        fetched_at=now_ts,
        expires_at=now_ts + 300,
    )
    _insert_pagespeed_cache_row(
        conn,
        cache_key=f"pagespeed::desktop::{url}",
        object_type="product",
        object_handle="widget",
        url=url,
        strategy="desktop",
        payload={},
        fetched_at=now_ts,
        expires_at=now_ts + 7 * 24 * 60 * 60,
    )

    total_targets, queued_targets = _sync._pagespeed_target_counts(conn)
    assert total_targets == 1
    assert queued_targets == []

    conn.execute(
        "UPDATE google_api_cache SET expires_at = ? WHERE cache_key = ?",
        (now_ts - 1, f"pagespeed::mobile::{url}"),
    )
    conn.commit()

    total_targets, queued_targets = _sync._pagespeed_target_counts(conn)
    assert total_targets == 1
    assert queued_targets == [("product", "widget", url, "mobile")]


def test_pagespeed_http_429_is_not_retried(monkeypatch):
    calls = {"before_each_http": 0, "google_api_get": 0}

    def _before_each_http() -> None:
        calls["before_each_http"] += 1

    def _google_api_get(*args, **kwargs):
        calls["google_api_get"] += 1
        raise HttpRequestError(
            "HTTP 429 for https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed",
            status=429,
            headers={"Retry-After": "120"},
        )

    def _unexpected_sleep(_seconds: float) -> None:
        raise AssertionError("429 responses should not be retried with backoff sleeps")

    monkeypatch.setattr(_gsc, "google_api_get", _google_api_get)
    monkeypatch.setattr(_gsc.time, "sleep", _unexpected_sleep)

    with pytest.raises(HttpRequestError):
        _gsc._fetch_run_pagespeed_with_retries(
            "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed",
            "fake-token",
            before_each_http=_before_each_http,
        )

    assert calls == {"before_each_http": 1, "google_api_get": 1}


def test_adaptive_pagespeed_limiter_slows_down_and_recovers(monkeypatch):
    now = 1_000_000.0

    def _monotonic() -> float:
        return now

    monkeypatch.setattr(rpm.time, "monotonic", _monotonic)

    limiter = rpm.AdaptiveMinuteRateLimiter(
        190,
        minimum_limit=95,
        maximum_limit=190,
    )

    changed, lowered = limiter.note_rate_limited(12)
    assert changed is True
    assert lowered == 142
    assert limiter.current_limit == 142
    assert limiter.wait_seconds() >= 11.0

    now += 12.0
    assert limiter.wait_seconds() == 0.0

    changed = False
    new_limit = limiter.current_limit
    for _ in range(25):
        changed, new_limit = limiter.note_success()
    assert changed is True
    assert new_limit == 147


def test_pagespeed_bulk_max_inflight_tracks_rate_limit():
    assert _sync._pagespeed_bulk_max_inflight(60) == 12
    assert _sync._pagespeed_bulk_max_inflight(190) == 24
    assert _sync._pagespeed_bulk_max_inflight(400) == 50


def test_get_pagespeed_hybrid_429_slowdown_then_inline_retry_succeeds(monkeypatch):
    conn = _memory_cache_conn()
    url = "https://example.com/p"
    calls = {"slowdown": 0, "fetch": 0, "get": 0}

    def _slowdown(_exc: HttpRequestError) -> None:
        calls["slowdown"] += 1

    def _fetch(*_a, **_k):
        calls["fetch"] += 1
        raise HttpRequestError("429", status=429, headers={"Retry-After": "2"})

    def _get(*_a, **_k):
        calls["get"] += 1
        return {"lighthouseResult": {"categories": {"performance": {"score": 1.0}}}}

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(_gsc, "_fetch_run_pagespeed_with_retries", _fetch)
    monkeypatch.setattr(_gsc, "google_api_get", _get)
    monkeypatch.setattr(_gsc, "_sleep_interruptible", lambda *_a, **_k: None)

    out = _gsc.get_pagespeed(
        conn,
        url,
        "mobile",
        refresh=True,
        object_type="product",
        object_handle="h",
        hybrid_pagespeed_429_retry=True,
        pagespeed_429_requeue_pass=0,
        on_hybrid_429_slowdown=_slowdown,
        hybrid_429_adaptive_wait_seconds=lambda: 0.0,
    )
    assert calls == {"slowdown": 1, "fetch": 1, "get": 1}
    assert out.get("_cache", {}).get("requeue_429") is None
    assert out.get("_cache", {}).get("rate_limited") is None


def test_get_pagespeed_hybrid_429_requeue_marker_on_second_429(monkeypatch):
    conn = _memory_cache_conn()
    url = "https://example.com/p"
    calls = {"get": 0}

    def _fetch(*_a, **_k):
        raise HttpRequestError("429", status=429, headers={"Retry-After": "1"})

    def _get(*_a, **_k):
        calls["get"] += 1
        raise HttpRequestError("429", status=429, headers={"Retry-After": "1"})

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(_gsc, "_fetch_run_pagespeed_with_retries", _fetch)
    monkeypatch.setattr(_gsc, "google_api_get", _get)
    monkeypatch.setattr(_gsc, "_sleep_interruptible", lambda *_a, **_k: None)

    out = _gsc.get_pagespeed(
        conn,
        url,
        "mobile",
        refresh=True,
        hybrid_pagespeed_429_retry=True,
        pagespeed_429_requeue_pass=0,
        on_hybrid_429_slowdown=lambda _e: None,
        hybrid_429_adaptive_wait_seconds=lambda: 0.0,
    )
    assert calls["get"] == 1
    assert out["_cache"].get("requeue_429") is True


def test_get_pagespeed_hybrid_429_final_pass_persists_rate_limit(monkeypatch):
    conn = _memory_cache_conn()
    url = "https://example.com/p"

    def _fetch(*_a, **_k):
        raise HttpRequestError("429", status=429, headers={"Retry-After": "1"})

    def _get(*_a, **_k):
        raise HttpRequestError("429", status=429, headers={"Retry-After": "1"})

    monkeypatch.setattr(_gsc, "google_token_has_scope", lambda *_a, **_k: True)
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda *_a, **_k: "tok")
    monkeypatch.setattr(_gsc, "_fetch_run_pagespeed_with_retries", _fetch)
    monkeypatch.setattr(_gsc, "google_api_get", _get)
    monkeypatch.setattr(_gsc, "_sleep_interruptible", lambda *_a, **_k: None)

    out = _gsc.get_pagespeed(
        conn,
        url,
        "mobile",
        refresh=True,
        hybrid_pagespeed_429_retry=True,
        pagespeed_429_requeue_pass=1,
        on_hybrid_429_slowdown=lambda _e: None,
        hybrid_429_adaptive_wait_seconds=lambda: 0.0,
    )
    assert out["_cache"].get("rate_limited") is True
    assert out["_cache"].get("hybrid_429_final") is True
    assert out["_cache"].get("requeue_429") is None
