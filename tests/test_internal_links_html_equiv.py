"""Tests for html_equivalent and whitespace-tolerant apply/reconcile."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from internal_links_support import BASE, OLD, Shopify, apply, database, preview
from shopifyseo.internal_links import apply as service
from shopifyseo.internal_links.safety import html_equivalent, body_hash


class TestHtmlEquivalentUnit:
    """Unit tests for html_equivalent function."""
    
    def test_identical_strings(self):
        """Identical strings are equivalent."""
        html = "<p>Hello</p><p>World</p>"
        assert html_equivalent(html, html)
    
    def test_inter_block_whitespace_tolerated(self):
        """Whitespace between block-level tags is tolerated."""
        assert html_equivalent("</p><p>", "</p>\n<p>")
        assert html_equivalent("</p><p>", "</p>\n\n  <p>")
        assert html_equivalent("<div>\n<p>", "<div><p>")
        assert html_equivalent("</p>\n<p>", "</p>  \n\t  <p>")
        assert html_equivalent("</div></section>", "</div>\n</section>")
        assert html_equivalent("<article><p>", "<article>\n<p>")
    
    def test_multiple_block_tags(self):
        """Multiple block-level tag transitions are handled."""
        a = "<div><p>Text</p></div>"
        b = "<div>\n<p>Text</p>\n</div>"
        assert html_equivalent(a, b)
    
    def test_leading_trailing_whitespace_tolerated(self):
        """Leading/trailing document whitespace is tolerated (Q1 default)."""
        assert html_equivalent("  <p>Hello</p>  ", "<p>Hello</p>")
        assert html_equivalent("<p>Hello</p>\n", "<p>Hello</p>")
        assert html_equivalent("\n\n<p>Hello</p>", "<p>Hello</p>")
    
    def test_text_change_fails(self):
        """A text change is not equivalent."""
        assert not html_equivalent("<p>Hello</p>", "<p>World</p>")
        assert not html_equivalent("<p>Hello world</p>", "<p>Hello world!</p>")
    
    def test_attribute_change_fails(self):
        """An attribute change is not equivalent."""
        assert not html_equivalent('<p class="a">Text</p>', '<p class="b">Text</p>')
        assert not html_equivalent('<p title="x">Text</p>', '<p title="y">Text</p>')
        assert not html_equivalent('<p>Text</p>', '<p class="a">Text</p>')
    
    def test_href_change_fails(self):
        """An href change is not equivalent."""
        assert not html_equivalent(
            '<a href="/products/a">Link</a>',
            '<a href="/products/b">Link</a>'
        )
    
    def test_extra_link_fails(self):
        """An extra link is not equivalent."""
        a = '<p>Text</p>'
        b = '<p>Text <a href="/x">link</a></p>'
        assert not html_equivalent(a, b)
    
    def test_missing_link_fails(self):
        """A missing link is not equivalent."""
        a = '<p>Text <a href="/x">link</a></p>'
        b = '<p>Text link</p>'
        assert not html_equivalent(a, b)
    
    def test_tag_rename_fails(self):
        """A tag rename is not equivalent."""
        assert not html_equivalent("<p>Text</p>", "<div>Text</div>")
        assert not html_equivalent("<h1>Title</h1>", "<h2>Title</h2>")
    
    def test_inline_space_preserved(self):
        """Space between inline tags is preserved (visible space)."""
        a = '</a> <strong>'
        b = '</a><strong>'
        assert not html_equivalent(a, b)
        
        a2 = '<p>Hello </a> <strong>world</strong></p>'
        b2 = '<p>Hello </a><strong>world</strong></p>'
        assert not html_equivalent(a2, b2)
    
    def test_entity_difference_fails(self):
        """Entity encoding differences are not equivalent."""
        assert not html_equivalent("<p>A &amp; B</p>", "<p>A & B</p>")
        assert not html_equivalent("<p>&lt;tag&gt;</p>", "<p><tag></p>")
    
    def test_pre_whitespace_preserved(self):
        """Whitespace inside <pre> is preserved."""
        a = "<pre>  code\n  here  </pre>"
        b = "<pre>code here</pre>"
        assert not html_equivalent(a, b)
        
        a2 = "<pre>\n  code\n</pre>"
        b2 = "<pre>  code</pre>"
        assert not html_equivalent(a2, b2)
    
    def test_script_style_whitespace_preserved(self):
        """Whitespace inside <script>/<style> is preserved."""
        a = "<script>  var x = 1;  </script>"
        b = "<script>var x = 1;</script>"
        assert not html_equivalent(a, b)
        
        a2 = "<style>  .a { color: red; }  </style>"
        b2 = "<style>.a { color: red; }</style>"
        assert not html_equivalent(a2, b2)
    
    def test_textarea_whitespace_preserved(self):
        """Whitespace inside <textarea> is preserved."""
        a = "<textarea>  some text  </textarea>"
        b = "<textarea>some text</textarea>"
        assert not html_equivalent(a, b)
    
    def test_text_node_whitespace_preserved(self):
        """Whitespace inside text nodes is preserved."""
        # Multiple spaces in text matter
        assert not html_equivalent("<p>Hello  world</p>", "<p>Hello world</p>")
        # Newlines in text matter
        assert not html_equivalent("<p>Hello\nworld</p>", "<p>Hello world</p>")
    
    def test_attribute_whitespace_preserved(self):
        """Whitespace inside attributes is preserved."""
        # We're not touching attributes at all
        a = '<p title="hello world">Text</p>'
        b = '<p title="hello  world">Text</p>'
        assert not html_equivalent(a, b)
    
    def test_br_vs_br_slash(self):
        """br vs br/ is different (we don't normalize tag styles)."""
        # This is technically different HTML
        assert not html_equivalent("<br>", "<br/>")
        assert not html_equivalent("<br>", "<br />")
    
    def test_complex_body_with_whitespace_variations(self):
        """Complex body with only inter-block whitespace differences."""
        a = '<p>First paragraph.</p><p>Second paragraph.</p>'
        b = '<p>First paragraph.</p>\n<p>Second paragraph.</p>'
        assert html_equivalent(a, b)
        
        a2 = '<div><p>Nested</p></div><p>After</p>'
        b2 = '<div>\n  <p>Nested</p>\n</div>\n<p>After</p>'
        assert html_equivalent(a2, b2)
    
    def test_mixed_block_inline(self):
        """Mixed block and inline tags - only block boundaries tolerate whitespace."""
        # Block transition: tolerate
        assert html_equivalent("</p><div>", "</p>\n<div>")
        # Inline adjacent: preserve space
        assert not html_equivalent("</em><strong>", "</em> <strong>")
    
    def test_empty_strings(self):
        """Empty strings are equivalent."""
        assert html_equivalent("", "")
        assert html_equivalent("  ", "")
        assert html_equivalent("", "\n")


class TestApplyWithWhitespaceTolerance:
    """Tests for apply_suggestion with whitespace-tolerant comparison."""
    
    def test_apply_with_shopify_newline_insertion(self):
        """Apply succeeds when Shopify returns body with newline inserted between blocks."""
        conn = database()
        live = Shopify()
        
        # Shopify returns body with newline inserted between </p><p>
        def push_with_newline(source_type, row, body):
            # Simulate Shopify re-serializing: insert \n between blocks
            modified = body.replace("</p><p>", "</p>\n<p>")
            live.body = modified
            return modified
        
        live.push.side_effect = push_with_newline
        
        result = apply(conn, live)
        
        assert result["status"] == "applied"
        assert live.push.call_count == 1
        
        # Local body should match what Shopify returned
        local_body = conn.execute("SELECT description_html FROM products").fetchone()[0]
        assert local_body == live.body
        assert "\n" in local_body  # Shopify's newline is preserved
        
        # Snapshot new_body should be updated to what Shopify returned
        snapshot = conn.execute("SELECT new_body FROM link_body_snapshots").fetchone()
        assert snapshot["new_body"] == live.body
    
    def test_apply_with_text_change_fails(self):
        """Apply fails when Shopify returns body with text change."""
        conn = database()
        live = Shopify()
        
        def push_with_text_change(source_type, row, body):
            # Simulate Shopify changing actual text
            modified = body.replace("ceramic tanks", "CERAMIC TANKS")
            live.body = modified
            return modified
        
        live.push.side_effect = push_with_text_change
        
        with pytest.raises(RuntimeError, match="different HTML"):
            apply(conn, live)
        
        # Should be in needs_reconciliation state
        snapshot = conn.execute("SELECT status FROM link_body_snapshots").fetchone()
        assert snapshot["status"] == "needs_reconciliation"
    
    def test_undo_after_whitespace_tolerant_apply(self):
        """Undo succeeds after a whitespace-tolerant apply."""
        conn = database()
        live = Shopify()
        
        # Apply with newline insertion
        def push_with_newline(source_type, row, body):
            modified = body.replace("</p><p>", "</p>\n<p>")
            live.body = modified
            return modified
        
        live.push.side_effect = push_with_newline
        
        apply(conn, live)
        
        # Reset push to return exact body (for undo)
        live.push.side_effect = live._push
        
        # Undo should work - snapshot new_body was updated to match live
        result = service.undo_suggestion(conn, 1, BASE, fetch_fn=live.fetch, push_fn=live.push)
        assert result["status"] == "undone"
        assert live.body == OLD  # Back to original


class TestReconcileWithWhitespaceTolerance:
    """Tests for reconcile_suggestion with whitespace-tolerant comparison."""
    
    def test_reconcile_with_whitespace_difference(self):
        """Reconcile succeeds when live differs from new_body only by inter-tag whitespace."""
        conn = database()
        live = Shopify()
        
        # Simulate timeout during apply
        live.push.side_effect = TimeoutError()
        with pytest.raises(TimeoutError):
            apply(conn, live)
        
        # Set live body to what Shopify would have stored (with newline)
        expected_new = conn.execute("SELECT new_body FROM link_body_snapshots").fetchone()[0]
        live.body = expected_new.replace("</p><p>", "</p>\n<p>")
        
        # Reconcile should succeed using html_equivalent
        result = service.reconcile_suggestion(conn, 1, BASE, fetch_fn=live.fetch)
        assert result["status"] == "applied"
        
        # Local body should equal live body
        local_body = conn.execute("SELECT description_html FROM products").fetchone()[0]
        assert local_body == live.body
        
        # No additional push should happen
        assert live.push.call_count == 1  # Only the original failed call
    
    def test_reconcile_with_content_drift_fails(self):
        """Reconcile fails when live has content drift (not just whitespace)."""
        conn = database()
        live = Shopify()
        
        live.push.side_effect = TimeoutError()
        with pytest.raises(TimeoutError):
            apply(conn, live)
        
        # Set live body to something completely different
        live.body = OLD + "<p>Someone else edited this.</p>"
        
        with pytest.raises(service.LinkConflict, match="neither backup"):
            service.reconcile_suggestion(conn, 1, BASE, fetch_fn=live.fetch)
        
        # Should still be in needs_reconciliation
        snapshot = conn.execute("SELECT status FROM link_body_snapshots").fetchone()
        assert snapshot["status"] == "needs_reconciliation"
    
    def test_reconcile_not_written_with_whitespace(self):
        """Reconcile returns not_written when live matches old_body with whitespace differences."""
        conn = database()
        live = Shopify()
        
        # Simulate push failure
        live.push.side_effect = RuntimeError("Network error")
        with pytest.raises(RuntimeError):
            apply(conn, live)
        
        # Live body equals old with whitespace difference
        live.body = OLD.replace("</p><p>", "</p>\n<p>")
        
        result = service.reconcile_suggestion(conn, 1, BASE, fetch_fn=live.fetch)
        assert result["status"] == "not_written"
        
        # Suggestion should still be suggested (not applied)
        sug = conn.execute("SELECT status FROM link_suggestions").fetchone()
        assert sug["status"] == "suggested"


class TestNewlineSeparatorInsert:
    """Tests for the optional newline-separator insert in build_edit."""
    
    def test_insert_preserves_newline_style(self):
        """When body uses newlines between blocks, inserted paragraph uses newline prefix."""
        conn = database()
        live = Shopify()
        
        # Body with newline between paragraphs
        body_with_newlines = "<p>First paragraph.</p>\n<p>Second paragraph.</p>"
        live.body = body_with_newlines
        conn.execute("UPDATE products SET description_html = ?", (body_with_newlines,))
        
        edit = {
            "anchor_phrase": "Ceramic Tanks",
            "insert_sentence": "Explore Ceramic Tanks for more options.",
            "insert_after_text": "Second paragraph.",
        }
        conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_edit_json=?", (json.dumps(edit),))
        conn.commit()
        
        result = apply(conn, live)
        assert result["status"] == "applied"
        
        # The inserted paragraph should start with \n to match the style
        assert "\n<p>Explore" in live.body
    
    def test_insert_without_newlines_stays_compact(self):
        """When body doesn't use newlines between blocks, inserted paragraph is compact."""
        conn = database()
        live = Shopify()
        
        # Body without newlines between paragraphs
        body_compact = "<p>First paragraph.</p><p>Second paragraph.</p>"
        live.body = body_compact
        conn.execute("UPDATE products SET description_html = ?", (body_compact,))
        
        edit = {
            "anchor_phrase": "Ceramic Tanks",
            "insert_sentence": "Explore Ceramic Tanks for more options.",
            "insert_after_text": "Second paragraph.",
        }
        conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_edit_json=?", (json.dumps(edit),))
        conn.commit()
        
        result = apply(conn, live)
        assert result["status"] == "applied"
        
        # The inserted paragraph should NOT have \n prefix
        assert "</p><p>Explore" in live.body
