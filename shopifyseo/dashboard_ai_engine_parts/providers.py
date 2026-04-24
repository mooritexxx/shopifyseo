"""AI provider API abstractions: request dispatch, response extraction, and error formatting.

This module owns everything that speaks directly to an AI provider's HTTP API.
``generation.py`` and other callers use :func:`_call_ai` as the single entry point.
"""

import json
import logging

from ..dashboard_http import HttpRequestError, request_json
from .config import (
    ANTHROPIC_API_URL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GENERATION_PROVIDER,
    DEFAULT_OLLAMA_TIMEOUT_SECONDS,
    DEFAULT_OPENROUTER_MODEL,
    GEMINI_API_URL,
    OPENAI_API_URL,
    OPENROUTER_API_URL,
)

logger = logging.getLogger(__name__)

# When structured JSON asks for very long strings (e.g. article body minLength 14k), raise completion
# budget for *all* providers using the same threshold — no provider-specific callers.
# Article HTML + JSON escaping needs a high ceiling; keep one shared constant for every provider path.
_LONG_STRING_MINLENGTH_THRESHOLD = 8000
_DEFAULT_ANTHROPIC_MAX_TOKENS = 4096
_LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET = 65536


def _max_string_min_length_in_schema_node(node: object, *, depth: int = 0) -> int:
    """Largest ``minLength`` on any string schema nested under *node* (shallow recursion)."""
    if depth > 12 or not isinstance(node, dict):
        return 0
    best = 0
    t = node.get("type")
    if t == "string" and isinstance(node.get("minLength"), int):
        best = max(best, int(node["minLength"]))
    props = node.get("properties")
    if isinstance(props, dict):
        for child in props.values():
            best = max(best, _max_string_min_length_in_schema_node(child, depth=depth + 1))
    items = node.get("items")
    if isinstance(items, dict):
        best = max(best, _max_string_min_length_in_schema_node(items, depth=depth + 1))
    for key in ("anyOf", "oneOf", "allOf"):
        opts = node.get(key)
        if isinstance(opts, list):
            for child in opts:
                best = max(best, _max_string_min_length_in_schema_node(child, depth=depth + 1))
    return best


def _max_string_min_length_from_response_json_schema(json_schema: dict | None) -> int:
    """Inspect OpenAI-style ``{name, schema: {type, properties...}}`` (or a bare JSON Schema object)."""
    if not isinstance(json_schema, dict):
        return 0
    inner = json_schema.get("schema")
    if isinstance(inner, dict):
        return _max_string_min_length_in_schema_node(inner)
    return _max_string_min_length_in_schema_node(json_schema)


def _completion_token_budget_from_json_schema(json_schema: dict | None, stage: str = "") -> int | None:
    """If the response schema implies very large generated strings, return a generous max completion size."""
    st = (stage or "").strip()
    # All article draft stages (single-shot, phased outline, phased HTML batches) share the same high cap.
    if st.startswith("article_draft"):
        return _LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET
    if _max_string_min_length_from_response_json_schema(json_schema) >= _LONG_STRING_MINLENGTH_THRESHOLD:
        return _LARGE_SCHEMA_OUTPUT_TOKEN_BUDGET
    return None


def _log_gemini_usage(response: dict, model: str, call_type: str, stage: str) -> None:
    """Best-effort logging of Gemini token usage from a generateContent response."""
    try:
        from ..api_usage import extract_usage_metadata, log_api_usage
        inp, out, total = extract_usage_metadata(response)
        if total > 0:
            log_api_usage(
                provider="gemini", model=model, call_type=call_type,
                stage=stage, input_tokens=inp, output_tokens=out, total_tokens=total,
            )
    except Exception:
        logger.debug("Gemini usage logging failed", exc_info=True)


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class AIProviderRequestError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, object]):
        super().__init__(message)
        self.details = details


# ---------------------------------------------------------------------------
# Response content extractors
# ---------------------------------------------------------------------------

