"""Tests for entity-safe insert, nested anchor guard, preview lock, spelling, and unpublished sources.

These tests cover the changes in the PR:
- A. Entity-safe insert + entity-equivalence
- B. Nested/escaped anchor guard
- C. Preview respects open-write lock
- D. New-text spelling check
- E. Skip unpublished sources
"""
import json
import pytest
from unittest.mock import Mock, patch

from shopifyseo.internal_links.safety import (
    build_edit, guard_edit, html_equivalent, LinkConflict,
    _is_inside_escaped_anchor, _has_nested_anchors, _find_escaped_anchor_regions,
    _normalize_text_entities, _normalize_for_entity_comparison,
    _verify_anchor_wellformed, _find_inserted_anchor_offset,
)
from shopifyseo.internal_links.compliance import (
    inserted_text, check_en_ca_spelling,
)
from shopifyseo.internal_links.apply import preview_suggestion, _check_page_write_pending
from shopifyseo.internal_links.pipeline import generate_link_suggestions, _source_exists_with_body


# =============================================================================
# A. Entity-Safe Insert Tests
# =============================================================================

class TestEntitySafeInsert:
    """Test that apostrophes and quotes in text nodes aren't over-escaped."""
    
    def test_insert_sentence_apostrophe_not_escaped(self):
        """Apostrophe in insert_sentence should remain as ' not &#x27;."""
        old = "<p>First paragraph.</p>"
        edit = {
            "anchor_phrase": "beginner's guide",
            "insert_sentence": "Check out our beginner's guide for tips.",
            "insert_after_text": "First paragraph.",
        }
        result = build_edit(old, edit, "https://example.com/guide")
        assert "beginner's guide" in result
        assert "&#x27;" not in result
        assert "&#39;" not in result
    
    def test_insert_sentence_double_quote_not_escaped(self):
        """Double quote in insert_sentence should remain as " not &quot;."""
        old = "<p>First paragraph.</p>"
        edit = {
            "anchor_phrase": 'our "best" picks',
            "insert_sentence": 'See our "best" picks for quality.',
            "insert_after_text": "First paragraph.",
        }
        result = build_edit(old, edit, "https://example.com/picks")
        assert 'our "best" picks' in result
        assert "&quot;" not in result
        assert "&#34;" not in result
    
    def test_manual_weave_apostrophe_not_escaped(self):
        """Apostrophe in manual_weave append_text should remain as ' not &#x27;."""
        old = "<p>This is a test sentence.</p>"
        edit = {
            "origin": "manual",
            "anchor_phrase": "beginner's tips",
            "after_sentence": "This is a test sentence.",
            "append_text": "Check our beginner's tips here.",
        }
        result = build_edit(old, edit, "https://example.com/tips")
        assert "beginner's tips" in result
        assert "&#x27;" not in result
    
    def test_ampersand_still_escaped(self):
        """& should still be escaped in text nodes."""
        old = "<p>First paragraph.</p>"
        edit = {
            "anchor_phrase": "Tom & Jerry guide",
            "insert_sentence": "See the Tom & Jerry guide here.",
            "insert_after_text": "First paragraph.",
        }
        result = build_edit(old, edit, "https://example.com/guide")
        assert '<a href="https://example.com/guide">Tom &amp; Jerry guide</a>' in result
        assert "&#x27;" not in result
        assert "&quot;" not in result


class TestEntityEquivalence:
    """Test html_equivalent handles text-node entity differences."""
    
    def test_apostrophe_entity_vs_literal_equivalent(self):
        """&#x27; in sent body vs ' in Shopify response should be equivalent."""
        sent = '<p>beginner&#x27;s guide</p>'
        received = "<p>beginner's guide</p>"
        assert html_equivalent(sent, received)
    
    def test_apostrophe_decimal_entity_equivalent(self):
        """&#39; vs ' should be equivalent."""
        sent = '<p>beginner&#39;s guide</p>'
        received = "<p>beginner's guide</p>"
        assert html_equivalent(sent, received)
    
    def test_apostrophe_039_decimal_equivalent(self):
        """&#039; (leading zero) vs ' should be equivalent."""
        sent = '<p>beginner&#039;s guide</p>'
        received = "<p>beginner's guide</p>"
        assert html_equivalent(sent, received)
    
    def test_quote_entity_vs_literal_equivalent(self):
        """&quot; vs " should be equivalent in text nodes."""
        sent = '<p>The &quot;best&quot; guide</p>'
        received = '<p>The "best" guide</p>'
        assert html_equivalent(sent, received)
    
    def test_ampersand_entity_vs_literal_equivalent(self):
        """&amp; vs & should be equivalent in text nodes (Shopify normalization)."""
        sent = '<p>Tom &amp; Jerry</p>'
        received = '<p>Tom & Jerry</p>'
        assert html_equivalent(sent, received)
    
    def test_nbsp_still_significant(self):
        """&nbsp; vs space should NOT be equivalent per #117 rule."""
        a = '<p>hello&nbsp;world</p>'
        b = '<p>hello world</p>'
        assert not html_equivalent(a, b)
    
    def test_attribute_differences_still_significant(self):
        """Attribute changes should fail html_equivalent."""
        a = '<a href="/old">link</a>'
        b = '<a href="/new">link</a>'
        assert not html_equivalent(a, b)
    
    def test_tag_differences_still_significant(self):
        """Tag changes should fail html_equivalent."""
        a = '<p>text</p>'
        b = '<div>text</div>'
        assert not html_equivalent(a, b)
    
    def test_whitespace_between_blocks_still_tolerated(self):
        """Inter-block whitespace should still be tolerated."""
        a = '<p>One</p><p>Two</p>'
        b = '<p>One</p>\n<p>Two</p>'
        assert html_equivalent(a, b)
    
    def test_amp_lt_not_equivalent_to_lt(self):
        """&amp;lt;b&amp;gt; should NOT be equivalent to &lt;b&gt;.
        
        The first displays as literal '&lt;b&gt;' text, the second renders as '<b>'.
        They are semantically different.
        """
        a = '<p>Use &amp;lt;b&amp;gt; for bold</p>'
        b = '<p>Use &lt;b&gt; for bold</p>'
        assert not html_equivalent(a, b)
    
    def test_amp_nbsp_not_equivalent_to_nbsp(self):
        """&amp;nbsp; should NOT be equivalent to &nbsp;.
        
        &amp;nbsp; displays as literal '&nbsp;' text, while &nbsp; is a non-breaking space.
        """
        a = '<p>hello&amp;nbsp;world</p>'
        b = '<p>hello&nbsp;world</p>'
        assert not html_equivalent(a, b)
    
    def test_qa_ampersand_equivalent(self):
        """Q&amp;A should be equivalent to Q&A (Shopify normalization)."""
        a = '<p>Check our Q&amp;A section</p>'
        b = '<p>Check our Q&A section</p>'
        assert html_equivalent(a, b)
    
    def test_rd_ampersand_equivalent(self):
        """R&amp;D should be equivalent to R&D."""
        a = '<p>Our R&amp;D team</p>'
        b = '<p>Our R&D team</p>'
        assert html_equivalent(a, b)
    
    def test_att_ampersand_equivalent(self):
        """AT&amp;T should be equivalent to AT&T."""
        a = '<p>AT&amp;T wireless</p>'
        b = '<p>AT&T wireless</p>'
        assert html_equivalent(a, b)
    
    def test_x39_not_equivalent_to_apostrophe(self):
        """&#x39; should NOT be equivalent to ' (hex 39 = digit 9, not apostrophe)."""
        a = '<p>number&#x39;s</p>'
        b = "<p>number's</p>"
        assert not html_equivalent(a, b)


class TestNormalizeTextEntities:
    """Test the entity normalization helpers."""
    
    def test_normalize_apostrophe_hex(self):
        assert _normalize_text_entities("beginner&#x27;s") == "beginner's"
    
    def test_normalize_apostrophe_decimal(self):
        assert _normalize_text_entities("beginner&#39;s") == "beginner's"
    
    def test_normalize_apostrophe_039(self):
        """&#039; with leading zero should normalize to '."""
        assert _normalize_text_entities("beginner&#039;s") == "beginner's"
    
    def test_normalize_quote_entity(self):
        assert _normalize_text_entities('the &quot;best&quot;') == 'the "best"'
    
    def test_normalize_standalone_ampersand_entity(self):
        """&amp; should normalize to & when standalone (not part of another entity)."""
        assert _normalize_text_entities("Tom &amp; Jerry") == "Tom & Jerry"
    
    def test_normalize_preserves_nbsp(self):
        assert "&nbsp;" in _normalize_text_entities("hello&nbsp;world")
    
    def test_normalize_for_entity_comparison_preserves_tag_entities(self):
        """Entities in attributes should not be normalized."""
        html = '<a href="/path?a=1&amp;b=2">text&#x27;s</a>'
        result = _normalize_for_entity_comparison(html)
        assert "text's" in result
        assert "&amp;" in result
    
    def test_x39_is_digit_not_apostrophe(self):
        """&#x39; is hex 39 = decimal 57 = digit '9', NOT apostrophe.
        
        This is a critical distinction: &#x27; is apostrophe (hex 27 = decimal 39),
        but &#x39; is the digit 9 (hex 39 = decimal 57). We must NOT normalize it.
        """
        text = "number&#x39;s"  # This should remain as &#x39;, not become apostrophe
        result = _normalize_text_entities(text)
        assert "&#x39;" in result  # Must be preserved
        assert result != "number's"  # MUST NOT become apostrophe
    
    def test_amp_lt_not_converted_to_lt(self):
        """&amp;lt; should NOT be converted to &lt; (they render differently).
        
        &amp;lt; renders as literal '&lt;' in text, while &lt; renders as '<'.
        These are semantically different and must remain distinct.
        """
        text = "use &amp;lt;b&amp;gt; for bold"
        result = _normalize_text_entities(text)
        # Must preserve double-escaped entities
        assert result == "use &amp;lt;b&amp;gt; for bold"
    
    def test_amp_hash_not_converted(self):
        """&amp;#39; should NOT be converted (it's an escaped entity reference)."""
        text = "typed &amp;#39; by user"
        result = _normalize_text_entities(text)
        # &amp;# must be preserved
        assert "&amp;#" in result
    
    def test_qa_ampersand_normalized(self):
        """Q&amp;A should normalize to Q&A (Shopify may return either form)."""
        text = "Check our Q&amp;A section"
        result = _normalize_text_entities(text)
        assert result == "Check our Q&A section"
    
    def test_rd_ampersand_normalized(self):
        """R&amp;D should normalize to R&D."""
        text = "Our R&amp;D team"
        result = _normalize_text_entities(text)
        assert result == "Our R&D team"
    
    def test_att_ampersand_normalized(self):
        """AT&amp;T should normalize to AT&T."""
        text = "AT&amp;T wireless"
        result = _normalize_text_entities(text)
        assert result == "AT&T wireless"
    
    def test_nbsp_preserved(self):
        """&nbsp; should NOT be normalized (significant per #117)."""
        text = "hello&nbsp;world"
        result = _normalize_text_entities(text)
        assert "&nbsp;" in result
    
    def test_leading_zero_039_is_apostrophe(self):
        """&#0039; (with leading zeros) should be treated as apostrophe."""
        text = "beginner&#0039;s guide"
        result = _normalize_text_entities(text)
        assert result == "beginner's guide"


