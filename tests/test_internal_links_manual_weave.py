"""Tests for manual-weave endpoint and service."""
import json
import sqlite3
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.routers import internal_links as router
from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links import shopify_io, pipeline
from shopifyseo.internal_links.manual_weave import submit_manual_weave, ManualWeaveRejected
from shopifyseo.internal_links.safety import LinkConflict, validate_edit

BASE = "https://example.myshopify.com"

# Sample bodies from the brief
SAMPLE_BODY_SIMPLE = '<p>Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada.</p>'
SAMPLE_BODY_WITH_LINK = '<p>For shoppers exploring other cooling fruit sensations, you can easily expand your collection by viewing <a href="/products/draggg-4k-mango-ice">Draggg 4K - Mango Ice</a> or browsing the complete lineup available within the STLTH 60K Disposable Vapes — Vapely collection.</p>'
SAMPLE_BODY_11_LINKS = '''<p>Some text with many links:
<a href="/products/a">A</a>,
<a href="/products/b">B</a>,
<a href="/products/c">C</a>,
<a href="/products/d">D</a>,
<a href="/products/e">E</a>,
<a href="/products/f">F</a>,
<a href="/products/g">G</a>,
<a href="/products/h">H</a>,
<a href="/products/i">I</a>,
<a href="/products/j">J</a>,
<a href="/products/k">K</a>.
Sentence at the end.</p>'''
SAMPLE_BODY_7_LINKS = '''<p>Some text:
<a href="/products/a">A</a>,
<a href="/products/b">B</a>,
<a href="/products/c">C</a>,
<a href="/products/d">D</a>,
<a href="/products/e">E</a>,
<a href="/products/f">F</a>,
<a href="/products/g">G</a>.
Sentence at the end.</p>'''
SAMPLE_BODY_8_LINKS = '''<p>Some text:
<a href="/products/a">A</a>,
<a href="/products/b">B</a>,
<a href="/products/c">C</a>,
<a href="/products/d">D</a>,
<a href="/products/e">E</a>,
<a href="/products/f">F</a>,
<a href="/products/g">G</a>,
<a href="/products/h">H</a>.
Sentence at the end.</p>'''


