"""Tests for SEO description length validation, retry logic, and spelling checks."""

import pytest

from shopifyseo.dashboard_ai_engine_parts.qa import (
    _score_description,
    check_title_puff_redundancy,
    description_needs_retry,
    validate_commonwealth_spelling,
    validate_single_field,
    RecommendationValidationError,
)
from shopifyseo.dashboard_ai_engine_parts.config import (
    DESCRIPTION_LIMIT,
    DESCRIPTION_RETRY_FLOOR,
    DESCRIPTION_TARGET_MIN,
    DESCRIPTION_TARGET_MAX,
)


class TestDescriptionLimits:
    """Verify the new 160-char limit configuration."""

    def test_description_limit_is_160(self):
        assert DESCRIPTION_LIMIT == 160

    def test_description_target_max_is_160(self):
        assert DESCRIPTION_TARGET_MAX == 160

    def test_product_description_target_min_is_150(self):
        assert DESCRIPTION_TARGET_MIN["product"] == 150

    def test_product_description_retry_floor_matches_target_min(self):
        # Retry floor should match target min so any below-target output gets a retry
        assert DESCRIPTION_RETRY_FLOOR["product"] == DESCRIPTION_TARGET_MIN["product"]
        assert DESCRIPTION_RETRY_FLOOR["product"] == 150


class TestScoreDescription:
    """Test _score_description scoring logic."""

    def test_score_perfect_at_160_chars(self):
        desc = "x" * 160
        score, issues = _score_description("product", desc)
        assert score == 1.0
        assert len(issues) == 0

    def test_score_perfect_at_155_chars(self):
        desc = "x" * 155
        score, issues = _score_description("product", desc)
        assert score == 1.0
        assert len(issues) == 0

    def test_score_good_at_150_chars(self):
        desc = "x" * 150
        score, issues = _score_description("product", desc)
        assert score == 1.0
        assert len(issues) == 0

    def test_score_below_target_at_145_chars(self):
        desc = "x" * 145
        score, issues = _score_description("product", desc)
        assert 0.5 < score < 1.0
        assert any("below target" in i for i in issues)

    def test_score_below_target_at_140_chars(self):
        desc = "x" * 140
        score, issues = _score_description("product", desc)
        assert 0.5 < score < 1.0
        assert any("below target" in i for i in issues)

    def test_score_too_short_at_100_chars(self):
        desc = "x" * 100
        score, issues = _score_description("product", desc)
        assert score == 0.2
        assert any("too short" in i for i in issues)

    def test_score_too_long_at_165_chars(self):
        desc = "x" * 165
        score, issues = _score_description("product", desc)
        assert score == 0.8
        assert any("too long" in i for i in issues)


class TestDescriptionNeedsRetry:
    """Test description_needs_retry retry floor logic."""

    def test_retry_not_needed_at_160_chars(self):
        desc = "x" * 160
        needs_retry, reason = description_needs_retry("product", desc)
        assert not needs_retry
        assert reason == ""

    def test_retry_not_needed_at_150_chars(self):
        desc = "x" * 150
        needs_retry, reason = description_needs_retry("product", desc)
        assert not needs_retry
        assert reason == ""

    def test_retry_needed_at_149_chars(self):
        # 149 is below retry floor (150), so retry is needed
        desc = "x" * 149
        needs_retry, reason = description_needs_retry("product", desc)
        assert needs_retry
        assert "too short" in reason

    def test_retry_needed_at_144_chars(self):
        # This was the observed gap case - 144 chars should trigger retry
        desc = "x" * 144
        needs_retry, reason = description_needs_retry("product", desc)
        assert needs_retry
        assert "too short" in reason

    def test_retry_needed_at_140_chars(self):
        # 140 is below retry floor (150), so retry IS needed now
        desc = "x" * 140
        needs_retry, reason = description_needs_retry("product", desc)
        assert needs_retry
        assert "too short" in reason

    def test_retry_needed_at_130_chars(self):
        desc = "x" * 130
        needs_retry, reason = description_needs_retry("product", desc)
        assert needs_retry
        assert "too short" in reason

    def test_retry_needed_at_100_chars(self):
        desc = "x" * 100
        needs_retry, reason = description_needs_retry("product", desc)
        assert needs_retry
        assert "too short" in reason


class TestValidateSingleFieldDescription:
    """Test validate_single_field for seo_description."""

    def test_passes_at_160_chars(self):
        desc = "x" * 160
        # Should not raise
        validate_single_field("product", "seo_description", desc)

    def test_passes_at_150_chars(self):
        desc = "x" * 150
        validate_single_field("product", "seo_description", desc)

    def test_passes_at_120_chars(self):
        desc = "x" * 120
        # Above hard min (115), should pass
        validate_single_field("product", "seo_description", desc)

    def test_fails_at_100_chars(self):
        desc = "x" * 100
        with pytest.raises(RecommendationValidationError) as exc_info:
            validate_single_field("product", "seo_description", desc)
        assert "too short" in str(exc_info.value)

    def test_fails_at_165_chars(self):
        desc = "x" * 165
        with pytest.raises(RecommendationValidationError) as exc_info:
            validate_single_field("product", "seo_description", desc)
        assert "too long" in str(exc_info.value)

    def test_fails_when_empty(self):
        with pytest.raises(RecommendationValidationError) as exc_info:
            validate_single_field("product", "seo_description", "")
        assert "empty" in str(exc_info.value)


