"""Tests for Internal Linking World-Class features (Phases B-E).

Phase B: Weak anchor blocking, smarter orphans
Phase D: Event logging, outcomes
Phase E: Auto-apply
"""

import sqlite3
import time

import pytest


def _make_test_db() -> sqlite3.Connection:
    """Create test database with all required tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (shopify_id TEXT, handle TEXT PRIMARY KEY, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT PRIMARY KEY, title TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT, api_unreachable INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT PRIMARY KEY, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0, seo_title TEXT, seo_description TEXT,
            PRIMARY KEY (blog_handle, handle));
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            anchor_text TEXT,
            href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
            anchor_phrase TEXT,
            ai_anchor_html TEXT,
            source_body_hash TEXT,
            score REAL NOT NULL DEFAULT 0,
            weak_anchor INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('suggested', 'applied', 'dismissed', 'undone')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE TABLE link_suggestion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            suggestion_id INTEGER NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN ('apply', 'undo', 'dismiss', 'auto_apply')),
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL,
            score REAL,
            gsc_clicks_at_event INTEGER,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE keyword_page_map (
            keyword TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_handle TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gsc',
            gsc_clicks INTEGER DEFAULT 0,
            PRIMARY KEY (keyword, object_type, object_handle)
        );
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, primary_keyword TEXT,
            match_type TEXT, match_handle TEXT
        );
        CREATE TABLE cluster_keywords (cluster_id INTEGER, keyword TEXT, PRIMARY KEY (cluster_id, keyword));
        """
    )
    return conn


class TestPhaseAUnreachableCollections:
    """Phase A: api_unreachable collection handling."""

    def test_unreachable_collection_excluded_from_targets(self):
        """Collections marked api_unreachable should not be valid link targets."""
        from shopifyseo.internal_links.pipeline import _target_exists_and_published
        
        conn = _make_test_db()
        conn.execute("INSERT INTO collections (handle, title) VALUES ('reachable', 'Reachable')")
        conn.execute("INSERT INTO collections (handle, title, api_unreachable) VALUES ('ghost', 'Ghost', 1)")
        conn.commit()
        
        assert _target_exists_and_published(conn, "collection", "reachable") is True
        assert _target_exists_and_published(conn, "collection", "ghost") is False

    def test_unreachable_collection_excluded_from_orphans(self):
        """Unreachable collections should not appear in orphan list."""
        from shopifyseo.internal_links.pipeline import _orphan_targets
        
        conn = _make_test_db()
        conn.execute("INSERT INTO collections (handle, title, gsc_clicks) VALUES ('visible', 'Visible', 100)")
        conn.execute("INSERT INTO collections (handle, title, api_unreachable) VALUES ('ghost', 'Ghost', 1)")
        conn.commit()
        
        orphans = _orphan_targets(conn)
        handles = [h for _, h, _, _ in orphans]
        
        assert "visible" in handles
        assert "ghost" not in handles


class TestPhaseBWeakAnchors:
    """Phase B: Weak anchor blocking in pipeline."""

    def test_weak_anchor_flag_set_on_suggestions(self):
        """Pipeline should set weak_anchor=1 for single-word generic anchors."""
        from shopifyseo.internal_links.pipeline import generate_link_suggestions
        
        conn = _make_test_db()
        # Body contains "products" - a weak single-word anchor
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) "
            "VALUES ('news', 'post', 'Post', '<p>Check out our products today.</p>', 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('products', 'Products')")
        conn.commit()
        
        def fake_related(conn, object_type, handle, top_k=10, type_quotas=None):
            if (object_type, handle) == ("blog_article", "news/post"):
                return [{"object_type": "collection", "object_handle": "products", "score": 0.9}]
            return []
        
        generate_link_suggestions(conn, related_fn=fake_related, rebuild_graph=False)
        
        row = conn.execute(
            "SELECT weak_anchor, score FROM link_suggestions WHERE target_handle = 'products'"
        ).fetchone()
        
        assert row is not None
        # "products" is a weak anchor word
        assert row["weak_anchor"] == 1
        # Score is penalized by WEAK_ANCHOR_PENALTY (0.4x) but still > 0
        # (base score + orphan bonus before penalty; penalty reduces it)
        assert row["score"] > 0

    def test_strong_anchor_not_penalized(self):
        """Multi-word phrase anchors should not be marked weak."""
        from shopifyseo.internal_links.pipeline import generate_link_suggestions
        
        conn = _make_test_db()
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) "
            "VALUES ('news', 'post', 'Post', '<p>Check out our ceramic tanks collection.</p>', 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def fake_related(conn, object_type, handle, top_k=10, type_quotas=None):
            if (object_type, handle) == ("blog_article", "news/post"):
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        generate_link_suggestions(conn, related_fn=fake_related, rebuild_graph=False)
        
        row = conn.execute(
            "SELECT weak_anchor, kind FROM link_suggestions WHERE target_handle = 'ceramic-tanks'"
        ).fetchone()
        
        assert row is not None
        assert row["kind"] == "phrase_wrap"
        assert row["weak_anchor"] == 0