def extract_content(payload: dict) -> str:
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    raise RuntimeError("OpenAI response did not include message content")


def extract_anthropic_content(payload: dict) -> str:
    parts = []
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
            parts.append(item["text"])
    if parts:
        return "".join(parts)
    raise RuntimeError("Anthropic response did not include text content")


def extract_ollama_content(payload: dict) -> str:
    message = payload.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    raise RuntimeError("Ollama response did not include message content")


def extract_gemini_content(payload: dict) -> str:
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini response did not include candidates")
    content = (candidates[0].get("content") or {})
    parts = content.get("parts") or []
    text_parts = []
    for item in parts:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            text_parts.append(item["text"])
    if text_parts:
        return "".join(text_parts)
    raise RuntimeError("Gemini response did not include text content")


# ---------------------------------------------------------------------------
# Schema conversion
# ---------------------------------------------------------------------------

def _gemini_schema_from_json_schema(schema: dict | None) -> dict | None:
    if not isinstance(schema, dict):
        return None

    type_map = {
        "object": "OBJECT",
        "array": "ARRAY",
        "string": "STRING",
        "number": "NUMBER",
        "integer": "INTEGER",
        "boolean": "BOOLEAN",
        "null": "NULL",
    }

    out: dict[str, object] = {}
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        out["type"] = type_map.get(schema_type.lower(), schema_type.upper())

    if isinstance(schema.get("description"), str) and schema.get("description"):
        out["description"] = schema["description"]

    if isinstance(schema.get("enum"), list) and schema.get("enum"):
        out["enum"] = schema["enum"]

    if isinstance(schema.get("properties"), dict) and schema.get("properties"):
        out["properties"] = {
            key: _gemini_schema_from_json_schema(value) or {}
            for key, value in schema["properties"].items()
            if isinstance(value, dict)
        }

    if isinstance(schema.get("required"), list) and schema.get("required"):
        out["required"] = [item for item in schema["required"] if isinstance(item, str)]

    if isinstance(schema.get("items"), dict):
        items_schema = _gemini_schema_from_json_schema(schema["items"])
        if items_schema:
            out["items"] = items_schema

    for source_key, target_key in (
        ("minLength", "minLength"),
        ("maxLength", "maxLength"),
        ("minItems", "minItems"),
        ("maxItems", "maxItems"),
        ("minimum", "minimum"),
        ("maximum", "maximum"),
    ):
        value = schema.get(source_key)
        if isinstance(value, (int, float)):
            out[target_key] = value

    return out or None


# ---------------------------------------------------------------------------
# JSON response parsing
# ---------------------------------------------------------------------------

def _parse_json_response_text(provider: str, model: str, stage: str, text: str) -> dict:
    normalized = (text or "").strip()
    provider_label = "OpenRouter" if provider == "openrouter" else provider.capitalize()
    if not normalized:
        raise RuntimeError(
            f"{provider_label} returned an empty response for {stage or 'this request'} using model {model}."
        )

    candidates = [normalized]
    if normalized.startswith("```"):
        fenced = normalized.strip("`").strip()
        if fenced.lower().startswith("json"):
            fenced = fenced[4:].strip()
        candidates.append(fenced)

    object_start = normalized.find("{")
    object_end = normalized.rfind("}")
    if object_start != -1 and object_end > object_start:
        candidates.append(normalized[object_start:object_end + 1])

    array_start = normalized.find("[")
    array_end = normalized.rfind("]")
    if array_start != -1 and array_end > array_start:
        candidates.append(normalized[array_start:array_end + 1])

    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        raise RuntimeError(
            f"{provider_label} returned JSON for {stage or 'this request'} using model {model}, "
            "but it was not a JSON object."
        )

    preview = normalized[:280].replace("\n", " ")
    raise RuntimeError(
        f"{provider_label} returned invalid JSON for {stage or 'this request'} using model {model}. "
        f"Response preview: {preview}"
    )


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------