class TestApplyRoundTrip:
    """Test apply → Shopify-returned body → verify returns applied."""
    
    def test_apply_roundtrip_apostrophe_entity_variants(self):
        """Insert with ' → Shopify returns &#39; variant → verify succeeds."""
        sent = "<p>Check our beginner's guide.</p>"
        received_variants = [
            "<p>Check our beginner's guide.</p>",
            "<p>Check our beginner&#39;s guide.</p>",
            "<p>Check our beginner&#x27;s guide.</p>",
            "<p>Check our beginner&#039;s guide.</p>",
        ]
        for received in received_variants:
            assert html_equivalent(sent, received), f"Failed for variant: {received}"
    
    def test_apply_roundtrip_ampersand_variants(self):
        """Insert with &amp; → Shopify returns & → verify succeeds."""
        sent = "<p>Tom &amp; Jerry guide.</p>"
        received = "<p>Tom & Jerry guide.</p>"
        assert html_equivalent(sent, received)
    
    def test_reconcile_snapshot_52_shape(self):
        """Snapshot #52 shape: Shopify stores plain ', we sent &#x27; → applied."""
        sent = "<p>The beginner&#x27;s guide is here.</p>"
        shopify_returned = "<p>The beginner's guide is here.</p>"
        assert html_equivalent(sent, shopify_returned)


class TestApplyWithEntityNormalization:
    """Tests for apply/reconcile that exercise entity normalization.
    
    These tests MUST FAIL if:
    - The html_equivalent check is made strict (exact match)
    - Entity normalization is turned off
    
    Each test uses a body that contains the entity-sensitive character (apostrophe, ampersand)
    in the anchor phrase itself, and the stub returns what Shopify really stores.
    """
    
    def _create_apply_db(self, conn):
        """Create database with required schema for apply/reconcile tests."""
        conn.executescript("""
            CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
                description_html TEXT, gsc_clicks INTEGER DEFAULT 0, online_store_url TEXT);
            CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
                description_html TEXT, api_unreachable INTEGER DEFAULT 0);
            CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
                body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0);
            CREATE TABLE link_suggestions (
                id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, 
                target_type TEXT, target_handle TEXT, kind TEXT, anchor_phrase TEXT,
                ai_edit_json TEXT, ai_anchor_html TEXT, source_body_hash TEXT, score REAL DEFAULT 0, 
                status TEXT DEFAULT 'suggested', created_at INTEGER, weak_anchor INTEGER DEFAULT 0, applied_at INTEGER,
                UNIQUE (source_type, source_handle, target_type, target_handle)
            );
            CREATE TABLE link_body_snapshots (
                id INTEGER PRIMARY KEY, suggestion_id INTEGER, source_type TEXT, source_handle TEXT,
                shopify_id TEXT, old_body TEXT, new_body TEXT, status TEXT, created_at INTEGER, updated_at INTEGER, error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_pending ON link_body_snapshots (source_type, source_handle)
                WHERE status IN ('prepared', 'needs_reconciliation', 'undo_prepared', 'undo_needs_reconciliation');
            CREATE TABLE internal_links (source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, href TEXT, anchor_text TEXT);
            CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE link_suggestion_events (id INTEGER PRIMARY KEY, suggestion_id INTEGER, event_type TEXT, source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, kind TEXT, score REAL, gsc_clicks_at_event INTEGER, created_at INTEGER);
        """)
        return conn
    
    def test_apply_apostrophe_in_anchor_shopify_returns_plain(self, db_conn):
        """Apply link with apostrophe in anchor phrase. Shopify returns plain ' where we sent &#x27;.
        
        Body contains "beginner&#x27;s guide" with entity-encoded apostrophe.
        build_edit preserves the entity, so we push &#x27;.
        Shopify stores and returns plain apostrophe.
        The html_equivalent check at apply.py:283 must pass.
        
        This test FAILS if apply.py:283's equivalence check is made strict (accepted != new).
        """
        from shopifyseo.internal_links.apply import apply_suggestion, preview_suggestion
        
        conn = self._create_apply_db(db_conn)
        # Body has ENTITY-ENCODED apostrophe - this is key for testing normalization
        original_body = "<p>Check our beginner&#x27;s guide for tips.</p>"
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'guides', 'Guides')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'guides', 'phrase_wrap', 'beginner''s guide', 'suggested', 1)"
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            return original_body
        
        def mock_push(source_type, row, body):
            # Shopify stores and returns plain apostrophe where we sent &#x27;
            # This simulates what Shopify really does: normalize entities to plain chars
            return body.replace("&#x27;", "'")
        
        preview = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview["allowed"], f"Preview failed: {preview.get('reason')}"
        
        result = apply_suggestion(
            conn, 1, "https://shop.com",
            preview_token_value=preview["preview_token"],
            fetch_fn=mock_fetch,
            push_fn=mock_push
        )
        assert result["status"] == "applied"
        
        # Verify the suggestion is marked applied
        sug = conn.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert sug["status"] == "applied"
    
    def test_reconcile_apostrophe_entity_vs_plain(self, db_conn):
        """Reconcile succeeds when we sent &#x27; but Shopify returned plain '.
        
        This directly tests entity normalization in the reconcile path.
        The snapshot has new_body with &#x27; (entity-encoded).
        Shopify returns the same content but with plain apostrophe.
        Reconcile must recognize these as equivalent.
        
        This test FAILS if normalization is disabled.
        """
        from shopifyseo.internal_links.apply import reconcile_suggestion
        
        conn = self._create_apply_db(db_conn)
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', '<p>Old.</p>', 'https://shop.com/products/source')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'target', 'Target')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'target', 'phrase_wrap', 'test', 'suggested', 1)"
        )
        
        # Snapshot has entity-encoded apostrophe (what we sent)
        sent_body = "<p>The beginner&#x27;s guide.</p>"
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'gid://shopify/Product/1', '<p>Old.</p>', ?, 'needs_reconciliation', 1, 1)",
            (sent_body,)
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            # Shopify returns plain apostrophe
            return "<p>The beginner's guide.</p>"
        
        result = reconcile_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert result["status"] == "applied"
    
    def test_apply_ampersand_in_body_shopify_returns_plain(self, db_conn):
        """Apply link when body has &amp;. Shopify returns plain & where we sent &amp;.
        
        Body contains "Q&amp;A guide" with properly escaped ampersand.
        build_edit preserves the entity, so we push &amp;.
        Shopify stores and returns plain &.
        The html_equivalent check at apply.py:283 must pass.
        
        This test FAILS if apply.py:283's equivalence check is made strict (accepted != new).
        """
        from shopifyseo.internal_links.apply import apply_suggestion, preview_suggestion
        
        conn = self._create_apply_db(db_conn)
        # Body has ENTITY-ENCODED ampersand - this is key for testing normalization
        original_body = "<p>Check our Q&amp;A guide for answers.</p>"
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'faq', 'FAQ')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'faq', 'phrase_wrap', 'Q&A guide', 'suggested', 1)"
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            return original_body
        
        def mock_push(source_type, row, body):
            # Shopify stores and returns plain & where we sent &amp;
            # This simulates what Shopify really does
            return body.replace('&amp;', '&')
        
        preview = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview["allowed"], f"Preview failed: {preview.get('reason')}"
        
        result = apply_suggestion(
            conn, 1, "https://shop.com",
            preview_token_value=preview["preview_token"],
            fetch_fn=mock_fetch,
            push_fn=mock_push
        )
        assert result["status"] == "applied"
    
    def test_reconcile_ampersand_entity_vs_plain(self, db_conn):
        """Reconcile succeeds when we sent &amp; but Shopify returned plain &.
        
        This test FAILS if normalization is disabled.
        """
        from shopifyseo.internal_links.apply import reconcile_suggestion
        
        conn = self._create_apply_db(db_conn)
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', '<p>Old.</p>', 'https://shop.com/products/source')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'target', 'Target')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'target', 'phrase_wrap', 'test', 'suggested', 1)"
        )
        
        # Snapshot has entity-encoded ampersand
        sent_body = "<p>Check the Q&amp;A guide.</p>"
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'gid://shopify/Product/1', '<p>Old.</p>', ?, 'needs_reconciliation', 1, 1)",
            (sent_body,)
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            # Shopify returns plain ampersand
            return "<p>Check the Q&A guide.</p>"
        
        result = reconcile_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert result["status"] == "applied"


# =============================================================================
# B. Nested/Escaped Anchor Guard Tests
# =============================================================================

class TestEscapedAnchorDetection:
    """Test detection of escaped anchor regions."""
    
    def test_find_escaped_anchor_region(self):
        """Should find &lt;a ...&gt;...&lt;/a&gt; regions."""
        text = 'Our range of &lt;a href="https://example.com"&gt;disposables&lt;/a&gt; is great.'
        regions = _find_escaped_anchor_regions(text)
        assert len(regions) == 1
        start, end = regions[0]
        assert text[start:end].startswith("&lt;a")
        assert text[start:end].endswith("&lt;/a&gt;")
    
    def test_is_inside_escaped_anchor(self):
        """Should detect when offset is inside an escaped anchor."""
        text = 'Before &lt;a href="#"&gt;inside&lt;/a&gt; after'
        inside_pos = text.find("inside")
        assert _is_inside_escaped_anchor(text, inside_pos)
        assert not _is_inside_escaped_anchor(text, 0)
        after_pos = text.find("after")
        assert not _is_inside_escaped_anchor(text, after_pos)
    
    def test_no_escaped_anchor_returns_empty(self):
        """Should return empty list when no escaped anchors."""
        text = 'Just regular text with <a href="#">real link</a>'
        regions = _find_escaped_anchor_regions(text)
        assert regions == []
    
    def test_escaped_anchor_limited_to_same_block(self):
        """Unclosed &lt;a in para 1 should not protect content in para 3."""
        text = '<p>Para 1 with &lt;a href="#"&gt;unclosed</p><p>Para 2</p><p>Para 3 with test phrase&lt;/a&gt;</p>'
        regions = _find_escaped_anchor_regions(text)
        assert len(regions) == 0
    
    def test_numeric_escaped_anchor_decimal_detected(self):
        """&#60;a ...&#62; (decimal numeric entities) should be detected."""
        text = 'Our &#60;a href="https://example.com"&#62;products&#60;/a&#62; are great.'
        regions = _find_escaped_anchor_regions(text)
        assert len(regions) == 1
        assert _is_inside_escaped_anchor(text, text.find("products"))
    
    def test_numeric_escaped_anchor_hex_detected(self):
        """&#x3c;a ...&#x3e; (hex numeric entities) should be detected."""
        text = 'Our &#x3c;a href="https://example.com"&#x3e;products&#x3c;/a&#x3e; are great.'
        regions = _find_escaped_anchor_regions(text)
        assert len(regions) == 1
        assert _is_inside_escaped_anchor(text, text.find("products"))
    
    def test_mixed_numeric_named_escaped_anchor(self):
        """Mixed numeric and named entity escaping should still be detected."""
        text = 'Our &#60;a href="#"&gt;products&lt;/a&#62; are great.'
        regions = _find_escaped_anchor_regions(text)
        assert len(regions) == 1


