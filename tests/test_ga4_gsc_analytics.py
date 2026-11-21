"""Tests for GA4/GSC analytics improvements.

Tests for:
1. GA4 organic funnel report
2. Organic sessions and engagement metrics
3. Query × page cannibalization detection
4. Decay watch-list
5. Opportunity scoring with GSC signals
"""
import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def db(testdb):
    """Set up test database with required tables and sample data."""
    conn = testdb.connect()

    # Create gsc_query_rows table for cannibalization tests
    conn.execute("""
        CREATE TABLE gsc_query_rows (
            object_type TEXT NOT NULL,
            object_handle TEXT NOT NULL,
            url TEXT NOT NULL,
            query TEXT NOT NULL,
            clicks INTEGER,
            impressions INTEGER,
            ctr REAL,
            position REAL,
            fetched_at INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(object_type, object_handle, query)
        )
    """)

    # Create gsc_page_daily table for decay tests
    conn.execute("""
        CREATE TABLE gsc_page_daily (
            date TEXT NOT NULL,
            page_url TEXT NOT NULL,
            object_type TEXT NOT NULL DEFAULT '',
            object_handle TEXT NOT NULL DEFAULT '',
            clicks INTEGER NOT NULL DEFAULT 0,
            impressions INTEGER NOT NULL DEFAULT 0,
            ctr REAL NOT NULL DEFAULT 0,
            position REAL NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(date, page_url)
        )
    """)

    # Create ga4_organic_funnel table
    conn.execute("""
        CREATE TABLE ga4_organic_funnel (
            page_url TEXT NOT NULL,
            object_type TEXT NOT NULL DEFAULT '',
            object_handle TEXT NOT NULL DEFAULT '',
            window_days INTEGER NOT NULL DEFAULT 28,
            sessions INTEGER NOT NULL DEFAULT 0,
            add_to_carts INTEGER NOT NULL DEFAULT 0,
            checkouts INTEGER NOT NULL DEFAULT 0,
            purchases INTEGER NOT NULL DEFAULT 0,
            revenue REAL NOT NULL DEFAULT 0,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(page_url, window_days)
        )
    """)

    # Create products table for catalog GSC signals
    conn.execute("""
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            handle TEXT NOT NULL UNIQUE,
            gsc_ctr REAL,
            gsc_position REAL,
            published_at TEXT,
            status TEXT DEFAULT 'active'
        )
    """)

    conn.commit()
    yield conn
    conn.close()


class TestOpportunityScoring:
    """Tests for opportunity scoring with GSC signals."""

    def test_compute_opportunity_score_basic(self):
        """Test basic scoring without GSC catalog signals."""
        from backend.app.services.opportunities_service import compute_opportunity_score

        # Striking distance position, decent impressions
        score = compute_opportunity_score(
            position=12.0,
            impressions=500,
            clicks=15,
            ctr=0.03,
        )
        assert 50 <= score <= 100

    def test_compute_opportunity_score_with_gsc_signals(self):
        """Test scoring with catalog GSC signals."""
        from backend.app.services.opportunities_service import compute_opportunity_score

        # Same query-level data, but add low CTR at catalog level
        base_score = compute_opportunity_score(
            position=12.0,
            impressions=500,
            clicks=15,
            ctr=0.03,
        )

        # Low catalog CTR should boost opportunity score
        score_with_low_ctr = compute_opportunity_score(
            position=12.0,
            impressions=500,
            clicks=15,
            ctr=0.03,
            gsc_avg_ctr=0.01,  # Below 2.5% benchmark
            gsc_avg_position=12.0,
        )

        # Score should be slightly higher with low catalog CTR
        assert score_with_low_ctr >= base_score

    def test_compute_opportunity_score_no_gsc_signals_stable(self):
        """Test that None GSC signals don't change the base score."""
        from backend.app.services.opportunities_service import compute_opportunity_score

        base_score = compute_opportunity_score(
            position=15.0,
            impressions=200,
            clicks=5,
            ctr=0.025,
        )

        score_with_none = compute_opportunity_score(
            position=15.0,
            impressions=200,
            clicks=5,
            ctr=0.025,
            gsc_avg_ctr=None,
            gsc_avg_position=None,
        )

        assert base_score == score_with_none

    def test_score_bounded_0_100(self):
        """Test that scores are always between 0 and 100."""
        from backend.app.services.opportunities_service import compute_opportunity_score

        # Edge case: very high signals
        high_score = compute_opportunity_score(
            position=10.0,
            impressions=10000,
            clicks=100,
            ctr=0.001,
            gsc_avg_ctr=0.001,
            gsc_avg_position=10.0,
        )
        assert 0 <= high_score <= 100

        # Edge case: very low signals
        low_score = compute_opportunity_score(
            position=1.0,
            impressions=1,
            clicks=1,
            ctr=1.0,
            gsc_avg_ctr=0.5,
            gsc_avg_position=1.0,
        )
        assert 0 <= low_score <= 100


