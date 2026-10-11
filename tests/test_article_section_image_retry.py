"""Section images generate one at a time and retry OpenRouter HTTP 402/429."""

from __future__ import annotations

import logging
from io import BytesIO

import pytest

from shopifyseo.dashboard_ai_engine_parts.images import (
    IMAGE_RATE_LIMIT_BACKOFF_SECONDS,
    _generate_article_image_bytes,
    _is_image_rate_limit_error,
    _openrouter_image_bytes,
    try_prepare_article_images_bundle,
)
from shopifyseo.dashboard_http import HttpRequestError

_SETTINGS = {
    "image_provider": "openrouter",
    "image_model": "google/gemini-2.5-flash-image",
    "openrouter_api_key": "sk-test",
    "openai_api_key": "",
    "gemini_api_key": "",
    "timeout": 30,
    "vision_provider": "",
    "vision_model": "",
}

_BODY_HTML = (
    "<p>Intro paragraph about starter kits.</p>"
    "<h2>Choosing a starter kit</h2>"
    "<p>Look for a simple pod system with replaceable coils.</p>"
)


def _tiny_png() -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(80, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _http_402(url: str = "https://openrouter.ai/api/v1/chat/completions") -> HttpRequestError:
    return HttpRequestError(f"HTTP 402 for {url}", status=402, body='{"error":{"message":"Payment required"}}')


def _http_429(url: str = "https://openrouter.ai/api/v1/chat/completions") -> HttpRequestError:
    return HttpRequestError(f"HTTP 429 for {url}", status=429, body='{"error":{"message":"Rate limited"}}')


def test_is_image_rate_limit_error_reads_status_and_wrapped_message():
    assert _is_image_rate_limit_error(_http_402()) is True
    assert _is_image_rate_limit_error(_http_429()) is True
    wrapped = RuntimeError("OpenRouter image generation failed: HTTP 402 for https://openrouter.ai/x")
    wrapped.__cause__ = _http_402()
    assert _is_image_rate_limit_error(wrapped) is True
    assert _is_image_rate_limit_error(RuntimeError("Gemini blocked image generation")) is False


def test_openrouter_does_not_try_second_modality_on_402(monkeypatch):
    calls: list[list[str]] = []

    def fake_request_json(_url, *, method, headers, payload, timeout):
        calls.append(list(payload["modalities"]))
        raise _http_402()

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers.request_json",
        fake_request_json,
    )
    with pytest.raises(RuntimeError, match="HTTP 402"):
        _openrouter_image_bytes("sk-test", "google/gemini-2.5-flash-image", "prompt", 30)
    assert calls == [["image", "text"]]


def test_generate_article_image_retries_402_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_openrouter(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("OpenRouter image generation failed: HTTP 402 for https://openrouter.ai/x") from _http_402()
        return _tiny_png(), "image/png"

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._openrouter_image_bytes",
        fake_openrouter,
    )

    data, mime = _generate_article_image_bytes(
        _SETTINGS,
        provider="openrouter",
        model="google/gemini-2.5-flash-image",
        prompt="cover",
        sleep=sleeps.append,
    )

    assert data
    assert mime == "image/png"
    assert calls["n"] == 2
    assert sleeps == [20.0]


def test_generate_article_image_retries_exhausted_raises(monkeypatch):
    sleeps: list[float] = []
    calls = {"n": 0}

    def always_402(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("OpenRouter image generation failed: HTTP 402 for https://openrouter.ai/x") from _http_402()

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._openrouter_image_bytes",
        always_402,
    )

    with pytest.raises(RuntimeError, match="HTTP 402"):
        _generate_article_image_bytes(
            _SETTINGS,
            provider="openrouter",
            model="google/gemini-2.5-flash-image",
            prompt="cover",
            sleep=sleeps.append,
        )

    assert calls["n"] == 1 + len(IMAGE_RATE_LIMIT_BACKOFF_SECONDS)
    assert sleeps == list(IMAGE_RATE_LIMIT_BACKOFF_SECONDS)


def test_generate_article_image_does_not_retry_non_rate_limit(monkeypatch):
    sleeps: list[float] = []
    calls = {"n": 0}

    def boom(*_a, **_k):
        calls["n"] += 1
        raise RuntimeError("OpenRouter response contained no inline image")

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._openrouter_image_bytes",
        boom,
    )

    with pytest.raises(RuntimeError, match="no inline image"):
        _generate_article_image_bytes(
            _SETTINGS,
            provider="openrouter",
            model="google/gemini-2.5-flash-image",
            prompt="cover",
            sleep=sleeps.append,
        )

    assert calls["n"] == 1
    assert sleeps == []


