"""Tests for the article idea lifecycle: approve, link to article, performance tracking."""
import sqlite3

import pytest

from shopifyseo.dashboard_queries import (
    fetch_article_ideas,
    link_idea_to_article,
    save_article_ideas,
    update_article_idea_status,
)
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_dashboard_schema(c)
    return c


@pytest.fixture
def saved_idea_id(conn: sqlite3.Connection) -> int:
    ids = save_article_ideas(
        conn,
        [
            {
                "suggested_title": "Best Disposable Vapes Canada 2025",
                "brief": "A buying guide for Canadian vapers.",
                "primary_keyword": "best disposable vapes canada",
                "supporting_keywords": [],
                "search_intent": "commercial",
                "content_format": "buying_guide",
                "estimated_monthly_traffic": 60,
                "linked_cluster_id": None,
                "linked_cluster_name": "",
                "linked_collection_handle": "disposable-vapes",
                "linked_collection_title": "Disposable Vapes",
                "source_type": "cluster_gap",
                "gap_reason": "Quick win at pos 14.",
                "total_volume": 1200,
                "avg_difficulty": 28.5,
                "opportunity_score": 75.0,
                "dominant_serp_features": "",
                "content_format_hints": "",
                "linked_keywords_json": "[]",
            }
        ],
    )
    return ids[0]


def test_schema_has_new_columns(conn: sqlite3.Connection):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(article_ideas)").fetchall()}
    for expected in ["linked_article_handle", "linked_blog_handle", "shopify_article_id"]:
        assert expected in cols, f"Missing column: {expected}"


def test_update_article_idea_status(conn: sqlite3.Connection, saved_idea_id: int):
    updated = update_article_idea_status(conn, saved_idea_id, "approved")
    assert updated is True
    ideas = fetch_article_ideas(conn)
    idea = next(i for i in ideas if i["id"] == saved_idea_id)
    assert idea["status"] == "approved"


def test_update_nonexistent_idea_returns_false(conn: sqlite3.Connection):
    updated = update_article_idea_status(conn, 9999, "approved")
    assert updated is False


def test_link_idea_to_article(conn: sqlite3.Connection, saved_idea_id: int):
    result = link_idea_to_article(
        conn,
        idea_id=saved_idea_id,
        article_handle="best-disposable-vapes-canada",
        blog_handle="news",
        shopify_article_id="gid://shopify/OnlineStoreArticle/999",
    )
    assert result is True
    ideas = fetch_article_ideas(conn)
    idea = next(i for i in ideas if i["id"] == saved_idea_id)
    assert idea["status"] == "idea"
    assert idea["linked_article_handle"] == "best-disposable-vapes-canada"
    assert idea["linked_blog_handle"] == "news"
    assert idea["shopify_article_id"] == "gid://shopify/OnlineStoreArticle/999"


def test_link_idea_to_article_keeps_approved_status(conn: sqlite3.Connection, saved_idea_id: int):
    assert update_article_idea_status(conn, saved_idea_id, "approved") is True
    assert (
        link_idea_to_article(
            conn,
            idea_id=saved_idea_id,
            article_handle="best-disposable-vapes-canada",
            blog_handle="news",
            shopify_article_id="gid://shopify/OnlineStoreArticle/999",
        )
        is True
    )
    idea = next(i for i in fetch_article_ideas(conn) if i["id"] == saved_idea_id)
    assert idea["status"] == "approved"


def test_fetch_ideas_includes_new_fields_with_defaults(conn: sqlite3.Connection, saved_idea_id: int):
    ideas = fetch_article_ideas(conn)
    idea = next(i for i in ideas if i["id"] == saved_idea_id)
    assert idea["linked_article_handle"] == ""
    assert idea["linked_blog_handle"] == ""
    assert idea["shopify_article_id"] == ""
    assert idea["article_count"] == 0
    assert idea["agg_gsc_clicks"] == 0
    assert idea["agg_gsc_impressions"] == 0
