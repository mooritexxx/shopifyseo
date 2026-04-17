"""Phase 3: GSC segments in AI context, prompts, and Sidekick."""

from shopifyseo.dashboard_ai_engine_parts.context import condensed_context, trim_gsc_segment_summary_for_prompt
from shopifyseo.dashboard_ai_engine_parts.prompts import gsc_segment_evidence_sentence
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
