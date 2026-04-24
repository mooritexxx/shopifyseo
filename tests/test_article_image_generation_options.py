import base64
from io import BytesIO

from PIL import Image

from shopifyseo.dashboard_ai_engine_parts.images import _gemini_image_bytes


def _tiny_png_b64() -> str:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color=(80, 120, 200)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_gemini_image_generation_sends_aspect_ratio(monkeypatch):
    captured = {}

    def fake_request_json(url, *, method, headers, payload, timeout):
        captured["payload"] = payload
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": _tiny_png_b64(),
                                }
                            }
                        ]
                    }
                }
            ]
        }

    monkeypatch.setattr(
        "shopifyseo.dashboard_ai_engine_parts.images._providers.request_json",
        fake_request_json,
    )

    data, mime = _gemini_image_bytes(
        "key",
        "gemini-2.5-flash-image",
        "Prompt",
        30,
        aspect_ratio="16:9",
    )

    assert data
    assert mime == "image/png"
    assert captured["payload"]["generationConfig"]["imageConfig"] == {"aspectRatio": "16:9"}
