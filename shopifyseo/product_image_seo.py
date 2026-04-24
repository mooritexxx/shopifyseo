"""Product image SEO: suggested alt text and filenames (e-commerce + accessibility)."""

from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import urlparse, unquote

from shopifyseo.seo_slug import slugify_article_handle

_SHORT_ID_ALPHABET = "23456789abcdefghjkmnpqrtvwxyz"
_ALT_MAX_LEN = 512
_GENERIC_ALT_TOKENS = frozenset(
    {"image", "photo", "picture", "pic", "img", "product", "product image", "untitled"}
)


def random_product_file_suffix(length: int = 4) -> str:
    return "".join(secrets.choice(_SHORT_ID_ALPHABET) for _ in range(length))


def stable_seo_filename_suffix(seed: str) -> str:
    """Deterministic 4-char suffix (same alphabet as :func:`random_product_file_suffix`) per media/catalog row."""
    digest = hashlib.sha256((seed or "").encode("utf-8")).digest()
    chars: list[str] = []
    for byte in digest:
        chars.append(_SHORT_ID_ALPHABET[byte % len(_SHORT_ID_ALPHABET)])
        if len(chars) >= 4:
            break
    return "".join(chars)


def normalize_shopify_image_url(url: str) -> str:
    """Strip query/fragment for stable matching (Shopify CDN often appends width params)."""
    u = (url or "").strip()
    if not u:
        return ""
    parsed = urlparse(u)
    netloc = (parsed.netloc or "").lower()
    scheme = (parsed.scheme or "https").lower()
    # Live Admin API and storefront CDNs may disagree on http vs https; same asset should still match.
    if "shopify" in netloc and (netloc.endswith(".com") or netloc.endswith(".net")):
        scheme = "https"
    path = parsed.path or ""
    return f"{scheme}://{netloc}{path}".rstrip("/")


def filename_from_image_url(url: str) -> str:
    path = urlparse((url or "").strip()).path
    seg = path.rsplit("/", 1)[-1] if path else ""
    return unquote(seg) if seg else ""


def image_format_label_from_url(url: str) -> str:
    """Human-readable format from URL path extension (Shopify CDN may omit true mime)."""
    fn = filename_from_image_url(url)
    stem = fn.split("?", 1)[0]
    if "." not in stem:
        return ""
    ext = stem.rsplit(".", 1)[-1].lower()
    return _IMAGE_EXT_TO_LABEL.get(ext, ext.upper() if ext else "")


_MIME_TO_LABEL: dict[str, str] = {
    "image/jpeg": "JPEG",
    "image/jpg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WebP",
    "image/gif": "GIF",
    "image/avif": "AVIF",
    "image/heic": "HEIC",
    "image/heif": "HEIC",
    "image/svg+xml": "SVG",
}

_IMAGE_EXT_TO_LABEL: dict[str, str] = {
    "jpg": "JPEG",
    "jpeg": "JPEG",
    "png": "PNG",
    "webp": "WebP",
    "gif": "GIF",
    "avif": "AVIF",
    "heic": "HEIC",
    "svg": "SVG",
}


def image_format_label_from_mime(mime: str) -> str:
    """Human-readable format from Content-Type / cached mime (empty if unknown)."""
    m = (mime or "").strip().lower()
    if not m:
        return ""
    if m in _MIME_TO_LABEL:
        return _MIME_TO_LABEL[m]
    if m.startswith("image/"):
        sub = m.split("/", 1)[-1].split(";", 1)[0].strip()
        return sub.upper() if sub else ""
    return ""


def infer_image_format_from_bytes(data: bytes) -> tuple[str, str] | None:
    """Detect (extension with dot, mime) from file magic. Prefer over HTTP Content-Type when CDN headers lie."""
    if not data or len(data) < 12:
        return None
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return ".jpg", "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png", "image/png"
    if len(data) >= 4 and data[:4] == b"\x89PNG":
        return ".png", "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif", "image/gif"
    return None


def is_probably_webp_url(url: str) -> bool:
    fn = filename_from_image_url(url).lower()
    return fn.endswith(".webp") or "format=webp" in (url or "").lower()


def is_weak_image_filename(url: str) -> bool:
    """Heuristic: random names, very short stems, pure dimensions, UUID-like."""
    fn = filename_from_image_url(url)
    stem = fn.rsplit(".", 1)[0].lower() if "." in fn else fn.lower()
    if not stem or len(stem) < 4:
        return True
    if re.match(r"^img[_-]?\d+$", stem, re.I):
        return True
    if re.match(r"^image[_-]?\d+$", stem, re.I):
        return True
    if re.match(r"^\d+x\d+$", stem):
        return True
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", stem, re.I):
        return True
    if re.match(r"^[0-9a-f]{32,}$", stem, re.I):
        return True
    if "_" in stem and not re.search(r"[a-z]{3,}", stem.replace("_", "")):
        return True
    return False


def is_missing_or_generic_alt(alt: str) -> bool:
    t = (alt or "").strip().lower()
    if not t or len(t) < 8:
        return True
    if t in _GENERIC_ALT_TOKENS:
        return True
    return False


