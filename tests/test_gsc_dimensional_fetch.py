"""GSC per-page breakdown fetch: error reasons, throttle, breakers, sync warnings."""
from __future__ import annotations

from datetime import date

from shopifyseo import dashboard_store
from shopifyseo.dashboard_actions import _sync
from shopifyseo.dashboard_google import _gsc
from shopifyseo.dashboard_http import HttpRequestError, describe_google_http_error
from shopifyseo.dashboard_store import ensure_dashboard_schema


def _http_error(status: int, body: str = "", headers: dict | None = None) -> HttpRequestError:
    return HttpRequestError(f"HTTP {status} for https://searchconsole.googleapis.com/x", status=status, body=body, headers=headers)


def test_describe_google_http_error_standard_json():
    body = (
        '{"error":{"code":403,"message":"Quota exceeded for metric queries",'
        '"status":"RESOURCE_EXHAUSTED","errors":[{"reason":"rateLimitExceeded","message":"too many"}]}}'
    )
    info = describe_google_http_error(_http_error(403, body))
    assert info.status == 403
    assert info.google_status == "RESOURCE_EXHAUSTED"
    assert info.reason == "rateLimitExceeded"
    assert "Quota exceeded" in info.message
    text = info.short_description()
    assert "HTTP 403" in text
    assert "rateLimitExceeded" in text


def test_describe_google_http_error_status_only_shape():
    body = '{"error":{"code":400,"message":"Invalid combination of dimensions","status":"INVALID_ARGUMENT"}}'
    info = describe_google_http_error(_http_error(400, body))
    assert info.reason == ""
    assert info.google_status == "INVALID_ARGUMENT"
    assert "Invalid combination" in info.message


def test_describe_google_http_error_errors_array_only():
    body = '{"error":{"errors":[{"reason":"quotaExceeded","message":"user quota"}]}}'
    info = describe_google_http_error(_http_error(403, body))
    assert info.reason == "quotaExceeded"
    assert "user quota" in info.message


def test_describe_google_http_error_string_error():
    info = describe_google_http_error(_http_error(400, '{"error":"invalidArgument"}'))
    assert info.message == "invalidArgument"
    assert info.reason == ""


def test_describe_google_http_error_non_json_and_empty():
    info = describe_google_http_error(_http_error(403, "<html>nope</html>"))
    assert info.status == 403
    assert info.reason == ""
    assert info.message == ""
    empty = describe_google_http_error(_http_error(500, ""))
    assert empty.status == 500
    assert empty.short_description() == "HTTP 500"


def test_describe_google_http_error_truncates_and_redacts_secrets():
    long_msg = "x" * 400
    body = '{"error":{"message":"%s","status":"PERMISSION_DENIED"}}' % long_msg
    info = describe_google_http_error(_http_error(403, body))
    assert len(info.message) == 200
    secret_body = '{"error":{"message":"Bearer ya29.secret-token-value failed"}}'
    info2 = describe_google_http_error(_http_error(401, secret_body))
    assert "ya29.secret-token-value" not in info2.message
    assert "[redacted]" in info2.message
    assert "Authorization" not in info2.short_description()


def test_is_google_throttle_error_variants():
    assert _gsc.is_google_throttle_error(_http_error(429)) is True
    assert _gsc.is_google_throttle_error(_http_error(503)) is True
    assert _gsc.is_google_throttle_error(
        _http_error(403, '{"error":{"errors":[{"reason":"rateLimitExceeded"}],"status":"PERMISSION_DENIED"}}')
    ) is True
    assert _gsc.is_google_throttle_error(
        _http_error(403, '{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}')
    ) is True
    assert _gsc.is_google_throttle_error(
        _http_error(403, '{"error":{"status":"RESOURCE_EXHAUSTED","message":"quota"}}')
    ) is True
    assert _gsc.is_google_throttle_error(
        _http_error(403, '{"error":{"message":"Rate limit exceeded for this project"}}')
    ) is True
    assert _gsc.is_google_throttle_error(
        _http_error(403, '{"error":{"errors":[{"reason":"insufficientPermissions"}],"status":"PERMISSION_DENIED","message":"no access"}}')
    ) is False
    assert _gsc.is_google_throttle_error(
        _http_error(400, '{"error":{"status":"INVALID_ARGUMENT","message":"bad combo"}}')
    ) is False


