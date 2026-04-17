import base64
import concurrent.futures
import contextlib
import html
import logging
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
from typing import Callable

from ..dashboard_http import HttpRequestError, request_json
from .config import DEFAULT_OPENROUTER_MODEL, GEMINI_API_URL, OPENAI_API_URL, OPENROUTER_API_URL
from .providers import extract_content, extract_gemini_content
from .settings import ai_settings
from ..seo_slug import slugify_article_handle

logger = logging.getLogger(__name__)


def _log_gemini_image_usage(response: dict, model: str, call_type: str, stage: str) -> None:
    try:
        from ..api_usage import extract_usage_metadata, log_api_usage
        inp, out, total = extract_usage_metadata(response)
        if total > 0:
            log_api_usage(
                provider="gemini", model=model, call_type=call_type,
                stage=stage, input_tokens=inp, output_tokens=out, total_tokens=total,
            )
    except Exception:
        logger.debug("Gemini image usage logging failed", exc_info=True)


def _http_get_bytes(url: str, timeout: int) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "ShopifySEO/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
            ct = (resp.headers.get("Content-Type") or "image/png").split(";")[0].strip()
            return raw, ct or "image/png"
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Could not download image URL (HTTP {exc.code})") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not download image URL: {exc}") from exc


def _openai_image_bytes(api_key: str, model: str, prompt: str, timeout: int) -> tuple[bytes, str]:
    """Use configured OpenAI image model exactly as set in Settings (images generations API)."""
    if not (api_key or "").strip():
        raise RuntimeError("OpenAI API key is missing")
    url = "https://api.openai.com/v1/images/generations"
    payload: dict = {"model": (model or "").strip(), "prompt": (prompt or "").strip(), "n": 1}
    headers = {"Authorization": f"Bearer {api_key.strip()}"}
    try:
        response = request_json(url, method="POST", headers=headers, payload=payload, timeout=timeout)
    except HttpRequestError as exc:
        raise RuntimeError(f"OpenAI image request failed: {exc}") from exc
    row = (response.get("data") or [None])[0]
    if not isinstance(row, dict):
        raise RuntimeError("OpenAI image response missing data")
    remote = (row.get("url") or "").strip()
    if remote.startswith("https://") or remote.startswith("http://"):
        return _http_get_bytes(remote, timeout)
    b64 = row.get("b64_json")
    if isinstance(b64, str) and b64.strip():
        return base64.b64decode(b64), "image/png"
    raise RuntimeError("OpenAI image response had no URL or base64 payload")


