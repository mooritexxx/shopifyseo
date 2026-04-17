from __future__ import annotations

import logging
import sqlite3
from typing import Any

import shopifyseo.dashboard_ai as dai
import shopifyseo.dashboard_google as dg
from backend.app.db import open_db_connection
from shopifyseo.dashboard_ai_engine_parts.config import (
    ANTHROPIC_API_URL,
    DEFAULT_OLLAMA_BASE_URL,
    GEMINI_API_URL,
    OPENROUTER_MODELS_URL,
)
from shopifyseo.dashboard_config import RUNTIME_SETTING_KEYS, _ENV_MAPPING, apply_runtime_settings, runtime_setting
from shopifyseo.dashboard_http import request_json

logger = logging.getLogger(__name__)


def _shopify_runtime_ready(conn: sqlite3.Connection) -> bool:
    """True when Admin API credentials and shop hostname are available (DB or env)."""
    shop, _ = runtime_setting(conn, "SHOPIFY_SHOP", "shopify_shop")
    cid, _ = runtime_setting(conn, "SHOPIFY_CLIENT_ID", "shopify_client_id")
    csec, _ = runtime_setting(conn, "SHOPIFY_CLIENT_SECRET", "shopify_client_secret")
    return bool(shop.strip() and cid.strip() and csec.strip())


def get_sync_scope_readiness(conn: sqlite3.Connection) -> dict[str, bool]:
    """Which sync pipeline steps can run given current credentials and OAuth state."""
    shopify_ok = _shopify_runtime_ready(conn)
    google_ok = dg.google_configured() and bool(dg.get_service_token(conn, "search_console"))
    return {
        "shopify": shopify_ok,
        "gsc": google_ok,
        "ga4": google_ok,
        "index": google_ok,
        "pagespeed": google_ok,
        "structured": shopify_ok,
    }


_GRANULAR_SHOPIFY_SCOPES = frozenset({"products", "collections", "pages", "blogs"})
_PIPELINE_SCOPES = frozenset({"shopify", "gsc", "ga4", "index", "pagespeed", "structured"})


def filter_normalized_scopes_for_readiness(normalized_scopes: list[str], readiness: dict[str, bool]) -> list[str]:
    """Drop sync steps the app cannot run yet (missing Shopify or Google OAuth)."""
    out: list[str] = []
    for s in normalized_scopes:
        if s in _GRANULAR_SHOPIFY_SCOPES:
            if readiness.get("shopify"):
                out.append(s)
        elif s in _PIPELINE_SCOPES:
            if readiness.get(s, False):
                out.append(s)
        else:
            out.append(s)
    return out


def get_settings_data() -> dict[str, Any]:
    conn = open_db_connection()
    try:
        values = {key: dg.get_service_setting(conn, key) for key in RUNTIME_SETTING_KEYS}
        for setting_key, env_key in _ENV_MAPPING.items():
            values[setting_key] = runtime_setting(conn, env_key, setting_key)[0]
        configured = dg.google_configured()
        connected = bool(dg.get_service_token(conn, "search_console"))
        available_gsc_sites: list[str] = []
        available_ga4_properties: list[dict] = []
        available_google_ads_customers: list[dict] = []
        ga4_api_activation_url = ""
        if configured and connected:
            try:
                sites = dg.get_search_console_sites(conn)
                available_gsc_sites = [s["siteUrl"] for s in sites]
            except Exception:
                logger.warning("Failed to fetch Search Console sites", exc_info=True)
            try:
                ga4_result = dg.get_ga4_properties(conn)
                available_ga4_properties = ga4_result.get("properties", [])
                ga4_api_activation_url = ga4_result.get("activation_url", "")
            except Exception:
                logger.warning("Failed to fetch GA4 properties", exc_info=True)
            dev_ads = runtime_setting(conn, "GOOGLE_ADS_DEVELOPER_TOKEN", "google_ads_developer_token")[0]
            if (dev_ads or "").strip():
                try:
                    available_google_ads_customers = dg.list_google_ads_accessible_customers(conn)
                except Exception:
                    logger.warning("Failed to list Google Ads customers", exc_info=True)
        sync_scope_ready = get_sync_scope_readiness(conn)
        return {
            "values": values,
            "google_configured": configured,
            "google_connected": connected,
            "ai_configured": dai.ai_configured(conn),
            "auth_url": "/auth/google/start" if configured else None,
            "available_gsc_sites": available_gsc_sites,
            "available_ga4_properties": available_ga4_properties,
            "available_google_ads_customers": available_google_ads_customers,
            "ga4_api_activation_url": ga4_api_activation_url,
            "sync_scope_ready": sync_scope_ready,
        }
    finally:
        conn.close()


