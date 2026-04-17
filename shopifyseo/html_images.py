"""Extract <img> sources from HTML, limited to Shopify CDN URLs."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse


def _absolute_url(src: str) -> str:
    s = (src or "").strip()
    if not s or s.lower().startswith("data:"):
        return ""
    if s.startswith("//"):
        return f"https:{s}"
    return s


def is_shopify_hosted_image_url(url: str) -> bool:
    """True if URL looks like a Shopify CDN / storefront image (not arbitrary external)."""
    u = _absolute_url(url)
    if not u or not u.startswith("http"):
        return False
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    if "shopify" in host or "shopifycdn" in host:
        return True
    if host.endswith(".myshopify.com") and "/cdn/" in u:
        return True
    return False


class _ImgSrcCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        ad = {k.lower(): (v or "").strip() for k, v in attrs if k}
        src = ad.get("src", "")
        if not src:
            return
        abs_u = _absolute_url(src)
        if not abs_u or not is_shopify_hosted_image_url(abs_u):
            return
        alt = (ad.get("alt") or "").strip()
        self._out.append((abs_u, alt))


def extract_shopify_images_from_html(html: str | None) -> list[tuple[str, str]]:
    """Return (url, alt) for each Shopify-hosted <img> in document order."""
    raw = (html or "").strip()
    if not raw:
        return []
    parser = _ImgSrcCollector()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return []
    return list(parser._out)
