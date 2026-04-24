"""Unit tests for article_draft_compliance helpers."""

from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    COMPLIANCE_BODY_LENGTH_RETRY_MARGIN,
    MIN_ARTICLE_BODY_HTML_CHARS,
    build_compliance_retry_user_message,
    collect_tier_related_queries,
    extract_faqpage_question_names_from_body,
    faqpage_visible_alignment_gaps,
    length_only_article_compliance_gaps,
    mixed_length_and_serp_compliance_gaps,
    primary_keyword_in_body,
    tier1_related_search_heading_gaps,
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


def test_length_gap_message_includes_deficit_and_target():
    gaps = validate_article_draft_compliance(
        body_html="<p>x</p>",
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=None,
        path_to_canonical={},
    )
    assert len(gaps) == 1
    g = gaps[0]
    assert g.startswith("Body HTML must be at least ")
    assert "characters short" in g
    assert str(MIN_ARTICLE_BODY_HTML_CHARS + COMPLIANCE_BODY_LENGTH_RETRY_MARGIN) in g


def test_length_only_article_compliance_gaps():
    assert length_only_article_compliance_gaps([]) is False
    assert (
        length_only_article_compliance_gaps(
            [f"Body HTML must be at least {MIN_ARTICLE_BODY_HTML_CHARS} characters (currently 1)."]
        )
        is True
    )
    assert (
        length_only_article_compliance_gaps(
            [
                f"Body HTML must be at least {MIN_ARTICLE_BODY_HTML_CHARS} characters (currently 1).",
                "Missing required secondary internal link",
            ]
        )
        is False
    )


def test_retry_message_extra_paragraph_for_length_only():
    g = f"Body HTML must be at least {MIN_ARTICLE_BODY_HTML_CHARS} characters (currently 1)."
    msg = build_compliance_retry_user_message([g])
    assert "only the `body` field needs to grow" in msg


def test_faqpage_alignment_passes_when_question_in_visible_text():
    q = "Can I use any pod with my device?"
    script = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":['
        '{"@type":"Question","name":"' + q.replace('"', '\\"') + '",'
        '"acceptedAnswer":{"@type":"Answer","text":"No — check compatibility."}}]}'
        "</script>"
    )
    body = f"<h2>FAQ</h2><h3>{q}</h3><p>No — check compatibility.</p>" + script + "<p>" + ("x " * 9000) + "</p>"
    assert extract_faqpage_question_names_from_body(body) == [q]
    assert faqpage_visible_alignment_gaps(body) == []


def test_faqpage_alignment_fails_when_question_only_in_schema():
    q = "Hidden schema only question?"
    script = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":['
        '{"@type":"Question","name":"' + q.replace('"', '\\"') + '",'
        '"acceptedAnswer":{"@type":"Answer","text":"Never shown."}}]}'
        "</script>"
    )
    body = "<h2>FAQ</h2><p>Visible text without the question above.</p>" + script + "<p>" + ("y " * 9000) + "</p>"
    gaps = faqpage_visible_alignment_gaps(body)
    assert len(gaps) == 1
    assert "does not appear in the visible article text" in gaps[0]


def test_collect_tier_related_queries_orders_by_position():
    rel = [
        {"query": "third", "position": 3},
        {"query": "first", "position": 1},
        {"query": "skip nine", "position": 9},
    ]
    assert collect_tier_related_queries(rel, max_position=3) == ["first", "third"]


def test_tier1_heading_gap_when_query_missing_from_h2_h3():
    body = "<h2>Intro only</h2><p>" + ("x " * 9000) + "</p>"
    gaps = tier1_related_search_heading_gaps(body, ["flavor shots vape"])
    assert len(gaps) == 1
    assert "SERP position 1–3" in gaps[0]


def test_tier1_heading_passes_when_h2_contains_query():
    body = "<h2>Flavor shots vape options</h2><p>" + ("y " * 9000) + "</p>"
    assert tier1_related_search_heading_gaps(body, ["Flavor Shots Vape"]) == []


def test_tier1_passes_when_query_only_in_paragraph_not_heading():
    q = "How to do o's with vape juice"
    body = f"<h2>Intro</h2><p>Here is how we cover: {q} for Canadian shoppers.</p><p>" + ("z " * 9000) + "</p>"
    assert tier1_related_search_heading_gaps(body, [q]) == []


def test_tier1_curly_apostrophe_matches_straight_in_body():
    q = "How to do o's with vape juice"
    body = "<p>We explain how to do o\u2019s with vape juice.</p><p>" + ("a " * 9000) + "</p>"
    assert tier1_related_search_heading_gaps(body, [q]) == []


def test_mixed_length_and_serp_detection():
    assert (
        mixed_length_and_serp_compliance_gaps(
            [
                "SERP position 1–3 related search must appear in an on-page <h2>, <h3>, <h4> heading or in visible "
                "body text (light paraphrase OK): 'x'.",
                "Body HTML must be at least 14000 characters (currently 1). The body is 13999 characters short.",
            ]
        )
        is True
    )


def test_build_retry_message_includes_mixed_hint():
    gaps = [
        "SERP position 1–3 related search must appear in an on-page <h2>, <h3>, <h4> heading or in visible "
        "body text (light paraphrase OK): 'q'.",
        "Body HTML must be at least 14000 characters (currently 1). The body is short. Expand with substantive HTML.",
    ]
    msg = build_compliance_retry_user_message(gaps)
    assert "Mixed fixes" in msg


def test_validate_includes_tier_queries_kwarg():
    body = "<h2>alpha one</h2><p>" + ("z " * 9000) + "</p>"
    gaps = validate_article_draft_compliance(
        body_html=body,
        require_faqpage_ld=False,
        secondary_urls=[],
        primary_keyword_for_body=None,
        path_to_canonical={},
        tier1_related_queries=["missing query"],
    )
    assert any("SERP position 1–3" in g for g in gaps)


def test_faqpage_empty_mainentity_gap():
    script = (
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[]}'
        "</script>"
    )
    body = script + "<p>" + ("z " * 9000) + "</p>"
    gaps = faqpage_visible_alignment_gaps(body)
    assert len(gaps) == 1
    assert "no usable Question entries" in gaps[0]
