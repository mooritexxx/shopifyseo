import sqlite3

from .config import (
    DEFAULT_GENERATION_MODEL,
    DEFAULT_GENERATION_PROVIDER,
    DEFAULT_MAX_RETRIES,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_PROMPT_PROFILE,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_REVIEW_MODEL,
    DEFAULT_REVIEW_PROVIDER,
    DEFAULT_TIMEOUT_SECONDS,
    PROMPT_VERSION_ALIASES,
    default_model_for_provider,
)
from .context import setting


def ai_settings(conn: sqlite3.Connection, overrides: dict[str, str] | None = None) -> dict:
    overrides = overrides or {}

    def setting_with_override(key: str, default: str = "") -> str:
        override_value = overrides.get(key)
        if isinstance(override_value, str):
            return override_value.strip()
        return setting(conn, key, default)

    timeout_raw = setting_with_override("ai_timeout_seconds", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        timeout = int((timeout_raw or str(DEFAULT_TIMEOUT_SECONDS)).strip() or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        timeout = DEFAULT_TIMEOUT_SECONDS
    timeout = max(10, min(600, timeout))
    retries_raw = setting_with_override("ai_max_retries", str(DEFAULT_MAX_RETRIES))
    try:
        retries = max(0, int(retries_raw or DEFAULT_MAX_RETRIES))
    except ValueError:
        retries = DEFAULT_MAX_RETRIES
    generation_provider = (setting_with_override("ai_generation_provider", DEFAULT_GENERATION_PROVIDER) or DEFAULT_GENERATION_PROVIDER).strip().lower()
    review_provider = (setting_with_override("ai_review_provider", DEFAULT_REVIEW_PROVIDER) or DEFAULT_REVIEW_PROVIDER).strip().lower()
    generation_model = (setting_with_override("ai_generation_model", DEFAULT_GENERATION_MODEL) or DEFAULT_GENERATION_MODEL).strip()
    sidekick_provider_raw = setting_with_override("ai_sidekick_provider", "")
    sidekick_model_raw = setting_with_override("ai_sidekick_model", "")
    sidekick_provider = (sidekick_provider_raw or "").strip().lower() or generation_provider
    sidekick_model = (sidekick_model_raw or "").strip()
    if not sidekick_model:
        sidekick_model = (
            generation_model if sidekick_provider == generation_provider else default_model_for_provider(sidekick_provider)
        )
    vision_provider_raw = setting_with_override("ai_vision_provider", "")
    vision_model_raw = setting_with_override("ai_vision_model", "")
    vision_provider = (vision_provider_raw or "").strip().lower() or generation_provider
    vision_model = (vision_model_raw or "").strip()
    if not vision_model:
        vision_model = (
            generation_model if vision_provider == generation_provider else default_model_for_provider(vision_provider)
        )
    review_model = (setting_with_override("ai_review_model", DEFAULT_REVIEW_MODEL) or DEFAULT_REVIEW_MODEL).strip()
    return {
        "api_key": setting_with_override("openai_api_key"),
        "openai_api_key": setting_with_override("openai_api_key"),
        "gemini_api_key": setting_with_override("gemini_api_key"),
        "anthropic_api_key": setting_with_override("anthropic_api_key"),
        "openrouter_api_key": setting_with_override("openrouter_api_key"),
        "ollama_api_key": setting_with_override("ollama_api_key"),
        "ollama_base_url": setting_with_override("ollama_base_url", DEFAULT_OLLAMA_BASE_URL) or DEFAULT_OLLAMA_BASE_URL,
        "generation_provider": generation_provider,
        "generation_model": generation_model,
        "sidekick_provider": sidekick_provider,
        "sidekick_model": sidekick_model,
        "review_provider": review_provider,
        "review_model": review_model,
        "image_provider": (setting_with_override("ai_image_provider", "") or "").strip().lower(),
        "image_model": (setting_with_override("ai_image_model", "") or "").strip(),
        "vision_provider": vision_provider,
        "vision_model": vision_model,
        "prompt_profile": DEFAULT_PROMPT_PROFILE,
        "prompt_version": DEFAULT_PROMPT_VERSION,
        "requested_prompt_version": DEFAULT_PROMPT_VERSION,
        "ai_timeout_seconds": str(timeout),
        "ai_max_retries": str(retries),
        "timeout": timeout,
        "max_retries": retries,
    }


def ai_configured(conn: sqlite3.Connection) -> bool:
    settings = ai_settings(conn)
    providers = {
        settings["generation_provider"],
        settings["review_provider"],
        settings["sidekick_provider"],
        settings["vision_provider"],
    }
    if "openai" in providers and not settings["openai_api_key"]:
        return False
    if "gemini" in providers and not settings["gemini_api_key"]:
        return False
    if "anthropic" in providers and not settings["anthropic_api_key"]:
        return False
    if "openrouter" in providers and not settings["openrouter_api_key"]:
        return False
    if "ollama" in providers and not settings["ollama_base_url"]:
        return False
    return True
