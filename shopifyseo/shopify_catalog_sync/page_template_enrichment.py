"""After page sync, pull image URLs from Online Store page JSON templates (main theme)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from shopifyseo.dashboard_http import HttpRequestError
from shopifyseo.shopify_theme_assets import fetch_main_theme_id
from shopifyseo.theme_template_images import collect_template_image_urls_for_pages


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

    pages_payload = [{"id": r["shopify_id"], "templateSuffix": (r["suf"] or "")} for r in rows]
    by_id = collect_template_image_urls_for_pages(pages_payload, theme_id=theme_id)

    updated = 0
    for row in rows:
        sid = row["shopify_id"]
        urls = list(by_id.get(str(sid), []) or [])
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
