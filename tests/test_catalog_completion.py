from backend.app.services.catalog_completion import build_catalog_completion


def test_build_catalog_completion_basic():
    counts = {
        "products": 10,
        "collections": 4,
        "pages": 2,
        "blog_articles": 5,
    }
    metrics = {
        "products_missing_meta": 2,
        "collections_missing_meta": 1,
        "pages_missing_meta": 0,
        "products_thin_body": 3,
    }
    out = build_catalog_completion(counts, metrics, articles_missing_meta=1)
    assert out["products"]["meta_complete"] == 8
    assert out["products"]["pct_meta_complete"] == 80.0
    assert out["products"]["thin_body"] == 3
    assert out["collections"]["pct_meta_complete"] == 75.0
    assert out["pages"]["pct_meta_complete"] == 100.0
    assert out["articles"]["missing_meta"] == 1
    assert out["articles"]["pct_meta_complete"] == 80.0


def test_empty_totals_are_handled():
    out = build_catalog_completion(
        {"products": 0, "collections": 0, "pages": 0, "blog_articles": 0},
        {
            "products_missing_meta": 0,
            "collections_missing_meta": 0,
            "pages_missing_meta": 0,
            "products_thin_body": 0,
        },
        articles_missing_meta=0,
    )
    assert out["products"]["pct_meta_complete"] == 100.0