def make_database(path=":memory:"):
    """Create a test database with minimal schema and ai_woven suggestion."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    
    # Add a source product
    conn.execute(
        """INSERT INTO products 
        (shopify_id, handle, title, status, online_store_url, description_html, tags_json, options_json, raw_json, synced_at)
        VALUES ('gid://shopify/Product/1', 'zyn-nicotine-pouches-canada-shopper', 'Zyn Nicotine Pouches', 'ACTIVE', 
        'https://example.myshopify.com/products/zyn-nicotine-pouches-canada-shopper', ?, '[]', '[]', '{}', 'now')""",
        (SAMPLE_BODY_SIMPLE,),
    )
    
    # Add blog (required for blog_articles FK)
    conn.execute(
        """INSERT INTO blogs
        (shopify_id, title, handle, tags_json, raw_json, synced_at)
        VALUES ('gid://shopify/Blog/1', 'Canada Blog', 'canada', '[]', '{}', 'now')"""
    )
    
    # Add a target blog article
    conn.execute(
        """INSERT INTO blog_articles
        (shopify_id, blog_shopify_id, blog_handle, handle, title, body, is_published, tags_json, raw_json, synced_at)
        VALUES ('gid://shopify/Article/2', 'gid://shopify/Blog/1', 'canada', 'zyn-canada-nicotine-pouch-availability',
        'Is Zyn Legal in Canada?', '<p>Article body.</p>', 1, '[]', '{}', 'now')"""
    )
    
    # Add an ai_woven suggestion
    conn.execute(
        """INSERT INTO link_suggestions
        (source_type, source_handle, target_type, target_handle, kind, anchor_phrase, score, created_at)
        VALUES ('product', 'zyn-nicotine-pouches-canada-shopper', 'blog_article', 'canada/zyn-canada-nicotine-pouch-availability',
        'ai_woven', NULL, 0.75, 1)"""
    )
    
    conn.commit()
    return conn


class MockShopify:
    """Mock Shopify API for testing."""
    
    def __init__(self, body=SAMPLE_BODY_SIMPLE):
        self.body = body
        self.fetch = Mock(side_effect=lambda *_: self.body)
        self.push = Mock(side_effect=self._push)
    
    def _push(self, source_type, row, body):
        self.body = body
        return body


@pytest.fixture
def database(tmp_path):
    """Create a fresh test database."""
    path = tmp_path / "test.sqlite"
    conn = make_database(path)
    yield conn
    conn.close()


@pytest.fixture
def live():
    """Create a mock Shopify client."""
    return MockShopify()


@pytest.fixture(autouse=True)
def no_ai_calls(monkeypatch):
    """Ensure no AI calls are made during tests."""
    mock_ai = Mock(side_effect=AssertionError("AI must not be called"))
    
    # Patch at module level to catch any imports
    monkeypatch.setattr("shopifyseo.internal_links.ai_weave.generate_ai_anchor", mock_ai)
    monkeypatch.setattr("shopifyseo.internal_links.ai_weave._default_call_ai", mock_ai)
    
    # Also patch providers
    try:
        from shopifyseo.dashboard_ai_engine_parts import providers
        monkeypatch.setattr(providers, "_call_ai", mock_ai)
        monkeypatch.setattr(providers, "_call_openai", mock_ai)
        monkeypatch.setattr(providers, "_call_openrouter", mock_ai)
    except (ImportError, AttributeError):
        pass
    
    yield mock_ai
    
    # Verify AI was never called
    mock_ai.assert_not_called()


@pytest.fixture
def api(tmp_path, monkeypatch, live):
    """Create a test API client with mocked dependencies."""
    path = tmp_path / "api.sqlite"
    conn = make_database(path)
    
    def connect():
        c = sqlite3.connect(path, timeout=10)
        c.row_factory = sqlite3.Row
        return c
    
    monkeypatch.setattr(router, "open_db_connection", connect)
    monkeypatch.setattr(router, "_base_url", lambda _: BASE)
    monkeypatch.setattr(pipeline, "generate_link_suggestions", Mock(return_value=0))
    monkeypatch.setattr(shopify_io, "fetch_body", live.fetch)
    monkeypatch.setattr(shopify_io, "push_body", live.push)
    
    yield TestClient(app), conn, live
    conn.close()


class TestManualWeaveHappyPath:
    """Test case 1: Happy path, append."""
    
    def test_happy_path_returns_allowed_and_token(self, database, live):
        """Test successful manual weave submission."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} For the full picture, see <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">is Zyn legal in Canada?</a>'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        assert result["preview_token"] is not None
        assert result["edit"]["origin"] == "manual"
        assert result["edit"]["anchor_phrase"] == "is Zyn legal in Canada?"
        assert "existing_link_count" in result
        assert result["link_cap"] == 8
        
        # Verify ai_edit_json was persisted
        row = database.execute("SELECT ai_edit_json FROM link_suggestions WHERE id = 1").fetchone()
        assert row["ai_edit_json"] is not None
        edit = json.loads(row["ai_edit_json"])
        assert edit["origin"] == "manual"
        
        # Verify no push was called (preview only)
        live.push.assert_not_called()
    
    def test_new_html_is_append_only(self, database, live):
        """Verify the new_html is old[:i] + insertion + old[i:] for some offset i."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} For the full picture, see <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">is Zyn legal in Canada?</a>'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        old_html = result["old_html"]
        new_html = result["new_html"]
        
        # Find the insertion point: new_html == old[:i] + insertion + old[i:]
        # The insertion contains the addition with link
        insertion_marker = 'For the full picture'
        insert_idx = new_html.find(insertion_marker)
        assert insert_idx > 0, "Insertion not found in new_html"
        
        # Find where the insertion ends (after </a>)
        insertion_end_marker = '</a>'
        insertion_end = new_html.find(insertion_end_marker, insert_idx)
        assert insertion_end > insert_idx, "Link end not found"
        insertion_end += len(insertion_end_marker)
        
        # The prefix before insertion should be in old_html
        # (minus the space that's part of the insertion)
        prefix = new_html[:insert_idx - 1]  # -1 for the leading space
        assert prefix in old_html, f"Prefix '{prefix[:50]}...' not found in old_html"
        
        # The suffix after insertion should be the closing tag(s) of old_html
        suffix = new_html[insertion_end:]
        # The suffix should be the remainder of old_html (just closing tags like </p>)
        # Old html ends with </p>, new html should end with </a></p> or similar
        assert suffix.strip() in old_html or old_html.endswith(suffix.strip().lstrip()), f"Suffix mismatch: '{suffix}'"
        
        # Link href can be relative or absolute
        assert 'zyn-canada-nicotine-pouch-availability"' in new_html
        assert "is Zyn legal in Canada?" in new_html


class TestManualWeaveWithExistingLink:
    """Test case 2: Append after sentence containing existing <a>."""
    
    def test_existing_link_bytes_unchanged(self, database, live):
        """The existing link bytes should be preserved."""
        live.body = SAMPLE_BODY_WITH_LINK
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (SAMPLE_BODY_WITH_LINK,),
        )
        database.commit()
        
        original = "For shoppers exploring other cooling fruit sensations, you can easily expand your collection by viewing Draggg 4K - Mango Ice or browsing the complete lineup available within the STLTH 60K Disposable Vapes — Vapely collection."
        replacement = f'{original} Check our <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">STLTH 60K buying guide</a> for more.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        # Original link should still be present
        assert 'href="/products/draggg-4k-mango-ice"' in result["new_html"]
        # New link should be added (URL may be relative or absolute)
        assert 'blogs/canada/zyn-canada-nicotine-pouch-availability"' in result["new_html"]


class TestManualWeaveRewriteRejected:
    """Test case 3: Rewrite rejected (400)."""
    
    def test_rewrite_that_changes_original_rejected(self, database, live):
        """Replacing words in the original sentence should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        # This changes "strict protocols" to something else
        replacement = 'Health Canada has regulations for nicotine pouches. See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "append-only" in str(exc_info.value).lower()
        
        # Nothing should be persisted
        row = database.execute("SELECT ai_edit_json FROM link_suggestions WHERE id = 1").fetchone()
        assert row["ai_edit_json"] is None


