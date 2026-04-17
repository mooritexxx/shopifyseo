"""Article images are re-encoded to WebP before Shopify upload when Pillow can decode the source."""

from io import BytesIO

import pytest

from shopifyseo.dashboard_ai_engine_parts.images import try_encode_image_bytes_as_webp


def _tiny_png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (12, 8), color=(10, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_try_encode_image_bytes_as_webp_outputs_riff_webp():
    webp, err = try_encode_image_bytes_as_webp(_tiny_png())
    assert err is None
    assert webp is not None
    assert webp[:4] == b"RIFF"
    assert webp[8:12] == b"WEBP"


def test_try_encode_image_bytes_as_webp_rejects_empty():
    webp, err = try_encode_image_bytes_as_webp(b"")
    assert webp is None
    assert err


def test_try_encode_image_bytes_as_webp_first_frame_of_animated_gif():
    from PIL import Image

    frames = [
        Image.new("RGB", (8, 8), color=(200, 0, 0)),
        Image.new("RGB", (8, 8), color=(0, 200, 0)),
    ]
    buf = BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    webp, err = try_encode_image_bytes_as_webp(buf.getvalue())
    assert err is None
    assert webp is not None
    assert webp[8:12] == b"WEBP"
