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


# SEO description clamp tests (new 160 char limit)

def test_clamp_seo_description_trims_over_160():
    s = "x" * 165
    out = clamp_generated_seo_field("seo_description", s)
    assert len(out) == 160


def test_clamp_seo_description_unchanged_at_160():
    s = "x" * 160
    out = clamp_generated_seo_field("seo_description", s)
    assert len(out) == 160


def test_clamp_seo_description_unchanged_when_ok():
    s = "Shop the best disposable vapes in Canada. Premium flavours, fast shipping, and excellent customer service. Find your perfect vape today at Vapely."
    assert len(s) < 160
    assert clamp_generated_seo_field("seo_description", s) == s.strip()


def test_clamp_seo_description_prefers_word_boundary():
    # 165 chars with words
    base = "Shop premium disposable vapes in Canada. Wide selection of flavours and nicotine strengths. Fast Canada-wide shipping. Trusted by Canadian vapers. Shop now at Vapely."
    assert len(base) > 160
    out = clamp_generated_seo_field("seo_description", base)
    assert len(out) <= 160
    assert out == out.strip()
