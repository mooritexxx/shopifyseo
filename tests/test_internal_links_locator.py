"""Tests for AI insert locator normalization and error codes."""
import json
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import internal_links as router
from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links import shopify_io, pipeline
from shopifyseo.internal_links.safety import LinkConflict, build_edit, _normalize_for_matching


BASE = "https://example.myshopify.com"


def make_database(path=":memory:"):
    """Create a test database with ai_woven suggestion."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    
    conn.execute(
        """INSERT INTO products 
        (shopify_id, handle, title, status, online_store_url, description_html, tags_json, options_json, raw_json, synced_at)
        VALUES ('gid://shopify/Product/1', 'source-product', 'Source Product', 'ACTIVE', 
        'https://example.myshopify.com/products/source-product', '<p>Test body.</p>', '[]', '[]', '{}', 'now')"""
    )
    
    conn.execute(
        """INSERT INTO collections 
        (shopify_id, handle, title, description_html, raw_json, synced_at)
        VALUES ('gid://shopify/Collection/1', 'target-collection', 'Target Collection', '<p>Desc</p>', '{}', 'now')"""
    )
    
    conn.execute(
        """INSERT INTO link_suggestions
        (source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, created_at)
        VALUES ('product', 'source-product', 'collection', 'target-collection', 'ai_woven', NULL, 0.75, 1)"""
    )
    
    conn.commit()
    return conn


class MockShopify:
    def __init__(self, body="<p>Test body.</p>"):
        self.body = body
        self.fetch = Mock(side_effect=lambda *_: self.body)
        self.push = Mock(side_effect=self._push)
    
    def _push(self, source_type, row, body):
        self.body = body
        return body


class TestNormalizationUnit:
    """Unit tests for _normalize_for_matching."""
    
    def test_curly_quotes_normalized(self):
        """Curly quotes are normalized to straight quotes."""
        assert _normalize_for_matching("It's great") == "It's great"
        assert _normalize_for_matching("It\u2019s great") == "It's great"  # right single quote
        assert _normalize_for_matching("It\u2018s great") == "It's great"  # left single quote
        assert _normalize_for_matching('"Hello"') == '"Hello"'
        assert _normalize_for_matching('\u201cHello\u201d') == '"Hello"'  # curly double quotes
    
    def test_dashes_normalized(self):
        """Em-dash and en-dash are normalized to hyphen."""
        assert _normalize_for_matching("A—B") == "A-B"  # em-dash
        assert _normalize_for_matching("A\u2014B") == "A-B"
        assert _normalize_for_matching("A–B") == "A-B"  # en-dash
        assert _normalize_for_matching("A\u2013B") == "A-B"
    
    def test_html_entities_unescaped(self):
        """HTML entities are unescaped."""
        assert _normalize_for_matching("A &amp; B") == "A & B"
        assert _normalize_for_matching("&lt;tag&gt;") == "<tag>"
    
    def test_whitespace_normalized(self):
        """Multiple whitespace collapsed, nbsp normalized."""
        assert _normalize_for_matching("A  B   C") == "A B C"
        assert _normalize_for_matching("A\u00a0B") == "A B"  # nbsp
        assert _normalize_for_matching("  leading and trailing  ") == "leading and trailing"


class TestBuildEditLocatorMatching:
    """Tests for locator matching in build_edit."""
    
    def test_curly_vs_straight_quotes_match(self):
        """Body with curly quotes matches locator with straight quotes."""
        body = "<p>Don\u2019t miss this \u201camazing\u201d offer today.</p>"
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "Check out Target Collection for more.",
            "insert_after_text": "Don't miss this \"amazing\" offer today."  # straight quotes
        }
        
        result = build_edit(body, edit, f"{BASE}/collections/target-collection")
        assert 'href="' in result
        assert "Target Collection" in result
    
    def test_amp_entity_match(self):
        """Body with &amp; matches locator with &."""
        body = "<p>Salt &amp; Pepper are essential.</p>"
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection for seasonings.",
            "insert_after_text": "Salt & Pepper are essential."  # unescaped &
        }
        
        result = build_edit(body, edit, f"{BASE}/collections/target-collection")
        assert 'href="' in result
    
    def test_long_paragraph_prefix_match(self):
        """Long paragraph (≥600 chars) matches using prefix (~120 chars)."""
        # Create a long paragraph
        long_text = "This is a very long paragraph that contains a lot of text. " * 15  # ~900 chars
        body = f"<p>{long_text}</p>"
        
        # Locator is just the first ~120 characters
        locator = long_text[:120].strip()
        
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection for more options.",
            "insert_after_text": locator
        }
        
        result = build_edit(body, edit, f"{BASE}/collections/target-collection")
        assert 'href="' in result
        assert "Target Collection" in result
    
    def test_ambiguous_paragraphs_fails(self):
        """Two paragraphs with same opening sentence fails as ambiguous."""
        body = "<p>The quick brown fox jumps.</p><p>The quick brown fox jumps over the lazy dog.</p>"
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "The quick brown fox jumps."  # Both paragraphs match
        }
        
        # Both paragraphs start with same text, should be ambiguous
        # Note: exact match on first, but second is longer so also matches
        # Actually, first is exact match, second is not
        # Let me create a truly ambiguous case
        body2 = "<p>Same start here.</p><p>Same start here.</p>"
        edit2 = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "Same start here."
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body2, edit2, f"{BASE}/collections/target-collection")
        
        assert exc_info.value.code == "insert_locator_ambiguous"
        assert exc_info.value.detail["match_count"] == 2
        assert "insert_after_text" in exc_info.value.detail
    
    def test_locator_not_in_body_fails(self):
        """Locator not in body fails with no_match."""
        body = "<p>This is the actual paragraph content.</p>"
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "This text does not appear in the body at all."
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target-collection")
        
        assert exc_info.value.code == "insert_locator_no_match"
        assert "insert_after_text" in exc_info.value.detail
        assert exc_info.value.detail["insert_after_text"] == edit["insert_after_text"]
    
    def test_short_prefix_no_full_match_fails(self):
        """Prefix shorter than minimum (40) that doesn't match full paragraph fails."""
        body = "<p>This is a paragraph with some text that is longer than 40 chars.</p>"
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "This is a"  # Only 10 chars, won't match as prefix
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target-collection")
        
        assert exc_info.value.code == "insert_locator_no_match"
    
    def test_exact_short_match_works(self):
        """Short locator that exactly matches a paragraph works."""
        body = "<p>Short.</p><p>Another paragraph here.</p>"
        edit = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "Short."
        }
        
        result = build_edit(body, edit, f"{BASE}/collections/target-collection")
        assert 'href="' in result
    
    def test_ellipsis_stripped_from_locator(self):
        """Trailing ellipsis is stripped from locator."""
        body = "<p>This is the opening of a long paragraph that continues.</p>"
        
        # Locator with ellipsis should still match
        edit1 = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "This is the opening of a long paragraph that continues…"
        }
        
        # Since the body doesn't have the ellipsis, this should work via stripping
        # Actually the body ends with period, so full match should work
        result = build_edit(body, edit1, f"{BASE}/collections/target-collection")
        assert 'href="' in result
        
        # Also test with ...
        edit2 = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": "This is the opening of a long paragraph that continues..."
        }
        
        result2 = build_edit(body, edit2, f"{BASE}/collections/target-collection")
        assert 'href="' in result2
    
    def test_prefix_match_minimum_40_chars(self):
        """Prefix match requires at least 40 normalized characters."""
        # Create a long paragraph with clear structure
        long_para = "This is a very long paragraph with many words that continues on and on for quite a while."
        body = f"<p>{long_para}</p>"
        
        # 39 char prefix should fail (no exact match, too short for prefix)
        edit_39 = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": long_para[:35]  # ~35 chars, normalized will be less than 40
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit_39, f"{BASE}/collections/target-collection")
        assert exc_info.value.code == "insert_locator_no_match"
        
        # 45 char prefix should work (over the 40 minimum)
        edit_45 = {
            "anchor_phrase": "Target Collection",
            "insert_sentence": "See Target Collection.",
            "insert_after_text": long_para[:50]  # ~50 chars, should be over 40 normalized
        }
        
        result = build_edit(body, edit_45, f"{BASE}/collections/target-collection")
        assert 'href="' in result


