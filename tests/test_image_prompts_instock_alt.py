"""Follow-up to #84: brand/number-free image prompts, in-stock-first writer candidates, word-safe alt text."""
import sqlite3

from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import validate_and_fix_alt_text
from shopifyseo.dashboard_ai_engine_parts.images import _alt_text, _bundle, _providers


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE products (handle TEXT, title TEXT, vendor TEXT, tracks_inventory INTEGER, total_inventory INTEGER)")
    c.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?)",
        [
            ("fog-a", "Fog Formulas 1600 Blue Razz", "Fog Formulas", 1, 0),
            ("fog-b", "Fog Formulas 1600 Mango", "Fog Formulas", 1, 12),
            ("fog-c", "Fog Formulas 1600 Grape", "Fog Formulas", 1, None),
            ("stlth-x", "STLTH 60K Peach", "STLTH", 1, 0),
        ],
    )
    return c


def test_image_safe_text_strips_brands_and_numbers():
    banned = _bundle._brand_terms(_conn())
    assert "Fog Formulas" in banned and "Formulas" in banned and "STLTH" in banned
    out = _bundle.image_safe_text("Fog Formulas 1600 vs STLTH 60K: 20mg Flavour Guide (5%)", banned)
    low = out.lower()
    assert "fog" not in low and "formulas" not in low and "stlth" not in low
    assert not any(ch.isdigit() for ch in out)
    assert "Flavour Guide" in out


def test_section_prompt_has_no_brand_or_numbers():
    banned = _bundle._brand_terms(_conn())
    for i in range(10):
        p = _bundle.build_section_image_prompt("Fog Formulas 1600 Puff Battery Life", i, banned)
        assert "Fog" not in p and "Formulas" not in p
        assert not any(ch.isdigit() for ch in p.replace("3:2", ""))
        assert _bundle.IMAGE_STYLE in p
        assert "smoke" not in p.lower()


def test_rank_in_stock_products_first_keeps_non_product_slots():
    conn = _conn()
    targets = [
        {"type": "collection", "handle": "fog"},
        {"type": "product", "handle": "fog-a"},  # out of stock
        {"type": "blog_article", "handle": "post"},
        {"type": "product", "handle": "fog-b"},
        {"type": "product", "handle": "fog-c"},  # NULL inventory = in stock
    ]
    out = _article_draft.rank_in_stock_products_first(conn, targets)
    assert [t["handle"] for t in out] == ["fog", "fog-b", "post", "fog-c", "fog-a"]
    assert len(out) == len(targets)


def test_focus_product_handles_in_stock_first(monkeypatch):
    conn = _conn()
    monkeypatch.setattr(_article_draft, "_relevance_context", lambda c, t, p: (" fog formulas ", "fog formulas", set()))
    import shopifyseo.product_linkability as pl
    monkeypatch.setattr(pl, "linkable_product_sql", lambda c, alias="p": "1=1")
    out = _article_draft.focus_product_handles(conn, "Fog Formulas 1600", None, limit=2)
    assert "fog-a" not in out and len(out) == 2


def test_validate_alt_never_cuts_mid_word():
    alt = "A sleek matte disposable vape device resting on pale stone beside fresh mango slices under soft window lighting in a studio"
    fixed, _ = validate_and_fix_alt_text(alt + " extraordinarilylongword", max_length=125, log_issues=False)
    assert len(fixed) <= 125
    assert alt.startswith(fixed) or fixed == alt
    assert not fixed.endswith("extraordinarilylongwo")


def test_build_alt_trims_on_word_boundary():
    head = "Fog Formulas Disposable Vape Complete Canadian Guide to Flavours Battery Life Pricing Where To Buy and Everything Else Worth Knowing"
    out = _alt_text._build_featured_alt(head, "")
    assert len(out) <= 125 and head.startswith(out) and head[len(out)] == " "


def test_truncated_caption_detection():
    assert _providers._response_was_truncated({"candidates": [{"finishReason": "MAX_TOKENS"}]})
    assert _providers._response_was_truncated({"choices": [{"finish_reason": "length"}]})
    assert not _providers._response_was_truncated({"candidates": [{"finishReason": "STOP"}]})


def test_gemini_caption_discards_truncated(monkeypatch):
    calls = []

    def fake(url, **kw):
        calls.append(1)
        if len(calls) == 1:
            return {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "A red rectangular Fog Formulas 16"}]}}]}
        return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "A plain matte device on pale stone"}]}}]}

    monkeypatch.setattr(_providers, "request_json", fake)
    monkeypatch.setattr(_providers.time, "sleep", lambda s: None)
    out = _providers._gemini_caption_image_alt("k", b"x", "image/png", 5, model="gemini-3.5-flash-lite")
    assert out == "A plain matte device on pale stone" and len(calls) == 2
