"""Tests for POST /api/internal-links/suggestions/{id}/restore endpoint.

Requirements:
- B3: Atomic conditional UPDATE, audit after successful update
- B4: X-Task-Token authentication, 401 without/with bad token
- B5: Reason validation: strip, 1-500 chars, extra='forbid'
- B6: Missing id -> 404
- B7: Unfinished snapshot -> 409
- B8: Restored rows survive rebuild only if source/target are still valid
"""
import json
import os
import pytest
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

from shopifyseo.internal_links.apply import restore_suggestion, SuggestionNotFound
from shopifyseo.internal_links.safety import LinkConflict
from shopifyseo.internal_links.store import ensure_schema

BASE = "https://example.myshopify.com"


def _init_db():
    """Initialize an in-memory database with required schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
            anchor_phrase TEXT,
            ai_anchor_html TEXT,
            ai_edit_json TEXT,
            source_body_hash TEXT,
            score REAL NOT NULL DEFAULT 0,
            weak_anchor INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('suggested', 'applied', 'dismissed', 'undone')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE INDEX idx_link_suggestions_status ON link_suggestions (status, score);
        
        CREATE TABLE link_body_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            shopify_id TEXT NOT NULL,
            old_body TEXT NOT NULL,
            new_body TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE UNIQUE INDEX idx_link_body_active_write
            ON link_body_snapshots(source_type, shopify_id)
            WHERE status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation');
        
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            shopify_id TEXT,
            handle TEXT NOT NULL,
            title TEXT,
            description_html TEXT,
            status TEXT DEFAULT 'ACTIVE',
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0,
            online_store_url TEXT
        );
        
        CREATE TABLE collections (
            id INTEGER PRIMARY KEY,
            shopify_id TEXT,
            handle TEXT NOT NULL,
            title TEXT,
            description_html TEXT,
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0,
            api_unreachable INTEGER DEFAULT 0
        );
        
        CREATE TABLE blog_articles (
            id INTEGER PRIMARY KEY,
            shopify_id TEXT,
            blog_handle TEXT NOT NULL,
            handle TEXT NOT NULL,
            title TEXT,
            body TEXT,
            is_published INTEGER DEFAULT 1,
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0
        );
        
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            anchor_text TEXT,
            href TEXT
        );
        
        CREATE TABLE service_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        
        INSERT INTO service_settings (key, value) VALUES ('internal_link_ai_woven_enabled_types', 'blog_article,product');
    """)
    ensure_schema(conn)
    return conn


@pytest.fixture
def database():
    """Create a test database with a dismissed suggestion."""
    conn = _init_db()
    
    # Insert sample product (target)
    conn.execute("""
        INSERT INTO products (shopify_id, handle, title, description_html, status, online_store_url)
        VALUES ('gid://shopify/Product/1', 'target-product', 'Target Product', '<p>Product body</p>', 'ACTIVE', 'https://example.myshopify.com/products/target-product')
    """)
    
    # Insert sample article (source)
    conn.execute("""
        INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, is_published)
        VALUES ('gid://shopify/Article/1', 'news', 'sample-article', 'Sample Article', '<p>Sample body with target product text.</p>', 1)
    """)
    
    # Insert a dismissed suggestion
    conn.execute("""
        INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
        VALUES (1, 'blog_article', 'news/sample-article', 'product', 'target-product', 'phrase_wrap', 'Target Product', 0.8, 'dismissed', 1700000000)
    """)
    
    # Insert a suggested (not dismissed) suggestion
    conn.execute("""
        INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
        VALUES (2, 'blog_article', 'news/sample-article', 'product', 'other-product', 'phrase_wrap', 'Other Product', 0.7, 'suggested', 1700000000)
    """)
    
    # Insert an applied suggestion
    conn.execute("""
        INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
        VALUES (3, 'blog_article', 'news/sample-article', 'product', 'third-product', 'phrase_wrap', 'Third Product', 0.6, 'applied', 1700000000)
    """)
    
    conn.commit()
    yield conn
    conn.close()