def save_settings(payload: dict[str, str]) -> str:
    conn = open_db_connection()
    try:
        migrated_payload = dict(payload)
        for key in RUNTIME_SETTING_KEYS:
            if key in migrated_payload:
                dg.set_service_setting(conn, key, migrated_payload[key].strip())
        # Clear legacy settings (old openai_* names are deprecated)
        dg.set_service_setting(conn, "openai_timeout_seconds", "")
        dg.set_service_setting(conn, "openai_model", "")
        dg.set_service_setting(conn, "openai_max_retries", "")
        dg.set_service_setting(conn, "openai_prompt_profile", "")
        dg.set_service_setting(conn, "openai_prompt_version", "")
        dg.clear_google_caches(conn)
        apply_runtime_settings(conn)
        # Reset cached store identity so new store_name takes effect immediately
        import shopifyseo.dashboard_ai_engine_parts.config as _ai_cfg
        _ai_cfg._STORE_IDENTITY_CACHE = None
        try:
            import shopifyseo.market_context as _mc
            _mc._PRIMARY_MARKET_CACHE = None
        except Exception:
            pass
        # Reset cached store base URL so custom domain changes take effect
        try:
            import shopifyseo.dashboard_queries as _dq
            _dq._BASE_URL_CACHE = None
        except Exception:
            pass
        return "Settings saved"
    finally:
        conn.close()


def test_ai_connection(settings_override: dict[str, str] | None = None, target: str = "generation") -> dict[str, Any]:
    conn = open_db_connection()
    try:
        return dai.test_connection(conn, settings_override=settings_override, target=target)
    finally:
        conn.close()


def test_image_model(settings_override: dict[str, str] | None = None) -> dict[str, Any]:
    conn = open_db_connection()
    try:
        return dai.test_image_model(conn, settings_override=settings_override)
    finally:
        conn.close()


def test_vision_model(settings_override: dict[str, str] | None = None) -> dict[str, Any]:
    conn = open_db_connection()
    try:
        return dai.test_vision_model(conn, settings_override=settings_override)
    finally:
        conn.close()


def test_google_ads_connection(override_token: str | None = None) -> dict[str, Any]:
    conn = open_db_connection()
    try:
        if override_token is not None and override_token.strip():
            dev_tok = override_token.strip()
        else:
            dev_tok, _src = runtime_setting(conn, "GOOGLE_ADS_DEVELOPER_TOKEN", "google_ads_developer_token")
        return dg.test_google_ads_api(conn, dev_tok)
    finally:
        conn.close()


def test_shopify_admin_connection(overrides: dict[str, str] | None = None) -> dict[str, Any]:
    from shopifyseo.shopify_admin import probe_shopify_admin_with_credentials

    o = {k: (v if isinstance(v, str) else "") for k, v in (overrides or {}).items()}
    conn = open_db_connection()
    try:
        shop = (o.get("shopify_shop") or "").strip()
        cid = (o.get("shopify_client_id") or "").strip()
        csec = (o.get("shopify_client_secret") or "").strip()
        ver = (o.get("shopify_api_version") or "").strip()
        if not shop:
            shop, _ = runtime_setting(conn, "SHOPIFY_SHOP", "shopify_shop")
        if not cid:
            cid, _ = runtime_setting(conn, "SHOPIFY_CLIENT_ID", "shopify_client_id")
        if not csec:
            csec, _ = runtime_setting(conn, "SHOPIFY_CLIENT_SECRET", "shopify_client_secret")
        if not ver:
            ver, _ = runtime_setting(conn, "SHOPIFY_API_VERSION", "shopify_api_version")
    finally:
        conn.close()
    return probe_shopify_admin_with_credentials(shop, cid, csec, ver)


