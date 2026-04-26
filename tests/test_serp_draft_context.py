"""Unit tests for SERP appendix packing and retrieval boost terms."""

from shopifyseo.dashboard_ai_engine_parts.serp_draft_context import (
    build_serp_appendix_and_retrieval_boost,
    select_required_paa_questions_for_draft,
)


def test_related_searches_sorted_by_position_in_appendix():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "pods",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [],
        "top_ranking_pages": [],
        "related_searches": [
            {"query": "second", "position": 2},
            {"query": "first", "position": 1},
            {"query": "third", "position": 3},
        ],
        "ai_overview": None,
    }
    appendix, boost, paa_n = build_serp_appendix_and_retrieval_boost(
        topic="Main topic",
        keywords=["pods"],
        idea_serp_context=ctx,
    )
    assert paa_n == 0
    i2 = appendix.index("(position 2)")
    i1 = appendix.index("(position 1)")
    i3 = appendix.index("(position 3)")
    assert i1 < i2 < i3
    assert "first" in boost and "second" in boost


def test_top_titles_have_no_urls_only_optional_host():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "x",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [],
        "top_ranking_pages": [
            {"title": "Competitor guide to pods", "url": "https://competitor.example.com/pods-guide"},
        ],
        "related_searches": [],
        "ai_overview": None,
    }
    appendix, _, paa_n = build_serp_appendix_and_retrieval_boost(
        topic="Pods topic",
        keywords=["x"],
        idea_serp_context=ctx,
    )
    assert paa_n == 0
    assert "https://" not in appendix.lower()
    assert "competitor.example.com" not in appendix.lower()
    assert "Competitor guide" in appendix


def test_style_examples_when_tier_one_related_exists():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "k",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [],
        "top_ranking_pages": [],
        "related_searches": [{"query": "pods vs disposables", "position": 1}],
        "ai_overview": None,
    }
    appendix, _, paa_n = build_serp_appendix_and_retrieval_boost(topic="Kits", keywords=[], idea_serp_context=ctx)
    assert paa_n == 0
    assert "Style examples (do not copy" in appendix
    assert "Comparison shape" in appendix
    assert "How-to shape" in appendix
    assert "Binary / safety shape" in appendix


def test_empty_context_yields_empty_appendix():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [],
        "top_ranking_pages": [],
        "related_searches": [],
        "ai_overview": None,
    }
    appendix, boost, paa_n = build_serp_appendix_and_retrieval_boost(topic="Only topic", keywords=[], idea_serp_context=ctx)
    assert appendix == ""
    assert boost == []
    assert paa_n == 0


def test_appendix_truncation_marker_when_over_budget():
    ctx = {
        "suggested_title": "",
        "brief": "x" * 5000,
        "primary_keyword": "kw",
        "supporting_keywords": [],
        "gap_reason": "y" * 5000,
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [{"question": "Q" + str(i), "snippet": "s" * 400} for i in range(30)],
        "top_ranking_pages": [],
        "related_searches": [{"query": f"q{n}", "position": n} for n in range(1, 25)],
        "ai_overview": {"text_blocks": [{"type": "paragraph", "snippet": "z" * 2000}]},
    }
    appendix, _, paa_n = build_serp_appendix_and_retrieval_boost(
        topic="T",
        keywords=["kw"],
        idea_serp_context=ctx,
        max_appendix_chars=1200,
    )
    assert paa_n > 0
    assert len(appendix) <= 1200
    assert "truncated" in appendix.lower()


def test_paa_shown_count_matches_appended_questions():
    qs = [{"question": f"Q{i}?", "snippet": ""} for i in range(25)]
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "k",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": qs,
        "top_ranking_pages": [],
        "related_searches": [],
        "ai_overview": None,
    }
    from shopifyseo.dashboard_ai_engine_parts.serp_draft_context import MAX_PAA_QUESTIONS

    appendix, _, paa_n = build_serp_appendix_and_retrieval_boost(topic="T", keywords=[], idea_serp_context=ctx)
    assert paa_n == MAX_PAA_QUESTIONS
    assert appendix.count("Q") >= MAX_PAA_QUESTIONS


def test_tier_one_related_block_includes_heading_requirement():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "k",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [],
        "top_ranking_pages": [],
        "related_searches": [{"query": "alpha query", "position": 1}],
        "ai_overview": None,
    }
    appendix, _, _ = build_serp_appendix_and_retrieval_boost(topic="T", keywords=[], idea_serp_context=ctx)
    assert "position 1, 2, or 3" in appendix


def test_paa_numbered_and_snippet_capped():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "p",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "",
        "content_format_hints": "",
        "audience_questions": [
            {"question": "Is this safe?", "snippet": "S" * 500},
        ],
        "top_ranking_pages": [],
        "related_searches": [],
        "ai_overview": None,
    }
    appendix, _, paa_n = build_serp_appendix_and_retrieval_boost(topic="P topic", keywords=[], idea_serp_context=ctx)
    assert paa_n == 1
    assert "1. Is this safe?" in appendix
    assert appendix.count("S") < 400


def test_expanded_paa_hierarchy_guides_section_depth():
    ctx = {
        "suggested_title": "",
        "brief": "",
        "primary_keyword": "pod kits",
        "supporting_keywords": [],
        "gap_reason": "",
        "dominant_serp_features": "PAA",
        "content_format_hints": "",
        "audience_questions": [
            {"question": "Which pod kit is best for beginners?", "snippet": "Choose simple kits."},
            {"question": "Are pod kits cheaper than disposables?", "snippet": ""},
        ],
        "paa_expansion": [
            {
                "parent_question": "Which pod kit is best for beginners?",
                "children": [
                    {"question": "What should a beginner look for in a pod kit?", "snippet": "Ease of use."},
                    {"question": "Are refillable pod kits hard to maintain?", "snippet": ""},
                    {"question": "Which pod kit is best for beginners?", "snippet": "duplicate parent"},
                ],
            }
        ],
        "top_ranking_pages": [],
        "related_searches": [],
        "ai_overview": None,
    }

    appendix, boost, paa_n = build_serp_appendix_and_retrieval_boost(
        topic="Pod kits guide",
        keywords=["pod kits"],
        idea_serp_context=ctx,
    )
    required = select_required_paa_questions_for_draft(ctx)

    assert paa_n == 2
    assert "PAA hierarchy" in appendix
    assert "Parent intent: Which pod kit is best for beginners?" in appendix
    assert "What should a beginner look for in a pod kit?" in appendix
    assert "Are refillable pod kits hard to maintain?" in appendix
    assert required[:2] == [
        "Which pod kit is best for beginners?",
        "Are pod kits cheaper than disposables?",
    ]
    assert "What should a beginner look for in a pod kit?" in required
    assert any("what should a beginner" in x for x in boost)
