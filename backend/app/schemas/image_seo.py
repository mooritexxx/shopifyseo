from typing import Literal

from pydantic import BaseModel, Field


class CatalogImageSeoRow(BaseModel):
    resource_type: Literal["product", "collection", "page", "article"]
    resource_shopify_id: str
    resource_handle: str
    resource_title: str
    blog_handle: str = ""
    article_handle: str = ""
    image_row_id: str
    image_shopify_id: str = ""
    product_shopify_id: str = ""
    product_handle: str = ""
    product_title: str = ""
    url: str
    alt_text: str = ""
    position: int | None = None
    roles: list[str] = Field(default_factory=list)
    role_for_suggestions: str
    variant_labels: list[str] = Field(default_factory=list)
    suggested_filename_webp: str
    optimize_supported: bool = False
    local_file_cached: bool | None = Field(
        default=None,
        description="Product gallery only: True if post-sync image cache has bytes on disk for this catalog URL.",
    )
    image_width: int | None = Field(default=None, description="Pixel width when known (catalog or API).")
    image_height: int | None = Field(default=None, description="Pixel height when known (catalog or API).")
    image_format: str = Field(
        default="",
        description="Display label e.g. JPEG, WebP (from cache mime for products, URL extension otherwise).",
    )
    file_size_bytes: int | None = Field(
        default=None,
        description="File size in bytes from the local cache (product gallery only).",
    )
    flags: dict[str, bool]


class ImageSeoSummary(BaseModel):
    total_images: int = 0
    optimized: int = 0
    missing_alt: int = 0
    not_webp: int = 0
    weak_filename: int = 0
    locally_cached: int = 0


class ProductImageSeoListPayload(BaseModel):
    items: list[CatalogImageSeoRow]
    total: int
    limit: int
    offset: int
    summary: ImageSeoSummary = Field(default_factory=ImageSeoSummary)


class ProductImageSeoDraftStep(BaseModel):
    id: str
    label: str
    status: Literal["ok", "warning", "skipped", "error"]
    detail: str = ""


class ProductImageSeoDraftRequest(BaseModel):
    product_shopify_id: str
    image_shopify_id: str
    apply_suggested_filename: bool = False
    convert_webp: bool = False
    auto_vision_alt: bool = True


class ProductImageSeoDraftResult(BaseModel):
    ok: bool = True
    message: str
    steps: list[ProductImageSeoDraftStep] = Field(default_factory=list)
    original_size_bytes: int = 0
    draft_size_bytes: int = 0
    draft_alt: str = ""
    draft_filename: str = ""
    draft_mime: str = ""
    preview_base64: str | None = None
    preview_omitted: bool = False


class ProductImageSeoOptimizeRequest(BaseModel):
    product_shopify_id: str
    image_shopify_id: str
    apply_suggested_alt: bool = False
    apply_suggested_filename: bool = False
    convert_webp: bool = False
    alt_override: str | None = None
    dry_run: bool = False


class ImageSeoSuggestAltRequest(BaseModel):
    url: str
    resource_type: Literal["product", "collection", "page", "article"] = "product"
    resource_title: str = ""
    resource_handle: str = ""
    role_for_suggestions: str = "gallery"
    variant_labels: list[str] = Field(default_factory=list)


class ImageSeoSuggestAltResult(BaseModel):
    ok: bool = True
    message: str
    suggested_alt: str = ""


class ProductImageSeoOptimizeResult(BaseModel):
    ok: bool
    message: str
    dry_run: bool = False
    applied_alt: str | None = None
    applied_filename: str | None = None
    new_image_url: str | None = None
    new_media_id: str | None = None
    details: dict | None = None
