"""Tests for article draft retrieval query + hybrid RAG merge (plan phases 0–2)."""

import sqlite3

from shopifyseo.article_draft_retrieval import (
    build_article_draft_retrieval_query,
    merge_embedding_rag_with_token_overlap,
)


def test_build_article_draft_retrieval_query_includes_keywords_and_cluster():
    conn = sqlite3.connect(":memory:")
    conn.execute(
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
            generated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "INSERT INTO clusters VALUES (1, 'Pod kits', 'guide', 'caliburn', 'Refillable pods overview', 0, 0, 0, '')",
    )
    q = build_article_draft_retrieval_query(
        topic="UWELL beginner guide",
        keywords=["caliburn", "refillable pods", {"keyword": "vape pods canada"}],
        linked_cluster_id=1,
        conn=conn,
    )
    lowered = q.lower()
    assert "uwell" in lowered
    assert "caliburn" in lowered
    assert "refillable" in lowered
    assert "pod kits" in lowered or "pods overview" in lowered


def test_merge_embedding_rag_boosts_on_topic_product_over_higher_cosine_noise():
    """Embedding-only leader with no token overlap loses to weaker cosine + strong overlap."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE products (
          shopify_id TEXT PRIMARY KEY,
          title TEXT NOT NULL,
          handle TEXT NOT NULL UNIQUE,
          vendor TEXT,
          product_type TEXT,
          status TEXT,
          tags_json TEXT NOT NULL,
          seo_title TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO products VALUES ('1','ABT Mega Disposable','abt-mega','ABT','Disposable','ACTIVE','[]','')",
    )
    conn.execute(
        "INSERT INTO products VALUES ('2','UWELL Caliburn Pod Kit','uwell-cal','UWELL','Pods','ACTIVE','[]','')",
    )
    conn.execute("CREATE TABLE collections (shopify_id TEXT, title TEXT, handle TEXT UNIQUE, seo_title TEXT, description_html TEXT)")
    conn.execute("CREATE TABLE blog_articles (blog_handle TEXT, handle TEXT, title TEXT, seo_title TEXT, seo_description TEXT, summary TEXT, body TEXT, tags_json TEXT)")

    retrieval_query = "uwell caliburn refillable pod systems canada"
    embedding_rows = [
        {
            "object_type": "product",
            "object_handle": "abt-mega",
            "chunk_index": 0,
            "source_text_preview": "ABT Mega Disposable Vape",
            "score": 0.88,
        },
        {
            "object_type": "product",
            "object_handle": "uwell-cal",
            "chunk_index": 0,
            "source_text_preview": "UWELL Caliburn Pod Kit",
            "score": 0.52,
        },
    ]
    merged = merge_embedding_rag_with_token_overlap(
        conn, retrieval_query, embedding_rows, out_k=2
    )
    handles = [m["object_handle"] for m in merged]
    assert handles[0] == "uwell-cal", f"expected on-topic product first, got {handles}"


def test_merge_embedding_falls_back_when_query_has_no_tokens():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE products (shopify_id TEXT, title TEXT, handle TEXT UNIQUE, vendor TEXT, product_type TEXT, status TEXT, tags_json TEXT, seo_title TEXT)"
    )
    conn.execute("CREATE TABLE collections (shopify_id TEXT, title TEXT, handle TEXT, seo_title TEXT, description_html TEXT)")
    conn.execute("CREATE TABLE blog_articles (blog_handle TEXT, handle TEXT, title TEXT, seo_title TEXT, seo_description TEXT, summary TEXT, body TEXT, tags_json TEXT)")
    rows = [
        {"object_type": "product", "object_handle": "a", "chunk_index": 0, "source_text_preview": "x", "score": 0.7},
        {"object_type": "product", "object_handle": "b", "chunk_index": 0, "source_text_preview": "y", "score": 0.6},
    ]
    out = merge_embedding_rag_with_token_overlap(conn, "!!!", rows, out_k=2)
    assert [r["object_handle"] for r in out] == ["a", "b"]