def get_shopify_shop_info() -> dict[str, Any]:
    """Fetch live shop name + description from Shopify using saved credentials.

    Used by the Settings UI to show Shopify values as hints/prefill alongside the
    stored `store_name` / `store_description` override fields. Returns an error
    payload instead of raising so the UI can degrade gracefully.
    """
    from shopifyseo.shopify_admin import probe_shopify_admin_with_credentials

    conn = open_db_connection()
    try:
        if not _shopify_runtime_ready(conn):
            return {"available": False, "shop_name": "", "shop_description": "", "shop_domain": "", "error": "Shopify not configured"}
        shop, _ = runtime_setting(conn, "SHOPIFY_SHOP", "shopify_shop")
        cid, _ = runtime_setting(conn, "SHOPIFY_CLIENT_ID", "shopify_client_id")
        csec, _ = runtime_setting(conn, "SHOPIFY_CLIENT_SECRET", "shopify_client_secret")
        ver, _ = runtime_setting(conn, "SHOPIFY_API_VERSION", "shopify_api_version")
    finally:
        conn.close()
    try:
        result = probe_shopify_admin_with_credentials(shop, cid, csec, ver)
    except Exception as exc:
        logger.warning("Failed to fetch Shopify shop info", exc_info=True)
        return {"available": False, "shop_name": "", "shop_description": "", "shop_domain": "", "error": str(exc)}
    return {
        "available": True,
        "shop_name": (result.get("shop_name") or "").strip(),
        "shop_description": (result.get("shop_description") or "").strip(),
        "shop_domain": (result.get("shop_domain") or "").strip(),
        "error": "",
    }


def get_ollama_models(ollama_base_url: str = "", ollama_api_key: str = "") -> dict[str, Any]:
    base_url = (ollama_base_url or "").strip() or DEFAULT_OLLAMA_BASE_URL
    api_key = (ollama_api_key or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    payload = request_json(
        f"{base_url.rstrip('/')}/api/tags",
        headers=headers,
        timeout=20,
    )
    seen: set[str] = set()
    models: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if name and name not in seen:
            seen.add(name)
            models.append(name)
    return {"models": models}


def get_anthropic_models(anthropic_api_key: str = "") -> dict[str, Any]:
    api_key = (anthropic_api_key or "").strip()
    if not api_key:
        return {"models": []}
    payload = request_json(
        ANTHROPIC_API_URL.replace("/messages", "/models"),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=20,
    )
    seen: set[str] = set()
    models: list[str] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or "").strip()
        if name and name not in seen:
            seen.add(name)
            models.append(name)
    return {"models": models}


def get_gemini_models(gemini_api_key: str = "") -> dict[str, Any]:
    api_key = (gemini_api_key or "").strip()
    if not api_key:
        return {"models": []}
    payload = request_json(
        f"{GEMINI_API_URL}/models",
        headers={
            "x-goog-api-key": api_key,
        },
        timeout=20,
    )
    seen: set[str] = set()
    models: list[str] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        name = raw_name.removeprefix("models/")
        supported_methods = item.get("supportedGenerationMethods") or []
        if name and "generateContent" in supported_methods and name not in seen:
            seen.add(name)
            models.append(name)
    return {"models": models}


def get_openrouter_models(openrouter_api_key: str = "") -> dict[str, Any]:
    api_key = (openrouter_api_key or "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    payload = request_json(
        OPENROUTER_MODELS_URL,
        headers=headers,
        timeout=20,
    )
    seen: set[str] = set()
    models: list[str] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or "").strip()
        if name and name not in seen:
            seen.add(name)
            models.append(name)
    return {"models": models}
