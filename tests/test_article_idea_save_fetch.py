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
        "audience_questions": [
            {"question": "Are disposable vapes legal in Canada?", "snippet": "Varies by province."},
            {"question": "What is the best disposable vape brand?", "snippet": ""},
        ],
        "top_ranking_pages": [
            {"title": "Health Canada — Vaping", "url": "https://www.canada.ca/en/health-canada/services/smoking-vaping.html"},
            {"title": "Wiki Vape", "url": "https://en.wikipedia.org/wiki/Vaping"},
        ],
        "ai_overview": {
            "text_blocks": [{"type": "paragraph", "snippet": "Disposable and pod systems are common."}],
            "references": [
                {"title": "Guide", "link": "https://example.com/guide", "snippet": "Preview", "source": "Ex", "index": 0},
            ],
        },
        "related_searches": [
            {"query": "disposable vapes cheap", "position": 2},
            {"query": "best vape canada", "position": 1},
        ],
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
    assert row["audience_questions"] == [
        {"question": "Are disposable vapes legal in Canada?", "snippet": "Varies by province."},
        {"question": "What is the best disposable vape brand?", "snippet": ""},
    ]
    assert row["top_ranking_pages"] == [
        {"title": "Health Canada — Vaping", "url": "https://www.canada.ca/en/health-canada/services/smoking-vaping.html"},
        {"title": "Wiki Vape", "url": "https://en.wikipedia.org/wiki/Vaping"},
    ]
    assert row["ai_overview"] is not None
    assert row["ai_overview"]["text_blocks"][0]["snippet"] == "Disposable and pod systems are common."
    assert row["ai_overview"]["references"][0]["link"] == "https://example.com/guide"
    assert row["related_searches"] == [
        {"query": "disposable vapes cheap", "position": 2},
        {"query": "best vape canada", "position": 1},
    ]

    db_conn.close()


def test_save_and_fetch_primary_and_secondary_targets(db_conn):
    idea = {
        "suggested_title": "Best Disposable Vapes Canada 2025",
        "brief": "Brief.",
        "primary_keyword": "best disposable vapes canada",
        "supporting_keywords": ["cheap disposable vapes"],
        "search_intent": "commercial",
        "primary_target": {
            "type": "collection",
            "handle": "disposable-vapes",
            "title": "Disposable Vapes",
            "url": "https://example.com/collections/disposable-vapes",
            "anchor_keyword": "",
            "source": "cluster_match",
        },
        "secondary_targets": [
            {
                "type": "product",
                "handle": "elfbar-bc5000",
                "title": "Elfbar BC5000",
                "url": "https://example.com/products/elfbar-bc5000",
                "anchor_keyword": "best disposable vape",
                "source": "keyword_page_map",
            },
            {
                "type": "blog_article",
                "handle": "blog/guide-to-vaping",
                "title": "Guide to Vaping",
                "url": "https://example.com/blogs/blog/guide-to-vaping",
                "anchor_keyword": "vape guide",
                "source": "keyword_page_map",
            },
        ],
    }
    save_article_ideas(db_conn, [idea])
    row = fetch_article_ideas(db_conn)[0]

    assert row["primary_target"] == {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    assert len(row["secondary_targets"]) == 2
    assert row["secondary_targets"][0]["type"] == "product"
    assert row["secondary_targets"][0]["anchor_keyword"] == "best disposable vape"
    assert row["secondary_targets"][1]["handle"] == "blog/guide-to-vaping"


def test_save_article_ideas_without_targets_returns_none_and_empty_list(db_conn):
    idea = {
        "suggested_title": "No Targets Idea",
        "brief": "Brief.",
        "primary_keyword": "kw",
        "supporting_keywords": [],
        "search_intent": "informational",
    }
    save_article_ideas(db_conn, [idea])
    row = fetch_article_ideas(db_conn)[0]
    assert row["primary_target"] is None
    assert row["secondary_targets"] == []


def test_save_article_ideas_legacy_string_audience_questions_normalized(db_conn):
    idea = {
        "suggested_title": "Legacy Qs",
        "brief": "Brief",
        "primary_keyword": "kw",
        "supporting_keywords": [],
        "search_intent": "informational",
        "audience_questions": ["  One?  ", "Two?"],
    }
    save_article_ideas(db_conn, [idea])
    row = fetch_article_ideas(db_conn)[0]
    assert row["audience_questions"] == [
        {"question": "One?", "snippet": ""},
        {"question": "Two?", "snippet": ""},
    ]


def test_save_article_ideas_legacy_answer_key_maps_to_snippet(db_conn):
    idea = {
        "suggested_title": "Legacy answer key",
        "brief": "Brief",
        "primary_keyword": "kw",
        "supporting_keywords": [],
        "search_intent": "informational",
        "audience_questions": [{"question": "Q?", "answer": "Legacy body"}],
    }
    save_article_ideas(db_conn, [idea])
    row = fetch_article_ideas(db_conn)[0]
    assert row["audience_questions"] == [{"question": "Q?", "snippet": "Legacy body"}]
