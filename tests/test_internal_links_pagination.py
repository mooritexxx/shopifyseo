"""Tests for internal-link suggestions pagination (offset/cursor + total count).

Test Requirements:
1. No overlap or skips across pages
2. Total matches filters
3. Offset past the end
4. Default unchanged
5. Cursor pagination
6. No Shopify pushes
"""
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import internal_links as router
from internal_links_support import BASE, Shopify
from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links import shopify_io, pipeline


def seed_suggestions(conn: sqlite3.Connection, count: int = 1200) -> list[int]:
    """Seed link_suggestions with diverse data for pagination testing.

    Creates rows with:
    - Deliberate duplicate scores (to test id tie-breaker)
    - Mix of statuses (suggested, dismissed, applied)
    - Mix of source_types (product, collection, page)
    - Mix of source_handles
    - Unique source+target combinations (using row index in handles)

    Returns list of suggestion IDs for the 'suggested' status (for verification).
    """
    conn.execute(
        "INSERT OR IGNORE INTO products (shopify_id, handle, title, status, description_html, tags_json, options_json, raw_json, synced_at) "
        "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', '<p>Body</p>', '[]', '[]', '{}', 'now')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO collections (shopify_id, handle, title, raw_json, synced_at) "
        "VALUES ('gid://shopify/Collection/1', 'target-col', 'Target Collection', '{}', 'now')"
    )

    suggested_ids = []
    statuses = ["suggested", "suggested", "suggested", "dismissed", "applied"]
    source_types = ["product", "collection", "page", "blog_article"]

    for i in range(count):
        status = statuses[i % len(statuses)]
        source_type = source_types[i % len(source_types)]
        source_handle = f"handle-{i}"
        target_handle = f"target-{i}"
        score = 1.0 - (i // 10) * 0.01

        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at) "
            "VALUES (?, ?, 'collection', ?, 'phrase_wrap', 'anchor text', ?, ?, ?)",
            (source_type, source_handle, target_handle, score, status, i),
        )
        if status == "suggested":
            suggested_ids.append(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    conn.commit()
    return suggested_ids


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Create test API with seeded database."""
    path = tmp_path / "pagination.sqlite"
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)

    suggested_ids = seed_suggestions(conn, count=1200)

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(router, "open_db_connection", connect)
    monkeypatch.setattr(router, "_base_url", lambda _: BASE)
    monkeypatch.setattr(pipeline, "generate_link_suggestions", Mock(return_value=0))

    live = Shopify()
    monkeypatch.setattr(shopify_io, "fetch_body", live.fetch)
    monkeypatch.setattr(shopify_io, "push_body", live.push)

    yield TestClient(app), conn, live, suggested_ids
    conn.close()


def test_no_overlap_or_skips_across_pages(api):
    """Test that limit=500 at offset=0, 500, 1000 returns pairwise-disjoint id sets."""
    client, conn, live, suggested_ids = api

    page1 = client.get("/api/internal-links/suggestions?status=suggested&limit=500&offset=0")
    assert page1.status_code == 200
    data1 = page1.json()
    assert data1["ok"]
    ids1 = [r["id"] for r in data1["data"]]

    page2 = client.get("/api/internal-links/suggestions?status=suggested&limit=500&offset=500")
    assert page2.status_code == 200
    data2 = page2.json()
    ids2 = [r["id"] for r in data2["data"]]

    page3 = client.get("/api/internal-links/suggestions?status=suggested&limit=500&offset=1000")
    assert page3.status_code == 200
    data3 = page3.json()
    ids3 = [r["id"] for r in data3["data"]]

    assert len(set(ids1) & set(ids2)) == 0, "Page 1 and 2 overlap"
    assert len(set(ids2) & set(ids3)) == 0, "Page 2 and 3 overlap"
    assert len(set(ids1) & set(ids3)) == 0, "Page 1 and 3 overlap"

    all_ids = ids1 + ids2 + ids3
    expected_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM link_suggestions WHERE status='suggested' ORDER BY score DESC, id ASC"
        ).fetchall()
    ]

    assert len(all_ids) == len(expected_ids), "Different number of rows"
    assert all_ids == expected_ids, "Order differs from direct SQL query"

    live.push.assert_not_called()


def test_total_matches_filters(api):
    """Test that meta.total and X-Total-Count match SELECT COUNT(*) for various filters."""
    client, conn, live, _ = api

    filters = [
        {"status": "suggested"},
        {"status": "dismissed"},
        {"status": "suggested", "source_type": "product"},
        {"status": "suggested", "source_type": "collection", "source_handle": "handle-1"},
    ]

    for filt in filters:
        params = "&".join(f"{k}={v}" for k, v in filt.items())
        res = client.get(f"/api/internal-links/suggestions?{params}")
        assert res.status_code == 200
        data = res.json()

        api_total = data["meta"]["total"]
        header_total = int(res.headers["X-Total-Count"])

        where_clauses = [f"{k} = ?" for k in filt]
        where_sql = " AND ".join(where_clauses)
        db_total = conn.execute(
            f"SELECT COUNT(*) FROM link_suggestions WHERE {where_sql}", list(filt.values())
        ).fetchone()[0]

        assert api_total == db_total, f"meta.total mismatch for {filt}"
        assert header_total == db_total, f"X-Total-Count mismatch for {filt}"
        assert data["meta"]["total"] == api_total, "total should be same on every page"

    live.push.assert_not_called()


def test_offset_past_the_end(api):
    """Test offset beyond total returns empty data with correct meta."""
    client, conn, live, _ = api

    total = conn.execute(
        "SELECT COUNT(*) FROM link_suggestions WHERE status='suggested'"
    ).fetchone()[0]

    res = client.get(f"/api/internal-links/suggestions?status=suggested&offset={total + 10}")
    assert res.status_code == 200
    data = res.json()

    assert data["data"] == []
    assert data["meta"]["total"] == total
    assert data["meta"]["has_more"] is False
    assert data["meta"]["next_offset"] is None
    assert data["meta"]["next_cursor"] is None
    assert int(res.headers["X-Total-Count"]) == total

    live.push.assert_not_called()


def test_default_unchanged(api):
    """Test that default behavior (no limit/offset) returns at most 100 rows with same first 100 ids."""
    client, conn, live, _ = api

    default_res = client.get("/api/internal-links/suggestions")
    assert default_res.status_code == 200
    default_data = default_res.json()

    explicit_res = client.get("/api/internal-links/suggestions?status=suggested&limit=100&offset=0")
    assert explicit_res.status_code == 200
    explicit_data = explicit_res.json()

    default_ids = [r["id"] for r in default_data["data"]]
    explicit_ids = [r["id"] for r in explicit_data["data"]]

    assert len(default_ids) <= 100, "Default should return at most 100"
    assert default_ids == explicit_ids, "Default should match explicit offset=0&limit=100"

    for row in default_data["data"]:
        assert row["status"] == "suggested", "Default should only return suggested"
        assert "ai_enabled" in row, "Row should have ai_enabled"
        assert "pending_operation" in row, "Row should have pending_operation"
        assert "weak_anchor_warning" in row, "Row should have weak_anchor_warning"

    limit_501 = client.get("/api/internal-links/suggestions?limit=501")
    assert limit_501.status_code == 422

    offset_neg = client.get("/api/internal-links/suggestions?offset=-1")
    assert offset_neg.status_code == 422

    live.push.assert_not_called()


def test_cursor_pagination(api):
    """Test cursor-based pagination yields every filtered id exactly once in same order.
    
    Also verifies that the last non-empty page has has_more=False and next_cursor=None.
    """
    client, conn, live, _ = api

    expected_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM link_suggestions WHERE status='suggested' ORDER BY score DESC, id ASC"
        ).fetchall()
    ]

    cursor_ids = []
    cursor = None
    page = 0
    max_pages = 20
    last_page_data = None

    while page < max_pages:
        if cursor:
            res = client.get(f"/api/internal-links/suggestions?status=suggested&limit=100&cursor={cursor}")
        else:
            res = client.get("/api/internal-links/suggestions?status=suggested&limit=100")

        assert res.status_code == 200
        data = res.json()

        page_ids = [r["id"] for r in data["data"]]
        cursor_ids.extend(page_ids)

        assert data["meta"]["total"] == len(expected_ids), "Total should match full filtered count"

        # Track the last page with data
        if page_ids:
            last_page_data = data

        if not data["meta"]["has_more"]:
            # Verify last page signals end correctly
            assert data["meta"]["next_cursor"] is None, "next_cursor should be None when has_more is False"
            break

        assert data["meta"]["next_cursor"] is not None, "next_cursor should exist when has_more is True"
        cursor = data["meta"]["next_cursor"]
        page += 1

    # Verify we visited every row exactly once
    assert cursor_ids == expected_ids, "Cursor walk should match direct SQL order"
    assert len(cursor_ids) == len(set(cursor_ids)), "Each ID should appear exactly once"

    # Verify the last non-empty page correctly reported end of data
    assert last_page_data is not None, "Should have at least one page with data"
    assert last_page_data["meta"]["has_more"] is False, "Last page should have has_more=False"
    assert last_page_data["meta"]["next_cursor"] is None, "Last page should have next_cursor=None"

    live.push.assert_not_called()


def test_bad_cursor_returns_400(api):
    """Test malformed cursor returns 400."""
    client, conn, live, _ = api

    res = client.get("/api/internal-links/suggestions?cursor=invalid-base64")
    assert res.status_code == 400
    body = res.json()
    assert "Malformed cursor" in (body.get("detail") or body.get("error", {}).get("message", ""))

    res2 = client.get("/api/internal-links/suggestions?cursor=eyJpbnZhbGlkIjp0cnVlfQ==")
    assert res2.status_code == 400

    live.push.assert_not_called()


def test_cursor_ignores_offset(api):
    """Test that when cursor is provided, offset is ignored."""
    client, conn, live, _ = api

    res1 = client.get("/api/internal-links/suggestions?status=suggested&limit=10")
    assert res1.status_code == 200
    data1 = res1.json()
    cursor = data1["meta"]["next_cursor"]
    assert cursor is not None

    res2 = client.get(f"/api/internal-links/suggestions?status=suggested&limit=10&cursor={cursor}")
    res3 = client.get(f"/api/internal-links/suggestions?status=suggested&limit=10&cursor={cursor}&offset=999")

    assert res2.status_code == 200
    assert res3.status_code == 200

    ids2 = [r["id"] for r in res2.json()["data"]]
    ids3 = [r["id"] for r in res3.json()["data"]]
    assert ids2 == ids3, "Offset should be ignored when cursor is present"

    live.push.assert_not_called()


def test_stable_ordering_with_tied_scores(api):
    """Test that rows with equal scores are ordered deterministically by id."""
    client, conn, live, _ = api

    conn.execute("UPDATE link_suggestions SET score = 0.5 WHERE status = 'suggested'")
    conn.commit()

    res = client.get("/api/internal-links/suggestions?status=suggested&limit=500")
    assert res.status_code == 200
    ids = [r["id"] for r in res.json()["data"]]

    expected = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM link_suggestions WHERE status='suggested' ORDER BY score DESC, id ASC LIMIT 500"
        ).fetchall()
    ]

    assert ids == expected, "With tied scores, ordering should be by id ASC"

    live.push.assert_not_called()


def test_existing_api_tests_still_pass(api):
    """Ensure backward compatibility - read routes still work."""
    client, conn, live, _ = api

    for route in ["summary", "suggestions", "orphans", "settings", "applied"]:
        res = client.get(f"/api/internal-links/{route}")
        assert res.status_code == 200 and res.json()["ok"], f"{route} should still work"

    live.push.assert_not_called()


def test_cursor_walk_matches_offset_walk(api):
    """Test that cursor-based and offset-based pagination return the same id sequence."""
    client, conn, live, _ = api
    
    limit = 100
    
    # Offset-based walk
    offset_ids = []
    offset = 0
    while True:
        res = client.get(f"/api/internal-links/suggestions?status=suggested&limit={limit}&offset={offset}")
        assert res.status_code == 200
        data = res.json()
        page_ids = [r["id"] for r in data["data"]]
        offset_ids.extend(page_ids)
        if not data["meta"]["has_more"]:
            break
        offset = data["meta"]["next_offset"]
    
    # Cursor-based walk
    cursor_ids = []
    cursor = None
    while True:
        if cursor:
            res = client.get(f"/api/internal-links/suggestions?status=suggested&limit={limit}&cursor={cursor}")
        else:
            res = client.get(f"/api/internal-links/suggestions?status=suggested&limit={limit}")
        assert res.status_code == 200
        data = res.json()
        page_ids = [r["id"] for r in data["data"]]
        cursor_ids.extend(page_ids)
        if not data["meta"]["has_more"]:
            break
        cursor = data["meta"]["next_cursor"]
    
    assert offset_ids == cursor_ids, "Offset walk and cursor walk should return the same ids in the same order"
    
    live.push.assert_not_called()


@pytest.fixture
def api_exact_multiple(tmp_path, monkeypatch):
    """Create test API where total is an exact multiple of page size (limit).
    
    Creates exactly 300 'suggested' rows so with limit=100, we get exactly 3 full pages.
    """
    path = tmp_path / "pagination_exact.sqlite"
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)

    # Create exactly 300 suggested rows (3 pages of 100)
    for i in range(300):
        source_handle = f"exact-handle-{i}"
        target_handle = f"exact-target-{i}"
        score = 1.0 - (i // 10) * 0.01
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at) "
            "VALUES ('product', ?, 'collection', ?, 'phrase_wrap', 'anchor', ?, 'suggested', ?)",
            (source_handle, target_handle, score, i),
        )
    conn.commit()

    def connect():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(router, "open_db_connection", connect)
    monkeypatch.setattr(router, "_base_url", lambda _: BASE)
    monkeypatch.setattr(pipeline, "generate_link_suggestions", Mock(return_value=0))

    live = Shopify()
    monkeypatch.setattr(shopify_io, "fetch_body", live.fetch)
    monkeypatch.setattr(shopify_io, "push_body", live.push)

    yield TestClient(app), conn, live
    conn.close()


def test_cursor_pagination_exact_multiple_of_limit(api_exact_multiple):
    """Test cursor pagination when total is an exact multiple of limit.
    
    With 300 rows and limit=100, we should get exactly 3 full pages.
    The 3rd page (last) should have has_more=False and next_cursor=None.
    """
    client, conn, live = api_exact_multiple
    
    limit = 100
    total = conn.execute("SELECT COUNT(*) FROM link_suggestions WHERE status='suggested'").fetchone()[0]
    assert total == 300, "Should have exactly 300 suggested rows"
    
    expected_ids = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM link_suggestions WHERE status='suggested' ORDER BY score DESC, id ASC"
        ).fetchall()
    ]
    
    cursor_ids = []
    cursor = None
    pages = []
    
    while True:
        if cursor:
            res = client.get(f"/api/internal-links/suggestions?status=suggested&limit={limit}&cursor={cursor}")
        else:
            res = client.get(f"/api/internal-links/suggestions?status=suggested&limit={limit}")
        
        assert res.status_code == 200
        data = res.json()
        page_ids = [r["id"] for r in data["data"]]
        cursor_ids.extend(page_ids)
        pages.append({
            "count": len(page_ids),
            "has_more": data["meta"]["has_more"],
            "next_cursor": data["meta"]["next_cursor"],
        })
        
        if not data["meta"]["has_more"]:
            break
        cursor = data["meta"]["next_cursor"]
    
    # Should have exactly 3 pages
    assert len(pages) == 3, f"Expected 3 pages, got {len(pages)}"
    
    # First two pages should have has_more=True
    assert pages[0]["has_more"] is True, "Page 1 should have has_more=True"
    assert pages[0]["next_cursor"] is not None, "Page 1 should have next_cursor"
    assert pages[1]["has_more"] is True, "Page 2 should have has_more=True"
    assert pages[1]["next_cursor"] is not None, "Page 2 should have next_cursor"
    
    # Last page (page 3) should have has_more=False
    assert pages[2]["has_more"] is False, "Page 3 (last) should have has_more=False"
    assert pages[2]["next_cursor"] is None, "Page 3 (last) should have next_cursor=None"
    assert pages[2]["count"] == 100, "Last page should still have 100 rows"
    
    # All IDs visited exactly once in correct order
    assert cursor_ids == expected_ids, "Cursor walk should match direct SQL order"
    assert len(cursor_ids) == 300, "Should visit all 300 rows"
    
    live.push.assert_not_called()
