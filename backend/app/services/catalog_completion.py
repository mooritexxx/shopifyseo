"""Derive catalog SEO completion rates from sync counts + overview metrics."""

from __future__ import annotations

from typing import Any


def _segment_meta(total: int, missing_meta: int) -> dict[str, Any]:
    total = max(0, int(total))
    missing_meta = max(0, min(int(missing_meta), total))
    complete = total - missing_meta
    pct = round(100.0 * complete / total, 1) if total > 0 else 100.0
    return {
        "total": total,
        "missing_meta": missing_meta,
        "meta_complete": complete,
        "pct_meta_complete": pct,
    }


def build_catalog_completion(
    counts: dict[str, int],
    metrics: dict[str, int],
    *,
    articles_missing_meta: int,
    live_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Meta-complete = both seo_title and seo_description non-empty (same rules as overview SQL).

    ``missing_meta`` only counts items live on the Online Store, so ``total`` must too:
    pass ``live_counts`` (``dq.fetch_live_counts``) to use live-only totals. When omitted,
    ``counts`` is used as before.
    """
    totals = {**counts, **(live_counts or {})}
    products = _segment_meta(totals.get("products", 0), metrics.get("products_missing_meta", 0))
    products["thin_body"] = int(metrics.get("products_thin_body", 0))
    return {
        "products": products,
        "collections": _segment_meta(totals.get("collections", 0), metrics.get("collections_missing_meta", 0)),
        "pages": _segment_meta(totals.get("pages", 0), metrics.get("pages_missing_meta", 0)),
        "articles": _segment_meta(totals.get("blog_articles", 0), articles_missing_meta),
    }
