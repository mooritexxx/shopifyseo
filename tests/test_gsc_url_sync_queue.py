"""GSC per-URL cache classification for sync queue building."""

from shopifyseo.dashboard_google._cache import CACHE_TTLS, _write_cache_payload, ensure_google_cache_schema
from shopifyseo.dashboard_google._gsc import (
    _url_detail_cache_key,
    gsc_url_detail_cache_meta_for_sync,
    gsc_url_detail_needs_refresh,
)


def _prepare(db_conn):
    ensure_google_cache_schema(db_conn)
    return db_conn


def test_gsc_url_detail_needs_refresh_without_row(db_conn) -> None:
    conn = _prepare(db_conn)
    site = "https://example.com/"
    url = "https://example.com/products/a"
    assert gsc_url_detail_needs_refresh(conn, url, site_url=site, gsc_period="mtd") is True
    meta = gsc_url_detail_cache_meta_for_sync(conn, url, site_url=site, gsc_period="mtd")
    assert meta.get("exists") is False
    assert meta.get("stale") is True


def test_gsc_url_detail_needs_refresh_with_fresh_row(db_conn) -> None:
    conn = _prepare(db_conn)
    site = "sc-domain:example.com"
    url = "https://shop.example.com/pages/about"
    key = _url_detail_cache_key(site, url, "mtd")
    _write_cache_payload(
        conn,
        cache_key=key,
        cache_type="search_console_url",
        payload={"url": url, "page_rows": [], "query_rows": []},
        ttl_seconds=CACHE_TTLS["search_console_url"],
        object_type="page",
        object_handle="about",
        url=url,
    )
    assert gsc_url_detail_needs_refresh(conn, url, site_url=site, gsc_period="mtd") is False
    meta = gsc_url_detail_cache_meta_for_sync(conn, url, site_url=site, gsc_period="mtd")
    assert meta.get("exists") is True
    assert meta.get("stale") is False
