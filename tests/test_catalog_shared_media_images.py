"""Regression: one Shopify MediaImage attached to two products must not break the sync.

2026-10-17: ``fileUpdate(referencesToAdd)`` attached MediaImage 47749919342955 to
both ABT e-liquid Blue Raspberry and Blueberry Raspberry. Shopify then returned
that media id under both products and the Shopify stage died with a duplicate
key on ``product_images.shopify_id``.
"""
from __future__ import annotations

import sqlite3
from unittest import mock

from shopifyseo.shopify_catalog_sync.db import ensure_schema
from shopifyseo.shopify_catalog_sync.image_pk import (
    TARGET_PK,
    migrate_product_images_pk,
    needs_migration,
    product_images_pk,
)
from shopifyseo.shopify_catalog_sync.products import sync_products, upsert_product

SHARED = "gid://shopify/MediaImage/47749919342955"


def _media(gid: str, alt: str, url: str) -> dict:
    return {"node": {"id": gid, "alt": alt, "image": {"url": url, "width": 800, "height": 800}}}


def _product(num: int, handle: str, media: list[dict]) -> dict:
    return {
        "id": f"gid://shopify/Product/{num}",
        "legacyResourceId": str(num),
        "handle": handle,
        "title": handle,
        "vendor": "ABT",
        "productType": "",
        "status": "ACTIVE",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-10-16T20:08:55Z",
        "publishedAt": "2026-01-01T00:00:00Z",
        "descriptionHtml": "",
        "seo": {"title": "", "description": ""},
        "tags": [],
        "totalInventory": 1,
        "tracksInventory": True,
        "onlineStoreUrl": f"https://example.com/products/{handle}",
        "variants": {"edges": []},
        "media": {"edges": media},
        "images": {"edges": []},
        "metafields": {"edges": []},
        "collections": {"edges": []},
        "options": [],
    }


def _two_products_sharing_media() -> list[dict]:
    shared = _media(SHARED, "ABT e-liquid Blueberry Raspberry", "https://cdn.shopify.com/tq.webp")
    return [
        _product(14937239847275, "abt-e-liquid-blue-raspberry", [shared]),
        _product(
            14937240273259,
            "abt-e-liquid-blueberry-raspberry",
            [
                shared,
                _media("gid://shopify/MediaImage/47749919408491", "", "https://cdn.shopify.com/wz.webp"),
                shared,  # repeated within one gallery: stored once
            ],
        ),
    ]


def _rows(conn) -> list[tuple]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT product_shopify_id, shopify_id, position FROM product_images "
            "ORDER BY product_shopify_id, position"
        ).fetchall()
    ]


def test_sync_stores_shared_media_for_each_product(testdb):
    conn = testdb.connect()
    try:
        ensure_schema(conn)
        assert tuple(product_images_pk(conn)[0]) == TARGET_PK
        products = _two_products_sharing_media()
        with mock.patch(
            "shopifyseo.shopify_catalog_sync.products.resolve_product_metaobject_labels"
        ):
            for _ in range(2):  # resync is idempotent
                for product in products:
                    upsert_product(conn, product, "2026-10-17T13:45:00+00:00")
                conn.commit()
        assert _rows(conn) == [
            ("gid://shopify/Product/14937239847275", SHARED, 1),
            ("gid://shopify/Product/14937240273259", SHARED, 1),
            ("gid://shopify/Product/14937240273259", "gid://shopify/MediaImage/47749919408491", 2),
        ]
    finally:
        conn.close()


def test_sync_products_end_to_end_with_shared_media(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "catalog.sqlite3"
    with mock.patch(
        "shopifyseo.shopify_catalog_sync.products.fetch_metaobjects_by_ids", return_value=[]
    ):
        result = sync_products(db_path, 50, products=_two_products_sharing_media())
    assert result["products_synced"] == 2
    conn = sqlite3.connect(db_path)
    try:
        assert len(_rows(conn)) == 3
    finally:
        conn.close()


def test_migrates_legacy_single_column_pk_sqlite(tmp_path):
    conn = sqlite3.connect(tmp_path / "legacy.sqlite3")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE product_images (
          shopify_id TEXT PRIMARY KEY,
          product_shopify_id TEXT NOT NULL,
          alt_text TEXT,
          url TEXT NOT NULL,
          width INTEGER,
          height INTEGER,
          raw_json TEXT NOT NULL,
          synced_at TEXT NOT NULL
        );
        ALTER TABLE product_images ADD COLUMN position INTEGER;
        INSERT INTO product_images VALUES
          ('gid://shopify/MediaImage/1', 'gid://shopify/Product/1', 'a', 'u', 1, 1, '{}', 's', 1);
        """
    )
    assert needs_migration(conn)
    assert migrate_product_images_pk(conn) is True
    assert tuple(product_images_pk(conn)[0]) == TARGET_PK
    assert not needs_migration(conn)
    assert migrate_product_images_pk(conn) is False
    conn.execute(
        "INSERT INTO product_images (shopify_id, product_shopify_id, url, raw_json, synced_at, position) "
        "VALUES ('gid://shopify/MediaImage/1', 'gid://shopify/Product/2', 'u', '{}', 's', 1)"
    )
    assert conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0] == 2
    conn.close()


def test_migrates_legacy_single_column_pk_on_test_backend(testdb):
    """Same upgrade on the suite's backend (Postgres in the backend-postgres job)."""
    conn = testdb.connect()
    try:
        conn.execute(
            """
            CREATE TABLE product_images (
              shopify_id TEXT PRIMARY KEY,
              product_shopify_id TEXT,
              position INTEGER,
              alt_text TEXT,
              url TEXT NOT NULL,
              width INTEGER,
              height INTEGER,
              raw_json TEXT NOT NULL,
              synced_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO product_images (shopify_id, product_shopify_id, position, url, raw_json, synced_at) "
            "VALUES (?, ?, 1, 'u', '{}', 's')",
            (SHARED, "gid://shopify/Product/1"),
        )
        conn.commit()
        assert needs_migration(conn)
        assert migrate_product_images_pk(conn) is True
        assert tuple(product_images_pk(conn)[0]) == TARGET_PK
        assert migrate_product_images_pk(conn) is False
        conn.execute(
            "INSERT INTO product_images (shopify_id, product_shopify_id, position, url, raw_json, synced_at) "
            "VALUES (?, ?, 1, 'u', '{}', 's')",
            (SHARED, "gid://shopify/Product/2"),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM product_images").fetchone()[0] == 2
    finally:
        conn.close()
