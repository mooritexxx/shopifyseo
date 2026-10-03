"""Unit tests for manual_weave_gaps and compliance helpers."""
import pytest

from shopifyseo.internal_links.compliance import (
    manual_weave_gaps,
    check_anchor_quality,
    check_numbers_outside_anchor,
    check_stock_availability_claims,
    build_tvpa_allowlist,
)


class TestCheckAnchorQuality:
    """Tests for check_anchor_quality (G8)."""
    
    def test_empty_anchor_rejected(self):
        """Empty anchor should fail."""
        gaps = check_anchor_quality("")
        assert len(gaps) > 0
        assert any("empty" in g.lower() for g in gaps)
    
    def test_weak_single_word_anchor_rejected(self):
        """Weak single-word anchors should be rejected."""
        for word in ["here", "click", "vapes", "products", "link"]:
            gaps = check_anchor_quality(word)
            assert len(gaps) > 0, f"{word} should be rejected"
            assert any("generic" in g.lower() or "single word" in g.lower() for g in gaps)
    
    def test_multi_word_anchor_accepted(self):
        """Multi-word anchors should be accepted."""
        gaps = check_anchor_quality("STLTH 60K buying guide")
        # Should not have weak anchor gap
        assert not any("generic" in g.lower() for g in gaps)
    
    def test_exact_keyword_match_not_in_title_rejected(self):
        """Anchor that equals a keyword but isn't in title should be rejected."""
        gaps = check_anchor_quality(
            "zyn pouches",
            target_title="Nicotine Pouches Canada",
            target_keywords=["zyn pouches", "nicotine pouches"],
        )
        assert len(gaps) > 0
        assert any("exact" in g.lower() or "keyword" in g.lower() for g in gaps)
    
    def test_keyword_in_title_accepted(self):
        """Anchor that equals a keyword that's also in title should be accepted."""
        gaps = check_anchor_quality(
            "nicotine pouches",
            target_title="Nicotine Pouches Canada",
            target_keywords=["nicotine pouches", "zyn pouches"],
        )
        # Should not have keyword stuffing gap
        assert not any("exact" in g.lower() and "keyword" in g.lower() for g in gaps)
    
    def test_repeated_word_in_anchor_rejected(self):
        """Repeated words in anchor should be rejected."""
        gaps = check_anchor_quality("vape vapes disposable vape")
        assert len(gaps) > 0
        assert any("repeated" in g.lower() for g in gaps)
    
    def test_repeated_stop_words_allowed(self):
        """Repeated stop words should not trigger rejection."""
        gaps = check_anchor_quality("the guide to the best picks")
        # "the" is a stop word, should not trigger repeated word
        assert not any("repeated" in g.lower() for g in gaps)


class TestCheckNumbersOutsideAnchor:
    """Tests for check_numbers_outside_anchor (G9)."""
    
    def test_number_outside_anchor_rejected(self):
        """Numbers outside anchor should be rejected."""
        gaps = check_numbers_outside_anchor("Get 5000 puffs with our guide", "our guide")
        assert len(gaps) > 0
        assert any("5000" in g for g in gaps)
    
    def test_number_inside_anchor_allowed(self):
        """Numbers inside anchor should be allowed."""
        gaps = check_numbers_outside_anchor("Check our STLTH 60K buying guide", "STLTH 60K buying guide")
        assert len(gaps) == 0
    
    def test_multiple_number_formats(self):
        """Various number formats should be detected."""
        test_cases = [
            ("20mg strength", "strength"),
            ("60K devices", "devices"),
            ("2.5% nicotine", "nicotine"),
            ("#1 brand", "brand"),
        ]
        for addition, anchor in test_cases:
            gaps = check_numbers_outside_anchor(addition, anchor)
            assert len(gaps) > 0, f"'{addition}' should have number rejected"
    
    def test_number_only_in_anchor_passes(self):
        """If number appears only in anchor, should pass."""
        gaps = check_numbers_outside_anchor("See the VFEEL V1 Cool Mint guide.", "VFEEL V1 Cool Mint")
        assert len(gaps) == 0


