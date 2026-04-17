"""Unit tests for embedding_store — embed, store, retrieve, prune round-trip."""

import sqlite3
import struct
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

from shopifyseo.embedding_store import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    _blob_to_array,
    _embed_to_blob,
    _md5,
    _strip_html,
    _coalesce,
    build_embed_text,
    prune_stale_embeddings,
    sync_embeddings,
    retrieve_related_by_handle,
    find_semantic_keyword_matches,
    find_cannibalization_candidates,
    _dedup_by_handle,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> sqlite3.Connection:
    """In-memory SQLite with all required tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE embeddings (
            object_type TEXT NOT NULL,
            object_handle TEXT NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            text_hash TEXT NOT NULL,
            model_version TEXT NOT NULL,
            embedding BLOB NOT NULL,
            source_text_preview TEXT,
            token_count INTEGER,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (object_type, object_handle, chunk_index)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_type ON embeddings(object_type)")
    conn.execute("""
        CREATE TABLE products (
            shopify_id TEXT, handle TEXT PRIMARY KEY, title TEXT, seo_title TEXT,
            seo_description TEXT, description_html TEXT, tags_json TEXT, status TEXT DEFAULT 'ACTIVE',
            device_type TEXT, battery_size TEXT, nicotine_strength TEXT, puff_count TEXT,
            charging_port TEXT, coil TEXT, size TEXT,
            e_liquid_flavor_labels_json TEXT, vaporizer_style_labels_json TEXT,
            vaping_style_labels_json TEXT, battery_type_labels_json TEXT,
            coil_connection_labels_json TEXT, color_pattern_labels_json TEXT
        )
    """)
    conn.execute("CREATE TABLE product_images (product_shopify_id TEXT, alt_text TEXT)")
    conn.execute("""
        CREATE TABLE collections (
            shopify_id TEXT, handle TEXT PRIMARY KEY, title TEXT,
            seo_title TEXT, seo_description TEXT, description_html TEXT
        )
    """)
    conn.execute("CREATE TABLE collection_products (collection_shopify_id TEXT, product_title TEXT)")
    conn.execute("CREATE TABLE pages (handle TEXT PRIMARY KEY, title TEXT, seo_title TEXT, seo_description TEXT, body TEXT)")
    conn.execute("""
        CREATE TABLE blog_articles (
            handle TEXT, blog_handle TEXT, title TEXT, seo_title TEXT,
            seo_description TEXT, body TEXT, PRIMARY KEY (blog_handle, handle)
        )
    """)
    conn.execute("""
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, primary_keyword TEXT, content_brief TEXT
        )
    """)
    conn.execute("CREATE TABLE cluster_keywords (cluster_id INTEGER, keyword TEXT)")
    conn.execute("""
        CREATE TABLE keyword_metrics (
            keyword TEXT PRIMARY KEY, parent_topic TEXT, intent TEXT,
            content_format_hint TEXT, volume INTEGER, difficulty INTEGER, status TEXT DEFAULT 'new'
        )
    """)
    conn.execute("""
        CREATE TABLE article_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, suggested_title TEXT, brief TEXT,
            primary_keyword TEXT, gap_reason TEXT, supporting_keywords TEXT, status TEXT DEFAULT 'idea'
        )
    """)
    conn.execute("""
        CREATE TABLE competitor_top_pages (
            competitor_domain TEXT, url TEXT, top_keyword TEXT, estimated_traffic INTEGER, page_type TEXT,
            PRIMARY KEY (competitor_domain, url)
        )
    """)
    conn.execute("""
        CREATE TABLE gsc_query_rows (
            object_type TEXT, object_handle TEXT, url TEXT, query TEXT, clicks INTEGER,
            impressions INTEGER, ctr REAL, position REAL, fetched_at TEXT
        )
    """)
    conn.execute("CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    conn.commit()
    return conn


def _insert_embedding(conn, obj_type, handle, vec=None, chunk_index=0):
    """Insert a fake embedding row."""
    if vec is None:
        vec = np.random.randn(EMBEDDING_DIMS).astype(np.float32)
    blob = _embed_to_blob(vec.tolist())
    text = f"test text for {obj_type}/{handle}"
    conn.execute(
        """INSERT INTO embeddings (object_type, object_handle, chunk_index, text_hash, model_version, embedding, source_text_preview, token_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
        (obj_type, handle, chunk_index, _md5(text), EMBEDDING_MODEL, blob, text[:200], len(text) // 4),
    )
    conn.commit()
    return vec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_strip_html(self):
        assert "Hello" in _strip_html("<p>Hello <b>world</b></p>")
        assert "world" in _strip_html("<p>Hello <b>world</b></p>")
        assert _strip_html(None) == ""
        assert _strip_html("") == ""

    def test_coalesce(self):
        assert _coalesce(None) == ""
        assert _coalesce("null") == ""
        assert _coalesce("  ") == ""
        assert _coalesce("hello") == "hello"

    def test_blob_roundtrip(self):
        vec = [1.0, 2.0, 3.0]
        blob = _embed_to_blob(vec)
        arr = _blob_to_array(blob)
        np.testing.assert_allclose(arr, vec, rtol=1e-6)

    def test_md5(self):
        h = _md5("test")
        assert len(h) == 32
        assert h == _md5("test")  # deterministic
        assert h != _md5("other")


class TestBuildEmbedText:
    def test_product(self):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, seo_title, seo_description, description_html, tags_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("1", "prod-a", "Product A", "SEO A", "Desc A", "<p>Body A</p>", '["vape","disposable"]', "ACTIVE"),
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM products WHERE handle = 'prod-a'").fetchone())
        text = build_embed_text("product", row, conn)
        assert isinstance(text, str)
        assert "Product A" in text
        assert "SEO A" in text
        assert "Body A" in text

    def test_page(self):
        conn = _make_conn()
        row = {"title": "Page T", "seo_title": "SEO T", "seo_description": "Desc T", "body": "<p>Page body</p>"}
        text = build_embed_text("page", row)
        assert "Page T" in text
        assert "Page body" in text

    def test_keyword(self):
        row = {"keyword": "vape pen", "parent_topic": "vaping", "intent": "commercial", "content_format_hint": "buying guide"}
        text = build_embed_text("keyword", row)
        assert "vape pen" in text
        assert "commercial" in text

    def test_competitor_page_root_url_still_embeddable(self):
        """Research rows with only a bare domain URL used to produce '' and skip embedding."""
        row = {
            "competitor_domain": "example.com",
            "url": "https://example.com",
            "top_keyword": "",
            "page_type": "",
        }
        text = build_embed_text("competitor_page", row)
        assert text.strip()
        assert "example.com" in text


class TestDedupByHandle:
    def test_keeps_highest_score(self):
        items = [
            {"object_type": "product", "object_handle": "a", "score": 0.9},
            {"object_type": "product", "object_handle": "a", "score": 0.8},
            {"object_type": "product", "object_handle": "b", "score": 0.7},
        ]
        result = _dedup_by_handle(items)
        assert len(result) == 2
        assert result[0]["object_handle"] == "a"
        assert result[0]["score"] == 0.9


class TestPrune:
    def test_prunes_orphan_products(self):
        conn = _make_conn()
        conn.execute("INSERT INTO products (handle, title, status) VALUES ('exists', 'P', 'ACTIVE')")
        _insert_embedding(conn, "product", "exists")
        _insert_embedding(conn, "product", "gone")
        conn.commit()
        count = prune_stale_embeddings(conn, "product")
        assert count == 1
        remaining = conn.execute("SELECT object_handle FROM embeddings WHERE object_type = 'product'").fetchall()
        handles = [r["object_handle"] for r in remaining]
        assert "exists" in handles
        assert "gone" not in handles


class TestRetrieveRelatedByHandle:
    def test_returns_similar(self):
        conn = _make_conn()
        base_vec = np.random.randn(EMBEDDING_DIMS).astype(np.float32)
        base_vec /= np.linalg.norm(base_vec)
        _insert_embedding(conn, "product", "query-prod", base_vec)

        similar_vec = base_vec + np.random.randn(EMBEDDING_DIMS).astype(np.float32) * 0.01
        _insert_embedding(conn, "product", "similar-prod", similar_vec)

        dissimilar_vec = np.random.randn(EMBEDDING_DIMS).astype(np.float32)
        _insert_embedding(conn, "product", "different-prod", dissimilar_vec)

        results = retrieve_related_by_handle(conn, "product", "query-prod", top_k=2)
        assert len(results) >= 1
        assert results[0]["object_handle"] == "similar-prod"

    def test_returns_empty_when_no_embedding(self):
        conn = _make_conn()
        results = retrieve_related_by_handle(conn, "product", "nonexistent", top_k=5)
        assert results == []


class TestFindSemanticKeywordMatches:
    def test_finds_keywords(self):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO keyword_metrics (keyword, parent_topic, intent, volume, difficulty, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("disposable vape", "vaping", "commercial", 5000, 30, "approved"),
        )
        conn.commit()

        base_vec = np.random.randn(EMBEDDING_DIMS).astype(np.float32)
        _insert_embedding(conn, "product", "test-product", base_vec)

        similar_kw_vec = base_vec + np.random.randn(EMBEDDING_DIMS).astype(np.float32) * 0.01
        _insert_embedding(conn, "keyword", "disposable vape", similar_kw_vec)

        results = find_semantic_keyword_matches(conn, "product", "test-product", top_k=5)
        assert len(results) == 1
        assert results[0]["keyword"] == "disposable vape"
        assert results[0]["volume"] == 5000


class TestSyncEmbeddings:
    @patch("shopifyseo.embedding_store.embed_batch")
    def test_sync_products_calls_api(self, mock_embed):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, seo_title, seo_description, description_html, tags_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("1", "p1", "Product 1", "SEO 1", "Desc 1", "<p>Body</p>", "[]", "ACTIVE"),
        )
        conn.execute("INSERT INTO service_settings (key, value) VALUES ('gemini_api_key', 'test-key')")
        conn.commit()

        mock_embed.return_value = [list(np.random.randn(EMBEDDING_DIMS).astype(float))]

        result = sync_embeddings(conn, object_type="product")
        assert result["embedded"] == 1
        assert result["skipped"] == 0
        mock_embed.assert_called_once()

    @patch("shopifyseo.embedding_store.embed_batch")
    def test_sync_skips_unchanged(self, mock_embed):
        conn = _make_conn()
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, seo_title, seo_description, description_html, tags_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("1", "p1", "Product 1", "SEO 1", "Desc 1", "<p>Body</p>", "[]", "ACTIVE"),
        )
        conn.execute("INSERT INTO service_settings (key, value) VALUES ('gemini_api_key', 'test-key')")
        conn.commit()

        fake_vec = list(np.random.randn(EMBEDDING_DIMS).astype(float))
        mock_embed.return_value = [fake_vec]

        sync_embeddings(conn, object_type="product")
        mock_embed.reset_mock()

        result = sync_embeddings(conn, object_type="product")
        assert result["skipped"] == 1
        assert result["embedded"] == 0
        mock_embed.assert_not_called()

    def test_sync_no_api_key(self):
        conn = _make_conn()
        result = sync_embeddings(conn, object_type="product")
        assert result.get("reason") == "no_api_key"
        assert result["embedded"] == 0


class TestCannibalization:
    def test_finds_similar_pages(self):
        conn = _make_conn()
        base_vec = np.random.randn(EMBEDDING_DIMS).astype(np.float32)
        base_vec /= np.linalg.norm(base_vec)

        nearly_same = base_vec + np.random.randn(EMBEDDING_DIMS).astype(np.float32) * 0.001
        _insert_embedding(conn, "product", "prod-a", base_vec)
        _insert_embedding(conn, "product", "prod-b", nearly_same)

        results = find_cannibalization_candidates(conn, threshold=0.5)
        assert len(results) >= 1
        assert results[0]["content_similarity"] > 0.5

    def test_no_candidates_below_threshold(self):
        conn = _make_conn()
        _insert_embedding(conn, "product", "prod-a", np.random.randn(EMBEDDING_DIMS).astype(np.float32))
        _insert_embedding(conn, "product", "prod-b", np.random.randn(EMBEDDING_DIMS).astype(np.float32))
        results = find_cannibalization_candidates(conn, threshold=0.99)
        assert results == []