class TestPhaseBSmarterOrphans:
    """Phase B: Smarter orphan detection."""

    def test_product_linked_from_collection_not_orphan(self):
        """Products linked from collection pages are not true orphans."""
        from shopifyseo.internal_links.pipeline import _orphan_targets
        
        conn = _make_test_db()
        conn.execute("INSERT INTO products (handle, title, status) VALUES ('widget', 'Widget', 'ACTIVE')")
        conn.execute("INSERT INTO products (handle, title, status) VALUES ('gadget', 'Gadget', 'ACTIVE')")
        # widget has inbound from collection, gadget doesn't
        conn.execute(
            "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, href) "
            "VALUES ('collection', 'featured', 'product', 'widget', '/products/widget')"
        )
        conn.commit()
        
        orphans = _orphan_targets(conn)
        handles = [h for t, h, _, _ in orphans if t == "product"]
        
        assert "gadget" in handles
        assert "widget" not in handles  # Linked from collection, not an orphan


class TestPhaseDEventLogging:
    """Phase D: Event logging for measurement."""

    def test_apply_logs_event(self):
        """Applying a suggestion should log an event."""
        import hashlib
        from shopifyseo.internal_links.apply import apply_suggestion
        
        conn = _make_test_db()
        body = "<p>Check out ceramic tanks today.</p>"
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        
        conn.execute(
            "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
            "VALUES ('gid://1', 'news', 'post', 'Post', ?, '', '')",
            (body,)
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, "
            "kind, anchor_phrase, source_body_hash, score, created_at) "
            "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', "
            "'ceramic tanks', ?, 1.0, ?)",
            (body_hash, int(time.time()))
        )
        conn.commit()
        
        sug_id = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
        
        def fake_push(source_type, row, new_body):
            return {"ok": True}
        
        apply_suggestion(conn, sug_id, base_url="https://test.com", push_fn=fake_push, sanitize_fn=lambda b: b)
        
        event = conn.execute(
            "SELECT * FROM link_suggestion_events WHERE suggestion_id = ?", (sug_id,)
        ).fetchone()
        
        assert event is not None
        assert event["event_type"] == "apply"
        assert event["source_type"] == "blog_article"
        assert event["target_handle"] == "ceramic-tanks"

    def test_undo_logs_event(self):
        """Undoing a suggestion should log an event."""
        import hashlib
        from shopifyseo.internal_links.apply import apply_suggestion, undo_suggestion
        
        conn = _make_test_db()
        body = "<p>Check out ceramic tanks today.</p>"
        body_hash = hashlib.sha256(body.encode()).hexdigest()
        
        conn.execute(
            "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
            "VALUES ('gid://1', 'news', 'post', 'Post', ?, '', '')",
            (body,)
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, "
            "kind, anchor_phrase, source_body_hash, score, created_at) "
            "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', "
            "'ceramic tanks', ?, 1.0, ?)",
            (body_hash, int(time.time()))
        )
        conn.commit()
        
        sug_id = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
        
        def fake_push(source_type, row, new_body):
            return {"ok": True}
        
        apply_suggestion(conn, sug_id, base_url="https://test.com", push_fn=fake_push, sanitize_fn=lambda b: b)
        undo_suggestion(conn, sug_id, base_url="https://test.com", push_fn=fake_push)
        
        events = conn.execute(
            "SELECT event_type FROM link_suggestion_events WHERE suggestion_id = ? ORDER BY created_at",
            (sug_id,)
        ).fetchall()
        
        assert len(events) == 2
        assert events[0]["event_type"] == "apply"
        assert events[1]["event_type"] == "undo"


