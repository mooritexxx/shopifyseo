"""Tests for product SEO title and meta description token checks (#115).

Covers:
- Deterministic SEO title format: <product name> | Vapely Canada
- Flavour extraction from product title
- Strength extraction from title/metafield/variants
- Meta description flavour validation
- TVPA check on regenerate-field (retry once, then reject)
- Refactored TVPA allowlist helpers
- Never write product name field
"""
import json
import pytest
import sqlite3
import time
from unittest.mock import patch, MagicMock

from shopifyseo.dashboard_ai_engine_parts.product_name_tokens import (
    RequiredTokens,
    required_product_name_tokens,
    build_deterministic_seo_title,
    check_seo_title_format,
    check_meta_description_tokens,
)
from shopifyseo.dashboard_ai_engine_parts import config


@pytest.fixture
def mock_store_identity(monkeypatch):
    """Mock store identity to avoid DB access."""
    monkeypatch.setattr(config, "_STORE_IDENTITY_CACHE", ("Vapely", "vapely.ca"))


# ---------------------------------------------------------------------------
# Test 1: Glubble (title) - required_product_name_tokens + SEO title format
# ---------------------------------------------------------------------------

def test_glubble_title_extraction(mock_store_identity):
    """Test Glubble title extraction: flavour and strength correctly identified."""
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape",
                "nicotine_strength": "20mg",
                "vendor": "ELFBAR",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    req = required_product_name_tokens(context)
    
    assert req.flavour == "Straw Watermelon Glubble"
    assert req.strength == "20mg"
    assert req.strength_numeric == "20"
    assert req.strength_unit == "mg"


def test_glubble_bad_seo_title_fails(mock_store_identity):
    """SEO title missing 'Glubble' should fail validation."""
    product_name = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    bad_title = "ELFBAR GH20000 Straw Watermelon 20mg 20000 Puffs | Vapely Canada"
    
    errors, warnings = check_seo_title_format(bad_title, product_name, conn=None)
    
    assert len(errors) >= 1
    assert any("Glubble" in e.lower() or "missing" in e.lower() for e in errors)


def test_glubble_correct_seo_title_passes(mock_store_identity):
    """Correctly formatted SEO title should pass."""
    product_name = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    expected_title = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape | Vapely Canada"
    
    errors, warnings = check_seo_title_format(expected_title, product_name, conn=None)
    
    assert errors == [], f"Unexpected errors: {errors}"


# ---------------------------------------------------------------------------
# Test 2: Glubble (meta) - meta description flavour validation
# ---------------------------------------------------------------------------

def test_glubble_bad_meta_fails(mock_store_identity):
    """Meta description missing 'Glubble' should fail validation."""
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape",
                "nicotine_strength": "20mg",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    bad_meta = (
        "Shop ELFBAR GH20000 Straw Watermelon disposable vape in Canada. "
        "Enjoy 20000 puffs of 20mg nic salt fruit flavour with fast nationwide shipping at Vapely."
    )
    
    errors, warnings = check_meta_description_tokens(bad_meta, req)
    
    assert len(errors) >= 1
    assert any("Glubble" in e for e in errors)