def _gemini_image_bytes(api_key: str, model: str, prompt: str, timeout: int) -> tuple[bytes, str]:
    """Use configured Gemini image model (generateContent + image modalities)."""
    if not (api_key or "").strip():
        raise RuntimeError("Gemini API key is missing")
    m = (model or "").strip()
    if not m:
        raise RuntimeError("Gemini image model is empty")
    model_path = m if m.startswith("models/") else f"models/{m}"
    payload: dict = {
        "contents": [{"role": "user", "parts": [{"text": (prompt or "").strip()}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }
    try:
        response = request_json(
            f"{GEMINI_API_URL}/{model_path}:generateContent",
            method="POST",
            headers={"x-goog-api-key": api_key.strip()},
            payload=payload,
            timeout=timeout,
        )
    except HttpRequestError as exc:
        raise RuntimeError(f"Gemini image request failed: {exc}") from exc
    _log_gemini_image_usage(response, m, "image", "image_generation")
    pf = response.get("promptFeedback") or {}
    if pf.get("blockReason"):
        raise RuntimeError(f"Gemini blocked image generation: {pf.get('blockReason')}")
    for cand in response.get("candidates") or []:
        content = cand.get("content") or {}
        for part in content.get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                mime = (inline.get("mimeType") or inline.get("mime_type") or "image/png").strip()
                return base64.b64decode(inline["data"]), mime or "image/png"
    raise RuntimeError("Gemini response contained no inline image (check model supports image output)")


def _decode_data_url_image(url: str) -> tuple[bytes, str]:
    m = re.match(r"^data:([^;]+);base64,(.+)$", (url or "").strip(), re.DOTALL)
    if not m:
        raise RuntimeError("Image URL was not a base64 data: URL")
    mime = m.group(1).strip() or "image/png"
    raw = base64.b64decode(m.group(2))
    return raw, mime


def _extract_openrouter_generated_image(response: dict) -> tuple[bytes, str]:
    choices = response.get("choices") or []
    if not choices:
        raise RuntimeError("OpenRouter image response missing choices")
    msg = (choices[0].get("message") or {})
    for img in msg.get("images") or []:
        if not isinstance(img, dict):
            continue
        url_obj = img.get("image_url") or img.get("imageUrl")
        if isinstance(url_obj, dict):
            url = (url_obj.get("url") or "").strip()
        elif isinstance(url_obj, str):
            url = url_obj.strip()
        else:
            url = ""
        if url.startswith("data:"):
            return _decode_data_url_image(url)
    raise RuntimeError("OpenRouter response contained no inline image (check model supports image output via OpenRouter)")


def _openrouter_image_bytes(api_key: str, model: str, prompt: str, timeout: int) -> tuple[bytes, str]:
    """Image generation via OpenRouter chat completions + modalities (see OpenRouter image generation docs)."""
    if not (api_key or "").strip():
        raise RuntimeError("OpenRouter API key is missing")
    m = (model or "").strip()
    if not m:
        raise RuntimeError("OpenRouter image model is empty")
    headers = {"Authorization": f"Bearer {api_key.strip()}"}
    last_err: Exception | None = None
    for modalities in (["image", "text"], ["image"]):
        try:
            response = request_json(
                OPENROUTER_API_URL,
                method="POST",
                headers=headers,
                payload={
                    "model": m,
                    "messages": [{"role": "user", "content": (prompt or "").strip()}],
                    "modalities": modalities,
                },
                timeout=timeout,
            )
            return _extract_openrouter_generated_image(response)
        except (HttpRequestError, RuntimeError) as exc:
            last_err = exc
            continue
    raise RuntimeError(f"OpenRouter image generation failed: {last_err}") from last_err


def _openrouter_caption_image_alt(
    api_key: str,
    image_bytes: bytes,
    mime: str,
    timeout: int,
    *,
    model: str,
    instruction: str | None = None,
    alt_max_len: int = 125,
) -> str | None:
    if not (api_key or "").strip():
        return None
    m = (mime or "image/png").strip() or "image/png"
    model_id = (model or "").strip() or DEFAULT_OPENROUTER_MODEL
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{m};base64,{b64}"
    text_part = (instruction or "").strip() or _ALT_CAPTION_INSTRUCTION
    last_err: Exception | None = None
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            response = request_json(
                OPENROUTER_API_URL,
                method="POST",
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                payload={
                    "model": model_id,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": text_part},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                    "max_tokens": 512 if alt_max_len > 200 else 256,
                    "temperature": 0.3,
                },
                timeout=timeout,
            )
            text = extract_content(response)
        except HttpRequestError as exc:
            last_err = exc
            logger.warning("OpenRouter vision caption attempt %d failed: %s", attempt + 1, exc)
            continue
        except RuntimeError as exc:
            last_err = exc
            logger.warning("OpenRouter vision caption extraction attempt %d failed: %s", attempt + 1, exc)
            continue
        out = _sanitize_image_alt(text, max_len=alt_max_len)
        if out:
            return out
    logger.warning("OpenRouter vision caption failed after 3 attempts: %s", last_err)
    return None


def _generate_article_image_bytes(settings: dict, *, provider: str, model: str, prompt: str) -> tuple[bytes, str]:
    prov = (provider or "").strip().lower()
    timeout = int(settings["timeout"])
    if prov == "openai":
        return _openai_image_bytes(settings["openai_api_key"], model, prompt, timeout)
    if prov == "gemini":
        return _gemini_image_bytes(settings["gemini_api_key"], model, prompt, timeout)
    if prov == "openrouter":
        return _openrouter_image_bytes(settings["openrouter_api_key"], model, prompt, timeout)
    raise RuntimeError(f"Unsupported image provider: {provider}")


def extract_first_paragraph_plain_text(body_html: str, *, max_chars: int = 520) -> str:
    """Plain text from the first <p>…</p> block (for image prompts tied to the intro section)."""
    m = re.search(r"<p\b[^>]*>(.*?)</p>", body_html or "", re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    inner = m.group(1)
    inner = re.sub(r"<[^>]+>", " ", inner)
    inner = html.unescape(inner)
    inner = re.sub(r"\s+", " ", inner).strip()
    if not inner:
        return ""
    return inner[:max_chars]


# ---------------------------------------------------------------------------
# H2 section parsing — used to generate one image per major section
# ---------------------------------------------------------------------------

def _strip_html_tags(text: str) -> str:
    """Remove HTML tags and unescape entities to plain text."""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_h2_sections(body_html: str) -> list[dict]:
    """Parse article body HTML into H2-delimited sections.

    Returns a list of dicts, each with:
        heading     – plain text of the H2
        context     – first ~500 chars of plain text from paragraphs in that section
        insert_pos  – character index in *body_html* after the first </p> within the section
                      (where a section image should be injected)
    Sections before the first H2 (intro) are excluded — they already get the featured/intro image.
    """
    h2_pattern = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.IGNORECASE | re.DOTALL)
    p_pattern = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)

    matches = list(h2_pattern.finditer(body_html))
    if not matches:
        return []

    sections = []
    for i, m in enumerate(matches):
        heading = _strip_html_tags(m.group(1))
        section_start = m.end()
        section_end = matches[i + 1].start() if i + 1 < len(matches) else len(body_html)
        section_html = body_html[section_start:section_end]

        # Collect plain text from <p> tags within this section (up to 500 chars)
        paragraphs = p_pattern.findall(section_html)
        context_parts: list[str] = []
        total = 0
        for p_inner in paragraphs:
            plain = _strip_html_tags(p_inner)
            if plain:
                context_parts.append(plain)
                total += len(plain)
                if total >= 500:
                    break
        context = " ".join(context_parts)[:500]

        # Insertion point: after the first </p> in the section
        p_end_offset = section_html.lower().find("</p>")
        if p_end_offset != -1:
            insert_pos = section_start + p_end_offset + 4  # len("</p>") == 4
        else:
            insert_pos = section_start

        sections.append({"heading": heading, "context": context, "insert_pos": insert_pos})

    return sections


def inject_article_body_image(body_html: str, image_url: str, alt_text: str) -> str:
    """Insert a hero <img> after the first </p> (intro). Uses a simple <p><img></p> — Shopify's editor often strips <figure>."""
    url = (image_url or "").strip()
    if not url.startswith("https://"):
        return body_html
    alt = (alt_text or "").strip() or "Blog hero image"
    safe_alt = html.escape(alt, quote=True)
    block = f'<p><img src="{url}" alt="{safe_alt}" loading="lazy" /></p>'
    lower = body_html.lower()
    idx = lower.find("</p>")
    if idx == -1:
        return block + "\n" + body_html
    end = idx + 4
    return body_html[:end] + "\n" + block + "\n" + body_html[end:]


def inject_article_body_images(body_html: str, images: list[dict]) -> str:
    """Inject multiple images into an article body at their designated positions.

    Each entry in *images* must have keys: url, alt, insert_pos (char index in body_html).
    Images are inserted bottom-up so earlier insertion positions stay valid.
    """
    valid = [img for img in images if (img.get("url") or "").strip().startswith("https://")]
    if not valid:
        return body_html
    # Sort descending by insert_pos so we inject from bottom to top
    for img in sorted(valid, key=lambda x: x["insert_pos"], reverse=True):
        alt = (img.get("alt") or "").strip() or "Blog article image"
        safe_alt = html.escape(alt, quote=True)
        block = f'\n<p><img src="{img["url"]}" alt="{safe_alt}" loading="lazy" /></p>\n'
        pos = img["insert_pos"]
        body_html = body_html[:pos] + block + body_html[pos:]
    return body_html


_ALT_CAPTION_INSTRUCTION = (
    "Write concise image alt text for accessibility and SEO. Describe only what is clearly visible in the photo: "
    "main subjects, setting, lighting, and important objects. Do not invent brand names or on-image text unless "
    "you can clearly read them. No promotional or sales language. One short sentence, maximum 125 characters. "
    "Output only the alt text — no quotes, no label like 'Alt:'."
)

def _sanitize_image_alt(raw: str, *, max_len: int = 125) -> str:
    t = (raw or "").strip()
    t = t.strip("\"'""")
    t = re.sub(r"^(alt\s*text|description|image)\s*:\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_len:
        cut = t[: max_len - 1]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        t = cut + "…"
    return t[:max_len]


def _fallback_alt_from_image_prompt(generation_prompt: str, *, max_len: int = 125) -> str:
    """If vision caption fails: derive alt from the same brief used to generate the image (scene intent, not title spam)."""
    p = re.sub(r"\s+", " ", (generation_prompt or "").strip())
    # Strip boilerplate prefixes used in image generation prompts
    _prefix_patterns = [
        r"^Wide blog cover photograph for a \w[\w\s]* online e-commerce retail article\.\s*",
        r"^Close, in-article editorial photograph for the opening section only[^.]*\.\s*",
        r"^In-article editorial photograph for the section titled '[^']*'\.\s*",
    ]
    for pat in _prefix_patterns:
        p = re.sub(pat, "", p, flags=re.IGNORECASE)
    # Strip common boilerplate sentences that appear in all prompts
    _boilerplate = [
        r"\bNo text, logos, or watermarks\.\s*",
        r"\bPhotorealistic,[^.]*\.\s*",
        r"\b\w[\w\s]* online e-commerce retail context\.\s*",
        r"\bArticle title[^.]*\.\s*",
        r"\bMust visually illustrate[^.]*\.\s*",
        r"\bDistinct composition[^.]*\.\s*",
        r"\bContext from the section:\s*",
        r"\bTitle for reference:[^.]*\.\s*",
        r"\bGround the scene in what the first paragraph actually says[^.]*:\s*",
        r"\bMust read as a distinct illustration[^.]*\.\s*",
        # Featured cover prompt boilerplate
        r"\bTopic:\s*",
        r"\bTitle:\s*",
        r"\bPremium editorial hero[^.]*\.\s*",
        r"\b[Oo]pen composition[^.]*\.\s*",
        r"\b[Cc]ommercial quality\.\s*",
    ]
    for pat in _boilerplate:
        p = re.sub(pat, "", p, flags=re.IGNORECASE)
    p = p.strip()
    if not p:
        return "Photograph supporting an e-commerce retail blog article."[:max_len]
    if len(p) > max_len:
        cut = p[: max_len - 1].rsplit(" ", 1)[0]
        p = (cut + "…") if cut else p[:max_len]
    return p[:max_len]


_SHORT_FILE_ID_ALPHABET = "23456789abcdefghjkmnpqrtvwxyz"


def _random_blog_file_suffix(length: int = 4) -> str:
    """Short disambiguator for filenames (avoids ambiguous 0/O/1/l)."""
    return "".join(secrets.choice(_SHORT_FILE_ID_ALPHABET) for _ in range(length))


def _mime_to_file_extension(mime: str) -> str:
    ml = (mime or "").lower()
    if "jpeg" in ml or "jpg" in ml:
        return ".jpg"
    if "webp" in ml:
        return ".webp"
    return ".png"


def try_encode_image_bytes_as_webp(data: bytes) -> tuple[bytes | None, str | None]:
    """Lossy WebP (quality 88) via Pillow.

    Returns ``(webp_bytes, None)`` on success, or ``(None, short_error_message)`` on failure.
    """
    return _try_encode_image_bytes_as_webp(data)


@contextlib.contextmanager
def _pillow_relax_max_pixels():
    """Allow very large Shopify originals without permanently changing global Pillow state."""
    from PIL import Image

    old = Image.MAX_IMAGE_PIXELS
    try:
        # Default Pillow cap is ~89M pixels; hero/product sources can exceed it.
        Image.MAX_IMAGE_PIXELS = max(int(old or 0), 200_000_000)
        yield
    finally:
        Image.MAX_IMAGE_PIXELS = old


_PRODUCT_IMAGE_TARGET_SIZE = 1000


def _flatten_alpha_to_white(im: "Image.Image") -> "Image.Image":
    """Replace transparency with a white background, returning an RGB image."""
    from PIL import Image as _PILImage

    if im.mode not in ("RGBA", "LA", "PA"):
        return im.convert("RGB") if im.mode != "RGB" else im
    bg = _PILImage.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, (0, 0), im)
    return bg


def _normalize_pil_image(im: "Image.Image", target: int = _PRODUCT_IMAGE_TARGET_SIZE) -> "Image.Image":
    """Resize + pad an image to a square ``target×target`` canvas with a white background.

    * Transparent pixels are flattened to white.
    * Images larger than ``target`` in either dimension are scaled down (aspect-preserving).
    * Images smaller are scaled up.
    * Non-square results are centered on a white canvas.
    """
    from PIL import Image as _PILImage

    im = _flatten_alpha_to_white(im)

    w, h = im.size
    if w == target and h == target:
        return im

    scale = min(target / w, target / h)
    new_w = round(w * scale)
    new_h = round(h * scale)
    im = im.resize((new_w, new_h), _PILImage.LANCZOS)

    if new_w == target and new_h == target:
        return im

    canvas = _PILImage.new("RGB", (target, target), (255, 255, 255))
    x = (target - new_w) // 2
    y = (target - new_h) // 2
    canvas.paste(im, (x, y))
    return canvas


def normalize_product_image_bytes(data: bytes, target: int = _PRODUCT_IMAGE_TARGET_SIZE) -> tuple[bytes | None, str | None]:
    """Pad/resize raw image bytes to ``target×target``. Returns PNG bytes (lossless intermediate)."""
    if not data:
        return None, "empty image data"
    try:
        from io import BytesIO
        from PIL import Image
    except ImportError:
        return None, "Pillow is not installed"

    try:
        with _pillow_relax_max_pixels():
            im = Image.open(BytesIO(data))
            im.load()
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
                im = im.copy()
            if im.mode == "P":
                im = im.convert("RGBA")
            elif im.mode in ("LA", "PA"):
                im = im.convert("RGBA")
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")

            im = _normalize_pil_image(im, target)

            out = BytesIO()
            im.save(out, format="PNG")
            return out.getvalue(), None
    except Exception as exc:
        logger.exception("Image normalization failed (input %d bytes)", len(data))
        return None, str(exc).strip() or type(exc).__name__


def _try_encode_image_bytes_as_webp(data: bytes) -> tuple[bytes | None, str | None]:
    """Lossy WebP (quality 88) for smaller Shopify uploads."""
    if not data:
        return None, "empty image data"
    try:
        from io import BytesIO

        from PIL import Image, features
    except ImportError:
        return None, "Pillow is not installed"

    try:
        if not features.check("webp"):
            return None, "Pillow has no WebP support (libwebp missing in this build). Reinstall Pillow or use a wheel that includes WebP."
    except Exception:
        pass

    try:
        with _pillow_relax_max_pixels():
            im = Image.open(BytesIO(data))
            im.load()
            if getattr(im, "n_frames", 1) > 1:
                im.seek(0)
                im = im.copy()
            if im.mode == "P":
                im = im.convert("RGBA")
            elif im.mode in ("LA", "PA"):
                im = im.convert("RGBA")
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGB")
            out = BytesIO()
            save_kw: dict = {"format": "WEBP", "quality": 88}
            try:
                im.save(out, **save_kw, method=4)
            except Exception:
                out = BytesIO()
                im.save(out, **save_kw, method=0)
            blob = out.getvalue()
            if not blob:
                return None, "WebP encoder returned empty output"
            return blob, None
    except Exception as exc:
        logger.exception("WebP conversion failed (input %d bytes)", len(data))
        msg = str(exc).strip() or type(exc).__name__
        return None, msg


def _blog_image_slug_stem(headline: str, topic: str, *, max_len: int = 52) -> str:
    """Compact SEO stem: comma-separated topic keywords when the title is long; else a trimmed title slug."""
    h = (headline or "").strip()
    t = (topic or "").strip()
    if not h and not t:
        return "blog"

    kws: list[str] = []
    seen: set[str] = set()
    for part in t.split(","):
        seg = slugify_article_handle(part.strip(), max_len=30)
        if len(seg) >= 3 and seg not in seen:
            seen.add(seg)
            kws.append(seg)

    headline_slug = slugify_article_handle(h or t, max_len=120)
    long_title = len(headline_slug) > 40 or len(h.split()) >= 7

    if kws and (long_title or not h):
        stem = "-".join(kws[:3])
    else:
        stem = headline_slug
        if len(stem) > max_len:
            cut = stem[:max_len]
            stem = cut.rsplit("-", 1)[0] if "-" in cut else cut.rstrip("-")

    stem = slugify_article_handle(stem.replace("--", "-"), max_len=max_len)
    if len(stem) > max_len:
        stem = stem[:max_len].rstrip("-")
        if "-" in stem:
            stem = stem.rsplit("-", 1)[0]
    return stem or "blog"


def _seo_blog_asset_filename(*, alt_text: str, headline: str = "", topic: str = "", ext: str) -> str:
    """SEO-optimised filename derived from the vision-generated alt text.

    The alt text describes the actual image pixels, so using it as the filename
    maximises alignment between filename, ``alt`` attribute, and visual content
    — the strongest signal combination for Google Image Search.

    Falls back to headline/topic stem when alt text is empty or too short.
    A 4-char random suffix prevents collisions across regenerations.
    """
    suffix = _random_blog_file_suffix(4)
    ext = ext if ext.startswith(".") else f".{ext}"
    max_stem = 50  # Google gives diminishing weight past ~50 chars

    alt = (alt_text or "").strip()
    # Slugify with generous limit, then trim at word boundary ourselves
    stem = slugify_article_handle(alt, max_len=120) if alt else ""

    # Fall back to headline/topic if alt is missing or too short to be useful
    if len(stem) < 8:
        stem = _blog_image_slug_stem(headline, topic, max_len=max_stem)

    # Trim at a word (hyphen) boundary so filenames never end mid-word.
    # Avoid leaving a stub like "e" from "e-liquid" by requiring 2+ chars in the last segment.
    if len(stem) > max_stem:
        cut = stem[:max_stem]
        while "-" in cut:
            cut = cut.rsplit("-", 1)[0]
            if not cut.endswith("-") and len(cut.rsplit("-", 1)[-1]) >= 2:
                break
        stem = cut

    return f"{stem}-{suffix}{ext}"


def _gemini_caption_image_alt(
    api_key: str,
    image_bytes: bytes,
    mime: str,
    timeout: int,
    *,
    model: str,
    instruction: str | None = None,
    alt_max_len: int = 125,
) -> str | None:
    if not (api_key or "").strip():
        return None
    m = (mime or "image/png").strip() or "image/png"
    model_id = (model or "").replace("models/", "").split("/")[-1].strip() or "gemini-2.5-flash"
    model_path = f"models/{model_id}"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    text_part = (instruction or "").strip() or _ALT_CAPTION_INSTRUCTION
    payload: dict = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": m, "data": b64}},
                    {"text": text_part},
                ],
            }
        ],
        "generationConfig": {"maxOutputTokens": 256, "temperature": 0.3},
    }
    last_err: Exception | None = None
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)  # 1.5s, 3s backoff for rate limits
        try:
            response = request_json(
                f"{GEMINI_API_URL}/{model_path}:generateContent",
                method="POST",
                headers={"x-goog-api-key": api_key.strip()},
                payload=payload,
                timeout=timeout,
            )
        except HttpRequestError as exc:
            last_err = exc
            logger.warning("Gemini vision caption attempt %d failed: %s", attempt + 1, exc)
            continue
        _log_gemini_image_usage(response, model_id, "vision", "vision_caption")
        try:
            text = extract_gemini_content(response)
        except RuntimeError as exc:
            last_err = exc
            logger.warning("Gemini vision caption extraction attempt %d failed: %s", attempt + 1, exc)
            continue
        out = _sanitize_image_alt(text, max_len=alt_max_len)
        if out:
            return out
    logger.warning("Gemini vision caption failed after 3 attempts: %s", last_err)
    return None


