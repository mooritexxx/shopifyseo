"""Unit tests for article_draft_compliance helpers."""

from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    MIN_ARTICLE_BODY_HTML_CHARS,
    build_compliance_retry_user_message,
    primary_keyword_in_body,
    validate_article_draft_compliance,
)


def test_validate_all_clear_when_no_requirements():
    body = "<p>" + ("x " * 9000) + "</p>"
    assert len(body) >= MIN_ARTICLE_BODY_HTML_CHARS
    assert (
        validate_article_draft_compliance(
            body_html=body,
            require_faqpage_ld=False,
            secondary_urls=[],
            primary_keyword_for_body=None,
            path_to_canonical={},
        )
        == []
    )


def test_validate_faqpage_missing():
    body = "<p>" + ("x " * 9000) + "</p>"  # length OK, no FAQ script
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=True,
        secondary_urls=[],
        primary_keyword_for_body=None,
        path_to_canonical={},
    )
    assert any("FAQPage" in g for g in gaps)


def test_validate_secondary_href_by_path():
    body = (
        '<p><a href="https://store.example/collections/foo">x</a></p>'
        + "<p>" + ("y " * 9000) + "</p>"
    )
    path_map = {"/collections/foo": "https://store.example/collections/foo"}
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=["https://store.example/collections/foo"],
        primary_keyword_for_body=None,
        path_to_canonical=path_map,
    )
    assert gaps == []


def test_primary_keyword_exact_and_long_substring():
    short_kw = "buy pods canada"
    body_short = f"<p>{short_kw}</p>" + "<p>" + ("z " * 9000) + "</p>"
    assert primary_keyword_in_body(body_short, short_kw) is True

    long_kw = "x" * 100
    sub = long_kw[:60]
    body_long = f"<p>intro {sub} tail</p>" + "<p>" + ("z " * 9000) + "</p>"
    assert primary_keyword_in_body(body_long, long_kw) is True
    assert primary_keyword_in_body(body_long, "this-phrase-is-not-in-the-body-at-all") is False


def test_retry_message_lists_gaps():
    msg = build_compliance_retry_user_message(["Gap one", "Gap two"])
    assert "Gap one" in msg and "Gap two" in msg
    assert "json object" in msg.lower()
