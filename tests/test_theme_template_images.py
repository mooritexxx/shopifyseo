"""Theme JSON template image URL extraction for page templates."""

from shopifyseo.theme_template_images import (
    extract_shopify_image_urls_from_theme_json_text,
    page_template_asset_keys,
)


def test_page_template_asset_keys_default_and_suffix():
    assert page_template_asset_keys("") == ["templates/page.json"]
    assert page_template_asset_keys(None) == ["templates/page.json"]
    assert page_template_asset_keys("contact") == [
        "templates/page.contact.json",
        "templates/page.json",
    ]


def test_extract_urls_from_section_settings():
    raw = """
    {
      "sections": {
        "banner": {
          "type": "image-banner",
          "settings": {
            "image": "https://cdn.shopify.com/s/files/1/1/1/files/hero.jpg?v=1",
            "opacity": 50
          }
        }
      },
      "order": ["banner"]
    }
    """
    urls = extract_shopify_image_urls_from_theme_json_text(raw)
    assert len(urls) == 1
    assert "cdn.shopify.com" in urls[0]


def test_extract_urls_from_blocks_and_dedupe():
    raw = """
    {
      "sections": {
        "slideshow": {
          "type": "slideshow",
          "blocks": {
            "a": {
              "type": "slide",
              "settings": {
                "image": "https://cdn.shopify.com/s/files/1/a.jpg"
              }
            },
            "b": {
              "type": "slide",
              "settings": {
                "image": "https://cdn.shopify.com/s/files/1/a.jpg"
              }
            }
          },
          "block_order": ["a", "b"]
        }
      },
      "order": ["slideshow"]
    }
    """
    urls = extract_shopify_image_urls_from_theme_json_text(raw)
    assert len(urls) == 1


def test_extract_img_from_embedded_html_string():
    raw = r"""
    {
      "sections": {
        "custom": {
          "type": "custom-liquid",
          "settings": {
            "liquid": "<img src=\"https://cdn.shopify.com/s/files/1/x.png\" alt=\"x\">"
          }
        }
      },
      "order": ["custom"]
    }
    """
    urls = extract_shopify_image_urls_from_theme_json_text(raw)
    assert len(urls) == 1
    assert urls[0].endswith(".png") or "x.png" in urls[0]
