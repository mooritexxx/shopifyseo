"""Opportunity Inbox skips queries for objects that are not live on the Online Store (#109)."""

import pytest

from backend.app.services.opportunities_service import fetch_opportunities, get_opportunity_stats
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def conn(db_conn):
    ensure_dashboard_schema(db_conn)
    return db_conn


def _gsc(conn, object_type, handle, query="q", impressions=100, position=8.0):
    conn.execute(
        "INSERT INTO gsc_query_rows (object_type, object_handle, url, query, clicks, impressions, ctr, position, fetched_at) "
        "VALUES (?, ?, ?, ?, 1, ?, 0.01, ?, 1)",
        (object_type, handle, f"https://x/{handle}", query, impressions, position),
    )


def _article(conn, blog, handle, is_published):
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_shopify_id, blog_handle, title, handle, is_published, tags_json, raw_json, synced_at) "
        "VALUES (?, 'b1', ?, ?, ?, ?, '[]', '{}', '')",
        (f"a-{handle}", blog, handle, handle, is_published),
    )


def _page(conn, handle, is_published):
    conn.execute(
        "INSERT INTO pages (shopify_id, title, handle, is_published, raw_json, synced_at) VALUES (?, ?, ?, ?, '{}', '')",
        (f"pg-{handle}", handle, handle, is_published),
    )


def _product(conn, handle, status="ACTIVE", url="https://x/products/p"):
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status, online_store_url, tags_json, options_json, raw_json, synced_at) "
        "VALUES (?, ?, ?, ?, ?, '[]', '[]', '{}', '')",
        (f"p-{handle}", handle, handle, status, url),
    )


def _seed(conn):
    conn.execute("INSERT INTO blogs (shopify_id, title, handle, tags_json, raw_json, synced_at) VALUES ('b1', 'Canada', 'canada', '[]', '{}', '')")
    _article(conn, "canada", "live-article", 1)
    _article(conn, "canada", "redirected-article", 0)
    _page(conn, "live-page", 1)
    _page(conn, "legacy-page", None)
    _page(conn, "draft-page", 0)
    _product(conn, "live-prod")
    _product(conn, "draft-prod", status="DRAFT")
    _product(conn, "no-url-prod", url="")
    for t, h in [
        ("blog_article", "canada/live-article"),
        ("blog_article", "canada/redirected-article"),
        ("page", "live-page"),
        ("page", "legacy-page"),
        ("page", "draft-page"),
        ("product", "live-prod"),
        ("product", "draft-prod"),
        ("product", "no-url-prod"),
        ("collection", "not-in-catalog"),
    ]:
        _gsc(conn, t, h)
    conn.commit()


def test_inbox_skips_non_live_objects_and_keeps_unknown(conn):
    _seed(conn)
    out = fetch_opportunities(conn, limit=100)
    handles = sorted(i["object_handle"] for i in out["items"])
    assert handles == sorted(
        ["canada/live-article", "live-page", "legacy-page", "live-prod", "not-in-catalog"]
    )
    assert out["total"] == 5


def test_inbox_page_type_filter_still_applies(conn):
    _seed(conn)
    out = fetch_opportunities(conn, page_type="blog_article", limit=100)
    assert [i["object_handle"] for i in out["items"]] == ["canada/live-article"]
    assert out["total"] == 1


def test_stats_exclude_non_live_objects(conn):
    _seed(conn)
    stats = get_opportunity_stats(conn)
    assert stats["total_queries"] == 5
    assert stats["by_page_type"] == {"Blog Article": 1, "Page": 2, "Product": 1, "Collection": 1}
    assert stats["striking_distance"] == 5