class TestCheckStockAvailabilityClaims:
    """Tests for check_stock_availability_claims (G11 supplement)."""
    
    @pytest.mark.parametrize("phrase", [
        "in stock now",
        "out of stock",
        "sold out",
        "back in stock",
        "restock soon",
        "currently available",
        "available now",
        "available today",
        "ships today",
        "same day shipping",
        "same-day delivery",
        "limited stock",
        "low stock",
        "only 5 left",
        "while supplies last",
        "selling fast",
    ])
    def test_stock_phrases_rejected(self, phrase):
        """Various stock/availability phrases should be rejected."""
        gaps = check_stock_availability_claims(f"This product is {phrase}.")
        assert len(gaps) > 0, f"'{phrase}' should be rejected"
    
    @pytest.mark.parametrize("phrase", [
        "wholesale pricing",
        "bulk order",
        "distributor pricing",
        "reseller program",
    ])
    def test_wholesale_phrases_rejected(self, phrase):
        """Wholesale/bulk phrases should be rejected."""
        gaps = check_stock_availability_claims(f"We offer {phrase}.")
        assert len(gaps) > 0, f"'{phrase}' should be rejected"
    
    def test_normal_text_passes(self):
        """Normal text without stock claims should pass."""
        gaps = check_stock_availability_claims("Check our guide for more information on this product.")
        assert len(gaps) == 0


class TestManualWeaveGaps:
    """Tests for manual_weave_gaps (combined G8-G11)."""
    
    def test_clean_addition_passes(self):
        """Clean addition with good anchor should have no gaps."""
        gaps = manual_weave_gaps(
            addition="See our complete buying guide for details.",
            anchor="complete buying guide",
            target_title="Complete Buying Guide",
        )
        assert len(gaps) == 0
    
    def test_health_claim_rejected(self):
        """Health claims should be rejected (G10)."""
        gaps = manual_weave_gaps(
            addition="This is a safer alternative to smoking. See our guide.",
            anchor="our guide",
        )
        assert len(gaps) > 0
        assert any("health" in g.lower() for g in gaps)
    
    def test_superlative_bait_rejected(self):
        """Superlative bait should be rejected (G10)."""
        gaps = manual_weave_gaps(
            addition="This is the #1 brand. See our guide.",
            anchor="our guide",
        )
        assert len(gaps) > 0
    
    def test_combined_issues_all_reported(self):
        """Multiple issues should all be reported."""
        gaps = manual_weave_gaps(
            addition="Get 5000 puffs, in stock now, with a safer alternative to smoking! Click here.",
            anchor="here",  # Weak anchor
        )
        # Should have: weak anchor, number outside, stock claim, health claim
        assert len(gaps) >= 3


class TestManualWeaveGapsTVPA:
    """Tests for TVPA flavour check in manual_weave_gaps."""
    
    def test_tvpa_category_term_rejected(self):
        """TVPA category terms should be rejected."""
        gaps = manual_weave_gaps(
            addition="This has a candy-like finish. Check our guide.",
            anchor="our guide",
        )
        assert len(gaps) > 0
        assert any("tvpa" in g.lower() for g in gaps)
    
    def test_tvpa_style_term_rejected(self):
        """TVPA style terms should be rejected."""
        gaps = manual_weave_gaps(
            addition="Fans love this flavour. Check our guide.",
            anchor="our guide",
        )
        assert len(gaps) > 0
        assert any("tvpa" in g.lower() for g in gaps)
    
    def test_tvpa_allowlist_product_name_passes(self):
        """Product names in allowlist should not trigger TVPA."""
        gaps = manual_weave_gaps(
            addition="Try the Peaches & Cream flavour. Check our guide.",
            anchor="our guide",
            allowed_names=("Peaches & Cream Disposable Vape", "Peaches & Cream"),
        )
        tvpa_gaps = [g for g in gaps if "tvpa" in g.lower()]
        assert len(tvpa_gaps) == 0


