"""HTTP + usage-logging helpers shared by image/vision provider calls."""

import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


def _log_gemini_image_usage(response: dict, model: str, call_type: str, stage: str) -> None:
    try:
        from ...api_usage import extract_usage_metadata, log_api_usage
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
