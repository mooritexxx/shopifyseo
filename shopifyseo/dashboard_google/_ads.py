"""Google Ads API integration (KeywordPlanIdeaService, accessible customers)."""

import json
import sys
from typing import Any

from ..dashboard_http import HttpRequestError, request_json
from ._auth import (
    get_google_access_token,
    get_service_token,
    google_api_get,
    google_configured,
)


def _ads_developer_token(conn) -> str:
    """Same resolution as Settings: env ``GOOGLE_ADS_DEVELOPER_TOKEN`` overrides DB."""
    from shopifyseo.dashboard_config import runtime_setting

    val, _ = runtime_setting(conn, "GOOGLE_ADS_DEVELOPER_TOKEN", "google_ads_developer_token")
    return (val or "").strip()


def _pkg():
    """Return the shopifyseo.dashboard_google package namespace."""
    return sys.modules["shopifyseo.dashboard_google"]


def test_google_ads_api(conn, developer_token: str) -> dict[str, Any]:
    """Call Google Ads ``customers:listAccessibleCustomers`` using OAuth + developer token."""
    token = (developer_token or "").strip()
    if not token:
        raise RuntimeError("Google Ads developer token is empty. Enter it below, save settings, then try again.")
    if not google_configured():
        raise RuntimeError("Configure Google OAuth Client ID and Secret in Settings first.")
    if not get_service_token(conn, "search_console"):
        raise RuntimeError("Google is not connected. Use Connect Google on this tab first.")
    access_token = get_google_access_token(conn)
    ads_version = _pkg().GOOGLE_ADS_API_VERSION
    url = f"https://googleads.googleapis.com/{ads_version}/customers:listAccessibleCustomers"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": token,
        "Content-Type": "application/json",
    }
    try:
        payload = request_json(url, method="GET", headers=headers, timeout=30)
    except HttpRequestError as exc:
        detail = (exc.body or str(exc))[:800]
        if exc.status == 401:
            raise RuntimeError(
                "Google Ads API returned 401. Verify the developer token in Google Ads API Center "
                "and that your Google user can access Ads accounts."
            ) from exc
        if exc.status == 403:
            try:
                err_json = json.loads(exc.body or "{}")
                msg = (err_json.get("error") or {}).get("message") or detail
            except Exception:
                msg = detail
            raise RuntimeError(
                f"Google Ads API denied the request: {msg} "
                "If this mentions permission or OAuth, enable the Google Ads API for your Google Cloud project, "
                "add scope https://www.googleapis.com/auth/adwords to your OAuth consent screen (or sensitive scopes), "
                "then use Reconnect Google and approve access."
            ) from exc
        raise RuntimeError(f"Google Ads API error ({exc.status}): {detail}") from exc
    resource_names = payload.get("resourceNames") or []
    return {
        "ok": True,
        "accessible_customer_count": len(resource_names),
        "sample_resource_names": resource_names[:10],
    }


def _google_ads_customer_descriptive_name(
    access_token: str, developer_token: str, customer_id: str
) -> str:
    ads_version = _pkg().GOOGLE_ADS_API_VERSION
    url = f"https://googleads.googleapis.com/{ads_version}/customers/{customer_id}/googleAds:search"
    try:
        payload = request_json(
            url,
            method="POST",
            headers={
                "Authorization": f"Bearer {access_token}",
                "developer-token": developer_token,
                "Content-Type": "application/json",
            },
            payload={"query": "SELECT customer.id, customer.descriptive_name FROM customer"},
            timeout=15,
        )
    except HttpRequestError:
        return ""
    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue
        cust = row.get("customer")
        if isinstance(cust, dict):
            name = (cust.get("descriptiveName") or cust.get("descriptive_name") or "").strip()
            if name:
                return name
    return ""


def list_google_ads_accessible_customers(conn) -> list[dict[str, str]]:
    """Customers returned by listAccessibleCustomers, optionally with descriptive names from Search."""
    dev_tok = _ads_developer_token(conn)
    if not dev_tok:
        return []
    if not google_configured() or not get_service_token(conn, "search_console"):
        return []
    try:
        access_token = get_google_access_token(conn)
    except Exception:
        return []
    ads_version = _pkg().GOOGLE_ADS_API_VERSION
    url = f"https://googleads.googleapis.com/{ads_version}/customers:listAccessibleCustomers"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": dev_tok,
        "Content-Type": "application/json",
    }
    try:
        payload = request_json(url, method="GET", headers=headers, timeout=30)
    except HttpRequestError:
        return []
    rows: list[dict[str, str]] = []
    for rn in payload.get("resourceNames") or []:
        if not isinstance(rn, str) or not rn.startswith("customers/"):
            continue
        cid = rn.removeprefix("customers/")
        if not cid.isdigit():
            continue
        rows.append({"customer_id": cid, "resource_name": rn, "descriptive_name": ""})
    for row in rows:
        row["descriptive_name"] = _google_ads_customer_descriptive_name(
            access_token, dev_tok, row["customer_id"]
        )
    rows.sort(key=lambda r: ((r.get("descriptive_name") or r["customer_id"]).lower(), r["customer_id"]))
    return rows


def _digits_only(s: str) -> str:
    return "".join(ch for ch in (s or "").strip() if ch.isdigit())


def _resource_to_customer_id(resource_name: str) -> str:
    if not isinstance(resource_name, str) or not resource_name.startswith("customers/"):
        return ""
    return _digits_only(resource_name.removeprefix("customers/"))


