"""Blog article image filename + alt helpers (SEO + accessibility)."""

import re

from shopifyseo.dashboard_ai_engine_parts.images import (
    _build_featured_alt,
    _build_section_alt,
    _fallback_alt_from_image_prompt,
    _sanitize_image_alt,
    _seo_blog_asset_filename,
)

_SUFFIX_RE = r"-[23456789abcdefghjkmnpqrtvwxyz]{4}\."


def test_seo_filename_uses_alt_text_as_primary_source():
    name = _seo_blog_asset_filename(
        alt_text="SMOK Novo 5 pod system charging on wooden desk",
        headline="Everything About SMOK Novo Pod Systems",
        topic="novo, pod kits",
        ext=".webp",
    )
    assert name.endswith(".webp")
    assert re.search(_SUFFIX_RE, name)
    # Alt text keywords should appear in the filename
    assert "smok" in name
    assert "novo" in name
    assert "charging" in name
    assert "wooden-desk" in name or "desk" in name


def test_seo_filename_falls_back_to_headline_when_alt_short():
    name = _seo_blog_asset_filename(
        alt_text="Vape",
        headline="Vape Guide",
        topic="disposables",
        ext=".png",
    )
    assert name.endswith(".png")
    assert re.search(_SUFFIX_RE, name)
    # Alt too short (< 8 chars slug), should fall back to headline/topic stem
    assert "vape-guide" in name


def test_seo_filename_falls_back_when_alt_empty():
    name = _seo_blog_asset_filename(
        alt_text="",
        headline="Best Disposable Vapes for Canadian Beginners",
        topic="disposable vapes, beginners, canada",
        ext=".webp",
    )
    assert name.endswith(".webp")
    assert re.search(_SUFFIX_RE, name)
    # Should use headline/topic fallback
    assert len(name) > 10


def test_seo_filename_trims_long_alt_at_word_boundary():
    name = _seo_blog_asset_filename(
        alt_text="Well lit close up photograph of a red and black vape device sitting on a marble countertop next to a cup of coffee",
        ext=".webp",
    )
    assert name.endswith(".webp")
    # Stem should be trimmed to ~50 chars, not cut mid-word
    stem = name.rsplit("-", 1)[0]  # remove suffix
    stem = stem.rsplit(".", 1)[0]  # remove ext if needed
    assert len(stem) <= 55  # 50 + a little slack from rsplit
    assert not stem.endswith("-")


def test_seo_filename_no_role_labels():
    """Filenames should not contain structural labels like 'cover', 'intro', 'section'."""
    name = _seo_blog_asset_filename(
        alt_text="Elf Bar disposable vape in mint green flavour",
        ext=".webp",
    )
    assert "cover" not in name
    assert "intro" not in name
    assert "section" not in name


def test_sanitize_image_alt_truncates():
    long = "word " * 40
    out = _sanitize_image_alt(long, max_len=40)
    assert len(out) <= 40
    assert "…" in out or len(out.split()) < len(long.split())


def test_sanitize_image_alt_strips_labels():
    assert _sanitize_image_alt('Alt text: A red box on a table') == "A red box on a table"


def test_fallback_alt_from_prompt_shortens():
    prompt = (
        "Wide blog cover photograph for a Canadian online vape retail article. Topic: pods. "
        "Premium editorial hero. No text, logos, or watermarks. Photorealistic, well-lit, commercial quality."
    )
    out = _fallback_alt_from_image_prompt(prompt, max_len=80)
    assert len(out) <= 80
    assert "Wide blog cover" not in out


# --- _build_section_alt ---

def test_section_alt_uses_heading():
    alt = _build_section_alt("Choosing the Right Pod System", "SMOK Novo Guide")
    assert "Choosing the Right Pod System" in alt
    # Should NOT contain article prose or boilerplate
    assert "Context from" not in alt
    assert "editorial photograph" not in alt


def test_section_alt_short_heading_gets_context():
    alt = _build_section_alt("Safety", "E-Liquid Recipe Guide: Understanding Ingredients")
    assert "Safety" in alt.split("—")[0]
    # Short heading should get extra context from headline
    assert len(alt.split()) > 1


def test_section_alt_empty_heading_uses_headline():
    alt = _build_section_alt("", "Best Disposable Vapes for Beginners")
    assert "Best Disposable Vapes for Beginners" in alt


def test_section_alt_max_length():
    long_heading = "A " * 100
    alt = _build_section_alt(long_heading, "Title")
    assert len(alt) <= 125


# --- _build_featured_alt ---

def test_featured_alt_uses_headline():
    alt = _build_featured_alt("E-Liquid Recipe Guide: Understanding Ingredients", "e-liquid, ingredients")
    assert "E-Liquid Recipe Guide" in alt
    # Should NOT contain prompt boilerplate
    assert "Wide blog cover" not in alt
    assert "Canadian online" not in alt


def test_featured_alt_falls_back_to_topic():
    alt = _build_featured_alt("", "disposable vapes, pod systems, coils")
    assert "disposable vapes" in alt


def test_featured_alt_empty():
    alt = _build_featured_alt("", "")
    assert len(alt) > 0