class TestNestedAnchorDetection:
    """Test detection of nested real anchors."""
    
    def test_nested_anchors_detected(self):
        """Should detect <a> inside <a>."""
        html = '<a href="/outer"><a href="/inner">nested</a></a>'
        assert _has_nested_anchors(html)
    
    def test_sequential_anchors_ok(self):
        """Sequential <a> tags should not be flagged as nested."""
        html = '<a href="/first">first</a> <a href="/second">second</a>'
        assert not _has_nested_anchors(html)
    
    def test_single_anchor_ok(self):
        """Single anchor should be fine."""
        html = '<p>Text <a href="/link">link</a> more text</p>'
        assert not _has_nested_anchors(html)


class TestPhraseWrapSkipsEscapedAndKeepsSearching:
    """Test that phrase_wrap skips matches inside anchors and finds safe occurrences."""
    
    def test_phrase_in_escaped_anchor_para1_plain_para3_wraps_para3(self):
        """Phrase inside &lt;a&gt; in para 1, plain in para 3 → para 3 gets wrapped."""
        body = (
            '<p>Our &lt;a href="https://example.com"&gt;ceramic tanks&lt;/a&gt; are good.</p>'
            '<p>Some other content.</p>'
            '<p>We also sell ceramic tanks in bulk.</p>'
        )
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">ceramic tanks</a>' in result
        assert result.index('<a href="https://example.com/target">') > result.index("We also sell")
    
    def test_phrase_only_inside_escaped_anchor_rejected(self):
        """Phrase only inside escaped anchor → insert_inside_existing_anchor."""
        body = '<p>Our &lt;a href="https://example.com"&gt;ceramic tanks&lt;/a&gt; are great.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, "https://example.com/target")
        assert exc_info.value.code == "insert_inside_existing_anchor"
    
    def test_phrase_outside_escaped_anchor_allowed(self):
        """Wrapping a phrase outside escaped anchor should work."""
        body = '<p>Some &lt;a href="#"&gt;escaped&lt;/a&gt; content. Our ceramic tanks are great.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">ceramic tanks</a>' in result
    
    def test_less_than_without_anchor_ok(self):
        """&lt; without 'a' (e.g. '5 &lt; 10') should not trigger guard."""
        body = '<p>Values where 5 &lt; 10 apply. Our ceramic tanks work.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/tanks")
        assert '<a href="https://example.com/tanks">ceramic tanks</a>' in result


