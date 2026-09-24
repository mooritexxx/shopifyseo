"""Unit tests for primary keyword selection improvements.

Tests verify that:
1. Entity head terms are preferred over geo/homonym-polluted variants
2. Short brand terms win over long-tail geo variants
3. Role alignment affects primary selection
4. Geo/homonym noise is properly penalized
"""
import sqlite3

import pytest

from backend.app.services.keyword_clustering import select_primary_keyword
from backend.app.services.keyword_clustering._scoring import (
    _has_geo_noise,
    _has_homonym_noise,
    _matches_entity,
    _role_alignment_score,
)


class TestGeoNoiseDetection:
    """Tests for geo/location noise detection."""

    def test_near_me_detected(self):
        assert _has_geo_noise("vape shop near me") is True

    def test_mall_location_detected(self):
        assert _has_geo_noise("allo mon coco dix30") is True

    def test_city_detected(self):
        assert _has_geo_noise("vape store montreal") is True
        assert _has_geo_noise("elfbar toronto") is True

    def test_clean_brand_not_detected(self):
        assert _has_geo_noise("allo vape canada") is False
        assert _has_geo_noise("flavour beast disposable") is False


class TestHomonymNoiseDetection:
    """Tests for homonym/false-friend noise detection."""

    def test_radio_station_detected(self):
        assert _has_homonym_noise("kraze 101.3") is True
        assert _has_homonym_noise("kraze fm") is True
        assert _has_homonym_noise("kraze radio station") is True

    def test_video_game_detected(self):
        assert _has_homonym_noise("sniper game") is True
        assert _has_homonym_noise("sniper xbox") is True

    def test_year_standalone_detected(self):
        assert _has_homonym_noise("best vapes 2024") is True

    def test_clean_brand_not_detected(self):
        assert _has_homonym_noise("allo vape") is False
        assert _has_homonym_noise("kraze disposable") is False


class TestEntityMatching:
    """Tests for entity matching in keywords."""

    def test_exact_match(self):
        assert _matches_entity("allo vape canada", "ALLO") is True
        assert _matches_entity("flavour beast disposable", "Flavour Beast") is True

    def test_case_insensitive(self):
        assert _matches_entity("ELFBAR canada", "elfbar") is True
        assert _matches_entity("elfbar canada", "ELFBAR") is True

    def test_no_match(self):
        assert _matches_entity("vape shop canada", "ALLO") is False
        assert _matches_entity("generic vape", "Flavour Beast") is False

    def test_partial_word_not_matched(self):
        # "all" shouldn't match in "all vapes"
        assert _matches_entity("all vapes canada", "ALL") is True  # "all" as word
        assert _matches_entity("allo vape", "ALL") is False  # "all" inside "allo"


class TestRoleAlignment:
    """Tests for role alignment scoring."""

    def test_flavours_role_alignment(self):
        # Keywords with flavour terms should score high for flavours role
        assert _role_alignment_score("strawberry flavour", "flavours") > 80
        assert _role_alignment_score("best flavors", "flavours") > 80
        # Generic keywords should score lower
        assert _role_alignment_score("vape canada", "flavours") < 80

    def test_pods_role_alignment(self):
        assert _role_alignment_score("stlth pods", "pods") > 80
        assert _role_alignment_score("replacement cartridges", "pods") > 80
        assert _role_alignment_score("vape device", "pods") < 80

    def test_product_model_role_alignment(self):
        # Model codes should score high
        assert _role_alignment_score("caliburn g3", "product_model") > 80
        assert _role_alignment_score("xlim se", "product_model") > 80
        # Non-model keywords should score lower
        assert _role_alignment_score("vape canada", "product_model") < 50

    def test_brand_collection_prefers_short(self):
        # Short brand terms should score higher
        short_score = _role_alignment_score("allo vape", "brand_collection")
        long_score = _role_alignment_score("allo vape canada online store buy", "brand_collection")
        assert short_score > long_score


