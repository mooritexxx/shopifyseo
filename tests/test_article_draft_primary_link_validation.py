"""Tests for the primary authority link hard-fail in generate_article_draft."""
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
        ("disposable-vapes", "Disposable Vapes"),
    )
    conn.commit()
    return conn


def _body_with_link(url: str) -> str:
    # Body is required to be >=14000 chars by the JSON schema, but the schema is
    # enforced by the AI provider, not by our Python code, so in unit tests we
    # can return anything — the validator only inspects <a> tags.
    filler = "<p>" + ("Padding paragraph. " * 50) + "</p>"
    link_block = f'<h2>Intro</h2><p>See our <a href="{url}">collection</a>.</p>'
    return link_block + filler


def _fake_call_factory(body: str):
    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        return {
            "title": "A Real Title For The Article",
            "seo_title": "A Real SEO Title Within Bounds For Testing Purposes Only",
            "seo_description": (
                "This is a meta description that is within the 135 to 155 character bound required "
                "by the schema. Concrete, specific, click-worthy."
            ),
            "body": body,
        }
    return fake_call_ai


def test_draft_succeeds_when_primary_link_present(db_conn, monkeypatch):
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(_body_with_link("https://example.com/collections/disposable-vapes")),
    )
    result = generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
    )
    assert "disposable-vapes" in result["body"]


def test_draft_hard_fails_when_primary_link_missing(db_conn, monkeypatch):
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    # AI returns body with no primary link (just filler).
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory("<h2>Intro</h2><p>" + ("Padding. " * 500) + "</p>"),
    )
    with pytest.raises(RuntimeError, match="missing required primary authority link"):
        generate_article_draft(
            db_conn,
            topic="Best Disposable Vapes",
            keywords=["disposable vapes"],
            primary_target=primary_target,
            secondary_targets=[],
        )


def test_draft_skips_validation_when_no_primary_target(db_conn, monkeypatch):
    # No primary_target supplied → validator must not fire.
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory("<h2>Intro</h2><p>No links here.</p>"),
    )
    result = generate_article_draft(
        db_conn,
        topic="General Topic",
        keywords=[],
        primary_target=None,
        secondary_targets=[],
    )
    assert result["title"]
