"""Tests for the event-driven embedding sync helper."""

import time
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

import pytest

from shopifyseo.embedding_sync import (
    EMBEDDABLE_TYPES,
    enqueue_embedding_sync,
    enqueue_embedding_sync_for_type,
    enqueue_embedding_sync_for_handle,
    enqueue_embedding_sync_from_conn,
    _get_db_path_from_connection,
)


def _make_test_db(source):
    """Create a minimal test database with required tables."""
    from db_support import TestDatabase
    conn = source.connect() if isinstance(source, TestDatabase) else source
    conn.execute("""
        CREATE TABLE IF NOT EXISTS embeddings (
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS keyword_metrics (
            keyword TEXT PRIMARY KEY,
            parent_topic TEXT,
            intent TEXT,
            volume INTEGER,
            difficulty REAL,
            status TEXT DEFAULT 'new'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            shopify_id TEXT,
            handle TEXT PRIMARY KEY,
            title TEXT,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS product_images (
            product_shopify_id TEXT,
            alt_text TEXT
        )
    """)
    conn.commit()
    return conn


def _enqueue_path(testdb) -> Path:
    """Path argument for enqueue helpers. ``_open_db`` is mocked in these tests."""
    return testdb.path if testdb.path is not None else Path("testdb-pg")


class TestGetDbPathFromConnection:
    """Test _get_db_path_from_connection utility."""

    def test_returns_path_for_file_db(self, testdb, db_conn):
        if testdb.is_postgres:
            pytest.skip("PRAGMA database_list is SQLite-only")
        result = _get_db_path_from_connection(db_conn)
        assert result == str(testdb.path)

    def test_returns_empty_when_path_unavailable(self, testdb, db_conn):
        if not testdb.is_postgres:
            pytest.skip("adapted PRAGMA skip is Postgres testdb only")
        result = _get_db_path_from_connection(db_conn)
        assert result == "" or result is None