class TestManualWeaveLocation:
    """Test case 4: Location issues (409)."""
    
    def test_sentence_not_in_live_body(self, database, live):
        """Sentence that doesn't exist in live body should fail."""
        original = "This sentence does not exist in the body at all."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our complete guide</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "not found" in str(exc_info.value).lower() or "drift" in str(exc_info.value).lower()
    
    def test_sentence_appears_twice_is_ambiguous(self, database, live):
        """Sentence appearing twice should fail as ambiguous."""
        live.body = "<p>Same sentence here. Some middle text. Same sentence here.</p>"
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (live.body,),
        )
        database.commit()
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence="Same sentence here.",
                replacement_sentence='Same sentence here. See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">this guide</a>.',
                fetch_fn=live.fetch,
            )
        
        assert "ambiguous" in str(exc_info.value).lower() or "multiple" in str(exc_info.value).lower()


class TestManualWeaveTargetAndShape:
    """Test case 5: Target and shape validation (400)."""
    
    def test_wrong_target_handle_rejected(self, database, live):
        """Link to different handle should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/wrong-handle">this</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "target" in str(exc_info.value).lower() or "expected" in str(exc_info.value).lower()
    
    def test_external_host_rejected(self, database, live):
        """Link to external host should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="https://other-site.com/page">this</a>.'
        
        with pytest.raises(ManualWeaveRejected):
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
    
    def test_two_links_rejected(self, database, live):
        """Multiple links in replacement should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">this</a> and <a href="/products/something">that</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "multiple" in str(exc_info.value).lower() or "one" in str(exc_info.value).lower()
    
    def test_no_link_rejected(self, database, live):
        """No link in replacement should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See the guide for more information.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "no link" in str(exc_info.value).lower()
    
    def test_extra_attributes_rejected(self, database, live):
        """Link with extra attributes should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability" onclick="alert(1)">this</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "attribute" in str(exc_info.value).lower()
    
    def test_extra_tags_rejected(self, database, live):
        """Extra HTML tags should fail."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <strong><a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">this</a></strong>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "tag" in str(exc_info.value).lower()
    
    def test_three_sentences_rejected(self, database, live):
        """More than 2 sentences in addition should fail with 400."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        # Use a valid multi-word anchor to test the sentence limit specifically
        replacement = f'{original} First sentence. Second sentence. Third sentence with <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our detailed guide</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "2 sentences" in str(exc_info.value).lower()
    
    def test_over_300_chars_rejected(self, database, live):
        """Addition over 300 characters should fail with 400."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        # Use a valid multi-word anchor and enough text to exceed 300 chars
        # "A" * 285 + " our detailed guide." = 285 + 1 + 18 + 1 = 305 chars
        long_addition = "A" * 285 + f' <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our detailed guide</a>.'
        replacement = f'{original} {long_addition}'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "300" in str(exc_info.value)


class TestManualWeaveLinkableTarget:
    """Test case 6: Linkable target validation (409)."""
    
    def test_draft_product_target_rejected(self, database, live):
        """Target product with DRAFT status should fail."""
        # Change the suggestion to target a product instead
        database.execute(
            """INSERT INTO products 
            (shopify_id, handle, title, status, description_html, tags_json, options_json, raw_json, synced_at)
            VALUES ('gid://shopify/Product/99', 'draft-product', 'Draft Product', 'DRAFT', '<p>body</p>', '[]', '[]', '{}', 'now')"""
        )
        database.execute(
            "UPDATE link_suggestions SET target_type = 'product', target_handle = 'draft-product' WHERE id = 1"
        )
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/products/draft-product">this product</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "no longer eligible" in str(exc_info.value).lower() or "not" in str(exc_info.value).lower()
    
    def test_unpublished_blog_rejected(self, database, live):
        """Unpublished blog article target should fail."""
        database.execute("UPDATE blog_articles SET is_published = 0")
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "no longer eligible" in str(exc_info.value).lower()