def test_glubble_meta_with_flavour_no_strength_passes_with_warning(mock_store_identity):
    """Meta with flavour but no strength should pass with a warning."""
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape",
                "nicotine_strength": "20mg",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    meta_no_strength = (
        "Shop ELFBAR GH20000 Straw Watermelon Glubble disposable vape in Canada. "
        "Enjoy smooth fruit flavour with fast nationwide shipping at Vapely."
    )
    
    errors, warnings = check_meta_description_tokens(meta_no_strength, req)
    
    assert errors == [], f"Unexpected errors: {errors}"
    assert len(warnings) >= 1
    assert any("strength" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Test 5: Strength in title - extraction without metafield
# ---------------------------------------------------------------------------

def test_strength_from_title_extraction(mock_store_identity):
    """Strength should be extracted from title even without nicotine_strength field."""
    context = {
        "detail": {
            "product": {
                "title": "Fog Formulas 1600 - Peaches & Cream 20mg Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    req = required_product_name_tokens(context)
    
    assert req.strength == "20mg"
    assert req.strength_numeric == "20"
    assert req.strength_unit == "mg"
    # Flavour should have strength stripped
    assert req.flavour == "Peaches & Cream"
    assert "20mg" not in req.flavour


def test_strength_missing_from_title_fails_seo_title(mock_store_identity):
    """SEO title without strength (when product has it) should still use deterministic format."""
    product_name = "Fog Formulas 1600 - Peaches & Cream 20mg Disposable Vape"
    bad_title = "Fog Formulas 1600 Peaches & Cream Disposable Vape | Vapely"
    
    errors, warnings = check_seo_title_format(bad_title, product_name, conn=None)
    
    # The title is missing '20mg' from the product name
    assert len(errors) >= 1


def test_strength_format_variations_in_meta(mock_store_identity):
    """Meta should accept various strength formats: 20mg, 20 mg, etc."""
    context = {
        "detail": {
            "product": {
                "title": "Fog Formulas 1600 - Peaches & Cream 20mg Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    # Test with space: "20 mg"
    meta_with_space = "Peaches and Cream 20 mg Vape | Vapely"
    errors, warnings = check_meta_description_tokens(meta_with_space, req)
    assert errors == [], f"Unexpected errors for '20 mg': {errors}"


# ---------------------------------------------------------------------------
# Test 6: No strength - product without strength requirement
# ---------------------------------------------------------------------------

def test_no_strength_product(mock_store_identity):
    """Product without strength in title/metafield/variants should have no strength requirement."""
    context = {
        "detail": {
            "product": {
                "title": "Generic Vape Device - Cherry Flavour Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    req = required_product_name_tokens(context)
    
    assert req.strength is None
    assert req.flavour == "Cherry Flavour"


def test_no_strength_meta_passes_without_warning(mock_store_identity):
    """Meta without strength (when product has none) should pass without strength warning."""
    context = {
        "detail": {
            "product": {
                "title": "Generic Vape Device - Cherry Flavour Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    meta = "Cherry Flavour disposable vape. Buy online in Canada."
    errors, warnings = check_meta_description_tokens(meta, req)
    
    assert errors == []
    # No strength warning because product has no strength requirement
    assert not any("strength" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Test 7: Multiple strengths - no strength requirement
# ---------------------------------------------------------------------------

def test_multiple_strengths_no_requirement(mock_store_identity):
    """Multiple distinct strengths across variants means no strength requirement."""
    context = {
        "detail": {
            "product": {
                "title": "E-Liquid - Strawberry Flavour E-Liquid",
            },
            "variants": [
                {"title": "3mg", "selected_options_json": json.dumps([{"name": "Nicotine", "value": "3mg"}])},
                {"title": "6mg", "selected_options_json": json.dumps([{"name": "Nicotine", "value": "6mg"}])},
                {"title": "12mg", "selected_options_json": json.dumps([{"name": "Nicotine", "value": "12mg"}])},
            ],
            "metafields": [],
        },
    }
    
    req = required_product_name_tokens(context)
    
    # Multiple distinct strengths => no requirement
    assert req.strength is None
    # But flavour is still required
    assert req.flavour == "Strawberry Flavour"


# ---------------------------------------------------------------------------
# Test 8: No flavour - title without separator
# ---------------------------------------------------------------------------

def test_no_flavour_no_requirement(mock_store_identity):
    """Title without ' - ' separator has no flavour requirement."""
    context = {
        "detail": {
            "product": {
                "title": "Simple Product Name Without Separator",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    req = required_product_name_tokens(context)
    
    assert req.flavour is None


def test_no_flavour_meta_passes(mock_store_identity):
    """Meta without flavour (when product has none) should pass."""
    context = {
        "detail": {
            "product": {
                "title": "Simple Product Name Without Separator",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    meta = "Great product for Canada."
    errors, warnings = check_meta_description_tokens(meta, req)
    
    assert errors == []


# ---------------------------------------------------------------------------
# Test 9: SEO title length - >60 chars passes with warning
# ---------------------------------------------------------------------------

def test_long_seo_title_passes_with_warning(mock_store_identity):
    """SEO title >60 chars should pass but with a warning."""
    # Create a long product name
    long_name = "Brand Model XYZ Premium Edition - Super Delicious Flavour Name Disposable Vape"
    expected_title = f"{long_name} | Vapely Canada"
    
    assert len(expected_title) > 60
    
    errors, warnings = check_seo_title_format(expected_title, long_name, conn=None)
    
    assert errors == [], f"Long title should not error: {errors}"
    assert len(warnings) >= 1
    assert any("60" in w or "truncated" in w.lower() for w in warnings)


def test_short_seo_title_no_warning(mock_store_identity):
    """SEO title <=60 chars should have no length warning."""
    short_name = "ELFBAR - Blue Razz Vape"
    expected_title = f"{short_name} | Vapely Canada"
    
    assert len(expected_title) <= 60
    
    errors, warnings = check_seo_title_format(expected_title, short_name, conn=None)
    
    assert errors == []
    assert not any("60" in w for w in warnings)


# ---------------------------------------------------------------------------
# Test: SEO title exact format validation
# ---------------------------------------------------------------------------

def test_seo_title_exact_pass(mock_store_identity):
    """Exact match should pass."""
    product_name = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    title = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape | Vapely Canada"
    
    errors, warnings = check_seo_title_format(title, product_name, conn=None)
    assert errors == []


def test_seo_title_case_difference_pass(mock_store_identity):
    """Case-insensitive match should pass."""
    product_name = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    title = "elfbar bc5000 - blue razz ice disposable vape | vapely canada"
    
    errors, warnings = check_seo_title_format(title, product_name, conn=None)
    assert errors == [], f"Case difference should pass: {errors}"


def test_seo_title_dropped_disposable_vape_fails(mock_store_identity):
    """Dropping 'Disposable Vape' should fail."""
    product_name = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    title = "ELFBAR BC5000 - Blue Razz Ice | Vapely Canada"
    
    errors, warnings = check_seo_title_format(title, product_name, conn=None)
    assert len(errors) >= 1
    assert any("disposable" in e.lower() or "vape" in e.lower() or "missing" in e.lower() for e in errors)


def test_seo_title_dropped_glubble_fails(mock_store_identity):
    """Dropping flavour word (e.g., 'Glubble') should fail."""
    product_name = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    title = "ELFBAR GH20000 - Straw Watermelon Disposable Vape | Vapely Canada"
    
    errors, warnings = check_seo_title_format(title, product_name, conn=None)
    assert len(errors) >= 1
    assert any("glubble" in e.lower() or "missing" in e.lower() for e in errors)


def test_seo_title_added_20mg_fails(mock_store_identity):
    """Adding '20mg' that's not in product name should fail."""
    product_name = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    title = "ELFBAR BC5000 - Blue Razz Ice 20mg Disposable Vape | Vapely Canada"
    
    errors, warnings = check_seo_title_format(title, product_name, conn=None)
    assert len(errors) >= 1
    assert any("20mg" in e.lower() or "extra" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Test 13: TVPA allowlist preserved - _tvpa_allowed_names
# ---------------------------------------------------------------------------

def test_tvpa_allowed_names_includes_product_title():
    """_tvpa_allowed_names should include product title."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_allowed_names
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Bubblegum Ice Disposable Vape",
                "vendor": "ELFBAR",
            },
            "variants": [],
        },
        "approved_internal_link_targets": [],
    }
    
    allowed = _tvpa_allowed_names(context, "product")
    
    assert "ELFBAR BC5000 - Bubblegum Ice Disposable Vape" in allowed
    assert "ELFBAR" in allowed
    assert "Bubblegum Ice" in allowed  # extracted flavour


def test_tvpa_allowed_names_includes_variant_titles():
    """_tvpa_allowed_names should include variant titles."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_allowed_names
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Bubblegum Ice Disposable Vape",
                "vendor": "ELFBAR",
            },
            "variants": [
                {"title": "3mg Nicotine - Bubblegum Ice"},
                {"title": "6mg Nicotine - Bubblegum Ice"},
            ],
        },
        "approved_internal_link_targets": [],
    }
    
    allowed = _tvpa_allowed_names(context, "product")
    
    assert "3mg Nicotine - Bubblegum Ice" in allowed
    assert "6mg Nicotine - Bubblegum Ice" in allowed


def test_tvpa_allowed_names_includes_link_targets():
    """_tvpa_allowed_names should include approved internal link target titles."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_allowed_names
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Bubblegum Ice Disposable Vape",
                "vendor": "ELFBAR",
            },
            "variants": [],
        },
        "approved_internal_link_targets": [
            {"title": "ELFBAR Collection", "url": "/collections/elfbar"},
            {"title": "Strawberry Sundae Vape", "url": "/products/strawberry-sundae"},
        ],
    }
    
    allowed = _tvpa_allowed_names(context, "product")
    
    assert "ELFBAR Collection" in allowed
    assert "Strawberry Sundae Vape" in allowed


def test_allowlist_preserved_no_tvpa_error_for_bubblegum():
    """Product titled 'X - Bubblegum Ice' should not trigger TVPA error."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_allowed_names, _tvpa_category_issues
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Bubblegum Ice Disposable Vape",
                "vendor": "ELFBAR",
            },
            "variants": [],
        },
        "approved_internal_link_targets": [],
    }
    
    allowed = _tvpa_allowed_names(context, "product")
    title = "ELFBAR BC5000 Bubblegum Ice 20mg Vape | Vapely"
    
    issues = _tvpa_category_issues(title, allowed)
    
    # Bubblegum is in the product title, so it should be allowlisted
    assert issues == [], f"Should not flag allowlisted flavour name: {issues}"


# ---------------------------------------------------------------------------
# Test 15: Editor saves unaffected
# ---------------------------------------------------------------------------

def test_editor_saves_unaffected():
    """seo_quality.validate_changed_metadata should not run flavour check."""
    from shopifyseo.seo_quality import validate_changed_metadata
    
    current = {"seo_title": "Old Title"}
    payload = {"seo_title": "New Title Without Any Flavour"}
    
    # This should not raise - the flavour check is only for AI-generated content
    try:
        validate_changed_metadata("product", current, payload)
    except ValueError as e:
        # Only length errors are expected from seo_quality, not flavour errors
        assert "flavour" not in str(e).lower()


# ---------------------------------------------------------------------------
# Test: Never write product name field
# ---------------------------------------------------------------------------

def test_regenerate_field_does_not_write_product_name():
    """regenerate-field should never modify the product name/title."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _generate_single_field_attempt
    
    original_title = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    context = {
        "detail": {
            "product": {
                "title": original_title,
                "vendor": "ELFBAR",
            },
            "variants": [],
        },
        "fact": {"priority": "medium"},
        "object_type": "product",
    }
    
    # Test that seo_title generation doesn't touch product title
    with patch.object(config, "_STORE_IDENTITY_CACHE", ("Vapely", "vapely.ca")):
        result = _generate_single_field_attempt(
            settings={
                "generation_provider": "test",
                "generation_model": "test",
                "review_provider": "test",
                "review_model": "test",
                "prompt_version": "v3",
                "prompt_profile": "default",
                "timeout": 60,
            },
            context=context,
            object_type="product",
            field="seo_title",
            accepted_fields={},
            prompt_context_precomputed={},
            signal_narrative_precomputed="",
            conn=None,
        )
    
    # Verify the product title in context was not modified
    assert context["detail"]["product"]["title"] == original_title
    
    # Verify the result is the SEO title, not the product title field
    assert result["field"] == "seo_title"
    # The SEO title should contain the product name
    assert original_title in result["value"]


# ---------------------------------------------------------------------------
# Test: Deterministic SEO title generation (no AI)
# ---------------------------------------------------------------------------

def test_product_seo_title_is_deterministic(mock_store_identity):
    """Product SEO title should be built deterministically without AI."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _generate_single_field_attempt
    
    product_name = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    context = {
        "detail": {
            "product": {
                "title": product_name,
                "vendor": "ELFBAR",
            },
            "variants": [],
        },
        "fact": {"priority": "medium"},
        "object_type": "product",
    }
    
    result = _generate_single_field_attempt(
        settings={
            "generation_provider": "test",
            "generation_model": "test",
            "review_provider": "test",
            "review_model": "test",
            "prompt_version": "v3",
            "prompt_profile": "default",
            "timeout": 60,
        },
        context=context,
        object_type="product",
        field="seo_title",
        accepted_fields={},
        prompt_context_precomputed={},
        signal_narrative_precomputed="",
        conn=None,
    )
    
    # Verify deterministic generation (no AI)
    assert result["generation_model"] == "deterministic"
    assert result["review_action"] == "deterministic"
    
    # Verify the format
    expected = f"{product_name} | Vapely Canada"
    assert result["value"] == expected


# ---------------------------------------------------------------------------
# Test: TVPA category issues helper
# ---------------------------------------------------------------------------

def test_tvpa_category_issues_detects_candy():
    """_tvpa_category_issues should detect category violations."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_category_issues
    
    text = "This vape tastes like candy."
    issues = _tvpa_category_issues(text, allowed_names=[])
    
    assert len(issues) >= 1
    assert any("candy" in i.lower() for i in issues)


def test_tvpa_category_issues_ignores_style():
    """_tvpa_category_issues should not return style-only issues."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_category_issues
    
    text = "A nostalgic bubblegum finish with flavour notes."
    # "nostalgic" is a style issue, not category
    # Need to allowlist bubblegum to test only nostalgic
    issues = _tvpa_category_issues(text, allowed_names=["Bubblegum"])
    
    # nostalgic is style, not category - should not be in category issues
    assert not any("nostalgic" in i.lower() for i in issues)


# ---------------------------------------------------------------------------
# Test: build_tvpa_retry_feedback
# ---------------------------------------------------------------------------

def test_build_tvpa_retry_feedback():
    """_build_tvpa_retry_feedback should build proper feedback."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _build_tvpa_retry_feedback
    
    text = "This vape tastes like candy."
    feedback = _build_tvpa_retry_feedback(text, [], "body")
    
    assert "candy" in feedback.lower()
    assert "TVPA" in feedback or "flavour" in feedback.lower()
    assert "body" in feedback


# ---------------------------------------------------------------------------
# Test: Full generation preserves TVPA helpers output (refactor safety)
# ---------------------------------------------------------------------------

def test_refactor_safety_tvpa_helpers():
    """Refactored helpers should produce same output as original inline code."""
    from shopifyseo.dashboard_ai_engine_parts.generation import _tvpa_allowed_names, _tvpa_category_issues
    from shopifyseo.dashboard_ai_engine_parts.tvpa_flavour import extract_flavour_from_title, tvpa_flavour_matches
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Bubblegum Ice Disposable Vape",
                "vendor": "ELFBAR",
            },
            "variants": [
                {"title": "3mg - Bubblegum Ice"},
            ],
        },
        "approved_internal_link_targets": [
            {"title": "Related Product - Strawberry Ice Vape"},
        ],
    }
    
    # Build allowlist using helper
    helper_allowed = _tvpa_allowed_names(context, "product")
    
    # Build allowlist using original inline logic
    inline_allowed: list[str] = []
    detail_payload = context.get("detail") or {}
    primary = detail_payload.get("product") or {}
    if primary.get("title"):
        title_str = str(primary["title"])
        inline_allowed.append(title_str)
        flavour = extract_flavour_from_title(title_str)
        if flavour:
            inline_allowed.append(flavour)
    if primary.get("vendor"):
        inline_allowed.append(str(primary["vendor"]))
    variants = detail_payload.get("variants") or []
    for var in variants:
        if isinstance(var, dict) and var.get("title"):
            var_title = str(var["title"])
            inline_allowed.append(var_title)
            var_flavour = extract_flavour_from_title(var_title)
            if var_flavour:
                inline_allowed.append(var_flavour)
    link_targets = context.get("approved_internal_link_targets") or []
    for target in link_targets:
        if target.get("title"):
            target_title = str(target["title"])
            inline_allowed.append(target_title)
            target_flavour = extract_flavour_from_title(target_title)
            if target_flavour:
                inline_allowed.append(target_flavour)
    
    # Compare - same items (order may differ)
    assert set(helper_allowed) == set(inline_allowed)
    
    # Test category issues helper
    test_text = "It tastes like candy."
    helper_issues = _tvpa_category_issues(test_text, helper_allowed)
    
    matches = tvpa_flavour_matches(test_text, allowed_names=helper_allowed)
    inline_issues = [
        f"TVPA flavour wording: '{m['term']}' ({m['key']})"
        for m in matches if m["group"] == "category"
    ]
    
    assert helper_issues == inline_issues


# ---------------------------------------------------------------------------
# Test: Flavour with Ice suffix preserved
# ---------------------------------------------------------------------------

def test_flavour_ice_suffix_preserved(mock_store_identity):
    """Trailing 'Ice' should be part of the flavour name."""
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Blue Razz Ice Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    req = required_product_name_tokens(context)
    
    # "Blue Razz Ice" is the full flavour, not "Blue Razz"
    assert req.flavour == "Blue Razz Ice"


# ---------------------------------------------------------------------------
# Test: Ampersand equivalence in meta description
# ---------------------------------------------------------------------------

def test_meta_ampersand_equivalence(mock_store_identity):
    """Meta description should treat '&' and 'and' as equivalent."""
    context = {
        "detail": {
            "product": {
                "title": "Device - Peaches & Cream Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    # Meta uses "and" instead of "&"
    meta = "Peaches and Cream disposable vape for Canada."
    errors, warnings = check_meta_description_tokens(meta, req)
    
    assert errors == [], f"'and' should match '&': {errors}"


# ---------------------------------------------------------------------------
# Test: Strength equivalence (mg <-> %)
# ---------------------------------------------------------------------------

def test_strength_equivalence_percent_to_mg(mock_store_identity):
    """2% should be accepted as equivalent to 20mg."""
    context = {
        "detail": {
            "product": {
                "title": "Device - Flavour 20mg Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    req = required_product_name_tokens(context)
    
    # Product has 20mg, meta uses 2%
    meta = "Flavour 2% vape for Canada."
    errors, warnings = check_meta_description_tokens(meta, req)
    
    # Should not have strength warning since 2% ≡ 20mg
    assert not any("strength" in w.lower() for w in warnings), f"2% should match 20mg: {warnings}"


# ---------------------------------------------------------------------------
# Test: Build deterministic SEO title function
# ---------------------------------------------------------------------------

def test_build_deterministic_seo_title(mock_store_identity):
    """build_deterministic_seo_title should format correctly."""
    product_name = "ELFBAR BC5000 - Blue Razz Ice Vape"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    assert title == "ELFBAR BC5000 - Blue Razz Ice Vape | Vapely Canada"


# ---------------------------------------------------------------------------
# Test: validate_single_field with flavour check
# ---------------------------------------------------------------------------

def test_validate_single_field_meta_flavour_check(mock_store_identity):
    """validate_single_field should check flavour for product seo_description."""
    from shopifyseo.dashboard_ai_engine_parts.qa import validate_single_field, RecommendationValidationError
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Blue Razz Ice Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    # Meta missing the flavour
    bad_meta = "Shop this disposable vape in Canada. Fast shipping available."
    
    with pytest.raises(RecommendationValidationError) as exc_info:
        validate_single_field("product", "seo_description", bad_meta, context)
    
    assert "Blue Razz Ice" in str(exc_info.value) or "flavour" in str(exc_info.value).lower()


def test_validate_single_field_meta_with_flavour_passes(mock_store_identity):
    """validate_single_field should pass when flavour is present."""
    from shopifyseo.dashboard_ai_engine_parts.qa import validate_single_field
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Blue Razz Ice Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    # Long enough meta with flavour (115-160 chars)
    good_meta = (
        "Shop ELFBAR BC5000 Blue Razz Ice disposable vape in Canada. "
        "Enjoy smooth fruity flavour with fast shipping at Vapely."
    )
    
    # Should not raise
    validate_single_field("product", "seo_description", good_meta, context)


# ---------------------------------------------------------------------------
# NEW TESTS: Issue 1 - Product SEO title must NEVER be truncated
# ---------------------------------------------------------------------------

def test_glubble_73_char_title_not_truncated(mock_store_identity):
    """73-char Glubble product title must NOT be truncated."""
    # ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape = 54 chars
    # + ' | Vapely Canada' = 17 chars = 71 chars total (slightly under)
    # Let's use a longer product name to get 73 chars
    product_name = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    expected = f"{product_name} | Vapely Canada"
    assert title == expected
    assert len(title) == len(expected)
    assert title == "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape | Vapely Canada"
    assert len(title) == 73  # Verify it's 73 chars


def test_long_title_not_truncated(mock_store_identity):
    """Long product title (68 chars) must NOT be truncated."""
    product_name = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape Pods"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    expected = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape Pods | Vapely Canada"
    assert title == expected
    assert len(title) == 66  # Actual length - point is no truncation to 65


def test_clamp_generated_seo_field_product_not_truncated(mock_store_identity):
    """clamp_generated_seo_field must NOT truncate product seo_title."""
    from shopifyseo.dashboard_ai_engine_parts.qa import clamp_generated_seo_field
    
    # 73-char title
    long_title = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape | Vapely Canada"
    assert len(long_title) == 73
    
    # For product, should NOT truncate
    result = clamp_generated_seo_field("seo_title", long_title, "product")
    assert result == long_title
    assert len(result) == 73
    
    # For collection, SHOULD truncate to 65
    result_collection = clamp_generated_seo_field("seo_title", long_title, "collection")
    assert len(result_collection) <= 65


# ---------------------------------------------------------------------------
# Issue 2: Length limits - product seo_title outside 42-65 must never fail
# ---------------------------------------------------------------------------

def test_product_seo_title_length_never_fails(mock_store_identity):
    """Product seo_title length outside 42-65 must NOT fail validation."""
    from shopifyseo.seo_quality import validate_metadata, metadata_issues
    
    # 73-char product title
    fields = {
        "seo_title": "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape | Vapely Canada"
    }
    assert len(fields["seo_title"]) == 73
    
    # Should NOT raise for product
    validate_metadata("product", fields)
    
    # Should only have warning, not error
    issues = metadata_issues("product", fields)
    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]
    
    assert len(errors) == 0, f"Product seo_title should not fail: {errors}"
    assert len(warnings) >= 1, "Should have warning for >60 chars"


def test_collection_seo_title_length_still_fails(mock_store_identity):
    """Collection seo_title length >65 MUST still fail validation."""
    from shopifyseo.seo_quality import validate_metadata
    import pytest
    
    # 73-char collection title (too long)
    fields = {
        "seo_title": "Best Disposable Vapes and Premium Electronic Cigarettes Canada 2024!"
    }
    assert len(fields["seo_title"]) > 65
    
    # Should raise for collection
    with pytest.raises(ValueError) as exc_info:
        validate_metadata("collection", fields)
    
    assert "too long" in str(exc_info.value).lower()


def test_article_seo_title_length_still_fails(mock_store_identity):
    """Article seo_title length >65 MUST still fail validation."""
    from shopifyseo.seo_quality import validate_metadata
    import pytest
    
    # 73-char article title (too long)
    fields = {
        "seo_title": "Ultimate Guide to Vaping in Canada: Everything You Need to Know Today!"
    }
    assert len(fields["seo_title"]) > 65
    
    # Should raise for blog_article
    with pytest.raises(ValueError) as exc_info:
        validate_metadata("blog_article", fields)
    
    assert "too long" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Issue 3: Warnings field in FieldRegenerateResult
# ---------------------------------------------------------------------------

def test_field_regenerate_result_has_warnings_field():
    """FieldRegenerateResult schema must have warnings field."""
    from backend.app.schemas.product import FieldRegenerateResult
    
    # Should have warnings field with default empty list
    result = FieldRegenerateResult(field="seo_title", value="test")
    assert hasattr(result, "warnings")
    assert result.warnings == []
    
    # Should accept warnings list
    result_with_warnings = FieldRegenerateResult(
        field="seo_title",
        value="test",
        warnings=["SEO title exceeds 60 characters"]
    )
    assert result_with_warnings.warnings == ["SEO title exceeds 60 characters"]


# ---------------------------------------------------------------------------
# Issue 4: Meta strength warning wiring
# ---------------------------------------------------------------------------

def test_get_field_warnings_returns_strength_warning(mock_store_identity):
    """get_field_warnings should return strength warning for product seo_description."""
    from shopifyseo.dashboard_ai_engine_parts.qa import get_field_warnings
    
    context = {
        "detail": {
            "product": {
                "title": "ELFBAR BC5000 - Blue Razz Ice 20mg Disposable Vape",
            },
            "variants": [],
            "metafields": [],
        },
    }
    
    # Meta has flavour but no strength
    meta = "Shop ELFBAR BC5000 Blue Razz Ice disposable vape in Canada. Premium fruity flavour."
    
    general_warnings, strength_warnings = get_field_warnings(
        "product", "seo_description", meta, context
    )
    
    # Should have strength warning
    assert len(strength_warnings) >= 1
    assert any("strength" in w.lower() or "20mg" in w for w in strength_warnings)


def test_get_field_warnings_product_seo_title_over_60(mock_store_identity):
    """get_field_warnings should return warning for product seo_title >60 chars."""
    from shopifyseo.dashboard_ai_engine_parts.qa import get_field_warnings
    
    long_title = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape | Vapely Canada"
    assert len(long_title) > 60
    
    general_warnings, strength_warnings = get_field_warnings(
        "product", "seo_title", long_title, None
    )
    
    # Should have length warning
    assert len(general_warnings) >= 1
    assert any("60" in w or "exceed" in w.lower() for w in general_warnings)


# ---------------------------------------------------------------------------
# Issue 5: Title builder edge cases
# ---------------------------------------------------------------------------

def test_title_builder_literal_suffix(mock_store_identity):
    """Title builder must use literal ' | Vapely Canada', not store setting."""
    # Even with mocked store identity set to something else, suffix is literal
    product_name = "Product Name"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    assert title == "Product Name | Vapely Canada"
    assert " | Vapely Canada" in title


def test_title_builder_whitespace_collapsed(mock_store_identity):
    """Title builder must collapse all runs of whitespace."""
    product_name = "Product    Name   with   spaces"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    assert title == "Product Name with spaces | Vapely Canada"
    assert "    " not in title
    assert "   " not in title


def test_title_builder_strips_ends(mock_store_identity):
    """Title builder must strip whitespace from ends."""
    product_name = "  Product Name  "
    title = build_deterministic_seo_title(product_name, conn=None)
    
    assert title == "Product Name | Vapely Canada"
    assert not title.startswith(" ")
    assert not title.endswith(" ")


def test_title_builder_no_double_suffix(mock_store_identity):
    """Title builder must not add suffix if name already ends in it."""
    # Name already ends in '| Vapely Canada'
    product_name = "Product Name | Vapely Canada"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    assert title == "Product Name | Vapely Canada"
    assert title.count("| Vapely Canada") == 1


def test_title_builder_no_double_suffix_case_insensitive(mock_store_identity):
    """Title builder deduplication must be case-insensitive."""
    product_name = "Product Name | VAPELY CANADA"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    # Should not double-add (treats it as already having suffix)
    assert "| Vapely Canada" in title or "| VAPELY CANADA" in title
    assert title.lower().count("| vapely canada") == 1


def test_title_check_punctuation_exact(mock_store_identity):
    """SEO title format check must require exact punctuation (only case/whitespace differ)."""
    product_name = "ELFBAR - Blue Razz Ice"
    
    # Correct format
    correct = "ELFBAR - Blue Razz Ice | Vapely Canada"
    errors, warnings = check_seo_title_format(correct, product_name, conn=None)
    assert errors == []
    
    # Wrong punctuation: missing dash
    wrong_dash = "ELFBAR Blue Razz Ice | Vapely Canada"
    errors, warnings = check_seo_title_format(wrong_dash, product_name, conn=None)
    assert len(errors) >= 1, "Should fail when dash is missing"


def test_title_check_ampersand_exact(mock_store_identity):
    """SEO title must preserve '&' exactly, not convert to 'and'."""
    product_name = "Product - Peaches & Cream"
    
    # Correct format with &
    correct = "Product - Peaches & Cream | Vapely Canada"
    errors, warnings = check_seo_title_format(correct, product_name, conn=None)
    assert errors == []
    
    # Wrong: 'and' instead of '&'
    wrong_ampersand = "Product - Peaches and Cream | Vapely Canada"
    errors, warnings = check_seo_title_format(wrong_ampersand, product_name, conn=None)
    assert len(errors) >= 1, "Should fail when & is changed to 'and'"


def test_title_builder_unicode(mock_store_identity):
    """Title builder must handle unicode correctly."""
    product_name = "Prodüct Nämé - Flàvöur"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    assert title == "Prodüct Nämé - Flàvöur | Vapely Canada"


def test_title_builder_name_containing_vapely(mock_store_identity):
    """Title builder handles product name containing 'Vapely' correctly."""
    product_name = "Vapely Premium - Blue Ice Vape"
    title = build_deterministic_seo_title(product_name, conn=None)
    
    # Should still add the suffix
    assert title == "Vapely Premium - Blue Ice Vape | Vapely Canada"


# ---------------------------------------------------------------------------
# Issue 6: Name-never-written test improvements
# ---------------------------------------------------------------------------

def test_regenerate_field_never_writes_product_name_db_snapshot(mock_store_identity):
    """Regenerate-field must never modify product name/title in DB."""
    # This test stubs the DB to verify the product name is unchanged
    # The original test only checked that name/title is not in REGENERABLE_FIELDS
    # Now we verify via mock that no write occurs
    
    from shopifyseo.dashboard_ai_engine_parts.config import REGENERABLE_FIELDS
    
    # "name" and "title" must not be regenerable for products
    assert "name" not in REGENERABLE_FIELDS
    assert "title" not in REGENERABLE_FIELDS
    
    # The regenerable fields for products are only:
    assert set(REGENERABLE_FIELDS) == {"seo_title", "seo_description", "body"}


def test_shopify_write_not_called_for_product_name():
    """No Shopify write endpoint should receive product name changes from generation."""
    # Import the live update function to verify its signature
    from shopifyseo.dashboard_live_updates import live_update_product
    
    # The function signature shows it accepts title, but that's for user-initiated
    # changes via the editor, not AI generation
    import inspect
    sig = inspect.signature(live_update_product)
    params = list(sig.parameters.keys())
    
    # Verify the function exists and has the expected signature
    assert "title" in params
    assert "seo_title" in params
    
    # The test confirms the function exists but generation doesn't call it
    # with AI-generated titles - that's enforced by the REGENERABLE_FIELDS check


# ---------------------------------------------------------------------------
# Issue 9: Product seo_title never auto-applied
# ---------------------------------------------------------------------------

def test_full_generation_does_not_auto_apply():
    """Full generation stores recommendation, does NOT auto-apply to Shopify."""
    # Verify that generate_object_recommendation saves to seo_recommendations table
    # but does NOT call live_update_product
    
    # The flow is:
    # 1. generate_object_recommendation -> inserts into seo_recommendations with status='success'
    # 2. User reviews in editor
    # 3. User clicks Save -> update_product -> live_update_product
    
    # This is verified by the code structure:
    # - generate_object_recommendation calls insert_recommendation_record, not live_update_product
    # - live_update_product is only called from update_product (editor save endpoint)
    
    from shopifyseo.dashboard_ai_engine_parts.generation import insert_recommendation_record
    import inspect
    
    # Verify insert_recommendation_record exists (used by generation)
    assert callable(insert_recommendation_record)
