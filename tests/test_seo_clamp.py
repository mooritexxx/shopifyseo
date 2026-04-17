"""SEO field length enforcement after AI generation."""

from shopifyseo.dashboard_ai_engine_parts.qa import clamp_generated_seo_field


def test_clamp_seo_title_trims_over_65():
    s = "x" * 66
    out = clamp_generated_seo_field("seo_title", s)
    assert len(out) == 65


def test_clamp_seo_title_prefers_word_boundary():
    base = "Acme Novo Filter Kits: Essential Features and Tips | Example Store"
    assert len(base) > 65
    out = clamp_generated_seo_field("seo_title", base)
    assert len(out) <= 65
    assert out == out.strip()


def test_clamp_seo_title_unchanged_when_ok():
    s = "Short title that fits easily under limit here ok"
    assert clamp_generated_seo_field("seo_title", s) == s.strip()