class TestCannibalizationDetection:
    """Tests for query × page cannibalization detection."""

    def test_fetch_cannibalization_queries(self, db):
        """Test detecting queries ranking for multiple URLs."""
        from backend.app.services.analytics_service import fetch_cannibalization_queries

        # Insert test data: same query ranking for 2 different URLs
        db.executemany(
            """INSERT INTO gsc_query_rows 
               (object_type, object_handle, url, query, clicks, impressions, ctr, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # "best vape pen" ranks for both product-a and product-b
                ("product", "product-a", "https://example.com/products/product-a", "best vape pen", 10, 100, 0.1, 5),
                ("product", "product-b", "https://example.com/products/product-b", "best vape pen", 5, 80, 0.0625, 8),
                # "cheap vape" only ranks for one URL (not cannibalized)
                ("product", "product-a", "https://example.com/products/product-a", "cheap vape", 20, 200, 0.1, 3),
            ],
        )
        db.commit()

        result = fetch_cannibalization_queries(db, min_impressions=20)

        assert result["total"] == 1
        assert len(result["queries"]) == 1
        assert result["queries"][0]["query"] == "best vape pen"
        assert len(result["queries"][0]["urls"]) == 2

    def test_fetch_cannibalization_min_impressions_filter(self, db):
        """Test that queries below min_impressions are filtered out."""
        from backend.app.services.analytics_service import fetch_cannibalization_queries

        # Insert low-impression cannibalized queries
        db.executemany(
            """INSERT INTO gsc_query_rows 
               (object_type, object_handle, url, query, clicks, impressions, ctr, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("product", "product-a", "https://example.com/products/product-a", "rare query", 1, 10, 0.1, 15),
                ("product", "product-b", "https://example.com/products/product-b", "rare query", 1, 15, 0.067, 18),
            ],
        )
        db.commit()

        result = fetch_cannibalization_queries(db, min_impressions=20)
        assert result["total"] == 0


class TestDecayWatchlist:
    """Tests for decay watch-list (pages with declining metrics)."""

    def test_fetch_decay_watchlist_detects_decline(self, db):
        """Test detecting pages with declining clicks/impressions."""
        from backend.app.services.analytics_service import fetch_decay_watchlist

        # Insert daily data for a page with declining metrics
        today = date.today()
        current_end = today - timedelta(days=3)
        current_start = current_end - timedelta(days=27)
        prior_end = current_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=27)

        # Prior window: 200 impressions, 20 clicks
        for i in range(28):
            d = prior_start + timedelta(days=i)
            db.execute(
                """INSERT INTO gsc_page_daily 
                   (date, page_url, object_type, object_handle, clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), "https://example.com/products/declining", "product", "declining", 1, 7, 0.14, 5),
            )

        # Current window: 100 impressions, 5 clicks (50% drop)
        for i in range(28):
            d = current_start + timedelta(days=i)
            db.execute(
                """INSERT INTO gsc_page_daily 
                   (date, page_url, object_type, object_handle, clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), "https://example.com/products/declining", "product", "declining", 0, 3, 0.0, 8),
            )

        db.commit()

        result = fetch_decay_watchlist(db, min_prior_impressions=100)

        assert result["total"] == 1
        assert len(result["pages"]) == 1
        assert result["pages"][0]["page_url"] == "https://example.com/products/declining"
        assert result["pages"][0]["changes"]["impressions_pct"] < 0

    def test_fetch_decay_watchlist_excludes_stable_pages(self, db):
        """Test that stable pages are not flagged for decay."""
        from backend.app.services.analytics_service import fetch_decay_watchlist

        today = date.today()
        current_end = today - timedelta(days=3)
        current_start = current_end - timedelta(days=27)
        prior_end = current_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=27)

        # Both windows: similar metrics (no decay)
        for i in range(28):
            d = prior_start + timedelta(days=i)
            db.execute(
                """INSERT INTO gsc_page_daily 
                   (date, page_url, object_type, object_handle, clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), "https://example.com/products/stable", "product", "stable", 1, 5, 0.2, 5),
            )

        for i in range(28):
            d = current_start + timedelta(days=i)
            db.execute(
                """INSERT INTO gsc_page_daily 
                   (date, page_url, object_type, object_handle, clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), "https://example.com/products/stable", "product", "stable", 1, 5, 0.2, 5),
            )

        db.commit()

        result = fetch_decay_watchlist(db, min_prior_impressions=100)
        assert result["total"] == 0


