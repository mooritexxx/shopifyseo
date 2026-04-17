"""Tests for save_article_ideas and fetch_article_ideas with new enrichment columns."""
import json
import sqlite3

import pytest

from shopifyseo.dashboard_queries import save_article_ideas, fetch_article_ideas
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    return conn


def test_save_and_fetch_article_ideas_round_trips_new_fields(db_conn):
    idea = {
        "suggested_title": "Best Disposable Vapes Canada 2025",
        "brief": "A 300-word buying guide for Canadian vapers.",
        "primary_keyword": "best disposable vapes canada",
        "supporting_keywords": ["cheap disposable vapes", "disposable vape canada"],
        "search_intent": "commercial",
        "content_format": "buying_guide",
        "estimated_monthly_traffic": 60,
        "linked_cluster_id": 1,
        "linked_cluster_name": "Disposable Vapes",
        "linked_collection_handle": "disposable-vapes",
        "linked_collection_title": "Disposable Vapes",
        "source_type": "cluster_gap",
        "gap_reason": "Ranking pos 14 for primary kw (1,200/mo) — strong quick win.",
        "total_volume": 1200,
        "avg_difficulty": 28.5,
        "opportunity_score": 75.0,
        "dominant_serp_features": "featured_snippet, people_also_ask",
        "content_format_hints": "buying_guide, listicle",
        "linked_keywords_json": json.dumps([{"keyword": "best disposable vapes canada", "volume": 1200}]),
    }
    ids = save_article_ideas(db_conn, [idea])
    assert len(ids) == 1

    fetched = fetch_article_ideas(db_conn)
    assert len(fetched) == 1
    row = fetched[0]

    assert row["suggested_title"] == "Best Disposable Vapes Canada 2025"
    assert row["content_format"] == "buying_guide"
    assert row["estimated_monthly_traffic"] == 60
    assert row["source_type"] == "cluster_gap"
    assert row["total_volume"] == 1200
    assert row["avg_difficulty"] == 28.5
    assert row["opportunity_score"] == 75.0
    assert row["dominant_serp_features"] == "featured_snippet, people_also_ask"
    assert row["content_format_hints"] == "buying_guide, listicle"
    kws = row["linked_keywords_json"]
    assert isinstance(kws, list)
    assert kws[0]["keyword"] == "best disposable vapes canada"

    db_conn.close()
