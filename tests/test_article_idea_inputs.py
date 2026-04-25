"""Tests for article idea gap aggregation (cluster stats + competitor dedupe)."""

import sqlite3

import pytest

from shopifyseo.dashboard_queries import fetch_article_idea_inputs
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def idea_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    return conn


def test_fetch_article_idea_inputs_cluster_stats_and_competitor_dedupe(idea_conn: sqlite3.Connection):
    conn = idea_conn

    conn.execute(
        """
        INSERT INTO clusters
          (name, content_type, primary_keyword, content_brief,
           total_volume, avg_difficulty, avg_opportunity,
           dominant_serp_features, content_format_hints, avg_cps,
           match_type, match_handle, match_title, generated_at)
        VALUES
          ('Test Cluster', 'blog_post', 'overlap kw', 'Brief here.',
           5000, 30.0, 80.0,
           'People also ask, Video', 'Long-form guide', 1.5,
           NULL, NULL, NULL, '2026-01-01T00:00:00Z')
        """
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
        (cid, "overlap kw"),
    )
    conn.execute(
        "INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
        (cid, "supporting kw"),
    )

    conn.execute(
        """
        INSERT INTO keyword_metrics (keyword, volume, difficulty, intent, opportunity, updated_at)
        VALUES
          ('overlap kw', 800, 25, 'informational', 70.0, 0),
          ('supporting kw', 400, 28, 'informational', 50.0, 0),
          ('unique competitor kw', 900, 20, 'informational', 85.0, 0)
        """
    )

    conn.execute(
        """
        INSERT INTO competitor_keyword_gaps
          (keyword, competitor_domain, volume, difficulty, traffic_potential,
           gap_type, updated_at)
        VALUES
          ('unique competitor kw', 'other.com', 900, 20, 100, 'they_rank_we_dont', 0),
          ('overlap kw', 'other.com', 2000, 15, 200, 'they_rank_we_dont', 0)
        """
    )
    conn.commit()

    data = fetch_article_idea_inputs(conn)

    assert len(data["cluster_gaps"]) == 1
    cg = data["cluster_gaps"][0]
    assert cg["dominant_serp_features"] == "People also ask, Video"
    assert cg["content_format_hints"] == "Long-form guide"
    assert cg["avg_cps"] == 1.5
    assert any(k["keyword"] == "overlap kw" for k in cg["top_keywords"])

    koverlap = next(k for k in cg["top_keywords"] if k["keyword"] == "overlap kw")
    assert "content_format_hint" in koverlap
    assert "serp_features_compact" in koverlap

    gaps = data["competitor_gaps"]
    keywords = {g["keyword"] for g in gaps}
    assert "overlap kw" not in keywords
    assert "unique competitor kw" in keywords
    assert data["competitor_gaps_dedupe_skipped"] >= 1

    conn.close()


def test_top_keywords_have_word_count_and_first_seen(idea_conn: sqlite3.Connection):
    conn = idea_conn
    conn.execute(
        """
        INSERT INTO clusters
          (name, content_type, primary_keyword, content_brief,
           total_volume, avg_difficulty, avg_opportunity,
           match_type, match_handle, match_title, generated_at)
        VALUES ('WC Cluster', 'blog_post', 'wc kw', 'Brief.',
                1000, 20.0, 60.0, NULL, NULL, NULL, '2026-01-01T00:00:00Z')
        """
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cid, "wc kw"))
    conn.execute(
        """
        INSERT INTO keyword_metrics
          (keyword, volume, difficulty, opportunity, word_count, first_seen,
           traffic_potential, global_volume, updated_at)
        VALUES ('wc kw', 500, 15, 40.0, 1800, '2024-01-01', 350, 9200, 0)
        """
    )
    conn.commit()
    data = fetch_article_idea_inputs(conn)
    cg = next(c for c in data["cluster_gaps"] if c["name"] == "WC Cluster")
    kw = next(k for k in cg["top_keywords"] if k["keyword"] == "wc kw")
    assert kw["word_count"] == 1800
    assert kw["first_seen"] == "2024-01-01"
    assert kw["traffic_potential"] == 350
    assert kw["global_volume"] == 9200
    conn.close()


def test_competitor_gaps_have_position_and_url(idea_conn: sqlite3.Connection):
    conn = idea_conn
    conn.execute(
        """
        INSERT INTO competitor_keyword_gaps
          (keyword, competitor_domain, volume, difficulty, traffic_potential,
           gap_type, competitor_position, competitor_url, updated_at)
        VALUES ('rival kw', 'rival.com', 300, 25, 100, 'they_rank_we_dont', 3, 'https://rival.com/page', 0)
        """
    )
    conn.execute(
        """
        INSERT INTO keyword_metrics (keyword, volume, difficulty, intent, opportunity, updated_at)
        VALUES ('rival kw', 300, 25, 'informational', 60.0, 0)
        """
    )
    conn.commit()
    data = fetch_article_idea_inputs(conn)
    gaps = data["competitor_gaps"]
    assert any(g["keyword"] == "rival kw" for g in gaps)
    gap = next(g for g in gaps if g["keyword"] == "rival kw")
    assert gap["competitor_position"] == 3
    assert gap["competitor_url"] == "https://rival.com/page"
    conn.close()


def test_cluster_gaps_have_existing_page_from_keyword_page_map(idea_conn: sqlite3.Connection):
    conn = idea_conn
    conn.execute(
        """
        INSERT INTO clusters
          (name, content_type, primary_keyword, content_brief,
           total_volume, avg_difficulty, avg_opportunity,
           match_type, match_handle, match_title, generated_at)
        VALUES ('KPM Cluster', 'blog_post', 'kpm kw', 'Brief.',
                800, 20.0, 50.0, NULL, NULL, NULL, '2026-01-01T00:00:00Z')
        """
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cid, "kpm kw"))
    conn.execute(
        """
        INSERT INTO keyword_page_map
          (keyword, object_type, object_handle, gsc_position, is_primary, updated_at)
        VALUES ('kpm kw', 'collection', 'vapes', 7.5, 1, 0)
        """
    )
    conn.commit()
    data = fetch_article_idea_inputs(conn)
    cg = next(c for c in data["cluster_gaps"] if c["name"] == "KPM Cluster")
    assert cg["existing_page"] is not None
    assert cg["existing_page"]["object_type"] == "collection"
    assert cg["existing_page"]["object_handle"] == "vapes"
    assert cg["existing_page"]["gsc_position"] == 7.5
    conn.close()


def test_article_ideas_schema_has_new_columns(idea_conn: sqlite3.Connection):
    cols = {
        row[1]
        for row in idea_conn.execute("PRAGMA table_info(article_ideas)").fetchall()
    }
    for expected in [
        "total_volume", "avg_difficulty", "opportunity_score",
        "dominant_serp_features", "content_format_hints",
        "content_format", "source_type", "linked_keywords_json",
        "estimated_monthly_traffic",
        "top_ranking_pages_json",
        "ai_overview_json",
        "related_searches_json",
        "paa_expansion_json",
    ]:
        assert expected in cols, f"Missing column: {expected}"