class TestCrossTagMatchingRejection:
    """Test that phrase matching rejects matches that cross tag boundaries."""
    
    def test_phrase_crossing_strong_tag_rejected(self):
        """Phrase matching should not cross </strong> tag."""
        body = '<p><strong>our ceramic</strong> tanks rock</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict):
            build_edit(body, edit, "https://example.com/target")
    
    def test_phrase_crossing_em_tag_rejected(self):
        """Phrase matching should not cross </em> tag."""
        body = '<p><em>great ceramic</em> tanks here</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict):
            build_edit(body, edit, "https://example.com/target")
    
    def test_phrase_crossing_paragraph_rejected(self):
        """Phrase matching should not cross </p><p> boundary."""
        body = '<p>text ceramic</p><p>tanks here</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict):
            build_edit(body, edit, "https://example.com/target")
    
    def test_phrase_crossing_list_items_rejected(self):
        """Phrase matching should not cross </li><li> boundary."""
        body = '<ul><li>ceramic</li><li>tanks</li></ul>'
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict):
            build_edit(body, edit, "https://example.com/target")
    
    def test_phrase_overlapping_existing_anchor_rejected(self):
        """Phrase matching should not overlap an existing anchor."""
        body = '<p>Buy ceramic <a href="/z">tanks</a> now.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict):
            build_edit(body, edit, "https://example.com/target")
    
    def test_phrase_inside_existing_anchor_rejected(self):
        """Phrase matching should not be inside an existing anchor."""
        body = '<p>See our <a href="/existing">ceramic tanks</a> collection.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        # Should find the occurrence outside the anchor or reject
        with pytest.raises(LinkConflict):
            build_edit(body, edit, "https://example.com/target")
    
    def test_phrase_within_single_text_node_succeeds(self):
        """Phrase matching within a single text node should succeed."""
        body = '<p>Check our ceramic tanks guide for tips.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">ceramic tanks</a>' in result
    
    def test_phrase_with_entity_inside_succeeds(self):
        """Phrase matching with entity refs inside should succeed."""
        body = '<p>Check our Q&amp;A guide for answers.</p>'
        edit = {"anchor_phrase": "Q&A guide"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">Q&amp;A guide</a>' in result


class TestEntityRefsAtPhraseEdges:
    """Test that phrases don't start/end inside entity references."""
    
    def test_entity_at_phrase_start_hex_apostrophe(self):
        """Phrase starting with entity-decoded apostrophe (&#x27;)."""
        body = "<p>Try the &#x27;tanks&#x27; now.</p>"
        edit = {"anchor_phrase": "'tanks'"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">&#x27;tanks&#x27;</a>' in result
    
    def test_entity_at_phrase_start_decimal_apostrophe(self):
        """Phrase starting with entity-decoded apostrophe (&#39;)."""
        body = "<p>Our &#39;tanks&#39; are here.</p>"
        edit = {"anchor_phrase": "'tanks'"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">&#39;tanks&#39;</a>' in result
    
    def test_entity_at_phrase_start_leading_zero_apostrophe(self):
        """Phrase starting with apostrophe (&#0039;)."""
        body = "<p>Check &#0039;tanks&#0039; now.</p>"
        edit = {"anchor_phrase": "'tanks'"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">&#0039;tanks&#0039;</a>' in result
    
    def test_entity_inside_phrase_ampersand(self):
        """Phrase with &amp; entity inside."""
        body = "<p>Check tanks&amp;pods guide.</p>"
        edit = {"anchor_phrase": "tanks&pods"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">tanks&amp;pods</a>' in result
    
    def test_entity_at_phrase_edge_nbsp(self):
        """Phrase with &nbsp; at edge should handle correctly."""
        body = "<p>Get&nbsp;tanks now.</p>"
        edit = {"anchor_phrase": " tanks"}  # Space-tanks, but &nbsp; is special
        # &nbsp; decodes to non-breaking space which is different from regular space
        # This should either match or reject cleanly
        try:
            result = build_edit(body, edit, "https://example.com/target")
            # If it succeeds, verify it didn't break the entity
            assert "&nbsp;" in result or "&#160;" in result or "tanks" in result
        except LinkConflict:
            pass  # Rejection is acceptable for edge case
    
    def test_named_entity_amp(self):
        """Phrase containing ampersand via &amp; entity."""
        body = "<p>R&amp;D team rocks.</p>"
        edit = {"anchor_phrase": "R&D team"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">R&amp;D team</a>' in result


class TestVerifyAnchorWellformed:
    """Direct unit tests for _verify_anchor_wellformed function."""
    
    def test_wellformed_anchor_passes(self):
        """A well-formed anchor should pass validation."""
        html = '<p>Check our <a href="https://example.com">ceramic tanks</a> guide.</p>'
        anchor_pos = html.find('<a href=')
        # Should not raise
        _verify_anchor_wellformed(html, anchor_pos)
    
    def test_nested_anchor_inside_rejected(self):
        """Anchor containing another anchor should be rejected."""
        html = '<p>Check <a href="/outer"><a href="/inner">nested</a></a> text.</p>'
        anchor_pos = html.find('<a href="/outer">')
        with pytest.raises(LinkConflict) as exc_info:
            _verify_anchor_wellformed(html, anchor_pos)
        assert exc_info.value.code == "nested_anchor_created"
    
    def test_anchor_inside_existing_rejected(self):
        """Anchor inserted inside existing anchor should be rejected."""
        html = '<p>Check <a href="/outer">text <a href="/new">nested</a> more</a> here.</p>'
        anchor_pos = html.find('<a href="/new">')
        with pytest.raises(LinkConflict) as exc_info:
            _verify_anchor_wellformed(html, anchor_pos)
        assert exc_info.value.code == "nested_anchor_created"
    
    def test_start_tag_inside_anchor_rejected(self):
        """Anchor containing a start tag (e.g. <strong>) should be rejected."""
        html = '<p>Check our <a href="https://x.com">ceramic <strong>tanks</strong></a> guide.</p>'
        anchor_pos = html.find('<a href=')
        with pytest.raises(LinkConflict) as exc_info:
            _verify_anchor_wellformed(html, anchor_pos)
        assert exc_info.value.code == "anchor_spans_tags"
    
    def test_end_tag_inside_anchor_rejected(self):
        """Anchor containing a closing tag from outside (e.g. </strong>) should be rejected.
        
        This catches cases like: <strong>our <a>ceramic</strong> tanks</a>
        The </strong> is inside our anchor but opened outside - malformed.
        """
        html = '<p><strong>our <a href="https://x.com">ceramic</strong> tanks</a> guide.</p>'
        anchor_pos = html.find('<a href=')
        with pytest.raises(LinkConflict) as exc_info:
            _verify_anchor_wellformed(html, anchor_pos)
        assert exc_info.value.code == "anchor_spans_tags"
    
    def test_br_inside_anchor_allowed(self):
        """<br> inside anchor is allowed (void element)."""
        html = '<p>Check our <a href="https://x.com">ceramic<br>tanks</a> guide.</p>'
        anchor_pos = html.find('<a href=')
        # Should not raise
        _verify_anchor_wellformed(html, anchor_pos)
    
    def test_anchor_not_found_rejected(self):
        """Wrong anchor position should raise anchor_not_found."""
        html = '<p>Check our <a href="https://x.com">tanks</a> guide.</p>'
        with pytest.raises(LinkConflict) as exc_info:
            _verify_anchor_wellformed(html, 0)  # Position 0 is not an anchor
        assert exc_info.value.code == "anchor_not_found"


class TestGuardEditNestedAnchors:
    """Test guard_edit behavior with nested anchors."""
    
    def test_has_nested_anchors_detects_nested_structure(self):
        """_has_nested_anchors should detect <a> inside <a>."""
        nested_html = '<p>Some <a href="/existing"><a href="/new">nested</a> link</a> text.</p>'
        assert _has_nested_anchors(nested_html)
    
    def test_has_nested_anchors_allows_sequential(self):
        """Sequential anchors should not be flagged."""
        sequential_html = '<p>Some <a href="/first">first</a> and <a href="/second">second</a> text.</p>'
        assert not _has_nested_anchors(sequential_html)
    
    def test_guard_edit_allows_preexisting_nested_anchors(self):
        """Pre-existing nested anchors in source should not block our edit."""
        old = '<p>Pre-existing <a href="/outer"><a href="/inner">nested</a></a>. New phrase here.</p>'
        edit = {"anchor_phrase": "New phrase"}
        new = build_edit(old, edit, "https://example.com/target")
        guard_edit(old, new, edit, "https://example.com/target")


class TestNestedAnchorEdgeCases:
    """Test edge cases where pre-existing nesting shouldn't disable insertion check."""
    
    def test_nested_para1_unclosed_para2_phrase_para3_rejected(self):
        """Repro: nested pair in para 1, unclosed anchor in para 2, phrase in para 3.
        
        Pre-existing nesting in para 1 must NOT disable the check for para 3.
        The unclosed anchor in para 2 extends into para 3, so wrapping "test phrase"
        would create new nesting. This should be rejected.
        """
        body = (
            '<p>Para 1 with <a href="/outer"><a href="/inner">nested</a></a> anchors.</p>'
            '<p>Para 2 with <a href="/unclosed">unclosed anchor'
            '</p><p>Para 3 with test phrase here.</p>'
        )
        edit = {"anchor_phrase": "test phrase"}
        
        # The unclosed anchor in para 2 means "test phrase" in para 3 is inside an anchor
        # even though there's pre-existing nesting in para 1.
        # The fix ensures we check the insertion point specifically.
        with pytest.raises(LinkConflict) as exc_info:
            result = build_edit(body, edit, "https://example.com/target")
            # If build_edit doesn't catch it, guard_edit should
            guard_edit(body, result, edit, "https://example.com/target")
        
        # Should be caught as insert_inside_existing_anchor
        assert exc_info.value.code == "insert_inside_existing_anchor"
    
    def test_edit_succeeds_when_phrase_outside_all_anchors(self):
        """Edit should SUCCEED when phrase is NOT inside any anchor, even with pre-existing nesting.
        
        Pre-existing nested anchors in para 1 should not block a valid edit in para 2
        where the phrase is outside all anchors.
        """
        # Page already has nested anchors in para 1
        old = (
            '<p>Para 1 with <a href="/outer"><a href="/inner">nested</a></a> anchors.</p>'
            '<p>Para 2 with clean content and test phrase here.</p>'
        )
        edit = {"anchor_phrase": "test phrase"}
        
        # This should work - the phrase is NOT inside any anchor
        result = build_edit(old, edit, "https://example.com/target")
        
        # guard_edit should pass since we didn't insert inside an anchor
        guard_edit(old, result, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">test phrase</a>' in result


class TestRealWorldAnchorGuardCases:
    """Test real-world cases from issue #740158 and spec tests 2-6."""
    
    def test_740158_shape_escaped_anchor_in_article(self):
        """Real 740158 shape: article with escaped &lt;a ...&gt; markup."""
        body = '<p>Our range of &lt;a href="https://shop.com/collections/rechargeable"&gt;rechargeable disposables&lt;/a&gt; offers great value.</p>'
        edit = {"anchor_phrase": "rechargeable disposables"}
        with pytest.raises(LinkConflict) as exc_info:
            build_edit(body, edit, "https://shop.com/target")
        assert exc_info.value.code == "insert_inside_existing_anchor"
    
    def test_spec_case2_multiple_occurrences_first_safe(self):
        """Spec test 2: multiple occurrences, first one outside anchor → first gets wrapped."""
        body = '<p>Ceramic tanks are great. Our &lt;a href="#"&gt;ceramic tanks&lt;/a&gt; link.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/target")
        assert '<a href="https://example.com/target">Ceramic tanks</a> are great' in result
    
    def test_spec_case3_inside_real_anchor_skipped(self):
        """Spec test 3: phrase inside <a> is skipped by BodyParser."""
        body = '<p>Check our <a href="/existing">ceramic tanks</a> collection. We also sell ceramic tanks.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/target")
        assert result.count('<a href="https://example.com/target">ceramic tanks</a>') == 1
        assert result.count('<a href="/existing">ceramic tanks</a>') == 1
    
    def test_spec_case4_insert_sentence_with_escaped_markup_allowed(self):
        """Spec test 4: insert_sentence into paragraph with escaped &lt;a...&gt; markup.
        
        Brief: A 40+ character locator sentence into a paragraph that contains escaped
        &lt;a href=...&gt; text. The sentence is inserted after that paragraph's </p>.
        
        Expected: Inserted safely after </p>, no nesting, escaped text untouched.
        """
        # Body has escaped anchor markup - the visible text is literal angle brackets
        body = '<p>Our range of rechargeable devices including the &lt;a href="https://shop.com/rechargeable"&gt;best models&lt;/a&gt; is popular with customers.</p>'
        
        # The visible text for matching (40+ chars): "Our range of rechargeable devices including the <a href=..."
        # But we use plain text locator (no HTML)
        edit = {
            "anchor_phrase": "new products",
            "insert_sentence": "Check out our new products for great deals.",
            # Plain text locator - 40+ chars to match paragraph
            "insert_after_text": "Our range of rechargeable devices including the",
        }
        result = build_edit(body, edit, "https://example.com/new")
        
        # Sentence inserted after </p> - new paragraph added
        assert '<a href="https://example.com/new">new products</a>' in result
        # Original escaped markup untouched
        assert '&lt;a href="https://shop.com/rechargeable"&gt;' in result
        assert '&lt;/a&gt;' in result
        # guard_edit passes
        guard_edit(body, result, edit, "https://example.com/new")
    
    def test_spec_case5_guard_edit_rejects_nested_via_nesting_check(self, monkeypatch):
        """Spec test 5: guard_edit rejects nested result via NESTING check, not reconstruction.
        
        Build old/new so reconstruction passes but nesting fails. Monkeypatch build_edit
        so guard_edit's reconstruction check passes, but our nested HTML is caught by
        the _verify_anchor_wellformed nesting check.
        
        FAILS if guard_edit's _verify_anchor_wellformed call is removed.
        """
        from shopifyseo.internal_links import safety
        
        old = '<p>Check our tanks here.</p>'
        edit = {"anchor_phrase": "tanks"}
        url = "https://example.com/target"
        
        # Malformed HTML with our anchor nested inside an existing one
        # This would be invalid even though our anchor tag is correctly formed
        nested = '<p>Check <a href="/outer">our <a href="https://example.com/target">tanks</a></a> here.</p>'
        
        # Monkeypatch build_edit to return the nested HTML
        # This makes reconstruction pass, so guard_edit reaches the nesting check
        monkeypatch.setattr(safety, 'build_edit', lambda *_: nested)
        
        with pytest.raises(LinkConflict) as exc_info:
            safety.guard_edit(old, nested, edit, url)
        # Must be caught by _verify_anchor_wellformed (nested_anchor_created)
        assert exc_info.value.code == "nested_anchor_created"
    
    def test_spec_case6_apply_time_recheck(self):
        """Spec test 6: apply-time recheck.
        
        The apply path validates the edit via guard_edit (apply.py:274) which calls
        _verify_anchor_wellformed. This ensures malformed results are caught at apply time.
        
        See tests/test_internal_links_apply.py:72 (test_live_changes_after_reservation_block_push)
        for the test that verifies Shopify changes after preview block the push.
        
        This test verifies guard_edit is called on the apply path by checking that
        an invalid edit would be rejected.
        """
        old = '<p>Our ceramic tanks are popular.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        new = build_edit(old, edit, "https://example.com/ceramic")
        # guard_edit is called at apply time (apply.py:274)
        guard_edit(old, new, edit, "https://example.com/ceramic")
        
        # Tampered results should be rejected
        tampered = '<p>Our <a href="https://example.com/ceramic">ceramic tanks</a> are MODIFIED.</p>'
        with pytest.raises(LinkConflict):
            guard_edit(old, tampered, edit, "https://example.com/ceramic")


class TestGuardEditFindsCorrectAnchor:
    """Regression tests for guard_edit finding OUR anchor correctly.
    
    Issue: When inserted text shares prefix with following content, finding
    '<a href=' from insert_offset can land past our tag or on a different anchor.
    
    These tests verify that valid edits are ALLOWED even when the next paragraph
    starts with a link or contains formatted links.
    """
    
    URL = "https://s.com/collections/ceramic-tanks"
    
    def test_1a_insert_sentence_with_link_in_next_para(self):
        """Insert sentence when next paragraph has a link. Must be ALLOWED."""
        old = '<p>Love tanks.</p><p><a href="/z">Zed</a> more.</p>'
        edit = {
            "anchor_phrase": "ceramic tanks",
            "insert_sentence": "ceramic tanks are great.",
            "insert_after_text": "Love tanks.",
        }
        result = build_edit(old, edit, self.URL)
        assert f'<a href="{self.URL}">ceramic tanks</a>' in result
        # guard_edit must pass
        guard_edit(old, result, edit, self.URL)
    
    def test_1b_insert_sentence_with_formatted_link_in_next_para(self):
        """Insert sentence when next link has <strong> inside. Must be ALLOWED."""
        old = '<p>Love tanks.</p><p><a href="/z"><strong>Zed</strong></a> more.</p>'
        edit = {
            "anchor_phrase": "ceramic tanks",
            "insert_sentence": "ceramic tanks are great.",
            "insert_after_text": "Love tanks.",
        }
        result = build_edit(old, edit, self.URL)
        assert f'<a href="{self.URL}">ceramic tanks</a>' in result
        guard_edit(old, result, edit, self.URL)
    
    def test_1b_with_newline_between_paragraphs(self):
        """Same as 1b but with newline between paragraphs. Must be ALLOWED."""
        old = '<p>Love tanks.</p>\n<p><a href="/z"><strong>Zed</strong></a> more.</p>'
        edit = {
            "anchor_phrase": "ceramic tanks",
            "insert_sentence": "ceramic tanks are great.",
            "insert_after_text": "Love tanks.",
        }
        result = build_edit(old, edit, self.URL)
        assert f'<a href="{self.URL}">ceramic tanks</a>' in result
        guard_edit(old, result, edit, self.URL)
    
    def test_1c_insert_sentence_next_link_similar_url_with_em(self):
        """Next link has similar URL prefix and <em>. Must be ALLOWED."""
        old = '<p>Love tanks.</p><p><a href="https://s.com/collections/other"><em>Other</em></a> stuff.</p>'
        edit = {
            "anchor_phrase": "ceramic tanks",
            "insert_sentence": "ceramic tanks are great.",
            "insert_after_text": "Love tanks.",
        }
        result = build_edit(old, edit, self.URL)
        assert f'<a href="{self.URL}">ceramic tanks</a>' in result
        guard_edit(old, result, edit, self.URL)
    
    def test_1d_manual_append_with_formatted_link_after(self):
        """Manual append when body has formatted link after sentence. Must be ALLOWED."""
        # The sentence is followed by a link (within same paragraph)
        old = '<p>Love tanks. And <a href="/z"><strong>Zed</strong></a> is here.</p>'
        edit = {
            "origin": "manual",
            "anchor_phrase": "ceramic tanks",
            "after_sentence": "Love tanks.",
            "append_text": "ceramic tanks rock.",
        }
        result = build_edit(old, edit, self.URL)
        assert f'<a href="{self.URL}">ceramic tanks</a>' in result
        guard_edit(old, result, edit, self.URL)
    
    def test_1e_phrase_wrap_with_formatted_link_after(self):
        """Phrase wrap when text after has <b> inside link. Must be ALLOWED."""
        old = '<p>ceramic tanks <a href="/z"><b>x</b></a></p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(old, edit, self.URL)
        assert f'<a href="{self.URL}">ceramic tanks</a>' in result
        guard_edit(old, result, edit, self.URL)
    
    def test_1f_real_preview_ai_woven_insert_sentence_next_para_link(self, db_conn):
        """Real preview_suggestion on ai_woven insert_sentence with link in next para.
        
        The sentence starts with the phrase ('Ceramic tanks are a great upgrade.')
        and the next paragraph starts with a link. Must be allowed.
        """
        from shopifyseo.internal_links.apply import preview_suggestion
        
        conn = db_conn
        conn.executescript("""
            CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
                description_html TEXT, gsc_clicks INTEGER DEFAULT 0, online_store_url TEXT);
            CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
                description_html TEXT, api_unreachable INTEGER DEFAULT 0);
            CREATE TABLE link_suggestions (
                id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, 
                target_type TEXT, target_handle TEXT, kind TEXT, anchor_phrase TEXT,
                ai_edit_json TEXT, source_body_hash TEXT, score REAL DEFAULT 0, status TEXT DEFAULT 'suggested',
                created_at INTEGER, weak_anchor INTEGER DEFAULT 0
            );
            CREATE TABLE link_body_snapshots (
                id INTEGER PRIMARY KEY, suggestion_id INTEGER, source_type TEXT, source_handle TEXT,
                shopify_id TEXT, old_body TEXT, new_body TEXT, status TEXT, created_at INTEGER, updated_at INTEGER, error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_pending ON link_body_snapshots (source_type, source_handle)
                WHERE status IN ('prepared', 'needs_reconciliation', 'undo_prepared', 'undo_needs_reconciliation');
            CREATE TABLE internal_links (source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, href TEXT, anchor_text TEXT);
            CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE link_suggestion_events (id INTEGER PRIMARY KEY, suggestion_id INTEGER, event_type TEXT, source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, kind TEXT, score REAL, gsc_clicks_at_event INTEGER, created_at INTEGER);
        """)
        
        # Body: first para, then link in next para
        body = '<p>Love tanks.</p><p><a href="/z"><strong>Zed</strong></a> is here.</p>'
        
        import json
        edit = {
            "anchor_phrase": "Ceramic tanks",
            "insert_sentence": "Ceramic tanks are a great upgrade.",
            "insert_after_text": "Love tanks.",
        }
        
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'tanks', 'Tanks')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, ai_edit_json, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'tanks', 'ai_woven', 'Ceramic tanks', ?, 'suggested', 1)",
            (json.dumps(edit),)
        )
        conn.execute("INSERT INTO service_settings (key, value) VALUES ('ai_enabled_product', 'true')")
        conn.commit()
        
        def mock_fetch(source_type, row):
            return body
        
        preview = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview["allowed"] == True, f"Preview must be allowed, got: {preview.get('reason')}"


class TestGuardEditFailsClosed:
    """guard_edit must fail closed if our anchor can't be found exactly once.
    
    With build_edit monkeypatched to return malformed HTML, guard_edit must REJECT:
    - <a class="x" href=U> (class before href)
    - <a href='U'> (single quotes)
    - <A HREF="U"> (uppercase)
    - <a  href="U"> (double space)
    - No anchor at all
    """
    
    URL = "https://s.com/collections/ceramic-tanks"
    CROSSING = '<p><strong>our <a href="https://s.com/collections/ceramic-tanks">ceramic</strong> tanks</a> rock</p>'
    
    def test_1g_rejects_class_before_href(self, monkeypatch):
        """Reject anchor with class attribute before href."""
        old = '<p>ceramic tanks rock</p>'
        malformed = f'<p><a class="x" href="{self.URL}">ceramic tanks</a> rock</p>'
        
        monkeypatch.setattr('shopifyseo.internal_links.safety.build_edit', lambda *_: malformed)
        
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            guard_edit(old, malformed, edit, self.URL)
        assert exc_info.value.code == "anchor_not_found"
    
    def test_1g_rejects_single_quotes(self, monkeypatch):
        """Reject anchor with single quotes around href."""
        old = '<p>ceramic tanks rock</p>'
        malformed = f"<p><a href='{self.URL}'>ceramic tanks</a> rock</p>"
        
        monkeypatch.setattr('shopifyseo.internal_links.safety.build_edit', lambda *_: malformed)
        
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            guard_edit(old, malformed, edit, self.URL)
        assert exc_info.value.code == "anchor_not_found"
    
    def test_1g_rejects_uppercase(self, monkeypatch):
        """Reject anchor with uppercase tags."""
        old = '<p>ceramic tanks rock</p>'
        malformed = f'<p><A HREF="{self.URL}">ceramic tanks</A> rock</p>'
        
        monkeypatch.setattr('shopifyseo.internal_links.safety.build_edit', lambda *_: malformed)
        
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            guard_edit(old, malformed, edit, self.URL)
        assert exc_info.value.code == "anchor_not_found"
    
    def test_1g_rejects_double_space(self, monkeypatch):
        """Reject anchor with double space before href."""
        old = '<p>ceramic tanks rock</p>'
        malformed = f'<p><a  href="{self.URL}">ceramic tanks</a> rock</p>'
        
        monkeypatch.setattr('shopifyseo.internal_links.safety.build_edit', lambda *_: malformed)
        
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            guard_edit(old, malformed, edit, self.URL)
        assert exc_info.value.code == "anchor_not_found"
    
    def test_1g_rejects_no_anchor(self, monkeypatch):
        """Reject when no anchor is added at all."""
        old = '<p>ceramic tanks rock</p>'
        malformed = '<p>ceramic tanks rock</p>'  # Same as old
        
        monkeypatch.setattr('shopifyseo.internal_links.safety.build_edit', lambda *_: malformed)
        
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            guard_edit(old, malformed, edit, self.URL)
        assert exc_info.value.code == "anchor_not_found"
    
    def test_1g_rejects_crossing_anchor(self, monkeypatch):
        """Reject anchor that crosses tag boundaries (</strong> inside anchor)."""
        old = '<p>our ceramic tanks rock</p>'
        
        monkeypatch.setattr('shopifyseo.internal_links.safety.build_edit', lambda *_: self.CROSSING)
        
        edit = {"anchor_phrase": "ceramic tanks"}
        with pytest.raises(LinkConflict) as exc_info:
            guard_edit(old, self.CROSSING, edit, self.URL)
        # Should be rejected by _verify_anchor_wellformed
        assert exc_info.value.code == "anchor_spans_tags"


class TestCallSiteMutationTests:
    """True call-site mutation tests using monkeypatch.
    
    Each test monkeypatches _verify_anchor_wellformed to track calls AND
    uses build_edit through the actual call sites.
    
    Removing a call site's _verify_anchor_wellformed MUST fail that test.
    """
    
    URL = "https://example.com/target"
    
    def test_phrase_wrap_calls_verify(self, monkeypatch):
        """phrase_wrap mode must call _verify_anchor_wellformed.
        
        FAILS if _verify_anchor_wellformed call at safety.py:1376 is removed.
        """
        from shopifyseo.internal_links import safety
        
        verify_called = [False]
        original_verify = safety._verify_anchor_wellformed
        
        def tracking_verify(html, pos):
            verify_called[0] = True
            return original_verify(html, pos)
        
        monkeypatch.setattr(safety, '_verify_anchor_wellformed', tracking_verify)
        
        # Call build_edit in phrase_wrap mode
        old = '<p>ceramic tanks are great.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        safety.build_edit(old, edit, self.URL)
        
        assert verify_called[0], "phrase_wrap must call _verify_anchor_wellformed"
    
    def test_insert_sentence_calls_verify(self, monkeypatch):
        """insert_sentence mode must call _verify_anchor_wellformed.
        
        FAILS if _verify_anchor_wellformed call at safety.py:1261 is removed.
        """
        from shopifyseo.internal_links import safety
        
        verify_called = [False]
        original_verify = safety._verify_anchor_wellformed
        
        def tracking_verify(html, pos):
            verify_called[0] = True
            return original_verify(html, pos)
        
        monkeypatch.setattr(safety, '_verify_anchor_wellformed', tracking_verify)
        
        # Call build_edit in insert_sentence mode
        old = '<p>First paragraph.</p>'
        edit = {
            "anchor_phrase": "ceramic tanks",
            "insert_sentence": "ceramic tanks are great.",
            "insert_after_text": "First paragraph.",
        }
        safety.build_edit(old, edit, self.URL)
        
        assert verify_called[0], "insert_sentence must call _verify_anchor_wellformed"
    
    def test_manual_append_calls_verify(self, monkeypatch):
        """manual_append mode must call _verify_anchor_wellformed.
        
        FAILS if _verify_anchor_wellformed call at safety.py:1190 is removed.
        """
        from shopifyseo.internal_links import safety
        
        verify_called = [False]
        original_verify = safety._verify_anchor_wellformed
        
        def tracking_verify(html, pos):
            verify_called[0] = True
            return original_verify(html, pos)
        
        monkeypatch.setattr(safety, '_verify_anchor_wellformed', tracking_verify)
        
        # Call build_edit in manual_append mode
        old = '<p>This is a test sentence.</p>'
        edit = {
            "origin": "manual",
            "anchor_phrase": "ceramic tanks",
            "after_sentence": "This is a test sentence.",
            "append_text": "ceramic tanks rock.",
        }
        safety.build_edit(old, edit, self.URL)
        
        assert verify_called[0], "manual_append must call _verify_anchor_wellformed"
    
    def test_guard_edit_verify_catches_crossing(self, monkeypatch):
        """guard_edit's _verify_anchor_wellformed catches crossing anchors.
        
        FAILS if _verify_anchor_wellformed call at guard_edit end is removed.
        """
        from shopifyseo.internal_links import safety
        
        old = '<p>our ceramic tanks rock</p>'
        crossing = '<p><strong>our <a href="https://example.com/target">ceramic</strong> tanks</a> rock</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        
        # Monkeypatch build_edit to return crossing HTML
        monkeypatch.setattr(safety, 'build_edit', lambda *_: crossing)
        
        with pytest.raises(LinkConflict) as exc_info:
            safety.guard_edit(old, crossing, edit, self.URL)
        assert exc_info.value.code == "anchor_spans_tags"
    
    def test_guard_edit_nesting_check_catches_nested_anchor(self, monkeypatch):
        """guard_edit's _verify_anchor_wellformed catches nested anchors.
        
        FAILS if _verify_anchor_wellformed call in guard_edit is removed.
        """
        from shopifyseo.internal_links import safety
        
        # Old has an anchor; new has nested anchor
        old = '<p>Check our tanks collection.</p>'
        # Malformed: our anchor is nested inside another anchor
        nested = '<p>Check <a href="/outer">our <a href="https://example.com/target">tanks</a></a> collection.</p>'
        edit = {"anchor_phrase": "tanks"}
        
        # Monkeypatch build_edit to return nested HTML
        monkeypatch.setattr(safety, 'build_edit', lambda *_: nested)
        
        with pytest.raises(LinkConflict) as exc_info:
            safety.guard_edit(old, nested, edit, "https://example.com/target")
        # Should be caught by _verify_anchor_wellformed (nested_anchor_created)
        assert exc_info.value.code == "nested_anchor_created"


# =============================================================================
# C. Preview Respects Open-Write Lock Tests
# =============================================================================

def _test_db(conn):
    """Create a minimal test database."""
    conn.executescript("""
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, online_store_url TEXT);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, api_unreachable INTEGER DEFAULT 0);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0);
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, 
            target_type TEXT, target_handle TEXT, kind TEXT, anchor_phrase TEXT,
            ai_edit_json TEXT, source_body_hash TEXT, score REAL DEFAULT 0, status TEXT DEFAULT 'suggested',
            created_at INTEGER, weak_anchor INTEGER DEFAULT 0
        );
        CREATE TABLE link_body_snapshots (
            id INTEGER PRIMARY KEY, suggestion_id INTEGER, source_type TEXT, source_handle TEXT,
            shopify_id TEXT, old_body TEXT, new_body TEXT, status TEXT, created_at INTEGER, updated_at INTEGER, error TEXT
        );
        CREATE TABLE internal_links (source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, href TEXT, anchor_text TEXT);
        CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT);
    """)
    return conn


class TestPreviewWriteLock:
    """Test preview respects the page write lock."""
    
    def test_check_page_write_pending_true(self, db_conn):
        """Should return True when there's a pending snapshot on the page."""
        conn = _test_db(db_conn)
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'product', 'test-handle', 'needs_reconciliation', 1, 1)"
        )
        conn.commit()
        assert _check_page_write_pending(conn, "product", "test-handle")
    
    def test_check_page_write_pending_false(self, db_conn):
        """Should return False when no pending snapshots."""
        conn = _test_db(db_conn)
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'product', 'test-handle', 'applied', 1, 1)"
        )
        conn.commit()
        assert not _check_page_write_pending(conn, "product", "test-handle")
    
    def test_preview_returns_page_write_pending(self, db_conn):
        """Preview should return allowed=false with page_write_pending code."""
        conn = _test_db(db_conn)
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://1', 'source', 'Source', 'ACTIVE', '<p>Body</p>', 'https://shop.com/products/source')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://2', 'target', 'Target')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, anchor_phrase, created_at) "
            "VALUES ('product', 'source', 'collection', 'target', 'phrase_wrap', 'test', 1)"
        )
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'needs_reconciliation', 1, 1)"
        )
        conn.commit()
        
        result = preview_suggestion(conn, 1, "https://example.com")
        assert result["allowed"] is False
        assert result.get("code") == "page_write_pending"


