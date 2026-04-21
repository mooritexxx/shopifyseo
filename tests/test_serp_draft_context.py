"""Unit tests for SERP appendix packing and retrieval boost terms."""

from shopifyseo.dashboard_ai_engine_parts.serp_draft_context import (
    build_serp_appendix_and_retrieval_boost,
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
    appendix, boost = build_serp_appendix_and_retrieval_boost(
        topic="Main topic",
        keywords=["pods"],
        idea_serp_context=ctx,
    )
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
    appendix, _ = build_serp_appendix_and_retrieval_boost(
        topic="Pods topic",
        keywords=["x"],
        idea_serp_context=ctx,
    )
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
    appendix, _ = build_serp_appendix_and_retrieval_boost(topic="Kits", keywords=[], idea_serp_context=ctx)
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
    appendix, boost = build_serp_appendix_and_retrieval_boost(topic="Only topic", keywords=[], idea_serp_context=ctx)
    assert appendix == ""
    assert boost == []


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
    appendix, _ = build_serp_appendix_and_retrieval_boost(
        topic="T",
        keywords=["kw"],
        idea_serp_context=ctx,
        max_appendix_chars=1200,
    )
    assert len(appendix) <= 1200
    assert "truncated" in appendix.lower()


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
    appendix, _ = build_serp_appendix_and_retrieval_boost(topic="P topic", keywords=[], idea_serp_context=ctx)
    assert "1. Is this safe?" in appendix
    assert appendix.count("S") < 400
