import os
import sys
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from shopifyseo.dashboard_google._auth import get_google_access_token, google_token_has_scope
from shopifyseo.dashboard_http import request_json
from shopifyseo.db import get_connection


def _pagespeed_url(page_url: str) -> str:
    params = {"url": page_url, "strategy": "mobile"}
    api_key = (os.getenv("PAGESPEED_API_KEY") or "").strip()
    if api_key:
        params["key"] = api_key
    return "https://pagespeedonline.googleapis.com/pagespeedonline/v5/runPagespeed?" + urlencode(params)


def _pagespeed_get(api_url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return request_json(api_url, headers=headers, method="GET", timeout=120)


conn = get_connection(path="/Users/home/Projects/shopifyseo/shopify_catalog.sqlite3")
try:
    token = ""
    if google_token_has_scope(conn, "openid"):
        try:
            token = get_google_access_token(conn)
        except Exception as e:
            print("OAuth token unavailable, using public quota:", type(e).__name__)
    url_v = _pagespeed_url("https://vapely.ca/")
    print("Vapely:")
    try:
        _pagespeed_get(url_v, token)
        print("Vapely Success")
    except Exception as e:
        print("Vapely Error:", type(e), getattr(e, "status", None))
except Exception as e:
    print("Token Error:", type(e).__name__)
