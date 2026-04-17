"""Tests for index sync target selection (skip already-indexed URLs unless force refresh)."""

import sqlite3

from shopifyseo import dashboard_actions as da


class _Row(dict):
    def __getitem__(self, key: str):
        return self.get(key)


def test_index_inspection_targets_skips_indexed_when_not_force(monkeypatch):
    conn = sqlite3.connect(":memory:")

    products = [
        _Row(handle="a", index_status="Indexed", index_coverage=""),
        _Row(handle="b", index_status="Needs Review", index_coverage=""),
        _Row(handle="c", index_status="Unknown", index_coverage=""),
    ]
    monkeypatch.setattr(da.dq, "fetch_all_products", lambda _c: products)
    monkeypatch.setattr(da.dq, "fetch_all_collections", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_all_pages", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_all_blog_articles", lambda _c: [])

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    assert skipped == 1
    assert [t[1] for t in targets] == ["b", "c"]


def test_index_inspection_targets_force_refresh_uses_all_targets(monkeypatch):
    conn = sqlite3.connect(":memory:")
    all_targets = [
        ("product", "a", "https://example.com/products/a"),
        ("collection", "c", "https://example.com/collections/c"),
    ]
    monkeypatch.setattr(da, "_all_object_targets", lambda _c: list(all_targets))

    targets, skipped = da._index_inspection_targets(conn, force_refresh=True)
    assert skipped == 0
    assert targets == all_targets


def test_index_inspection_targets_blog_article_skips_indexed(monkeypatch):
    conn = sqlite3.connect(":memory:")
    monkeypatch.setattr(da.dq, "fetch_all_products", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_all_collections", lambda _c: [])
    monkeypatch.setattr(da.dq, "fetch_all_pages", lambda _c: [])
    articles = [
        _Row(blog_handle="news", handle="post-1", index_status="Indexed", index_coverage=""),
        _Row(blog_handle="news", handle="post-2", index_status="Not Indexed", index_coverage=""),
    ]
    monkeypatch.setattr(da.dq, "fetch_all_blog_articles", lambda _c: articles)

    targets, skipped = da._index_inspection_targets(conn, force_refresh=False)
    assert skipped == 1
    assert len(targets) == 1
    assert targets[0][0] == "blog_article"
    assert targets[0][1] == "news/post-2"