class TestManualWeaveLinkCap:
    """Test case 7: 8-link cap (dedicated parametrised test)."""
    
    @pytest.mark.parametrize("link_count,expected_allowed", [
        (7, True),   # 7 + 1 = 8, allowed
        (8, False),  # 8 + 1 = 9, rejected
        (11, False), # 11 + 1 = 12, rejected
    ])
    def test_link_cap(self, database, live, link_count, expected_allowed):
        """Test 8-link cap enforcement."""
        if link_count == 7:
            live.body = SAMPLE_BODY_7_LINKS
        elif link_count == 8:
            live.body = SAMPLE_BODY_8_LINKS
        else:
            live.body = SAMPLE_BODY_11_LINKS
        
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (live.body,),
        )
        database.commit()
        
        original = "Sentence at the end."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        if expected_allowed:
            result = submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
            assert result["allowed"] is True
        else:
            with pytest.raises(LinkConflict) as exc_info:
                submit_manual_weave(
                    database, 1, BASE,
                    original_sentence=original,
                    replacement_sentence=replacement,
                    fetch_fn=live.fetch,
                )
            assert "cap" in str(exc_info.value).lower() or "8" in str(exc_info.value)


class TestManualWeaveSettingGate:
    """Test case 8: Setting gate (409)."""
    
    def test_disabled_ai_woven_for_product_blocks_manual_weave(self, database, live):
        """Disabling ai_woven for products should block manual weave."""
        database.execute(
            "INSERT OR REPLACE INTO service_settings (key, value) VALUES ('internal_links_ai_woven_enabled_types', 'blog_article')"
        )
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "disabled" in str(exc_info.value).lower()


class TestManualWeaveAnchorQuality:
    """Test case 9: Anchor quality (400)."""
    
    def test_weak_single_word_anchor_rejected(self, database, live):
        """Weak single-word anchors like 'here' should be rejected."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">here</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        # Check that the gap mentions the issue
        all_gaps = " ".join(exc_info.value.gaps).lower()
        assert "generic" in all_gaps or "single word" in all_gaps


class TestManualWeaveNumbers:
    """Test case 10: Numbers outside anchor (400)."""
    
    def test_number_outside_anchor_rejected(self, database, live):
        """Numbers like '5000 puffs' outside anchor should be rejected."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} This 5000 puffs device is covered in our <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "number" in " ".join(exc_info.value.gaps).lower() or "5000" in " ".join(exc_info.value.gaps)
    
    def test_number_inside_anchor_allowed(self, database, live):
        """Numbers inside anchor like 'STLTH 60K' should be allowed."""
        # First add a product target with 60K in the name
        database.execute(
            """INSERT INTO products 
            (shopify_id, handle, title, status, online_store_url, description_html, tags_json, options_json, raw_json, synced_at)
            VALUES ('gid://shopify/Product/100', 'stlth-60k-guide', 'STLTH 60K Guide', 'ACTIVE', 
            'https://example.myshopify.com/products/stlth-60k-guide', '<p>body</p>', '[]', '[]', '{}', 'now')"""
        )
        database.execute(
            "UPDATE link_suggestions SET target_type = 'product', target_handle = 'stlth-60k-guide' WHERE id = 1"
        )
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} Check our <a href="/products/stlth-60k-guide">STLTH 60K buying guide</a>.'
        
        # Should not raise (number is inside anchor)
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        assert result["allowed"] is True


