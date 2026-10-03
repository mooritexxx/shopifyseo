"""Unit tests for FAQ answer sanitization and escaped markup detection.

Test cases from the 2026-10-03 trace covering:
1. HTML <p> answer → single real <p>
2. Two-paragraph answer → two <p>
3. Answer containing <a href> → plain text, no &lt;a
4. <strong> kept
5. '5 < 10 &' stays escaped as text and isn't flagged
6. Guard flags &lt;p&gt; and &lt;a
7. Guard ignores '&lt; 5'
8. Full generate_article_draft run with stubbed AI returning HTML answers
"""

import html
import pytest

from shopifyseo.dashboard_ai_engine_parts._article_draft import _faq_answer_to_html
from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    escaped_markup_gaps,
)


class TestFaqAnswerToHtml:
    """Test _faq_answer_to_html helper function."""

    def test_html_p_answer_becomes_single_real_p(self):
        """Case 1: AI answer with <p> wrapper → single real <p> in output."""
        answer = "<p>At its core, vaping is the inhalation of vapor created by an electronic device.</p>"
        result = _faq_answer_to_html(answer)
        assert result == "<p>At its core, vaping is the inhalation of vapor created by an electronic device.</p>"
        assert "&lt;p" not in result
        assert "&lt;/p" not in result

    def test_two_paragraph_answer_becomes_two_p_elements(self):
        """Case 2: Two-paragraph answer → two <p> siblings."""
        answer = "<p>First paragraph about vaping devices.</p><p>Second paragraph about flavours.</p>"
        result = _faq_answer_to_html(answer)
        assert result.count("<p>") == 2
        assert result.count("</p>") == 2
        assert "First paragraph" in result
        assert "Second paragraph" in result
        assert "&lt;p" not in result

    def test_two_paragraph_via_double_newline(self):
        """Two-paragraph answer via double newlines → two <p> siblings."""
        answer = "First paragraph about vaping devices.\n\nSecond paragraph about flavours."
        result = _faq_answer_to_html(answer)
        assert result.count("<p>") == 2
        assert result.count("</p>") == 2
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_anchor_tag_converted_to_plain_text(self):
        """Case 3: Answer with <a href> → plain text, no &lt;a."""
        answer = 'Check out our <a href="https://vapely.ca/collections/pods">pod collection</a> for options.'
        result = _faq_answer_to_html(answer)
        assert "pod collection" in result
        assert "<a " not in result
        assert "&lt;a" not in result
        assert "href" not in result
        assert "<p>" in result and "</p>" in result

    def test_strong_tag_preserved(self):
        """Case 4: <strong> is kept as a real tag."""
        answer = "The best choice is the <strong>STLTH Pro</strong> for most users."
        result = _faq_answer_to_html(answer)
        assert "<strong>STLTH Pro</strong>" in result
        assert "&lt;strong" not in result
        assert "<p>" in result

    def test_em_tag_preserved(self):
        """<em> is also kept as a real tag."""
        answer = "This is <em>important</em> information about vaping."
        result = _faq_answer_to_html(answer)
        assert "<em>important</em>" in result
        assert "&lt;em" not in result

    def test_br_tag_preserved(self):
        """<br> is kept as a real tag."""
        answer = "Line one.<br>Line two."
        result = _faq_answer_to_html(answer)
        assert "<br>" in result
        assert "&lt;br" not in result

    def test_less_than_in_text_properly_escaped(self):
        """Case 5: '5 < 10 &' stays escaped as text and isn't flagged."""
        answer = "The nicotine level is 5 < 10 & that's within safe limits."
        result = _faq_answer_to_html(answer)
        assert "5 &lt; 10 &amp;" in result
        assert "<p>" in result
        gaps = escaped_markup_gaps(result)
        assert gaps == [], "Legitimate escaped text like '5 < 10' should not be flagged"

    def test_script_tag_removed(self):
        """<script> tags are removed but text kept."""
        answer = "Normal text <script>alert('xss')</script> more text."
        result = _faq_answer_to_html(answer)
        assert "<script" not in result
        assert "alert" in result
        assert "Normal text" in result
        assert "more text" in result

    def test_div_tag_removed_text_kept(self):
        """<div> tags are removed but text content kept."""
        answer = "<div>Important content here</div>"
        result = _faq_answer_to_html(answer)
        assert "<div" not in result
        assert "Important content here" in result
        assert "<p>" in result

    def test_nested_tags_handled(self):
        """Nested tags are properly handled."""
        answer = "<p><strong>Bold</strong> and <em>italic</em> text</p>"
        result = _faq_answer_to_html(answer)
        assert "<strong>Bold</strong>" in result
        assert "<em>italic</em>" in result
        assert result.count("<p>") == 1

    def test_empty_answer(self):
        """Empty answer returns empty string."""
        assert _faq_answer_to_html("") == ""
        assert _faq_answer_to_html("   ") == ""
        assert _faq_answer_to_html(None) == ""

    def test_plain_text_answer(self):
        """Plain text answer is properly wrapped in <p>."""
        answer = "Simple plain text answer without any markup."
        result = _faq_answer_to_html(answer)
        assert result == "<p>Simple plain text answer without any markup.</p>"

    def test_html_entities_in_answer(self):
        """HTML entities in answers are handled correctly."""
        answer = "The ratio is 50/50 &amp; that's common."
        result = _faq_answer_to_html(answer)
        assert "&amp;" in result
        assert "<p>" in result

    def test_multiple_anchor_tags_all_converted(self):
        """Multiple <a> tags are all converted to plain text."""
        answer = 'See <a href="/a">link one</a> and <a href="/b">link two</a>.'
        result = _faq_answer_to_html(answer)
        assert "link one" in result
        assert "link two" in result
        assert "<a " not in result
        assert "&lt;a" not in result