class TestAiWeaveLocatorErrors:
    """Tests for locator errors through generate_ai_anchor."""
    
    @pytest.fixture
    def setup(self, tmp_path, monkeypatch):
        path = tmp_path / "test.sqlite"
        conn = make_database(path)
        live = MockShopify()
        
        # Disable actual AI
        monkeypatch.setattr("shopifyseo.internal_links.ai_weave._default_call_ai", 
                            Mock(side_effect=AssertionError("AI should not be called")))
        
        yield conn, live
        conn.close()
    
    def test_no_match_error_persists_nothing(self, setup, monkeypatch):
        """insert_locator_no_match error doesn't persist ai_edit_json."""
        conn, live = setup
        
        body = "<p>Actual paragraph content here.</p>"
        live.body = body
        conn.execute("UPDATE products SET description_html = ?", (body,))
        conn.commit()
        
        # Mock AI to return locator that doesn't match
        def mock_ai(*args):
            return {
                "anchor_phrase": "Target Collection",
                "insert_sentence": "See Target Collection.",
                "insert_after_text": "This text is not in the body at all."
            }
        
        monkeypatch.setattr("shopifyseo.internal_links.ai_weave._default_call_ai", mock_ai)
        
        from shopifyseo.internal_links.ai_weave import generate_ai_anchor
        
        with pytest.raises(LinkConflict) as exc_info:
            generate_ai_anchor(conn, 1, BASE, fetch_fn=live.fetch)
        
        assert exc_info.value.code == "insert_locator_no_match"
        assert "insert_after_text" in exc_info.value.detail
        
        # Verify nothing was persisted
        row = conn.execute("SELECT ai_edit_json FROM link_suggestions WHERE id = 1").fetchone()
        assert row["ai_edit_json"] is None
    
    def test_ambiguous_error_persists_nothing(self, setup, monkeypatch):
        """insert_locator_ambiguous error doesn't persist ai_edit_json."""
        conn, live = setup
        
        body = "<p>Same paragraph.</p><p>Same paragraph.</p>"
        live.body = body
        conn.execute("UPDATE products SET description_html = ?", (body,))
        conn.commit()
        
        def mock_ai(*args):
            return {
                "anchor_phrase": "Target Collection",
                "insert_sentence": "See Target Collection.",
                "insert_after_text": "Same paragraph."
            }
        
        monkeypatch.setattr("shopifyseo.internal_links.ai_weave._default_call_ai", mock_ai)
        
        from shopifyseo.internal_links.ai_weave import generate_ai_anchor
        
        with pytest.raises(LinkConflict) as exc_info:
            generate_ai_anchor(conn, 1, BASE, fetch_fn=live.fetch)
        
        assert exc_info.value.code == "insert_locator_ambiguous"
        assert exc_info.value.detail["match_count"] == 2
        
        # Verify nothing was persisted
        row = conn.execute("SELECT ai_edit_json FROM link_suggestions WHERE id = 1").fetchone()
        assert row["ai_edit_json"] is None


