"""Discovery reports progress while fetching and respects Shopify's page cap."""

import threading

import pytest

from shopifyseo.shopify_catalog_sync import discovery as disc_mod
from shopifyseo.shopify_catalog_sync.discovery import (
    MAX_SHOPIFY_PAGE_SIZE,
    discover_shopify_catalog,
)


@pytest.fixture
def fake_shopify(monkeypatch):
    """Stub the five fetchers; record the page size each was asked for."""
    seen: dict = {"page_sizes": [], "product_pages": 0}

    def _products(page_size, *, after_page=None):
        seen["page_sizes"].append(page_size)
        out = []
        for _ in range(3):  # three pages
            out.extend([{"id": f"gid://p/{len(out) + i}"} for i in range(page_size)])
            seen["product_pages"] += 1
            if after_page is not None:
                after_page(len(out))
        return out

    def _collections(page_size):
        seen["page_sizes"].append(page_size)
        return [{"id": "c1"}, {"id": "c2"}]

    def _pages(page_size):
        seen["page_sizes"].append(page_size)
        return [{"id": "pg1"}]

    def _blogs(page_size):
        seen["page_sizes"].append(page_size)
        return [{"id": "b1"}, {"id": "b2"}]

    def _articles(blog_id, page_size):
        return [{"id": f"{blog_id}-a1"}, {"id": f"{blog_id}-a2"}]

    monkeypatch.setattr(disc_mod, "fetch_all_products", _products)
    monkeypatch.setattr(disc_mod, "fetch_all_collections", _collections)
    monkeypatch.setattr(disc_mod, "fetch_all_pages", _pages)
    monkeypatch.setattr(disc_mod, "fetch_all_blogs", _blogs)
    monkeypatch.setattr(disc_mod, "fetch_all_articles_for_blog", _articles)
    return seen


def test_products_report_progress_during_the_fetch_not_only_at_the_end(fake_shopify) -> None:
    """The UI used to sit on one label for the whole product fetch."""
    events: list[tuple[str, int]] = []
    discover_shopify_catalog(10, progress_callback=lambda kind, n: events.append((kind, n)))

    product_events = [n for kind, n in events if kind == "products"]
    # One per page (3) plus the final total.
    assert len(product_events) >= fake_shopify["product_pages"]
    assert product_events == sorted(product_events), "counts must climb, not jump around"
    assert product_events[-1] == 30


def test_all_kinds_still_reported(fake_shopify) -> None:
    events: list[tuple[str, int]] = []
    discover_shopify_catalog(10, progress_callback=lambda kind, n: events.append((kind, n)))
    assert {k for k, _ in events} == {"products", "collections", "pages", "blogs", "blog_articles"}


def test_page_size_is_clamped_to_the_shopify_cap(fake_shopify) -> None:
    discover_shopify_catalog(5000)
    assert set(fake_shopify["page_sizes"]) == {MAX_SHOPIFY_PAGE_SIZE}


def test_page_size_below_the_cap_is_passed_through(fake_shopify) -> None:
    discover_shopify_catalog(50)
    assert set(fake_shopify["page_sizes"]) == {50}


def test_returns_the_same_shape_as_before(fake_shopify) -> None:
    d = discover_shopify_catalog(10)
    assert len(d.products) == 30
    assert len(d.collections) == 2
    assert len(d.pages) == 1
    assert len(d.blogs) == 2
    assert d.blog_articles_total == 4
    assert set(d.articles_by_blog_id) == {"b1", "b2"}


def test_list_fetches_run_concurrently(monkeypatch) -> None:
    """They are independent, so they should overlap rather than queue up."""
    barrier = threading.Barrier(4, timeout=5)

    def _blocking(result):
        def _fn(page_size, **_kw):
            barrier.wait()  # only passes if all four are in flight together
            return result

        return _fn

    monkeypatch.setattr(disc_mod, "fetch_all_products", _blocking([{"id": "p"}]))
    monkeypatch.setattr(disc_mod, "fetch_all_collections", _blocking([]))
    monkeypatch.setattr(disc_mod, "fetch_all_pages", _blocking([]))
    monkeypatch.setattr(disc_mod, "fetch_all_blogs", _blocking([]))
    monkeypatch.setattr(disc_mod, "fetch_all_articles_for_blog", lambda *a, **k: [])

    d = discover_shopify_catalog(10)  # raises BrokenBarrierError if they were sequential
    assert len(d.products) == 1


def test_cancellation_during_the_product_fetch_propagates(fake_shopify) -> None:
    """Cancel is raised from the progress callback, which now runs inside a worker."""

    class Cancelled(Exception):
        pass

    def _cb(kind, n):
        if kind == "products" and n >= 10:
            raise Cancelled()

    with pytest.raises(Cancelled):
        discover_shopify_catalog(10, progress_callback=_cb)