class TestPhaseCWriteTime:
    """Phase C: Write-time link prioritization tests."""

    def test_prioritize_targets_for_write_time_orphans_first(self):
        """Orphan pages should be moved to top of their type group."""
        from shopifyseo.internal_links.write_time import prioritize_targets_for_write_time

        conn = _make_test_db()
        # Create two products - one with inbound links (not orphan), one without (orphan)
        conn.execute("INSERT INTO products (handle, title) VALUES ('linked-product', 'Linked Product')")
        conn.execute("INSERT INTO products (handle, title) VALUES ('orphan-product', 'Orphan Product')")
        # Create an inbound link to linked-product
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published) "
            "VALUES ('news', 'post', 'Post', '<a href=\"/products/linked-product\">Link</a>', 1)"
        )
        conn.execute(
            "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text) "
            "VALUES ('blog_article', 'news/post', 'product', 'linked-product', 'Link')"
        )
        conn.commit()

        link_targets = [
            {"type": "product", "handle": "linked-product", "title": "Linked Product", "url": "/products/linked-product"},
            {"type": "product", "handle": "orphan-product", "title": "Orphan Product", "url": "/products/orphan-product"},
        ]

        result = prioritize_targets_for_write_time(conn, link_targets)

        # Orphan product should come first
        assert result[0]["handle"] == "orphan-product"
        assert result[1]["handle"] == "linked-product"

    def test_prioritize_targets_type_ordering(self):
        """Products should be prioritized over collections, collections over pages."""
        from shopifyseo.internal_links.write_time import prioritize_targets_for_write_time

        conn = _make_test_db()
        conn.execute("INSERT INTO products (handle, title) VALUES ('test-product', 'Test Product')")
        conn.execute("INSERT INTO collections (handle, title) VALUES ('test-collection', 'Test Collection')")
        conn.execute("INSERT INTO pages (handle, title) VALUES ('test-page', 'Test Page')")
        conn.commit()

        link_targets = [
            {"type": "page", "handle": "test-page", "title": "Test Page", "url": "/pages/test-page"},
            {"type": "collection", "handle": "test-collection", "title": "Test Collection", "url": "/collections/test-collection"},
            {"type": "product", "handle": "test-product", "title": "Test Product", "url": "/products/test-product"},
        ]

        result = prioritize_targets_for_write_time(conn, link_targets)

        # Product first, then collection, then page
        assert result[0]["type"] == "product"
        assert result[1]["type"] == "collection"
        assert result[2]["type"] == "page"

    def test_ai_body_links_disabled_by_default(self):
        """AI body links setting should be disabled by default."""
        from shopifyseo.internal_links.write_time import is_ai_body_links_enabled

        conn = _make_test_db()
        assert is_ai_body_links_enabled(conn) is False

    def test_ai_body_links_enabled_when_set(self):
        """AI body links should be enabled when setting is '1'."""
        from shopifyseo.internal_links.write_time import is_ai_body_links_enabled

        conn = _make_test_db()
        conn.execute(
            "INSERT INTO service_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("internal_link_ai_body_links_enabled", "1"),
        )
        conn.commit()

        assert is_ai_body_links_enabled(conn) is True

    def test_enhance_prompt_context_skips_when_disabled(self):
        """enhance_prompt_context_with_prioritized_links should skip when disabled."""
        from shopifyseo.internal_links.write_time import enhance_prompt_context_with_prioritized_links

        conn = _make_test_db()
        original_targets = [
            {"type": "page", "handle": "test-page", "title": "Test Page"},
            {"type": "product", "handle": "test-product", "title": "Test Product"},
        ]
        prompt_ctx = {"approved_internal_link_targets": original_targets.copy()}

        result = enhance_prompt_context_with_prioritized_links(conn, prompt_ctx, "product")

        # Should be unchanged (still page first) because setting is disabled
        assert result["approved_internal_link_targets"][0]["type"] == "page"

    def test_enhance_prompt_context_prioritizes_when_enabled(self):
        """enhance_prompt_context_with_prioritized_links should prioritize when enabled."""
        from shopifyseo.internal_links.write_time import enhance_prompt_context_with_prioritized_links

        conn = _make_test_db()
        conn.execute("INSERT INTO products (handle, title) VALUES ('test-product', 'Test Product')")
        conn.execute("INSERT INTO pages (handle, title) VALUES ('test-page', 'Test Page')")
        conn.execute(
            "INSERT INTO service_settings (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("internal_link_ai_body_links_enabled", "1"),
        )
        conn.commit()

        original_targets = [
            {"type": "page", "handle": "test-page", "title": "Test Page"},
            {"type": "product", "handle": "test-product", "title": "Test Product"},
        ]
        prompt_ctx = {"approved_internal_link_targets": original_targets.copy()}

        result = enhance_prompt_context_with_prioritized_links(conn, prompt_ctx, "product")

        # Should be reordered (product first) because setting is enabled
        assert result["approved_internal_link_targets"][0]["type"] == "product"


