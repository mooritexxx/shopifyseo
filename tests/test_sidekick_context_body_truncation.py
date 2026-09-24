"""Sidekick context block: a long body_html must not truncate away trailing metadata keys."""

from shopifyseo.sidekick import build_sidekick_context_block

_LONG_BODY = "x" * 15000


def test_blog_article_context_keeps_metadata_after_long_body():
    detail = {
        "draft": {
            "title": "Long Article",
            "seo_title": "Long Article SEO",
            "seo_description": "Desc",
            "body_html": _LONG_BODY,
        },
        "recommendation": {"details": {"seo_title": "Rec title"}, "status": "reviewed"},
        "opportunity": {"score": 42, "priority": "high"},
    }
    block = build_sidekick_context_block(
        resource_type="blog_article",
        handle="long-article",
        detail=detail,
        client_draft=None,
    )
    assert "recommendation_status" in block
    assert "reviewed" in block
    assert "opportunity" in block
    assert "42" in block
    assert "high" in block


def test_product_context_keeps_metadata_after_long_body():
    detail = {
        "draft": {
            "title": "Long Product",
            "seo_title": "Long Product SEO",
            "seo_description": "Desc",
            "tags": "a,b",
            "body_html": _LONG_BODY,
        },
        "recommendation": {"details": {"seo_title": "Rec title"}, "status": "reviewed"},
        "opportunity": {"score": 42, "priority": "high"},
    }
    block = build_sidekick_context_block(
        resource_type="product",
        handle="long-product",
        detail=detail,
        client_draft=None,
    )
    assert "recommendation_status" in block
    assert "reviewed" in block
    assert "opportunity" in block
    assert "42" in block
    assert "high" in block
