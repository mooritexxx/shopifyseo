"""Equal sort keys must come out in the same order on SQLite and Postgres.

Each CI job runs one backend via testdb. The expected order is the
tie-break (primary key / type+handle ASC) so both jobs assert the same
sequence. Temporary databases only.
"""

from __future__ import annotations

import numpy as np

from backend.app.routers.internal_links import collect_entity_graph_stats
from backend.app.services.keyword_clustering import load_clusters
from shopifyseo.embedding_store import EMBEDDING_DIMS, find_cannibalization_candidates
from shopifyseo.internal_links.pipeline import _orphan_targets
from tests.test_embedding_store import _insert_embedding, _make_conn


def _link_schema(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE products (
            handle TEXT PRIMARY KEY,
            status TEXT DEFAULT 'ACTIVE',
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0
        );
        CREATE TABLE collections (
            handle TEXT PRIMARY KEY,
            api_unreachable INTEGER DEFAULT 0,
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0
        );
        CREATE TABLE pages (
            handle TEXT PRIMARY KEY,
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0
        );
        CREATE TABLE blog_articles (
            blog_handle TEXT,
            handle TEXT,
            is_published INTEGER DEFAULT 1,
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0
        );
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL
        );
        """
    )


def test_orphan_targets_tie_break_is_type_then_handle(db_conn):
    _link_schema(db_conn)
    # Same clicks+impressions (10) so the old reverse sort is a tie.
    db_conn.execute(
        "INSERT INTO products (handle, status, gsc_clicks, gsc_impressions) VALUES (?, 'ACTIVE', 5, 5)",
        ("zeta",),
    )
    db_conn.execute(
        "INSERT INTO products (handle, status, gsc_clicks, gsc_impressions) VALUES (?, 'ACTIVE', 5, 5)",
        ("alpha",),
    )
    db_conn.execute(
        "INSERT INTO collections (handle, gsc_clicks, gsc_impressions) VALUES (?, 5, 5)",
        ("mid",),
    )
    db_conn.commit()
    orphans = _orphan_targets(db_conn)
    keys = [(t, h) for t, h, _c, _i in orphans]
    assert keys == [("collection", "mid"), ("product", "alpha"), ("product", "zeta")]


def test_graph_stats_tie_break_is_type_then_handle(db_conn):
    _link_schema(db_conn)
    # Each pair has degree 1 so the primary sort ties.
    db_conn.executemany(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle) VALUES (?, ?, ?, ?)",
        [
            ("product", "zeta", "page", "about"),
            ("collection", "all", "product", "alpha"),
        ],
    )
    db_conn.commit()
    stats = collect_entity_graph_stats(db_conn)
    keys = [(row["object_type"], row["handle"]) for row in stats]
    assert keys == [
        ("collection", "all"),
        ("page", "about"),
        ("product", "alpha"),
        ("product", "zeta"),
    ]


def test_load_clusters_tie_break_is_id_asc(db_conn):
    db_conn.executescript(
        """
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            content_type TEXT NOT NULL,
            primary_keyword TEXT NOT NULL,
            content_brief TEXT NOT NULL,
            total_volume INTEGER NOT NULL DEFAULT 0,
            avg_difficulty REAL NOT NULL DEFAULT 0.0,
            avg_opportunity REAL NOT NULL DEFAULT 0.0,
            priority_score REAL NOT NULL DEFAULT 0.0,
            match_type TEXT,
            match_handle TEXT,
            match_title TEXT,
            generated_at TEXT NOT NULL
        );
        CREATE TABLE cluster_keywords (
            cluster_id INTEGER NOT NULL,
            keyword TEXT NOT NULL
        );
        CREATE TABLE service_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    db_conn.execute(
        """INSERT INTO clusters
           (id, name, content_type, primary_keyword, content_brief, avg_opportunity, priority_score, generated_at)
           VALUES (2, 'Later', 'blog_post', 'b', 'b', 50.0, 50.0, '2026-01-01T00:00:00Z')"""
    )
    db_conn.execute(
        """INSERT INTO clusters
           (id, name, content_type, primary_keyword, content_brief, avg_opportunity, priority_score, generated_at)
           VALUES (1, 'First', 'blog_post', 'a', 'a', 50.0, 50.0, '2026-01-01T00:00:00Z')"""
    )
    db_conn.commit()
    data = load_clusters(db_conn)
    assert [c["name"] for c in data["clusters"]] == ["First", "Later"]
    assert [c["id"] for c in data["clusters"]] == [1, 2]


def test_cannibalization_tie_break_is_type_then_handle(db_conn):
    conn = _make_conn(db_conn)
    vec = np.ones(EMBEDDING_DIMS, dtype=np.float32)
    vec /= np.linalg.norm(vec)
    _insert_embedding(conn, "product", "zeta", vec)
    _insert_embedding(conn, "product", "alpha", vec)
    _insert_embedding(conn, "page", "about", vec)
    results = find_cannibalization_candidates(conn, threshold=0.5)
    pairs = [
        (
            row["object_a"]["type"],
            row["object_a"]["handle"],
            row["object_b"]["type"],
            row["object_b"]["handle"],
        )
        for row in results
    ]
    assert pairs == [
        ("page", "about", "product", "alpha"),
        ("page", "about", "product", "zeta"),
        ("product", "alpha", "product", "zeta"),
    ]
    assert all(row["content_similarity"] == results[0]["content_similarity"] for row in results)
    for row in results:
        a = (row["object_a"]["type"], row["object_a"]["handle"])
        b = (row["object_b"]["type"], row["object_b"]["handle"])
        assert a < b, (a, b)