class TestManualWeaveBannedWording:
    """Test case 11: Banned wording, stock and commerce (400)."""
    
    @pytest.mark.parametrize("bad_phrase,gap_keyword", [
        ("a safer alternative to smoking", "health"),
        ("in stock now", "stock"),
        ("available now", "stock"),
        ("ships today", "stock"),
        ("limited stock", "stock"),
        ("selling fast", "stock"),
        ("wholesale pricing", "wholesale"),
    ])
    def test_banned_phrases_rejected(self, database, live, bad_phrase, gap_keyword):
        """Various banned phrases should be rejected."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} This is {bad_phrase}, see <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        with pytest.raises((ManualWeaveRejected, LinkConflict)) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        if hasattr(exc_info.value, "gaps"):
            all_gaps = " ".join(exc_info.value.gaps).lower()
            assert gap_keyword in all_gaps or bad_phrase.lower() in all_gaps


class TestManualWeaveTVPA:
    """Test case 12: TVPA flavour check (400)."""
    
    def test_tvpa_candy_category_rejected(self, database, live):
        """TVPA category terms like 'candy-like' should be rejected."""
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} This has a candy-like finish, see <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "tvpa" in " ".join(exc_info.value.gaps).lower()


class TestManualWeaveDrift:
    """Test case 13: Drift detection (409)."""
    
    def test_live_change_after_submit_fails_apply(self, database, live, api):
        """Changes to live body between submit and apply should fail."""
        client, conn, live = api
        
        # First do a successful manual-weave
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        response = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response.status_code == 200
        token = response.json()["data"]["preview_token"]
        
        # Simulate drift: change the live body
        live.body += "<p>New content added.</p>"
        
        # Apply should fail
        apply_response = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token},
        )
        assert apply_response.status_code == 409
        live.push.assert_not_called()


class TestManualWeaveHTTPRoundTrip:
    """Test case 14: HTTP round trip."""
    
    def test_manual_weave_apply_undo_flow(self, api):
        """Full HTTP flow: manual-weave, apply, undo."""
        client, conn, live = api
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our complete guide</a>.'
        
        # Manual weave
        response = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["allowed"] is True
        token = data["preview_token"]
        
        # Apply
        apply_response = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token},
        )
        assert apply_response.status_code == 200
        assert apply_response.json()["data"]["status"] == "applied"
        
        # Check applied list shows can_undo
        applied = client.get("/api/internal-links/applied").json()["data"][0]
        assert applied["can_undo"] is True
        
        # Undo
        undo_response = client.post("/api/internal-links/suggestions/1/undo")
        assert undo_response.status_code == 200
        
        # Body should be back to original
        assert live.body == SAMPLE_BODY_SIMPLE


class TestManualWeavePriorityUnchanged:
    """Test case 15: Priority unchanged."""
    
    def test_score_unchanged_after_manual_weave(self, database, live):
        """Score, weak_anchor, kind, status should be unchanged after manual-weave."""
        # Get original values
        before = database.execute(
            "SELECT score, weak_anchor, kind, status FROM link_suggestions WHERE id = 1"
        ).fetchone()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        # Get values after
        after = database.execute(
            "SELECT score, weak_anchor, kind, status FROM link_suggestions WHERE id = 1"
        ).fetchone()
        
        assert after["score"] == before["score"]
        assert after["weak_anchor"] == before["weak_anchor"]
        assert after["kind"] == before["kind"]
        assert after["status"] == before["status"]


class TestManualWeaveNoAI:
    """Test case 16: No AI calls."""
    
    def test_full_flow_no_ai_calls(self, api, no_ai_calls):
        """Full flow should never call AI."""
        client, conn, live = api
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        # Manual weave
        response = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response.status_code == 200
        token = response.json()["data"]["preview_token"]
        
        # Apply
        apply_response = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token},
        )
        assert apply_response.status_code == 200
        
        # Undo
        undo_response = client.post("/api/internal-links/suggestions/1/undo")
        assert undo_response.status_code == 200
        
        # Fixture verifies AI was never called at teardown


class TestPR46Compatibility:
    """Test case 17: #46 compatibility."""
    
    def test_validate_edit_with_empty_insert_sentence_returns_phrase_wrap(self):
        """validate_edit with empty insert_sentence should return phrase-wrap edit."""
        # This is how #46 sends empty strings
        raw = {"anchor_phrase": "ceramic tanks", "insert_sentence": "", "insert_after_text": ""}
        edit = validate_edit(raw)
        
        assert edit == {"anchor_phrase": "ceramic tanks"}
        assert "insert_sentence" not in edit
        assert "insert_after_text" not in edit
    
    def test_manual_mode_with_insert_sentence_rejected(self):
        """Manual mode combined with insert_sentence should be rejected."""
        raw = {
            "origin": "manual",
            "anchor_phrase": "test",
            "after_sentence": "Some sentence.",
            "append_text": "Added text.",
            "insert_sentence": "Should not be here.",
        }
        
        with pytest.raises(LinkConflict) as exc_info:
            validate_edit(raw)
        
        assert "cannot be combined" in str(exc_info.value).lower()


class TestManualWeaveWrongKind:
    """Test manual-weave rejects non-ai_woven suggestions."""
    
    def test_phrase_wrap_suggestion_rejected(self, database, live):
        """phrase_wrap suggestions should be rejected with 400."""
        database.execute("UPDATE link_suggestions SET kind = 'phrase_wrap' WHERE id = 1")
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">guide</a>.'
        
        with pytest.raises(ManualWeaveRejected) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "ai_woven" in str(exc_info.value).lower()


class TestManualWeaveImportCheck:
    """Verify manual_weave.py doesn't import AI modules."""
    
    def test_no_ai_weave_import(self):
        """manual_weave.py should not import ai_weave."""
        import shopifyseo.internal_links.manual_weave as mw
        import inspect
        
        source = inspect.getsource(mw)
        assert "ai_weave" not in source
        assert "providers" not in source or "dashboard_ai_engine_parts.providers" not in source


class TestManualWeaveHeadingProtection:
    """Test that sentences only in headings give zero matches (B1)."""
    
    def test_heading_only_match_gives_zero(self, database, live):
        """Sentence that only appears in an h2 should not match."""
        body_with_heading = '''<h2>This sentence is in a heading.</h2>
<p>Other content here that is different.</p>'''
        live.body = body_with_heading
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body_with_heading,),
        )
        database.commit()
        
        original = "This sentence is in a heading."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "not found" in str(exc_info.value).lower() or "heading" in str(exc_info.value).lower()


class TestManualWeavePartialSubstring:
    """Test that partial substring matches are rejected (B2)."""
    
    def test_partial_substring_rejected(self, database, live):
        """Mid-sentence substring should not match."""
        body = '<p>Before the sentence we want to match. After it.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        # Try to match a partial substring (missing the period at the end)
        original = "the sentence we want to match"  # Not a complete sentence
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        with pytest.raises((LinkConflict, ManualWeaveRejected)):
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )


