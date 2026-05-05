from shopifyseo import dashboard_store as ds


class DummyConnection:
    def commit(self) -> None:
        pass


def test_refresh_structured_seo_data_reports_catalog_row_progress(monkeypatch):
    products = [{"handle": "p1"}, {"handle": "p2"}]
    collections = [{"handle": "c1"}]
    pages = [{"handle": "pg1"}]
    articles = [{"blog_handle": "blog", "handle": "a1"}, {"blog_handle": "blog", "handle": "a2"}]
    seen: list[tuple[str, int, int]] = []

    monkeypatch.setattr(ds, "ensure_dashboard_schema", lambda conn: None)
    monkeypatch.setattr(ds.dq, "fetch_all_products", lambda conn: products)
    monkeypatch.setattr(ds.dq, "fetch_all_collections", lambda conn: collections)
    monkeypatch.setattr(ds.dq, "fetch_all_pages", lambda conn: pages)
    monkeypatch.setattr(ds.dq, "fetch_all_blog_articles", lambda conn: articles)
    monkeypatch.setattr(ds.dq, "blog_article_composite_handle", lambda blog, handle: f"{blog}/{handle}")
    monkeypatch.setattr(ds, "_refresh_object_signals_into_table", lambda *args, **kwargs: None)
    monkeypatch.setattr(ds, "_refresh_blog_article_signals_into_table", lambda *args, **kwargs: None)

    ds.refresh_structured_seo_data(
        DummyConnection(),
        progress_callback=lambda kind, done, total: seen.append((kind, done, total)),
    )

    assert seen == [
        ("products", 0, 6),
        ("products", 1, 6),
        ("products", 2, 6),
        ("collections", 3, 6),
        ("pages", 4, 6),
        ("articles", 5, 6),
        ("articles", 6, 6),
    ]