class TestEscapedMarkupGaps:
    """Test escaped_markup_gaps compliance checker."""

    def test_flags_escaped_p_tag(self):
        """Case 6a: Guard flags &lt;p&gt;."""
        body = "<p>&lt;p&gt;This is double escaped&lt;/p&gt;</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]
        assert "escaped HTML tags" in gaps[0]

    def test_flags_escaped_a_tag(self):
        """Case 6b: Guard flags &lt;a."""
        body = '<p>Click &lt;a href="https://example.com"&gt;here&lt;/a&gt;</p>'
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]

    def test_flags_escaped_closing_tag(self):
        """Guard flags &lt;/p&gt;."""
        body = "<p>Text &lt;/p&gt; more text</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]

    def test_ignores_less_than_with_space(self):
        """Case 7: Guard ignores '&lt; 5'."""
        body = "<p>The value is &lt; 5 units, which is acceptable.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt; 5' should not be flagged as escaped markup"

    def test_ignores_less_than_with_digit(self):
        """Guard ignores '&lt;5'."""
        body = "<p>If x &lt;5 then it works.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt;5' (digit) should not be flagged"

    def test_ignores_legitimate_math_expression(self):
        """Guard ignores legitimate math expressions."""
        body = "<p>When n &lt; 10 &amp; m &gt; 5, the formula applies.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == []

    def test_flags_escaped_strong(self):
        """Guard flags &lt;strong&gt;."""
        body = "<p>This is &lt;strong&gt;bold&lt;/strong&gt; text.</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]

    def test_flags_escaped_div(self):
        """Guard flags &lt;div&gt;."""
        body = "<p>&lt;div class='test'&gt;content&lt;/div&gt;</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1

    def test_flags_amp_escaped_lt(self):
        """Guard flags &amp;lt; (double encoded)."""
        body = "<p>&amp;lt;p&gt;Double encoded&amp;lt;/p&gt;</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1

    def test_empty_body_no_gaps(self):
        """Empty body returns no gaps."""
        assert escaped_markup_gaps("") == []
        assert escaped_markup_gaps(None) == []

    def test_clean_body_no_gaps(self):
        """Properly formatted body has no gaps."""
        body = """
        <h2>FAQ Section</h2>
        <h3>What is vaping?</h3>
        <p>Vaping is the inhalation of vapor from an electronic device.</p>
        <h3>Which device is best?</h3>
        <p>The best device depends on your preferences and <strong>budget</strong>.</p>
        """
        gaps = escaped_markup_gaps(body)
        assert gaps == []


class TestIntegrationFaqAnswerAndGaps:
    """Integration tests ensuring _faq_answer_to_html output passes escaped_markup_gaps."""

    def test_html_answer_sanitized_passes_compliance(self):
        """HTML answer after sanitization passes the escaped markup check."""
        html_answer = "<p>At its core, vaping involves <a href='/link'>devices</a> that heat liquid.</p>"
        sanitized = _faq_answer_to_html(html_answer)
        gaps = escaped_markup_gaps(sanitized)
        assert gaps == [], f"Sanitized answer should pass compliance, got: {sanitized}"

    def test_multi_paragraph_html_passes_compliance(self):
        """Multi-paragraph HTML answer after sanitization passes compliance."""
        html_answer = "<p>First paragraph.</p><p>Second <strong>paragraph</strong>.</p>"
        sanitized = _faq_answer_to_html(html_answer)
        gaps = escaped_markup_gaps(sanitized)
        assert gaps == []

    def test_answer_with_links_passes_after_sanitization(self):
        """Answer with links passes compliance after sanitization strips them."""
        html_answer = 'Check <a href="https://store.com/pods">our pods</a> and <a href="/flavours">flavours</a>.'
        sanitized = _faq_answer_to_html(html_answer)
        gaps = escaped_markup_gaps(sanitized)
        assert gaps == []
        assert "<a " not in sanitized
        assert "&lt;a" not in sanitized

    def test_answer_with_special_chars_passes(self):
        """Answer with special characters (< >) passes compliance."""
        answer = "If nicotine < 20mg & strength > minimum, it's legal in Canada."
        sanitized = _faq_answer_to_html(answer)
        gaps = escaped_markup_gaps(sanitized)
        assert gaps == []
        assert "&lt;" in sanitized
        assert "&gt;" in sanitized
        assert "&amp;" in sanitized


class TestNormalizeFlavourIntegration:
    """Verify normalize_flavor_to_flavour is still applied after sanitization."""

    def test_flavor_normalized_in_faq_pipeline(self):
        """Case 8 (partial): normalize_flavor_to_flavour still applied after sanitization.

        Note: The full integration test with generate_article_draft is in
        test_article_draft_runs.py. This test verifies the helper doesn't break
        the normalization that happens afterward.
        """
        from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import normalize_flavor_to_flavour

        answer = "The best flavor is Grape Ice."
        sanitized = _faq_answer_to_html(answer)
        normalized = normalize_flavor_to_flavour(sanitized, log_changes=False)
        assert "flavour" in normalized.lower()
        assert "flavor" not in normalized.lower()