class TestManualWeaveEntityHandling:
    """Test that HTML entities are handled correctly (B3)."""
    
    def test_entity_amp_handled(self, database, live):
        """Body with &amp; entity should work correctly."""
        body = '<p>This text has an ampersand &amp; more text here.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "This text has an ampersand & more text here."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        # Verify the entity is preserved and insert is in the right place
        assert "&amp;" in result["new_html"]
        assert "our guide" in result["new_html"]
    
    def test_entity_nbsp_handled(self, database, live):
        """Body with &nbsp; entity should work correctly."""
        body = '<p>This text has a non-breaking&nbsp;space here.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "This text has a non-breaking\u00a0space here."  # Use actual nbsp char
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        assert "our guide" in result["new_html"]
    
    def test_entity_mdash_handled(self, database, live):
        """Body with &mdash; entity should work correctly."""
        body = '<p>This text has an em dash&mdash;right here.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "This text has an em dash\u2014right here."  # Use actual mdash char
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        assert "our guide" in result["new_html"]
    
    def test_entity_rsquo_handled(self, database, live):
        """Body with &rsquo; entity should work correctly."""
        body = '<p>Don&rsquo;t miss this important information here.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "Don't miss this important information here."  # Use ASCII apostrophe
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        new_html = result["new_html"]
        # Verify the entity is preserved and insertion is in the right place
        assert "&rsquo;" in new_html, "Entity should be preserved"
        assert "our guide" in new_html, "Insertion should be present"
        # Verify the insertion comes after the sentence end, not inside it
        entity_idx = new_html.find("&rsquo;")
        insertion_idx = new_html.find("our guide")
        assert insertion_idx > entity_idx, "Insertion should come after the entity"
    
    def test_entity_eacute_handled(self, database, live):
        """Body with &eacute; entity (é) should work correctly."""
        body = '<p>Visit the caf&eacute; for great coffee.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "Visit the café for great coffee."  # Use actual é char
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        new_html = result["new_html"]
        insertion_idx = new_html.find("our guide")
        assert insertion_idx > 0, "Insertion not found"
    
    def test_entity_hellip_handled(self, database, live):
        """Body with &hellip; entity (…) should work correctly."""
        body = '<p>Wait&hellip; this is important information.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "Wait… this is important information."  # Use actual ellipsis char
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True
        new_html = result["new_html"]
        insertion_idx = new_html.find("our guide")
        assert insertion_idx > 0, "Insertion not found"


class TestManualWeaveSentenceBoundary:
    """Test sentence boundary detection (B2')."""
    
    def test_partial_sentence_match_rejected(self, database, live):
        """'Battery is 2.' must NOT match 'Battery is 2.5 times stronger'."""
        body = '<p>Battery is 2.5 times stronger now.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        # This should NOT match - "Battery is 2." is a partial match of "Battery is 2.5"
        original = "Battery is 2."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        # Should get "not found" error since it's not a valid sentence match
        assert "not found" in str(exc_info.value).lower() or "sentence" in str(exc_info.value).lower()
    
    def test_valid_sentence_boundary_accepted(self, database, live):
        """A real sentence boundary should be accepted."""
        body = '<p>Battery is 2. The new model is better.</p>'
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        # This should match - "Battery is 2." is a complete sentence
        original = "Battery is 2."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        assert result["allowed"] is True


class TestManualWeaveOnlineStoreUrlNull:
    """Test that online_store_url=NULL products are NOT linkable (B6)."""
    
    def test_null_online_store_url_rejected(self, database, live):
        """Product with NULL online_store_url should be rejected as target."""
        # Change target to a product with NULL online_store_url
        database.execute("""
            INSERT INTO products 
            (shopify_id, handle, title, status, online_store_url, description_html, tags_json, options_json, raw_json, synced_at)
            VALUES ('gid://shopify/Product/999', 'null-url-product', 'Null URL Product', 'ACTIVE', 
            NULL, '<p>Description</p>', '[]', '[]', '{}', 'now')
        """)
        database.execute("""
            UPDATE link_suggestions SET target_type = 'product', target_handle = 'null-url-product' WHERE id = 1
        """)
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/products/null-url-product">Null URL Product</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "online store url" in str(exc_info.value).lower()
    
    def test_empty_online_store_url_rejected(self, database, live):
        """Product with empty string online_store_url should be rejected as target."""
        # Change target to a product with empty online_store_url
        database.execute("""
            INSERT INTO products 
            (shopify_id, handle, title, status, online_store_url, description_html, tags_json, options_json, raw_json, synced_at)
            VALUES ('gid://shopify/Product/998', 'empty-url-product', 'Empty URL Product', 'ACTIVE', 
            '', '<p>Description</p>', '[]', '[]', '{}', 'now')
        """)
        database.execute("""
            UPDATE link_suggestions SET target_type = 'product', target_handle = 'empty-url-product' WHERE id = 1
        """)
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/products/empty-url-product">Empty URL Product</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        # Empty string fails the general linkability check (is_product_linkable rejects empty URLs)
        error_msg = str(exc_info.value).lower()
        assert "no longer eligible" in error_msg or "online store url" in error_msg


