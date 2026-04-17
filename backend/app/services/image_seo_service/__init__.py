"""image_seo_service package — re-exports all public symbols for backward compatibility.

Internal layout:
  _catalog.py   — catalog image SEO listing (products, collections, pages, articles)
  _optimizer.py — product image optimization pipeline (draft + apply to Shopify)
"""

from ._catalog import (
    list_catalog_image_seo_rows,
    list_product_image_seo_rows,
    suggest_catalog_image_alt_vision,
)
from ._optimizer import (
    draft_optimize_product_image,
    optimize_product_image,
)

__all__ = [
    "list_catalog_image_seo_rows",
    "list_product_image_seo_rows",
    "suggest_catalog_image_alt_vision",
    "draft_optimize_product_image",
    "optimize_product_image",
]
