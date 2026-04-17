"""Shopify product media helpers (Image SEO)."""

from shopifyseo.shopify_product_media import match_media_id_for_catalog_image


def test_match_media_prefers_catalog_gid_over_url():
    rows = [
        {"id": "gid://shopify/MediaImage/111", "url": "https://cdn.shopify.com/s/files/1/1/1/a.jpg"},
        {"id": "gid://shopify/MediaImage/222", "url": "https://cdn.shopify.com/s/files/1/1/1/b.jpg"},
    ]
    out = match_media_id_for_catalog_image(
        rows,
        catalog_media_gid="gid://shopify/MediaImage/222",
        catalog_image_url="https://cdn.shopify.com/s/files/1/1/1/stale.jpg",
    )
    assert out == "gid://shopify/MediaImage/222"


def test_match_media_falls_back_to_url_when_gid_missing():
    rows = [
        {"id": "gid://shopify/MediaImage/111", "url": "https://cdn.shopify.com/s/files/1/1/1/a.jpg?v=1"},
    ]
    out = match_media_id_for_catalog_image(
        rows,
        catalog_media_gid="gid://shopify/MediaImage/999",
        catalog_image_url="https://cdn.shopify.com/s/files/1/1/1/a.jpg",
    )
    assert out == "gid://shopify/MediaImage/111"