def _default_visual_hint(role: str, gallery_position: int | None) -> str:
    if role == "featured":
        return "front view on white background"
    if role == "variant":
        return "product detail photo"
    pos = gallery_position or 1
    if pos <= 1:
        return "alternate product angle"
    if pos == 2:
        return "side view"
    if pos == 3:
        return "detail view"
    return f"gallery image {pos}"


def product_image_seo_suggested_alt(
    *,
    product_title: str,
    role: str,
    gallery_position: int | None = None,
    variant_label: str | None = None,
    visual_hint: str | None = None,
) -> str:
    """Build a suggested alt: product identity + distinct visual/variant context. Clamped to 512 chars."""
    title = (product_title or "").strip() or "Product"
    hint = (visual_hint or "").strip() or _default_visual_hint(role, gallery_position)
    vlab = (variant_label or "").strip()

    if role == "variant" and vlab:
        base = f"{title}, {vlab} — {hint}"
    elif role == "featured":
        base = f"{title} — {hint}"
    else:
        base = f"{title} — {hint}"

    base = re.sub(r"\s+", " ", base).strip()
    if len(base) > _ALT_MAX_LEN:
        base = base[: _ALT_MAX_LEN - 1].rsplit(" ", 1)[0] + "…"
    return base[:_ALT_MAX_LEN]


_SEO_FILENAME_MAX_WORDS = 8

_FILLER_WORDS: frozenset[str] = frozenset({
    "x", "the", "and", "with", "for", "of", "in", "on", "a", "an",
})

# Generic product-type tail keywords preserved when trimming long image filenames.
# Add your store's product-type words here so they're never dropped from SEO filenames.
_SEO_TAIL_KEYWORDS: frozenset[str] = frozenset({
    "kit", "device", "coil", "coils", "accessory", "cartridge", "replacement",
    "ice", "iced",
})


def _smart_trim_handle(handle: str, max_words: int) -> str:
    """Trim a product handle to *max_words* meaningful words.

    Strategy: keep the first 2 words (brand/model) and the last words
    (product-type keywords from _SEO_TAIL_KEYWORDS). Drop filler
    and middle words first. Never cuts mid-word.
    """
    parts = handle.split("-")
    if len(parts) <= max_words:
        return handle

    tail_count = 0
    for w in reversed(parts):
        if w in _SEO_TAIL_KEYWORDS:
            tail_count += 1
        else:
            break
    tail_count = max(tail_count, 1)

    head_budget = max(2, max_words - tail_count)
    head = parts[:head_budget]
    tail = parts[-tail_count:] if tail_count else []

    middle = parts[len(head): len(parts) - tail_count] if tail_count else parts[len(head):]
    middle_kept = [w for w in middle if w not in _FILLER_WORDS]

    room = max_words - len(head) - len(tail)
    if room > 0 and middle_kept:
        middle_kept = middle_kept[:room]
    else:
        middle_kept = []

    result = head + middle_kept + tail
    return "-".join(result)


def product_image_seo_suggested_filename(
    *,
    product_handle: str,
    role: str,
    gallery_position: int | None = None,
    variant_label: str | None = None,
    visual_hint: str | None = None,
    ext: str = ".webp",
    collision_suffix: str | None = None,
) -> str:
    """Build an SEO-optimised image filename (3-8 keyword words + short suffix).

    * **Brand + key descriptor + product type** are preserved (e.g. ``brand-model-color-blue-kit``).
    * Middle filler (``x``, ``the``) is dropped first; then excess middle words.
    * Position suffix (``-2``, ``-3`` …) only for image 2+.
    * Collision suffix shortened to 2 chars.
    """
    ext = ext if ext.startswith(".") else f".{ext}"
    handle = slugify_article_handle(product_handle or "product", max_len=200)

    suf = (collision_suffix or random_product_file_suffix(2))[:2]

    pos = gallery_position if gallery_position is not None else 1
    needs_pos = pos > 1

    vslug = ""
    if role == "variant" and (variant_label or "").strip():
        vslug = slugify_article_handle(variant_label, max_len=16)

    fixed_tail_len = len(suf) + len(ext) + 1
    if needs_pos:
        pos_part = str(pos)
        fixed_tail_len += len(pos_part) + 1
    if vslug:
        fixed_tail_len += len(vslug) + 1

    handle_trimmed = _smart_trim_handle(handle, _SEO_FILENAME_MAX_WORDS)

    parts = [handle_trimmed]
    if vslug:
        parts.append(vslug)
    if needs_pos:
        parts.append(str(pos))
    parts.append(suf)

    stem = "-".join(parts)
    full = f"{stem}{ext}"

    if len(full) > 80:
        handle_trimmed = _smart_trim_handle(handle, max(3, _SEO_FILENAME_MAX_WORDS - 2))
        parts[0] = handle_trimmed
        stem = "-".join(parts)
        full = f"{stem}{ext}"

    return full.lower()
