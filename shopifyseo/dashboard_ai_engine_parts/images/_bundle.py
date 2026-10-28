"""Orchestration: build featured cover + section images, upload to Shopify, return injection-ready bundle."""

import logging
import re
from typing import Any, Callable

from ..settings import ai_settings
from ..faq_content_filter import validate_and_fix_alt_text
from ._alt_text import _build_featured_alt, _build_section_alt
from ._encoding import (
    _mime_to_file_extension,
    _try_encode_image_bytes_as_webp,
    resize_image_bytes_to_dimensions,
)
from ._filenames import _seo_blog_asset_filename
from ._html import extract_first_paragraph_plain_text, parse_h2_sections
from ._providers import _generate_article_image_bytes
from ._vision import _vision_alt_for_article_image

logger = logging.getLogger(__name__)

_MAX_BODY_IMAGES = 8  # upper bound on section images (cost / latency control)
_ARTICLE_COVER_SIZE = (1600, 900)
_ARTICLE_INLINE_SIZE = (1200, 800)
_ARTICLE_COVER_ASPECT_RATIO = "16:9"
_ARTICLE_INLINE_ASPECT_RATIO = "3:2"

# Applied to every article image prompt (cover + sections), for all providers.
# Phrased positively on purpose: diffusion models such as Flux tend to *draw* whatever a
# prompt names, even after "no" (e.g. "no smoke" yields smoke, "no numbers" yields numbers).
IMAGE_STYLE = (
    "A single plain unbranded matte disposable vape device with smooth blank surfaces, "
    "an empty object-only still life on a clean seamless studio background, "
    "clean clear air, soft even studio lighting, minimalist premium product photography, "
    "photorealistic, sharp focus."
)
IMAGE_SAFETY_RULES = IMAGE_STYLE  # backwards-compatible alias

_STOPWORD_BRAND_TOKENS = {
    "vape", "vapes", "vapor", "vapour", "disposable", "disposables", "pod", "pods", "co", "inc",
    "ltd", "labs", "the", "and", "salt", "salts", "nic", "juice", "liquid", "e-liquid", "canada",
}


def _brand_terms(conn: Any) -> list[str]:
    """Vendor names (and their distinctive words) that must never reach an image prompt."""
    try:
        from shopifyseo.db import execute as db_execute

        rows = db_execute(conn, "SELECT DISTINCT vendor FROM products WHERE vendor IS NOT NULL AND vendor != ''").fetchall()
    except Exception:
        return []
    terms: set[str] = set()
    for (vendor,) in rows:
        v = str(vendor or "").strip()
        if not v:
            continue
        terms.add(v)
        for w in re.split(r"[\s\-_/&]+", v):
            if len(w) >= 3 and w.casefold() not in _STOPWORD_BRAND_TOKENS:
                terms.add(w)
    return sorted(terms, key=len, reverse=True)


