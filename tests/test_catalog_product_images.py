"""Tests for product gallery extraction during Shopify catalog sync."""

from shopifyseo.shopify_catalog_sync.products import _product_images_for_upsert


def test_prefers_media_mediaimage_rows():
    product = {
        "media": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/MediaImage/1",
                        "alt": "Front",
                        "image": {"url": "https://cdn.shopify.com/a.jpg", "width": 800, "height": 800},
                    }
                },
                {
                    "node": {
                        "id": "gid://shopify/MediaImage/2",
                        "alt": "",
                        "image": {"url": "https://cdn.shopify.com/b.jpg", "width": 400, "height": 400},
                    }
                },
            ]
        },
        "images": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductImage/9",
                        "altText": "",
                        "url": "https://cdn.shopify.com/only-legacy.jpg",
                    }
                }
            ]
        },
    }
    rows = _product_images_for_upsert(product)
    assert len(rows) == 2
    assert rows[0]["id"] == "gid://shopify/MediaImage/1"
    assert rows[0]["altText"] == "Front"
    assert rows[0]["url"] == "https://cdn.shopify.com/a.jpg"
    assert rows[1]["url"] == "https://cdn.shopify.com/b.jpg"


def test_falls_back_to_legacy_images_when_media_empty():
    product = {
        "media": {"edges": []},
        "images": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductImage/1",
                        "altText": "Legacy",
                        "url": "https://cdn.shopify.com/legacy.jpg",
                        "width": 100,
                        "height": 200,
                    }
                }
            ]
        },
    }
    rows = _product_images_for_upsert(product)
    assert len(rows) == 1
    assert rows[0]["id"] == "gid://shopify/ProductImage/1"
    assert rows[0]["altText"] == "Legacy"


def test_skips_video_nodes_and_falls_back():
    product = {
        "media": {"edges": [{"node": {"__typename": "Video", "id": "gid://shopify/Video/1"}}]},
        "images": {
            "edges": [{"node": {"id": "gid://shopify/ProductImage/2", "altText": "", "url": "https://cdn.shopify.com/p.jpg"}}]
        },
    }
    rows = _product_images_for_upsert(product)
    assert len(rows) == 1
    assert rows[0]["id"] == "gid://shopify/ProductImage/2"
