"""Read Online Store theme files via Admin REST (requires read_themes scope)."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlencode

from shopifyseo.dashboard_http import HttpRequestError, request_json
from shopifyseo.shopify_admin import DEFAULT_API_VERSION, env, normalize_shop_domain, token_request


def _admin_rest_get(path_query: str) -> dict[str, Any]:
    shop = normalize_shop_domain(env("SHOPIFY_SHOP"))
    tok = token_request()
    version = env("SHOPIFY_API_VERSION", required=False, default=DEFAULT_API_VERSION)
    if not path_query.startswith("/"):
        path_query = "/" + path_query
    url = f"https://{shop}/admin/api/{version}{path_query}"
    return request_json(url, method="GET", headers={"X-Shopify-Access-Token": tok})


def fetch_main_theme_id() -> int | None:
    """Return numeric theme id for the main (live) theme, or None if list is empty."""
    data = _admin_rest_get("/themes.json")
    for t in data.get("themes") or []:
        if (t.get("role") or "").lower() == "main":
            tid = t.get("id")
            if tid is not None:
                try:
                    return int(tid)
                except (TypeError, ValueError):
                    return None
    return None


def fetch_theme_asset_text(theme_id: int, asset_key: str) -> str | None:
    """Return UTF-8 text for a theme asset, or None if missing / unreadable."""
    qs = urlencode({"asset[key]": asset_key})
    try:
        data = _admin_rest_get(f"/themes/{theme_id}/assets.json?{qs}")
    except HttpRequestError as exc:
        if exc.status == 404:
            return None
        raise
    asset = data.get("asset") or {}
    val = asset.get("value")
    if isinstance(val, str) and val.strip():
        return val
    att = asset.get("attachment")
    if isinstance(att, str) and att.strip():
        try:
            return base64.b64decode(att).decode("utf-8", errors="replace")
        except (ValueError, UnicodeError):
            return None
    return None
