"""Tests for the primary authority link hard-fail in generate_article_draft."""
import sqlite3

import pytest

from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts._article_draft import generate_article_draft
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture(autouse=True)
def _disable_phased_article_draft(monkeypatch):
    """Existing tests assume one structured `article_draft` completion."""
    from shopifyseo.dashboard_ai_engine_parts import settings as _dash_settings

    def _ai(conn, overrides=None):
        d = _dash_settings.ai_settings(conn, overrides)
        return {**d, "article_draft_phased": False}

    monkeypatch.setattr(_article_draft, "ai_settings", _ai)


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
    """Meet post-draft compliance minimum length (14k+ HTML chars) for unit tests."""
    link_block = f'<h2>Intro</h2><p>See our <a href="{url}">collection</a> for disposable vapes.</p>'
    filler = "<p>" + ("Padding paragraph. " * 950) + "</p>"
    return link_block + filler


def _long_body_wrong_link() -> str:
    """Long enough for compliance but links to a different path than the required primary."""
    return (
        "<h2>Intro</h2><p>We discuss disposable vapes and link to the wrong place: "
        '<a href="https://example.com/collections/wrong-handle">wrong</a>.</p>'
        + "<p>" + ("More disposable vapes content. " * 950) + "</p>"
    )


def _long_body_no_links() -> str:
    return "<h2>Intro</h2><p>No links here.</p>" + "<p>" + ("Plain text padding. " * 950) + "</p>"


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


def test_draft_repairs_when_primary_link_missing(db_conn, monkeypatch):
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(_long_body_wrong_link()),
    )
    result = generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
    )
    assert 'href="https://example.com/collections/disposable-vapes"' in result["body"]


def test_draft_skips_validation_when_no_primary_target(db_conn, monkeypatch):
    # No primary_target supplied → validator must not fire.
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(_long_body_no_links()),
    )
    result = generate_article_draft(
        db_conn,
        topic="General Topic",
        keywords=[],
        primary_target=None,
        secondary_targets=[],
    )
    assert result["title"]