def test_dimensional_backoff_retries_then_success(monkeypatch):
    sleeps: list[float] = []
    clock = {"t": 0.0}
    monkeypatch.setattr(_gsc, "_dimensional_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(_gsc, "_dimensional_clock", lambda: clock["t"])
    monkeypatch.setattr(_gsc.random, "uniform", lambda a, b: 0.0)
    calls = {"n": 0}

    def fake_post(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(
                403,
                '{"error":{"errors":[{"reason":"rateLimitExceeded"}],"status":"RESOURCE_EXHAUSTED"}}',
                headers={"Retry-After": "3"},
            )
        return {"rows": [{"keys": ["q", "usa"], "clicks": 1, "impressions": 2, "ctr": 0.1, "position": 3.0}]}

    monkeypatch.setattr(_gsc, "google_api_post", fake_post)
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda _c: "tok")
    result = _gsc.fetch_gsc_url_query_second_dimension(
        object(),
        "sc-domain:example.com",
        "https://example.com/p",
        date(2026, 1, 1),
        date(2026, 1, 28),
        second_dimension="country",
        pace=False,
    )
    rows, err = result
    assert err is None
    assert rows[0]["query"] == "q"
    assert result.recovered is True
    assert result.retried == 2
    assert calls["n"] == 3
    assert sleeps
    assert all(0 < s <= 60 for s in sleeps)
    assert any(s >= 3.0 for s in sleeps)


def test_dimensional_retry_after_honored_and_bounded(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(_gsc, "_dimensional_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(_gsc.random, "uniform", lambda a, b: 0.0)

    def always_429(*_a, **_k):
        raise _http_error(429, '{"error":{"status":"RESOURCE_EXHAUSTED"}}', headers={"Retry-After": "9"})

    monkeypatch.setattr(_gsc, "google_api_post", always_429)
    monkeypatch.setattr(_gsc, "get_google_access_token", lambda _c: "tok")
    result = _gsc.fetch_gsc_url_query_second_dimension(
        object(),
        "sc-domain:example.com",
        "https://example.com/p",
        date(2026, 1, 1),
        date(2026, 1, 28),
        second_dimension="device",
        pace=False,
    )
    assert result.err
    assert result.status == 429
    assert result.throttled is True
    assert sleeps
    assert all(s <= 60 for s in sleeps)
    assert min(sleeps) >= 9.0


def _insert_dim_row(conn, handle="widget", kind="country", value="usa"):
    conn.execute(
        """
        INSERT INTO gsc_query_dimension_rows(
          object_type, object_handle, query, dimension_kind, dimension_value,
          clicks, impressions, ctr, position, fetched_at, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        ("product", handle, "vape", kind, value, 4, 10, 0.4, 2.0, 111),
    )
    conn.commit()


def _prepare_dim_conn(db_conn):
    ensure_dashboard_schema(db_conn)
    db_conn.execute(
        "INSERT OR IGNORE INTO service_settings(key, value) VALUES('search_console_site', 'sc-domain:example.com')"
    )
    db_conn.commit()
    return db_conn


def test_non_throttle_403_trips_breaker_after_one_call(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    _gsc.reset_gsc_dimensional_fetch_session()
    calls: list[str] = []

    def fail_perm(*_a, second_dimension, **_k):
        calls.append(second_dimension)
        return _gsc.GscDimensionalFetchResult(
            [],
            "HTTP 403 PERMISSION_DENIED insufficientPermissions",
            status=403,
            reason="insufficientPermissions",
            google_status="PERMISSION_DENIED",
            message="caller does not have permission",
            throttled=False,
        )

    monkeypatch.setattr(_gsc, "fetch_gsc_url_query_second_dimension", fail_perm)
    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fail_perm)
    dashboard_store._refresh_gsc_query_dimensions_into_table(
        conn, "product", "a", "https://example.com/a", fetched_at=1
    )
    dashboard_store._refresh_gsc_query_dimensions_into_table(
        conn, "product", "b", "https://example.com/b", fetched_at=1
    )
    assert calls == ["country"]
    session = _gsc.get_gsc_dimensional_fetch_session()
    assert session.permission_breaker is True
    assert session.warnings == 1
    assert session.warning_counts["country:403:insufficientPermissions"] == 1


def test_search_appearance_400s_trip_breaker_after_five(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    _gsc.reset_gsc_dimensional_fetch_session()
    calls: list[tuple[str, str]] = []

    def fake_fetch(_conn, _site, page_url, *_a, second_dimension, **_k):
        calls.append((page_url, second_dimension))
        if second_dimension != "searchAppearance":
            return _gsc.GscDimensionalFetchResult([], None)
        return _gsc.GscDimensionalFetchResult(
            [],
            "HTTP 400 INVALID_ARGUMENT Invalid combination of dimensions",
            status=400,
            reason="invalidArgument",
            google_status="INVALID_ARGUMENT",
            message="Invalid combination of dimensions",
        )

    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fake_fetch)
    for handle in list("abcdef"):
        dashboard_store._refresh_gsc_query_dimensions_into_table(
            conn, "product", handle, f"https://example.com/{handle}", fetched_at=1
        )
    sa_calls = [c for c in calls if c[1] == "searchAppearance"]
    assert len(sa_calls) == 5
    session = _gsc.get_gsc_dimensional_fetch_session()
    assert session.search_appearance_breaker is True
    assert session.warning_counts["searchAppearance:400:invalidArgument"] == 5
    assert session.warnings == 5
    assert session.skipped == 1
    assert session.details_dict()["skipped"] == 1


def test_previous_dimension_rows_preserved_on_failure(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    _insert_dim_row(conn)
    before = conn.execute("SELECT * FROM gsc_query_dimension_rows").fetchall()
    assert len(before) == 1

    def fail(*_a, **_k):
        return _gsc.GscDimensionalFetchResult([], "HTTP 403", status=403, reason="rateLimitExceeded", throttled=True)

    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fail)
    dashboard_store._refresh_gsc_query_dimensions_into_table(
        conn, "product", "widget", "https://example.com/products/widget", fetched_at=9
    )
    after = conn.execute("SELECT query, dimension_kind, dimension_value, clicks FROM gsc_query_dimension_rows").fetchall()
    assert len(after) == 1
    assert after[0]["query"] == "vape"
    assert after[0]["clicks"] == 4


def test_pacing_limiter_spaces_calls_with_fake_clock():
    clock = {"t": 100.0}
    sleeps: list[float] = []

    def now():
        return clock["t"]

    def sleeper(s):
        sleeps.append(s)
        clock["t"] += s

    limiter = _gsc.MinIntervalLimiter(0.125, clock=now, sleeper=sleeper)
    limiter.acquire()
    limiter.acquire()
    limiter.acquire()
    assert sleeps == [0.125, 0.125]
    assert clock["t"] == 100.25


def test_sync_summary_includes_warnings_errors_unchanged():
    _gsc.reset_gsc_dimensional_fetch_session()
    session = _gsc.get_gsc_dimensional_fetch_session()
    session.record_warning("country", 403, "rateLimitExceeded")
    session.record_warning("searchAppearance", 400, "invalidArgument", count=4)
    session.retried = 3
    session.recovered = 2
    summary = {"considered": 10, "refreshed": 9, "errors": 1, "skipped_fresh": 0}
    _sync._apply_gsc_dimensional_warnings(summary)
    assert summary["errors"] == 1
    assert summary["warnings"] == 5
    assert summary["warning_details"]["country:403:rateLimitExceeded"] == 1
    assert summary["warning_details"]["searchAppearance:400:invalidArgument"] == 4
    assert summary["warning_details"]["retried"] == 3
    assert summary["warning_details"]["recovered"] == 2
    assert summary["warning_details"]["skipped"] == 0
    assert _sync._gsc_breakdown_warnings_suffix(summary) == "; 5 breakdown warnings"
    assert _sync._gsc_breakdown_warnings_suffix({"warnings": 1}) == "; 1 breakdown warning"
    assert _sync._gsc_breakdown_warnings_suffix({"warnings": 0}) == ""


def test_url_inspection_uses_search_data_token_helper(monkeypatch, db_conn):
    from shopifyseo.dashboard_google._cache import ensure_google_cache_schema

    ensure_google_cache_schema(db_conn)
    posted = {}

    def fake_post(url, token, body):
        posted["url"] = url
        posted["token"] = token
        return {"inspectionResult": {"indexStatusResult": {"coverageState": "INDEXED"}}}

    monkeypatch.setattr(_gsc, "get_google_access_token", lambda conn: "search-data-token")
    monkeypatch.setattr(
        _gsc,
        "get_oauth_access_token",
        lambda conn: (_ for _ in ()).throw(AssertionError("oauth must not be used for inspection")),
    )
    monkeypatch.setattr(_gsc, "google_api_post", fake_post)
    monkeypatch.setattr(_gsc, "google_api_get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    monkeypatch.setattr(_gsc, "get_service_setting", lambda *a, **k: "sc-domain:example.com")
    out = _gsc.get_url_inspection(
        db_conn,
        "https://example.com/p",
        refresh=True,
        object_type="product",
        object_handle="p",
        site_url_override="sc-domain:example.com",
    )
    assert posted["token"] == "search-data-token"
    assert "urlInspection" in posted["url"]
    assert out.get("inspectionResult")


def _sa_400_result():
    return _gsc.GscDimensionalFetchResult(
        [],
        "HTTP 400 INVALID_ARGUMENT Invalid combination of dimensions",
        status=400,
        reason="invalidArgument",
        google_status="INVALID_ARGUMENT",
        message="Invalid combination of dimensions",
    )


def _sa_ok_result():
    return _gsc.GscDimensionalFetchResult(
        [{"query": "q", "segment": "AMP", "clicks": 1, "impressions": 2, "ctr": 0.1, "position": 3.0}],
        None,
    )


def test_search_appearance_streak_resets_on_success(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    _gsc.reset_gsc_dimensional_fetch_session()
    sa_n = {"n": 0}

    def fake_fetch(_conn, _site, page_url, *_a, second_dimension, **_k):
        if second_dimension != "searchAppearance":
            return _gsc.GscDimensionalFetchResult([], None)
        sa_n["n"] += 1
        if sa_n["n"] <= 4:
            return _sa_400_result()
        if sa_n["n"] == 5:
            return _sa_ok_result()
        return _sa_400_result()

    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fake_fetch)
    for handle in list("abcdef"):
        dashboard_store._refresh_gsc_query_dimensions_into_table(
            conn, "product", handle, f"https://example.com/{handle}", fetched_at=1
        )
    session = _gsc.get_gsc_dimensional_fetch_session()
    assert sa_n["n"] == 6
    assert session.search_appearance_breaker is False
    assert session.search_appearance_400_streak == 1
    assert session.warnings == 5


def test_search_appearance_five_consecutive_same_reason_trips(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    _gsc.reset_gsc_dimensional_fetch_session()
    sa_n = {"n": 0}

    def fake_fetch(_conn, _site, page_url, *_a, second_dimension, **_k):
        if second_dimension != "searchAppearance":
            return _gsc.GscDimensionalFetchResult([], None)
        sa_n["n"] += 1
        return _sa_400_result()

    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fake_fetch)
    for handle in list("abcde"):
        dashboard_store._refresh_gsc_query_dimensions_into_table(
            conn, "product", handle, f"https://example.com/{handle}", fetched_at=1
        )
    session = _gsc.get_gsc_dimensional_fetch_session()
    assert sa_n["n"] == 5
    assert session.search_appearance_breaker is True
    assert session.warnings == 5
    assert session.skipped == 0


def test_dimensional_run_resets_breaker_for_next_run(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    calls: list[str] = []

    def fail_perm(*_a, second_dimension, **_k):
        calls.append(second_dimension)
        return _gsc.GscDimensionalFetchResult(
            [],
            "HTTP 403 PERMISSION_DENIED insufficientPermissions",
            status=403,
            reason="insufficientPermissions",
            google_status="PERMISSION_DENIED",
            message="caller does not have permission",
            throttled=False,
        )

    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fail_perm)
    with _gsc.gsc_dimensional_run() as run1:
        dashboard_store._refresh_gsc_query_dimensions_into_table(
            conn, "product", "a", "https://example.com/a", fetched_at=1
        )
        assert run1.permission_breaker is True
        assert calls == ["country"]
    calls.clear()
    with _gsc.gsc_dimensional_run() as run2:
        dashboard_store._refresh_gsc_query_dimensions_into_table(
            conn, "product", "b", "https://example.com/b", fetched_at=1
        )
        assert run2.permission_breaker is True
        assert calls == ["country"]
        assert run2 is not run1


def test_single_object_refresh_after_bulk_run_ignores_bulk_breaker(monkeypatch, db_conn):
    conn = _prepare_dim_conn(db_conn)
    calls: list[str] = []

    def fail_perm(*_a, second_dimension, **_k):
        calls.append(second_dimension)
        return _gsc.GscDimensionalFetchResult(
            [],
            "HTTP 403 PERMISSION_DENIED insufficientPermissions",
            status=403,
            reason="insufficientPermissions",
            google_status="PERMISSION_DENIED",
            message="caller does not have permission",
            throttled=False,
        )

    monkeypatch.setattr(dashboard_store.dg, "fetch_gsc_url_query_second_dimension", fail_perm)
    monkeypatch.setattr(
        dashboard_store.dg,
        "get_search_console_url_detail",
        lambda *_a, **_k: {
            "page_rows": [{"clicks": 1, "impressions": 2, "ctr": 0.1, "position": 3.0}],
            "query_rows": [],
            "_cache": {"exists": True, "fetched_at": 1},
            "period_mode": "28d",
        },
    )
    monkeypatch.setattr(dashboard_store.dq, "object_url", lambda *_a, **_k: "https://example.com/p")
    with _gsc.gsc_dimensional_run() as bulk:
        dashboard_store._refresh_gsc_query_dimensions_into_table(
            conn, "product", "bulk", "https://example.com/bulk", fetched_at=1
        )
        snap = _gsc.snapshot_gsc_dimensional_warning_summary()
        assert bulk.permission_breaker is True
        assert snap["warnings"] == 1
        assert "country:403:insufficientPermissions" in snap["warning_details"]
    calls.clear()
    dashboard_store._refresh_object_gsc_into_table(conn, "products", "product", "after")
    assert "country" in calls


def test_bulk_summary_warnings_captured_before_leaving_run():
    with _gsc.gsc_dimensional_run():
        session = _gsc.get_gsc_dimensional_fetch_session()
        session.record_warning("country", 403, "insufficientPermissions")
        session.record_skip(12)
        summary = {"errors": 0}
        _sync._apply_gsc_dimensional_warnings(summary)
        assert summary["errors"] == 0
        assert summary["warnings"] == 1
        assert summary["warning_details"]["skipped"] == 12
        assert summary["warning_details"]["country:403:insufficientPermissions"] == 1
        assert _sync._gsc_breakdown_warnings_suffix(summary) == "; 1 breakdown warning"
    after = _gsc.snapshot_gsc_dimensional_warning_summary()
    assert after["warnings"] == 0


def test_concurrent_dimensional_run_cannot_trip_outer_breaker():
    import threading

    with _gsc.gsc_dimensional_run() as outer:
        def _inner():
            with _gsc.gsc_dimensional_run() as inner:
                inner.permission_breaker = True
                inner.record_warning("country", 403, "insufficientPermissions")

        t = threading.Thread(target=_inner)
        t.start()
        t.join()
        assert outer.permission_breaker is False
        assert outer.warnings == 0
        with _gsc.gsc_dimensional_run() as nested:
            assert nested is outer

