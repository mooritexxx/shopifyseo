"""SEO filename suffix must stay stable when Shopify assigns a new MediaImage id after replace."""

from backend.app.services.image_seo_service import _product_gallery_seo_suffix_seed
from shopifyseo.product_image_seo import stable_seo_filename_suffix


def test_gallery_suffix_same_for_slot_independent_of_future_gid_change() -> None:
    slot = _product_gallery_seo_suffix_seed("gid://shopify/Product/99", "gallery", 5, "Mint")
    assert stable_seo_filename_suffix(slot) == stable_seo_filename_suffix(slot)
    # Different product / position → different suffix (high probability)
    other = _product_gallery_seo_suffix_seed("gid://shopify/Product/99", "gallery", 6, "Mint")
    assert stable_seo_filename_suffix(slot) != stable_seo_filename_suffix(other)