class TestRestoreSuggestion:
    """Tests for restore_suggestion function."""
    
    def test_restore_dismissed_suggestion(self, database):
        """Dismissed suggestion can be restored."""
        result = restore_suggestion(database, 1, "salar", "Customer requested this link")
        
        assert result["status"] == "suggested"
        assert result["suggestion_id"] == 1
        assert result["actor"] == "salar"
        
        # Verify database state
        row = database.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "suggested"
        
        # Verify audit record
        audit = database.execute("SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        assert audit["actor"] == "salar"
        assert audit["reason"] == "Customer requested this link"
    
    def test_restore_not_found_raises_suggestion_not_found(self, database):
        """B6: Non-existent suggestion raises SuggestionNotFound (-> 404)."""
        with pytest.raises(SuggestionNotFound) as exc_info:
            restore_suggestion(database, 999, "salar", "reason")
        
        assert "not found" in str(exc_info.value).lower()
    
    def test_restore_suggested_fails(self, database):
        """Already suggested suggestions cannot be restored."""
        with pytest.raises(LinkConflict) as exc_info:
            restore_suggestion(database, 2, "salar", "reason")
        
        assert "dismissed" in str(exc_info.value).lower()
        assert "suggested" in str(exc_info.value).lower()
    
    def test_restore_applied_fails(self, database):
        """Applied suggestions cannot be restored."""
        with pytest.raises(LinkConflict) as exc_info:
            restore_suggestion(database, 3, "salar", "reason")
        
        assert "dismissed" in str(exc_info.value).lower()
        assert "applied" in str(exc_info.value).lower()


class TestRestoreAtomicity:
    """B3: Atomic conditional UPDATE tests."""
    
    def test_concurrent_status_change_leaves_row_unchanged(self, database):
        """B3: Concurrent status change leaves row in its new state, no audit row."""
        # Simulate row becoming 'applied' before restore runs
        database.execute("UPDATE link_suggestions SET status = 'applied', applied_at = 123 WHERE id = 1")
        database.commit()
        
        with pytest.raises(LinkConflict) as exc_info:
            restore_suggestion(database, 1, "salar", "reason")
        
        assert "applied" in str(exc_info.value)
        
        # Row should still be 'applied' with applied_at intact
        row = database.execute("SELECT status, applied_at FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "applied"
        assert row["applied_at"] == 123
        
        # No audit row should exist
        audit = database.execute("SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        assert audit is None
    
    def test_audit_only_written_after_successful_update(self, database):
        """B3: Audit row is only written after successful UPDATE."""
        # Successful restore
        restore_suggestion(database, 1, "salar", "reason")
        
        # Audit exists
        audit = database.execute("SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        assert audit is not None
        
        # Status is 'suggested'
        row = database.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "suggested"


class TestUnfinishedSnapshot:
    """B7: Unfinished snapshot blocks restore."""
    
    def test_restore_with_needs_reconciliation_fails(self, database):
        """B7: Dismissed row with needs_reconciliation snapshot -> 409."""
        # Add a needs_reconciliation snapshot
        database.execute("""
            INSERT INTO link_body_snapshots 
            (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at)
            VALUES (1, 'blog_article', 'news/sample-article', 'gid://shopify/Article/1', '<p>old</p>', '<p>new</p>', 'needs_reconciliation', 1700000000, 1700000000)
        """)
        database.commit()
        
        with pytest.raises(LinkConflict) as exc_info:
            restore_suggestion(database, 1, "salar", "reason")
        
        assert "pending" in str(exc_info.value).lower() or "unfinished" in str(exc_info.value).lower()
        
        # Status should still be 'dismissed'
        row = database.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "dismissed"
        
        # No audit row
        audit = database.execute("SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        assert audit is None
    
    def test_restore_with_prepared_snapshot_fails(self, database):
        """B7: Dismissed row with prepared snapshot -> 409."""
        database.execute("""
            INSERT INTO link_body_snapshots 
            (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at)
            VALUES (1, 'blog_article', 'news/sample-article', 'gid://shopify/Article/1', '<p>old</p>', '<p>new</p>', 'prepared', 1700000000, 1700000000)
        """)
        database.commit()
        
        with pytest.raises(LinkConflict):
            restore_suggestion(database, 1, "salar", "reason")


class TestRestoreAuditLog:
    """Tests for restore audit logging."""
    
    def test_audit_record_created(self, database):
        """Restore creates an audit record."""
        restore_suggestion(database, 1, "jimmy", "SEO review")
        
        audits = database.execute("SELECT * FROM link_suggestion_restore_audit").fetchall()
        assert len(audits) == 1
        assert audits[0]["suggestion_id"] == 1
        assert audits[0]["actor"] == "jimmy"
        assert audits[0]["reason"] == "SEO review"
    
    def test_multiple_restores_logged(self, database):
        """Multiple restore operations create multiple audit records."""
        # First restore
        restore_suggestion(database, 1, "salar", "first restore")
        
        # Dismiss again
        database.execute("UPDATE link_suggestions SET status = 'dismissed' WHERE id = 1")
        database.commit()
        
        # Second restore
        restore_suggestion(database, 1, "jimmy", "second restore")
        
        audits = database.execute(
            "SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1 ORDER BY id"
        ).fetchall()
        assert len(audits) == 2
        assert audits[0]["actor"] == "salar"
        assert audits[1]["actor"] == "jimmy"


class TestRebuildWithRealPipeline:
    """B8: Restored suggestions survive rebuild through real pipeline."""
    
    @pytest.fixture
    def pipeline_db(self, tmp_path):
        """Create a file-based database for pipeline tests."""
        db_path = tmp_path / "pipeline_test.sqlite"
        conn = sqlite3.connect(str(db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        
        # Create full schema
        conn.executescript("""
            CREATE TABLE link_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_handle TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_handle TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
                anchor_phrase TEXT,
                ai_anchor_html TEXT,
                ai_edit_json TEXT,
                source_body_hash TEXT,
                score REAL NOT NULL DEFAULT 0,
                weak_anchor INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'suggested',
                created_at INTEGER NOT NULL,
                applied_at INTEGER,
                UNIQUE (source_type, source_handle, target_type, target_handle)
            );
            CREATE INDEX idx_link_suggestions_status ON link_suggestions (status, score);
            
            CREATE TABLE link_body_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_handle TEXT NOT NULL,
                shopify_id TEXT NOT NULL,
                old_body TEXT NOT NULL,
                new_body TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                shopify_id TEXT,
                handle TEXT NOT NULL UNIQUE,
                title TEXT,
                description_html TEXT,
                status TEXT DEFAULT 'ACTIVE',
                gsc_clicks INTEGER DEFAULT 0,
                gsc_impressions INTEGER DEFAULT 0,
                online_store_url TEXT
            );
            
            CREATE TABLE collections (
                id INTEGER PRIMARY KEY,
                shopify_id TEXT,
                handle TEXT NOT NULL UNIQUE,
                title TEXT,
                description_html TEXT,
                gsc_clicks INTEGER DEFAULT 0,
                gsc_impressions INTEGER DEFAULT 0,
                api_unreachable INTEGER DEFAULT 0
            );
            
            CREATE TABLE blog_articles (
                id INTEGER PRIMARY KEY,
                shopify_id TEXT,
                blog_handle TEXT NOT NULL,
                handle TEXT NOT NULL,
                title TEXT,
                body TEXT,
                is_published INTEGER DEFAULT 1,
                gsc_clicks INTEGER DEFAULT 0,
                gsc_impressions INTEGER DEFAULT 0
            );
            
            CREATE TABLE pages (
                id INTEGER PRIMARY KEY,
                shopify_id TEXT,
                handle TEXT NOT NULL UNIQUE,
                title TEXT,
                body TEXT,
                gsc_clicks INTEGER DEFAULT 0,
                gsc_impressions INTEGER DEFAULT 0,
                is_published INTEGER DEFAULT 1
            );
            
            CREATE TABLE internal_links (
                id INTEGER PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_handle TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_handle TEXT NOT NULL,
                anchor_text TEXT,
                href TEXT
            );
            
            CREATE TABLE service_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            
            INSERT INTO service_settings (key, value) VALUES ('internal_link_ai_woven_enabled_types', 'blog_article,product');
            INSERT INTO service_settings (key, value) VALUES ('internal_link_sim_threshold', '0.1');
        """)
        ensure_schema(conn)
        
        # Insert source article
        conn.execute("""
            INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, is_published, gsc_clicks)
            VALUES ('gid://shopify/Article/1', 'news', 'sample-article', 'Sample Article', '<p>Sample body with target product.</p>', 1, 100)
        """)
        
        # Insert target product (valid)
        conn.execute("""
            INSERT INTO products (shopify_id, handle, title, description_html, status, online_store_url, gsc_clicks)
            VALUES ('gid://shopify/Product/1', 'valid-target', 'Valid Target', '<p>Product body</p>', 'ACTIVE', 'https://example.com/products/valid-target', 50)
        """)
        
        # Insert target product (will be deleted)
        conn.execute("""
            INSERT INTO products (shopify_id, handle, title, description_html, status, online_store_url, gsc_clicks)
            VALUES ('gid://shopify/Product/2', 'invalid-target', 'Invalid Target', '<p>Product body</p>', 'ACTIVE', 'https://example.com/products/invalid-target', 50)
        """)
        
        conn.commit()
        yield conn, db_path
        conn.close()
    
    def test_restored_valid_pair_survives_rebuild(self, pipeline_db):
        """B8: Restored suggestion with valid source/target survives rebuild."""
        conn, db_path = pipeline_db
        
        # Insert dismissed suggestion
        conn.execute("""
            INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
            VALUES (1, 'blog_article', 'news/sample-article', 'product', 'valid-target', 'phrase_wrap', 'Valid Target', 0.8, 'dismissed', 1700000000)
        """)
        conn.commit()
        
        # Restore the suggestion
        restore_suggestion(conn, 1, "salar", "preserve this link")
        
        # Verify it's now suggested
        row = conn.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "suggested"
        
        # Run the real pipeline (with mocked related_fn to avoid embedding lookup)
        from shopifyseo.internal_links.pipeline import generate_link_suggestions
        
        def mock_related(conn, object_type, handle, top_k=10):
            return []  # No new suggestions
        
        generate_link_suggestions(conn, related_fn=mock_related, rebuild_graph=False)
        
        # The restored suggestion should still exist
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is not None
        assert row["status"] == "suggested"
    
    def test_restored_invalid_pair_deleted_on_rebuild(self, pipeline_db):
        """B8: Restored suggestion with deleted target is removed on rebuild."""
        conn, db_path = pipeline_db
        
        # Insert dismissed suggestion pointing to invalid-target
        conn.execute("""
            INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
            VALUES (1, 'blog_article', 'news/sample-article', 'product', 'invalid-target', 'phrase_wrap', 'Invalid Target', 0.8, 'dismissed', 1700000000)
        """)
        conn.commit()
        
        # Restore the suggestion
        restore_suggestion(conn, 1, "salar", "preserve this link")
        
        # Delete the target product (make it invalid)
        conn.execute("DELETE FROM products WHERE handle = 'invalid-target'")
        conn.commit()
        
        # Run the real pipeline
        from shopifyseo.internal_links.pipeline import generate_link_suggestions
        
        def mock_related(conn, object_type, handle, top_k=10):
            return []
        
        generate_link_suggestions(conn, related_fn=mock_related, rebuild_graph=False)
        
        # The restored suggestion should be deleted (target no longer valid)
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is None
    
    def test_delete_restored_row_succeeds_with_foreign_keys_on(self, pipeline_db):
        """B8: Delete of a restored row succeeds (no FK constraint)."""
        conn, db_path = pipeline_db
        
        # Enable foreign keys
        conn.execute("PRAGMA foreign_keys = ON")
        
        # Insert and restore a suggestion
        conn.execute("""
            INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
            VALUES (1, 'blog_article', 'news/sample-article', 'product', 'valid-target', 'phrase_wrap', 'Valid Target', 0.8, 'dismissed', 1700000000)
        """)
        conn.commit()
        
        restore_suggestion(conn, 1, "salar", "test")
        
        # Delete should succeed (no FK from audit table)
        conn.execute("DELETE FROM link_suggestions WHERE id = 1")
        conn.commit()
        
        # Audit entry still exists (append-only)
        audit = conn.execute("SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        assert audit is not None


def _make_api_database(path):
    """Create a test database at a file path for API testing."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
            anchor_phrase TEXT,
            ai_anchor_html TEXT,
            ai_edit_json TEXT,
            source_body_hash TEXT,
            score REAL NOT NULL DEFAULT 0,
            weak_anchor INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('suggested', 'applied', 'dismissed', 'undone')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE INDEX idx_link_suggestions_status ON link_suggestions (status, score);
        
        CREATE TABLE link_body_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            shopify_id TEXT NOT NULL,
            old_body TEXT NOT NULL,
            new_body TEXT NOT NULL,
            status TEXT NOT NULL,
            error TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            shopify_id TEXT,
            handle TEXT NOT NULL,
            title TEXT,
            description_html TEXT,
            status TEXT DEFAULT 'ACTIVE',
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0,
            online_store_url TEXT
        );
        
        CREATE TABLE blog_articles (
            id INTEGER PRIMARY KEY,
            shopify_id TEXT,
            blog_handle TEXT NOT NULL,
            handle TEXT NOT NULL,
            title TEXT,
            body TEXT,
            is_published INTEGER DEFAULT 1,
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0
        );
        
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            anchor_text TEXT,
            href TEXT
        );
        
        CREATE TABLE service_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        
        INSERT INTO service_settings (key, value) VALUES ('internal_link_ai_woven_enabled_types', 'blog_article,product');
        
        -- Sample product
        INSERT INTO products (shopify_id, handle, title, description_html, status, online_store_url)
        VALUES ('gid://shopify/Product/1', 'target-product', 'Target Product', '<p>Product</p>', 'ACTIVE', 'https://example.myshopify.com/products/target-product');
        
        -- Sample article
        INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, is_published)
        VALUES ('gid://shopify/Article/1', 'news', 'sample-article', 'Sample Article', '<p>Sample body</p>', 1);
        
        -- Dismissed suggestion
        INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
        VALUES (1, 'blog_article', 'news/sample-article', 'product', 'target-product', 'phrase_wrap', 'Target Product', 0.8, 'dismissed', 1700000000);
        
        -- Suggested suggestion
        INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, status, created_at)
        VALUES (2, 'blog_article', 'news/sample-article', 'product', 'other-product', 'phrase_wrap', 'Other Product', 0.7, 'suggested', 1700000000);
    """)
    ensure_schema(conn)
    conn.commit()
    return conn


class TestRestoreAPIEndpoint:
    """Tests for the restore API endpoint."""
    
    @pytest.fixture
    def api_with_auth(self, tmp_path, monkeypatch):
        """Create API test client with mocked auth tokens."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        from backend.app.routers import internal_links as router
        
        # Create token directory with test tokens
        token_dir = tmp_path / "tokens"
        token_dir.mkdir()
        (token_dir / "salar.token").write_text("test_token_salar_32chars_minimum")
        (token_dir / "jimmy.token").write_text("test_token_jimmy_32chars_minimum")
        
        monkeypatch.setenv("TASK_MANAGER_TOKEN_DIR", str(token_dir))
        
        path = tmp_path / "restore_api.sqlite"
        _make_api_database(path)
        
        def connect():
            c = sqlite3.connect(path, timeout=10)
            c.row_factory = sqlite3.Row
            return c
        
        monkeypatch.setattr(router, "open_db_connection", connect)
        
        yield TestClient(app), path
    
    def test_restore_api_success_with_valid_token(self, api_with_auth):
        """API endpoint restores dismissed suggestion with valid token."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "Customer requested"},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "suggested"
        assert data["actor"] == "salar"
    
    def test_restore_api_401_without_token(self, api_with_auth):
        """B4: API returns 401 without X-Task-Token."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "Should fail"},
        )
        
        assert response.status_code == 401
    
    def test_restore_api_401_with_invalid_token(self, api_with_auth):
        """B4: API returns 401 with invalid X-Task-Token."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "Should fail"},
            headers={"X-Task-Token": "invalid_token_that_doesnt_match"},
        )
        
        assert response.status_code == 401
    
    def test_restore_api_404_not_found(self, api_with_auth):
        """B6: API returns 404 for non-existent suggestions."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/999/restore",
            json={"reason": "Should fail"},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 404
        # App uses custom exception handler: {"ok": False, "error": {"code": "...", "message": "..."}}
        assert "not found" in response.json()["error"]["message"].lower()
    
    def test_restore_api_409_not_dismissed(self, api_with_auth):
        """API returns 409 for non-dismissed suggestions."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/2/restore",
            json={"reason": "Should fail"},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 409
        assert "dismissed" in response.json()["error"]["message"].lower()
    
    def test_restore_api_422_empty_reason(self, api_with_auth):
        """B5: API returns 422 for empty reason."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": ""},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 422
    
    def test_restore_api_422_whitespace_only_reason(self, api_with_auth):
        """B5: API returns 422 for whitespace-only reason."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "   \n  \t  "},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 422
    
    def test_restore_api_422_reason_too_long(self, api_with_auth):
        """B5: API returns 422 for reason over 500 chars."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "x" * 501},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 422
    
    def test_restore_api_422_unknown_field(self, api_with_auth):
        """B5: API returns 422 for unknown fields (extra='forbid')."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "valid reason", "unknown_field": "value"},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 422
    
    def test_restore_api_reason_is_stripped(self, api_with_auth):
        """B5: Reason is stripped before validation and storage."""
        client, db_path = api_with_auth
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "  valid reason with spaces  "},
            headers={"X-Task-Token": "test_token_salar_32chars_minimum"},
        )
        
        assert response.status_code == 200
        
        # Verify stripped reason in audit
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        audit = conn.execute("SELECT reason FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        conn.close()
        assert audit["reason"] == "valid reason with spaces"