def _friendly_ai_error(exc: Exception) -> str:
    if isinstance(exc, HttpRequestError) and "Read timed out" in str(exc):
        return (
            "The AI request timed out before it finished responding. "
            "This can happen with complex SEO prompts or slow API responses. "
            "The timeout is currently set to 120 seconds. If this persists, you may need to increase the timeout in settings."
        )
    if isinstance(exc, HttpRequestError) and exc.status == 429:
        return "OpenAI rate limit hit. Please wait a few seconds and try again."
    if isinstance(exc, AIProviderRequestError):
        return str(exc)
    return str(exc)


def _provider_display(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def _approx_prompt_tokens(messages: list[dict]) -> int:
    total_chars = sum(len(str(message.get("content") or "")) for message in messages if isinstance(message, dict))
    return max(1, total_chars // 4)


def _parse_provider_error_body(provider: str, body: str) -> dict[str, object]:
    if not body:
        return {}
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"raw_body": body[:500]}
    error = payload.get("error")
    if not isinstance(error, dict):
        if provider == "anthropic" and isinstance(payload.get("type"), str):
            error = payload
        else:
            return {"raw_body": body[:500]}
    details: dict[str, object] = {}
    for key in ("message", "type", "code", "param"):
        value = error.get(key)
        if value not in (None, ""):
            details[f"{provider}_{key}"] = value
    metadata = error.get("metadata")
    if isinstance(metadata, dict):
        for key in ("raw", "provider_name", "is_byok"):
            value = metadata.get(key)
            if value not in (None, ""):
                details[f"{provider}_{key}"] = value
    return details


def _extract_rate_limit_headers(headers: dict[str, object]) -> dict[str, object]:
    wanted = (
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-tokens",
        "retry-after",
        "openai-organization",
        "openai-project",
        "x-request-id",
    )
    normalized = {str(key).lower(): value for key, value in headers.items()}
    return {key: normalized[key] for key in wanted if key in normalized}


def _ai_request_details(*, stage: str, provider: str, model: str, messages: list[dict], json_schema: dict | None, exc: HttpRequestError) -> dict[str, object]:
    prompt_chars = sum(len(str(message.get("content") or "")) for message in messages if isinstance(message, dict))
    details: dict[str, object] = {
        "stage": stage,
        "provider": provider,
        "model": model,
        "prompt_chars": prompt_chars,
        "approx_prompt_tokens": _approx_prompt_tokens(messages),
        "response_format": "json_schema" if json_schema is not None else "json_object",
        "http_status": exc.status,
        "exception_message": str(exc),
        "exception_reason": exc.reason if exc.reason else None,
    }
    details.update(_parse_provider_error_body(provider, exc.body))
    details.update(_extract_rate_limit_headers(getattr(exc, "headers", {}) or {}))
    return details


def _format_ai_request_error(details: dict[str, object]) -> str:
    provider = str(details.get("provider") or "AI")
    provider_label = "OpenRouter" if provider == "openrouter" else "OpenAI" if provider == "openai" else provider.capitalize()
    raw_provider_message = details.get(f"{provider}_message")
    http_status = details.get("http_status")

    if http_status == 429:
        if raw_provider_message not in (None, ""):
            return str(raw_provider_message)
        extras = []
        for key in (
            f"{provider}_type",
            f"{provider}_code",
            f"{provider}_provider_name",
            f"{provider}_is_byok",
            "x-ratelimit-limit-requests",
            "x-ratelimit-remaining-requests",
            "x-ratelimit-limit-tokens",
            "x-ratelimit-remaining-tokens",
            "retry-after",
            "openai-organization",
            "openai-project",
            "x-request-id",
        ):
            value = details.get(key)
            if value not in (None, ""):
                extras.append(f"{key}={value}")
        extra_text = f" Details: {', '.join(extras)}." if extras else ""
        return (
            f"{provider_label} rate limit hit. Please wait a few seconds and try again. "
            f"Stage={details.get('stage')}, model={details.get('model')}, "
            f"approx_prompt_tokens={details.get('approx_prompt_tokens')}, prompt_chars={details.get('prompt_chars')}."
            f"{extra_text}"
        )

    # Build a detailed error message with all available information
    error_parts = []

    # Start with provider message if available (most specific)
    if raw_provider_message not in (None, ""):
        error_parts.append(str(raw_provider_message))

    # Add exception message/reason if different from provider message
    exception_message = details.get("exception_message")
    exception_reason = details.get("exception_reason")
    if exception_message and exception_message not in (raw_provider_message, None, ""):
        # Only add if it provides additional info
        if not raw_provider_message or exception_message not in str(raw_provider_message):
            error_parts.append(str(exception_message))
    if exception_reason and exception_reason not in (raw_provider_message, exception_message, None, ""):
        error_parts.append(f"Reason: {exception_reason}")

    # Add HTTP status if available
    if http_status:
        error_parts.append(f"HTTP {http_status}")

    # Add provider error type/code if available
    provider_type = details.get(f"{provider}_type")
    provider_code = details.get(f"{provider}_code")
    if provider_type or provider_code:
        type_parts = []
        if provider_type:
            type_parts.append(f"type={provider_type}")
        if provider_code:
            type_parts.append(f"code={provider_code}")
        error_parts.append(f"({', '.join(type_parts)})")

    # Add raw body if available (truncated) - only if we don't have a provider message
    if not raw_provider_message:
        raw_body = details.get("raw_body")
        if raw_body:
            body_str = str(raw_body)
            if len(body_str) > 300:
                body_str = body_str[:300] + "..."
            error_parts.append(f"Response: {body_str}")

    # If we have error parts, join them; otherwise use a generic message with available context
    if error_parts:
        return " | ".join(error_parts)

    # Fallback: include what context we have
    context_parts = [f"{provider_label} request failed"]
    if http_status:
        context_parts.append(f"(HTTP {http_status})")
    if details.get("stage"):
        context_parts.append(f"at stage '{details.get('stage')}'")
    if details.get("model"):
        context_parts.append(f"with model '{details.get('model')}'")
    return ". ".join(context_parts) + "."


# ---------------------------------------------------------------------------
# Provider-specific call functions
# ---------------------------------------------------------------------------

def _call_openai(api_key: str, model: str, messages: list[dict], timeout: int, *, json_schema: dict | None = None, stage: str = "") -> dict:
    """Make a single OpenAI call and return the parsed dict.

    When *json_schema* is provided, uses structured output (response_format
    type ``json_schema``) so the API enforces field types and length
    constraints declared in the schema.  Otherwise falls back to plain
    ``json_object`` mode.
    """
    if json_schema is not None:
        response_format = {
            "type": "json_schema",
            "json_schema": json_schema,
        }
    else:
        response_format = {"type": "json_object"}

    payload: dict[str, object] = {
        "model": model,
        "response_format": response_format,
        "messages": messages,
    }
    _budget = _completion_token_budget_from_json_schema(json_schema, stage=stage)
    if _budget is not None:
        payload["max_tokens"] = _budget
    try:
        response = request_json(
            OPENAI_API_URL,
            method="POST",
            headers={"Authorization": f"Bearer {api_key}"},
            payload=payload,
            timeout=timeout,
        )
        return _parse_json_response_text("openai", model, stage, extract_content(response))
    except HttpRequestError as exc:
        details = _ai_request_details(stage=stage, provider="openai", model=model, messages=messages, json_schema=json_schema, exc=exc)
        raise AIProviderRequestError(
            _format_ai_request_error(details),
            details=details,
        ) from exc


def _call_anthropic(
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: int,
    *,
    json_schema: dict | None = None,
    stage: str = "",
) -> dict:
    system_parts = [str(message.get("content") or "") for message in messages if message.get("role") == "system"]
    chat_messages = [
        {"role": message.get("role"), "content": str(message.get("content") or "")}
        for message in messages
        if message.get("role") in {"user", "assistant"}
    ]
    _budget = _completion_token_budget_from_json_schema(json_schema, stage=stage)
    payload = {
        "model": model or DEFAULT_ANTHROPIC_MODEL,
        "max_tokens": _budget if _budget is not None else _DEFAULT_ANTHROPIC_MAX_TOKENS,
        "system": "\n\n".join(part for part in system_parts if part),
        "messages": chat_messages,
    }
    if json_schema is not None:
        payload["tools"] = [{
            "name": "structured_response",
            "description": "Return the response as valid JSON matching the required schema.",
            "input_schema": json_schema.get("schema", json_schema),
        }]
        payload["tool_choice"] = {"type": "tool", "name": "structured_response"}
    try:
        response = request_json(
            ANTHROPIC_API_URL,
            method="POST",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            payload=payload,
            timeout=timeout,
        )
        if json_schema is not None:
            for item in response.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_use" and isinstance(item.get("input"), dict):
                    return item["input"]
            raise RuntimeError("Anthropic response did not include structured tool output")
        return _parse_json_response_text("anthropic", model or DEFAULT_ANTHROPIC_MODEL, stage, extract_anthropic_content(response))
    except HttpRequestError as exc:
        details = _ai_request_details(stage=stage, provider="anthropic", model=model, messages=messages, json_schema=json_schema, exc=exc)
        raise AIProviderRequestError(
            _format_ai_request_error(details),
            details=details,
        ) from exc


def _call_openrouter(api_key: str, model: str, messages: list[dict], timeout: int, *, json_schema: dict | None = None, stage: str = "") -> dict:
    if json_schema is not None:
        response_format = {
            "type": "json_schema",
            "json_schema": json_schema,
        }
    else:
        response_format = {"type": "json_object"}

    payload: dict[str, object] = {
        "model": model or DEFAULT_OPENROUTER_MODEL,
        "response_format": response_format,
        "messages": messages,
    }
    _budget = _completion_token_budget_from_json_schema(json_schema, stage=stage)
    if _budget is not None:
        payload["max_tokens"] = _budget
    try:
        response = request_json(
            OPENROUTER_API_URL,
            method="POST",
            headers={"Authorization": f"Bearer {api_key}"},
            payload=payload,
            timeout=timeout,
        )
        return _parse_json_response_text("openrouter", model or DEFAULT_OPENROUTER_MODEL, stage, extract_content(response))
    except HttpRequestError as exc:
        details = _ai_request_details(stage=stage, provider="openrouter", model=model, messages=messages, json_schema=json_schema, exc=exc)
        raise AIProviderRequestError(
            _format_ai_request_error(details),
            details=details,
        ) from exc


def _call_gemini(
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: int,
    *,
    json_schema: dict | None = None,
    stage: str = "",
) -> dict:
    effective_model = (model or DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    model_path = effective_model if effective_model.startswith("models/") else f"models/{effective_model}"
    system_parts = [str(message.get("content") or "") for message in messages if message.get("role") == "system"]
    contents = []
    for message in messages:
        role = message.get("role")
        if role not in {"user", "assistant"}:
            continue
        text = str(message.get("content") or "")
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": text}],
        })
    payload: dict[str, object] = {
        "contents": contents,
    }
    if system_parts:
        payload["system_instruction"] = {
            "parts": [{"text": "\n\n".join(part for part in system_parts if part)}]
        }
    generation_config: dict[str, object] = {}
    _budget = _completion_token_budget_from_json_schema(json_schema, stage=stage)
    if _budget is not None:
        generation_config["maxOutputTokens"] = _budget
    if json_schema is not None:
        gemini_schema = _gemini_schema_from_json_schema(json_schema.get("schema", json_schema))
        generation_config["responseMimeType"] = "application/json"
        if gemini_schema:
            generation_config["responseSchema"] = gemini_schema
    if generation_config:
        payload["generationConfig"] = generation_config
    try:
        response = request_json(
            f"{GEMINI_API_URL}/{model_path}:generateContent",
            method="POST",
            headers={"x-goog-api-key": api_key},
            payload=payload,
            timeout=timeout,
        )
        _log_gemini_usage(response, effective_model, "chat", stage)
        return _parse_json_response_text("gemini", effective_model, stage, extract_gemini_content(response))
    except HttpRequestError as exc:
        details = _ai_request_details(stage=stage, provider="gemini", model=effective_model, messages=messages, json_schema=json_schema, exc=exc)
        raise AIProviderRequestError(
            _format_ai_request_error(details),
            details=details,
        ) from exc


