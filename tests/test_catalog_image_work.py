"""Tests for unified catalog image URL registry (discovery + DB parity)."""

from shopifyseo.catalog_image_work import (
    CatalogImageRegistry,
    count_catalog_image_urls_discover,
    count_catalog_images_for_cache_db,
)
from shopifyseo.shopify_catalog_sync.db import ensure_schema


def test_registry_prefers_gid_over_catalogurl_for_same_norm() -> None:
    reg = CatalogImageRegistry()
    u = "https://cdn.shopify.com/s/files/1/1/x.jpg?v=1"
    reg.register(u, preferred_key=None)
    assert len(reg) == 1
    assert next(iter(reg.expected_cache_ids())).startswith("catalogurl:")
    reg.register(u, preferred_key="gid://shopify/MediaImage/99")
    assert reg.expected_cache_ids() == frozenset({"gid://shopify/MediaImage/99"})


def test_count_discover_empty_payloads() -> None:
    assert (
        count_catalog_image_urls_discover(
            products=[],
            collections=[],
            pages=[],
            articles_by_blog_id={},
        )
        == 0
    )


def test_count_db_empty_schema(tmp_path) -> None:
    import sqlite3

    db_path = tmp_path / "t.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    conn.commit()
    assert count_catalog_images_for_cache_db(conn) == 0
    conn.close()