class TestManualWeaveInventoryNotRequired:
    """Test that inventory/stock is never checked (products always linkable if ACTIVE)."""
    
    def test_zero_inventory_product_linkable(self, database, live):
        """Product with zero inventory should still be linkable."""
        # Create a product target with zero inventory but valid online_store_url
        database.execute("""
            INSERT INTO products 
            (shopify_id, handle, title, status, online_store_url, description_html, tags_json, options_json, raw_json, synced_at, total_inventory)
            VALUES ('gid://shopify/Product/997', 'zero-stock-product', 'Zero Stock Product', 'ACTIVE', 
            '/products/zero-stock-product', '<p>Description</p>', '[]', '[]', '{}', 'now', 0)
        """)
        database.execute("""
            UPDATE link_suggestions SET target_type = 'product', target_handle = 'zero-stock-product' WHERE id = 1
        """)
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/products/zero-stock-product">Zero Stock Product</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        # Stock/inventory is never checked - only status and online_store_url matter
        assert result["allowed"] is True


class TestManualWeaveApiUnreachable:
    """Test handling of api_unreachable collections."""
    
    def test_api_unreachable_collection_rejected(self, database, live):
        """Collection with api_unreachable=1 should be rejected as target."""
        database.execute("""
            INSERT INTO collections 
            (shopify_id, handle, title, description_html, raw_json, synced_at, api_unreachable)
            VALUES ('gid://shopify/Collection/999', 'unreachable-collection', 'Unreachable Collection', 
            '<p>Description</p>', '{}', 'now', 1)
        """)
        database.execute("""
            UPDATE link_suggestions SET target_type = 'collection', target_handle = 'unreachable-collection' WHERE id = 1
        """)
        database.commit()
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/collections/unreachable-collection">our collection</a>.'
        
        with pytest.raises(LinkConflict) as exc_info:
            submit_manual_weave(
                database, 1, BASE,
                original_sentence=original,
                replacement_sentence=replacement,
                fetch_fn=live.fetch,
            )
        
        assert "no longer eligible" in str(exc_info.value).lower()


class TestManualWeaveCapRecheckedAtApply:
    """Test that 8-link cap is re-checked at apply time."""
    
    def test_cap_recheck_at_apply(self, api):
        """If body changes to have 8 links after submit, apply should fail."""
        client, conn, live = api
        
        # Start with 7 links in body
        body_7_links = '''<p>Links:
<a href="/a">A</a>, <a href="/b">B</a>, <a href="/c">C</a>,
<a href="/d">D</a>, <a href="/e">E</a>, <a href="/f">F</a>,
<a href="/g">G</a>. Health Canada has strict protocols.</p>'''
        live.body = body_7_links
        
        original = "Health Canada has strict protocols."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        # Submit should succeed (7 + 1 = 8, within cap)
        response = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response.status_code == 200
        token = response.json()["data"]["preview_token"]
        
        # Now change live body to have 8 links (cap would be exceeded)
        body_8_links = '''<p>Links:
<a href="/a">A</a>, <a href="/b">B</a>, <a href="/c">C</a>,
<a href="/d">D</a>, <a href="/e">E</a>, <a href="/f">F</a>,
<a href="/g">G</a>, <a href="/h">H</a>. Health Canada has strict protocols.</p>'''
        live.body = body_8_links
        
        # Apply should fail because body changed (drift) or cap exceeded
        apply_response = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token},
        )
        # Should fail - either 409 for drift/cap or the response includes error info
        assert apply_response.status_code in (400, 409)
        response_text = str(apply_response.json()).lower()
        assert "cap" in response_text or "drift" in response_text or "changed" in response_text