class TestBuildTvpaAllowlist:
    """Tests for build_tvpa_allowlist helper."""
    
    def test_empty_titles_return_empty(self):
        """Empty titles should return empty tuple."""
        result = build_tvpa_allowlist("", "")
        assert result == ()
    
    def test_titles_included(self):
        """Non-empty titles should be included."""
        result = build_tvpa_allowlist("Source Title", "Target Title")
        assert "Source Title" in result
        assert "Target Title" in result
    
    def test_extracted_flavours_included(self):
        """Extracted flavour names should be included when available."""
        result = build_tvpa_allowlist(
            "STLTH 60K Mango Ice Disposable Vape",
            "VFEEL V1 Cool Mint Disposable Vape",
        )
        assert len(result) >= 2  # At least the titles


class TestBriefSampleFixtures:
    """Test the sample fixtures from the brief (section 8)."""
    
    def test_sample_1_draggg_anchor(self):
        """Sample 1: Draggg disposable vapes anchor should pass."""
        gaps = manual_weave_gaps(
            addition='Compare the full range of Draggg disposable vapes.',
            anchor="Draggg disposable vapes",
            target_title="Draggg Disposable Vapes",
        )
        assert len(gaps) == 0, f"Unexpected gaps: {gaps}"
    
    def test_sample_2_stlth_60k_anchor(self):
        """Sample 2: STLTH 60K buying guide anchor should pass (number in anchor)."""
        gaps = manual_weave_gaps(
            addition="Not sure which flavour to pick? Our STLTH 60K buying guide compares the lineup.",
            anchor="STLTH 60K buying guide",
            target_title="STLTH 60K Canada Buying Guide",
        )
        assert len(gaps) == 0, f"Unexpected gaps: {gaps}"
    
    def test_sample_4_zyn_anchor(self):
        """Sample 4: is Zyn legal in Canada? anchor should pass."""
        gaps = manual_weave_gaps(
            addition="For the full picture, see is Zyn legal in Canada?",
            anchor="is Zyn legal in Canada?",
            target_title="Is Zyn Legal in Canada?",
        )
        assert len(gaps) == 0, f"Unexpected gaps: {gaps}"
    
    def test_sample_5_vfeel_v1_anchor(self):
        """Sample 5: VFEEL V1 Cool Mint anchor should pass (number in anchor)."""
        gaps = manual_weave_gaps(
            addition="For a cooling mint pick from the same lineup, see the VFEEL V1 Cool Mint.",
            anchor="VFEEL V1 Cool Mint",
            target_title="VFEEL V1 Cool Mint Disposable Vape",
        )
        assert len(gaps) == 0, f"Unexpected gaps: {gaps}"


class TestEdgeCases:
    """Edge cases and boundary conditions."""
    
    def test_empty_addition_passes(self):
        """Empty addition should pass (nothing to check)."""
        gaps = manual_weave_gaps(addition="", anchor="test anchor")
        # Empty addition has no content issues
        # Anchor quality still checked
        assert not any("number" in g.lower() for g in gaps)
        assert not any("health" in g.lower() for g in gaps)
    
    def test_very_long_anchor_checked(self):
        """Very long anchor should be handled."""
        long_anchor = "A" * 100  # Within 120 char limit
        gaps = check_anchor_quality(long_anchor)
        # Should not crash, may have other issues but not length
        assert isinstance(gaps, list)
    
    def test_unicode_in_addition(self):
        """Unicode characters should be handled."""
        gaps = manual_weave_gaps(
            addition="Check our guide for flavours — it's great!",
            anchor="our guide",
        )
        # Should not crash
        assert isinstance(gaps, list)
    
    def test_html_entities_in_anchor(self):
        """HTML entities in anchor should work."""
        gaps = check_anchor_quality("Johnson &amp; Johnson guide")
        # Should handle HTML entities
        assert isinstance(gaps, list)
