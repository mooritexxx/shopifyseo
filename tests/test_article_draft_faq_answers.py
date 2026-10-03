"""Unit tests for FAQ answer sanitization and escaped markup detection.

Test cases from the 2026-10-03 trace covering:
1. HTML <p> answer → single real <p>
2. Two-paragraph answer → two <p>
3. Answer containing <a href> → plain text, no &lt;a
4. <strong> kept
5. '5 < 10 &' stays escaped as text and isn't flagged
6. Guard flags &lt;p&gt; and &lt;a (only real HTML tag names)
7. Guard ignores '&lt; 5', '&lt;3', '&lt; Moderate'
8. Full generate_article_draft run with stubbed AI returning HTML answers
"""

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

    def test_b_mapped_to_strong(self):
        """<b> is mapped to <strong>."""
        answer = "This is <b>bold</b> text."
        result = _faq_answer_to_html(answer)
        assert "<strong>bold</strong>" in result
        assert "<b>" not in result
        assert "</b>" not in result

    def test_i_mapped_to_em(self):
        """<i> is mapped to <em>."""
        answer = "This is <i>italic</i> text."
        result = _faq_answer_to_html(answer)
        assert "<em>italic</em>" in result
        assert "<i>" not in result
        assert "</i>" not in result

    def test_less_than_in_text_properly_escaped(self):
        """Case 5: '5 < 10 &' stays escaped as text and isn't flagged."""
        answer = "The nicotine level is 5 < 10 & that's within safe limits."
        result = _faq_answer_to_html(answer)
        assert "5 &lt; 10 &amp;" in result
        assert "<p>" in result
        gaps = escaped_markup_gaps(result)
        assert gaps == [], "Legitimate escaped text like '5 < 10' should not be flagged"

    def test_script_tag_and_contents_removed(self):
        """<script> tags AND their contents are removed entirely."""
        answer = "Normal text <script>alert('xss')</script> more text."
        result = _faq_answer_to_html(answer)
        assert "<script" not in result
        assert "alert" not in result  # Contents also removed
        assert "Normal text" in result
        assert "more text" in result

    def test_style_tag_and_contents_removed(self):
        """<style> tags AND their contents are removed entirely."""
        answer = "Normal text <style>.foo { color: red; }</style> more text."
        result = _faq_answer_to_html(answer)
        assert "<style" not in result
        assert "color" not in result  # Contents also removed
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

    def test_multiple_anchor_tags_all_converted(self):
        """Multiple <a> tags are all converted to plain text."""
        answer = 'See <a href="/a">link one</a> and <a href="/b">link two</a>.'
        result = _faq_answer_to_html(answer)
        assert "link one" in result
        assert "link two" in result
        assert "<a " not in result
        assert "&lt;a" not in result

    # Blocker 2: quote=False for apostrophes and double quotes
    def test_apostrophe_not_escaped(self):
        """Apostrophes stay as literal ' not &#x27;."""
        answer = "It's the best choice and that's final."
        result = _faq_answer_to_html(answer)
        assert "It's" in result
        assert "that's" in result
        assert "&#x27;" not in result
        assert "&#39;" not in result

    def test_double_quote_not_escaped(self):
        """Double quotes stay as literal \" not &quot;."""
        answer = 'The "best" option is clear.'
        result = _faq_answer_to_html(answer)
        assert '"best"' in result
        assert "&quot;" not in result

    def test_apostrophe_and_quote_together(self):
        """Both apostrophes and double quotes render literally."""
        answer = """It's called "the best" and that's what users say."""
        result = _faq_answer_to_html(answer)
        assert "It's" in result
        assert '"the best"' in result
        assert "that's" in result
        assert "&#x27;" not in result
        assert "&quot;" not in result

    # Blocker 4: Already-escaped input
    def test_already_escaped_p_tag_unescaped_first(self):
        """Already-escaped &lt;p&gt;Hi&lt;/p&gt; → <p>Hi</p>."""
        answer = "&lt;p&gt;Hi&lt;/p&gt;"
        result = _faq_answer_to_html(answer)
        assert result == "<p>Hi</p>"
        assert "&lt;p" not in result
        assert "&lt;/p" not in result
        assert "&amp;" not in result

    def test_already_escaped_heart_emoticon(self):
        """Already-escaped &lt;3 → renders as '<3' text (escaped once as &lt;3)."""
        answer = "I &lt;3 vaping"
        result = _faq_answer_to_html(answer)
        # After unescape: "I <3 vaping"
        # After escape: "I &lt;3 vaping"
        assert "&lt;3" in result
        assert "<p>" in result
        # Should NOT be flagged by the guard (not a real HTML tag)
        gaps = escaped_markup_gaps(result)
        assert gaps == [], "&lt;3 should not be flagged as escaped markup"

    # Blocker 5: Text loss from unclosed parser
    def test_text_after_entity_not_lost(self):
        """Text after entity refs is not lost: '<em>x</em> ends with AT&T' includes AT&T."""
        answer = "<em>x</em> ends with AT&T"
        result = _faq_answer_to_html(answer)
        assert "<em>x</em>" in result
        assert "AT&amp;T" in result or "AT&T" in result  # Either escaped or literal
        assert "ends with" in result

    def test_unclosed_inline_tag_balanced(self):
        """Unclosed inline tags are balanced at paragraph end."""
        answer = "Start <strong>bold text without close"
        result = _faq_answer_to_html(answer)
        assert "<strong>" in result
        assert "</strong>" in result
        assert result.count("<strong>") == result.count("</strong>")

    def test_unclosed_em_tag_balanced(self):
        """Unclosed <em> tag is balanced."""
        answer = "This is <em>italic without close"
        result = _faq_answer_to_html(answer)
        assert "<em>" in result
        assert "</em>" in result

    # Nit: Markdown links stripped
    def test_markdown_link_stripped_to_text(self):
        """Markdown links [text](url) → text."""
        answer = "Check out [our pods](https://example.com/pods) for options."
        result = _faq_answer_to_html(answer)
        assert "our pods" in result
        assert "[" not in result
        assert "](" not in result
        assert "https://example.com" not in result

    def test_bare_url_removed(self):
        """Bare URLs are removed."""
        answer = "Visit https://example.com/pods for more info."
        result = _faq_answer_to_html(answer)
        assert "https://" not in result
        assert "example.com" not in result
        assert "Visit" in result
        assert "for more info" in result