class TestApiLocatorErrors:
    """Tests for locator errors through the HTTP API."""
    
    @pytest.fixture
    def api(self, tmp_path, monkeypatch):
        path = tmp_path / "api.sqlite"
        conn = make_database(path)
        
        body = "<p>Actual paragraph content here.</p>"
        conn.execute("UPDATE products SET description_html = ?", (body,))
        conn.commit()
        
        live = MockShopify(body)
        
        def connect():
            c = sqlite3.connect(path, timeout=10)
            c.row_factory = sqlite3.Row
            return c
        
        monkeypatch.setattr(router, "open_db_connection", connect)
        monkeypatch.setattr(router, "_base_url", lambda _: BASE)
        monkeypatch.setattr(pipeline, "generate_link_suggestions", Mock(return_value=0))
        monkeypatch.setattr(shopify_io, "fetch_body", live.fetch)
        monkeypatch.setattr(shopify_io, "push_body", live.push)
        
        # Mock AI to return locator that doesn't match
        def mock_ai(*args):
            return {
                "anchor_phrase": "Target Collection",
                "insert_sentence": "See Target Collection.",
                "insert_after_text": "This text is not in the body at all."
            }
        
        from shopifyseo.internal_links import ai_weave
        monkeypatch.setattr(ai_weave, "_default_call_ai", mock_ai)
        
        yield TestClient(app), conn, live
        conn.close()
    
    def test_generate_anchor_no_match_returns_409_with_code(self, api):
        """generate-anchor with no-match returns HTTP 409 with error.code == "insert_locator_no_match"."""
        client, conn, live = api
        
        response = client.post("/api/internal-links/suggestions/1/generate-anchor")
        
        assert response.status_code == 409
        data = response.json()
        assert data["ok"] is False
        assert data["error"]["code"] == "insert_locator_no_match"
        assert "insert_after_text" in data["error"]
        
        # Other conflicts should still return link_conflict
        live.push.assert_not_called()
    
    def test_other_conflicts_return_link_conflict_code(self, api, monkeypatch):
        """Other LinkConflict errors still return code == "link_conflict"."""
        client, conn, live = api
        
        # Set up for a different kind of conflict - suggestion not found
        from shopifyseo.internal_links import ai_weave
        def mock_ai_that_wont_run(*args):
            raise AssertionError("Should not reach AI")
        monkeypatch.setattr(ai_weave, "_default_call_ai", mock_ai_that_wont_run)
        
        # Request non-existent suggestion
        response = client.post("/api/internal-links/suggestions/99999/generate-anchor")
        
        assert response.status_code == 409
        data = response.json()
        assert data["error"]["code"] == "link_conflict"  # default code


