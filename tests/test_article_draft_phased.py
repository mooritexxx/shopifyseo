"""Phased article draft: outline + HTML batches with mocked AI."""

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
    conn.commit()
    return conn


def _outline_payload() -> dict:
    beats = (
        "Cover buyer intent, common objections, and a practical checklist. "
        "Mention how this fits seasonal demand and what to verify before purchase."
    )
    sections = []
    for i in range(8):
        sections.append(
            {
                "heading": f"Section {i + 1} deep dive for testing phased batches",
                "level": "h2" if i % 2 == 0 else "h3",
                "beats": beats,
            }
        )
    return {
        "title": "Phased Article Title For Compliance Testing Only",
        "seo_title": "Phased Article Title For Compliance Testing Only Long Enough",
        "seo_description": (
            "This is a meta description that is within the 135 to 155 character bound required "
            "by the schema. Concrete, specific, click-worthy."
        ),
        "sections": sections,
    }


def _html_fragment(min_chars: int) -> str:
    inner_len = max(0, min_chars - 7)
    return "<p>" + ("z" * inner_len) + "</p>"


def test_phased_generation_outline_then_batches(db_conn, monkeypatch):
    from shopifyseo.dashboard_ai_engine_parts import settings as _dash_settings

    def _ai(conn, overrides=None):
        d = _dash_settings.ai_settings(conn, overrides)
        return {**d, "article_draft_phased": True}

    monkeypatch.setattr(_article_draft, "ai_settings", _ai)

    stages: list[str] = []

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        stages.append(stage or "")
        if stage == "article_draft_outline":
            return _outline_payload()
        if stage == "article_draft_section":
            hb = (json_schema or {}).get("schema", {}).get("properties", {}).get("html_blocks", {})
            n_items = int(hb.get("minItems") or 3)
            frags = [_html_fragment(1750) for _ in range(n_items)]
            return {"html_blocks": frags}
        raise AssertionError(f"unexpected stage {stage!r}")

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)

    out = generate_article_draft(
        db_conn,
        topic="Widget buyers guide for unit tests",
        keywords=["widgets"],
        primary_target=None,
        secondary_targets=[],
        idea_serp_context=None,
    )

    assert stages[0] == "article_draft_outline"
    assert stages.count("article_draft_section") == 3
    assert len(stages) == 4
    assert len(out["body"]) >= 14000
    assert out["title"] == _outline_payload()["title"]
