"""Phase 3: GSC segments in AI context, prompts, and Sidekick."""

from shopifyseo.dashboard_ai_engine_parts.context import condensed_context, trim_gsc_segment_summary_for_prompt
from shopifyseo.dashboard_ai_engine_parts.prompts import _slim_seo_description_context, gsc_segment_evidence_sentence
from shopifyseo.sidekick import build_sidekick_context_block


def _sample_segment_summary() -> dict:
    return {
        "fetched_at": 1700000000,
        "device_mix": [
            {"segment": "MOBILE", "clicks": 10, "impressions": 100, "share": 0.67},
            {"segment": "DESKTOP", "clicks": 5, "impressions": 50, "share": 0.33},
        ],
        "top_countries": [{"segment": "can", "clicks": 2, "impressions": 40, "share": 1.0}],
        "search_appearances": [],
        "top_pairs": [],
    }


def test_condensed_context_includes_segment_rollups_in_seo_fact_summary():
    ctx = {
        "object_type": "product",
        "fact": {"gsc_impressions": 100},
        "detail": {"product": {"title": "T"}},
        "gsc_query_rows": [],
        "gsc_query_clusters": [],
        "gsc_segment_summary": _sample_segment_summary(),
    }
    cc = condensed_context(ctx)
    sf = cc["seo_fact_summary"]
    assert sf["gsc_device_mix"]
    assert sf["gsc_top_countries"]
    assert sf["gsc_device_mix"][0]["segment"] == "MOBILE"


def test_trim_gsc_segment_summary_for_prompt_empty():
    assert trim_gsc_segment_summary_for_prompt(None) is None
    assert trim_gsc_segment_summary_for_prompt({}) is None


def test_gsc_segment_evidence_sentence():
    ctx = {"gsc_segment_summary": _sample_segment_summary()}
    full = gsc_segment_evidence_sentence(ctx, compact=False)
    assert full and "MOBILE" in full and "can" in full
    compact = gsc_segment_evidence_sentence(ctx, compact=True)
    assert compact and "MOBILE" in compact
    assert len(compact) <= len(full or "")


def test_slim_seo_description_context_includes_gsc_query_highlights():
    full = {
        "object_type": "product",
        "primary_object": {
            "title": "Test Product",
            "specs": {},
            "intent": {"primary_terms": ["vape"], "canada_keywords": ["canada"], "flavor_family": "fruit"},
        },
        "seo_context": {
            "current_fields": {"seo_title": "T", "seo_description": "D"},
            "seo_fact_summary": {"gsc_impressions": 10, "gsc_ctr": 0.1, "gsc_position": 5.0, "index_status": "Indexed"},
        },
        "top_queries": [
            {"query": "buy vape online", "impressions": 100, "ctr": 0.05, "position": 3.2},
            {"query": "disposable pods", "impressions": 80, "ctr": 0.01, "position": 8.0},
        ],
    }
    slim = _slim_seo_description_context("product", full)
    assert "gsc_query_highlights" in slim
    hq = slim["gsc_query_highlights"]
    assert len(hq) <= 3
    assert hq[0]["query"] == "buy vape online"
    assert hq[0]["impressions"] == 100


def test_slim_seo_description_highlights_fall_back_to_seo_context_rows():
    full = {
        "object_type": "product",
        "primary_object": {
            "title": "X",
            "specs": {},
            "intent": {"primary_terms": [], "canada_keywords": [], "flavor_family": ""},
        },
        "seo_context": {
            "current_fields": {"seo_title": "", "seo_description": ""},
            "seo_fact_summary": {},
            "gsc_query_rows": [
                {"query": "fallback query", "impressions": 50, "ctr": 0.02, "position": 6.0},
            ],
        },
        "top_queries": [],
    }
    slim = _slim_seo_description_context("product", full)
    assert slim.get("gsc_query_highlights")
    assert slim["gsc_query_highlights"][0]["query"] == "fallback query"


def test_sidekick_context_block_includes_gsc_segment_summary():
    detail = {
        "draft": {"title": "X", "seo_title": "", "seo_description": "", "body_html": ""},
        "recommendation": {"details": {}, "status": ""},
        "opportunity": {},
        "gsc_segment_summary": _sample_segment_summary(),
    }
    block = build_sidekick_context_block(
        resource_type="product",
        handle="test-handle",
        detail=detail,
        client_draft=None,
    )
    assert "gsc_segment_summary" in block
    assert "MOBILE" in block