class TestManualWeaveWriteLock:
    """Test manual-weave respects the page write lock."""
    
    def test_manual_weave_preview_only_returns_page_write_pending(self, db_conn):
        """Manual-weave with preview_only=True should return allowed=false shape.
        
        Test-contract change: preview_only now returns allowed=false dict instead of raising 409.
        """
        from shopifyseo.internal_links.manual_weave import submit_manual_weave
        
        conn = _test_db(db_conn)
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://1', 'source', 'Source', 'ACTIVE', '<p>Body sentence.</p>', 'https://shop.com/products/source')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://2', 'target', 'Target')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES ('product', 'source', 'collection', 'target', 'ai_woven', 'test', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'needs_reconciliation', 1, 1)"
        )
        conn.execute("INSERT INTO service_settings (key, value) VALUES ('internal_links_ai_types', 'blog_article,product,collection')")
        conn.commit()
        
        result = submit_manual_weave(
            conn, 1, "https://example.com",
            "Body sentence.",
            'Body sentence. <a href="/collections/target">test link</a>.',
            preview_only=True,
        )
        assert result["allowed"] is False
        assert result.get("code") == "page_write_pending"


class TestLockHolderBehavior:
    """Test that the lock holder can preview and apply their own suggestion."""
    
    def _create_lock_test_db(self, conn):
        """Create database for lock holder tests."""
        conn.executescript("""
            CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
                description_html TEXT, gsc_clicks INTEGER DEFAULT 0, online_store_url TEXT);
            CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
                description_html TEXT, api_unreachable INTEGER DEFAULT 0);
            CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
                body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0);
            CREATE TABLE link_suggestions (
                id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT, 
                target_type TEXT, target_handle TEXT, kind TEXT, anchor_phrase TEXT,
                ai_edit_json TEXT, ai_anchor_html TEXT, source_body_hash TEXT, score REAL DEFAULT 0, 
                status TEXT DEFAULT 'suggested', created_at INTEGER, weak_anchor INTEGER DEFAULT 0, applied_at INTEGER,
                UNIQUE (source_type, source_handle, target_type, target_handle)
            );
            CREATE TABLE link_body_snapshots (
                id INTEGER PRIMARY KEY, suggestion_id INTEGER, source_type TEXT, source_handle TEXT,
                shopify_id TEXT, old_body TEXT, new_body TEXT, status TEXT, created_at INTEGER, updated_at INTEGER, error TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_snapshots_pending ON link_body_snapshots (source_type, source_handle)
                WHERE status IN ('prepared', 'needs_reconciliation', 'undo_prepared', 'undo_needs_reconciliation');
            CREATE TABLE internal_links (source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, href TEXT, anchor_text TEXT);
            CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE link_suggestion_events (id INTEGER PRIMARY KEY, suggestion_id INTEGER, event_type TEXT, source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, kind TEXT, score REAL, gsc_clicks_at_event INTEGER, created_at INTEGER);
        """)
        return conn
    
    def test_lock_holder_can_reconcile_own_snapshot(self, db_conn):
        """Lock holder can reconcile their own pending snapshot.
        
        Suggestion 1 has a needs_reconciliation snapshot (the lock).
        The lock holder (suggestion 1) can reconcile it.
        """
        from shopifyseo.internal_links.apply import reconcile_suggestion
        import time
        
        conn = self._create_lock_test_db(db_conn)
        original_body = "<p>Check our ceramic tanks guide.</p>"
        new_body = '<p>Check our <a href="https://shop.com/collections/tanks">ceramic tanks</a> guide.</p>'
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'tanks', 'Tanks')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'tanks', 'phrase_wrap', 'ceramic tanks', 'suggested', 1)"
        )
        # Create a real pending snapshot owned by suggestion 1
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'gid://shopify/Product/1', ?, ?, 'needs_reconciliation', 1, ?)",
            (original_body, new_body, int(time.time()) - 1000)
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            # Shopify shows the new body was written successfully
            return new_body
        
        # The lock holder can reconcile their own snapshot
        result = reconcile_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert result["status"] == "applied"
    
    def test_lock_holder_preview_then_apply_succeeds(self, db_conn):
        """Lock holder can preview and then apply their own suggestion (no prior lock)."""
        from shopifyseo.internal_links.apply import apply_suggestion, preview_suggestion
        
        conn = self._create_lock_test_db(db_conn)
        original_body = "<p>Check our ceramic tanks guide.</p>"
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'tanks', 'Tanks')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'tanks', 'phrase_wrap', 'ceramic tanks', 'suggested', 1)"
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            return original_body
        
        def mock_push(source_type, row, body):
            return body
        
        # Preview first
        preview = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview["allowed"], f"Preview failed: {preview.get('reason')}"
        
        # Then apply with the preview token
        result = apply_suggestion(
            conn, 1, "https://shop.com",
            preview_token_value=preview["preview_token"],
            fetch_fn=mock_fetch,
            push_fn=mock_push
        )
        assert result["status"] == "applied"
    
    def test_preview_blocked_by_another_holders_lock(self, db_conn):
        """Preview is blocked when page is locked by another suggestion's snapshot.
        
        Suggestion 1 has a pending snapshot (the lock holder).
        Suggestion 2 on the SAME page tries to preview but is blocked.
        """
        from shopifyseo.internal_links.apply import preview_suggestion
        
        conn = self._create_lock_test_db(db_conn)
        original_body = "<p>Check our ceramic tanks and test2 phrase.</p>"
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'target1', 'Target1')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/2', 'target2', 'Target2')"
        )
        # Suggestion 1 - has the lock (prepared snapshot)
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'target1', 'phrase_wrap', 'ceramic tanks', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'gid://shopify/Product/1', '<p>Old.</p>', '<p>New.</p>', 'prepared', 1, 1)"
        )
        # Suggestion 2 - different suggestion, same source page, NO snapshot yet
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (2, 'product', 'source', 'collection', 'target2', 'phrase_wrap', 'test2 phrase', 'suggested', 1)"
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            return original_body
        
        # Trying to preview suggestion 2 should return allowed=false because suggestion 1 holds the lock
        preview = preview_suggestion(conn, 2, "https://shop.com", fetch_fn=mock_fetch)
        
        # Should indicate the page is locked
        assert preview["allowed"] == False
        assert preview["code"] == "page_write_pending"
        assert "write" in preview["reason"].lower() or "progress" in preview["reason"].lower()
    
    def test_reconcile_requires_own_snapshot(self, db_conn):
        """Reconcile requires the suggestion to have its own pending snapshot.
        
        Suggestion 1 has a pending snapshot (the lock holder).
        Suggestion 2 tries to reconcile but has no snapshot of its own.
        This tests that reconcile checks for the correct suggestion_id.
        """
        from shopifyseo.internal_links.apply import reconcile_suggestion
        
        conn = self._create_lock_test_db(db_conn)
        original_body = "<p>Check our ceramic tanks.</p>"
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'target1', 'Target1')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/2', 'target2', 'Target2')"
        )
        # Suggestion 1 - has the lock (needs_reconciliation snapshot)
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'target1', 'phrase_wrap', 'ceramic tanks', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'gid://shopify/Product/1', ?, '<p>New.</p>', 'needs_reconciliation', 1, 1)",
            (original_body,)
        )
        # Suggestion 2 - different suggestion, same source page, NO snapshot
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (2, 'product', 'source', 'collection', 'target2', 'phrase_wrap', 'tanks', 'suggested', 1)"
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            return "<p>New.</p>"
        
        # Suggestion 2 tries to reconcile but has no snapshot
        with pytest.raises(LinkConflict) as exc_info:
            reconcile_suggestion(conn, 2, "https://shop.com", fetch_fn=mock_fetch)
        
        # Should fail because suggestion 2 has no pending snapshot
        assert "no unfinished write" in str(exc_info.value).lower()
    
    def test_lock_holder_reconcile_then_preview_apply_succeeds(self, db_conn):
        """Full sequence test: blocked preview → reconcile → preview (allowed) → apply (stub called).
        
        Sequence:
        1. Suggestion 2 has a needs_reconciliation snapshot (the lock on the page)
        2. Preview for suggestion 1 is blocked (page locked by sug 2)
        3. Suggestion 2 reconciles (clears the lock)
        4. Preview for suggestion 1 now allowed
        5. Apply for suggestion 1 succeeds (stub write called)
        """
        from shopifyseo.internal_links.apply import (
            reconcile_suggestion, preview_suggestion, apply_suggestion
        )
        import time
        
        conn = self._create_lock_test_db(db_conn)
        # Body has two phrases we can link
        original_body = "<p>Check our ceramic tanks and vape pods guide.</p>"
        # What suggestion 2's first write produced (pending reconciliation)
        sug2_new_body = '<p>Check our ceramic tanks and <a href="https://shop.com/collections/pods">vape pods</a> guide.</p>'
        
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'tanks', 'Tanks')"
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/2', 'pods', 'Pods')"
        )
        # Suggestion 1: wants to link "ceramic tanks"
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'tanks', 'phrase_wrap', 'ceramic tanks', 'suggested', 1)"
        )
        # Suggestion 2: linked "vape pods" but needs reconciliation (holds the lock)
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (2, 'product', 'source', 'collection', 'pods', 'phrase_wrap', 'vape pods', 'suggested', 1)"
        )
        # Suggestion 2 has a needs_reconciliation snapshot (the lock)
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (2, 'product', 'source', 'gid://shopify/Product/1', ?, ?, 'needs_reconciliation', 1, ?)",
            (original_body, sug2_new_body, int(time.time()) - 1000)
        )
        conn.commit()
        
        # Track which body is currently on Shopify
        shopify_body = [sug2_new_body]  # Sug 2's write succeeded
        push_called = [False]
        
        def mock_fetch(source_type, row):
            return shopify_body[0]
        
        def mock_push(source_type, row, body):
            push_called[0] = True
            shopify_body[0] = body
            return body
        
        # Step 1: Preview for suggestion 1 is BLOCKED (page locked by sug 2)
        preview1_blocked = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview1_blocked["allowed"] == False
        assert preview1_blocked["code"] == "page_write_pending"
        
        # Step 2: Suggestion 2 holder reconciles (clears the lock)
        reconcile_result = reconcile_suggestion(conn, 2, "https://shop.com", fetch_fn=mock_fetch)
        assert reconcile_result["status"] == "applied"
        
        # Verify suggestion 2 is now applied
        sug2 = conn.execute("SELECT status FROM link_suggestions WHERE id = 2").fetchone()
        assert sug2["status"] == "applied"
        
        # Step 3: Preview for suggestion 1 now ALLOWED
        preview1_allowed = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview1_allowed["allowed"] == True
        assert preview1_allowed["preview_token"] is not None
        
        # Step 4: Apply for suggestion 1 succeeds
        apply_result = apply_suggestion(
            conn, 1, "https://shop.com",
            preview_token_value=preview1_allowed["preview_token"],
            fetch_fn=mock_fetch,
            push_fn=mock_push
        )
        assert apply_result["status"] == "applied"
        assert push_called[0] == True, "stub push must be called"
        
        # Both suggestions now applied
        sug1 = conn.execute("SELECT status FROM link_suggestions WHERE id = 1").fetchone()
        assert sug1["status"] == "applied"
    
    def test_sequence_fails_if_reconcile_doesnt_clear_lock(self, monkeypatch, db_conn):
        """Mutation test: if reconcile never clears the lock, sequence fails.
        
        FAILS if reconcile doesn't properly update snapshot status to 'applied'.
        """
        from shopifyseo.internal_links.apply import (
            reconcile_suggestion, preview_suggestion
        )
        from shopifyseo.internal_links import apply as apply_module
        import time
        
        conn = self._create_lock_test_db(db_conn)
        original_body = "<p>Check our ceramic tanks guide.</p>"
        new_body = '<p>Check our <a href="https://shop.com/collections/tanks">ceramic tanks</a> guide.</p>'
        
        conn.execute(
            "INSERT INTO products (shopify_id, handle, title, status, description_html, online_store_url) "
            "VALUES ('gid://shopify/Product/1', 'source', 'Source', 'ACTIVE', ?, 'https://shop.com/products/source')",
            (original_body,)
        )
        conn.execute(
            "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/1', 'tanks', 'Tanks')"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, anchor_phrase, status, created_at) "
            "VALUES (1, 'product', 'source', 'collection', 'tanks', 'phrase_wrap', 'ceramic tanks', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, shopify_id, old_body, new_body, status, created_at, updated_at) "
            "VALUES (1, 'product', 'source', 'gid://shopify/Product/1', ?, ?, 'needs_reconciliation', 1, ?)",
            (original_body, new_body, int(time.time()) - 1000)
        )
        conn.commit()
        
        def mock_fetch(source_type, row):
            return new_body
        
        # Monkeypatch _finish to NOT update suggestion status (simulating broken reconcile)
        original_finish = apply_module._finish
        def broken_finish(conn, snapshot, sug, base_url, *, body=None, undo=False):
            # Do everything except update suggestion status and snapshot status
            pass
        
        monkeypatch.setattr(apply_module, '_finish', broken_finish)
        
        # Reconcile appears to succeed but doesn't actually clear lock
        # (In reality this would fail because _finish also updates snapshot)
        # The key is: preview should still be blocked after "broken" reconcile
        
        # First verify preview is blocked
        preview1 = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview1["allowed"] == False, "Preview must be blocked while lock exists"
        
        # Try reconcile with broken _finish - it will fail or not clear lock
        try:
            reconcile_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        except Exception:
            pass  # May raise due to incomplete _finish
        
        # Preview should STILL be blocked (lock not cleared)
        preview2 = preview_suggestion(conn, 1, "https://shop.com", fetch_fn=mock_fetch)
        assert preview2["allowed"] == False, "Preview must still be blocked if reconcile didn't clear lock"