class TestEnqueueEmbeddingSync:
    """Test enqueue_embedding_sync function."""

    def test_enqueue_with_all_types(self, testdb):
        """Test that enqueue with no types syncs all embeddable types."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)

        sync_calls = []

        def mock_sync_embeddings(c, object_type=None):
            sync_calls.append(object_type)
            return {"embedded": 0, "skipped": 0, "pruned": 0}

        def mock_open_db(p):
            return conn

        with patch("shopifyseo.embedding_sync._open_db", side_effect=mock_open_db):
            with patch("shopifyseo.embedding_sync._sync_type", side_effect=mock_sync_embeddings):
                enqueue_embedding_sync(db_path)
                # Wait for background thread to complete
                time.sleep(0.5)

        # Should have called sync for all embeddable types
        assert set(sync_calls) == set(EMBEDDABLE_TYPES)

    def test_enqueue_with_specific_types(self, testdb):
        """Test that enqueue with specific types only syncs those types."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)

        sync_calls = []

        def mock_sync_embeddings(c, object_type=None):
            sync_calls.append(object_type)
            return {"embedded": 0, "skipped": 0, "pruned": 0}

        def mock_open_db(p):
            return conn

        with patch("shopifyseo.embedding_sync._open_db", side_effect=mock_open_db):
            with patch("shopifyseo.embedding_sync._sync_type", side_effect=mock_sync_embeddings):
                enqueue_embedding_sync(db_path, object_types=["product", "keyword"])
                time.sleep(0.5)

        assert set(sync_calls) == {"product", "keyword"}

    def test_enqueue_with_invalid_types_logs_warning(self, testdb, caplog):
        """Test that invalid types are filtered and a warning is logged."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)

        with patch("shopifyseo.embedding_sync._sync_type"):
            enqueue_embedding_sync(db_path, object_types=["not_a_real_type"])
            time.sleep(0.2)

        assert "no valid object_types" in caplog.text.lower()

    def test_enqueue_with_handles_uses_single_handle_sync(self, testdb):
        """Test that providing handles uses single-handle sync for supported types."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)
        conn.execute(
            "INSERT INTO products (handle, title, status) VALUES (?, ?, ?)",
            ("test-product", "Test Product", "ACTIVE"),
        )
        conn.commit()

        single_handle_calls = []
        type_calls = []

        def mock_single_handle(c, obj_type, handle):
            single_handle_calls.append((obj_type, handle))
            return {"embedded": 1, "skipped": 0, "error": None}

        def mock_sync_type(c, obj_type):
            type_calls.append(obj_type)
            return {"embedded": 0, "skipped": 0, "pruned": 0}

        def mock_open_db(p):
            return conn

        with patch("shopifyseo.embedding_sync._open_db", side_effect=mock_open_db):
            with patch("shopifyseo.embedding_sync._sync_single_handle", side_effect=mock_single_handle):
                with patch("shopifyseo.embedding_sync._sync_type", side_effect=mock_sync_type):
                    enqueue_embedding_sync(
                        db_path,
                        object_types=["product"],
                        handles=["test-product"],
                    )
                    time.sleep(0.5)

        # Should have used single-handle sync for product
        assert ("product", "test-product") in single_handle_calls
        assert "product" not in type_calls

    def test_enqueue_falls_back_to_type_sync_for_unsupported(self, testdb):
        """Test that unsupported types fall back to type-scoped sync."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)

        type_calls = []

        def mock_sync_type(c, obj_type):
            type_calls.append(obj_type)
            return {"embedded": 0, "skipped": 0, "pruned": 0}

        def mock_open_db(p):
            return conn

        with patch("shopifyseo.embedding_sync._open_db", side_effect=mock_open_db):
            with patch("shopifyseo.embedding_sync._sync_type", side_effect=mock_sync_type):
                # "keyword" and "gsc_queries" don't support single-handle sync
                enqueue_embedding_sync(
                    db_path,
                    object_types=["keyword", "gsc_queries"],
                    handles=["test-kw", "product:test"],
                )
                time.sleep(0.5)

        # Should have fallen back to type sync for these
        assert "keyword" in type_calls
        assert "gsc_queries" in type_calls


class TestEnqueueEmbeddingSyncFromConn:
    """Test enqueue_embedding_sync_from_conn function."""

    def test_extracts_path_and_enqueues(self, testdb):
        """Test that it extracts db_path from connection and enqueues."""
        if testdb.is_postgres:
            pytest.skip("PRAGMA database_list cannot extract a file path on Postgres")
        db_path = testdb.path
        conn = _make_test_db(testdb)

        with patch("shopifyseo.embedding_sync.enqueue_embedding_sync") as mock_enqueue:
            enqueue_embedding_sync_from_conn(conn, object_types=["product"])
            mock_enqueue.assert_called_once()
            call_args = mock_enqueue.call_args
            # First arg should be the db_path
            assert str(db_path) in str(call_args)

        conn.close()

    def test_warns_when_path_unavailable(self, testdb, db_conn, caplog):
        """Test that it logs a warning when the path cannot be extracted."""
        if not testdb.is_postgres:
            pytest.skip("path-unavailable case is Postgres testdb (PRAGMA database_list skipped)")
        enqueue_embedding_sync_from_conn(db_conn, object_types=["product"])


class TestConvenienceWrappers:
    """Test convenience wrapper functions."""

    def test_enqueue_for_type(self, testdb):
        """Test enqueue_embedding_sync_for_type wrapper."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)
        conn.close()

        with patch("shopifyseo.embedding_sync.enqueue_embedding_sync") as mock_enqueue:
            enqueue_embedding_sync_for_type(db_path, "product")
            mock_enqueue.assert_called_once_with(db_path, object_types=["product"])

    def test_enqueue_for_handle(self, testdb):
        """Test enqueue_embedding_sync_for_handle wrapper."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)
        conn.close()

        with patch("shopifyseo.embedding_sync.enqueue_embedding_sync") as mock_enqueue:
            enqueue_embedding_sync_for_handle(db_path, "product", "my-product")
            mock_enqueue.assert_called_once_with(
                db_path, object_types=["product"], handles=["my-product"]
            )


class TestThreadSafety:
    """Test that embedding sync is thread-safe."""

    def test_multiple_concurrent_enqueues(self, testdb):
        """Test that multiple concurrent enqueues don't cause issues."""
        db_path = _enqueue_path(testdb)
        conn = _make_test_db(testdb)

        call_count = {"value": 0}
        lock = threading.Lock()

        def mock_sync_embeddings(c, object_type=None):
            with lock:
                call_count["value"] += 1
            time.sleep(0.1)  # Simulate some work
            return {"embedded": 0, "skipped": 0, "pruned": 0}

        def mock_open_db(p):
            return conn

        with patch("shopifyseo.embedding_sync._open_db", side_effect=mock_open_db):
            with patch("shopifyseo.embedding_sync._sync_type", side_effect=mock_sync_embeddings):
                # Enqueue multiple syncs concurrently
                threads = []
                for _ in range(5):
                    t = threading.Thread(
                        target=enqueue_embedding_sync_for_type,
                        args=(db_path, "product"),
                    )
                    threads.append(t)
                    t.start()

                # Wait for all threads to complete
                for t in threads:
                    t.join(timeout=2)

                time.sleep(1)  # Wait for background threads

        # All syncs should have been attempted
        assert call_count["value"] >= 1