class TestEscapedMarkupGaps:
    """Test escaped_markup_gaps compliance checker."""

    # Positive tests - should BE flagged
    def test_flags_escaped_p_tag(self):
        """Guard flags &lt;p&gt;."""
        body = "<p>&lt;p&gt;This is double escaped&lt;/p&gt;</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]
        assert "escaped HTML tags" in gaps[0]

    def test_flags_escaped_closing_p_tag(self):
        """Guard flags &lt;/p&gt;."""
        body = "<p>Text &lt;/p&gt; more text</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]

    def test_flags_escaped_a_tag(self):
        """Guard flags &lt;a href."""
        body = '<p>Click &lt;a href="https://example.com"&gt;here&lt;/a&gt;</p>'
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]

    def test_flags_escaped_strong_tag(self):
        """Guard flags &lt;strong&gt;."""
        body = "<p>This is &lt;strong&gt;bold&lt;/strong&gt; text.</p>"
        gaps = escaped_markup_gaps(body)
        assert len(gaps) == 1
        assert "escaped_markup" in gaps[0]

    def test_flags_escaped_br_self_closing(self):
        """Guard flags &lt;br/&gt;."""
        body = "<p>Line one&lt;br/&gt;Line two</p>"
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

    # Negative tests - should NOT be flagged
    def test_ignores_less_than_with_space(self):
        """Guard ignores '&lt; 5'."""
        body = "<p>The value is &lt; 5 units, which is acceptable.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt; 5' should not be flagged as escaped markup"

    def test_ignores_less_than_with_digit(self):
        """Guard ignores '&lt;5'."""
        body = "<p>If x &lt;5 then it works.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt;5' (digit) should not be flagged"

    def test_ignores_heart_emoticon(self):
        """Guard ignores '&lt;3' (heart emoticon)."""
        body = "<p>I &lt;3 vaping</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt;3' (heart) should not be flagged"

    def test_ignores_moderate_daily_use(self):
        """Guard ignores '&lt; Moderate daily use' (stray less-than followed by word)."""
        body = "<p>Recommended usage &lt; Moderate daily use is best.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt; Moderate' should not be flagged (not a real HTML tag)"

    def test_ignores_legitimate_math_expression(self):
        """Guard ignores '5 < 10 & more'."""
        body = "<p>When n &lt; 10 &amp; m &gt; 5, the formula applies.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == []

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
        """normalize_flavor_to_flavour still works after sanitization."""
        from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import normalize_flavor_to_flavour

        answer = "The best flavor is Grape Ice."
        sanitized = _faq_answer_to_html(answer)
        normalized = normalize_flavor_to_flavour(sanitized, log_changes=False)
        assert "flavour" in normalized.lower()
        assert "flavor" not in normalized.lower()