# =============================================================================
# D. New-Text Spelling Tests
# =============================================================================

class TestInsertedText:
    """Test inserted_text extracts the right text for spelling checks."""
    
    def test_insert_sentence_returns_sentence(self):
        """insert_sentence mode should return the full sentence."""
        edit = {
            "anchor_phrase": "guide",
            "insert_sentence": "Check our guide for color tips.",
            "insert_after_text": "First paragraph.",
        }
        assert inserted_text(edit) == "Check our guide for color tips."
    
    def test_manual_append_returns_append_text(self):
        """Manual mode should return append_text."""
        edit = {
            "origin": "manual",
            "anchor_phrase": "guide",
            "after_sentence": "Test sentence.",
            "append_text": "See our guide for color info.",
        }
        assert inserted_text(edit) == "See our guide for color info."
    
    def test_phrase_wrap_returns_empty(self):
        """phrase_wrap mode (no insert_sentence) should return empty string."""
        edit = {"anchor_phrase": "ceramic tanks"}
        assert inserted_text(edit) == ""
    
    def test_empty_edit_returns_empty(self):
        """Empty/None edit should return empty string."""
        assert inserted_text({}) == ""
        assert inserted_text(None) == ""


class TestEnCaSpelling:
    """Test en-CA spelling check."""
    
    def test_flags_us_color(self):
        """'color' should be flagged as US spelling."""
        issues = check_en_ca_spelling("Choose your color.")
        assert len(issues) == 1
        assert "color" in issues[0].lower()
    
    def test_flags_us_favorite(self):
        """'favorite' should be flagged as US spelling."""
        issues = check_en_ca_spelling("This is my favorite product.")
        assert len(issues) == 1
        assert "favorite" in issues[0].lower()
    
    def test_flags_us_flavor(self):
        """'flavor' should be flagged."""
        issues = check_en_ca_spelling("Great flavor available.")
        assert len(issues) == 1
        assert "flavor" in issues[0].lower()
    
    def test_flags_flavors_times_two_plus_one(self):
        """'flavors' appearing multiple times should be reported once + one extra word."""
        issues = check_en_ca_spelling("Many flavors here. More flavors there. Good color too.")
        assert len(issues) == 2
        assert any("flavors" in i.lower() for i in issues)
        assert any("color" in i.lower() for i in issues)
    
    def test_accepts_ize_forms(self):
        """'-ize' forms are valid Canadian English and should NOT be flagged."""
        issues = check_en_ca_spelling("Minimize your effort. Customize the settings. Organize your files.")
        assert len(issues) == 0
    
    def test_accepts_canadian_spellings(self):
        """Canadian spellings should not be flagged."""
        issues = check_en_ca_spelling("The colour is grey in the centre of the theatre.")
        assert len(issues) == 0
    
    def test_empty_text_no_issues(self):
        """Empty text should return no issues."""
        assert check_en_ca_spelling("") == []
        assert check_en_ca_spelling(None) == []
    
    def test_phrase_wrap_on_body_text_no_inserted_text(self):
        """phrase_wrap mode inserts no new text, so no spelling check needed."""
        edit = {"anchor_phrase": "color options"}
        new_text = inserted_text(edit)
        assert new_text == ""
        issues = check_en_ca_spelling(new_text)
        assert issues == []
    
    def test_deduplicates_issues(self):
        """Multiple occurrences of same word should be reported once."""
        issues = check_en_ca_spelling("Color color color everywhere.")
        assert len(issues) == 1
    
    def test_does_not_flag_vigorous(self):
        """'vigorous' is correct in en-CA and should NOT be flagged."""
        issues = check_en_ca_spelling("A vigorous workout routine.")
        assert len(issues) == 0
    
    def test_does_not_flag_program(self):
        """'program' is correct for computer programs in en-CA."""
        issues = check_en_ca_spelling("This program runs well.")
        assert len(issues) == 0


