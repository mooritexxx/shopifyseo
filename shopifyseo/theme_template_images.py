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


def collect_template_image_urls_for_pages(
    pages: list[dict[str, Any]],
    *,
    theme_id: int | None = None,
) -> dict[str, list[str]]:
    """Map page Shopify id -> image URLs from Online Store 2.0 JSON templates.

    Uses the same asset keys and parsing as :func:`enrich_pages_template_images`.
    Requires Admin **read_themes** (REST). Returns an empty dict if the themes API fails
    or no main theme exists (discovery counts then match post-sync only when enrich also skips).
    """
    from shopifyseo.dashboard_http import HttpRequestError
    from shopifyseo.shopify_theme_assets import fetch_main_theme_id, fetch_theme_asset_text

    if not pages:
        return {}

    tid = theme_id
    if tid is None:
        try:
            tid = fetch_main_theme_id()
        except HttpRequestError:
            return {}
    if tid is None:
        return {}
    parsed_by_key: dict[str, list[str]] = {}
    out: dict[str, list[str]] = {}
    for page in pages:
        pid = str(page.get("id") or "")
        if not pid:
            continue
        suf = (page.get("templateSuffix") or page.get("template_suffix") or "").strip()
        urls: list[str] = []
        for key in page_template_asset_keys(suf):
            if key not in parsed_by_key:
                try:
                    raw = fetch_theme_asset_text(tid, key)
                except HttpRequestError:
                    parsed_by_key[key] = []
                    continue
                parsed_by_key[key] = extract_shopify_image_urls_from_theme_json_text(raw) if raw else []
            if parsed_by_key[key]:
                urls = list(parsed_by_key[key])
                break
        out[pid] = urls
    return out
