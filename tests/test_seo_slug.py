from shopifyseo.seo_slug import seo_article_slug, slugify_article_handle


def test_slugify_basic():
    assert slugify_article_handle("Salt Nic vs Freebase E-Liquid!") == "salt-nic-vs-freebase-e-liquid"


def test_slugify_accent_strip():
    assert "cafe" in slugify_article_handle("Café Vape Guide")


def test_slugify_empty():
    assert slugify_article_handle("") == "article"
    assert slugify_article_handle("   ") == "article"


def test_slugify_max_len():
    long = "word " * 40
    s = slugify_article_handle(long, max_len=24)
    assert len(s) <= 24


# --- seo_article_slug ---

def test_seo_slug_strips_stop_words():
    slug = seo_article_slug("Everything You Need to Know About SMOK Novo Pod Systems")
    assert "everything" not in slug
    assert "need" not in slug
    assert "about" not in slug
    assert "smok" in slug
    assert "novo" in slug
    assert "pod" in slug


def test_seo_slug_with_keywords():
    slug = seo_article_slug(
        "Everything You Need to Know About SMOK Novo Pod Systems",
        keywords=["novo", "pod", "coils"],
    )
    assert "smok" in slug
    assert "novo" in slug
    assert "coils" in slug


def test_seo_slug_max_5_words():
    slug = seo_article_slug(
        "The Complete Ultimate Guide to Disposable Vapes Pods Coils and More in Canada",
        keywords=["disposable", "vapes", "pods"],
    )
    # Should have at most 5 hyphen-separated segments
    assert len(slug.split("-")) <= 5


def test_seo_slug_empty_title():
    assert seo_article_slug("") == "article"


def test_seo_slug_keywords_fill_gaps():
    slug = seo_article_slug("Vape Tips", keywords=["salt nic", "flavours"])
    assert "vape" in slug
    assert "tips" in slug
    assert "salt" in slug or "nic" in slug
