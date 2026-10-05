"""Cache-only GSC reads must not refresh OAuth or call Google."""

import pytest

from shopifyseo.dashboard_google import _gsc
from shopifyseo import dashboard_store
from shopifyseo.dashboard_store import ensure_dashboard_schema


def _prepare(db_conn):
    ensure_dashboard_schema(db_conn)
    return db_conn


def test_gsc_url_detail_cache_only_without_site_does_not_call_google(monkeypatch, db_conn) -> None:
    conn = _prepare(db_conn)
    monkeypatch.setattr(
        _gsc,
        "get_search_console_sites",
        lambda _conn: pytest.fail("cache-only GSC detail should not call Google"),
    )

    out = _gsc.get_search_console_url_detail(
        conn,
        "https://example.com/products/widget",
        refresh=False,
        object_type="product",
        object_handle="widget",
    )

    assert out["site_url"] == ""
    assert out["_cache"]["exists"] is False


def test_url_inspection_cache_only_without_site_does_not_call_google(monkeypatch, db_conn) -> None:
    conn = _prepare(db_conn)
    monkeypatch.setattr(
        _gsc,
        "get_search_console_sites",
        lambda _conn: pytest.fail("cache-only URL inspection should not call Google"),
    )

    out = _gsc.get_url_inspection(
        conn,
        "https://example.com/products/widget",
        refresh=False,
        object_type="product",
        object_handle="widget",
    )

    assert out["site_url"] == ""
    assert out["_cache"]["exists"] is False


def test_gsc_signal_refresh_can_skip_embedding_sync(monkeypatch, db_conn) -> None:
    conn = _prepare(db_conn)
    called = {}

    monkeypatch.setattr(
        dashboard_store,
        "_refresh_object_gsc_into_table",
        lambda *_args, **_kwargs: called.setdefault("row_refreshed", True),
    )
    monkeypatch.setattr(
        dashboard_store,
        "_table_for_object_type",
        lambda object_type: "products",
    )

    import shopifyseo.embedding_store as embedding_store

    monkeypatch.setattr(
        embedding_store,
        "sync_embeddings",
        lambda *_args, **_kwargs: pytest.fail("embedding sync should run in background, not inline"),
    )

    dashboard_store.refresh_gsc_signal_data_for_objects(
        conn,
        [("product", "widget")],
        sync_query_embeddings=False,
    )

    assert called["row_refreshed"] is True


def test_gsc_query_cache_dimensions_are_opt_in(monkeypatch, db_conn) -> None:
    conn = _prepare(db_conn)
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        dashboard_store,
        "_refresh_gsc_query_dimensions_into_table",
        lambda _conn, object_type, handle, *_args, **_kwargs: calls.append((object_type, handle)),
    )

    dashboard_store._write_gsc_per_url_query_caches(
        conn,
        "product",
        "widget",
        "https://example.com/products/widget",
        {"query_rows": [], "_cache": {"fetched_at": 123}},
    )
    assert calls == []

    dashboard_store._write_gsc_per_url_query_caches(
        conn,
        "product",
        "widget",
        "https://example.com/products/widget",
        {"query_rows": [], "_cache": {"fetched_at": 123}},
        include_query_dimensions=True,
    )
    assert calls == [("product", "widget")]
