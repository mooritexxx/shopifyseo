"""Product image SEO filename and alt builders."""

import re

from shopifyseo.dashboard_ai_engine_parts.images import build_image_optimizer_vision_instruction
from shopifyseo.product_image_seo import (
    infer_image_format_from_bytes,
    is_missing_or_generic_alt,
    is_probably_webp_url,
    is_weak_image_filename,
    normalize_shopify_image_url,
    product_image_seo_suggested_alt,
    product_image_seo_suggested_filename,
    stable_seo_filename_suffix,
)


def test_normalize_shopify_image_url_strips_query():
    a = "https://cdn.shopify.com/s/files/1/1/1/foo.jpg?v=1&width=800"
    b = "https://cdn.shopify.com/s/files/1/1/1/foo.jpg"
    assert normalize_shopify_image_url(a) == normalize_shopify_image_url(b)


def test_normalize_shopify_image_url_http_https_equivalent():
    a = "http://cdn.shopify.com/s/files/1/1/1/foo.jpg"
    b = "https://cdn.shopify.com/s/files/1/1/1/foo.jpg"
    assert normalize_shopify_image_url(a) == normalize_shopify_image_url(b)


def test_infer_image_format_from_bytes_webp():
    # Minimal RIFF/WEBP header (not a valid full file, enough for magic check)
    hdr = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 4
    assert infer_image_format_from_bytes(hdr) == (".webp", "image/webp")


def test_infer_image_format_from_bytes_png():
    assert infer_image_format_from_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x00") == (".png", "image/png")


def test_stable_seo_filename_suffix_is_deterministic():
    assert stable_seo_filename_suffix("gid://shopify/MediaImage/1") == stable_seo_filename_suffix(
        "gid://shopify/MediaImage/1"
    )
    assert len(stable_seo_filename_suffix("a")) == 4
    assert stable_seo_filename_suffix("a") != stable_seo_filename_suffix("b")


def test_weak_filename_heuristics():
    assert is_weak_image_filename("https://x/IMG_001.jpg") is True
    assert is_weak_image_filename("https://x/1200x800.jpg") is True
    assert is_weak_image_filename("https://x/acme-x200-starter-kit-featured-b3xq.webp") is False


def test_is_probably_webp_url():
    assert is_probably_webp_url("https://cdn/x/foo.webp") is True
    assert is_probably_webp_url("https://cdn/x/foo.jpg") is False


def test_missing_or_generic_alt():
    assert is_missing_or_generic_alt("") is True
    assert is_missing_or_generic_alt("image") is True
    assert is_missing_or_generic_alt("Acme X200 starter kit — front view on white background") is False


def test_suggested_alt_roles():
    alt_f = product_image_seo_suggested_alt(
        product_title="Acme X200 Starter Kit",
        role="featured",
        gallery_position=1,
    )
    assert "Acme X200 Starter Kit" in alt_f
    assert len(alt_f) <= 512

    alt_v = product_image_seo_suggested_alt(
        product_title="Acme X200 Starter Kit",
        role="variant",
        gallery_position=1,
        variant_label="Mint Green",
        visual_hint="lifestyle handheld photo",
    )
    assert "Mint Green" in alt_v
    assert "lifestyle" in alt_v.lower()


def test_suggested_filename_multi_image_uniqueness():
    base = dict(product_handle="acme-x200-starter-kit", role="gallery", ext=".webp")
    a = product_image_seo_suggested_filename(**base, gallery_position=1, collision_suffix="aaaa")
    b = product_image_seo_suggested_filename(**base, gallery_position=2, collision_suffix="bbbb")
    c = product_image_seo_suggested_filename(
        product_handle="acme-x200-starter-kit",
        role="featured",
        gallery_position=1,
        collision_suffix="cccc",
        ext=".webp",
    )
    assert a != b != c
    assert a == "acme-x200-starter-kit-aa.webp"
    assert re.search(r"-2-", b)
    assert all(x.endswith(".webp") for x in (a, b, c))


def test_build_image_optimizer_vision_instruction_includes_context():
    s = build_image_optimizer_vision_instruction(
        resource_type="product",
        resource_title="X200 Pod Kit",
        resource_handle="x200-pod-kit",
        role_hint="variant",
        variant_labels=["Menthol", "30ml"],
    )
    assert "product" in s.lower()
    assert "X200 Pod Kit" in s
    assert "x200-pod-kit" in s
    assert "variant" in s.lower()
    assert "Menthol" in s
    assert "512" in s
