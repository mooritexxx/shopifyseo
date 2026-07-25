"""The trend field must survive FastAPI's response_model filtering.

`response_model` drops any key the Pydantic model does not declare, silently and
without an error. `gsc_page_trend_map` populated `trend` correctly and the
frontend read it correctly, but no schema declared it, so every row arrived
without a trend and the Trend column rendered an em dash for the whole table.

These tests assert the field survives the HTTP boundary, not just the service
call -- a service-level assertion would have passed the entire time the bug was
live.
"""
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.schemas.blog import AllArticleListItem
from backend.app.schemas.content import ContentDetailPayload, ContentListItem
from backend.app.schemas.product import ProductDetailPayload, ProductListItem


client = TestClient(app)

TREND_KEYS = {
    "clicks_current",
    "clicks_previous",
    "clicks_delta_pct",
    "impressions_current",
    "impressions_previous",
    "impressions_delta_pct",
    "series",
}


def _assert_trend_shape(trend):
    assert trend is not None, "trend was dropped from the response"
    # The frontend declares `trend: trendSchema.optional()`, and Zod's optional()
    # rejects null, so emitting null here would fail validation for the whole list.
    assert isinstance(trend, dict)
    assert set(trend) == TREND_KEYS, f"unexpected trend keys: {sorted(trend)}"
    for key in ("clicks_current", "clicks_previous", "impressions_current", "impressions_previous"):
        assert isinstance(trend[key], int)
    for key in ("clicks_delta_pct", "impressions_delta_pct"):
        assert trend[key] is None or isinstance(trend[key], (int, float))
    assert isinstance(trend["series"], list)
    assert all(isinstance(v, int) for v in trend["series"])


def test_every_list_schema_that_carries_trend_declares_it():
    """Guards the actual failure mode: a field present in the service dict but
    absent from the response model."""
    for model in (ProductListItem, ContentListItem, AllArticleListItem,
                  ProductDetailPayload, ContentDetailPayload):
        assert "trend" in model.model_fields, f"{model.__name__} would drop `trend`"


def test_product_list_rows_carry_trend():
    response = client.get("/api/products")
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    if not items:
        return
    for item in items:
        _assert_trend_shape(item.get("trend"))


def test_content_and_article_list_rows_carry_trend():
    for path in ("/api/collections", "/api/pages", "/api/articles"):
        response = client.get(path)
        assert response.status_code == 200, path
        for item in response.json()["data"]["items"]:
            _assert_trend_shape(item.get("trend"))


def test_product_detail_carries_trend():
    listing = client.get("/api/products")
    items = listing.json()["data"]["items"]
    if not items:
        return
    handle = items[0]["handle"]
    response = client.get(f"/api/products/{handle}")
    assert response.status_code == 200
    _assert_trend_shape(response.json()["data"].get("trend"))


def test_trend_deltas_are_present_when_the_catalog_has_click_history():
    """If gsc_page_daily holds history, at least one row should report a real
    delta. Catches a backfill that lands rows without object_type/object_handle,
    which the trend query filters out."""
    from backend.app.db import open_db_connection

    conn = open_db_connection()
    try:
        keyed_rows = conn.execute(
            "SELECT COUNT(*) FROM gsc_page_daily WHERE object_type != '' AND object_handle != ''"
        ).fetchone()[0]
    finally:
        conn.close()
    if not keyed_rows:
        return  # nothing backfilled yet; nothing to assert

    items = client.get("/api/products").json()["data"]["items"]
    assert any(
        (item.get("trend") or {}).get("clicks_delta_pct") is not None
        or (item.get("trend") or {}).get("impressions_delta_pct") is not None
        for item in items
    ), "gsc_page_daily has keyed history but no product reports a trend delta"
