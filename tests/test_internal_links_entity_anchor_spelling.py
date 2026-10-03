"""Tests for entity-safe insert, nested anchor guard, preview lock, spelling, and unpublished sources.

These tests cover the changes in the PR:
- A. Entity-safe insert + entity-equivalence
- B. Nested/escaped anchor guard
- C. Preview respects open-write lock
- D. New-text spelling check
- E. Skip unpublished sources
"""
import json
import sqlite3
import pytest
from unittest.mock import Mock, patch

from shopifyseo.internal_links.safety import (
    build_edit, guard_edit, html_equivalent, LinkConflict,
    _is_inside_escaped_anchor, _has_nested_anchors, _find_escaped_anchor_regions,
    _normalize_text_entities, _normalize_for_entity_comparison,
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
    
    def test_normalize_ampersand_entity(self):
        """&amp; should normalize to &."""
        assert _normalize_text_entities("Tom &amp; Jerry") == "Tom & Jerry"
    
    def test_normalize_preserves_nbsp(self):
        assert "&nbsp;" in _normalize_text_entities("hello&nbsp;world")
    
    def test_normalize_for_entity_comparison_preserves_tag_entities(self):
        """Entities in attributes should not be normalized."""
        html = '<a href="/path?a=1&amp;b=2">text&#x27;s</a>'
        result = _normalize_for_entity_comparison(html)
        assert "text's" in result
        assert "&amp;" in result


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
    
    def test_spec_case4_phrase_wrap_basic(self):
        """Spec test 4: basic phrase_wrap via build_edit."""
        body = '<p>Our ceramic tanks collection is popular.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        result = build_edit(body, edit, "https://example.com/ceramic")
        assert '<a href="https://example.com/ceramic">ceramic tanks</a>' in result
    
    def test_spec_case5_guard_edit_exact_reconstruction(self):
        """Spec test 5: guard_edit verifies exact reconstruction."""
        old = '<p>Our ceramic tanks are popular.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        new = build_edit(old, edit, "https://example.com/ceramic")
        guard_edit(old, new, edit, "https://example.com/ceramic")
    
    def test_spec_case6_guard_edit_blocks_unauthorized_changes(self):
        """Spec test 6: guard_edit blocks changes outside approved insertion."""
        old = '<p>Our ceramic tanks are popular.</p>'
        edit = {"anchor_phrase": "ceramic tanks"}
        tampered_new = '<p>Our <a href="https://example.com/ceramic">ceramic tanks</a> are MODIFIED.</p>'
        with pytest.raises(LinkConflict):
            guard_edit(old, tampered_new, edit, "https://example.com/ceramic")


# =============================================================================
# C. Preview Respects Open-Write Lock Tests
# =============================================================================

def _test_db():
    """Create a minimal test database."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
    
    def test_check_page_write_pending_true(self):
        """Should return True when there's a pending snapshot on the page."""
        conn = _test_db()
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'product', 'test-handle', 'needs_reconciliation', 1, 1)"
        )
        conn.commit()
        assert _check_page_write_pending(conn, "product", "test-handle")
    
    def test_check_page_write_pending_false(self):
        """Should return False when no pending snapshots."""
        conn = _test_db()
        conn.execute(
            "INSERT INTO link_body_snapshots (suggestion_id, source_type, source_handle, status, created_at, updated_at) "
            "VALUES (1, 'product', 'test-handle', 'applied', 1, 1)"
        )
        conn.commit()
        assert not _check_page_write_pending(conn, "product", "test-handle")
    
    def test_preview_returns_page_write_pending(self):
        """Preview should return allowed=false with page_write_pending code."""
        conn = _test_db()
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
    
    def test_manual_weave_preview_only_returns_page_write_pending(self):
        """Manual-weave with preview_only=True should return allowed=false shape."""
        from shopifyseo.internal_links.manual_weave import submit_manual_weave
        
        conn = _test_db()
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

def _pipeline_db():
    """Create database for pipeline tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
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
    
    def test_unpublished_article_skipped(self):
        """Unpublished blog article should not generate suggestions."""
        conn = _pipeline_db()
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
    
    def test_published_article_generates_suggestions(self):
        """Published blog article should generate suggestions."""
        conn = _pipeline_db()
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
    
    def test_draft_product_skipped(self):
        """Draft product (status != ACTIVE) should not generate suggestions."""
        conn = _pipeline_db()
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
    
    def test_active_product_with_online_store_url_generates_suggestions(self):
        """Active product with online_store_url should generate suggestions."""
        conn = _pipeline_db()
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
    
    def test_active_product_without_online_store_url_skipped(self):
        """Active product WITHOUT online_store_url should not generate suggestions."""
        conn = _pipeline_db()
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
        assert n == 0
    
    def test_existing_applied_rows_for_unpublished_source_untouched(self):
        """Existing applied rows for unpublished sources should not be deleted."""
        conn = _pipeline_db()
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
    
    def test_restored_row_both_pages_eligible_survives(self):
        """Restored row with both source and target eligible survives rebuild."""
        conn = _pipeline_db()
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
    
    def test_restored_row_source_unpublished_dropped(self):
        """Restored row with unpublished source is dropped at rebuild."""
        conn = _pipeline_db()
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
    
    def test_restored_row_target_removed_dropped(self):
        """Restored row with removed target is dropped at rebuild."""
        conn = _pipeline_db()
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
    
    def test_restored_row_target_blocked_dropped(self):
        """Restored row with blocked target (api_unreachable) is dropped at rebuild."""
        conn = _pipeline_db()
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
    
    def test_row_with_open_snapshot_on_unpublished_source_kept(self):
        """Row with open snapshot on unpublished source is protected from deletion."""
        conn = _pipeline_db()
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
    
    def test_unpublished_article_returns_false(self):
        """Unpublished article should return False."""
        conn = _pipeline_db()
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published) "
            "VALUES ('news', 'unpub', 'Unpublished', '<p>Content.</p>', 0)"
        )
        conn.commit()
        assert not _source_exists_with_body(conn, "blog_article", "news/unpub")
    
    def test_published_article_returns_true(self):
        """Published article with body should return True."""
        conn = _pipeline_db()
        conn.execute(
            "INSERT INTO blog_articles (blog_handle, handle, title, body, is_published) "
            "VALUES ('news', 'pub', 'Published', '<p>Content.</p>', 1)"
        )
        conn.commit()
        assert _source_exists_with_body(conn, "blog_article", "news/pub")
    
    def test_draft_product_returns_false(self):
        """Draft product should return False."""
        conn = _pipeline_db()
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, online_store_url) "
            "VALUES ('draft', 'Draft', 'DRAFT', '<p>Content.</p>', 'https://shop.com/products/draft')"
        )
        conn.commit()
        assert not _source_exists_with_body(conn, "product", "draft")
    
    def test_active_product_with_url_returns_true(self):
        """Active product with online_store_url should return True."""
        conn = _pipeline_db()
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, online_store_url) "
            "VALUES ('active', 'Active', 'ACTIVE', '<p>Content.</p>', 'https://shop.com/products/active')"
        )
        conn.commit()
        assert _source_exists_with_body(conn, "product", "active")
    
    def test_active_product_without_url_returns_false(self):
        """Active product without online_store_url should return False."""
        conn = _pipeline_db()
        conn.execute(
            "INSERT INTO products (handle, title, status, description_html, online_store_url) "
            "VALUES ('no-url', 'No URL', 'ACTIVE', '<p>Content.</p>', NULL)"
        )
        conn.commit()
        assert not _source_exists_with_body(conn, "product", "no-url")