class TestPhaseEAutoApply:
    """Phase E: Auto-apply settings and logic."""

    def test_auto_apply_disabled_by_default(self):
        """Auto-apply should be disabled by default."""
        from shopifyseo.internal_links.auto_apply import get_auto_apply_settings
        
        conn = _make_test_db()
        settings = get_auto_apply_settings(conn)
        
        assert settings["enabled"] is False
        assert "phrase_wrap" in settings["kinds"]
        assert "ai_woven" not in settings["kinds"]

    def test_auto_apply_respects_min_score(self):
        """Auto-apply should only consider suggestions above min_score."""
        from shopifyseo.internal_links.auto_apply import find_auto_apply_candidates
        
        conn = _make_test_db()
        now = int(time.time())
        
        # Insert suggestions with different scores
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, "
            "kind, score, weak_anchor, status, created_at) VALUES "
            "('blog_article', 'news/post1', 'collection', 'tanks', 'phrase_wrap', 1.5, 0, 'suggested', ?)",
            (now,)
        )
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, "
            "kind, score, weak_anchor, status, created_at) VALUES "
            "('blog_article', 'news/post2', 'collection', 'other', 'phrase_wrap', 0.8, 0, 'suggested', ?)",
            (now,)
        )
        conn.commit()
        
        # Enable auto-apply with min_score=1.2
        conn.execute(
            "INSERT INTO service_settings (key, value) VALUES "
            "('internal_link_auto_apply_enabled', '1')"
        )
        conn.execute(
            "INSERT INTO service_settings (key, value) VALUES "
            "('internal_link_auto_apply_min_score', '1.2')"
        )
        conn.commit()
        
        candidates = find_auto_apply_candidates(conn)
        
        # Only the high-score suggestion should be a candidate
        assert len(candidates) == 1
        assert candidates[0]["target_handle"] == "tanks"

    def test_auto_apply_excludes_weak_anchors(self):
        """Auto-apply should not apply suggestions with weak anchors."""
        from shopifyseo.internal_links.auto_apply import find_auto_apply_candidates
        
        conn = _make_test_db()
        now = int(time.time())
        
        # Insert suggestion with weak anchor
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, "
            "kind, score, weak_anchor, status, created_at) VALUES "
            "('blog_article', 'news/post', 'collection', 'products', 'phrase_wrap', 1.5, 1, 'suggested', ?)",
            (now,)
        )
        conn.commit()
        
        # Enable auto-apply
        conn.execute(
            "INSERT INTO service_settings (key, value) VALUES "
            "('internal_link_auto_apply_enabled', '1')"
        )
        conn.commit()
        
        candidates = find_auto_apply_candidates(conn)
        
        # Weak anchor suggestion should not be a candidate
        assert len(candidates) == 0

    def test_auto_apply_never_applies_ai_woven(self):
        """Auto-apply should never apply ai_woven suggestions."""
        from shopifyseo.internal_links.auto_apply import find_auto_apply_candidates
        
        conn = _make_test_db()
        now = int(time.time())
        
        # Insert ai_woven suggestion with high score
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, "
            "kind, score, weak_anchor, status, created_at) VALUES "
            "('blog_article', 'news/post', 'collection', 'tanks', 'ai_woven', 2.0, 0, 'suggested', ?)",
            (now,)
        )
        conn.commit()
        
        # Enable auto-apply with kinds=ai_woven (should be ignored)
        conn.execute(
            "INSERT INTO service_settings (key, value) VALUES "
            "('internal_link_auto_apply_enabled', '1')"
        )
        conn.execute(
            "INSERT INTO service_settings (key, value) VALUES "
            "('internal_link_auto_apply_kinds', 'ai_woven,phrase_wrap')"
        )
        conn.commit()
        
        candidates = find_auto_apply_candidates(conn)
        
        # ai_woven should never be auto-applied
        assert len(candidates) == 0
