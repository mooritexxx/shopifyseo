"""Tests for HTML injection of generated article images."""

from shopifyseo.dashboard_ai_engine_parts.generation import (
    inject_article_body_image,
    inject_article_body_images,
    parse_h2_sections,
)


# ---------------------------------------------------------------------------
# Legacy single-image injection (inject_article_body_image)
# ---------------------------------------------------------------------------

def test_inject_after_first_paragraph() -> None:
    html = "<p>Intro here.</p><h2>Next</h2>"
    out = inject_article_body_image(html, "https://cdn.example.com/a.png", 'Photo & "vapes"')
    assert out.startswith("<p>Intro here.</p>")
    assert "https://cdn.example.com/a.png" in out
    assert "Photo &amp; &quot;vapes&quot;" in out or "&quot;vapes&quot;" in out
    assert "<img" in out


def test_inject_prepends_when_no_paragraph() -> None:
    html = "<h2>Only heading</h2>"
    out = inject_article_body_image(html, "https://x.test/i.jpg", "Alt")
    assert "<p><img" in out
    assert "<h2>Only heading</h2>" in out


def test_inject_skips_non_https() -> None:
    html = "<p>x</p>"
    assert inject_article_body_image(html, "http://insecure.com/x.png", "a") == html


# ---------------------------------------------------------------------------
# H2 section parser (parse_h2_sections)
# ---------------------------------------------------------------------------

def test_parse_h2_sections_basic() -> None:
    body = (
        "<p>Intro paragraph.</p>"
        "<h2>Section One</h2><p>First section content.</p>"
        "<h2>Section Two</h2><p>Second section content.</p><p>More text here.</p>"
        "<h2>Section Three</h2><p>Third section content.</p>"
    )
    sections = parse_h2_sections(body)
    assert len(sections) == 3
    assert sections[0]["heading"] == "Section One"
    assert sections[1]["heading"] == "Section Two"
    assert sections[2]["heading"] == "Section Three"
    # Context should include paragraph text
    assert "First section content" in sections[0]["context"]
    assert "Second section content" in sections[1]["context"]
    # insert_pos should be after first </p> in each section
    for sec in sections:
        assert sec["insert_pos"] > 0


def test_parse_h2_sections_no_h2() -> None:
    body = "<p>Just paragraphs.</p><p>No headings here.</p>"
    assert parse_h2_sections(body) == []


def test_parse_h2_sections_strips_inner_tags() -> None:
    body = '<h2><a href="#">Link <strong>Heading</strong></a></h2><p>Content.</p>'
    sections = parse_h2_sections(body)
    assert len(sections) == 1
    assert sections[0]["heading"] == "Link Heading"


# ---------------------------------------------------------------------------
# Multi-image injection (inject_article_body_images)
# ---------------------------------------------------------------------------

def test_inject_multiple_images_at_positions() -> None:
    body = (
        "<p>Intro.</p>"
        "<h2>Sec 1</h2><p>Content 1.</p>"
        "<h2>Sec 2</h2><p>Content 2.</p>"
    )
    sections = parse_h2_sections(body)
    images = [
        {"url": "https://cdn.test/s1.webp", "alt": "Section 1 image", "insert_pos": sections[0]["insert_pos"]},
        {"url": "https://cdn.test/s2.webp", "alt": "Section 2 image", "insert_pos": sections[1]["insert_pos"]},
    ]
    out = inject_article_body_images(body, images)
    # Both images should be present
    assert "https://cdn.test/s1.webp" in out
    assert "https://cdn.test/s2.webp" in out
    # Images should appear after their respective section's first </p>
    s1_pos = out.find("https://cdn.test/s1.webp")
    s2_pos = out.find("https://cdn.test/s2.webp")
    assert s1_pos < s2_pos
    # Original structure preserved
    assert "<h2>Sec 1</h2>" in out
    assert "<h2>Sec 2</h2>" in out


def test_inject_multiple_images_skips_non_https() -> None:
    body = "<p>Intro.</p><h2>A</h2><p>Text.</p>"
    images = [
        {"url": "http://insecure.com/x.png", "alt": "bad", "insert_pos": 10},
        {"url": "", "alt": "empty", "insert_pos": 20},
    ]
    assert inject_article_body_images(body, images) == body


def test_inject_multiple_images_empty_list() -> None:
    body = "<p>Intro.</p>"
    assert inject_article_body_images(body, []) == body


def test_inject_multiple_images_html_escapes_alt() -> None:
    body = "<h2>Test</h2><p>Content.</p>"
    sections = parse_h2_sections(body)
    images = [
        {"url": "https://cdn.test/img.webp", "alt": 'Photo & "vapes"', "insert_pos": sections[0]["insert_pos"]},
    ]
    out = inject_article_body_images(body, images)
    assert "&amp;" in out
    assert "&quot;" in out
