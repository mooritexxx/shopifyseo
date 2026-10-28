"""Cloudflare Workers AI (Flux) image provider, prompt rules, and retry-once behaviour."""
import base64

import pytest

from shopifyseo.dashboard_ai_engine_parts.images import _bundle, _providers
from shopifyseo.dashboard_http import HttpRequestError

JPEG = b"\xff\xd8\xff\xe0fakejpeg"


def _settings(**kw):
    base = {"cloudflare_account_id": "acct123", "cloudflare_ai_token": "tok", "timeout": 30}
    base.update(kw)
    return base


def test_cloudflare_decodes_base64_jpeg(monkeypatch):
    calls = []

    def fake_request_json(url, **kwargs):
        calls.append((url, kwargs))
        return {"success": True, "result": {"image": base64.b64encode(JPEG).decode()}}

    monkeypatch.setattr(_providers, "request_json", fake_request_json)
    raw, mime = _providers._generate_article_image_bytes(
        _settings(), provider="cloudflare", model="@cf/black-forest-labs/flux-1-schnell",
        prompt="a cover", aspect_ratio="16:9", sleep=lambda s: None,
    )
    assert raw == JPEG and mime == "image/jpeg"
    url, kwargs = calls[0]
    assert url.endswith("/accounts/acct123/ai/run/@cf/black-forest-labs/flux-1-schnell")
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["payload"]["steps"] == 4


def test_cloudflare_retries_once_then_raises(monkeypatch):
    attempts = []

    def boom(url, **kwargs):
        attempts.append(1)
        raise HttpRequestError("HTTP 500", status=500)

    monkeypatch.setattr(_providers, "request_json", boom)
    with pytest.raises(RuntimeError):
        _providers._generate_article_image_bytes(
            _settings(), provider="cloudflare", model="", prompt="x",
            aspect_ratio="16:9", sleep=lambda s: None,
        )
    assert len(attempts) == 2  # initial + one retry, no paid fallback


def test_cloudflare_missing_credentials():
    with pytest.raises(RuntimeError, match="account ID"):
        _providers._cloudflare_image_bytes("", "tok", "", "x", 10)
    with pytest.raises(RuntimeError, match="token"):
        _providers._cloudflare_image_bytes("acct", "", "", "x", 10)


def test_prompt_rules_and_product_hint():
    hint = _bundle._product_look_hint("Fog Formulas 1600 Disposable Vape")
    assert "Fog Formulas 1600" in hint and "Do not render" in hint
    rules = _bundle.IMAGE_SAFETY_RULES.lower()
    for word in ("no people", "hands", "smoke", "logos", "lettering"):
        assert word in rules