class TestCommonwealthSpelling:
    """Test Canadian/Commonwealth spelling validation."""

    def test_passes_with_commonwealth_spellings(self):
        text = "Shop premium flavours and vapour products. Your favourite colours available."
        passed, issues = validate_commonwealth_spelling(text)
        assert passed
        assert len(issues) == 0

    def test_fails_with_flavor(self):
        text = "Shop premium flavor products."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'flavor' should be 'flavour'" in i for i in issues)

    def test_fails_with_flavors(self):
        text = "Wide variety of flavors available."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'flavors' should be 'flavours'" in i for i in issues)

    def test_fails_with_vapor(self):
        text = "Premium vapor technology."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'vapor' should be 'vapour'" in i for i in issues)

    def test_fails_with_color(self):
        text = "Available in multiple color options."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'color' should be 'colour'" in i for i in issues)

    def test_fails_with_favorite(self):
        text = "Find your favorite vape today."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'favorite' should be 'favourite'" in i for i in issues)

    def test_fails_with_center(self):
        text = "Visit our center for more options."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'center' should be 'centre'" in i for i in issues)

    def test_fails_with_organize(self):
        text = "We organize products by category."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert any("'organize' should be 'organise'" in i for i in issues)

    def test_multiple_issues(self):
        text = "Shop premium flavor and color options. Your favorite vapor products."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert len(issues) == 4

    def test_case_insensitive(self):
        text = "FLAVOR products with VAPOR technology."
        passed, issues = validate_commonwealth_spelling(text)
        assert not passed
        assert len(issues) == 2

    def test_passes_with_empty_string(self):
        passed, issues = validate_commonwealth_spelling("")
        assert passed
        assert len(issues) == 0


class TestTitlePuffRedundancy:
    """Test SEO title puff count redundancy detection."""

    def test_passes_without_puff_counts(self):
        product_title = "Draggg Frost Disposable Vape"
        seo_title = "Draggg Frost Disposable Vape Canada | Vapely"
        passed, issues = check_title_puff_redundancy(product_title, seo_title)
        assert passed
        assert len(issues) == 0

    def test_passes_with_single_puff_count(self):
        product_title = "Draggg 10K Frost"
        seo_title = "Draggg 10K Frost Disposable Vape Canada | Vapely"
        passed, issues = check_title_puff_redundancy(product_title, seo_title)
        assert passed
        assert len(issues) == 0

    def test_fails_with_redundant_puff_count_10k_and_10000(self):
        product_title = "Draggg 10K Frost"
        seo_title = "Draggg 10K Frost 10000 Puffs | Vapely"
        passed, issues = check_title_puff_redundancy(product_title, seo_title)
        assert not passed
        assert any("redundantly repeats puff count 10,000" in i for i in issues)

    def test_fails_with_redundant_puff_count_4k_and_4000(self):
        product_title = "Novo 4K Device"
        seo_title = "Novo 4K Device 4000 Puffs | Vapely"
        passed, issues = check_title_puff_redundancy(product_title, seo_title)
        assert not passed
        assert any("redundantly repeats puff count 4,000" in i for i in issues)

    def test_passes_with_empty_product_title(self):
        passed, issues = check_title_puff_redundancy("", "Some SEO Title 10000 Puffs")
        assert passed
        assert len(issues) == 0

    def test_passes_with_empty_seo_title(self):
        passed, issues = check_title_puff_redundancy("Product 10K", "")
        assert passed
        assert len(issues) == 0

    def test_passes_with_different_puff_counts(self):
        product_title = "Device 5K"
        seo_title = "Device 5K with 10000 Puffs Capacity | Vapely"
        # 5K = 5000, but SEO says 10000 - these are different
        passed, issues = check_title_puff_redundancy(product_title, seo_title)
        assert passed  # Different puff counts, not redundant


class TestRealWorldScenarios:
    """Test real-world SEO description scenarios."""

    def test_typical_good_description_at_155_chars(self):
        desc = "Shop Draggg 10K Frost disposable vape in Canada. Premium icy menthol flavour with 10,000 puffs. Fast Canada-wide shipping. Buy online now at Vapely store."
        assert 150 <= len(desc) <= 160, f"Description is {len(desc)} chars, expected 150-160"
        score, issues = _score_description("product", desc)
        assert score == 1.0

        needs_retry, _ = description_needs_retry("product", desc)
        assert not needs_retry

        passed, spelling_issues = validate_commonwealth_spelling(desc)
        assert passed

    def test_typical_bad_description_too_short(self):
        desc = "Shop Draggg 10K Frost disposable vape. Premium icy menthol flavour. Fast shipping."
        assert len(desc) < 140
        score, issues = _score_description("product", desc)
        assert score < 1.0

        needs_retry, reason = description_needs_retry("product", desc)
        assert needs_retry

    def test_typical_bad_description_us_spelling(self):
        desc = "Shop Draggg 10K Frost disposable vape in Canada. Premium icy menthol flavor with 10,000 puffs. Fast Canada-wide shipping. Shop now at Vapely store."
        passed, issues = validate_commonwealth_spelling(desc)
        assert not passed
        assert any("flavor" in i for i in issues)
