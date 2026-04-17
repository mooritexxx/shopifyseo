from __future__ import annotations

import logging
from typing import Any

import shopifyseo.dashboard_google as dg
from backend.app.db import open_db_connection
from shopifyseo.dashboard_config import runtime_setting
from shopifyseo.dashboard_http import HttpRequestError

logger = logging.getLogger(__name__)

# Shown on the Google Ads lab page — aligns with:
# https://developers.google.com/google-ads/api/docs/api-policy/access-levels
# https://developers.google.com/google-ads/api/samples/generate-keyword-ideas
GOOGLE_ADS_LAB_HINTS = [
    "Developer token: GOOGLE_ADS_DEVELOPER_TOKEN (environment) overrides Settings (database) when set.",
    "Keyword Planner (KeywordPlanIdeaService) uses the same OAuth token and developer token as Settings; "
    "the REST path is POST …/customers/{customerId}:generateKeywordIdeas per Google’s docs.",
    "If your saved customer ID is a manager (MCC), the API must use a client account ID in the path and "
    "login-customer-id = the manager — the lab resolves this automatically when possible.",
]

KEYWORD_PLANNING_RPCS = frozenset(
    {
        "generateKeywordIdeas",
        "generateKeywordHistoricalMetrics",
        "generateKeywordForecastMetrics",
        "generateAdGroupThemes",
    }
)


def get_google_ads_lab_context() -> dict[str, Any]:
    conn = open_db_connection()
    try:
        configured = dg.google_configured()
        connected = bool(dg.get_service_token(conn, "search_console"))
        cust, _ = runtime_setting(conn, "GOOGLE_ADS_CUSTOMER_ID", "google_ads_customer_id")
        login_def, _ = runtime_setting(conn, "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "google_ads_login_customer_id")
        dev, dev_src = runtime_setting(conn, "GOOGLE_ADS_DEVELOPER_TOKEN", "google_ads_developer_token")
        return {
            "google_configured": configured,
            "google_connected": connected,
            "customer_id": (cust or "").strip(),
            "login_customer_id_default": (login_def or "").strip(),
            "developer_token_configured": bool((dev or "").strip()),
            "developer_token_source": dev_src,
            "lab_hints": list(GOOGLE_ADS_LAB_HINTS),
        }
    finally:
        conn.close()


def invoke_keyword_planning_rpc(
    *,
    rpc_method: str,
    body: dict[str, Any],
    customer_id: str | None = None,
    login_customer_id: str | None = None,
) -> dict[str, Any]:
    if rpc_method not in KEYWORD_PLANNING_RPCS:
        raise ValueError(f"Unsupported rpc_method: {rpc_method}")
    conn = open_db_connection()
    try:
        cid = (customer_id or "").strip()
        if not cid:
            cid, _ = runtime_setting(conn, "GOOGLE_ADS_CUSTOMER_ID", "google_ads_customer_id")
            cid = cid.strip()
        if not cid:
            raise RuntimeError("No Google Ads customer ID. Set it in Settings → Data Sources → Google Ads.")
        dev_tok, _ = runtime_setting(conn, "GOOGLE_ADS_DEVELOPER_TOKEN", "google_ads_developer_token")
        dev_tok = dev_tok.strip()
        if not dev_tok:
            raise RuntimeError("No Google Ads developer token. Add it in Settings.")
        if not dg.google_configured() or not dg.get_service_token(conn, "search_console"):
            raise RuntimeError("Google OAuth is not connected. Connect Google in Settings first.")
        access_token = dg.get_google_access_token(conn)
        login = (login_customer_id or "").strip() or None
        if not login:
            login = (runtime_setting(conn, "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "google_ads_login_customer_id")[0] or "").strip() or None

        url_cid, login_header, note = dg.resolve_keyword_planning_customer(
            cid,
            access_token=access_token,
            developer_token=dev_tok,
            login_customer_id=login,
        )
        raw = dg.google_ads_keyword_plan_idea_post(
            url_cid,
            rpc_method,
            body,
            access_token=access_token,
            developer_token=dev_tok,
            login_customer_id=login_header,
        )
        return {
            "result": raw,
            "planning": {
                "url_customer_id": url_cid,
                "login_customer_id": login_header or "",
                "note": note or "",
            },
        }
    except HttpRequestError as exc:
        detail = (exc.body or str(exc))[:4000]
        logger.warning("Google Ads API error %s: %s", exc.status, detail[:500])
        raise RuntimeError(f"Google Ads API HTTP {exc.status}: {detail}") from exc
    finally:
        conn.close()