def _call_ollama(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: int,
    *,
    json_schema: dict | None = None,
    stage: str = "",
) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    effective_timeout = max(timeout, DEFAULT_OLLAMA_TIMEOUT_SECONDS)
    payload: dict[str, object] = {
        "model": model,
        "stream": False,
        "format": json_schema.get("schema", json_schema) if json_schema is not None else "json",
        "messages": messages,
    }
    _budget = _completion_token_budget_from_json_schema(json_schema, stage=stage)
    if _budget is not None:
        payload["options"] = {"num_predict": _budget}
    try:
        response = request_json(
            f"{base_url.rstrip('/')}/api/chat",
            method="POST",
            headers=headers,
            payload=payload,
            timeout=effective_timeout,
        )
        return _parse_json_response_text("ollama", model, stage, extract_ollama_content(response))
    except HttpRequestError as exc:
        details = _ai_request_details(stage=stage, provider="ollama", model=model, messages=messages, json_schema=json_schema, exc=exc)
        raise AIProviderRequestError(
            _format_ai_request_error(details),
            details=details,
        ) from exc


# ---------------------------------------------------------------------------
# Unified dispatcher and credential guard
# ---------------------------------------------------------------------------

def _call_ai(settings: dict, provider: str, model: str, messages: list[dict], timeout: int, *, json_schema: dict | None = None, stage: str = "") -> dict:
    provider = (provider or DEFAULT_GENERATION_PROVIDER).strip().lower()
    if provider == "openai":
        return _call_openai(settings["openai_api_key"], model, messages, timeout, json_schema=json_schema, stage=stage)
    if provider == "gemini":
        return _call_gemini(settings["gemini_api_key"], model, messages, timeout, json_schema=json_schema, stage=stage)
    if provider == "anthropic":
        return _call_anthropic(settings["anthropic_api_key"], model, messages, timeout, json_schema=json_schema, stage=stage)
    if provider == "openrouter":
        return _call_openrouter(settings["openrouter_api_key"], model, messages, timeout, json_schema=json_schema, stage=stage)
    if provider == "ollama":
        return _call_ollama(settings["ollama_base_url"], settings["ollama_api_key"], model, messages, timeout, json_schema=json_schema, stage=stage)
    raise RuntimeError(f"Unsupported AI provider: {provider}")


def _require_provider_credentials(settings: dict, provider: str) -> None:
    provider = (provider or DEFAULT_GENERATION_PROVIDER).strip().lower()
    if provider == "openai" and not settings["openai_api_key"]:
        raise RuntimeError("OpenAI API key is not configured")
    if provider == "gemini" and not settings["gemini_api_key"]:
        raise RuntimeError("Gemini API key is not configured")
    if provider == "anthropic" and not settings["anthropic_api_key"]:
        raise RuntimeError("Anthropic API key is not configured")
    if provider == "openrouter" and not settings["openrouter_api_key"]:
        raise RuntimeError("OpenRouter API key is not configured")
    if provider == "ollama" and not settings["ollama_base_url"]:
        raise RuntimeError("Ollama base URL is not configured")