def resolve_keyword_planning_customer(
    customer_id: str,
    *,
    access_token: str,
    developer_token: str,
    login_customer_id: str | None,
) -> tuple[str, str | None, str | None]:
    """
    ``KeywordPlanIdeaService`` REST calls must use a **client** customer ID in the URL
    and ``login-customer-id`` = the **manager** when the account in Settings is an MCC.
    Google’s curl sample uses ``CUSTOMER_ID`` in the path and ``login-customer-id`` for
    the manager when acting through a hierarchy.

    Returns ``(url_customer_id, login_header_id, resolution_note)``.
    """
    cid = _digits_only(customer_id)
    if not cid:
        raise RuntimeError("Invalid customer_id")
    login = _digits_only(login_customer_id) if login_customer_id else None
    if login == "":
        login = None

    def _search(url_cid: str, query: str, *, login_hdr: str | None) -> dict[str, Any]:
        return google_ads_search(
            url_cid,
            {"query": query},
            access_token=access_token,
            developer_token=developer_token,
            login_customer_id=login_hdr,
        )

    # 1) Leaf client under MCC: customer.manager is false — use URL + optional login as-is.
    q_mgr = "SELECT customer.id, customer.manager FROM customer LIMIT 1"
    try:
        payload = _search(cid, q_mgr, login_hdr=login)
    except HttpRequestError:
        payload = None
    if not payload or not (payload.get("results") or []):
        if login:
            try:
                payload = _search(cid, q_mgr, login_hdr=None)
            except HttpRequestError:
                payload = None
    if not payload or not (payload.get("results") or []):
        raise RuntimeError(
            "Could not read customer metadata (customer.manager). "
            "If this account is a client under an MCC, set Google Ads login customer ID to the manager in Settings."
        )
    row0 = (payload.get("results") or [])[0]
    if not isinstance(row0, dict):
        raise RuntimeError("Unexpected Search response shape.")
    cust = row0.get("customer") or {}
    is_manager = bool(cust.get("manager")) if isinstance(cust, dict) else False

    if not is_manager:
        note = None
        if login:
            note = (
                f"Using customer ID {cid} in the request path with login-customer-id {login} "
                "(client under MCC)."
            )
        return (cid, login, note)

    # 2) Saved customer is a manager: pick a leaf client for KeywordPlanIdeaService URL.
    q_clients = """
SELECT customer_client.client_customer, customer_client.manager, customer_client.hidden
FROM customer_client
WHERE customer_client.hidden = FALSE AND customer_client.manager = FALSE
LIMIT 10
""".strip()
    try:
        cc_payload = _search(cid, q_clients, login_hdr=None)
    except HttpRequestError as exc:
        raise RuntimeError(
            "Could not list client accounts under this manager. "
            "Keyword Planner requires a client account ID in the API path; "
            "ensure your Google Ads user can access this manager account."
        ) from exc

    def _leaf_from_cc_payload(payload: dict[str, Any]) -> str:
        for row in payload.get("results") or []:
            if not isinstance(row, dict):
                continue
            cc = row.get("customerClient") or row.get("customer_client") or {}
            if not isinstance(cc, dict):
                continue
            if cc.get("manager") is True:
                continue
            rn = cc.get("clientCustomer") or cc.get("client_customer") or ""
            lid = _resource_to_customer_id(rn)
            if lid:
                return lid
        return ""

    leaf_id = _leaf_from_cc_payload(cc_payload)
    if not leaf_id:
        q_broad = """
SELECT customer_client.client_customer, customer_client.manager, customer_client.hidden
FROM customer_client
WHERE customer_client.hidden = FALSE
LIMIT 20
""".strip()
        try:
            broad = _search(cid, q_broad, login_hdr=None)
            leaf_id = _leaf_from_cc_payload(broad)
        except HttpRequestError:
            leaf_id = ""
    if not leaf_id:
        raise RuntimeError(
            "Your saved Google Ads customer ID is a manager (MCC) account. "
            "Keyword Planner API calls must use a **client** account ID in the path "
            "and ``login-customer-id`` = this manager. "
            "No non-manager client accounts were found under this manager — link a "
            "client account in Google Ads or set Customer ID to a client account "
            "and put the manager ID in login customer ID."
        )
    note = (
        f"Settings customer {cid} is a manager account. Keyword Planner uses client "
        f"{leaf_id} in the URL with login-customer-id {cid}."
    )
    return (leaf_id, cid, note)


def google_ads_keyword_plan_idea_post(
    customer_id: str,
    rpc_method: str,
    body: dict[str, Any],
    *,
    access_token: str,
    developer_token: str,
    login_customer_id: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """POST to ``customers/{id}:{rpc_method}`` (KeywordPlanIdeaService). JSON uses camelCase."""
    cid = _digits_only(customer_id)
    if not cid:
        raise RuntimeError("Invalid customer_id")
    ads_version = _pkg().GOOGLE_ADS_API_VERSION
    url = f"https://googleads.googleapis.com/{ads_version}/customers/{cid}:{rpc_method}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token.strip(),
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = _digits_only(login_customer_id)
    return request_json(url, method="POST", headers=headers, payload=body, timeout=timeout)


def google_ads_search(
    customer_id: str,
    body: dict[str, Any],
    *,
    access_token: str,
    developer_token: str,
    login_customer_id: str | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    """POST ``customers/{id}/googleAds:search`` — reporting / read path (Explorer allows vs Keyword Planning)."""
    cid = _digits_only(customer_id)
    if not cid:
        raise RuntimeError("Invalid customer_id")
    ads_version = _pkg().GOOGLE_ADS_API_VERSION
    url = f"https://googleads.googleapis.com/{ads_version}/customers/{cid}/googleAds:search"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token.strip(),
        "Content-Type": "application/json",
    }
    if login_customer_id:
        headers["login-customer-id"] = _digits_only(login_customer_id)
    return request_json(url, method="POST", headers=headers, payload=body, timeout=timeout)
