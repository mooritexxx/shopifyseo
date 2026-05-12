"""Tests for new grounding signals threaded into generate_article_draft.

Covers:
- cluster ``content_brief`` is surfaced in the prompt user message
- regeneration_context (existing title, body skeleton, GSC queries) is surfaced
- the draft router's article_idea SELECT pulls ``paa_expansion_json``
"""
import sqlite3

import pytest

from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts._article_draft import generate_article_draft
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture(autouse=True)
def _disable_phased_article_draft(monkeypatch):
    """Force single-shot drafting so a single fake _call_ai captures the full user message."""
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


def _seed_cluster(conn) -> int:
    """Return id of a freshly-inserted cluster with a rich content_brief."""
    cur = conn.execute(
        """
        INSERT INTO clusters
            (name, content_type, primary_keyword, content_brief, generated_at,
             cluster_intent, cluster_role)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Disposable Vapes Canada",
            "blog_post",
            "best disposable vapes canada",
            "Strategic narrative: pillar article that anchors the disposable-vape cluster. "
            "Compare 3-4 popular models, surface compliance signals, and link out to product pages.",
            "2025-01-01",
            "commercial-informational",
            "pillar",
        ),
    )
    cluster_id = cur.lastrowid
    for kw in [
        "best disposable vapes canada",
        "disposable vape comparison",
        "elfbar vs lost mary",
    ]:
        conn.execute(
            "INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
            (cluster_id, kw),
        )
    conn.commit()
    return cluster_id


def _body_with_link(url: str) -> str:
    """Meet post-draft compliance minimum length (14k+ HTML chars) for unit tests.

    Includes the test keyword 'disposable vapes' so the user-supplied primary keyword
    compliance check passes.
    """
    link_block = (
        f'<h2>Intro</h2><p>See our <a href="{url}">collection</a> of disposable vapes.</p>'
    )
    filler = "<p>" + ("More about disposable vapes. " * 950) + "</p>"
    return link_block + filler


def _fake_call_factory(captured: dict, body: str):
    """Return a fake _call_ai that records messages and returns a valid response."""

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        # Save messages by stage so tests can assert against the right one.
        captured.setdefault("messages_by_stage", {})[stage or "default"] = messages
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


# ---------------------------------------------------------------------------
# #7 — cluster content_brief surfaces in the prompt
# ---------------------------------------------------------------------------
def test_cluster_content_brief_appears_in_draft_prompt(db_conn, monkeypatch):
    cluster_id = _seed_cluster(db_conn)
    captured: dict = {}
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(
            captured,
            _body_with_link("https://example.com/collections/disposable-vapes"),
        ),
    )

    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
        linked_cluster_id=cluster_id,
    )

    # Walk all captured messages and find the cluster strategy text.
    all_messages_blob = ""
    for stage_messages in captured["messages_by_stage"].values():
        for msg in stage_messages:
            all_messages_blob += str(msg.get("content") or "")
    assert "Cluster strategy" in all_messages_blob
    assert "Disposable Vapes Canada" in all_messages_blob  # cluster name
    assert "pillar" in all_messages_blob  # cluster_role
    assert "Strategic narrative" in all_messages_blob  # content_brief excerpt


# ---------------------------------------------------------------------------
# #17 / #18 — regeneration_context surfaces existing title + GSC queries + outline
# ---------------------------------------------------------------------------
def test_regeneration_context_surfaces_existing_signals(db_conn, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(
            captured,
            _body_with_link("https://example.com/collections/disposable-vapes"),
        ),
    )
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    regen_ctx = {
        "existing_title": "Best Disposable Vapes 2024 Edition",
        "existing_gsc_position": 14.2,
        "existing_gsc_queries": [
            {"query": "best disposable vape canada", "clicks": 42, "impressions": 980, "position": 6.1},
            {"query": "cheap disposable vape canada", "clicks": 12, "impressions": 410, "position": 14.3},
            {"query": "elfbar vs lost mary", "clicks": 0, "impressions": 88, "position": 22.0},
        ],
        "existing_body_html": (
            "<h2>Why disposables?</h2><p>" + ("text " * 80) + "</p>"
            "<h2>Top picks</h2><p>" + ("more " * 80) + "</p>"
            "<h3>Elfbar BC5000</h3><p>" + ("more " * 80) + "</p>"
        ),
    }
    db_conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, ?)",
        ("article_draft_phased", "0"),
    )
    db_conn.commit()
    generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
        regeneration_context=regen_ctx,
    )
    blob = ""
    for stage_messages in captured["messages_by_stage"].values():
        for msg in stage_messages:
            blob += str(msg.get("content") or "")
    assert "Regenerating an existing article" in blob
    assert "Best Disposable Vapes 2024 Edition" in blob
    # GSC query should appear with click count.
    assert "best disposable vape canada" in blob
    assert "clicks=42" in blob
    # Headings outline should mention an existing h2.
    assert "Why disposables" in blob or "Top picks" in blob
    # Average position should be surfaced (allow either 14.2 or 14.20 formats).
    assert "14.2" in blob


def test_regeneration_context_skipped_when_none(db_conn, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(
            captured,
            _body_with_link("https://example.com/collections/disposable-vapes"),
        ),
    )
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    db_conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, ?)",
        ("article_draft_phased", "0"),
    )
    db_conn.commit()
    generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
        regeneration_context=None,
    )
    blob = ""
    for stage_messages in captured["messages_by_stage"].values():
        for msg in stage_messages:
            blob += str(msg.get("content") or "")
    assert "Regenerating an existing article" not in blob


# ---------------------------------------------------------------------------
# C — PAA snippets are passed to the FAQ repair AI call
# ---------------------------------------------------------------------------
def test_faq_repair_includes_paa_snippets_when_questions_missing(db_conn, monkeypatch):
    captured: dict = {}
    # The body intentionally omits the required PAA questions so FAQ repair fires.
    long_filler = "<p>" + ("Disposable vapes content. " * 950) + "</p>"
    incomplete_body = (
        '<h2>Intro</h2><p>See our <a href="https://example.com/collections/disposable-vapes">collection</a> '
        "of disposable vapes for context.</p>"
        + long_filler
    )
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(captured, incomplete_body),
    )
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    idea_serp_context = {
        "primary_keyword": "disposable vapes",
        "audience_questions": [
            {
                "question": "Are disposable vapes legal in Canada?",
                "snippet": "Yes — disposables are legal nationwide but provincial rules vary on flavours.",
            },
            {
                "question": "How long do disposable vapes last?",
                "snippet": "Most disposables last 600–7000 puffs depending on capacity.",
            },
        ],
    }
    try:
        generate_article_draft(
            db_conn,
            topic="Best Disposable Vapes",
            keywords=["disposable vapes"],
            primary_target=primary_target,
            secondary_targets=[],
            idea_serp_context=idea_serp_context,
        )
    except RuntimeError:
        # Compliance may still fail because our fake AI returns the same incomplete body
        # for the FAQ repair call; we only care that the repair stage was reached and
        # received the PAA snippets in its prompt.
        pass
    repair_messages = captured["messages_by_stage"].get("article_draft_faq_repair")
    assert repair_messages, "FAQ repair stage must have been invoked"
    blob = "".join(str(m.get("content") or "") for m in repair_messages)
    # Snippets must appear in the repair prompt so the AI can ground answers in PAA content.
    assert "provincial rules vary on flavours" in blob
    assert "600\\u20137000 puffs" in blob or "600-7000 puffs" in blob or "600–7000 puffs" in blob
    # Each question must be paired with its snippet (json struct).
    assert "Are disposable vapes legal in Canada?" in blob
    assert "How long do disposable vapes last?" in blob


# ---------------------------------------------------------------------------
# B — content_format + source_type produce a structural directive in the prompt
# ---------------------------------------------------------------------------
def test_structural_directive_appears_for_known_format(db_conn, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(
            captured,
            _body_with_link("https://example.com/collections/disposable-vapes"),
        ),
    )
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    idea_serp_context = {
        "primary_keyword": "disposable vapes",
        "content_format": "buying_guide",
        "source_type": "competitor_gap",
    }
    generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
        idea_serp_context=idea_serp_context,
    )
    blob = ""
    for stage_messages in captured["messages_by_stage"].values():
        for msg in stage_messages:
            blob += str(msg.get("content") or "")
    assert "Structural directives" in blob
    # Format directive present.
    assert "buying guide" in blob
    # Source angle directive present.
    assert "competitor-gap" in blob


def test_structural_directive_surfaces_raw_unknown_format(db_conn, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(
            captured,
            _body_with_link("https://example.com/collections/disposable-vapes"),
        ),
    )
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    idea_serp_context = {
        "primary_keyword": "disposable vapes",
        "content_format": "newfangled_format",
        "source_type": "",
    }
    generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
        idea_serp_context=idea_serp_context,
    )
    blob = ""
    for stage_messages in captured["messages_by_stage"].values():
        for msg in stage_messages:
            blob += str(msg.get("content") or "")
    # Unknown format still surfaces so the writer sees it.
    assert "newfangled_format" in blob


# ---------------------------------------------------------------------------
# D — cluster keyword metrics table shows per-keyword volume / KD / ranking
# ---------------------------------------------------------------------------
def test_cluster_keyword_metrics_table_appears_in_prompt(db_conn, monkeypatch):
    # Build a cluster with 3 keywords joined to keyword_metrics rows of different statuses.
    cur = db_conn.execute(
        """
        INSERT INTO clusters
            (name, content_type, primary_keyword, content_brief, generated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        ("Disposable Vapes Canada", "blog_post", "best disposable vapes canada",
         "Pillar article anchoring the disposable-vape cluster.", "2025-01-01"),
    )
    cluster_id = cur.lastrowid
    cluster_kws = [
        ("best disposable vapes canada", 1200, 28, "commercial", "not_ranking", None, 75.0),
        ("elfbar vs lost mary", 480, 22, "commercial", "striking_distance", 7.2, 80.0),
        ("are disposable vapes legal canada", 320, 18, "informational", "quick_win", 4.1, 88.0),
    ]
    for kw, vol, kd, intent, status, pos, opp in cluster_kws:
        db_conn.execute(
            "INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
            (cluster_id, kw),
        )
        db_conn.execute(
            """
            INSERT INTO keyword_metrics
                (keyword, volume, difficulty, intent, ranking_status, gsc_position, opportunity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (kw, vol, kd, intent, status, pos, opp, 0),
        )
    db_conn.commit()

    captured: dict = {}
    monkeypatch.setattr(
        _article_draft,
        "_call_ai",
        _fake_call_factory(
            captured,
            _body_with_link("https://example.com/collections/disposable-vapes"),
        ),
    )
    primary_target = {
        "type": "collection",
        "handle": "disposable-vapes",
        "title": "Disposable Vapes",
        "url": "https://example.com/collections/disposable-vapes",
    }
    generate_article_draft(
        db_conn,
        topic="Best Disposable Vapes",
        keywords=["disposable vapes"],
        primary_target=primary_target,
        secondary_targets=[],
        linked_cluster_id=cluster_id,
    )
    blob = ""
    for stage_messages in captured["messages_by_stage"].values():
        for msg in stage_messages:
            blob += str(msg.get("content") or "")
    assert "Cluster keyword strategy table" in blob
    # All three keywords appear.
    assert "best disposable vapes canada" in blob
    assert "elfbar vs lost mary" in blob
    assert "are disposable vapes legal canada" in blob
    # Metrics surface inline.
    assert "vol:1200" in blob
    assert "KD:28" in blob
    assert "status:striking_distance" in blob
    assert "status:quick_win" in blob
    assert "pos:7.2" in blob
    # Star prefix for opportunity-rich terms.
    assert "★" in blob


# ---------------------------------------------------------------------------
# #1 — paa_expansion_json is in the draft router's idea SELECT
# ---------------------------------------------------------------------------
def test_draft_router_select_includes_paa_expansion():
    """Regression guard: the SELECT that loads idea data for drafting must include
    paa_expansion_json, otherwise build_paa_question_hierarchy silently returns []."""
    import inspect

    from backend.app.routers import blogs as blogs_router

    src = inspect.getsource(blogs_router._run_generate_article_draft)
    assert "paa_expansion_json" in src, (
        "draft router must SELECT paa_expansion_json for the PAA hierarchy to reach the draft prompt"
    )