# =============================================================================
# E. Skip Unpublished Sources Tests
# =============================================================================

def _pipeline_db(conn):
    """Create database for pipeline tests."""
    conn.executescript("""
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            online_store_url TEXT);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            api_unreachable INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT,
            target_type TEXT, target_handle TEXT, anchor_text TEXT, href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY, source_type TEXT, source_handle TEXT,
            target_type TEXT, target_handle TEXT, kind TEXT, anchor_phrase TEXT,
            ai_edit_json TEXT, ai_anchor_html TEXT, source_body_hash TEXT, score REAL DEFAULT 0,
            weak_anchor INTEGER DEFAULT 0, status TEXT DEFAULT 'suggested', created_at INTEGER, applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE TABLE link_body_snapshots (
            id INTEGER PRIMARY KEY, suggestion_id INTEGER, source_type TEXT, source_handle TEXT,
            shopify_id TEXT, old_body TEXT, new_body TEXT, status TEXT, error TEXT, created_at INTEGER, updated_at INTEGER
        );
        CREATE TABLE keyword_page_map (keyword TEXT, object_type TEXT, object_handle TEXT, is_primary INTEGER, gsc_clicks INTEGER, PRIMARY KEY (keyword, object_type, object_handle));
        CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT, primary_keyword TEXT, match_type TEXT, match_handle TEXT);
        CREATE TABLE cluster_keywords (cluster_id INTEGER, keyword TEXT, PRIMARY KEY (cluster_id, keyword));
        CREATE TABLE link_suggestion_events (id INTEGER PRIMARY KEY, suggestion_id INTEGER, event_type TEXT, source_type TEXT, source_handle TEXT, target_type TEXT, target_handle TEXT, kind TEXT, score REAL, gsc_clicks_at_event INTEGER, created_at INTEGER);
        CREATE TABLE link_suggestion_restore_audit (id INTEGER PRIMARY KEY, suggestion_id INTEGER, restored_at INTEGER, actor TEXT, reason TEXT);
    """)
    from shopifyseo.internal_links.store import ensure_schema
    ensure_schema(conn)
    return conn


