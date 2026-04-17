import os
import sqlite3

from . import dashboard_google as dg


ORIGINAL_ENV = dict(os.environ)
RUNTIME_SETTING_KEYS = (
    "store_name",
    "store_description",
    "primary_market_country",
    "dashboard_timezone",
    "store_custom_domain",
    "shopify_shop",
    "shopify_api_version",
    "shopify_client_id",
    "shopify_client_secret",
    "dataforseo_api_login",
    "dataforseo_api_password",
    "google_client_id",
    "google_client_secret",
    "search_console_site",
    "ga4_property_id",
    "openai_api_key",
    "gemini_api_key",
    "anthropic_api_key",
    "openrouter_api_key",
    "ollama_api_key",
    "ollama_base_url",
    "ai_generation_provider",
    "ai_generation_model",
    "ai_sidekick_provider",
    "ai_sidekick_model",
    "ai_review_provider",
    "ai_review_model",
    "ai_image_provider",
    "ai_image_model",
    "ai_vision_provider",
    "ai_vision_model",
    "ai_timeout_seconds",
    "ai_max_retries",
    "google_ads_developer_token",
    "google_ads_customer_id",
    "google_ads_login_customer_id",
)

_ENV_MAPPING = {
    "store_custom_domain": "SHOPIFY_STORE_URL",
    "shopify_shop": "SHOPIFY_SHOP",
    "shopify_api_version": "SHOPIFY_API_VERSION",
    "shopify_client_id": "SHOPIFY_CLIENT_ID",
    "shopify_client_secret": "SHOPIFY_CLIENT_SECRET",
    "dataforseo_api_login": "DATAFORSEO_API_LOGIN",
    "dataforseo_api_password": "DATAFORSEO_API_PASSWORD",
    "google_client_id": "GOOGLE_CLIENT_ID",
    "google_client_secret": "GOOGLE_CLIENT_SECRET",
    "openai_api_key": "OPENAI_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "ollama_api_key": "OLLAMA_API_KEY",
    "ollama_base_url": "OLLAMA_BASE_URL",
    "ai_generation_provider": "AI_GENERATION_PROVIDER",
    "ai_generation_model": "AI_GENERATION_MODEL",
    "ai_sidekick_provider": "AI_SIDEKICK_PROVIDER",
    "ai_sidekick_model": "AI_SIDEKICK_MODEL",
    "ai_review_provider": "AI_REVIEW_PROVIDER",
    "ai_review_model": "AI_REVIEW_MODEL",
    "ai_image_provider": "AI_IMAGE_PROVIDER",
    "ai_image_model": "AI_IMAGE_MODEL",
    "ai_vision_provider": "AI_VISION_PROVIDER",
    "ai_vision_model": "AI_VISION_MODEL",
    "ai_timeout_seconds": "AI_TIMEOUT_SECONDS",
    "ai_max_retries": "AI_MAX_RETRIES",
    "google_ads_developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
    "google_ads_customer_id": "GOOGLE_ADS_CUSTOMER_ID",
    "google_ads_login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
}


def apply_runtime_settings(conn: sqlite3.Connection) -> None:
    """Mirror mapped credentials from SQLite into ``os.environ`` and refresh Google globals.

    Non-empty values saved in Settings win over process environment so operators are not required
    to use ``.env`` for Shopify, Google OAuth, AI keys, etc. Empty values in the DB do not clear
    ``os.environ`` (so optional operator-only env overrides remain possible when a field was never
    filled in the UI).
    """
    for setting_key, env_key in _ENV_MAPPING.items():
        value = (dg.get_service_setting(conn, setting_key) or "").strip()
        if value:
            os.environ[env_key] = value
    dg.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    dg.GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()


def mask_secret(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def runtime_setting(conn: sqlite3.Connection, env_key: str, setting_key: str) -> tuple[str, str]:
    """Resolve a setting: values saved in Settings (SQLite) take precedence over startup ``os.environ``."""
    db_value = (dg.get_service_setting(conn, setting_key) or "").strip()
    if db_value:
        return db_value, "db"
    env_value = ORIGINAL_ENV.get(env_key, "").strip()
    if env_value:
        return env_value, "env"
    return "", "unset"