class TestManualWeavePendingSnapshot:
    """Test that applied suggestion blocks new submit."""
    
    def test_applied_suggestion_blocks_new_submit(self, api):
        """If suggestion is already applied, new submit should fail."""
        client, conn, live = api
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        # First submit and apply
        response1 = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response1.status_code == 200
        token1 = response1.json()["data"]["preview_token"]
        
        apply_response = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token1},
        )
        assert apply_response.status_code == 200
        
        # Now status is 'applied' - new submit should fail
        response2 = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response2.status_code == 409
        response_text = str(response2.json()).lower()
        assert "applied" in response_text or "status" in response_text or "refresh" in response_text
    
    def test_pending_snapshot_blocks_submit(self, api):
        """If a pending snapshot exists (status='prepared'), new submit should fail."""
        client, conn, live = api
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        # Get current ai_edit_json before inserting snapshot
        row_before = conn.execute("SELECT ai_edit_json FROM link_suggestions WHERE id = 1").fetchone()
        edit_json_before = row_before["ai_edit_json"] if row_before else None
        
        # Get source info from suggestion for the snapshot
        sug = conn.execute("SELECT source_type, source_handle FROM link_suggestions WHERE id = 1").fetchone()
        source_type = sug["source_type"]
        source_handle = sug["source_handle"]
        
        # Insert a pending snapshot manually using correct schema columns
        import time
        conn.execute("""
            INSERT INTO link_body_snapshots 
            (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at)
            VALUES (1, ?, ?, 'gid://shopify/Product/1', '<p>old</p>', '<p>new</p>', 'prepared', ?, ?)
        """, (source_type, source_handle, int(time.time()), int(time.time())))
        conn.commit()
        
        # Submit should fail with 409 due to pending snapshot
        response = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response.status_code == 409
        response_text = str(response.json()).lower()
        assert "in progress" in response_text or "reconciliation" in response_text
        
        # Verify ai_edit_json unchanged
        row_after = conn.execute("SELECT ai_edit_json FROM link_suggestions WHERE id = 1").fetchone()
        edit_json_after = row_after["ai_edit_json"] if row_after else None
        assert edit_json_before == edit_json_after


class TestManualWeaveDismiss:
    """Test dismiss after manual-weave."""
    
    def test_dismiss_after_submit(self, api):
        """Dismissing after manual-weave submit should work."""
        client, conn, live = api
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        # Submit manual-weave
        response = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement},
        )
        assert response.status_code == 200
        
        # Dismiss (using existing dismiss endpoint)
        dismiss_response = client.post("/api/internal-links/suggestions/1/dismiss")
        assert dismiss_response.status_code == 200
        
        # Verify status changed
        row = conn.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert row["status"] == "dismissed"


class TestManualWeaveTokenResubmit:
    """Test that old token fails after re-submit."""
    
    def test_old_token_fails_after_resubmit(self, api):
        """After re-submitting, old token should fail."""
        client, conn, live = api
        
        original = "Health Canada has strict protocols for authorizing nicotine pouches, and Zyn pouches do not hold the required market authorization for legal sale in Canada."
        replacement1 = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        replacement2 = f'{original} Check <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">the full article</a>.'
        
        # First submit
        response1 = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement1},
        )
        assert response1.status_code == 200
        token1 = response1.json()["data"]["preview_token"]
        
        # Second submit with different anchor
        response2 = client.post(
            "/api/internal-links/suggestions/1/manual-weave",
            json={"original_sentence": original, "replacement_sentence": replacement2},
        )
        assert response2.status_code == 200
        token2 = response2.json()["data"]["preview_token"]
        
        # Old token should fail
        apply_response1 = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token1},
        )
        assert apply_response1.status_code in (400, 409)
        
        # New token should succeed
        apply_response2 = client.post(
            "/api/internal-links/suggestions/1/apply",
            json={"preview_token": token2},
        )
        assert apply_response2.status_code == 200


class TestManualWeaveMixedLinks:
    """Test body with mixed internal/external links - all are counted toward cap."""
    
    def test_all_links_counted_toward_cap(self, database, live):
        """ALL links (internal and external) count toward the 8-link cap."""
        # 6 internal + 1 external = 7 links, so adding 1 more = 8 (at cap)
        body_mixed = '''<p>External <a href="https://google.com">Google</a> and internal
<a href="/products/a">A</a>, <a href="/products/b">B</a>, <a href="/products/c">C</a>,
<a href="/products/d">D</a>, <a href="/products/e">E</a>, <a href="/products/f">F</a>.
Health Canada has strict protocols.</p>'''
        live.body = body_mixed
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body_mixed,),
        )
        database.commit()
        
        original = "Health Canada has strict protocols."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        # Should succeed - 7 existing + 1 new = 8, at cap
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        # All <a> tags are counted (internal + external)
        assert result["existing_link_count"] == 7
        assert result["allowed"] is True


class TestManualWeaveLinkWithoutHref:
    """Test that <a> without href is not counted as a link."""
    
    def test_anchor_without_href_not_counted(self, database, live):
        """<a> tags without href (bookmarks) should NOT count toward link cap."""
        body = '''<p>An anchor <a name="bookmark">bookmark</a> here.
<a href="/products/a">A</a>. Health Canada has strict protocols.</p>'''
        live.body = body
        database.execute(
            "UPDATE products SET description_html = ? WHERE handle = 'zyn-nicotine-pouches-canada-shopper'",
            (body,),
        )
        database.commit()
        
        original = "Health Canada has strict protocols."
        replacement = f'{original} See <a href="/blogs/canada/zyn-canada-nicotine-pouch-availability">our guide</a>.'
        
        result = submit_manual_weave(
            database, 1, BASE,
            original_sentence=original,
            replacement_sentence=replacement,
            fetch_fn=live.fetch,
        )
        
        # Only <a> with href counts - bookmark anchors don't
        assert result["existing_link_count"] == 1
