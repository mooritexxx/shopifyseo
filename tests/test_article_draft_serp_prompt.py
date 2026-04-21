"""SERP-informed draft: prompt wiring with mocked AI."""

import sqlite3

import pytest

from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts._article_draft import generate_article_draft
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, ?)",
        ("store_custom_domain", "https://example.com"),
    )
    conn.execute(
        "INSERT INTO collections (handle, title, raw_json, synced_at) VALUES (?, ?, '{}', '')",
        ("pods", "Pod Kits"),
    )
    conn.commit()
    return conn


def _filler_body(url: str) -> str:
    link = f'<p><a href="{url}">pods</a></p>'
    return link + "<p>" + ("word " * 5000) + "</p>"


def test_generate_article_draft_includes_serp_signals_in_user_message(db_conn, monkeypatch):
    captured: list[dict] = []

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        captured.extend(messages)
        return {
            "title": "Pod Kits Compared For Everyday Buyers",
            "seo_title": "Pod Kits Compared For Everyday Buyers Long Enough Here",
            "seo_description": (
                "This is a meta description that is within the 135 to 155 character bound required "
                "by the schema. Concrete, specific, click-worthy."
            ),
            "body": _filler_body("https://example.com/collections/pods"),
        }

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)

    idea_ctx = {
        "suggested_title": "Pod kits guide",
        "brief": "Explain refillable pod systems.",
        "primary_keyword": "pod kits",
        "supporting_keywords": [],
        "gap_reason": "Cluster lacks a definitive comparison.",
        "dominant_serp_features": "PAA, related searches",
        "content_format_hints": "comparison tables",
        "audience_questions": [{"question": "Which pod kit is best for beginners?", "snippet": "Hint only."}],
        "top_ranking_pages": [{"title": "SERP Title Alpha", "url": "https://serp.example/a"}],
        "related_searches": [
            {"query": "pod kits vs disposable vapes", "position": 1},
            {"query": "pod kits long tail nine", "position": 9},
        ],
        "ai_overview": {"text_blocks": [{"type": "paragraph", "snippet": "Overview commodity line."}]},
    }

    generate_article_draft(
        db_conn,
        topic="Pod kits guide",
        keywords=["pod kits"],
        primary_target={
            "type": "collection",
            "handle": "pods",
            "title": "Pod Kits",
            "url": "https://example.com/collections/pods",
        },
        secondary_targets=[],
        idea_serp_context=idea_ctx,
    )

    assert len(captured) >= 2
    user_content = next(m["content"] for m in captured if m.get("role") == "user")
    system_content = next(m["content"] for m in captured if m.get("role") == "system")

    assert "SERP-informed research" in user_content
    assert "Which pod kit is best for beginners" in user_content
    assert "(position 1)" in user_content and "pod kits vs disposable" in user_content
    assert "SERP Title Alpha" in user_content
    # Competitor domains must not appear in the SERP appendix (titles only).
    assert "serp.example" not in user_content

    assert "SERP research appendix" in system_content or "information gain" in system_content.lower()
    assert "FAQPage JSON-LD" in user_content
