"""Image generation, vision captioning, and article-image orchestration.

Historically a single 1300-line `images.py`; split into focused submodules:

* ``_http``         – HTTP download + usage-metadata logging
* ``_alt_text``     – alt text sanitisation, fallbacks, structured builders
* ``_html``         – article body HTML parsing + image injection
* ``_encoding``     – Pillow normalisation + WebP encoding
* ``_filenames``    – SEO filename helpers
* ``_providers``    – OpenAI / Gemini / OpenRouter image + vision calls
* ``_vision``       – high-level alt-text dispatch (chooses a provider)
* ``_bundle``       – orchestration for article featured + section images
* ``_model_tests``  – Settings connectivity smoke tests

The public API is re-exported here so ``from shopifyseo.dashboard_ai_engine_parts.images
import X`` keeps working for every previously exposed name.
"""

from ._alt_text import (
    _ALT_CAPTION_INSTRUCTION,
    _build_featured_alt,
    _build_section_alt,
    _fallback_alt_from_image_prompt,
    _sanitize_image_alt,
    alt_text_from_prompt,
)
from ._bundle import _MAX_BODY_IMAGES, try_prepare_article_images_bundle
from ._encoding import (
    _PRODUCT_IMAGE_TARGET_SIZE,
    _flatten_alpha_to_white,
    _mime_to_file_extension,
    _normalize_pil_image,
    _pillow_relax_max_pixels,
    _try_encode_image_bytes_as_webp,
    normalize_product_image_bytes,
    try_encode_image_bytes_as_webp,
)
from ._filenames import (
    _SHORT_FILE_ID_ALPHABET,
    _blog_image_slug_stem,
    _random_blog_file_suffix,
    _seo_blog_asset_filename,
)
from ._html import (
    _strip_html_tags,
    extract_first_paragraph_plain_text,
    inject_article_body_image,
    inject_article_body_images,
    parse_h2_sections,
)
from ._http import _http_get_bytes, _log_gemini_image_usage
from ._model_tests import (
    _IMAGE_MODEL_TEST_PROMPT,
    _VISION_MODEL_TEST_INSTRUCTION,
    _VISION_TEST_IMAGE_PNG,
    test_image_model,
    test_vision_model,
)
from ._providers import (
    _decode_data_url_image,
    _extract_openrouter_generated_image,
    _gemini_caption_image_alt,
    _gemini_image_bytes,
    _generate_article_image_bytes,
    _openai_caption_image_alt,
    _openai_image_bytes,
    _openrouter_caption_image_alt,
    _openrouter_image_bytes,
)
from ._vision import (
    _build_article_image_vision_instruction,
    _vision_alt_for_article_image,
    build_image_optimizer_vision_instruction,
    vision_suggest_catalog_image_alt,
)

__all__ = [
    # Public API
    "alt_text_from_prompt",
    "build_image_optimizer_vision_instruction",
    "extract_first_paragraph_plain_text",
    "inject_article_body_image",
    "inject_article_body_images",
    "normalize_product_image_bytes",
    "parse_h2_sections",
    "test_image_model",
    "test_vision_model",
    "try_encode_image_bytes_as_webp",
    "try_prepare_article_images_bundle",
    "vision_suggest_catalog_image_alt",
]