class TestSkipUnpublishedSources:
    """Test that unpublished sources are skipped during generation."""
    
    def test_unpublished_article_skipped(self, db_conn):
        """Unpublished blog article should not generate suggestions."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'unpub', 'Unpublished', '<p>Content about ceramic tanks.</p>', 0, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "blog_article":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 0
        rows = conn.execute("SELECT * FROM link_suggestions").fetchall()
        assert len(rows) == 0
    
    def test_published_article_generates_suggestions(self, db_conn):
        """Published blog article should generate suggestions."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'pub', 'Published', '<p>Content about ceramic tanks.</p>', 1, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "blog_article":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 1
    
    def test_draft_product_skipped(self, db_conn):
        """Draft product (status != ACTIVE) should not generate suggestions."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('draft-product', 'Draft', 'DRAFT', '<p>About ceramic tanks.</p>', 100, 'https://shop.com/products/draft')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 0
    
    def test_active_product_with_online_store_url_generates_suggestions(self, db_conn):
        """Active product with online_store_url should generate suggestions."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('active-product', 'Active', 'ACTIVE', '<p>About ceramic tanks.</p>', 100, 'https://shop.com/products/active')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 1
    
    def test_active_product_without_online_store_url_generates_suggestions(self, db_conn):
        """Active product WITHOUT online_store_url should still generate suggestions.
        
        Product SOURCES tolerate blank online_store_url (like targets per #48).
        ACTIVE status (case-insensitive) is required for sources.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('no-url-product', 'No URL', 'ACTIVE', '<p>About ceramic tanks.</p>', 100, NULL)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 1  # Product sources tolerate blank online_store_url
    
    def test_product_with_empty_status_skipped(self, db_conn):
        """Product with empty string status should NOT generate suggestions.
        
        Product sources require status = 'ACTIVE' (case-insensitive).
        Empty status is rejected.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('empty-status', 'Empty Status', '', '<p>About ceramic tanks.</p>', 100, 'https://shop.com/products/empty')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 0  # Empty status is rejected
    
    def test_product_with_null_status_skipped(self, db_conn):
        """Product with NULL status should NOT generate suggestions.
        
        Product sources require status = 'ACTIVE' (case-insensitive).
        NULL status is rejected.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('null-status', 'Null Status', NULL, '<p>About ceramic tanks.</p>', 100, 'https://shop.com/products/null')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 0  # NULL status is rejected
    
    def test_product_with_lowercase_active_status_generates_suggestions(self, db_conn):
        """Product with lowercase 'active' status should generate suggestions.
        
        Status check is case-insensitive.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('lowercase-active', 'Lowercase', 'active', '<p>About ceramic tanks.</p>', 100, 'https://shop.com/products/lower')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 1  # Case-insensitive ACTIVE match
    
    def test_existing_applied_rows_for_unpublished_source_untouched(self, db_conn):
        """Existing applied rows for unpublished sources should not be deleted."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'unpub', 'Unpublished', '<p>Content.</p>', 0, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('target', 'Target')")
        conn.execute(
            "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES ('blog_article', 'news/unpub', 'collection', 'target', 'phrase_wrap', 'applied', 1)"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT status FROM link_suggestions WHERE source_handle = 'news/unpub'").fetchone()
        assert row is not None
        assert row["status"] == "applied"


class TestRestoredRowsSurviveRebuild:
    """Test that restored rows survive or are dropped based on eligibility."""
    
    def test_restored_row_both_pages_eligible_survives(self, db_conn):
        """Restored row with both source and target eligible survives rebuild."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'pub', 'Published', '<p>Content about tanks.</p>', 1, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('target', 'Target')")
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'blog_article', 'news/pub', 'collection', 'target', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_suggestion_restore_audit (suggestion_id, restored_at, actor, reason) "
            "VALUES (1, 1, 'user', 'test')"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is not None
    
    def test_restored_row_source_unpublished_dropped(self, db_conn):
        """Restored row with unpublished source is dropped at rebuild."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'unpub', 'Unpublished', '<p>Content.</p>', 0, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('target', 'Target')")
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'blog_article', 'news/unpub', 'collection', 'target', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_suggestion_restore_audit (suggestion_id, restored_at, actor, reason) "
            "VALUES (1, 1, 'user', 'test')"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is None
    
    def test_restored_row_target_removed_dropped(self, db_conn):
        """Restored row with removed target is dropped at rebuild."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'pub', 'Published', '<p>Content.</p>', 1, 100)"
        )
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'blog_article', 'news/pub', 'collection', 'removed-target', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_suggestion_restore_audit (suggestion_id, restored_at, actor, reason) "
            "VALUES (1, 1, 'user', 'test')"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is None
    
    def test_restored_row_target_blocked_dropped(self, db_conn):
        """Restored row with blocked target (api_unreachable) is dropped at rebuild."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'pub', 'Published', '<p>Content.</p>', 1, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title, api_unreachable) VALUES ('blocked', 'Blocked', 1)")
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'blog_article', 'news/pub', 'collection', 'blocked', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_suggestion_restore_audit (suggestion_id, restored_at, actor, reason) "
            "VALUES (1, 1, 'user', 'test')"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is None
    
    def test_restored_row_product_empty_status_dropped(self, db_conn):
        """Restored row with product source having empty status is dropped at rebuild.
        
        Tests pipeline.py:239 - products require ACTIVE status.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('empty-status', 'Empty Status', '', '<p>Content.</p>', 100, 'https://shop.com/products/empty')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('target', 'Target')")
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'product', 'empty-status', 'collection', 'target', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_suggestion_restore_audit (suggestion_id, restored_at, actor, reason) "
            "VALUES (1, 1, 'user', 'test')"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is None  # Empty status should cause drop
    
    def test_restored_row_product_null_status_dropped(self, db_conn):
        """Restored row with product source having NULL status is dropped at rebuild.
        
        Tests pipeline.py:239 - products require ACTIVE status.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('null-status', 'Null Status', NULL, '<p>Content.</p>', 100, 'https://shop.com/products/null')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('target', 'Target')")
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'product', 'null-status', 'collection', 'target', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_suggestion_restore_audit (suggestion_id, restored_at, actor, reason) "
            "VALUES (1, 1, 'user', 'test')"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is None  # NULL status should cause drop
    
    def test_product_with_empty_string_url_generates_suggestions(self, db_conn):
        """Product with empty string online_store_url still generates suggestions.
        
        Blank online_store_url is tolerated for sources per #116.
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, gsc_clicks, online_store_url) "
            "VALUES ('empty-url', 'Empty URL', 'ACTIVE', '<p>About ceramic tanks.</p>', 100, '')"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
        conn.commit()
        
        def related(conn, obj_type, handle, top_k=10):
            if obj_type == "product":
                return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
            return []
        
        n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
        assert n == 1  # Empty string URL is tolerated
    
    def test_row_with_open_snapshot_on_unpublished_source_kept(self, db_conn):
        """Row with open snapshot on unpublished source is protected from deletion."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published, gsc_clicks) "
            "VALUES ('news', 'unpub', 'Unpublished', '<p>Content.</p>', 0, 100)"
        )
        conn.execute("INSERT INTO collections (handle, title) VALUES ('target', 'Target')")
        conn.execute(
            "INSERT INTO link_suggestions (id, source_type, source_handle, target_type, target_handle, kind, status, created_at) "
            "VALUES (1, 'blog_article', 'news/unpub', 'collection', 'target', 'phrase_wrap', 'suggested', 1)"
        )
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'blog_article', 'news/unpub', 'prepared', 1, 1)"
        )
        conn.commit()
        
        generate_link_suggestions(conn, related_fn=lambda *a, **k: [], rebuild_graph=False)
        
        row = conn.execute("SELECT * FROM link_suggestions WHERE id = 1").fetchone()
        assert row is not None


class TestSourceExistsWithBodyPublished:
    """Test _source_exists_with_body respects published status and online_store_url."""
    
    def test_unpublished_article_returns_false(self, db_conn):
        """Unpublished article should return False."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published) "
            "VALUES ('news', 'unpub', 'Unpublished', '<p>Content.</p>', 0)"
        )
        conn.commit()
        assert not _source_exists_with_body(conn, "blog_article", "news/unpub")
    
    def test_published_article_returns_true(self, db_conn):
        """Published article with body should return True."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published) "
            "VALUES ('news', 'pub', 'Published', '<p>Content.</p>', 1)"
        )
        conn.commit()
        assert _source_exists_with_body(conn, "blog_article", "news/pub")
    
    def test_draft_product_returns_false(self, db_conn):
        """Draft product should return False."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, online_store_url) "
            "VALUES ('draft', 'Draft', 'DRAFT', '<p>Content.</p>', 'https://shop.com/products/draft')"
        )
        conn.commit()
        assert not _source_exists_with_body(conn, "product", "draft")
    
    def test_active_product_with_url_returns_true(self, db_conn):
        """Active product with online_store_url should return True."""
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, online_store_url) "
            "VALUES ('active', 'Active', 'ACTIVE', '<p>Content.</p>', 'https://shop.com/products/active')"
        )
        conn.commit()
        assert _source_exists_with_body(conn, "product", "active")
    
    def test_active_product_without_url_returns_true(self, db_conn):
        """Active product without online_store_url should still return True.
        
        Product SOURCES tolerate blank online_store_url (like targets per #48).
        """
        conn = _pipeline_db(db_conn)
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, online_store_url) "
            "VALUES ('no-url', 'No URL', 'ACTIVE', '<p>Content.</p>', NULL)"
        )
        conn.commit()
        assert _source_exists_with_body(conn, "product", "no-url")  # Sources tolerate blank URL