def _openai_caption_image_alt(
    api_key: str,
    image_bytes: bytes,
    mime: str,
    timeout: int,
    *,
    model: str,
    instruction: str | None = None,
    alt_max_len: int = 125,
) -> str | None:
    if not (api_key or "").strip():
        return None
    m = (mime or "image/png").strip() or "image/png"
    openai_model = (model or "").strip() or "gpt-4o-mini"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{m};base64,{b64}"
    text_part = (instruction or "").strip() or _ALT_CAPTION_INSTRUCTION
    last_err: Exception | None = None
    for attempt in range(3):
        if attempt:
            time.sleep(1.5 * attempt)  # 1.5s, 3s backoff for rate limits
        try:
            response = request_json(
                OPENAI_API_URL,
                method="POST",
                headers={"Authorization": f"Bearer {api_key.strip()}"},
                payload={
                    "model": openai_model,
                    "max_tokens": 200,
                    "temperature": 0.3,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": text_part},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        }
                    ],
                },
                timeout=timeout,
            )
        except HttpRequestError as exc:
            last_err = exc
            logger.warning("OpenAI vision caption attempt %d failed: %s", attempt + 1, exc)
            continue
        try:
            text = extract_content(response)
        except RuntimeError as exc:
            last_err = exc
            logger.warning("OpenAI vision caption extraction attempt %d failed: %s", attempt + 1, exc)
            continue
        out = _sanitize_image_alt(text, max_len=alt_max_len)
        if out:
            return out
    logger.warning("OpenAI vision caption failed after 3 attempts: %s", last_err)
    return None


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