class TestLinkConflictCode:
    """Tests for LinkConflict code attribute."""
    
    def test_default_code_is_link_conflict(self):
        """Default code is 'link_conflict'."""
        exc = LinkConflict("Test error")
        assert exc.code == "link_conflict"
        assert exc.detail["message"] == "Test error"
    
    def test_custom_code(self):
        """Custom code is preserved."""
        exc = LinkConflict("Test error", code="custom_code")
        assert exc.code == "custom_code"
    
    def test_extra_in_detail(self):
        """Extra dict is included in detail."""
        exc = LinkConflict("Test error", extra={"foo": "bar", "count": 42})
        assert exc.detail["foo"] == "bar"
        assert exc.detail["count"] == 42
        assert exc.detail["message"] == "Test error"
    
    def test_text_diff_and_extra(self):
        """text_diff and extra can both be used."""
        exc = LinkConflict("Test error", text_diff="diff here", extra={"key": "value"})
        assert exc.detail["text_diff"] == "diff here"
        assert exc.detail["key"] == "value"


class TestLocatorEmptyPunctuationRejection:
    """S2: Empty or punctuation-only locator must reject with insert_locator_no_match."""
    
    def test_ellipsis_only_locator_rejects(self):
        """Locator '...' normalizes to empty and rejects."""
        body = "<p>Normal paragraph text here.</p>"
        edit = {
            "anchor_phrase": "link text",
            "insert_sentence": "See link text.",
            "insert_after_text": "..."
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target")
        
        assert exc_info.value.code == "insert_locator_no_match"
        assert "empty" in str(exc_info.value).lower() or "punctuation" in str(exc_info.value).lower()
    
    def test_unicode_ellipsis_only_locator_rejects(self):
        """Locator '…' normalizes to empty and rejects."""
        body = "<p>Normal paragraph text here.</p>"
        edit = {
            "anchor_phrase": "link text",
            "insert_sentence": "See link text.",
            "insert_after_text": "…"
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target")
        
        assert exc_info.value.code == "insert_locator_no_match"
    
    def test_whitespace_only_locator_rejects(self):
        """Locator with whitespace that normalizes to empty rejects.
        
        Note: Pure whitespace is caught by validate_edit first.
        This tests whitespace that survives validation but becomes empty after normalization.
        """
        body = "<p>Normal paragraph text here.</p>"
        # A locator with just spaces would be caught by validate_edit,
        # so we test that ellipsis followed by spaces is rejected
        edit = {
            "anchor_phrase": "link text",
            "insert_sentence": "See link text.",
            "insert_after_text": "...   "  # Ellipsis gets stripped, then whitespace
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target")
        
        assert exc_info.value.code == "insert_locator_no_match"
    
    def test_punctuation_only_locator_rejects(self):
        """Locator with only punctuation (no alphanumeric) rejects."""
        body = "<p>Normal paragraph text here.</p>"
        edit = {
            "anchor_phrase": "link text",
            "insert_sentence": "See link text.",
            "insert_after_text": "... --- !!!"
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target")
        
        assert exc_info.value.code == "insert_locator_no_match"
    
    def test_ellipsis_locator_never_matches_empty_paragraph(self):
        """S2: Ellipsis locator (empty after processing) must not match empty paragraphs."""
        body = "<p></p><p>Real content here.</p>"
        # An empty string would be caught by validate_edit,
        # so we use ellipsis which gets stripped to empty
        edit = {
            "anchor_phrase": "link text",
            "insert_sentence": "See link text.",
            "insert_after_text": "..."
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target")
        
        assert exc_info.value.code == "insert_locator_no_match"
    
    def test_empty_locator_never_matches_nbsp_paragraph(self):
        """S2: Empty locator must not match &nbsp; spacer paragraphs."""
        body = "<p>&nbsp;</p><p>Real content here.</p>"
        edit = {
            "anchor_phrase": "link text",
            "insert_sentence": "See link text.",
            "insert_after_text": "..."
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, f"{BASE}/collections/target")
        
        assert exc_info.value.code == "insert_locator_no_match"
