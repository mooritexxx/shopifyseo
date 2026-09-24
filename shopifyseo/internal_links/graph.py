"""Internal link graph: parse catalog bodies into the internal_links table."""
from __future__ import annotations

import sqlite3
from html.parser import HTMLParser
from urllib.parse import urlparse

# (source_type, table, handle_expr, body_column)
_SOURCES: tuple[tuple[str, str, str, str], ...] = (
    ("product", "products", "handle", "description_html"),
    ("collection", "collections", "handle", "description_html"),
    ("page", "pages", "handle", "body"),
    ("blog_article", "blog_articles", "blog_handle || '/' || handle", "body"),
)

_PATH_TYPES = {"products": "product", "collections": "collection", "pages": "page"}


def resolve_internal_target(href: str, base_url: str) -> tuple[str, str] | None:
    """Map an href to (target_type, target_handle), or None if not an internal catalog link."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    parsed = urlparse(href)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return None
    base_host = urlparse(base_url).netloc.lower() if base_url else ""
    if parsed.netloc and parsed.netloc.lower() != base_host:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) == 2 and parts[0] in _PATH_TYPES:
        return (_PATH_TYPES[parts[0]], parts[1])
    if len(parts) == 3 and parts[0] == "blogs":
        return ("blog_article", f"{parts[1]}/{parts[2]}")
    return None


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a" and self._href is None:
            self._href = dict(attrs).get("href") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text).strip()))
            self._href = None


def extract_links(html: str | None) -> list[tuple[str, str]]:
    """Return [(href, anchor_text)] for every <a> in *html*."""
    if not html:
        return []
    collector = _LinkCollector()
    collector.feed(html)
    return collector.links


def rebuild_internal_link_graph(conn: sqlite3.Connection, base_url: str | None = None) -> int:
    """Re-parse every catalog body into internal_links. Returns row count."""
    if base_url is None:
        from ..dashboard_queries._urls import _base_store_url

        base_url = _base_store_url(conn)
    conn.execute("DELETE FROM internal_links")
    inserted = 0
    for source_type, table, handle_expr, body_col in _SOURCES:
        rows = conn.execute(
            f"SELECT {handle_expr} AS src_handle, {body_col} AS body FROM {table} "
            f"WHERE {body_col} IS NOT NULL AND TRIM({body_col}) != ''"
        ).fetchall()
        for row in rows:
            for href, anchor_text in extract_links(row["body"]):
                target = resolve_internal_target(href, base_url)
                if not target:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO internal_links "
                    "(source_type, source_handle, target_type, target_handle, anchor_text, href) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (source_type, row["src_handle"], target[0], target[1], anchor_text, href),
                )
                inserted += 1
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM internal_links").fetchone()[0]
