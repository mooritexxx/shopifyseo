"""Orchestration: build featured cover + section images, upload to Shopify, return injection-ready bundle."""

import concurrent.futures
import logging
import sqlite3
from typing import Callable

from ..settings import ai_settings
from ._alt_text import _build_featured_alt, _build_section_alt
from ._encoding import _mime_to_file_extension, _try_encode_image_bytes_as_webp
from ._filenames import _seo_blog_asset_filename
from ._html import extract_first_paragraph_plain_text, parse_h2_sections
from ._providers import _generate_article_image_bytes
from ._vision import _vision_alt_for_article_image

logger = logging.getLogger(__name__)

_MAX_BODY_IMAGES = 8  # upper bound on section images (cost / latency control)


def try_prepare_article_images_bundle(
    conn: sqlite3.Connection,
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
    if prov not in {"openai", "gemini", "openrouter"}:
        emit("No images — WebP encoding not needed.", "encode", "skipped")
        return None, "", [], [f"Skipping images: provider {prov!r} is not supported yet (use OpenAI, Gemini, or OpenRouter)."]
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

    def prepare_shopify_upload(raw: bytes, raw_mime: str) -> tuple[bytes, str, str]:
        """Prefer WebP for new article images; fall back to the provider's format if conversion fails."""
        nonlocal _webp_fallback_logged
        webp, _err = _try_encode_image_bytes_as_webp(raw)
        if webp is not None:
            return webp, "image/webp", ".webp"
        if not _webp_fallback_logged:
            progress.append("WebP conversion skipped — uploading original image format.")
            _webp_fallback_logged = True
        rm = (raw_mime or "image/png").strip() or "image/png"
        return raw, rm, _mime_to_file_extension(rm)

    # ── Featured (blog cover / social) ─────────────────────────────────────
    featured_prompt = (
        f"Wide blog cover photograph for a {_country_adj} online e-commerce retail article. "
        f"Topic: {seed[:420]}. Title: {headline[:200]}. "
        "Premium editorial hero for a blog index or social preview: clear focal subject, "
        "open composition with space for a headline overlay. "
        "CRITICAL: The image must contain absolutely NO text, NO words, NO letters, NO numbers, "
        "NO labels, NO captions, NO signs, NO logos, NO watermarks, NO typography of any kind. "
        "The entire image must be purely photographic with zero rendered text. "
        "Photorealistic, well-lit, commercial quality."
    )
    progress.append("Generating featured cover image (blog / OG style)…")
    try:
        feat_bytes, feat_mime = _generate_article_image_bytes(
            settings, provider=prov, model=model_raw, prompt=featured_prompt
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
    emit("Encoding images as WebP for Shopify (Pillow)…", "encode", "start")
    feat_upload, feat_upload_mime, feat_ext = prepare_shopify_upload(feat_bytes, feat_mime)
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
        "Featured cover uploaded — generating section images in parallel…",
        "image",
        "running",
        images_total=images_total,
        images_done=1,
    )

    # ── Per-H2-section body images (parallel generation) ───────────────────
    progress.append(f"Generating {n_sections} section image{'s' if n_sections != 1 else ''} (parallel)...")
    emit(
        f"Generating {n_sections} section images in parallel…",
        "section-images",
        "start",
        images_total=images_total,
        images_done=1,
    )

    def _build_section_prompt(sec: dict, idx: int) -> str:
        return (
            f"In-article editorial photograph for the section titled '{sec['heading'][:120]}'. "
            f"Context from the section: {sec['context'][:450]}. "
            f"{_country_adj} online e-commerce retail context. Article title: {headline[:200]}. "
            "Must visually illustrate what this specific section discusses — not a generic stock photo. "
            f"Distinct composition from a blog cover hero and from other section images. "
            "CRITICAL: The image must contain absolutely NO text, NO words, NO letters, NO numbers, "
            "NO labels, NO captions, NO signs, NO logos, NO watermarks, NO typography of any kind. "
            "The entire image must be purely photographic with zero rendered text. "
            "Photorealistic, well-lit, commercial quality."
        )

    def _generate_one_section_image(sec: dict, idx: int) -> dict | None:
        """Generate, caption, encode, and upload a single section image. Returns image dict or None."""
        prompt = _build_section_prompt(sec, idx)
        try:
            img_bytes, img_mime = _generate_article_image_bytes(
                settings, provider=prov, model=model_raw, prompt=prompt
            )
        except RuntimeError as exc:
            logger.warning("Section %d image generation failed: %s", idx + 1, exc)
            return None

        alt = _vision_alt_for_article_image(
            settings, img_bytes, img_mime,
            article_title=headline, section_heading=sec["heading"], role="section",
        ) or _build_section_alt(sec["heading"], headline)
        upload_bytes, upload_mime, upload_ext = prepare_shopify_upload(img_bytes, img_mime)
        fname = _seo_blog_asset_filename(alt_text=alt, headline=headline, topic=seed, ext=upload_ext)
        try:
            url = upload_image_bytes_and_get_url(upload_bytes, fname, upload_mime, alt=alt)
        except (RuntimeError, SystemExit) as exc:
            logger.warning("Section %d image upload failed: %s", idx + 1, exc)
            return None

        return {"url": url, "alt": alt, "insert_pos": sec["insert_pos"]}

    body_images: list[dict] = []
    section_ok = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(n_sections, 4)) as pool:
        futures = {
            pool.submit(_generate_one_section_image, sec, idx): idx
            for idx, sec in enumerate(sections)
        }
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
            except Exception:
                logger.exception("Unexpected error generating section %d image", idx + 1)
                result = None
            if result:
                body_images.append(result)
                section_ok += 1
                progress.append(f"Section {idx + 1} image uploaded ('{sections[idx]['heading'][:40]}').")
            else:
                progress.append(f"Section {idx + 1} image skipped ('{sections[idx]['heading'][:40]}').")
            emit(
                f"Section images: {section_ok}/{n_sections} done ({1 + section_ok}/{images_total} uploaded).",
                "image",
                "running",
                images_total=images_total,
                images_done=1 + section_ok,
            )

    progress.append(f"Body images ready: {len(body_images)} of {n_sections} sections.")
    return featured_url, feat_alt, body_images, progress