def alt_text_from_prompt(generation_prompt: str) -> str:
    """Derive concise, SEO-friendly alt text directly from the image generation prompt.

    Since AI image generators faithfully reproduce their prompts, the prompt itself
    is the most reliable description of the image content.  This avoids an extra
    vision-model API call per image (which was the #1 source of alt-text failures).
    """
    return _fallback_alt_from_image_prompt(generation_prompt)


def _build_section_alt(heading: str, headline: str) -> str:
    """Build a concise, descriptive alt text for a section image from structured data.

    Uses the H2 heading as the primary source — it's topical, concise, and describes
    what the section (and therefore the image) is about.  Falls back to the article
    headline if the heading is too short.

    Examples:
        heading="Choosing the Right Pod System" → "Choosing the right pod system for vaping"
        heading="Safety Standards" → "Safety standards in e-liquid manufacturing"
    """
    h = (heading or "").strip()
    t = (headline or "").strip()

    if h:
        # Clean the heading: remove trailing punctuation, normalise case
        alt = re.sub(r"[:.!?]+$", "", h).strip()
        # If the heading is very short (< 4 words), append context from headline
        if len(alt.split()) < 4 and t:
            # Extract a few meaningful words from headline to add context
            title_words = re.sub(r"[^a-zA-Z0-9\s]", " ", t).split()
            extra = [w for w in title_words if w.lower() not in alt.lower().split()][:3]
            if extra:
                alt = f"{alt} — {' '.join(extra).lower()}"
        return alt[:125]

    if t:
        return re.sub(r"[:.!?]+$", "", t).strip()[:125]

    return "Photograph supporting an e-commerce retail blog article"


def _build_featured_alt(headline: str, topic: str) -> str:
    """Build a concise alt text for the featured / hero cover image.

    Uses the article headline directly — it's the best summary of what the
    entire article (and therefore the hero image) is about.
    """
    h = (headline or "").strip()
    t = (topic or "").strip()

    if h:
        return re.sub(r"[:.!?]+$", "", h).strip()[:125]
    if t:
        # Topic is often comma-separated keywords; join first few
        parts = [p.strip() for p in t.split(",") if p.strip()][:4]
        return ", ".join(parts)[:125]

    return "Featured image for an e-commerce retail blog article"


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
    from ..shopify_admin import upload_image_bytes_and_get_url

    progress: list[str] = []
    settings = ai_settings(conn)
    prov = settings["image_provider"]
    model_raw = settings["image_model"]

    from ..market_context import get_primary_country_code, country_display_name
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
