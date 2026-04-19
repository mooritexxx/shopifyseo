"""Tests for sync progress helpers (Shopify aggregate + progress pair semantics)."""

from shopifyseo.dashboard_actions._sync import (
    SYNC_PIPELINE_ORDER,
    _normalize_sync_scopes,
    shopify_aggregate_progress,
    sync_progress_pair,
)


def test_sync_progress_pair_pagespeed_queueing():
    state = {
        "active_scope": "pagespeed",
        "pagespeed_phase": "queueing",
        "pagespeed_queue_total": 100,
        "pagespeed_queue_completed": 30,
    }
    assert sync_progress_pair(state) == (30, 100)


def test_sync_progress_pair_structured_scope_uses_dedicated_counters():
    state = {
        "active_scope": "structured",
        "stage": "updating_structured_seo",
        "structured_total": 1,
        "structured_done": 0,
    }
    assert sync_progress_pair(state) == (0, 1)


def test_shopify_aggregate_progress_includes_blog_articles():
    state = {
        "products_total": 10,
        "collections_total": 5,
        "pages_total": 2,
        "blogs_total": 1,
        "blog_articles_total": 7,
        "images_total": 3,
        "products_synced": 10,
        "collections_synced": 5,
        "pages_synced": 2,
        "blogs_synced": 1,
        "blog_articles_synced": 7,
        "images_synced": 0,
    }
    assert shopify_aggregate_progress(state) == (25, 28)


def test_normalize_sync_scopes_follows_pipeline_order_not_ui_toggle_order():
    scope, scopes = _normalize_sync_scopes("custom", ["structured", "shopify", "pagespeed", "gsc"])
    assert scope == "custom"
    assert scopes == ["shopify", "gsc", "pagespeed", "structured"]
    assert scopes == [s for s in SYNC_PIPELINE_ORDER if s in set(scopes)]
