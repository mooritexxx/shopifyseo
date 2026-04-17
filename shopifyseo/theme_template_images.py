"""Extract Shopify CDN image URLs from Online Store 2.0 JSON templates (e.g. templates/page*.json)."""

from __future__ import annotations

import json
from typing import Any

from shopifyseo.html_images import extract_shopify_images_from_html, is_shopify_hosted_image_url


def page_template_asset_keys(template_suffix: str | None) -> list[str]:
    """Candidate theme asset keys for a page, most specific first."""
    s = (template_suffix or "").strip()
    keys: list[str] = []
    if s:
        keys.append(f"templates/page.{s}.json")
    keys.append("templates/page.json")
    return keys


def extract_shopify_image_urls_from_theme_json_obj(obj: Any) -> list[str]:
    """Walk JSON (sections, blocks, settings) and collect unique Shopify-hosted image URLs."""
    out: list[str] = []
    seen: set[str] = set()

    def visit(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                visit(v)
        elif isinstance(x, list):
            for v in x:
                visit(v)
        elif isinstance(x, str):
            s = x.strip()
            if not s:
                return
            if is_shopify_hosted_image_url(s):
                if s not in seen:
                    seen.add(s)
                    out.append(s)
                return
            if "<img" in s.lower():
                for url, _alt in extract_shopify_images_from_html(s):
                    if url not in seen:
                        seen.add(url)
                        out.append(url)

    visit(obj)
    return out


def extract_shopify_image_urls_from_theme_json_text(raw: str) -> list[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return extract_shopify_image_urls_from_theme_json_obj(data)