def image_safe_text(text: str, banned_terms: list[str] | tuple[str, ...] = ()) -> str:
    """Strip product/brand names and every number (puff counts, mAh, mg, model numbers) from text.

    The result is only used as a loose scene hint, so over-stripping is harmless.
    """
    t = str(text or "")
    for term in banned_terms:
        if term:
            t = re.sub(r"(?<!\w)" + re.escape(term) + r"(?!\w)", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\S*\d\S*", " ", t)  # any token containing a digit (1600, 20mg, 60K, 5%)
    t = re.sub(r"[\"'“”‘’()\[\]{}|#@*_]", " ", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([:;,.!?])", r"\1", t)
    t = re.sub(r"([:;,.!?])(?:\s*[:;,.!?])+", r"\1", t)
    return t.strip(" :;,.-–—")


_SECTION_ANGLES = (
    "three-quarter angle, device resting on smooth pale stone",
    "overhead flat lay, device on soft linen fabric",
    "low side angle, device on light natural wood",
    "close-up macro detail of the device's smooth matte finish",
    "device standing upright on a simple white plinth",
    "device beside a few fresh fruit slices on a clean surface",
    "device on brushed concrete with soft window light",
    "device on a pastel paper sweep with a gentle shadow",
)


def build_section_image_prompt(heading: str, idx: int, banned_terms: list[str] | tuple[str, ...] = ()) -> str:
    """Section image prompt: no product/brand names or numbers, positive phrasing only."""
    theme = image_safe_text(heading, banned_terms)[:100].strip()
    theme_part = f" Mood loosely inspired by the theme '{theme}'." if theme else ""
    return (
        "In-article editorial photograph, 3:2. "
        + IMAGE_STYLE
        + f" Composition: {_SECTION_ANGLES[idx % len(_SECTION_ANGLES)]}."
        + theme_part
    )


def try_prepare_article_images_bundle(
    conn: Any,
    *,
    title: str,
    topic: str,
    body_html: str,
    on_step: Callable[..., None] | None = None,
) -> tuple[str | None, str, list[dict], list[str]]:
    """Generate a featured cover image + one image per H2 section, upload to Shopify Files.

    Returns ``(featured_url, featured_alt, body_images, progress_messages)``.

    *body_images* is a list of dicts with keys ``url``, ``alt``, ``insert_pos`` — ready
    to pass directly to :func:`inject_article_body_images`.  The list may be empty when
    generation or upload fails; the article still gets the featured cover.
    """
    from ...shopify_admin import upload_image_bytes_and_get_url

    progress: list[str] = []
    settings = ai_settings(conn)
    prov = settings["image_provider"]
    model_raw = settings["image_model"]

    from ...market_context import get_primary_country_code, country_display_name
    _market_code = get_primary_country_code(conn)
    _country_adj = country_display_name(_market_code)

    def emit(msg: str, phase: str, state: str, **kwargs: object) -> None:
        if on_step:
            on_step(msg, phase, state, **kwargs)

    # --- Provider validation (unchanged) ---
    if not prov and not model_raw:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: set Image generation provider and model in Settings."]
    if not prov:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: set Image generation provider in Settings."]
    if not model_raw:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: set Image generation model in Settings."]
    if prov not in {"openai", "gemini", "openrouter", "cloudflare"}:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], [f"Skipping images: provider {prov!r} is not supported yet (use OpenAI, Gemini, OpenRouter, or Cloudflare)."]
    if prov == "cloudflare" and not (settings.get("cloudflare_account_id") and settings.get("cloudflare_ai_token")):
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: Cloudflare account ID or Workers AI token is missing in Settings."]
    if prov == "openai" and not settings["openai_api_key"]:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: OpenAI API key is missing in Settings."]
    if prov == "gemini" and not settings["gemini_api_key"]:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: Gemini API key is missing in Settings."]
    if prov == "openrouter" and not settings["openrouter_api_key"]:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], ["Skipping images: OpenRouter API key is missing in Settings."]

    headline = (title or "").strip() or "Vape blog article"
    seed = (topic or "").strip() or headline

    # Plan section images before cover so progress can show total count (1 cover + N sections).
    sections = parse_h2_sections(body_html)
    if not sections:
        intro_plain = extract_first_paragraph_plain_text(body_html)
        intro_context = intro_plain if intro_plain else f"General introduction to: {seed[:320]}"
        sections = [{
            "heading": "Introduction",
            "context": intro_context,
            "insert_pos": (body_html.lower().find("</p>") + 4) if "</p>" in body_html.lower() else 0,
        }]
    sections = sections[:_MAX_BODY_IMAGES]
    n_sections = len(sections)
    images_total = 1 + n_sections
    emit(
        f"Image plan: {images_total} total (1 cover + {n_sections} section). Generating featured cover…",
        "image",
        "start",
        images_total=images_total,
        images_done=0,
    )
    progress.append(
        f"Planned {images_total} images (1 featured cover + {n_sections} section image{'s' if n_sections != 1 else ''})."
    )

    _webp_fallback_logged = False

    def prepare_shopify_upload(raw: bytes, raw_mime: str, target_size: tuple[int, int]) -> tuple[bytes, str, str]:
        """Resize article images to their target dimensions and prefer WebP for Shopify uploads."""
        nonlocal _webp_fallback_logged
        resized, resize_err = resize_image_bytes_to_dimensions(raw, target_size[0], target_size[1])
        source = resized if resized is not None else raw
        if resize_err and not resized:
            progress.append(f"Image resize skipped — uploading original dimensions: {resize_err}")
        webp, _err = _try_encode_image_bytes_as_webp(source)
        if webp is not None:
            return webp, "image/webp", ".webp"
        if not _webp_fallback_logged:
            progress.append("WebP conversion skipped — uploading original image format.")
            _webp_fallback_logged = True
        rm = (raw_mime or "image/png").strip() or "image/png"
        return raw, rm, _mime_to_file_extension(rm)

    # ── Featured (blog cover / social) ─────────────────────────────────────
    _banned = _brand_terms(conn)
    featured_prompt = (
        "Wide blog cover photograph, 16:9 editorial still life. "
        + IMAGE_STYLE
        + " Generous open negative space on one side, calm balanced composition."
    )
    progress.append("Generating featured cover image (blog / OG style)…")
    try:
        feat_bytes, feat_mime = _generate_article_image_bytes(
            settings,
            provider=prov,
            model=model_raw,
            prompt=featured_prompt,
            aspect_ratio=_ARTICLE_COVER_ASPECT_RATIO,
        )
    except RuntimeError as exc:
        progress.append(f"Featured image skipped: {exc}")
        emit("No image bytes — WebP step skipped.", "encode", "skipped")
        return None, "", [], progress

    feat_alt = _vision_alt_for_article_image(
        settings, feat_bytes, feat_mime,
        article_title=headline, role="featured",
    )
    if feat_alt:
        progress.append("Featured image alt text generated via vision model.")
    else:
        feat_alt = _build_featured_alt(headline, seed)
        progress.append("Featured image alt text from article title (vision unavailable).")

    # Validate and fix alt text (guards against prompt leakage, cut-offs, US spelling)
    fallback_alt = _build_featured_alt(headline, seed)
    feat_alt, alt_was_fixed = validate_and_fix_alt_text(
        feat_alt,
        fallback_text=fallback_alt,
        min_length=20,
        max_length=125,
        log_issues=True,
    )
    if alt_was_fixed:
        progress.append("Featured image alt text validated/fixed.")
    emit("Encoding images as WebP for Shopify (Pillow)…", "encode", "start")
    feat_upload, feat_upload_mime, feat_ext = prepare_shopify_upload(feat_bytes, feat_mime, _ARTICLE_COVER_SIZE)
    feat_fname = _seo_blog_asset_filename(alt_text=feat_alt, headline=headline, topic=seed, ext=feat_ext)
    try:
        featured_url = upload_image_bytes_and_get_url(
            feat_upload, feat_fname, feat_upload_mime, alt=feat_alt
        )
    except (RuntimeError, SystemExit) as exc:
        progress.append(f"Featured image upload failed: {exc}")
        emit("WebP encoding finished; featured upload failed.", "encode", "done")
        return None, "", [], progress
    progress.append("Featured cover uploaded to Shopify Files.")
    emit(
        "Featured cover uploaded — generating section images one at a time…",
        "image",
        "running",
        images_total=images_total,
        images_done=1,
    )

    # ── Per-H2-section body images (sequential — free-tier keys 402 in parallel) ──
    progress.append(f"Generating {n_sections} section image{'s' if n_sections != 1 else ''} one at a time...")
    emit(
        f"Generating {n_sections} section images one at a time…",
        "section-images",
        "start",
        images_total=images_total,
        images_done=1,
    )

    def _build_section_prompt(sec: dict, idx: int) -> str:
        return build_section_image_prompt(sec.get("heading") or "", idx, _banned)

    def _generate_one_section_image(sec: dict, idx: int) -> tuple[dict | None, str | None]:
        """Generate, caption, encode, and upload a single section image.

        Returns ``(image_dict, None)`` on success or ``(None, reason)`` on failure.
        """
        prompt = _build_section_prompt(sec, idx)
        try:
            img_bytes, img_mime = _generate_article_image_bytes(
                settings,
                provider=prov,
                model=model_raw,
                prompt=prompt,
                aspect_ratio=_ARTICLE_INLINE_ASPECT_RATIO,
            )
        except RuntimeError as exc:
            logger.warning(
                "Section %d image generation failed: %s; continuing draft without this image.",
                idx + 1,
                exc,
            )
            return None, str(exc)

        alt = _vision_alt_for_article_image(
            settings, img_bytes, img_mime,
            article_title=headline, section_heading=sec["heading"], role="section",
        ) or _build_section_alt(sec["heading"], headline)

        # Validate and fix alt text (guards against prompt leakage, cut-offs, US spelling)
        fallback_alt = _build_section_alt(sec["heading"], headline)
        alt, _ = validate_and_fix_alt_text(
            alt,
            fallback_text=fallback_alt,
            min_length=20,
            max_length=125,
            log_issues=True,
        )

        upload_bytes, upload_mime, upload_ext = prepare_shopify_upload(img_bytes, img_mime, _ARTICLE_INLINE_SIZE)
        fname = _seo_blog_asset_filename(alt_text=alt, headline=headline, topic=seed, ext=upload_ext)
        try:
            url = upload_image_bytes_and_get_url(upload_bytes, fname, upload_mime, alt=alt)
        except (RuntimeError, SystemExit) as exc:
            logger.warning(
                "Section %d image upload failed: %s; continuing draft without this image.",
                idx + 1,
                exc,
            )
            return None, str(exc)

        return {"url": url, "alt": alt, "insert_pos": sec["insert_pos"]}, None

    body_images: list[dict] = []
    section_ok = 0
    for idx, sec in enumerate(sections):
        try:
            result, fail_reason = _generate_one_section_image(sec, idx)
        except Exception as exc:
            logger.exception(
                "Unexpected error generating section %d image; continuing draft without this image.",
                idx + 1,
            )
            result, fail_reason = None, str(exc)
        if result:
            body_images.append(result)
            section_ok += 1
            progress.append(f"Section {idx + 1} image uploaded ('{sections[idx]['heading'][:40]}').")
        else:
            reason = fail_reason or "unknown error"
            progress.append(
                f"Section {idx + 1} image unavailable ('{sections[idx]['heading'][:40]}'): {reason}"
            )
        emit(
            f"Section images: {section_ok}/{n_sections} done ({1 + section_ok}/{images_total} uploaded).",
            "image",
            "running",
            images_total=images_total,
            images_done=1 + section_ok,
        )

    progress.append(f"Body images ready: {len(body_images)} of {n_sections} sections.")
    return featured_url, feat_alt, body_images, progress
