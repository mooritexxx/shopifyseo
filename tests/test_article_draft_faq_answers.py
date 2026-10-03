"""Unit tests for FAQ answer sanitization and escaped markup detection.

Test cases from the 2026-10-03 trace covering:
1. HTML <p> answer → single real <p>
2. Two-paragraph answer → two <p>
3. Answer containing <a href> → plain text, no &lt;a
4. <strong> kept
5. '5 < 10 &' stays escaped as text and isn't flagged
6. Guard flags &lt;p&gt; and &lt;a (only real HTML tag names, no space after <)
7. Guard ignores '&lt; 5', '&lt;3', '&lt; Moderate', 'if a &lt; b then'
8. Full generate_article_draft run with stubbed AI returning HTML answers
"""

import sqlite3

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

    # Nit: Stray closing tags dropped
    def test_stray_closing_strong_dropped(self):
        """Stray closing </strong> with no open tag is dropped."""
        answer = "</strong>stray text here"
        result = _faq_answer_to_html(answer)
        assert "</strong>" not in result
        assert "stray text here" in result
        assert "<p>stray text here</p>" == result

    def test_stray_closing_em_dropped(self):
        """Stray closing </em> with no open tag is dropped."""
        answer = "text</em> more text"
        result = _faq_answer_to_html(answer)
        assert "</em>" not in result
        assert "text more text" in result

    # Nit: Double spaces collapsed and empty () removed
    def test_double_spaces_collapsed(self):
        """Double spaces are collapsed to single space."""
        answer = "Text  with   multiple    spaces."
        result = _faq_answer_to_html(answer)
        assert "  " not in result
        assert "Text with multiple spaces." in result

    def test_empty_parens_removed_after_url_strip(self):
        """Empty () left after URL stripping is removed."""
        answer = "See details (https://example.com/link) for info."
        result = _faq_answer_to_html(answer)
        assert "()" not in result
        assert "( )" not in result
        assert "See details for info." in result

    # Double-encoded handling
    def test_double_encoded_unescaped_twice(self):
        """Double-encoded &amp;lt;p&amp;gt; is unescaped twice to become <p>."""
        answer = "&amp;lt;p&amp;gt;Double encoded&amp;lt;/p&amp;gt;"
        result = _faq_answer_to_html(answer)
        assert result == "<p>Double encoded</p>"
        assert "&amp;" not in result
        assert "&lt;" not in result


class TestEscapedMarkupGaps:
    """Test escaped_markup_gaps compliance checker."""

    # Positive tests - should BE flagged (real HTML tags without space after <)
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

    # Negative tests - should NOT be flagged (space after < or not a real tag)
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

    def test_ignores_if_a_less_than_b(self):
        """Guard ignores 'if a &lt; b then' (comparison expression)."""
        body = "<p>The condition if a &lt; b then triggers the alert.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'if a &lt; b then' should not be flagged"

    def test_ignores_nicotine_comparison(self):
        """Guard ignores 'nicotine &lt; a typical cigarette'."""
        body = "<p>The nicotine content is &lt; a typical cigarette amount.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt; a typical' should not be flagged (space before 'a')"

    def test_ignores_i_think(self):
        """Guard ignores '&lt; i think' (not an italic tag due to space)."""
        body = "<p>The value &lt; i think is acceptable.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt; i think' should not be flagged (space before 'i')"

    def test_ignores_p_value(self):
        """Guard ignores '&lt; p value' (not a paragraph tag due to space)."""
        body = "<p>The result &lt; p value threshold.</p>"
        gaps = escaped_markup_gaps(body)
        assert gaps == [], "'&lt; p value' should not be flagged (space before 'p')"

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


# Import fixtures from test_article_draft_phased for the e2e test
from tests.test_article_draft_phased import db_conn, _outline_payload, _html_fragment


