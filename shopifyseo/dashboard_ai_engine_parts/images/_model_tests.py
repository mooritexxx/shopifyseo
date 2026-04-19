"""Settings connectivity smoke tests for configured image and vision models."""

import base64
import sqlite3

from ..settings import ai_settings
from ._providers import (
    _gemini_caption_image_alt,
    _generate_article_image_bytes,
    _openai_caption_image_alt,
    _openrouter_caption_image_alt,
)

_IMAGE_MODEL_TEST_PROMPT = (
    "Small square test image: a single ripe strawberry on a plain light-gray surface, soft studio light, "
    "no text, no watermark, minimalist product photo style."
)

# 1×1 PNG (approx. solid blue pixel) for vision connectivity tests — tiny payload, valid multimodal input.
_VISION_TEST_IMAGE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

_VISION_MODEL_TEST_INSTRUCTION = (
    "This is a Settings connectivity test for a vision (multimodal) model. "
    "The image is a 1×1 pixel test patch. Reply with one short phrase naming the dominant color you see. "
    "Maximum 20 words. No quotation marks, no preamble."
)


def test_image_model(conn: sqlite3.Connection, settings_override: dict[str, str] | None = None) -> dict:
    """Generate one sample image using Settings image provider/model; returns base64 for UI preview (no Shopify upload)."""
    settings = ai_settings(conn, settings_override)
    prov = settings["image_provider"]
    model_raw = settings["image_model"]
    if not prov:
        raise RuntimeError("Set Image generation provider in Settings.")
    if not model_raw:
        raise RuntimeError("Set Image generation model in Settings.")
    if prov not in {"openai", "gemini", "openrouter"}:
        raise RuntimeError(f"Image test supports OpenAI, Gemini, and OpenRouter only (got {prov!r}).")
    if prov == "openai" and not (settings["openai_api_key"] or "").strip():
        raise RuntimeError("OpenAI API key is missing in Settings.")
    if prov == "gemini" and not (settings["gemini_api_key"] or "").strip():
        raise RuntimeError("Gemini API key is missing in Settings.")
    if prov == "openrouter" and not (settings["openrouter_api_key"] or "").strip():
        raise RuntimeError("OpenRouter API key is missing in Settings.")

    raw_bytes, mime = _generate_article_image_bytes(
        settings,
        provider=prov,
        model=model_raw,
        prompt=_IMAGE_MODEL_TEST_PROMPT,
    )
    b64 = base64.b64encode(raw_bytes).decode("ascii")
    return {
        "mime_type": mime,
        "image_base64": b64,
        "_meta": {
            "target": "image",
            "provider": prov,
            "model": model_raw,
            "bytes": len(raw_bytes),
        },
    }


def test_vision_model(conn: sqlite3.Connection, settings_override: dict[str, str] | None = None) -> dict:
    """Send a tiny PNG to the configured Vision provider/model; returns a short caption for UI (no Shopify writes)."""
    settings = ai_settings(conn, settings_override)
    prov = (settings.get("vision_provider") or "").strip().lower()
    model_raw = (settings.get("vision_model") or "").strip()
    timeout = int(settings.get("timeout") or 120)

    if not prov:
        raise RuntimeError("Vision provider could not be resolved. Set Generation provider or Vision override in Settings.")
    if not model_raw:
        raise RuntimeError("Vision model could not be resolved. Set Vision model or Generation model in Settings.")
    if prov not in {"openai", "gemini", "openrouter"}:
        raise RuntimeError(f"Vision test supports OpenAI, Gemini, and OpenRouter only (got {prov!r}).")
    if prov == "openai" and not (settings.get("openai_api_key") or "").strip():
        raise RuntimeError("OpenAI API key is missing in Settings.")
    if prov == "gemini" and not (settings.get("gemini_api_key") or "").strip():
        raise RuntimeError("Gemini API key is missing in Settings.")
    if prov == "openrouter" and not (settings.get("openrouter_api_key") or "").strip():
        raise RuntimeError("OpenRouter API key is missing in Settings.")

    caption: str | None = None
    if prov == "gemini":
        caption = _gemini_caption_image_alt(
            (settings.get("gemini_api_key") or "").strip(),
            _VISION_TEST_IMAGE_PNG,
            "image/png",
            timeout,
            model=model_raw,
            instruction=_VISION_MODEL_TEST_INSTRUCTION,
            alt_max_len=200,
        )
    elif prov == "openrouter":
        caption = _openrouter_caption_image_alt(
            (settings.get("openrouter_api_key") or "").strip(),
            _VISION_TEST_IMAGE_PNG,
            "image/png",
            timeout,
            model=model_raw,
            instruction=_VISION_MODEL_TEST_INSTRUCTION,
            alt_max_len=200,
        )
    else:
        caption = _openai_caption_image_alt(
            (settings.get("openai_api_key") or "").strip(),
            _VISION_TEST_IMAGE_PNG,
            "image/png",
            timeout,
            model=model_raw,
            instruction=_VISION_MODEL_TEST_INSTRUCTION,
            alt_max_len=200,
        )

    if not caption:
        raise RuntimeError("Vision model returned no caption. Check that the model supports image input.")

    return {
        "ok": True,
        "suggested_alt": caption,
        "_meta": {
            "target": "vision",
            "provider": prov,
            "model": model_raw,
        },
    }
