"""Tests for refresh_article_idea_serp_snapshot (SerpAPI backfill on existing ideas)."""

import sqlite3

import pytest

from shopifyseo.dashboard_queries import refresh_article_idea_serp_snapshot, save_article_ideas
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_dashboard_schema(c)
    return c


def _save_minimal_idea(conn: sqlite3.Connection, *, primary_keyword: str = "test keyword") -> int:
    ids = save_article_ideas(
        conn,
        [
            {
                "suggested_title": "Title",
                "brief": "Brief body.",
                "primary_keyword": primary_keyword,
                "supporting_keywords": [],
                "search_intent": "informational",
                "content_format": "",
                "estimated_monthly_traffic": 0,
                "linked_cluster_id": None,
                "linked_cluster_name": "",
                "linked_collection_handle": "",
                "linked_collection_title": "",
                "source_type": "cluster_gap",
                "gap_reason": "",
                "total_volume": 0,
                "avg_difficulty": 0.0,
                "opportunity_score": 0.0,
                "dominant_serp_features": "",
                "content_format_hints": "",
                "linked_keywords_json": "[]",
            }
        ],
    )
    return ids[0]


def test_refresh_requires_serpapi_key(conn: sqlite3.Connection):
    idea_id = _save_minimal_idea(conn)
    with pytest.raises(ValueError, match="SerpAPI"):
        refresh_article_idea_serp_snapshot(conn, idea_id)


def test_refresh_requires_primary_keyword(conn: sqlite3.Connection):
    idea_id = _save_minimal_idea(conn, primary_keyword="")
    conn.execute(
        "INSERT OR REPLACE INTO service_settings (key, value) VALUES (?, ?)",
        ("serpapi_api_key", "k"),
    )
    conn.commit()
    with pytest.raises(ValueError, match="primary keyword"):
        refresh_article_idea_serp_snapshot(conn, idea_id)


def test_refresh_not_found(conn: sqlite3.Connection):
    conn.execute(
        "INSERT OR REPLACE INTO service_settings (key, value) VALUES (?, ?)",
        ("serpapi_api_key", "k"),
    )
    conn.commit()
    with pytest.raises(LookupError):
        refresh_article_idea_serp_snapshot(conn, 99999)


def test_refresh_persists_snapshot(monkeypatch: pytest.MonkeyPatch, conn: sqlite3.Connection):
    idea_id = _save_minimal_idea(conn, primary_keyword="zyn canada")
    conn.execute(
        "INSERT OR REPLACE INTO service_settings (key, value) VALUES (?, ?)",
        ("serpapi_api_key", "fake-key"),
    )
    conn.commit()

    def fake_snapshot(_c: sqlite3.Connection, _kw: str) -> dict:
        return {
            "audience_questions": [{"question": "Q1?", "snippet": "S1"}],
            "top_ranking_pages": [{"title": "T1", "url": "https://example.com/1"}],
            "ai_overview": {
                "text_blocks": [{"type": "paragraph", "snippet": "Overview line."}],
                "references": [{"title": "R1", "link": "https://r.example", "snippet": "", "source": "", "index": 0}],
            },
            "related_searches": [{"query": "related kw one", "position": 5}],
        }

    monkeypatch.setattr(
        "shopifyseo.audience_questions_api.fetch_serpapi_primary_keyword_snapshot",
        fake_snapshot,
    )

    updated = refresh_article_idea_serp_snapshot(conn, idea_id)
    assert updated["id"] == idea_id
    assert updated["audience_questions"] == [{"question": "Q1?", "snippet": "S1"}]
    assert updated["top_ranking_pages"] == [{"title": "T1", "url": "https://example.com/1"}]
    assert updated["ai_overview"] is not None
    assert updated["ai_overview"]["text_blocks"][0]["snippet"] == "Overview line."
    assert updated["related_searches"] == [{"query": "related kw one", "position": 5}]

    row = conn.execute(
        "SELECT audience_questions_json, top_ranking_pages_json, ai_overview_json, related_searches_json FROM article_ideas WHERE id = ?",
        (idea_id,),
    ).fetchone()
    assert "Q1?" in (row[0] or "")
    assert "example.com" in (row[1] or "")
    assert "Overview line." in (row[2] or "")
    assert "related kw one" in (row[3] or "")
