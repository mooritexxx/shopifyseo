"""Image optimizer upload-byte decisions."""

import backend.app.services.image_seo_service._optimizer as _optimizer


def test_filename_only_webp_reupload_preserves_original_bytes() -> None:
    raw = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 8

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
