from urllib.parse import urlencode, quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

import shopifyseo.dashboard_google as dg

from backend.app.db import open_db_connection
from shopifyseo.dashboard_config import apply_runtime_settings


router = APIRouter(tags=["auth"])


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("google_auth_callback"))


@router.get("/auth/google/start", name="google_auth_start")
def google_auth_start(request: Request):
    conn = open_db_connection()
    try:
        apply_runtime_settings(conn)
    finally:
        conn.close()
    if not dg.google_configured():
        return RedirectResponse(url="/app/settings?tab=data-sources&message=Google+OAuth+is+not+configured", status_code=303)
    dg.GOOGLE_REDIRECT_URI = _redirect_uri(request)
    dg.new_oauth_state()
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(
            {
                "client_id": dg.GOOGLE_CLIENT_ID,
                "redirect_uri": dg.GOOGLE_REDIRECT_URI,
                "response_type": "code",
                "scope": dg.GOOGLE_SCOPES,
                "access_type": "offline",
                "prompt": "consent",
                "state": dg.GOOGLE_AUTH_STATE["value"],
            }
        )
    )
    return RedirectResponse(url=auth_url, status_code=303)


@router.get("/auth/google/callback", name="google_auth_callback")
def google_auth_callback(request: Request, state: str = "", code: str = ""):
    if state != dg.GOOGLE_AUTH_STATE["value"]:
        return RedirectResponse(url="/app/settings?tab=data-sources&message=Google+OAuth+state+mismatch", status_code=303)
    if not code:
        return RedirectResponse(url="/app/settings?tab=data-sources&message=Missing+Google+OAuth+code", status_code=303)
    dg.GOOGLE_REDIRECT_URI = _redirect_uri(request)
    conn = open_db_connection()
    try:
        apply_runtime_settings(conn)
        payload = dg.google_exchange_code(code)
        dg.set_service_token(conn, "search_console", payload)
        return RedirectResponse(url="/app/settings?tab=data-sources&message=Google+Search+Console+connected", status_code=303)
    except Exception as exc:
        return RedirectResponse(url=f"/app/settings?tab=data-sources&message=Google+OAuth+failed:+{quote(str(exc))}", status_code=303)
    finally:
        conn.close()
