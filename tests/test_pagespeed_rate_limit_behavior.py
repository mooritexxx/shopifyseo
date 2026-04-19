"""PageSpeed rate-limit behavior tests."""

import json
import sqlite3

import pytest

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
