"""High-level vision-alt-text dispatch (picks provider based on Settings)."""

import logging

from ._providers import (
    _gemini_caption_image_alt,
    _openai_caption_image_alt,
    _openrouter_caption_image_alt,
)

logger = logging.getLogger(__name__)


def _build_article_image_vision_instruction(
    *,
    article_title: str,
    section_heading: str = "",
    role: str = "featured",
) -> str:
    """Vision prompt for article draft images: describe the actual pixels with blog SEO context."""
    title = (article_title or "").strip() or "(untitled article)"
    heading = (section_heading or "").strip()
    role_desc = "featured blog cover / hero image" if role == "featured" else f"in-article section image for '{heading}'" if heading else "in-article section image"
    context_line = f"Section heading: {heading}. " if heading else ""
    return (
        "You write SEO-friendly HTML alt text for a blog article image.\n"
        f"Article title: {title}. {context_line}"
        f"Image role: {role_desc}.\n"
        "Describe only what is clearly visible in the photograph: subjects, objects, materials, "
        "colors, actions, and setting. Be specific about what makes this image unique.\n"
        "SEO: natural language, one clear main subject, incorporate the article topic naturally, "
        "no keyword stuffing, no hype or calls to action.\n"
        "Output a single concise sentence. Maximum 125 characters. No quotation marks, no 'Alt:' prefix."
    )


def _vision_alt_for_article_image(
    settings: dict,
    image_bytes: bytes,
    mime: str,
    *,
    article_title: str,
    section_heading: str = "",
    role: str = "featured",
) -> str | None:
    """Run vision model on article image bytes to generate alt text. Returns None on failure."""
    prov = (settings.get("vision_provider") or "").strip().lower()
    model = (settings.get("vision_model") or "").strip()
    timeout = int(settings.get("timeout") or 90)

    if prov not in ("gemini", "openai", "openrouter"):
        return None

    instr = _build_article_image_vision_instruction(
        article_title=article_title,
        section_heading=section_heading,
        role=role,
    )

    try:
        if prov == "gemini":
            key = (settings.get("gemini_api_key") or "").strip()
            if not key:
                return None
            return _gemini_caption_image_alt(key, image_bytes, mime, timeout, model=model, instruction=instr, alt_max_len=125)
        if prov == "openai":
            key = (settings.get("openai_api_key") or "").strip()
            if not key:
                return None
            return _openai_caption_image_alt(key, image_bytes, mime, timeout, model=model, instruction=instr, alt_max_len=125)
        if prov == "openrouter":
            key = (settings.get("openrouter_api_key") or "").strip()
            if not key:
                return None
            return _openrouter_caption_image_alt(
                key, image_bytes, mime, timeout, model=model, instruction=instr, alt_max_len=125
            )
    except Exception:
        logger.debug("Vision alt for article image failed", exc_info=True)
    return None


def build_image_optimizer_vision_instruction(
    *,
    resource_type: str,
    resource_title: str,
    resource_handle: str,
    role_hint: str,
    variant_labels: list[str] | None,
) -> str:
    """Prompt for catalog Image SEO: SEO-friendly alt grounded in visible pixels + store context."""
    rt = (resource_type or "product").strip() or "product"
    title = (resource_title or "").strip() or "(untitled)"
    handle = (resource_handle or "").strip()
    role = (role_hint or "gallery").strip() or "gallery"
    vpart = ""
    if variant_labels:
        uniq = [str(x).strip() for x in variant_labels if str(x).strip()][:6]
        if uniq:
            vpart = f"Variant context (may apply to this image): {', '.join(uniq)}. "
    handle_line = f"Store handle/slug: {handle}. " if handle else ""
    return (
        "You write SEO-friendly HTML alt text for an e-commerce catalog image.\n"
        f"Catalog item type: {rt}. Title: {title}. {handle_line}"
        f"Image role: {role}. {vpart}"
        "Describe only what is clearly visible: product(s), packaging, materials, colors, and setting. "
        "If text is readable on the product or packaging, transcribe it briefly; otherwise do not guess copy.\n"
        "SEO: natural language, one clear main subject, no keyword stuffing, no hype or calls to action.\n"
        "Output a single concise sentence. Maximum 512 characters. No quotation marks, no 'Alt:' prefix."
    )


def vision_suggest_catalog_image_alt(
    settings: dict,
    *,
    image_bytes: bytes,
    mime: str,
    resource_type: str,
    resource_title: str,
    resource_handle: str,
    role_hint: str,
    variant_labels: list[str] | None = None,
    timeout: int | None = None,
) -> str | None:
    """Use Settings → Vision provider/model to caption a catalog image for alt text."""
    prov = (settings.get("vision_provider") or "").strip().lower()
    model = (settings.get("vision_model") or "").strip()
    to = int(timeout or settings.get("timeout") or 90)
    instr = build_image_optimizer_vision_instruction(
        resource_type=resource_type,
        resource_title=resource_title,
        resource_handle=resource_handle,
        role_hint=role_hint,
        variant_labels=variant_labels,
    )
    if prov == "gemini":
        key = (settings.get("gemini_api_key") or "").strip()
        if not key:
            return None
        return _gemini_caption_image_alt(
            key, image_bytes, mime, to, model=model, instruction=instr, alt_max_len=512
        )
    if prov == "openai":
        key = (settings.get("openai_api_key") or "").strip()
        if not key:
            return None
        return _openai_caption_image_alt(
            key, image_bytes, mime, to, model=model, instruction=instr, alt_max_len=512
        )
    if prov == "openrouter":
        key = (settings.get("openrouter_api_key") or "").strip()
        if not key:
            return None
        return _openrouter_caption_image_alt(
            key, image_bytes, mime, to, model=model, instruction=instr, alt_max_len=512
        )
    return None
