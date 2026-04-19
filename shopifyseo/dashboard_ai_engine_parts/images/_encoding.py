"""Pillow-based image normalization and WebP encoding."""

import contextlib
import logging

logger = logging.getLogger(__name__)

_PRODUCT_IMAGE_TARGET_SIZE = 1000


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