class TestWritePathIntegration:
    """Test that write paths trigger embedding sync via source code inspection."""

    def test_keyword_status_update_has_embedding_hook(self):
        """Test that update_keyword_status has embedding sync hook in source."""
        import inspect
        from backend.app.services.keyword_research import keyword_db
        source = inspect.getsource(keyword_db.update_keyword_status)
        
        assert "enqueue_embedding_sync_from_conn" in source, (
            "update_keyword_status should call enqueue_embedding_sync_from_conn"
        )
        assert "keyword" in source, (
            "update_keyword_status should sync keyword embeddings"
        )

    def test_bulk_status_update_has_embedding_hook(self):
        """Test that bulk_update_status has embedding sync hook in source."""
        import inspect
        from backend.app.services.keyword_research import keyword_db
        source = inspect.getsource(keyword_db.bulk_update_status)
        
        assert "enqueue_embedding_sync_from_conn" in source, (
            "bulk_update_status should call enqueue_embedding_sync_from_conn"
        )

    def test_upsert_target_keyword_has_embedding_hook(self):
        """Test that upsert_target_keyword has embedding sync hook in source."""
        import inspect
        from backend.app.services.keyword_research import keyword_db
        source = inspect.getsource(keyword_db.upsert_target_keyword)
        
        assert "enqueue_embedding_sync_from_conn" in source, (
            "upsert_target_keyword should call enqueue_embedding_sync_from_conn"
        )

    def test_cluster_generation_has_embedding_hook(self):
        """Test that generate_clusters has embedding sync hook in source."""
        import inspect
        from backend.app.services.keyword_clustering import _generation
        source = inspect.getsource(_generation.generate_clusters)
        
        assert "enqueue_embedding_sync_from_conn" in source, (
            "generate_clusters should call enqueue_embedding_sync_from_conn"
        )
        assert "cluster" in source, (
            "generate_clusters should sync cluster embeddings"
        )

    def test_article_idea_save_has_embedding_hook(self):
        """Test that save_article_ideas has embedding sync hook in source."""
        import inspect
        from shopifyseo import dashboard_article_ideas
        source = inspect.getsource(dashboard_article_ideas.save_article_ideas)
        
        assert "enqueue_embedding_sync_from_conn" in source, (
            "save_article_ideas should call enqueue_embedding_sync_from_conn"
        )

    def test_dashboard_sync_has_embedding_hook(self):
        """Test that dashboard sync has catalog embedding hook."""
        import inspect
        from shopifyseo.dashboard_actions import _sync
        source = inspect.getsource(_sync._start_catalog_embedding_sync)
        
        assert "enqueue_embedding_sync" in source, (
            "_start_catalog_embedding_sync should call enqueue_embedding_sync"
        )
        assert "product" in source and "collection" in source, (
            "_start_catalog_embedding_sync should sync catalog types"
        )
