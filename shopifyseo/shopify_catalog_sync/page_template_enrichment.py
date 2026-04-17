"""After page sync, pull image URLs from Online Store page JSON templates (main theme)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from shopifyseo.dashboard_http import HttpRequestError
from shopifyseo.shopify_theme_assets import fetch_main_theme_id, fetch_theme_asset_text
from shopifyseo.theme_template_images import (
    extract_shopify_image_urls_from_theme_json_text,
    page_template_asset_keys,
)


def enrich_pages_template_images(conn: sqlite3.Connection) -> dict[str, Any]:
    """Parse templates/page*.json for each page's templateSuffix; store URLs in template_images_json.

    Requires Shopify Admin **read_themes** (REST). If the API is unavailable, returns ok=False and skips updates.
    """
    try:
        theme_id = fetch_main_theme_id()
    except HttpRequestError as exc:
        return {
            "ok": False,
            "message": f"Themes API unavailable ({exc}). Ensure the custom app has read_themes scope.",
            "pages_updated": 0,
        }
    if theme_id is None:
        return {"ok": False, "message": "No main theme found.", "pages_updated": 0}

    rows = conn.execute("SELECT shopify_id, COALESCE(template_suffix, '') AS suf FROM pages").fetchall()
    if not rows:
        return {"ok": True, "message": "No pages.", "pages_updated": 0}

    # Cache: theme asset key -> list of image URLs (parsed once per template file)
    parsed_by_key: dict[str, list[str]] = {}

    def urls_for_suffix(suffix: str) -> list[str]:
        for key in page_template_asset_keys(suffix):
            if key not in parsed_by_key:
                try:
                    raw = fetch_theme_asset_text(theme_id, key)
                except HttpRequestError:
                    parsed_by_key[key] = []
                    continue
                if not raw:
                    parsed_by_key[key] = []
                else:
                    parsed_by_key[key] = extract_shopify_image_urls_from_theme_json_text(raw)
            if parsed_by_key[key]:
                return list(parsed_by_key[key])
        return []

    updated = 0
    for row in rows:
        sid = row["shopify_id"]
        suffix = (row["suf"] or "") if row["suf"] is not None else ""
        urls = urls_for_suffix(suffix)
        conn.execute(
            "UPDATE pages SET template_images_json = ? WHERE shopify_id = ?",
            (json.dumps(urls), sid),
        )
        updated += 1
    return {
        "ok": True,
        "message": f"Updated template images for {updated} page(s) from theme {theme_id}.",
        "pages_updated": updated,
    }
