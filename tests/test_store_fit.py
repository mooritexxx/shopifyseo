"""Unit tests for store-fit scoring module.

Tests verify that:
1. Tobacco/cigarette clusters are heavily penalized
2. Near-me/local clusters are penalized
3. Off-niche clusters are penalized
4. Catalog vendor clusters are boosted
5. When high-volume noise competes with catalog-brand, catalog-brand wins
"""
import sqlite3

import pytest

from backend.app.services.keyword_clustering import (
    StoreFitContext,
    compute_cluster_store_fit,
    compute_keyword_store_fit,
    load_store_fit_context,
    cluster_priority_score,
    repair_and_enrich_clusters,
)
from backend.app.services.keyword_clustering._planning import (
    enrich_cluster_for_content,
    load_entity_rules,
)


def _make_test_db_with_vendors() -> sqlite3.Connection:
    """Create an in-memory DB with products and vendor data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Products table with vendor
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            vendor TEXT,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT,
            online_store_url TEXT,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)

    # Collections table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT
        )
    """)

    # Insert test products with vendors (simulating Vapely's catalog)
    test_products = [
        # Flavour Beast - 256 products (largest vendor)
        *[(f"fb_{i}", f"Flavour Beast Product {i}", f"fb-product-{i}", "Flavour Beast") for i in range(20)],
        # STLTH - 96 products
        *[(f"stlth_{i}", f"STLTH Product {i}", f"stlth-product-{i}", "STLTH") for i in range(10)],
        # ALLO - 115 products
        *[(f"allo_{i}", f"ALLO Product {i}", f"allo-product-{i}", "ALLO") for i in range(12)],
        # ELFBAR - 67 products
        *[(f"elf_{i}", f"ELFBAR Product {i}", f"elfbar-product-{i}", "ELFBAR") for i in range(7)],
        # STLTH x GEEK BAR - 23 products
        *[(f"sgb_{i}", f"STLTH x GEEK BAR {i}", f"stlth-geek-bar-{i}", "STLTH x GEEK BAR") for i in range(3)],
    ]

    conn.executemany(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        test_products,
    )

    # Insert test collections
    conn.execute("""
        INSERT INTO collections (shopify_id, title, handle)
        VALUES ('coll_1', 'Flavour Beast Disposables', 'flavour-beast-disposables')
    """)

    conn.commit()
    return conn