def _patch_bundle_deps(monkeypatch, generate_fn, uploads: list[str] | None = None):
    uploads = uploads if uploads is not None else []

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle.ai_settings",
        lambda _conn: _SETTINGS,
    )
    monkeypatch.setattr(
        "shopifyseo.market_context.get_primary_country_code",
        lambda _conn: "CA",
    )
    monkeypatch.setattr(
        "shopifyseo.market_context.country_display_name",
        lambda _code: "Canadian",
    )
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle._generate_article_image_bytes",
        generate_fn,
    )
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle._vision_alt_for_article_image",
        lambda *_a, **_k: None,
    )

    def fake_upload(_data, filename, _mime, *, alt=None):
        url = f"https://cdn.example.com/{filename}"
        uploads.append(url)
        return url

    monkeypatch.setattr(
        "shopifyseo.shopify_admin.upload_image_bytes_and_get_url",
        fake_upload,
    )
    return uploads


def test_section_image_402_then_success_completes_bundle(monkeypatch):
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_openrouter(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("OpenRouter image generation failed: HTTP 402 for https://openrouter.ai/x") from _http_402()
        return _tiny_png(), "image/png"

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle.ai_settings",
        lambda _conn: _SETTINGS,
    )
    monkeypatch.setattr("shopifyseo.market_context.get_primary_country_code", lambda _conn: "CA")
    monkeypatch.setattr("shopifyseo.market_context.country_display_name", lambda _code: "Canadian")
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._openrouter_image_bytes",
        fake_openrouter,
    )
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._sleep_image_retry",
        sleeps.append,
    )
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle._vision_alt_for_article_image",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "shopifyseo.shopify_admin.upload_image_bytes_and_get_url",
        lambda *_a, **_k: "https://cdn.example.com/img.webp",
    )

    featured_url, featured_alt, body_images, progress = try_prepare_article_images_bundle(
        object(),
        title="Starter kit guide",
        topic="pod systems",
        body_html=_BODY_HTML,
    )

    assert featured_url == "https://cdn.example.com/img.webp"
    assert featured_alt
    assert len(body_images) == 1
    assert body_images[0]["url"] == "https://cdn.example.com/img.webp"
    assert calls["n"] == 3  # featured + section 402 + section success
    assert sleeps == [20.0]
    assert any("one at a time" in note for note in progress)
    assert not any("parallel" in note.lower() for note in progress)


def test_section_image_all_retries_fail_draft_still_completes(monkeypatch, caplog):
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_openrouter(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tiny_png(), "image/png"
        raise RuntimeError("OpenRouter image generation failed: HTTP 402 for https://openrouter.ai/x") from _http_402()

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle.ai_settings",
        lambda _conn: _SETTINGS,
    )
    monkeypatch.setattr("shopifyseo.market_context.get_primary_country_code", lambda _conn: "CA")
    monkeypatch.setattr("shopifyseo.market_context.country_display_name", lambda _code: "Canadian")
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._openrouter_image_bytes",
        fake_openrouter,
    )
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers._sleep_image_retry",
        sleeps.append,
    )
    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._bundle._vision_alt_for_article_image",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "shopifyseo.shopify_admin.upload_image_bytes_and_get_url",
        lambda *_a, **_k: "https://cdn.example.com/cover.webp",
    )

    with caplog.at_level(logging.WARNING, logger="shopifyseo.dashboard_ai_engine_parts.images._bundle"):
        featured_url, featured_alt, body_images, progress = try_prepare_article_images_bundle(
            object(),
            title="Starter kit guide",
            topic="pod systems",
            body_html=_BODY_HTML,
        )

    assert featured_url == "https://cdn.example.com/cover.webp"
    assert featured_alt
    assert body_images == []
    assert calls["n"] == 1 + (1 + len(IMAGE_RATE_LIMIT_BACKOFF_SECONDS))
    assert sleeps == list(IMAGE_RATE_LIMIT_BACKOFF_SECONDS)
    assert any("Section 1 image unavailable" in note for note in progress)
    assert any("HTTP 402" in note for note in progress)
    assert any(
        rec.message.startswith("Section 1 image generation failed:")
        for rec in caplog.records
    )
    assert any("continuing draft without this image" in rec.message for rec in caplog.records)


def test_section_images_run_one_at_a_time(monkeypatch):
    in_flight = {"n": 0, "max": 0}
    order: list[str] = []

    def generate(_settings, *, prompt, **_kwargs):
        in_flight["n"] += 1
        in_flight["max"] = max(in_flight["max"], in_flight["n"])
        try:
            if "Wide blog cover" in prompt:
                kind = "featured"
            elif "In-article editorial" in prompt:
                kind = "section"
            else:
                kind = "other"
            order.append(kind)
            return _tiny_png(), "image/png"
        finally:
            in_flight["n"] -= 1

    _patch_bundle_deps(monkeypatch, generate)

    two_sections = (
        "<p>Intro.</p>"
        "<h2>First section</h2><p>One.</p>"
        "<h2>Second section</h2><p>Two.</p>"
    )
    featured_url, _alt, body_images, progress = try_prepare_article_images_bundle(
        object(),
        title="Two sections",
        topic="kits",
        body_html=two_sections,
    )

    assert featured_url
    assert len(body_images) == 2
    assert order == ["featured", "section", "section"]
    assert in_flight["max"] == 1
    assert any("one at a time" in note for note in progress)
