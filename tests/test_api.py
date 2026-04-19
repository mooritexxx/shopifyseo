import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import content as content_router
from backend.app.services import dashboard_service


client = TestClient(app)


def test_summary_contract():
    response = client.get("/api/summary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "counts" in payload["data"]
    assert "last_dashboard_sync_at" in payload["data"]
    assert "metrics" in payload["data"]
    assert "gsc_site" in payload["data"]
    assert "ai_learning" not in payload["data"]
    assert "available" in payload["data"]["gsc_site"]
    gsc = payload["data"]["gsc_site"]
    assert "url_segment" in gsc
    assert gsc["url_segment"] == "all"
    for row in gsc["series"]:
        assert "date" in row and "clicks" in row and "impressions" in row
        assert isinstance(row["clicks"], int)
        assert isinstance(row["impressions"], int)
        assert "ctr_pct" in row
        assert "position" in row
    cc = payload["data"]["catalog_completion"]
    assert "products" in cc
    assert "articles" in cc
    assert cc["products"]["total"] >= 0
    for seg_name, seg in cc.items():
        for k in ("total", "missing_meta", "meta_complete", "pct_meta_complete"):
            assert k in seg
        assert 0 <= seg["pct_meta_complete"] <= 100
    ga4 = payload["data"]["ga4_site"]
    assert "available" in ga4
    assert "series" in ga4
    for row in ga4["series"]:
        assert "date" in row and "sessions" in row and "views" in row
        assert isinstance(row["sessions"], int)
        assert isinstance(row["views"], int)
    if ga4.get("available") and ga4.get("current"):
        cur = ga4["current"]
        for k in ("new_users", "avg_session_duration", "bounce_rate"):
            assert k in cur
    idx = payload["data"]["indexing_rollup"]
    assert idx["total"] >= 0
    assert "indexed" in idx
    assert "by_type" in idx
    for t in ("product", "collection", "page", "blog_article"):
        assert t in idx["by_type"]
        b = idx["by_type"][t]
        for k in ("total", "indexed", "not_indexed", "needs_review", "unknown"):
            assert k in b
    og = payload["data"]["overview_goals"]
    assert set(og.keys()) == {
        "gsc_daily_clicks",
        "gsc_daily_impressions",
        "ga4_daily_sessions",
        "ga4_daily_views",
    }
    bd = payload["data"]["gsc_property_breakdowns"]
    assert "available" in bd
    assert "window" in bd
    for k in ("country", "device", "searchAppearance"):
        assert k in bd
        assert "rows" in bd[k]
    assert isinstance(payload["data"]["gsc_queries"], list)
    assert isinstance(payload["data"]["gsc_pages"], list)
    assert "start_date" in payload["data"]["gsc_performance_period"]
    assert "end_date" in payload["data"]["gsc_performance_period"]
    assert isinstance(payload["data"]["gsc_performance_error"], str)


def test_summary_gsc_segment_query_normalizes_unknown():
    r = client.get("/api/summary?gsc_segment=nope")
    assert r.status_code == 200
    assert r.json()["data"]["gsc_site"]["url_segment"] == "all"


def test_summary_gsc_segment_products():
    r = client.get("/api/summary?gsc_segment=products")
    assert r.status_code == 200
    assert r.json()["data"]["gsc_site"]["url_segment"] == "products"


def test_summary_gsc_property_breakdowns_follows_gsc_period(monkeypatch):
    calls: list[dict] = []

    def fake_breakdowns(conn, *, site_url, period_mode, anchor, current_start, current_end, refresh=False, **kwargs):
        calls.append(
            {
                "period_mode": period_mode,
                "start": current_start.isoformat(),
                "end": current_end.isoformat(),
            }
        )
        # Mark slices as cached so summary does not run a second live refresh pass.
        empty = {
            "rows": [],
            "error": "",
            "_cache": {"exists": True, "stale": False, "fetched_at": 1, "expires_at": 2**31 - 1},
            "top_bucket_impressions_pct_vs_prior": None,
        }
        return {
            "available": bool(site_url),
            "period_mode": period_mode,
            "anchor_date": anchor.isoformat(),
            "window": {"start_date": current_start.isoformat(), "end_date": current_end.isoformat()},
            "error": "",
            "country": {**empty},
            "device": {**empty},
            "searchAppearance": {**empty},
            "errors": [],
        }

    monkeypatch.setattr(dashboard_service.dg, "get_gsc_property_breakdowns_cached", fake_breakdowns)

    def fake_qp_tables(conn, **kwargs):
        empty = {
            "rows": [],
            "error": "",
            "_cache": {"exists": True, "stale": False, "fetched_at": 1, "expires_at": 2**31 - 1},
        }
        anchor = kwargs["anchor"]
        return {
            "available": True,
            "period_mode": kwargs["period_mode"],
            "anchor_date": anchor.isoformat(),
            "window": {
                "start_date": kwargs["current_start"].isoformat(),
                "end_date": kwargs["current_end"].isoformat(),
            },
            "url_segment": kwargs.get("url_segment", "all"),
            "queries": {**empty},
            "pages": {**empty},
            "error": "",
        }

    monkeypatch.setattr(dashboard_service.dg, "get_gsc_query_page_tables_cached", fake_qp_tables)

    r_default = client.get("/api/summary")
    assert r_default.status_code == 200
    bd_default = r_default.json()["data"]["gsc_property_breakdowns"]
    assert bd_default["period_mode"] == "rolling_30d"

    r_roll = client.get("/api/summary?gsc_period=rolling_30d")
    assert r_roll.status_code == 200
    bd_roll = r_roll.json()["data"]["gsc_property_breakdowns"]
    assert bd_roll["period_mode"] == "rolling_30d"
    assert bd_roll["window"]["start_date"] == bd_default["window"]["start_date"]

    r_since = client.get("/api/summary?gsc_period=since_2026_02_15")
    assert r_since.status_code == 200
    bd_since = r_since.json()["data"]["gsc_property_breakdowns"]
    assert bd_since["period_mode"] == "since_2026_02_15"

    r_mtd = client.get("/api/summary?gsc_period=mtd")
    assert r_mtd.status_code == 200
    bd_mtd = r_mtd.json()["data"]["gsc_property_breakdowns"]
    assert bd_mtd["period_mode"] == "mtd"
    assert len(calls) >= 1
    assert calls[-1]["period_mode"] == "mtd"
    assert bd_mtd["window"]["start_date"] == calls[-1]["start"]
    assert bd_mtd["window"]["end_date"] == calls[-1]["end"]

    r_fm = client.get("/api/summary?gsc_period=full_months")
    assert r_fm.status_code == 200
    bd_fm = r_fm.json()["data"]["gsc_property_breakdowns"]
    assert bd_fm["period_mode"] == "full_months"
    assert calls[-1]["period_mode"] == "full_months"
    assert bd_fm["window"]["start_date"] == calls[-1]["start"]
    assert bd_fm["window"]["end_date"] == calls[-1]["end"]
    assert (bd_fm["window"]["start_date"], bd_fm["window"]["end_date"]) != (
        bd_mtd["window"]["start_date"],
        bd_mtd["window"]["end_date"],
    )
    assert (bd_default["window"]["start_date"], bd_default["window"]["end_date"]) != (
        bd_mtd["window"]["start_date"],
        bd_mtd["window"]["end_date"],
    )
    assert (bd_since["window"]["start_date"], bd_since["window"]["end_date"]) != (
        bd_roll["window"]["start_date"],
        bd_roll["window"]["end_date"],
    )


def test_products_contract():
    response = client.get("/api/products?limit=5")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["limit"] == 5
    assert isinstance(payload["data"]["items"], list)
    assert payload["data"]["total"] >= len(payload["data"]["items"])
    for it in payload["data"]["items"]:
        assert "gsc_segment_flags" in it
        assert "has_dimensional" in it["gsc_segment_flags"]


def test_product_detail_includes_gsc_queries_from_gsc_payload(monkeypatch):
    prod = client.get("/api/products?limit=1").json()["data"]["items"]
    if not prod:
        pytest.skip("no products in database")
    handle = prod[0]["handle"]

    fake_rows = [
        {"keys": ["vape test query"], "clicks": 2, "impressions": 9, "ctr": 0.222, "position": 4.1},
        {"keys": ["zz zero clicks"], "clicks": 0, "impressions": 21, "ctr": 0.0, "position": 12.0},
    ]

    def fake_load(kind, handle_arg, *, conn, gsc_period="mtd"):
        assert kind == "product" and handle_arg == handle
        return {
            "gsc_detail": {"query_rows": fake_rows},
            "inspection_detail": None,
            "site_url": "https://example.com/",
            "ga4_summary": None,
            "pagespeed_detail": None,
            "errors": {},
        }

    monkeypatch.setattr("backend.app.services.product_service.load_object_signals", fake_load)
    r = client.get(f"/api/products/{handle}")
    assert r.status_code == 200
    data = r.json()["data"]
    assert "gsc_queries" in data
    qs = data["gsc_queries"]
    assert len(qs) == 2
    assert qs[0]["query"] == "vape test query"
    assert qs[0]["clicks"] == 2
    assert qs[1]["query"] == "zz zero clicks"
    assert qs[1]["impressions"] == 21
    assert qs[0]["ctr"] == pytest.approx(0.222)
    assert qs[1]["position"] == pytest.approx(12.0)


def test_product_detail_not_found_uses_error_envelope():
    response = client.get("/api/products/not-a-real-handle")
    assert response.status_code == 404
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "http_404"


def test_status_endpoints():
    sync_response = client.get("/api/sync-status")
    ai_response = client.get("/api/ai-status")
    assert sync_response.status_code == 200
    assert ai_response.status_code == 200
    assert sync_response.json()["ok"] is True
    assert ai_response.json()["ok"] is True


def test_collections_and_pages_contract():
    collections_response = client.get("/api/collections?limit=3")
    pages_response = client.get("/api/pages?limit=3")
    assert collections_response.status_code == 200
    assert pages_response.status_code == 200
    assert collections_response.json()["ok"] is True
    assert pages_response.json()["ok"] is True
    for it in collections_response.json()["data"]["items"]:
        assert "gsc_segment_flags" in it
        assert "has_dimensional" in it["gsc_segment_flags"]
    for it in pages_response.json()["data"]["items"]:
        assert "gsc_segment_flags" in it
        assert "has_dimensional" in it["gsc_segment_flags"]


def test_settings_shopify_test_errors_without_credentials():
    response = client.post("/api/settings/shopify-test", json={})
    assert response.status_code == 500
    body = response.json()
    assert body.get("ok") is False
    assert "error" in body or "detail" in body


def test_operations_contract():
    settings_response = client.get("/api/settings")
    google_response = client.get("/api/google-signals")
    assert settings_response.status_code == 200
    assert google_response.status_code == 200
    assert settings_response.json()["ok"] is True
    assert google_response.json()["ok"] is True
    data = settings_response.json()["data"]
    vals = data["values"]
    assert "dataforseo_api_login" in vals
    assert isinstance(vals["dataforseo_api_login"], str)
    sr = data["sync_scope_ready"]
    for key in ("shopify", "gsc", "ga4", "index", "pagespeed"):
        assert key in sr
        assert isinstance(sr[key], bool)
    gsig = google_response.json()["data"]
    assert "gsc_property_breakdowns" in gsig
    bd = gsig["gsc_property_breakdowns"]
    for key in ("country", "device", "searchAppearance", "window", "errors"):
        assert key in bd
    for dim in ("country", "device", "searchAppearance"):
        assert "rows" in bd[dim] and "cache" in bd[dim]


def test_product_regenerate_field_bad_field():
    response = client.post(
        "/api/products/not-a-real-handle/regenerate-field",
        json={"field": "invalid_field", "accepted_fields": {}},
    )
    assert response.status_code in (400, 404, 500)
    payload = response.json()
    assert payload["ok"] is False


def test_collections_regenerate_field_bad_field():
    response = client.post(
        "/api/collections/not-a-real-handle/regenerate-field",
        json={"field": "invalid_field", "accepted_fields": {}},
    )
    assert response.status_code in (400, 404, 500)
    payload = response.json()
    assert payload["ok"] is False


def test_pages_regenerate_field_bad_field():
    response = client.post(
        "/api/pages/not-a-real-handle/regenerate-field",
        json={"field": "invalid_field", "accepted_fields": {}},
    )
    assert response.status_code in (400, 404, 500)
    payload = response.json()
    assert payload["ok"] is False


def test_auth_start_redirects_when_not_configured():
    response = client.get("/auth/google/start", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location
    assert "/app/google-signals" in location or "accounts.google.com/o/oauth2" in location


def test_collection_update_uses_targeted_refresh(monkeypatch):
    calls: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(dashboard_service, "open_db_connection", lambda: DummyConn())
    monkeypatch.setattr(
        dashboard_service.dq,
        "fetch_collection_detail",
        lambda conn, handle: {
            "collection": {
                "shopify_id": "gid://shopify/Collection/123",
                "handle": handle,
                "title": "Disposable Vapes",
                "seo_title": "Disposable Vapes Canada",
                "seo_description": "Shop disposable vapes in Canada.",
                "description_html": "<p>Updated collection copy.</p>",
                "gsc_clicks": 0,
                "gsc_impressions": 0,
                "gsc_ctr": 0,
                "gsc_position": 0,
                "gsc_last_fetched_at": None,
                "ga4_sessions": 0,
                "ga4_views": 0,
                "ga4_avg_session_duration": 0,
                "ga4_last_fetched_at": None,
                "index_status": "",
                "index_coverage": "",
                "google_canonical": "",
                "index_last_fetched_at": None,
                "pagespeed_performance": None,
                "pagespeed_status": "",
            },
            "products": [],
            "metafields": [],
            "workflow": None,
            "recommendation": None,
            "recommendation_event": None,
            "recommendation_history": [],
        },
    )
    monkeypatch.setattr(
        dashboard_service,
        "live_update_collection",
        lambda db_path, collection_id, title, seo_title, seo_description, body_html: calls.setdefault(
            "live_update_collection",
            {
                "db_path": db_path,
                "collection_id": collection_id,
                "title": title,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "body_html": body_html,
            },
        ),
    )
    monkeypatch.setattr(
        dashboard_service.dq,
        "apply_saved_collection_fields_from_editor",
        lambda conn, shopify_id, **kwargs: calls.setdefault("apply_local_collection", {"shopify_id": shopify_id, **kwargs}),
    )
    monkeypatch.setattr(
        dashboard_service.dq,
        "set_workflow_state",
        lambda conn, kind, handle, status, notes: calls.setdefault(
            "workflow",
            {"kind": kind, "handle": handle, "status": status, "notes": notes},
        ),
    )
    monkeypatch.setattr(
        dashboard_service,
        "refresh_object_structured_seo_data",
        lambda conn, kind, handle: calls.setdefault("refresh", {"kind": kind, "handle": handle}),
    )
    monkeypatch.setattr(dashboard_service, "clear_last_error", lambda: None)
    monkeypatch.setattr(
        content_router,
        "get_content_detail",
        lambda kind, handle, gsc_period="mtd": {
            "object_type": kind,
            "current": {"title": "Disposable Vapes"},
            "draft": {
                "title": "Disposable Vapes",
                "seo_title": "Disposable Vapes Canada",
                "seo_description": "Shop disposable vapes in Canada.",
                "body_html": "<p>Updated collection copy.</p>",
                "workflow_status": "Ready",
                "workflow_notes": "Reviewed",
            },
            "workflow": {"status": "Ready", "notes": "Reviewed", "updated_at": None},
            "recommendation": {
                "summary": "",
                "status": "not_generated",
                "model": "",
                "created_at": None,
                "error_message": "",
                "details": {},
            },
            "recommendation_history": [],
            "signal_cards": [],
            "related_items": [],
            "metafields": [],
            "opportunity": {"score": 0, "priority": "Low", "reasons": []},
        },
    )

    response = client.post(
        "/api/collections/disposables/update",
        json={
            "title": "Disposable Vapes",
            "seo_title": "Disposable Vapes Canada",
            "seo_description": "Shop disposable vapes in Canada.",
            "body_html": "<p>Updated collection copy.</p>",
            "workflow_status": "Ready",
            "workflow_notes": "Reviewed",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["message"] == "Collection saved"
    assert calls["live_update_collection"] == {
        "db_path": dashboard_service.DB_PATH,
        "collection_id": "gid://shopify/Collection/123",
        "title": "Disposable Vapes",
        "seo_title": "Disposable Vapes Canada",
        "seo_description": "Shop disposable vapes in Canada.",
        "body_html": "<p>Updated collection copy.</p>",
    }
    assert calls["workflow"] == {
        "kind": "collection",
        "handle": "disposables",
        "status": "Ready",
        "notes": "Reviewed",
    }
    assert calls["refresh"] == {"kind": "collection", "handle": "disposables"}
    assert calls["apply_local_collection"] == {
        "shopify_id": "gid://shopify/Collection/123",
        "title": "Disposable Vapes",
        "seo_title": "Disposable Vapes Canada",
        "seo_description": "Shop disposable vapes in Canada.",
        "description_html": "<p>Updated collection copy.</p>",
    }


def test_page_bulk_save_uses_current_page_drafts(monkeypatch):
    calls: list[dict[str, str]] = []

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(dashboard_service, "open_db_connection", lambda: DummyConn())
    monkeypatch.setattr(
        dashboard_service.dq,
        "fetch_all_pages",
        lambda conn: [
            {
                "shopify_id": "gid://shopify/OnlineStorePage/10",
                "handle": "about-us",
                "seo_title": "About Our Store",
                "seo_description": "Learn about our store and product selection.",
                "body": "<p>About page body.</p>",
            },
            {
                "shopify_id": "gid://shopify/OnlineStorePage/11",
                "handle": "empty-page",
                "seo_title": "",
                "seo_description": "",
                "body": "",
            },
        ],
    )
    monkeypatch.setattr(
        dashboard_service,
        "live_update_page",
        lambda db_path, page_id, title, seo_title, seo_description, body_html: calls.append(
            {
                "db_path": db_path,
                "page_id": page_id,
                "title": title,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "body_html": body_html,
            }
        ),
    )
    monkeypatch.setattr(
        dashboard_service,
        "refresh_object_structured_seo_data",
        lambda conn, kind, handle: None,
    )
    monkeypatch.setattr(
        dashboard_service.dq,
        "apply_saved_page_fields_from_editor",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(dashboard_service, "clear_last_error", lambda: None)

    response = client.post("/api/pages/save-meta")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["message"] == "Saved page SEO content for 1 pages"
    assert payload["data"]["result"] == {
        "saved": 1,
        "skipped": 1,
        "total": 2,
        "skipped_handles": ["empty-page"],
    }
    assert calls == [
        {
            "db_path": dashboard_service.DB_PATH,
            "page_id": "gid://shopify/OnlineStorePage/10",
            "title": "",
            "seo_title": "About Our Store",
            "seo_description": "Learn about our store and product selection.",
            "body_html": "<p>About page body.</p>",
        }
    ]


def test_page_detail_contract():
    response = client.get("/api/pages/contact")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["object_type"] == "page"
    assert payload["data"]["draft"]["title"] == payload["data"]["current"]["title"]


def test_page_bulk_save_returns_json_error_when_shopify_write_fails(monkeypatch):
    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(dashboard_service, "open_db_connection", lambda: DummyConn())
    monkeypatch.setattr(
        dashboard_service.dq,
        "fetch_all_pages",
        lambda conn: [
            {
                "shopify_id": "gid://shopify/OnlineStorePage/10",
                "handle": "about-us",
                "seo_title": "About Our Store",
                "seo_description": "Learn about our store and product selection.",
                "body": "<p>About page body.</p>",
            }
        ],
    )
    monkeypatch.setattr(
        dashboard_service,
        "live_update_page",
        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit("Shopify API connection error")),
    )

    response = client.post("/api/pages/save-meta")

    assert response.status_code == 500
    payload = response.json()
    assert payload["ok"] is False
    assert payload["error"]["message"] == "Shopify API connection error"


def test_page_update_uses_targeted_refresh(monkeypatch):
    calls: dict[str, object] = {}

    class DummyConn:
        def close(self):
            return None

    monkeypatch.setattr(dashboard_service, "open_db_connection", lambda: DummyConn())
    monkeypatch.setattr(
        dashboard_service.dq,
        "fetch_page_detail",
        lambda conn, handle: {
            "page": {
                "shopify_id": "gid://shopify/Page/123",
                "handle": handle,
                "title": "Contact",
                "seo_title": "Contact Us",
                "seo_description": "Get in touch with our team.",
                "body": "<p>Contact us.</p>",
                "body_length": 18,
                "updated_at": "2026-03-18T00:00:00Z",
                "gsc_clicks": 0,
                "gsc_impressions": 0,
                "gsc_ctr": 0,
                "gsc_position": 0,
                "gsc_last_fetched_at": None,
                "ga4_sessions": 0,
                "ga4_views": 0,
                "ga4_avg_session_duration": 0,
                "ga4_last_fetched_at": None,
                "index_status": "",
                "index_coverage": "",
                "google_canonical": "",
                "index_last_fetched_at": None,
                "pagespeed_performance": None,
                "pagespeed_seo": None,
                "pagespeed_status": "",
            },
            "related_collections": [],
            "related_products": [],
            "workflow": None,
            "recommendation": None,
            "recommendation_event": None,
            "recommendation_history": [],
        },
    )
    monkeypatch.setattr(
        dashboard_service,
        "live_update_page",
        lambda db_path, page_id, title, seo_title, seo_description, body_html: calls.setdefault(
            "live_update_page",
            {
                "db_path": db_path,
                "page_id": page_id,
                "title": title,
                "seo_title": seo_title,
                "seo_description": seo_description,
                "body_html": body_html,
            },
        ),
    )
    monkeypatch.setattr(
        dashboard_service.dq,
        "set_workflow_state",
        lambda conn, kind, handle, status, notes: calls.setdefault(
            "workflow",
            {"kind": kind, "handle": handle, "status": status, "notes": notes},
        ),
    )
    monkeypatch.setattr(
        dashboard_service,
        "refresh_object_structured_seo_data",
        lambda conn, kind, handle: calls.setdefault("refresh", {"kind": kind, "handle": handle}),
    )
    monkeypatch.setattr(
        dashboard_service.dq,
        "apply_saved_page_fields_from_editor",
        lambda conn, shopify_id, **kwargs: calls.setdefault("apply_local_page", {"shopify_id": shopify_id, **kwargs}),
    )
    monkeypatch.setattr(dashboard_service, "clear_last_error", lambda: None)
    monkeypatch.setattr(
        content_router,
        "get_content_detail",
        lambda kind, handle, gsc_period="mtd": {
            "object_type": kind,
            "current": {"title": "Contact"},
            "draft": {
                "title": "Contact",
                "seo_title": "Contact Us",
                "seo_description": "Get in touch with our team.",
                "body_html": "<p>Contact us.</p>",
                "workflow_status": "Ready",
                "workflow_notes": "Reviewed",
            },
            "workflow": {"status": "Ready", "notes": "Reviewed", "updated_at": None},
            "recommendation": {
                "summary": "",
                "status": "not_generated",
                "model": "",
                "created_at": None,
                "error_message": "",
                "details": {},
            },
            "recommendation_history": [],
            "signal_cards": [],
            "related_items": [],
            "metafields": [],
            "opportunity": {"score": 0, "priority": "Low", "reasons": [], "handle": handle, "title": "Contact", "object_type": kind, "gsc_impressions": 0, "gsc_clicks": 0, "gsc_position": 0, "ga4_sessions": 0, "pagespeed_performance": None},
        },
    )

    response = client.post(
        "/api/pages/contact/update",
        json={
            "title": "Contact",
            "seo_title": "Contact Us",
            "seo_description": "Get in touch with our team.",
            "body_html": "<p>Contact us.</p>",
            "workflow_status": "Ready",
            "workflow_notes": "Reviewed",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["message"] == "Page saved"
    assert calls["live_update_page"] == {
        "db_path": dashboard_service.DB_PATH,
        "page_id": "gid://shopify/Page/123",
        "title": "Contact",
        "seo_title": "Contact Us",
        "seo_description": "Get in touch with our team.",
        "body_html": "<p>Contact us.</p>",
    }
    assert calls["workflow"] == {
        "kind": "page",
        "handle": "contact",
        "status": "Ready",
        "notes": "Reviewed",
    }
    assert calls["refresh"] == {"kind": "page", "handle": "contact"}
    assert calls["apply_local_page"] == {
        "shopify_id": "gid://shopify/Page/123",
        "title": "Contact",
        "seo_title": "Contact Us",
        "seo_description": "Get in touch with our team.",
        "body_html": "<p>Contact us.</p>",
    }
