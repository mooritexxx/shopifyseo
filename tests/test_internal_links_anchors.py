"""Tests for anchor phrase detection in catalog bodies."""

from shopifyseo.internal_links.anchors import find_anchor_phrase


def test_finds_phrase_case_insensitively_preserving_body_case():
    html = "<p>Our Ceramic Vape Tanks are popular.</p>"
    assert find_anchor_phrase(html, ["ceramic vape tanks"]) == "Ceramic Vape Tanks"


def test_ignores_text_inside_existing_links_and_headings():
    html = (
        '<h2>ceramic vape tanks</h2>'
        '<p><a href="/x">ceramic vape tanks</a> elsewhere</p>'
    )
    assert find_anchor_phrase(html, ["ceramic vape tanks"]) is None


def test_prefers_longest_candidate_and_requires_word_boundary():
    html = "<p>Best ceramic vape tanks for travel.</p>"
    assert find_anchor_phrase(html, ["vape", "ceramic vape tanks"]) == "ceramic vape tanks"
    assert find_anchor_phrase("<p>vaperizer</p>", ["vape"]) is None


def test_returns_none_for_empty_inputs():
    assert find_anchor_phrase("", ["x"]) is None
    assert find_anchor_phrase("<p>x</p>", []) is None