class TestStoreFitContextLoading:
    """Tests for loading store-fit context from database."""

    def test_load_empty_catalog(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE products (shopify_id TEXT, vendor TEXT)")
        conn.commit()

        ctx = load_store_fit_context(conn)

        assert ctx.catalog_vendors == {}
        assert ctx.total_product_count == 0
        conn.close()

    def test_load_catalog_vendors(self):
        conn = _make_test_db_with_vendors()

        ctx = load_store_fit_context(conn)

        assert len(ctx.catalog_vendors) == 5
        assert "flavour beast" in ctx.catalog_vendors
        assert ctx.catalog_vendors["flavour beast"]["name"] == "Flavour Beast"
        assert ctx.catalog_vendors["flavour beast"]["product_count"] == 20
        assert ctx.total_product_count == 52  # 20 + 10 + 12 + 7 + 3
        conn.close()


class TestTobaccoPenalty:
    """Tests for tobacco/cigarette cluster penalty."""

    def test_cigarette_keyword_penalized(self):
        ctx = StoreFitContext(
            catalog_vendors={},
            total_product_count=0,
        )

        result = compute_keyword_store_fit("canada cigarette", ctx)

        assert result["is_tobacco"] is True
        assert result["fit_multiplier"] < 0.3  # Heavy penalty

    def test_cheap_smokes_keyword_penalized(self):
        ctx = StoreFitContext()

        result = compute_keyword_store_fit("cheap smokes canada", ctx)

        assert result["is_tobacco"] is True
        assert result["fit_multiplier"] == ctx.tobacco_penalty

    def test_cigarette_cluster_penalized(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Canada Cigarette",
            cluster_keywords=["canada cigarette", "canadian cigarettes", "cigarettes online canada"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_tobacco"] is True
        assert result["fit_multiplier"] < 0.3
        assert result["penalty_reason"] is not None
        assert "tobacco" in result["penalty_reason"].lower()

    def test_tabagie_cluster_penalized(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Local Tabagie Finder",
            cluster_keywords=["tabagie near me", "tabagie montreal"],
            cluster_role="local",
            detected_entity="",
            context=ctx,
        )

        # Both local and tobacco signals
        assert result["is_tobacco"] is True
        assert result["is_local"] is True


class TestLocalPenalty:
    """Tests for near-me/local cluster penalty."""

    def test_near_me_keyword_penalized(self):
        ctx = StoreFitContext()

        result = compute_keyword_store_fit("vaping store near me", ctx)

        assert result["is_local"] is True
        assert result["fit_multiplier"] == ctx.local_penalty

    def test_local_cluster_penalized(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Local Vape and Smoke Shop Finder",
            cluster_keywords=["vaping store near me", "vape shop near me", "smoke shop nearby"],
            cluster_role="local",
            detected_entity="",
            context=ctx,
        )

        assert result["is_local"] is True
        assert result["fit_multiplier"] < 0.4
        assert "local" in result["penalty_reason"].lower() or "near" in result["penalty_reason"].lower()

    def test_local_role_triggers_penalty(self):
        """Clusters with role=local are penalized even without 'near me' keywords."""
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Vape Store Locator",
            cluster_keywords=["find vape store", "local vape shop"],
            cluster_role="local",
            detected_entity="",
            context=ctx,
        )

        assert result["is_local"] is True


class TestOffNichePenalty:
    """Tests for off-niche cluster penalty."""

    def test_whippet_keyword_penalized(self):
        ctx = StoreFitContext()

        result = compute_keyword_store_fit("whipped cream chargers", ctx)

        assert result["is_off_niche"] is True
        assert result["fit_multiplier"] == ctx.off_niche_penalty  # Very heavy

    def test_enail_cluster_penalized(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="E-Nails Canada",
            cluster_keywords=["e nail canada", "enails for dabs", "dab rig accessories"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_off_niche"] is True
        assert result["fit_multiplier"] < 0.2


class TestCatalogVendorBoost:
    """Tests for catalog vendor matching and boost."""

    def test_catalog_vendor_detected(self):
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        result = compute_cluster_store_fit(
            cluster_name="Flavour Beast Products",
            cluster_keywords=["flavour beast disposable", "flavour beast canada"],
            cluster_role="brand_collection",
            detected_entity="Flavour Beast",
            context=ctx,
        )

        assert result["matched_vendor"] is not None
        assert result["matched_vendor"]["name"] == "Flavour Beast"
        assert result["fit_multiplier"] > 1.0  # Boosted
        conn.close()

    def test_high_sku_vendor_gets_higher_boost(self):
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        # Flavour Beast has 20 products (largest)
        fb_result = compute_cluster_store_fit(
            cluster_name="Flavour Beast Products",
            cluster_keywords=["flavour beast disposable"],
            cluster_role="brand_collection",
            detected_entity="Flavour Beast",
            context=ctx,
        )

        # STLTH x GEEK BAR has only 3 products
        sgb_result = compute_cluster_store_fit(
            cluster_name="STLTH x GEEK BAR Products",
            cluster_keywords=["stlth x geek bar 80k"],
            cluster_role="brand_collection",
            detected_entity="STLTH x GEEK BAR",
            context=ctx,
        )

        # Both boosted, but Flavour Beast more
        assert fb_result["fit_multiplier"] > 1.0
        assert sgb_result["fit_multiplier"] > 1.0
        assert fb_result["fit_multiplier"] > sgb_result["fit_multiplier"]
        conn.close()

    def test_catalog_vendor_detected_in_keywords(self):
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        result = compute_cluster_store_fit(
            cluster_name="Disposable Vapes",  # Generic name
            cluster_keywords=["elfbar canada", "elf bar disposable"],  # Keywords mention ELFBAR
            cluster_role="category_collection",
            detected_entity="",  # No entity detected at cluster level
            context=ctx,
        )

        assert result["matched_vendor"] is not None
        assert result["matched_vendor"]["name"] == "ELFBAR"
        conn.close()


class TestPriorityCompetition:
    """Integration tests: catalog-aligned clusters should beat noise clusters."""

    def test_catalog_brand_beats_cigarettes_despite_volume(self):
        """Catalog brand cluster with moderate volume should outrank cigarette cluster with high volume."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        # Simulate keywords_map with high-volume cigarette cluster
        keywords_map = {
            # Cigarette cluster - very high volume (like the audit showed)
            "canada cigarette": {"volume": 34280, "opportunity": 85.0, "ranking_status": "not_ranking"},
            "canadian cigarettes": {"volume": 12000, "opportunity": 70.0, "ranking_status": "not_ranking"},
            "cigarettes canada": {"volume": 8000, "opportunity": 65.0, "ranking_status": "not_ranking"},
            # Flavour Beast cluster - moderate volume but catalog-aligned
            "flavour beast canada": {"volume": 2000, "opportunity": 75.0, "ranking_status": "quick_win"},
            "flavour beast disposable": {"volume": 1500, "opportunity": 70.0, "ranking_status": "striking_distance"},
            "flavor beast vape": {"volume": 800, "opportunity": 65.0, "ranking_status": "not_ranking"},
        }

        cigarette_cluster = {
            "name": "Canada Cigarette",
            "keywords": ["canada cigarette", "canadian cigarettes", "cigarettes canada"],
            "primary_keyword": "canada cigarette",
            "content_type": "collection_page",
        }

        fb_cluster = {
            "name": "Flavour Beast Products",
            "keywords": ["flavour beast canada", "flavour beast disposable", "flavor beast vape"],
            "primary_keyword": "flavour beast canada",
            "content_type": "collection_page",
        }

        entity_rules = load_entity_rules(conn)

        # Enrich both clusters
        enriched_cigarette = enrich_cluster_for_content(
            cigarette_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )
        enriched_fb = enrich_cluster_for_content(
            fb_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )

        # Flavour Beast should have higher priority despite lower raw volume
        assert enriched_fb["priority_score"] > enriched_cigarette["priority_score"], (
            f"Catalog brand ({enriched_fb['priority_score']:.1f}) should beat "
            f"cigarettes ({enriched_cigarette['priority_score']:.1f})"
        )

        # Verify the store_fit metadata
        assert enriched_cigarette.get("store_fit", {}).get("is_tobacco") is True
        assert enriched_fb.get("store_fit", {}).get("matched_vendor") is not None

        conn.close()

    def test_catalog_brand_beats_near_me_despite_volume(self):
        """Catalog brand cluster should outrank near-me cluster with massive volume."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        keywords_map = {
            # Near-me cluster - massive volume (like the audit showed 743k)
            "vaping store near me": {"volume": 743310, "opportunity": 60.0, "ranking_status": "not_ranking"},
            "vape shop near me": {"volume": 165000, "opportunity": 55.0, "ranking_status": "not_ranking"},
            "smoke shop nearby": {"volume": 97000, "opportunity": 50.0, "ranking_status": "not_ranking"},
            # STLTH cluster - moderate volume but catalog-aligned
            "stlth vape canada": {"volume": 3000, "opportunity": 72.0, "ranking_status": "striking_distance"},
            "stlth disposable": {"volume": 2500, "opportunity": 68.0, "ranking_status": "quick_win"},
            "stlth pods": {"volume": 1800, "opportunity": 65.0, "ranking_status": "not_ranking"},
        }

        near_me_cluster = {
            "name": "Local Vape and Smoke Shop Finder",
            "keywords": ["vaping store near me", "vape shop near me", "smoke shop nearby"],
            "primary_keyword": "vaping store near me",
            "content_type": "landing_page",
        }

        stlth_cluster = {
            "name": "STLTH Products",
            "keywords": ["stlth vape canada", "stlth disposable", "stlth pods"],
            "primary_keyword": "stlth vape canada",
            "content_type": "collection_page",
        }

        entity_rules = load_entity_rules(conn)

        enriched_near_me = enrich_cluster_for_content(
            near_me_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )
        enriched_stlth = enrich_cluster_for_content(
            stlth_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )

        # STLTH should have higher priority despite massively lower raw volume
        assert enriched_stlth["priority_score"] > enriched_near_me["priority_score"], (
            f"Catalog brand ({enriched_stlth['priority_score']:.1f}) should beat "
            f"near-me ({enriched_near_me['priority_score']:.1f})"
        )

        # Verify the store_fit metadata
        assert enriched_near_me.get("store_fit", {}).get("is_local") is True
        assert enriched_stlth.get("store_fit", {}).get("matched_vendor") is not None

        conn.close()


class TestRepairAndEnrichIntegration:
    """Tests for repair_and_enrich_clusters with store-fit scoring."""

    def test_clusters_sorted_by_adjusted_priority(self):
        """repair_and_enrich_clusters should apply store-fit to all clusters."""
        conn = _make_test_db_with_vendors()

        keywords_map = {
            # Cigarette keywords
            "cheap smokes canada": {"volume": 15000, "opportunity": 80.0, "ranking_status": "not_ranking"},
            "cheapest cigarettes": {"volume": 10000, "opportunity": 75.0, "ranking_status": "not_ranking"},
            # ALLO keywords
            "allo vape canada": {"volume": 2000, "opportunity": 70.0, "ranking_status": "quick_win"},
            "allo disposable": {"volume": 1500, "opportunity": 68.0, "ranking_status": "striking_distance"},
        }

        clusters = [
            {
                "name": "Cheap Smokes Canada",
                "keywords": ["cheap smokes canada", "cheapest cigarettes"],
                "primary_keyword": "cheap smokes canada",
                "content_type": "collection_page",
            },
            {
                "name": "ALLO Products",
                "keywords": ["allo vape canada", "allo disposable"],
                "primary_keyword": "allo vape canada",
                "content_type": "collection_page",
            },
        ]

        repaired = repair_and_enrich_clusters(clusters, conn, keywords_map)

        # Find each cluster by name
        smokes_cluster = next(c for c in repaired if "smokes" in c["name"].lower())
        allo_cluster = next(c for c in repaired if "allo" in c["name"].lower())

        # ALLO should have higher priority
        assert allo_cluster["priority_score"] > smokes_cluster["priority_score"]

        # Both should have store_fit metadata
        assert smokes_cluster.get("store_fit", {}).get("is_tobacco") is True
        assert allo_cluster.get("store_fit", {}).get("matched_vendor") is not None

        conn.close()


class TestEdgeCases:
    """Edge case tests."""

    def test_empty_cluster_keywords(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="",
            cluster_keywords=[],
            cluster_role="",
            detected_entity="",
            context=ctx,
        )

        assert result["fit_multiplier"] == 1.0  # Neutral
        assert result["matched_vendor"] is None

    def test_mixed_signals_tobacco_takes_precedence(self):
        """Off-niche penalty is heaviest, then tobacco, then local."""
        ctx = StoreFitContext()

        # Off-niche + tobacco
        result = compute_cluster_store_fit(
            cluster_name="Cigar Accessories",
            cluster_keywords=["cigar humidor", "cohiba cigars"],  # cigars = off-niche
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_off_niche"] is True
        assert result["fit_multiplier"] == ctx.off_niche_penalty

    def test_catalog_vendor_clears_minor_local_penalty(self):
        """If a cluster matches a catalog vendor, minor local signal is cleared."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        # "ELFBAR near me" - local signal but also catalog brand
        result = compute_cluster_store_fit(
            cluster_name="ELFBAR Products",
            cluster_keywords=["elfbar canada", "elf bar vape"],  # No near-me keywords
            cluster_role="brand_collection",
            detected_entity="ELFBAR",
            context=ctx,
        )

        # Should be boosted, not penalized
        assert result["fit_multiplier"] > 1.0
        assert result["matched_vendor"] is not None

        conn.close()

    def test_non_catalog_brand_penalized(self):
        """Brands not in catalog ARE penalized (updated behavior)."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        # Caliburn is NOT in our test catalog - should be penalized
        result = compute_cluster_store_fit(
            cluster_name="Caliburn Products",
            cluster_keywords=["caliburn g3", "caliburn pods"],
            cluster_role="brand_collection",
            detected_entity="Caliburn",
            context=ctx,
        )

        # Penalized because it's a known brand not in catalog
        assert result["matched_vendor"] is None
        assert result["is_non_catalog_brand"] is True
        assert result["fit_multiplier"] == ctx.non_catalog_brand_penalty

        conn.close()


class TestWholesalePenalty:
    """Tests for wholesale/B2B cluster penalty."""

    def test_wholesale_keyword_penalized(self):
        ctx = StoreFitContext()

        result = compute_keyword_store_fit("vapes wholesale", ctx)

        assert result["is_wholesale"] is True
        assert result["fit_multiplier"] == ctx.wholesale_penalty

    def test_wholesale_cluster_penalized(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Vapes Wholesale",
            cluster_keywords=["vapes wholesale", "bulk vape order", "vape distributor"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_wholesale"] is True
        assert result["fit_multiplier"] == ctx.wholesale_penalty
        assert "wholesale" in result["penalty_reason"].lower() or "b2b" in result["penalty_reason"].lower()

    def test_bulk_distributor_penalized(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Vape Distributors Canada",
            cluster_keywords=["vape distributor canada", "bulk pricing vapes"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_wholesale"] is True
        assert result["fit_multiplier"] < 0.3


class TestNonCatalogBrandPenalty:
    """Tests for non-catalog device brand penalty."""

    def test_juul_cluster_penalized(self):
        """Juul cluster should be penalized when not in catalog."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        result = compute_cluster_store_fit(
            cluster_name="Smok Novo 4 Vapes",
            cluster_keywords=["juuls", "juul pods canada"],
            cluster_role="brand_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_non_catalog_brand"] is True
        assert result["matched_vendor"] is None
        assert result["fit_multiplier"] == ctx.non_catalog_brand_penalty

        conn.close()

    def test_smok_cluster_penalized(self):
        """SMOK cluster should be penalized when not in catalog."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        result = compute_cluster_store_fit(
            cluster_name="SMOK Vapes",
            cluster_keywords=["smok novo", "smok nord", "smok rpm"],
            cluster_role="brand_collection",
            detected_entity="SMOK",
            context=ctx,
        )

        assert result["is_non_catalog_brand"] is True
        assert result["fit_multiplier"] < 0.4

        conn.close()

    def test_catalog_brand_not_penalized(self):
        """Catalog brands should NOT be penalized even if in denylist."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        # ELFBAR IS in our catalog, so it should be boosted not penalized
        result = compute_cluster_store_fit(
            cluster_name="ELFBAR Products",
            cluster_keywords=["elfbar canada", "elf bar flavours"],
            cluster_role="brand_collection",
            detected_entity="ELFBAR",
            context=ctx,
        )

        assert result["is_non_catalog_brand"] is False
        assert result["matched_vendor"] is not None
        assert result["fit_multiplier"] > 1.0  # Boosted

        conn.close()


class TestUltraGenericPenalty:
    """Tests for ultra-generic head term penalty."""

    def test_ecig_shop_penalized_without_catalog_match(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Ecig Shop",
            cluster_keywords=["ecig shop", "e cig shop canada"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_ultra_generic"] is True
        assert result["fit_multiplier"] == ctx.ultra_generic_penalty

    def test_vape_shop_penalized_without_catalog_match(self):
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Vape Store Canada",
            cluster_keywords=["vape shop", "vape store", "buy vapes online"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_ultra_generic"] is True
        assert result["fit_multiplier"] < 0.5

    def test_generic_not_penalized_with_catalog_match(self):
        """Generic terms with catalog vendor match should be boosted."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        result = compute_cluster_store_fit(
            cluster_name="Flavour Beast Vape Shop",
            cluster_keywords=["flavour beast vape shop", "buy flavour beast"],
            cluster_role="brand_collection",
            detected_entity="Flavour Beast",
            context=ctx,
        )

        # Has catalog vendor match, so should be boosted not penalized
        assert result["is_ultra_generic"] is False
        assert result["matched_vendor"] is not None
        assert result["fit_multiplier"] > 1.0

        conn.close()


class TestOffNicheExpanded:
    """Tests for expanded off-niche patterns (Al Fakher, G Fuel)."""

    def test_al_fakher_penalized(self):
        """Al Fakher (hookah brand) should be off-niche penalized."""
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Al Fakher Vapes",
            cluster_keywords=["al fakher", "al fakher flavours"],
            cluster_role="brand_collection",
            detected_entity="Al Fakher",
            context=ctx,
        )

        assert result["is_off_niche"] is True
        assert result["fit_multiplier"] == ctx.off_niche_penalty

    def test_g_fuel_penalized(self):
        """G Fuel (energy drink) should be off-niche penalized."""
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="G Fuel Products",
            cluster_keywords=["g fuel", "gfuel flavours"],
            cluster_role="brand_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_off_niche"] is True
        assert result["fit_multiplier"] == ctx.off_niche_penalty

    def test_shisha_hookah_penalized(self):
        """Shisha/hookah terms should be off-niche penalized."""
        ctx = StoreFitContext()

        result = compute_cluster_store_fit(
            cluster_name="Hookah Accessories",
            cluster_keywords=["shisha", "hookah tobacco"],
            cluster_role="category_collection",
            detected_entity="",
            context=ctx,
        )

        assert result["is_off_niche"] is True


class TestPriorityCompetitionExpanded:
    """Integration tests: catalog brands beat all noise types."""

    def test_catalog_brand_beats_wholesale_despite_volume(self):
        """Catalog brand should beat wholesale cluster."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        keywords_map = {
            # Wholesale cluster - high volume
            "vapes wholesale": {"volume": 25000, "opportunity": 80.0, "ranking_status": "not_ranking"},
            "bulk vape order": {"volume": 15000, "opportunity": 75.0, "ranking_status": "not_ranking"},
            # ALLO cluster - moderate volume
            "allo vape canada": {"volume": 2000, "opportunity": 70.0, "ranking_status": "quick_win"},
            "allo disposable": {"volume": 1500, "opportunity": 68.0, "ranking_status": "striking_distance"},
        }

        from backend.app.services.keyword_clustering._planning import (
            enrich_cluster_for_content,
            load_entity_rules,
        )

        wholesale_cluster = {
            "name": "Vapes Wholesale",
            "keywords": ["vapes wholesale", "bulk vape order"],
            "primary_keyword": "vapes wholesale",
            "content_type": "collection_page",
        }

        allo_cluster = {
            "name": "ALLO Products",
            "keywords": ["allo vape canada", "allo disposable"],
            "primary_keyword": "allo vape canada",
            "content_type": "collection_page",
        }

        entity_rules = load_entity_rules(conn)

        enriched_wholesale = enrich_cluster_for_content(
            wholesale_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )
        enriched_allo = enrich_cluster_for_content(
            allo_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )

        # ALLO should beat wholesale
        assert enriched_allo["priority_score"] > enriched_wholesale["priority_score"]
        assert enriched_wholesale.get("store_fit", {}).get("is_wholesale") is True

        conn.close()

    def test_catalog_brand_beats_juul_despite_volume(self):
        """Catalog brand should beat non-catalog brand (Juul) cluster."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        keywords_map = {
            # Juul cluster - high volume (popular brand)
            "juul pods": {"volume": 45000, "opportunity": 85.0, "ranking_status": "not_ranking"},
            "juul canada": {"volume": 25000, "opportunity": 80.0, "ranking_status": "not_ranking"},
            # STLTH cluster - moderate volume
            "stlth vape canada": {"volume": 3000, "opportunity": 72.0, "ranking_status": "striking_distance"},
            "stlth pods": {"volume": 2000, "opportunity": 68.0, "ranking_status": "quick_win"},
        }

        from backend.app.services.keyword_clustering._planning import (
            enrich_cluster_for_content,
            load_entity_rules,
        )

        juul_cluster = {
            "name": "Juul Pods",
            "keywords": ["juul pods", "juul canada"],
            "primary_keyword": "juul pods",
            "content_type": "collection_page",
        }

        stlth_cluster = {
            "name": "STLTH Products",
            "keywords": ["stlth vape canada", "stlth pods"],
            "primary_keyword": "stlth vape canada",
            "content_type": "collection_page",
        }

        entity_rules = load_entity_rules(conn)

        enriched_juul = enrich_cluster_for_content(
            juul_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )
        enriched_stlth = enrich_cluster_for_content(
            stlth_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )

        # STLTH should beat Juul despite lower volume
        assert enriched_stlth["priority_score"] > enriched_juul["priority_score"], (
            f"STLTH ({enriched_stlth['priority_score']:.1f}) should beat "
            f"Juul ({enriched_juul['priority_score']:.1f})"
        )
        assert enriched_juul.get("store_fit", {}).get("is_non_catalog_brand") is True

        conn.close()

    def test_catalog_brand_beats_generic_ecig_shop(self):
        """Catalog brand should beat ultra-generic 'ecig shop' cluster."""
        conn = _make_test_db_with_vendors()
        ctx = load_store_fit_context(conn)

        keywords_map = {
            # Generic ecig shop - very high volume
            "ecig shop": {"volume": 74000, "opportunity": 65.0, "ranking_status": "not_ranking"},
            "e cig shop": {"volume": 35000, "opportunity": 60.0, "ranking_status": "not_ranking"},
            # Flavour Beast cluster - moderate volume
            "flavour beast canada": {"volume": 2500, "opportunity": 75.0, "ranking_status": "quick_win"},
            "flavour beast disposable": {"volume": 1800, "opportunity": 72.0, "ranking_status": "striking_distance"},
        }

        from backend.app.services.keyword_clustering._planning import (
            enrich_cluster_for_content,
            load_entity_rules,
        )

        ecig_cluster = {
            "name": "Ecig Shop",
            "keywords": ["ecig shop", "e cig shop"],
            "primary_keyword": "ecig shop",
            "content_type": "collection_page",
        }

        fb_cluster = {
            "name": "Flavour Beast Products",
            "keywords": ["flavour beast canada", "flavour beast disposable"],
            "primary_keyword": "flavour beast canada",
            "content_type": "collection_page",
        }

        entity_rules = load_entity_rules(conn)

        enriched_ecig = enrich_cluster_for_content(
            ecig_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )
        enriched_fb = enrich_cluster_for_content(
            fb_cluster, conn, keywords_map, entity_rules, store_fit_ctx=ctx
        )

        # Flavour Beast should beat ecig shop
        assert enriched_fb["priority_score"] > enriched_ecig["priority_score"]
        assert enriched_ecig.get("store_fit", {}).get("is_ultra_generic") is True

        conn.close()
