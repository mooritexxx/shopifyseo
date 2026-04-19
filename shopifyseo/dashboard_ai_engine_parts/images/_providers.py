"""Per-provider image generation and vision caption calls (OpenAI, Gemini, OpenRouter)."""

import base64
import logging
import re
import time

from ...dashboard_http import HttpRequestError, request_json
from ..config import DEFAULT_OPENROUTER_MODEL, GEMINI_API_URL, OPENAI_API_URL, OPENROUTER_API_URL
from ..providers import extract_content, extract_gemini_content
from ._alt_text import _ALT_CAPTION_INSTRUCTION, _sanitize_image_alt
from ._http import _http_get_bytes, _log_gemini_image_usage

logger = logging.getLogger(__name__)


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
