OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"  # Override via OLLAMA_BASE_URL env var for remote Ollama instances
DEFAULT_OLLAMA_TIMEOUT_SECONDS = 240
DEFAULT_OPENAI_MODEL = "gpt-5.4"
DEFAULT_ANTHROPIC_MODEL = "claude-3-7-sonnet-latest"
DEFAULT_OPENROUTER_MODEL = "z-ai/glm-4.5-air:free"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_PROMPT_VERSION = "v3"
DEFAULT_PROMPT_PROFILE = "ranking_aggressive"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2
DEFAULT_GENERATION_PROVIDER = "openrouter"
DEFAULT_GENERATION_MODEL = DEFAULT_OPENROUTER_MODEL
DEFAULT_REVIEW_PROVIDER = "openrouter"
DEFAULT_REVIEW_MODEL = DEFAULT_OPENROUTER_MODEL
GENERATION_MODEL = DEFAULT_GENERATION_MODEL
REVIEW_MODEL = DEFAULT_REVIEW_MODEL
MODEL_ALIASES = {
    "gpt-5.4-fast": "gpt-5.4",
    "chatgpt-5.4-fast": "gpt-5.4",
    "gpt-5.4-mini-fast": "gpt-5.4-mini",
}
PROMPT_VERSION_ALIASES = {
    "v1": "v3",
    "v2": "v3",
    "latest": "v3",
}
TITLE_LIMIT = 65
DESCRIPTION_LIMIT = 155
BODY_MIN_LENGTH = {
    "product": 1500,
    "collection": 220,
    "page": 300,
    "blog_article": 300,
}
TITLE_TARGET_MIN = {
    "product": 50,
    "collection": 45,
    "page": 45,
    "blog_article": 45,
}
DESCRIPTION_TARGET_MIN = {
    "product": 140,
    "collection": 135,
    "page": 135,
    "blog_article": 135,
}
TITLE_HARD_MIN = {
    "product": 42,
    "collection": 40,
    "page": 40,
    "blog_article": 40,
}
DESCRIPTION_HARD_MIN = {
    "product": 115,
    "collection": 110,
    "page": 110,
    "blog_article": 110,
}
QA_SCORE_FLOOR = {
    "product": 5,
    "collection": 4,
    "page": 4,
    "blog_article": 4,
}
REGENERABLE_FIELDS = ("seo_title", "seo_description", "body")

_STORE_IDENTITY_CACHE: tuple[str, str] | None = None


def get_store_identity(conn=None) -> tuple[str, str]:
    """Return (store_name, store_domain) from DB settings or env vars.

    Falls back to empty strings when unconfigured.  Result is cached for
    the lifetime of the process (settings rarely change at runtime).
    """
    global _STORE_IDENTITY_CACHE
    if _STORE_IDENTITY_CACHE is not None:
        return _STORE_IDENTITY_CACHE

    import os
    try:
        if conn is None:
            from shopifyseo.dashboard_store import db_connect
            conn = db_connect()
            _own = True
        else:
            _own = False
        try:
            from shopifyseo.dashboard_google import get_service_setting
            from shopifyseo.dashboard_queries import _base_store_url
            store_name = (get_service_setting(conn, "store_name") or "").strip()
            shop = (get_service_setting(conn, "shopify_shop") or os.getenv("SHOPIFY_SHOP", "") or "").strip()
            if not store_name and shop:
                store_name = shop.removesuffix(".myshopify.com").rstrip("/")
            base_url = _base_store_url(conn)
            if base_url:
                from urllib.parse import urlparse
                domain = urlparse(base_url).netloc or base_url.replace("https://", "").replace("http://", "").rstrip("/")
            elif shop:
                domain = shop.removesuffix(".myshopify.com").rstrip("/") if ".myshopify.com" in shop else shop.rstrip("/")
            else:
                domain = ""
        finally:
            if _own:
                conn.close()
    except Exception:
        store_name = ""
        domain = ""
    _STORE_IDENTITY_CACHE = (store_name, domain)
    return _STORE_IDENTITY_CACHE


def default_model_for_provider(provider: str) -> str:
    """Fallback model id when only a provider is known (e.g. empty sidekick model with non-default provider)."""
    p = (provider or "").strip().lower()
    if p == "gemini":
        return DEFAULT_GEMINI_MODEL
    if p == "anthropic":
        return DEFAULT_ANTHROPIC_MODEL
    if p == "openrouter":
        return DEFAULT_OPENROUTER_MODEL
    if p == "ollama":
        return "llama3.1"
    return DEFAULT_GENERATION_MODEL