class TestEndToEndGenerateArticleDraft:
    """End-to-end test with real generate_article_draft and stubbed AI."""

    def test_e2e_html_faq_answers(self, db_conn, monkeypatch):
        """Case 8: Full generate_article_draft with stubbed AI returning HTML FAQ answers.

        The AI stub returns HTML FAQ answers (<p>-wrapped, containing <a href>,
        and an already-escaped &lt;p&gt; answer). The final body must have:
        - Real <p> answers (not escaped)
        - No &lt; tags anywhere (escaped markup)
        - No FAQ <a> tags at all (links stripped from FAQ)
        - Flavour spelling normalized (not flavor)
        - FAQ JSON-LD present
        - No escaped_markup compliance gap

        This test FAILS if _append_faq_answers goes back to html.escape.
        """
        from shopifyseo.dashboard_ai_engine_parts import _article_draft
        from shopifyseo.dashboard_ai_engine_parts import settings as _s
        from shopifyseo.dashboard_ai_engine_parts._article_draft import generate_article_draft
        from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import escaped_markup_gaps

        # Force phased mode
        monkeypatch.setattr(
            _article_draft, "ai_settings",
            lambda c, o=None: {**_s.ai_settings(c, o), "article_draft_phased": True}
        )

        seen = {}

        def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
            if stage == "article_draft_outline":
                return _outline_payload()
            if stage == "article_draft_section":
                n = int(json_schema["schema"]["properties"]["html_blocks"].get("minItems") or 3)
                return {"html_blocks": [_html_fragment(1750) for _ in range(n)]}
            if stage == "article_draft_append_repair":
                return {"append_html": _html_fragment(int(json_schema["schema"]["properties"]["append_html"].get("minLength") or 700))}
            if stage == "article_draft_faq_repair":
                # Capture the system message to verify prompt rules
                seen["faq_system"] = messages[0]["content"]
                n = json_schema["schema"]["properties"]["answers"]["minItems"]
                # Return HTML answers: <p>-wrapped, with <a href>, multi-paragraph, and already-escaped
                html_answers = [
                    # Answer with HTML <p>, <a href>, and "flavor" (should become flavour)
                    '<p>Widget batteries charge over USB-C in about an hour, see <a href="https://example.com/collections/x">our widgets</a> for the flavor list.</p><p>Second paragraph about <strong>care</strong>.</p>',
                    # Already-escaped answer (simulates double-escaping)
                    '&lt;p&gt;Already escaped content here&lt;/p&gt;',
                    # Plain HTML paragraph
                    '<p>Simple answer with proper formatting.</p>',
                ]
                return {"answers": html_answers[:n] if n <= len(html_answers) else html_answers * ((n // len(html_answers)) + 1)}
            return {}

        monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)

        out = generate_article_draft(
            db_conn,
            topic="Widget buyers guide for unit tests",
            keywords=["widgets"],
            primary_target=None,
            secondary_targets=[],
            idea_serp_context={
                "audience_questions": [
                    {"question": "How long does a widget battery take to charge?", "snippet": "About an hour."},
                    {"question": "What is widget care?", "snippet": "Keep them clean."},
                    {"question": "Are widgets good?", "snippet": "Yes they are."},
                ]
            },
        )

        body = out["body"]

        # Find the FAQ section
        faq_start = body.find("Helpful questions")
        assert faq_start != -1, "FAQ section should be present in body"
        faq_section = body[faq_start:]

        # 1. Verify FAQ system prompt was used with plain text instruction
        assert "faq_system" in seen, "FAQ system prompt should have been captured"
        assert "plain text" in seen["faq_system"].lower(), "FAQ prompt should ask for plain text"

        # 2. No escaped markup anywhere in body (&lt;p, &lt;a, etc.)
        assert "&lt;" not in body, f"Body should not contain escaped markup, found &lt; at: {body[body.find('&lt;'):body.find('&lt;')+50] if '&lt;' in body else 'N/A'}"

        # 3. Flavour spelling normalized (not flavor)
        assert "flavour" in body.lower(), "Body should contain 'flavour' (normalized spelling)"
        # Note: "flavor" might appear in non-FAQ content, so we check the FAQ section specifically
        if "flavor" in faq_section.lower():
            assert False, "FAQ section should have 'flavour' not 'flavor'"

        # 4. No <a> tags in the FAQ section (links stripped)
        assert "<a " not in faq_section.lower(), "FAQ section should not contain anchor tags"
        assert "<a>" not in faq_section.lower(), "FAQ section should not contain anchor tags"

        # 5. Real <p> tags present (not escaped)
        assert "<p>" in faq_section, "FAQ section should have real <p> tags"
        assert "</p>" in faq_section, "FAQ section should have real </p> tags"

        # 6. Check for FAQ JSON-LD (FAQPage schema)
        assert "FAQPage" in body, "Body should contain FAQPage JSON-LD schema"
        assert "application/ld+json" in body.lower(), "Body should contain JSON-LD script"

        # 7. No escaped_markup compliance gap
        gaps = escaped_markup_gaps(body)
        escaped_gaps = [g for g in gaps if "escaped_markup" in g]
        assert escaped_gaps == [], f"Body should have no escaped_markup gaps, got: {escaped_gaps}"

        # 8. Verify content from HTML answers made it through (but sanitized)
        assert "batteries charge" in body.lower() or "battery" in body.lower(), "FAQ answer content should be present"
        assert "<strong>care</strong>" in faq_section or "care" in faq_section, "Strong tag or its content should be present"

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