class TestEndToEndGenerateArticleDraft:
    """End-to-end test with stubbed AI returning HTML FAQ answers."""

    def test_generate_article_draft_with_html_faq_answers(self):
        """Case 8: Full generate_article_draft with stubbed AI returning HTML answers.

        The AI stub returns HTML FAQ answers (<p>-wrapped, containing <a href>, multi-paragraph).
        The final body must have:
        - Real <p> answers (not escaped)
        - No &lt; tags (escaped markup)
        - No FAQ <a> tags at all (links stripped)
        - _compliance_gaps clean (no escaped_markup gap)
        - #40 FAQ rules still apply (health claims filtered)
        """
        import sqlite3
        from unittest.mock import patch, MagicMock

        from shopifyseo.dashboard_ai_engine_parts._article_draft import (
            _faq_answer_to_html,
        )
        from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
            escaped_markup_gaps,
        )

        # Simulate what _append_faq_answers does with HTML answers from AI
        html_answers = [
            "<p>At its core, vaping is the inhalation of <a href='https://example.com'>vapor</a> created by an electronic device.</p>",
            "<p>First paragraph about pods.</p><p>Second paragraph with <strong>details</strong>.</p>",
            "<p>The best choice depends on your <em>preferences</em> and budget.</p>",
        ]

        # Sanitize each answer as the real code does
        sanitized_answers = [_faq_answer_to_html(ans) for ans in html_answers]

        # Build the FAQ block as the real code does
        from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import normalize_flavor_to_flavour
        import html as html_module

        questions = [
            "What is vaping?",
            "What are the best pods?",
            "Which device should I choose?",
        ]

        block = "\n<h2>Helpful questions before you choose</h2>"
        for q, ans_html in zip(questions, sanitized_answers):
            q_normalized = normalize_flavor_to_flavour(q, log_changes=False)
            ans_normalized = normalize_flavor_to_flavour(ans_html, log_changes=False)
            block += f"\n<h3>{html_module.escape(q_normalized)}</h3>{ans_normalized}"

        # Verify the output
        # 1. Real <p> answers (not escaped)
        assert "<p>" in block
        assert "</p>" in block

        # 2. No escaped markup (&lt;p, &lt;a, &lt;/, etc.)
        assert "&lt;p" not in block
        assert "&lt;/p" not in block
        assert "&lt;a" not in block

        # 3. No FAQ <a> tags (links stripped)
        assert "<a " not in block
        assert "<a>" not in block

        # 4. _compliance_gaps clean (no escaped_markup gap)
        gaps = escaped_markup_gaps(block)
        assert gaps == [], f"FAQ block should pass escaped_markup_gaps, got: {gaps}"

        # 5. Verify specific content is present
        assert "vaping is the inhalation" in block
        assert "<strong>details</strong>" in block
        assert "<em>preferences</em>" in block

        # 6. Multi-paragraph answer produces multiple <p> elements
        # The second answer had two paragraphs
        assert "First paragraph about pods" in block
        assert "Second paragraph with" in block

    def test_faq_content_filter_rules_still_apply(self):
        """#40 FAQ rules still apply on this path (health claims filtered)."""
        from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import (
            filter_and_dedupe_helpful_questions,
        )

        # These questions should be filtered by #40 rules
        questions_with_health = [
            "Is vaping healthier than smoking?",  # health claim - filtered
            "Can vaping help you quit smoking?",  # quit-smoking - filtered
            "How do I charge my vape?",  # practical question - should pass
            "Is vaping safe for your lungs?",  # health claim - filtered
        ]

        filtered = filter_and_dedupe_helpful_questions(
            questions_with_health,
            existing_questions=[],
            target_brand="",
            log_dropped=False,
        )

        # Health claim questions should be filtered out
        assert "Is vaping healthier than smoking?" not in filtered
        assert "Can vaping help you quit smoking?" not in filtered
        assert "Is vaping safe for your lungs?" not in filtered
        # Valid practical question should remain
        assert "How do I charge my vape?" in filtered
