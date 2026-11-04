"""Optional article-draft request overrides: length floor, related products, extra rules."""

import pytest
from pydantic import ValidationError

from backend.app.schemas.blog import ArticleGenerateDraftRequest
from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts._article_draft import generate_article_draft
from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import MIN_ARTICLE_BODY_HTML_CHARS
from shopifyseo.dashboard_store import ensure_dashboard_schema

RELATED_PRODUCTS_H2 = "Related products from this brand or collection"
FILLER = "<p>" + ("Product details. " * 1000) + "</p>"
PRODUCT_LINKS = "".join(
    f'<a href="https://example.com/products/p{i}">Product {i}</a>' for i in range(3)
)

_BASE_REQUEST = dict(blog_id="gid://shopify/Blog/1", blog_handle="news", topic="How to charge a device")


@pytest.fixture
def db_conn(testdb, monkeypatch):
    from shopifyseo.dashboard_ai_engine_parts import config
    from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings
    from shopifyseo.dashboard_queries import _urls

    monkeypatch.setattr(config, "_STORE_IDENTITY_CACHE", None)
    monkeypatch.setattr(_urls, "_BASE_URL_CACHE", None)
    conn = testdb.connect()
    ensure_dashboard_schema(conn)
    conn.execute(
        "INSERT INTO service_settings (key, value) VALUES (?, ?)",
        ("store_custom_domain", "https://example.com"),
    )
    conn.executemany(
        "INSERT INTO products (shopify_id, handle, title, tags_json, options_json, raw_json, synced_at) VALUES (?, ?, ?, '[]', '[]', '{}', '')",
        [(f"gid://shopify/Product/{i}", f"p{i}", f"Product {i}") for i in range(3)],
    )
    conn.execute("UPDATE products SET vendor = 'Fog', total_inventory = 5, status = 'ACTIVE'")
    conn.commit()
    monkeypatch.setattr(
        _article_draft, "ai_settings", lambda c: {**ai_settings(c), "article_draft_phased": False}
    )
    return conn


def _ai_payload(body: str) -> dict:
    return {
        "title": "Fog Pro X product guide",
        "seo_title": "Fog Pro X product guide",
        "seo_description": "Product details.",
        "body": body,
    }


def test_request_defaults_match_historical_behaviour():
    req = ArticleGenerateDraftRequest(**_BASE_REQUEST)
    assert req.min_body_chars is None
    assert req.append_related_products is True
    assert req.extra_rules is None


def test_request_accepts_and_validates_optional_overrides():
    req = ArticleGenerateDraftRequest(
        **_BASE_REQUEST,
        min_body_chars=9500,
        append_related_products=False,
        extra_rules=["  Never say miracle  ", "", "Do not name competitors"],
    )
    assert req.min_body_chars == 9500
    assert req.append_related_products is False
    assert req.extra_rules == ["Never say miracle", "Do not name competitors"]
    with pytest.raises(ValidationError):
        ArticleGenerateDraftRequest(**_BASE_REQUEST, min_body_chars=100)
    with pytest.raises(ValidationError):
        ArticleGenerateDraftRequest(**_BASE_REQUEST, min_body_chars=40000)
    with pytest.raises(ValidationError):
        ArticleGenerateDraftRequest(**_BASE_REQUEST, extra_rules=["x" * 241])


def test_generate_article_draft_defaults_keep_14000_floor_and_related_products_h2(db_conn, monkeypatch):
    captured: list[dict] = []
    schemas: dict[str, dict] = {}

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        captured.extend(messages)
        if json_schema is not None:
            schemas[stage or ""] = json_schema
        if stage == "article_draft":
            return _ai_payload(FILLER)
        raise AssertionError(f"unexpected stage {stage!r}")

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)
    out = generate_article_draft(db_conn, "Fog Pro X")
    system_text = "\n".join(m["content"] for m in captured if m.get("role") == "system")
    assert "14,000" in system_text
    assert schemas["article_draft"]["schema"]["properties"]["body"]["minLength"] == MIN_ARTICLE_BODY_HTML_CHARS
    assert RELATED_PRODUCTS_H2 in out["body"]


def test_generate_article_draft_custom_min_body_chars_in_prompt_and_schema(db_conn, monkeypatch):
    captured: list[dict] = []
    schemas: dict[str, dict] = {}

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        captured.extend(messages)
        if json_schema is not None:
            schemas[stage or ""] = json_schema
        if stage == "article_draft":
            return _ai_payload(PRODUCT_LINKS + FILLER)
        raise AssertionError(f"unexpected stage {stage!r}")

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)
    generate_article_draft(db_conn, "Fog Pro X", min_body_chars=9500)
    system_text = "\n".join(m["content"] for m in captured if m.get("role") == "system")
    user_text = "\n".join(m["content"] for m in captured if m.get("role") == "user")
    assert "9,500" in system_text
    assert "9,500" in user_text
    assert "14,000" not in system_text
    assert schemas["article_draft"]["schema"]["properties"]["body"]["minLength"] == 9500


def test_generate_article_draft_skips_related_products_h2_when_disabled(db_conn, monkeypatch):
    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        if stage == "article_draft":
            return _ai_payload(FILLER)
        if stage == "article_draft_append_repair":
            return {"append_html": PRODUCT_LINKS + "<p>More product context for the repair pass.</p>"}
        raise AssertionError(f"unexpected stage {stage!r}")

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)
    out = generate_article_draft(db_conn, "Fog Pro X", append_related_products=False)
    assert RELATED_PRODUCTS_H2 not in out["body"]
    assert "/products/p0" in out["body"]


def test_generate_article_draft_extra_rules_appear_in_system_prompt(db_conn, monkeypatch):
    captured: list[dict] = []

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        captured.extend(messages)
        if stage == "article_draft":
            return _ai_payload(PRODUCT_LINKS + FILLER)
        raise AssertionError(f"unexpected stage {stage!r}")

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)
    generate_article_draft(
        db_conn,
        "Fog Pro X",
        extra_rules=["Never say miracle", "Do not mention competitors by name"],
    )
    system_text = "\n".join(m["content"] for m in captured if m.get("role") == "system")
    assert "Extra writing rules from the requester" in system_text
    assert "- Never say miracle" in system_text
    assert "- Do not mention competitors by name" in system_text


def test_generate_article_draft_reads_overrides_from_request_context(db_conn, monkeypatch):
    captured: list[dict] = []

    def fake_call_ai(settings, provider, model, messages, timeout, *, json_schema=None, stage=""):
        captured.extend(messages)
        if stage == "article_draft":
            return _ai_payload(FILLER)
        if stage == "article_draft_append_repair":
            return {"append_html": PRODUCT_LINKS}
        raise AssertionError(f"unexpected stage {stage!r}")

    monkeypatch.setattr(_article_draft, "_call_ai", fake_call_ai)
    payload = ArticleGenerateDraftRequest(
        **_BASE_REQUEST,
        min_body_chars=9500,
        append_related_products=False,
        extra_rules=["Ban the word guaranteed"],
    )
    out = generate_article_draft(db_conn, "Fog Pro X", request_context=payload.model_dump())
    system_text = "\n".join(m["content"] for m in captured if m.get("role") == "system")
    assert "9,500" in system_text
    assert "- Ban the word guaranteed" in system_text
    assert RELATED_PRODUCTS_H2 not in out["body"]