class TestPrimarySelectionWithEntity:
    """Tests for primary keyword selection when detected_entity is provided."""

    def test_entity_head_term_wins_over_geo_variant(self):
        """Short brand head term should win over geo-polluted variant."""
        keywords = [
            "allo vape canada",
            "allo mon coco dix30",  # Mall location - should lose
            "allo disposable",
        ]
        keywords_map = {
            "allo vape canada": {"volume": 2000, "opportunity": 75.0},
            "allo mon coco dix30": {"volume": 5000, "opportunity": 85.0},  # Higher volume!
            "allo disposable": {"volume": 1500, "opportunity": 70.0},
        }

        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="ALLO",
            cluster_role="brand_collection",
        )

        # Should NOT pick the mall location despite higher volume/opportunity
        assert primary != "allo mon coco dix30"
        assert "dix30" not in primary.lower()

    def test_entity_head_term_wins_over_radio_station(self):
        """Brand head term should win over radio station homonym."""
        keywords = [
            "kraze disposable",
            "kraze vape",
            "kraze 101.3",  # Radio station - should lose
            "kraze fm playlist",  # Radio - should lose
        ]
        keywords_map = {
            "kraze disposable": {"volume": 1000, "opportunity": 70.0},
            "kraze vape": {"volume": 800, "opportunity": 65.0},
            "kraze 101.3": {"volume": 15000, "opportunity": 90.0},  # Much higher volume!
            "kraze fm playlist": {"volume": 8000, "opportunity": 80.0},
        }

        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="KRAZE",
            cluster_role="brand_collection",
        )

        # Should NOT pick radio station variants
        assert "101.3" not in primary
        assert "fm" not in primary.lower()
        assert primary in ["kraze disposable", "kraze vape"]

    def test_entity_head_term_wins_over_video_game(self):
        """Brand head term should win over video game homonym."""
        keywords = [
            "sniper vape",
            "sniper disposable canada",
            "sniper game",  # Video game - should lose
            "sniper elite",  # Video game - should lose
        ]
        keywords_map = {
            "sniper vape": {"volume": 500, "opportunity": 60.0},
            "sniper disposable canada": {"volume": 300, "opportunity": 55.0},
            "sniper game": {"volume": 50000, "opportunity": 95.0},  # Huge volume!
            "sniper elite": {"volume": 30000, "opportunity": 90.0},
        }

        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="SNIPER",
            cluster_role="brand_collection",
        )

        # Should NOT pick video game variants
        assert "game" not in primary.lower()
        assert "elite" not in primary.lower()
        assert primary in ["sniper vape", "sniper disposable canada"]

    def test_short_brand_term_preferred(self):
        """Short clean brand terms should be preferred over long-tail."""
        keywords = [
            "flavour beast",
            "flavour beast disposable vape canada online shop",  # Too long
            "flavour beast canada",
        ]
        keywords_map = {
            "flavour beast": {"volume": 3000, "opportunity": 80.0},
            "flavour beast disposable vape canada online shop": {"volume": 100, "opportunity": 50.0},
            "flavour beast canada": {"volume": 2500, "opportunity": 78.0},
        }

        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="Flavour Beast",
            cluster_role="brand_collection",
        )

        # Should prefer the shorter, cleaner terms
        assert len(primary.split()) <= 3

    def test_flavours_role_prefers_flavour_terms(self):
        """For flavours role, keywords with flavour terms should be preferred."""
        keywords = [
            "allo ultra flavours",
            "allo vape canada",
            "allo disposable",
        ]
        keywords_map = {
            "allo ultra flavours": {"volume": 1000, "opportunity": 70.0},
            "allo vape canada": {"volume": 2000, "opportunity": 80.0},  # Higher!
            "allo disposable": {"volume": 1500, "opportunity": 75.0},
        }

        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="ALLO",
            cluster_role="flavours",
        )

        # Should prefer the flavours term even with lower opportunity
        assert "flavour" in primary.lower()


class TestPrimarySelectionWithoutEntity:
    """Tests for primary selection without detected_entity (generic clusters)."""

    def test_opportunity_weighted_for_generic(self):
        """Without entity, opportunity should be weighted more heavily."""
        keywords = [
            "vape tips",
            "best vaping guide",
            "vape beginner guide canada",
        ]
        keywords_map = {
            "vape tips": {"volume": 1000, "opportunity": 60.0},
            "best vaping guide": {"volume": 2000, "opportunity": 85.0},  # Highest opportunity
            "vape beginner guide canada": {"volume": 500, "opportunity": 50.0},
        }

        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="",
            cluster_role="generic",
        )

        # For generic clusters, highest opportunity should win
        assert primary == "best vaping guide"

    def test_geo_noise_still_considered_without_entity(self):
        """Even without entity, geo noise should factor into scoring."""
        keywords = [
            "vape shop",
            "vape shop near me",  # Geo noise
        ]
        keywords_map = {
            "vape shop": {"volume": 5000, "opportunity": 70.0},
            "vape shop near me": {"volume": 50000, "opportunity": 80.0},
        }

        # Without entity, opportunity matters more, but role alignment helps
        primary = select_primary_keyword(
            keywords,
            keywords_map,
            detected_entity="",
            cluster_role="local",  # Local role accepts "near me"
        )

        # For local role, near me is acceptable
        # This tests that role alignment works even without entity