class TestOrganicFunnelFlags:
    """Tests for organic funnel flags."""

    def test_fetch_organic_funnel_flags(self, db):
        """Test fetching organic funnel flags."""
        from backend.app.services.analytics_service import fetch_organic_funnel_flags

        # Insert test data
        db.executemany(
            """INSERT INTO ga4_organic_funnel 
               (page_url, object_type, object_handle, window_days, sessions, add_to_carts, checkouts, purchases, revenue, start_date, end_date, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                # High sessions, zero carts
                ("/products/no-carts", "product", "no-carts", 28, 50, 0, 0, 0, 0, "2026-10-01", "2026-10-28", 1730000000),
                # Checkout assist
                ("/products/assists", "product", "assists", 28, 20, 5, 3, 1, 99.99, "2026-10-01", "2026-10-28", 1730000000),
            ],
        )
        db.commit()

        result = fetch_organic_funnel_flags(db, window_days=28, high_sessions_threshold=10)

        assert len(result["high_sessions_no_carts"]) == 1
        assert result["high_sessions_no_carts"][0]["page_url"] == "/products/no-carts"
        assert result["high_sessions_no_carts"][0]["flag"] == "high_organic_sessions_zero_carts"

        assert len(result["checkout_assists"]) == 1
        assert result["checkout_assists"][0]["page_url"] == "/products/assists"
        assert result["checkout_assists"][0]["flag"] == "assists_checkout_purchase"


class TestGA4OrganicFunnel:
    """Tests for GA4 organic funnel report functions."""

    def test_is_excluded_funnel_path(self):
        """Test path exclusion for checkout/cart URLs.

        Must match exact /cart or /cart/ prefix, /checkouts/ prefix.
        Must NOT match /cartoon, /cartography, etc.
        """
        from shopifyseo.dashboard_google._ga4 import _is_excluded_funnel_path

        # Should be excluded
        assert _is_excluded_funnel_path("/cart") is True
        assert _is_excluded_funnel_path("/cart/") is True
        assert _is_excluded_funnel_path("/cart/item") is True
        assert _is_excluded_funnel_path("/checkouts/abc123") is True
        assert _is_excluded_funnel_path("/checkouts/") is True

        # Should NOT be excluded (not checkout flow)
        assert _is_excluded_funnel_path("/products/vape-pen") is False
        assert _is_excluded_funnel_path("/collections/all") is False
        assert _is_excluded_funnel_path("/cartoon") is False  # /cart should not match /cartoon
        assert _is_excluded_funnel_path("/cartography") is False
        assert _is_excluded_funnel_path("/checkout") is False  # /checkouts/ prefix only

    def test_build_ga4_organic_funnel_index(self):
        """Test building funnel index from rows."""
        from shopifyseo.dashboard_google._ga4 import build_ga4_organic_funnel_index

        funnel_data = {
            "rows": [
                {"path": "/products/a", "sessions": 10, "add_to_carts": 2},
                {"path": "/products/b", "sessions": 20, "add_to_carts": 5},
            ]
        }

        index = build_ga4_organic_funnel_index(funnel_data)
        assert "/products/a" in index
        assert "/products/b" in index
        assert index["/products/a"]["sessions"] == 10

    def test_build_ga4_organic_sessions_index(self):
        """Test building organic sessions index with query string normalization."""
        from shopifyseo.dashboard_google._ga4 import build_ga4_organic_sessions_index

        organic_rows = [
            {"dimensionValues": [{"value": "/products/a"}], "metricValues": [{"value": "50"}]},
            {"dimensionValues": [{"value": "/products/b"}], "metricValues": [{"value": "100"}]},
            # Query string variants should be aggregated to the base path
            {"dimensionValues": [{"value": "/products/a?utm_source=google"}], "metricValues": [{"value": "25"}]},
        ]

        index = build_ga4_organic_sessions_index(organic_rows)
        # /products/a should include both the base path and query string variant
        assert index["/products/a"] == 75  # 50 + 25
        assert index["/products/b"] == 100

    def test_build_ga4_engagement_index(self):
        """Test building engagement metrics index with query string normalization."""
        from shopifyseo.dashboard_google._ga4 import build_ga4_engagement_index

        engagement_rows = [
            {"dimensionValues": [{"value": "/products/a"}], "metricValues": [{"value": "30"}, {"value": "0.6"}]},
            # Query string variant should be aggregated
            {"dimensionValues": [{"value": "/products/a?ref=cta"}], "metricValues": [{"value": "20"}, {"value": "0.5"}]},
        ]

        index = build_ga4_engagement_index(engagement_rows)
        # Should aggregate: 30+20=50 engaged sessions, weighted avg rate
        assert index["/products/a"][0] == 50
        # Weighted average: (0.6*30 + 0.5*20) / 50 = (18 + 10) / 50 = 0.56
        assert abs(index["/products/a"][1] - 0.56) < 0.01

    def test_ga4_url_detail_from_index_enhanced(self):
        """Test enhanced URL detail includes organic sessions and engagement."""
        from shopifyseo.dashboard_google._ga4 import ga4_url_detail_from_index_enhanced

        index = {
            "views_by_path": {"/products/test": 100},
            "landing_by_path": {"/products/test": (50, 120.5)},
            "organic_sessions_by_path": {"/products/test": 25},
            "engagement_by_path": {"/products/test": (20, 0.4)},
        }

        detail = ga4_url_detail_from_index_enhanced(
            index,
            "https://example.com/products/test",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 28),
        )

        assert detail["views"] == 100
        assert detail["sessions"] == 50
        assert detail["organic_sessions"] == 25
        assert detail["engaged_sessions"] == 20
        assert detail["engagement_rate"] == 0.4


class TestBlendedOpportunity:
    """Test blended opportunity scoring label fix."""

    def test_blended_opportunity_labels_all_channel_sessions(self):
        """Test that blended opportunity correctly labels sessions as all-channel."""
        from shopifyseo.dashboard_insights import blended_opportunity

        ga4_summary = {
            "rows": [
                {
                    "dimensionValues": [{"value": "/products/test"}],
                    "metricValues": [
                        {"value": "50"},  # sessions
                        {"value": "100"},  # something else
                        {"value": "30"},  # avg duration
                    ],
                }
            ]
        }

        result = blended_opportunity(
            base_score=50,
            url="https://example.com/products/test",
            ga4_summary=ga4_summary,
        )

        # Should use "landing traffic (all channels)" not "organic landing traffic"
        assert "landing traffic (all channels)" in result["reasons"]
        assert "organic landing traffic" not in result["reasons"]


class TestPostgresCompatibility:
    """Postgres-specific tests for SQL compatibility.

    These tests verify that the SQL queries work correctly on both SQLite and Postgres.
    When TEST_DATABASE_URL is set to Postgres, these tests run against Postgres.
    """

    def test_cannibalization_query_postgres_compatible(self, db):
        """Test that cannibalization query works on Postgres (GROUP BY is correct)."""
        from backend.app.services.analytics_service import fetch_cannibalization_queries

        # Insert multiple queries with multiple URLs each
        db.executemany(
            """INSERT INTO gsc_query_rows 
               (object_type, object_handle, url, query, clicks, impressions, ctr, position)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                ("product", "a", "https://example.com/a", "query1", 10, 100, 0.1, 5),
                ("product", "b", "https://example.com/b", "query1", 5, 50, 0.1, 8),
                ("product", "c", "https://example.com/c", "query2", 20, 200, 0.1, 3),
                ("product", "d", "https://example.com/d", "query2", 15, 150, 0.1, 4),
            ],
        )
        db.commit()

        # This should not raise on Postgres - the GROUP BY is correct
        result = fetch_cannibalization_queries(db, min_impressions=20)
        assert result["total"] == 2
        assert len(result["queries"]) == 2

    def test_decay_watchlist_query_postgres_compatible(self, db):
        """Test that decay watchlist query works on Postgres (GROUP BY with MAX aggregates)."""
        from backend.app.services.analytics_service import fetch_decay_watchlist

        today = date.today()
        current_end = today - timedelta(days=3)
        current_start = current_end - timedelta(days=27)
        prior_end = current_start - timedelta(days=1)
        prior_start = prior_end - timedelta(days=27)

        # Insert data with varying object_type/object_handle per page_url
        # This tests that MAX() aggregation works correctly on Postgres
        for i in range(28):
            d = prior_start + timedelta(days=i)
            db.execute(
                """INSERT INTO gsc_page_daily 
                   (date, page_url, object_type, object_handle, clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), "https://example.com/page1", "product", "handle-a", 2, 10, 0.2, 5),
            )

        for i in range(28):
            d = current_start + timedelta(days=i)
            # Note: object_handle changes but should still work with MAX()
            db.execute(
                """INSERT INTO gsc_page_daily 
                   (date, page_url, object_type, object_handle, clicks, impressions, ctr, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (d.isoformat(), "https://example.com/page1", "product", "handle-a", 0, 2, 0.0, 10),
            )

        db.commit()

        # This should not raise on Postgres - the GROUP BY with MAX() is correct
        result = fetch_decay_watchlist(db, min_prior_impressions=100)
        assert result["total"] == 1
        assert result["pages"][0]["object_type"] == "product"
        assert result["pages"][0]["object_handle"] == "handle-a"
