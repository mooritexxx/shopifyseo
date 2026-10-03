"""Tests for POST /api/internal-links/suggestions/{id}/restore endpoint.

Requirements:
- Only dismissed suggestions can be restored
- Reason is required
- Actor is logged
- Restored suggestions survive rebuild
"""
import json
import pytest
import sqlite3
from unittest.mock import patch, MagicMock

from shopifyseo.internal_links.apply import restore_suggestion
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
    
    # Insert sample product
    conn.execute("""
        INSERT INTO products (shopify_id, handle, title, description_html, status, online_store_url)
        VALUES ('gid://shopify/Product/1', 'target-product', 'Target Product', '<p>Product</p>', 'ACTIVE', 'https://example.myshopify.com/products/target-product')
    """)
    
    # Insert sample article
    conn.execute("""
        INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, is_published)
        VALUES ('gid://shopify/Article/1', 'news', 'sample-article', 'Sample Article', '<p>Sample body</p>', 1)
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
        
        assert result["id"] == 1
        assert result["status"] == "suggested"
        assert result["actor"] == "salar"
        assert result["reason"] == "Customer requested this link"
        assert "restored_at" in result
        
        # Verify database state
        row = database.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "suggested"
        
        # Verify audit record
        audit = database.execute("SELECT * FROM link_suggestion_restore_audit WHERE suggestion_id = 1").fetchone()
        assert audit["actor"] == "salar"
        assert audit["reason"] == "Customer requested this link"
    
    def test_restore_not_found(self, database):
        """Non-existent suggestion raises LinkConflict."""
        with pytest.raises(LinkConflict) as exc_info:
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
    
    def test_restore_clears_applied_at(self, database):
        """Restoring a suggestion clears applied_at."""
        # Set applied_at on the dismissed suggestion
        database.execute("UPDATE link_suggestions SET applied_at = 1700001000 WHERE id = 1")
        database.commit()
        
        restore_suggestion(database, 1, "salar", "reason")
        
        row = database.execute("SELECT applied_at FROM link_suggestions WHERE id = 1").fetchone()
        assert row["applied_at"] is None


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


class TestRestoredSuggestionSurvivesRebuild:
    """Q8 tests: restored suggestions survive rebuild."""
    
    def test_restored_survives_rebuild(self, database):
        """A restored suggestion is not deleted by rebuild."""
        # Restore the dismissed suggestion
        restore_suggestion(database, 1, "salar", "preserve this link")
        
        # Verify it's now suggested
        row = database.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "suggested"
        
        # Run the rebuild DELETE logic (same as pipeline.py)
        database.execute("""DELETE FROM link_suggestions WHERE status = 'suggested' 
            AND NOT EXISTS (
                SELECT 1 FROM link_body_snapshots b 
                WHERE b.suggestion_id = link_suggestions.id 
                AND b.status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')
            )
            AND NOT EXISTS (
                SELECT 1 FROM link_suggestion_restore_audit r
                WHERE r.suggestion_id = link_suggestions.id
            )""")
        
        # The restored suggestion should still exist
        row = database.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is not None
        assert row["status"] == "suggested"
    
    def test_non_restored_deleted_by_rebuild(self, database):
        """Non-restored suggested rows are deleted by rebuild."""
        # Verify suggestion 2 exists and is suggested
        row = database.execute("SELECT status FROM link_suggestions WHERE id = 2").fetchone()
        assert row["status"] == "suggested"
        
        # Run the rebuild DELETE logic
        database.execute("""DELETE FROM link_suggestions WHERE status = 'suggested' 
            AND NOT EXISTS (
                SELECT 1 FROM link_body_snapshots b 
                WHERE b.suggestion_id = link_suggestions.id 
                AND b.status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')
            )
            AND NOT EXISTS (
                SELECT 1 FROM link_suggestion_restore_audit r
                WHERE r.suggestion_id = link_suggestions.id
            )""")
        
        # Non-restored suggestion should be deleted
        row = database.execute("SELECT * FROM link_suggestions WHERE id = 2").fetchone()
        assert row is None


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
    def api(self, tmp_path, monkeypatch):
        """Create API test client with test database."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        from backend.app.routers import internal_links as router
        
        path = tmp_path / "restore_api.sqlite"
        conn = _make_api_database(path)
        
        def connect():
            c = sqlite3.connect(path, timeout=10)
            c.row_factory = sqlite3.Row
            return c
        
        monkeypatch.setattr(router, "open_db_connection", connect)
        
        yield TestClient(app), conn
        conn.close()
    
    def test_restore_api_success(self, api):
        """API endpoint restores dismissed suggestion."""
        client, conn = api
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "Customer requested", "actor": "salar"},
        )
        
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "suggested"
        assert data["actor"] == "salar"
        assert data["reason"] == "Customer requested"
    
    def test_restore_api_default_actor(self, api):
        """API endpoint uses 'web' as default actor."""
        client, conn = api
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={"reason": "No actor specified"},
        )
        
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["actor"] == "web"
    
    def test_restore_api_not_dismissed_409(self, api):
        """API returns 409 for non-dismissed suggestions."""
        client, conn = api
        
        response = client.post(
            "/api/internal-links/suggestions/2/restore",
            json={"reason": "Should fail"},
        )
        
        assert response.status_code == 409
        assert "dismissed" in response.json()["error"]["message"].lower()
    
    def test_restore_api_not_found_409(self, api):
        """API returns 409 for non-existent suggestions."""
        client, conn = api
        
        response = client.post(
            "/api/internal-links/suggestions/999/restore",
            json={"reason": "Should fail"},
        )
        
        assert response.status_code == 409
        assert "not found" in response.json()["error"]["message"].lower()
    
    def test_restore_api_requires_reason(self, api):
        """API requires reason field."""
        client, conn = api
        
        response = client.post(
            "/api/internal-links/suggestions/1/restore",
            json={},  # No reason
        )
        
        assert response.status_code == 422  # Validation error
