"""Image optimizer upload-byte decisions."""

from io import BytesIO

import backend.app.services.image_seo_service._optimizer as _optimizer


def _webp_bytes(size: tuple[int, int]) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color=(12, 120, 200)).save(buf, format="WEBP")
    return buf.getvalue()


def test_filename_only_webp_reupload_preserves_original_bytes() -> None:
    raw = _webp_bytes((1000, 1000))

    out, ext, mime, err, preserved = _optimizer._image_upload_output(
        raw,
        "https://cdn.shopify.com/foo.webp",
        "image/webp",
        apply_fn=True,
        convert_webp=False,
    )

    assert out == raw
    assert ext == ".webp"
    assert mime == "image/webp"
    assert err is None
    assert preserved is True


def test_filename_only_webp_reupload_resizes_when_not_square() -> None:
    raw = _webp_bytes((738, 1356))

    out, ext, mime, err, preserved = _optimizer._image_upload_output(
        raw,
        "https://cdn.shopify.com/foo.webp",
        "image/webp",
        apply_fn=True,
        convert_webp=False,
    )

    assert out != raw
    assert ext == ".webp"
    assert mime == "image/webp"
    assert err is None
    assert preserved is False

    from PIL import Image

    im = Image.open(BytesIO(out))
    assert im.size == (1000, 1000)


def test_filename_reupload_converts_non_webp_even_when_convert_flag_false(monkeypatch) -> None:
    calls = {}

    def fake_replace(raw, url, header_mime, *, convert_webp_flag):
        calls["convert_webp_flag"] = convert_webp_flag
        return b"webp", ".webp", "image/webp", None

    monkeypatch.setattr(_optimizer, "_product_image_replace_output", fake_replace)

    out, ext, mime, err, preserved = _optimizer._image_upload_output(
        b"\xff\xd8\xff" + b"x" * 20,
        "https://cdn.shopify.com/foo.jpg",
        "image/jpeg",
        apply_fn=True,
        convert_webp=False,
    )

    assert calls["convert_webp_flag"] is True
    assert (out, ext, mime, err, preserved) == (b"webp", ".webp", "image/webp", None, False)
