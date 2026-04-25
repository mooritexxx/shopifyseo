"""Tests for automatic article idea cluster linkage."""

import sqlite3

from shopifyseo.dashboard_ai_engine_parts._article_ideas import (
    _best_cluster_for_idea,
    _cluster_keywords_snapshot,
)
from shopifyseo.dashboard_store import ensure_dashboard_schema


def test_best_cluster_for_idea_repairs_missing_cluster_id():
    idea = {
        "suggested_title": "Best Disposable Vapes in Canada",
        "primary_keyword": "best disposable vapes canada",
        "supporting_keywords": ["cheap disposable vapes", "disposable vape canada"],
        "brief": "A buying guide for disposable vape shoppers.",
        "linked_cluster_id": None,
    }
    clusters = [
        {
            "id": 11,
            "name": "Pod Systems",
            "primary_keyword": "refillable pod vape",
            "top_keywords": [{"keyword": "pod vape canada"}],
            "total_volume": 5000,
            "avg_opportunity": 70.0,
        },
        {
            "id": 22,
            "name": "Disposable Vapes",
            "primary_keyword": "best disposable vapes canada",
            "top_keywords": [
                {"keyword": "cheap disposable vapes"},
                {"keyword": "disposable vape canada"},
            ],
            "total_volume": 1200,
            "avg_opportunity": 40.0,
        },
    ]

    chosen = _best_cluster_for_idea(idea, clusters)

    assert chosen["id"] == 22
    assert chosen["name"] == "Disposable Vapes"


def test_cluster_keywords_snapshot_includes_all_cluster_keywords_with_metrics():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    conn.execute(
        """
        INSERT INTO clusters
          (name, content_type, primary_keyword, content_brief,
           total_volume, avg_difficulty, avg_opportunity, generated_at)
        VALUES ('Disposable Vapes', 'blog_post', 'best disposable vapes canada',
                'Brief.', 1200, 28.0, 75.0, '2026-01-01T00:00:00Z')
        """
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
        (cid, "best disposable vapes canada"),
    )
    conn.execute(
        "INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
        (cid, "cheap disposable vapes"),
    )
    conn.execute(
        """
        INSERT INTO keyword_metrics
          (keyword, volume, difficulty, ranking_status, gsc_position, opportunity, updated_at)
        VALUES
          ('best disposable vapes canada', 1200, 28, 'quick_win', 14.2, 80.0, 0),
          ('cheap disposable vapes', 700, 22, 'not_ranking', NULL, 55.0, 0)
        """
    )
    conn.commit()

    rows = _cluster_keywords_snapshot(
        conn,
        {
            "id": cid,
            "primary_keyword": "best disposable vapes canada",
            "top_keywords": [],
        },
    )

    assert [row["keyword"] for row in rows[:2]] == [
        "best disposable vapes canada",
        "cheap disposable vapes",
    ]
    assert rows[0]["volume"] == 1200
    assert rows[0]["ranking_status"] == "quick_win"
    assert rows[0]["gsc_position"] == 14.2
