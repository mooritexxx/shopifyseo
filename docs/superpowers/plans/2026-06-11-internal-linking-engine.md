# Internal Linking Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embedding-driven internal link suggestions with one-click apply, a full internal link graph, and orphan-page detection.

**Architecture:** A new `shopifyseo/internal_links/` package holds the graph parser, anchor detection, suggestion pipeline, and apply service. Suggestions are precomputed into a `link_suggestions` table by a post-sync daemon thread; AI-woven anchors are generated lazily at review time via the existing `_call_ai` provider layer. A new FastAPI router exposes the queue; the React app gets an Internal Links page plus a detail-page card.

**Tech Stack:** Python 3.10 / FastAPI / SQLite (existing patterns), existing `embedding_store.retrieve_related_by_handle`, `dashboard_live_updates` push functions, React 19 + TanStack Query 5.

**Spec:** `docs/superpowers/specs/2026-06-11-internal-linking-design.md`

**Verified integration points (do not re-derive):**
- Bodies live in: `products.description_html`, `collections.description_html`, `pages.body`, `blog_articles.body`. Blog article composite handle = `blog_handle || '/' || handle`. Primary keys are `shopify_id`. Products have `status` ('ACTIVE' etc.); articles have `is_published`.
- GSC traffic: `gsc_clicks` / `gsc_impressions` columns exist on all four entity tables (`SEO_SIGNAL_COLUMNS`, `shopifyseo/dashboard_store.py:44`).
- Similarity: `retrieve_related_by_handle(conn, object_type, handle, top_k=...)` (`shopifyseo/embedding_store.py:1013`) — no API call, returns `[{object_type, object_handle, score, ...}]`.
- URLs: `object_url_with_base(base_url, object_type, handle)` and `_base_store_url(conn)` in `shopifyseo/dashboard_queries/_urls.py`.
- Shopify push: `live_update_product(db_path, product_id, title, seo_title, seo_description, body_html, tags)`, `live_update_collection(db_path, collection_id, title, seo_title, seo_description, body_html)`, `live_update_article(db_path, article_id, title, seo_title, seo_description, body_html)` in `shopifyseo/dashboard_live_updates.py`.
- AI: `from shopifyseo.dashboard_ai_engine_parts.providers import _call_ai, AIProviderRequestError`; `from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings`. `_call_ai(settings, provider, model, messages, timeout, json_schema=..., stage=...)` returns a parsed dict when `json_schema` is set (see `shopifyseo/sidekick.py:295` for the usage pattern).
- Router conventions: see `backend/app/routers/embeddings.py` (`open_db_connection` from `backend.app.db`, `success_response` from `backend.app.schemas.common`).
- Test conventions: in-memory SQLite + minimal `executescript` schema, like `tests/test_internal_link_allowlist.py`.
- Run tests with: `.venv/bin/python -m pytest tests/<file> -v` from repo root.

---

### Task 1: Schema — `internal_links` and `link_suggestions` tables

**Files:**
- Modify: `shopifyseo/dashboard_store.py` (inside `ensure_dashboard_schema`, after the `embeddings` CREATE TABLE around line 523)
- Test: `tests/test_internal_links_schema.py`

- [ ] **Step 1: Write the failing test**

```python
"""Schema tests for internal link graph and suggestion tables."""

import sqlite3

from shopifyseo.dashboard_store import ensure_dashboard_schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    return conn


def test_internal_links_table_exists_with_unique_edge():
    conn = _conn()
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'product', 'widget', 'widget', '/products/widget')"
    )
    # Same edge again must be ignorable via OR IGNORE
    conn.execute(
        "INSERT OR IGNORE INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'product', 'widget', 'widget', '/products/widget')"
    )
    rows = conn.execute("SELECT COUNT(*) AS c FROM internal_links").fetchone()
    assert rows["c"] == 1


def test_link_suggestions_unique_pair_and_status_default():
    conn = _conn()
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, score, created_at) "
        "VALUES ('blog_article', 'news/post', 'collection', 'vapes', 'phrase_wrap', 1.5, 123)"
    )
    row = conn.execute("SELECT status, kind FROM link_suggestions").fetchone()
    assert row["status"] == "suggested"
    conn.execute(
        "INSERT OR IGNORE INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, score, created_at) "
        "VALUES ('blog_article', 'news/post', 'collection', 'vapes', 'ai_woven', 9.9, 456)"
    )
    assert conn.execute("SELECT COUNT(*) AS c FROM link_suggestions").fetchone()["c"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_internal_links_schema.py -v`
Expected: FAIL with `sqlite3.OperationalError: no such table: internal_links`

- [ ] **Step 3: Add the tables in `ensure_dashboard_schema`**

In `shopifyseo/dashboard_store.py`, after the `embeddings` table block, add:

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS internal_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            anchor_text TEXT,
            href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_internal_links_target ON internal_links (target_type, target_handle)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
            anchor_phrase TEXT,
            ai_anchor_html TEXT,
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('suggested', 'applied', 'dismissed')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_link_suggestions_status ON link_suggestions (status, score)"
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_internal_links_schema.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/dashboard_store.py tests/test_internal_links_schema.py
git commit -m "feat: add internal_links and link_suggestions tables"
```

---

### Task 2: Graph parser — `shopifyseo/internal_links/graph.py`

**Files:**
- Create: `shopifyseo/internal_links/__init__.py` (empty)
- Create: `shopifyseo/internal_links/graph.py`
- Test: `tests/test_internal_links_graph.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for internal link extraction and graph rebuild."""

import sqlite3

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links.graph import (
    extract_links,
    rebuild_internal_link_graph,
    resolve_internal_target,
)

BASE = "https://example-store.com"


def test_resolve_internal_target_paths():
    assert resolve_internal_target("/products/widget", BASE) == ("product", "widget")
    assert resolve_internal_target(f"{BASE}/collections/vapes/", BASE) == ("collection", "vapes")
    assert resolve_internal_target("/pages/faq?x=1#top", BASE) == ("page", "faq")
    assert resolve_internal_target("/blogs/news/hello-world", BASE) == ("blog_article", "news/hello-world")


def test_resolve_rejects_external_mailto_fragment_and_unknown():
    assert resolve_internal_target("https://competitor.example/products/widget", BASE) is None
    assert resolve_internal_target("mailto:a@b.co", BASE) is None
    assert resolve_internal_target("#section", BASE) is None
    assert resolve_internal_target("/cart", BASE) is None
    assert resolve_internal_target("", BASE) is None


def test_extract_links_returns_href_and_text():
    html = '<p>See <a href="/products/widget">the widget</a> and <a href="https://x.example/p">ext</a>.</p>'
    assert extract_links(html) == [("/products/widget", "the widget"), ("https://x.example/p", "ext")]


def _catalog_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER, gsc_impressions INTEGER,
            seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, gsc_clicks INTEGER, gsc_impressions INTEGER,
            seo_title TEXT, seo_description TEXT);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER, gsc_impressions INTEGER);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER, gsc_impressions INTEGER,
            seo_title TEXT, seo_description TEXT);
        """
    )
    ensure_dashboard_schema(conn)
    return conn


def test_rebuild_graph_fills_rows_and_is_idempotent():
    conn = _catalog_conn()
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body) VALUES "
        "('news', 'post', 'Post', '<p><a href=\"/products/widget\">widget</a></p>')"
    )
    conn.execute(
        "INSERT INTO products (handle, title, description_html) VALUES "
        "('widget', 'Widget', '<p><a href=\"/collections/all\">all</a></p>')"
    )
    n = rebuild_internal_link_graph(conn, base_url="https://example-store.com")
    assert n == 2
    rows = conn.execute(
        "SELECT source_type, source_handle, target_type, target_handle FROM internal_links ORDER BY source_type"
    ).fetchall()
    assert (rows[0]["source_type"], rows[0]["source_handle"]) == ("blog_article", "news/post")
    assert (rows[0]["target_type"], rows[0]["target_handle"]) == ("product", "widget")
    assert (rows[1]["source_type"], rows[1]["target_handle"]) == ("product", "all")
    # Re-run replaces, not duplicates
    assert rebuild_internal_link_graph(conn, base_url="https://example-store.com") == 2
    assert conn.execute("SELECT COUNT(*) AS c FROM internal_links").fetchone()["c"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_internal_links_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shopifyseo.internal_links'`

- [ ] **Step 3: Implement `graph.py`**

Create empty `shopifyseo/internal_links/__init__.py`, then `shopifyseo/internal_links/graph.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_internal_links_graph.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/internal_links/ tests/test_internal_links_graph.py
git commit -m "feat: internal link graph parser and rebuild"
```

---

### Task 3: Anchor phrase detection — `shopifyseo/internal_links/anchors.py`

**Files:**
- Create: `shopifyseo/internal_links/anchors.py`
- Test: `tests/test_internal_links_anchors.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for anchor phrase detection in catalog bodies."""

from shopifyseo.internal_links.anchors import find_anchor_phrase


def test_finds_phrase_case_insensitively_preserving_body_case():
    html = "<p>Our Ceramic Vape Tanks are popular.</p>"
    assert find_anchor_phrase(html, ["ceramic vape tanks"]) == "Ceramic Vape Tanks"


def test_ignores_text_inside_existing_links_and_headings():
    html = (
        '<h2>ceramic vape tanks</h2>'
        '<p><a href="/x">ceramic vape tanks</a> elsewhere</p>'
    )
    assert find_anchor_phrase(html, ["ceramic vape tanks"]) is None


def test_prefers_longest_candidate_and_requires_word_boundary():
    html = "<p>Best ceramic vape tanks for travel.</p>"
    assert find_anchor_phrase(html, ["vape", "ceramic vape tanks"]) == "ceramic vape tanks"
    assert find_anchor_phrase("<p>vaperizer</p>", ["vape"]) is None


def test_returns_none_for_empty_inputs():
    assert find_anchor_phrase("", ["x"]) is None
    assert find_anchor_phrase("<p>x</p>", []) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_internal_links_anchors.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `anchors` module)

- [ ] **Step 3: Implement `anchors.py`**

```python
"""Find existing body phrases that can carry an internal link."""
from __future__ import annotations

import re
from html.parser import HTMLParser

_EXCLUDED_TAGS = {"a", "h1", "h2", "h3", "h4", "h5", "h6", "script", "style"}


class _EligibleTextExtractor(HTMLParser):
    """Collect visible text that is NOT inside links, headings, scripts, or styles."""

    def __init__(self) -> None:
        super().__init__()
        self._excluded_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _EXCLUDED_TAGS:
            self._excluded_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _EXCLUDED_TAGS and self._excluded_depth > 0:
            self._excluded_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._excluded_depth == 0:
            self.parts.append(data)


def eligible_text(html: str | None) -> str:
    if not html:
        return ""
    extractor = _EligibleTextExtractor()
    extractor.feed(html)
    return " ".join(" ".join(extractor.parts).split())


def find_anchor_phrase(html: str | None, candidates: list[str]) -> str | None:
    """Return the first candidate phrase present in eligible body text (longest first).

    The returned string preserves the body's original casing.
    """
    text = eligible_text(html)
    if not text:
        return None
    for candidate in sorted({c.strip() for c in candidates if c and c.strip()}, key=len, reverse=True):
        pattern = r"\b" + re.escape(candidate) + r"\b"
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_internal_links_anchors.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/internal_links/anchors.py tests/test_internal_links_anchors.py
git commit -m "feat: anchor phrase detection for internal links"
```

---

### Task 4: Suggestion pipeline — `shopifyseo/internal_links/pipeline.py`

**Files:**
- Create: `shopifyseo/internal_links/pipeline.py`
- Test: `tests/test_internal_links_pipeline.py`

The pipeline must be testable without embeddings: `generate_link_suggestions` accepts a `related_fn` parameter (defaults to `retrieve_related_by_handle`) so tests inject a fake.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for the link suggestion pipeline: suppression, scoring, caps."""

import sqlite3

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links.pipeline import generate_link_suggestions


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0, seo_title TEXT, seo_description TEXT);
        """
    )
    ensure_dashboard_schema(conn)
    return conn


def _seed(conn):
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('news', 'post', 'Post', '<p>All about ceramic tanks and more.</p>', 100)"
    )
    conn.execute(
        "INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')"
    )
    conn.execute(
        "INSERT INTO products (handle, title, status) VALUES ('widget', 'Widget Pro', 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO products (handle, title, status) VALUES ('hidden', 'Hidden', 'DRAFT')"
    )
    conn.commit()


def _fake_related(conn, object_type, handle, top_k=10, type_quotas=None):
    if (object_type, handle) == ("blog_article", "news/post"):
        return [
            {"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9},
            {"object_type": "product", "object_handle": "widget", "score": 0.8},
            {"object_type": "product", "object_handle": "hidden", "score": 0.95},
            {"object_type": "product", "object_handle": "low-sim", "score": 0.2},
        ]
    return []


def test_generates_phrase_wrap_and_ai_woven_and_suppresses():
    conn = _conn()
    _seed(conn)
    n = generate_link_suggestions(conn, related_fn=_fake_related)
    rows = conn.execute(
        "SELECT * FROM link_suggestions ORDER BY score DESC"
    ).fetchall()
    handles = {(r["target_type"], r["target_handle"]) for r in rows}
    # draft product and below-threshold target suppressed
    assert ("product", "hidden") not in handles
    assert ("product", "low-sim") not in handles
    assert n == len(rows) == 2
    by_target = {r["target_handle"]: r for r in rows}
    # 'Ceramic Tanks' title appears in body text -> phrase_wrap
    assert by_target["ceramic-tanks"]["kind"] == "phrase_wrap"
    assert by_target["ceramic-tanks"]["anchor_phrase"] == "ceramic tanks"
    # 'Widget Pro' does not appear -> ai_woven placeholder
    assert by_target["widget"]["kind"] == "ai_woven"
    assert by_target["widget"]["anchor_phrase"] is None


def test_existing_link_and_dismissed_are_suppressed_and_rerun_is_stable():
    conn = _conn()
    _seed(conn)
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', '/collections/ceramic-tanks')"
    )
    generate_link_suggestions(conn, related_fn=_fake_related)
    rows = conn.execute("SELECT target_handle, status FROM link_suggestions").fetchall()
    assert [r["target_handle"] for r in rows] == ["widget"]
    conn.execute("UPDATE link_suggestions SET status = 'dismissed' WHERE target_handle = 'widget'")
    conn.commit()
    generate_link_suggestions(conn, related_fn=_fake_related)
    rows = conn.execute("SELECT target_handle, status FROM link_suggestions").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "dismissed"


def test_traffic_weighted_scoring_orders_high_traffic_sources_first():
    conn = _conn()
    _seed(conn)
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('news', 'quiet', 'Quiet', '<p>ceramic tanks here too</p>', 0)"
    )
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if object_type == "blog_article":
            return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
        return []

    generate_link_suggestions(conn, related_fn=related)
    rows = conn.execute(
        "SELECT source_handle, score FROM link_suggestions ORDER BY score DESC"
    ).fetchall()
    assert rows[0]["source_handle"] == "news/post"  # 100 clicks beats 0 clicks
    assert rows[0]["score"] > rows[1]["score"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_internal_links_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `pipeline` module)

- [ ] **Step 3: Implement `pipeline.py`**

```python
"""Generate internal link suggestions from embeddings, traffic, and the link graph."""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from typing import Callable

from .anchors import find_anchor_phrase
from .graph import rebuild_internal_link_graph

logger = logging.getLogger(__name__)

SIM_THRESHOLD = 0.55
MAX_OUTGOING = 5
MAX_INCOMING = 15
TARGET_VALUE = {"product": 1.5, "collection": 1.5, "page": 1.0, "blog_article": 0.8}
ORPHAN_BOOST = 1.25

# (source_type, table, handle_expr, body_col)
_SUGGESTION_SOURCES = (
    ("blog_article", "blog_articles", "blog_handle || '/' || handle", "body"),
    ("product", "products", "handle", "description_html"),
    ("collection", "collections", "handle", "description_html"),
)

_PROGRESS: dict = {"running": False, "stage": "", "done": 0, "total": 0, "finished_at": None}
_PROGRESS_LOCK = threading.Lock()


def internal_link_sync_progress() -> dict:
    with _PROGRESS_LOCK:
        return dict(_PROGRESS)


def _set_progress(**updates) -> None:
    with _PROGRESS_LOCK:
        _PROGRESS.update(updates)


def _default_related(conn, object_type, handle, top_k=10, type_quotas=None):
    from ..embedding_store import retrieve_related_by_handle

    return retrieve_related_by_handle(conn, object_type, handle, top_k=top_k)


def _target_exists_and_published(conn: sqlite3.Connection, t_type: str, t_handle: str) -> bool:
    if t_type == "product":
        row = conn.execute(
            "SELECT status FROM products WHERE handle = ?", (t_handle,)
        ).fetchone()
        return bool(row) and (row["status"] or "ACTIVE").upper() == "ACTIVE"
    if t_type == "collection":
        return conn.execute("SELECT 1 FROM collections WHERE handle = ?", (t_handle,)).fetchone() is not None
    if t_type == "page":
        return conn.execute("SELECT 1 FROM pages WHERE handle = ?", (t_handle,)).fetchone() is not None
    if t_type == "blog_article":
        blog_h, _, article_h = t_handle.partition("/")
        row = conn.execute(
            "SELECT is_published FROM blog_articles WHERE blog_handle = ? AND handle = ?",
            (blog_h, article_h),
        ).fetchone()
        return bool(row) and bool(row["is_published"])
    return False


def _target_title_and_keywords(conn: sqlite3.Connection, t_type: str, t_handle: str) -> list[str]:
    candidates: list[str] = []
    if t_type == "blog_article":
        blog_h, _, article_h = t_handle.partition("/")
        row = conn.execute(
            "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ?",
            (blog_h, article_h),
        ).fetchone()
    else:
        table = {"product": "products", "collection": "collections", "page": "pages"}[t_type]
        row = conn.execute(f"SELECT title FROM {table} WHERE handle = ?", (t_handle,)).fetchone()
    if row and row["title"]:
        candidates.append(row["title"])
    for kw in conn.execute(
        "SELECT keyword FROM keyword_page_map WHERE object_type = ? AND object_handle = ? "
        "ORDER BY is_primary DESC, gsc_clicks DESC LIMIT 10",
        (t_type, t_handle),
    ).fetchall():
        candidates.append(kw["keyword"])
    return candidates


def _orphan_targets(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    linked = {
        (r["target_type"], r["target_handle"])
        for r in conn.execute("SELECT DISTINCT target_type, target_handle FROM internal_links").fetchall()
    }
    orphans: set[tuple[str, str]] = set()
    for t_type, table, handle_expr in (
        ("product", "products", "handle"),
        ("collection", "collections", "handle"),
        ("page", "pages", "handle"),
        ("blog_article", "blog_articles", "blog_handle || '/' || handle"),
    ):
        for r in conn.execute(f"SELECT {handle_expr} AS h FROM {table}").fetchall():
            if (t_type, r["h"]) not in linked:
                orphans.add((t_type, r["h"]))
    return orphans


def generate_link_suggestions(
    conn: sqlite3.Connection,
    related_fn: Callable | None = None,
    rebuild_graph: bool = True,
    base_url: str | None = None,
) -> int:
    """Run the full pipeline. Returns the number of suggestions inserted."""
    related_fn = related_fn or _default_related
    _set_progress(running=True, stage="graph", done=0, total=0)
    try:
        if rebuild_graph:
            rebuild_internal_link_graph(conn, base_url=base_url)
        existing_edges = {
            (r["source_type"], r["source_handle"], r["target_type"], r["target_handle"])
            for r in conn.execute(
                "SELECT source_type, source_handle, target_type, target_handle FROM internal_links"
            ).fetchall()
        }
        orphans = _orphan_targets(conn)
        incoming_pending: dict[tuple[str, str], int] = {}
        for r in conn.execute(
            "SELECT target_type, target_handle, COUNT(*) AS c FROM link_suggestions "
            "WHERE status = 'suggested' GROUP BY 1, 2"
        ).fetchall():
            incoming_pending[(r["target_type"], r["target_handle"])] = r["c"]

        sources: list[tuple[str, str, str, int]] = []  # (type, handle, body, clicks)
        for s_type, table, handle_expr, body_col in _SUGGESTION_SOURCES:
            for r in conn.execute(
                f"SELECT {handle_expr} AS h, {body_col} AS body, COALESCE(gsc_clicks, 0) AS clicks "
                f"FROM {table} WHERE {body_col} IS NOT NULL AND TRIM({body_col}) != ''"
            ).fetchall():
                sources.append((s_type, r["h"], r["body"], r["clicks"]))

        _set_progress(stage="suggestions", total=len(sources))
        inserted = 0
        now = int(time.time())
        for idx, (s_type, s_handle, body, clicks) in enumerate(sources):
            _set_progress(done=idx)
            pending_outgoing = conn.execute(
                "SELECT COUNT(*) FROM link_suggestions WHERE source_type = ? AND source_handle = ? "
                "AND status = 'suggested'",
                (s_type, s_handle),
            ).fetchone()[0]
            if pending_outgoing >= MAX_OUTGOING:
                continue
            try:
                related = related_fn(conn, s_type, s_handle, top_k=10)
            except Exception:
                logger.warning("related lookup failed for %s/%s", s_type, s_handle, exc_info=True)
                continue
            traffic_weight = 1.0 + math.log10(1 + max(0, clicks))
            for cand in related:
                t_type = cand.get("object_type")
                t_handle = cand.get("object_handle")
                sim = float(cand.get("score") or 0)
                if t_type not in TARGET_VALUE or sim < SIM_THRESHOLD:
                    continue
                if (s_type, s_handle) == (t_type, t_handle):
                    continue
                if (s_type, s_handle, t_type, t_handle) in existing_edges:
                    continue
                if incoming_pending.get((t_type, t_handle), 0) >= MAX_INCOMING:
                    continue
                if not _target_exists_and_published(conn, t_type, t_handle):
                    continue
                candidates = _target_title_and_keywords(conn, t_type, t_handle)
                phrase = find_anchor_phrase(body, candidates)
                kind = "phrase_wrap" if phrase else "ai_woven"
                score = sim * traffic_weight * TARGET_VALUE[t_type]
                if (t_type, t_handle) in orphans:
                    score *= ORPHAN_BOOST
                cur = conn.execute(
                    "INSERT OR IGNORE INTO link_suggestions "
                    "(source_type, source_handle, target_type, target_handle, kind, anchor_phrase, "
                    " score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (s_type, s_handle, t_type, t_handle, kind, phrase, score, now),
                )
                if cur.rowcount:
                    inserted += 1
                    incoming_pending[(t_type, t_handle)] = incoming_pending.get((t_type, t_handle), 0) + 1
                    pending_outgoing += 1
                    if pending_outgoing >= MAX_OUTGOING:
                        break
        conn.commit()
        return inserted
    finally:
        _set_progress(running=False, stage="idle", finished_at=int(time.time()))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_internal_links_pipeline.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/internal_links/pipeline.py tests/test_internal_links_pipeline.py
git commit -m "feat: internal link suggestion pipeline with scoring and caps"
```

---

### Task 5: Apply service — `shopifyseo/internal_links/apply.py`

**Files:**
- Create: `shopifyseo/internal_links/apply.py`
- Test: `tests/test_internal_links_apply.py`

`apply_suggestion` takes a `push_fn` parameter (defaults to the real Shopify push) so tests fake the network. The real push dispatches on source type to `live_update_*`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for wrapping anchors and applying suggestions."""

import sqlite3

import pytest

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links.apply import apply_suggestion, wrap_phrase_in_html


def test_wrap_phrase_wraps_first_eligible_occurrence_only():
    html = '<h2>ceramic tanks</h2><p><a href="/x">ceramic tanks</a> Love ceramic tanks. More ceramic tanks.</p>'
    out = wrap_phrase_in_html(html, "ceramic tanks", "https://s.com/collections/ceramic-tanks")
    assert out.count('<a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>') == 1
    # heading and existing link untouched
    assert "<h2>ceramic tanks</h2>" in out
    assert '<a href="/x">ceramic tanks</a>' in out
    # second plain occurrence untouched
    assert out.endswith("More ceramic tanks.</p>")


def test_wrap_phrase_returns_none_when_absent():
    assert wrap_phrase_in_html("<p>nothing here</p>", "ceramic tanks", "u") is None


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]',
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        """
    )
    ensure_dashboard_schema(conn)
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', "
        "'<p>Love ceramic tanks.</p>', 'st', 'sd')"
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, score, created_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', 1.0, 1)"
    )
    conn.commit()
    return conn


def test_apply_phrase_wrap_pushes_and_updates_state():
    conn = _conn()
    pushed = {}

    def fake_push(source_type, row, new_body):
        pushed["body"] = new_body
        return {"ok": True}

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    result = apply_suggestion(conn, sid, base_url="https://s.com", push_fn=fake_push)
    assert result["status"] == "applied"
    assert '<a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>' in pushed["body"]
    row = conn.execute("SELECT status, applied_at FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "applied" and row["applied_at"]
    # local body updated and graph row inserted
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert 'href="https://s.com/collections/ceramic-tanks"' in body
    assert conn.execute(
        "SELECT COUNT(*) FROM internal_links WHERE source_handle = 'news/post' AND target_handle = 'ceramic-tanks'"
    ).fetchone()[0] == 1


def test_apply_failure_leaves_status_suggested():
    conn = _conn()

    def failing_push(source_type, row, new_body):
        raise RuntimeError("shopify down")

    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(RuntimeError):
        apply_suggestion(conn, sid, base_url="https://s.com", push_fn=failing_push)
    row = conn.execute("SELECT status FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["status"] == "suggested"
    body = conn.execute("SELECT body FROM blog_articles WHERE handle = 'post'").fetchone()["body"]
    assert "<a " not in body  # local untouched
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_internal_links_apply.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `apply` module)

- [ ] **Step 3: Implement `apply.py`**

```python
"""Apply link suggestions: wrap the anchor, push to Shopify, update local state."""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Callable

from ..dashboard_queries._urls import object_url_with_base

# Split out regions we must not touch: existing links, headings, script/style.
_PROTECTED_RE = re.compile(
    r"(<a\b.*?</a>|<h[1-6]\b.*?</h[1-6]>|<script\b.*?</script>|<style\b.*?</style>)",
    re.IGNORECASE | re.DOTALL,
)


def wrap_phrase_in_html(html: str, phrase: str, url: str) -> str | None:
    """Wrap the first occurrence of *phrase* outside protected regions. None if absent."""
    if not html or not phrase:
        return None
    pattern = re.compile(r"\b(" + re.escape(phrase) + r")\b", re.IGNORECASE)
    segments = _PROTECTED_RE.split(html)
    for i, segment in enumerate(segments):
        if _PROTECTED_RE.fullmatch(segment):
            continue
        # Also skip text that sits inside a tag attribute by only replacing in text nodes:
        # split the segment on tags and substitute only in non-tag parts.
        parts = re.split(r"(<[^>]+>)", segment)
        for j, part in enumerate(parts):
            if part.startswith("<"):
                continue
            new_part, n = pattern.subn(rf'<a href="{url}">\1</a>', part, count=1)
            if n:
                parts[j] = new_part
                segments[i] = "".join(parts)
                return "".join(segments)
    return None


_SOURCE_META = {
    # source_type: (table, where_sql, body_col, select_cols)
    "blog_article": ("blog_articles", "blog_handle = ? AND handle = ?", "body",
                     "shopify_id, title, seo_title, seo_description, body"),
    "product": ("products", "handle = ?", "description_html",
                "shopify_id, title, seo_title, seo_description, description_html, tags_json"),
    "collection": ("collections", "handle = ?", "description_html",
                   "shopify_id, title, seo_title, seo_description, description_html"),
}


def _load_source_row(conn: sqlite3.Connection, source_type: str, source_handle: str) -> sqlite3.Row:
    table, where, _body_col, cols = _SOURCE_META[source_type]
    if source_type == "blog_article":
        blog_h, _, article_h = source_handle.partition("/")
        params: tuple = (blog_h, article_h)
    else:
        params = (source_handle,)
    row = conn.execute(f"SELECT {cols} FROM {table} WHERE {where}", params).fetchone()
    if not row:
        raise ValueError(f"source not found: {source_type}/{source_handle}")
    return row


def _shopify_push(source_type: str, row: sqlite3.Row, new_body: str) -> dict:
    import json

    from ..dashboard_live_updates import (
        live_update_article,
        live_update_collection,
        live_update_product,
    )
    from ..dashboard_store import DB_PATH

    title = row["title"] or ""
    seo_title = row["seo_title"] or ""
    seo_description = row["seo_description"] or ""
    if source_type == "blog_article":
        return live_update_article(str(DB_PATH), row["shopify_id"], title, seo_title, seo_description, new_body)
    if source_type == "collection":
        return live_update_collection(str(DB_PATH), row["shopify_id"], title, seo_title, seo_description, new_body)
    tags = ", ".join(json.loads(row["tags_json"] or "[]"))
    return live_update_product(str(DB_PATH), row["shopify_id"], title, seo_title, seo_description, new_body, tags)


def apply_suggestion(
    conn: sqlite3.Connection,
    suggestion_id: int,
    base_url: str,
    push_fn: Callable | None = None,
) -> dict:
    """Apply one suggestion: wrap anchor, push to Shopify, then update local DB.

    Raises on push failure; local state is only mutated after the push succeeds.
    """
    push_fn = push_fn or _shopify_push
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if sug["status"] == "applied":
        return {"status": "applied", "already": True}
    if sug["status"] != "suggested":
        raise ValueError(f"suggestion {suggestion_id} is {sug['status']}")

    source_type, source_handle = sug["source_type"], sug["source_handle"]
    table, where, body_col, _cols = _SOURCE_META[source_type]
    row = _load_source_row(conn, source_type, source_handle)
    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])

    if sug["kind"] == "phrase_wrap":
        new_body = wrap_phrase_in_html(row[body_col], sug["anchor_phrase"], url)
        if new_body is None:
            raise ValueError("anchor phrase no longer present in body")
    else:
        if not sug["ai_anchor_html"]:
            raise ValueError("ai_woven suggestion has no generated anchor yet")
        new_body = sug["ai_anchor_html"]  # full replacement body produced at review time

    push_fn(source_type, row, new_body)  # raises on failure -> nothing below runs

    if source_type == "blog_article":
        blog_h, _, article_h = source_handle.partition("/")
        conn.execute(
            f"UPDATE {table} SET {body_col} = ? WHERE blog_handle = ? AND handle = ?",
            (new_body, blog_h, article_h),
        )
    else:
        conn.execute(f"UPDATE {table} SET {body_col} = ? WHERE handle = ?", (new_body, source_handle))
    conn.execute(
        "INSERT OR IGNORE INTO internal_links "
        "(source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_type, source_handle, sug["target_type"], sug["target_handle"],
         sug["anchor_phrase"] or "", url),
    )
    conn.execute(
        "UPDATE link_suggestions SET status = 'applied', applied_at = ? WHERE id = ?",
        (int(time.time()), suggestion_id),
    )
    conn.commit()
    return {"status": "applied", "url": url}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_internal_links_apply.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/internal_links/apply.py tests/test_internal_links_apply.py
git commit -m "feat: one-click apply for link suggestions with safe push ordering"
```

---

### Task 6: Lazy AI weave — `shopifyseo/internal_links/ai_weave.py`

**Files:**
- Create: `shopifyseo/internal_links/ai_weave.py`
- Test: `tests/test_internal_links_ai_weave.py`

The AI returns the full revised body (one sentence rewritten or added, carrying the link). We store it in `ai_anchor_html` and the UI diffs it against the current body. `call_ai_fn` is injectable for tests.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for AI-woven anchor generation."""

import sqlite3

import pytest

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links.ai_weave import generate_ai_anchor


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]',
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        """
    )
    ensure_dashboard_schema(conn)
    conn.execute(
        "INSERT INTO blog_articles (blog_handle, handle, title, body) VALUES "
        "('news', 'post', 'Post', '<p>Original text.</p>')"
    )
    conn.execute("INSERT INTO products (handle, title, status) VALUES ('widget', 'Widget Pro', 'ACTIVE')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "score, created_at) VALUES ('blog_article', 'news/post', 'product', 'widget', 'ai_woven', 1.0, 1)"
    )
    conn.commit()
    return conn


def test_generate_ai_anchor_stores_revised_body():
    conn = _conn()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    revised = '<p>Original text. Check out the <a href="https://s.com/products/widget">Widget Pro</a>.</p>'

    def fake_call_ai(messages, json_schema):
        return {"revised_body": revised}

    result = generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=fake_call_ai)
    assert result["ai_anchor_html"] == revised
    row = conn.execute("SELECT ai_anchor_html FROM link_suggestions WHERE id = ?", (sid,)).fetchone()
    assert row["ai_anchor_html"] == revised


def test_generate_ai_anchor_rejects_missing_link():
    conn = _conn()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]

    def fake_call_ai(messages, json_schema):
        return {"revised_body": "<p>No link at all.</p>"}

    with pytest.raises(ValueError, match="target link"):
        generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=fake_call_ai)


def test_generate_ai_anchor_only_for_ai_woven():
    conn = _conn()
    conn.execute("UPDATE link_suggestions SET kind = 'phrase_wrap'")
    conn.commit()
    sid = conn.execute("SELECT id FROM link_suggestions").fetchone()["id"]
    with pytest.raises(ValueError, match="ai_woven"):
        generate_ai_anchor(conn, sid, base_url="https://s.com", call_ai_fn=lambda m, j: {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_internal_links_ai_weave.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `ai_weave` module)

- [ ] **Step 3: Implement `ai_weave.py`**

```python
"""Generate an AI-woven anchor sentence for suggestions with no matching phrase."""
from __future__ import annotations

import sqlite3
from typing import Callable

from ..dashboard_queries._urls import object_url_with_base
from .apply import _load_source_row, _SOURCE_META

WEAVE_SCHEMA = {
    "type": "object",
    "properties": {"revised_body": {"type": "string", "minLength": 1}},
    "required": ["revised_body"],
    "additionalProperties": False,
}

_PROMPT = (
    "You are an SEO editor. Revise the HTML body below so it naturally links to the target page. "
    "Change AT MOST one sentence (rewrite one existing sentence or append one short sentence to the most "
    "relevant paragraph). Keep every other character of the HTML identical. The link must use the exact "
    "URL given, with natural anchor text related to the target title.\n\n"
    "Target title: {title}\nTarget URL: {url}\n\nHTML body:\n{body}\n\n"
    "Return JSON: {{\"revised_body\": \"<the full revised HTML body>\"}}"
)


def _default_call_ai(messages: list[dict], json_schema: dict) -> dict:
    from ..dashboard_ai_engine_parts.providers import _call_ai
    from ..dashboard_ai_engine_parts.settings import ai_settings
    from ..dashboard_store import db_connect

    conn = db_connect()
    try:
        settings = ai_settings(conn)
    finally:
        conn.close()
    provider = settings.get("generation_provider") or settings.get("provider") or ""
    model = settings.get("generation_model") or settings.get("model") or ""
    return _call_ai(settings, provider, model, messages, 120, json_schema=json_schema, stage="link_weave")


def _target_title(conn: sqlite3.Connection, t_type: str, t_handle: str) -> str:
    if t_type == "blog_article":
        blog_h, _, article_h = t_handle.partition("/")
        row = conn.execute(
            "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ?", (blog_h, article_h)
        ).fetchone()
    else:
        table = {"product": "products", "collection": "collections", "page": "pages"}[t_type]
        row = conn.execute(f"SELECT title FROM {table} WHERE handle = ?", (t_handle,)).fetchone()
    return (row["title"] if row else "") or t_handle


def generate_ai_anchor(
    conn: sqlite3.Connection,
    suggestion_id: int,
    base_url: str,
    call_ai_fn: Callable | None = None,
) -> dict:
    """Generate and persist the revised body for an ai_woven suggestion."""
    call_ai_fn = call_ai_fn or _default_call_ai
    sug = conn.execute("SELECT * FROM link_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    if not sug:
        raise ValueError(f"suggestion {suggestion_id} not found")
    if sug["kind"] != "ai_woven":
        raise ValueError("anchor generation only applies to ai_woven suggestions")

    row = _load_source_row(conn, sug["source_type"], sug["source_handle"])
    body_col = _SOURCE_META[sug["source_type"]][2]
    url = object_url_with_base(base_url, sug["target_type"], sug["target_handle"])
    title = _target_title(conn, sug["target_type"], sug["target_handle"])
    prompt = _PROMPT.format(title=title, url=url, body=row[body_col] or "")
    raw = call_ai_fn([{"role": "user", "content": prompt}], WEAVE_SCHEMA)
    revised = str(raw.get("revised_body") or "").strip()
    if f'href="{url}"' not in revised:
        raise ValueError("AI response does not contain the target link")
    conn.execute(
        "UPDATE link_suggestions SET ai_anchor_html = ? WHERE id = ?", (revised, suggestion_id)
    )
    conn.commit()
    return {"ai_anchor_html": revised, "current_body": row[body_col] or "", "url": url}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_internal_links_ai_weave.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/internal_links/ai_weave.py tests/test_internal_links_ai_weave.py
git commit -m "feat: lazy AI-woven anchor generation for link suggestions"
```

---

### Task 7: API router — `backend/app/routers/internal_links.py`

**Files:**
- Create: `backend/app/routers/internal_links.py`
- Modify: `backend/app/main.py` (register router alongside the existing includes)
- Test: `tests/test_internal_links_api.py`

- [ ] **Step 1: Write the failing tests**

Follow the existing API test style (FastAPI `TestClient` against the app with a temp DB — check `tests/test_audience_questions_api.py` for the project's exact fixture pattern and mirror it; the key assertions:)

```python
"""API contract tests for the internal links router."""

from fastapi.testclient import TestClient


def test_summary_endpoint_shape(internal_links_client: TestClient):
    res = internal_links_client.get("/api/internal-links/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    data = body["data"]
    assert set(data) >= {"total_links", "orphan_count", "suggested", "applied", "dismissed"}


def test_suggestions_filter_and_dismiss_flow(internal_links_client: TestClient, seeded_suggestion_id: int):
    res = internal_links_client.get("/api/internal-links/suggestions?status=suggested")
    assert res.json()["data"][0]["id"] == seeded_suggestion_id
    res = internal_links_client.post(f"/api/internal-links/suggestions/{seeded_suggestion_id}/dismiss")
    assert res.json()["ok"] is True
    res = internal_links_client.get("/api/internal-links/suggestions?status=suggested")
    assert res.json()["data"] == []
```

(Define `internal_links_client` / `seeded_suggestion_id` fixtures in the test file using the same temp-DB mechanism the existing API tests use — `seeded_suggestion_id` inserts one `link_suggestions` row directly.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_internal_links_api.py -v`
Expected: FAIL (404s — router not registered)

- [ ] **Step 3: Implement the router**

```python
"""API endpoints for the internal linking engine."""

import logging
import threading

from fastapi import APIRouter, HTTPException, Query, status

from backend.app.db import open_db_connection
from backend.app.schemas.common import SuccessResponse, success_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal-links", tags=["internal-links"])


def _base_url(conn) -> str:
    from shopifyseo.dashboard_queries._urls import _base_store_url

    return _base_store_url(conn)


@router.get("/summary", response_model=SuccessResponse[dict])
def summary():
    conn = open_db_connection()
    try:
        total_links = conn.execute("SELECT COUNT(*) FROM internal_links").fetchone()[0]
        counts = {
            r["status"]: r["c"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS c FROM link_suggestions GROUP BY status"
            ).fetchall()
        }
        from shopifyseo.internal_links.pipeline import _orphan_targets, internal_link_sync_progress

        return success_response({
            "total_links": total_links,
            "orphan_count": len(_orphan_targets(conn)),
            "suggested": counts.get("suggested", 0),
            "applied": counts.get("applied", 0),
            "dismissed": counts.get("dismissed", 0),
            "progress": internal_link_sync_progress(),
        })
    finally:
        conn.close()


@router.get("/orphans", response_model=SuccessResponse[list])
def orphans():
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.pipeline import _orphan_targets

        items = sorted(_orphan_targets(conn))
        return success_response([
            {"object_type": t, "handle": h} for t, h in items
        ])
    finally:
        conn.close()


@router.get("/suggestions", response_model=SuccessResponse[list])
def suggestions(
    source_type: str | None = Query(default=None),
    source_handle: str | None = Query(default=None),
    status_filter: str = Query(default="suggested", alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
):
    conn = open_db_connection()
    try:
        sql = "SELECT * FROM link_suggestions WHERE status = ?"
        params: list = [status_filter]
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        if source_handle:
            sql += " AND source_handle = ?"
            params.append(source_handle)
        sql += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return success_response(rows)
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/generate-anchor", response_model=SuccessResponse[dict])
def generate_anchor(suggestion_id: int):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.ai_weave import generate_ai_anchor

        return success_response(generate_ai_anchor(conn, suggestion_id, base_url=_base_url(conn)))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Anchor generation failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/apply", response_model=SuccessResponse[dict])
def apply(suggestion_id: int):
    conn = open_db_connection()
    try:
        from shopifyseo.internal_links.apply import apply_suggestion

        return success_response(apply_suggestion(conn, suggestion_id, base_url=_base_url(conn)))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.warning("Apply suggestion failed", exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    finally:
        conn.close()


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuccessResponse[dict])
def dismiss(suggestion_id: int):
    conn = open_db_connection()
    try:
        cur = conn.execute(
            "UPDATE link_suggestions SET status = 'dismissed' WHERE id = ? AND status = 'suggested'",
            (suggestion_id,),
        )
        conn.commit()
        if not cur.rowcount:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="suggestion not found or not pending")
        return success_response({"status": "dismissed"})
    finally:
        conn.close()


@router.post("/rebuild", response_model=SuccessResponse[dict])
def rebuild():
    def _bg():
        try:
            from shopifyseo.internal_links.pipeline import generate_link_suggestions

            conn = open_db_connection()
            try:
                generate_link_suggestions(conn)
            finally:
                conn.close()
        except Exception:
            logger.warning("Internal link rebuild failed", exc_info=True)

    threading.Thread(target=_bg, daemon=True).start()
    return success_response({"status": "started"})
```

Register in `backend/app/main.py`: import `internal_links` next to the other router imports and add `app.include_router(internal_links.router)` next to the existing `include_router` calls.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_internal_links_api.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass, no regressions

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/internal_links.py backend/app/main.py tests/test_internal_links_api.py
git commit -m "feat: internal links API router"
```

---

### Task 8: Post-sync hook

**Files:**
- Modify: `shopifyseo/dashboard_actions/_sync.py` (next to `_start_gsc_query_embedding_sync`, ~line 188)
- Test: `tests/test_internal_links_sync_hook.py`

- [ ] **Step 1: Write the failing test**

```python
"""The post-sync hook starts an internal link refresh thread."""

from unittest.mock import patch

from shopifyseo.dashboard_actions import _sync


def test_start_internal_link_refresh_runs_pipeline():
    with patch("shopifyseo.internal_links.pipeline.generate_link_suggestions") as gen, \
         patch.object(_sync, "_db_connect_for_actions") as connect:
        thread = _sync._start_internal_link_refresh("/tmp/x.sqlite3")
        thread.join(timeout=5)
        assert gen.called
        assert connect.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_internal_links_sync_hook.py -v`
Expected: FAIL with `AttributeError: ... has no attribute '_start_internal_link_refresh'`

- [ ] **Step 3: Implement the hook**

Add to `shopifyseo/dashboard_actions/_sync.py`, mirroring `_start_gsc_query_embedding_sync` (which returns nothing — make the new one return the thread for testability):

```python
def _start_internal_link_refresh(db_path: str) -> threading.Thread:
    """Rebuild the internal link graph and suggestions after visible sync completion."""

    def _worker() -> None:
        conn: sqlite3.Connection | None = None
        try:
            conn = _db_connect_for_actions(db_path)
            from ..internal_links.pipeline import generate_link_suggestions

            generate_link_suggestions(conn)
        except Exception:
            logger.warning("Background internal link refresh failed", exc_info=True)
        finally:
            if conn is not None:
                conn.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread
```

Call it immediately after the existing `_start_gsc_query_embedding_sync(db_path)` call site (find it with `grep -n "_start_gsc_query_embedding_sync(" shopifyseo/dashboard_actions/_sync.py` and add `_start_internal_link_refresh(db_path)` on the next line).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_internal_links_sync_hook.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/dashboard_actions/_sync.py tests/test_internal_links_sync_hook.py
git commit -m "feat: refresh internal link suggestions after catalog sync"
```

---

### Task 9: Frontend — Internal Links page

**Files:**
- Create: `frontend/src/routes/internal-links-page.tsx`
- Create: `frontend/src/hooks/use-internal-links.ts`
- Modify: `frontend/src/app/router.tsx` (lazy import + `/internal-links` route via `shell(...)`)
- Modify: the sidebar/nav component (find it with `grep -rn "Embeddings" frontend/src/components/` and add an "Internal Links" entry pointing to `/internal-links` in the same list)

- [ ] **Step 1: Add the API hooks**

`frontend/src/hooks/use-internal-links.ts`:

```tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export interface LinkSuggestion {
  id: number;
  source_type: string;
  source_handle: string;
  target_type: string;
  target_handle: string;
  kind: "phrase_wrap" | "ai_woven";
  anchor_phrase: string | null;
  ai_anchor_html: string | null;
  score: number;
  status: "suggested" | "applied" | "dismissed";
}

export interface LinkSummary {
  total_links: number;
  orphan_count: number;
  suggested: number;
  applied: number;
  dismissed: number;
  progress: { running: boolean; stage: string; done: number; total: number };
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const body = await res.json();
  if (!body.ok) throw new Error(body.error?.message ?? "Request failed");
  return body.data as T;
}

async function postJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "POST" });
  const body = await res.json();
  if (!body.ok) throw new Error(body.error?.message ?? body.detail ?? "Request failed");
  return body.data as T;
}

export function useLinkSummary() {
  return useQuery({ queryKey: ["internal-links", "summary"], queryFn: () => getJson<LinkSummary>("/api/internal-links/summary") });
}

export function useLinkSuggestions(params: { sourceType?: string; sourceHandle?: string } = {}) {
  const search = new URLSearchParams({ status: "suggested" });
  if (params.sourceType) search.set("source_type", params.sourceType);
  if (params.sourceHandle) search.set("source_handle", params.sourceHandle);
  return useQuery({
    queryKey: ["internal-links", "suggestions", params],
    queryFn: () => getJson<LinkSuggestion[]>(`/api/internal-links/suggestions?${search}`),
  });
}

export function useOrphans() {
  return useQuery({
    queryKey: ["internal-links", "orphans"],
    queryFn: () => getJson<{ object_type: string; handle: string }[]>("/api/internal-links/orphans"),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["internal-links"] });
}

export function useApplySuggestion() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => postJson(`/api/internal-links/suggestions/${id}/apply`),
    onSuccess: invalidate,
  });
}

export function useDismissSuggestion() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => postJson(`/api/internal-links/suggestions/${id}/dismiss`),
    onSuccess: invalidate,
  });
}

export function useGenerateAnchor() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) =>
      postJson<{ ai_anchor_html: string; current_body: string }>(
        `/api/internal-links/suggestions/${id}/generate-anchor`,
      ),
    onSuccess: invalidate,
  });
}

export function useRebuildLinks() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: () => postJson("/api/internal-links/rebuild"), onSuccess: invalidate });
}
```

- [ ] **Step 2: Build the page**

`frontend/src/routes/internal-links-page.tsx` — match the visual idiom of `embeddings-page.tsx` (cards, table classes, buttons). Structure:

```tsx
import { useState } from "react";
import {
  useApplySuggestion,
  useDismissSuggestion,
  useGenerateAnchor,
  useLinkSuggestions,
  useLinkSummary,
  useOrphans,
  useRebuildLinks,
  type LinkSuggestion,
} from "../hooks/use-internal-links";

export function InternalLinksPage() {
  const summary = useLinkSummary();
  const suggestions = useLinkSuggestions();
  const orphans = useOrphans();
  const apply = useApplySuggestion();
  const dismiss = useDismissSuggestion();
  const generate = useGenerateAnchor();
  const rebuild = useRebuildLinks();
  const [tab, setTab] = useState<"suggestions" | "orphans">("suggestions");
  const [diffFor, setDiffFor] = useState<LinkSuggestion | null>(null);

  // Summary cards row: total_links / orphan_count / suggested (+ "Rebuild" button calling rebuild.mutate()).
  // Tab bar: Suggestions | Orphans.
  // Suggestions tab: table rows = source -> target, kind badge ("wraps existing text" vs
  //   "modifies copy" in a warning color), score, actions:
  //   - phrase_wrap: [Apply] [Dismiss]
  //   - ai_woven without ai_anchor_html: [Generate] [Dismiss]; Generate calls generate.mutate(s.id)
  //   - ai_woven with ai_anchor_html: [Review] opens diff modal (setDiffFor), [Dismiss]
  // Diff modal: current body vs ai_anchor_html side by side (render as <pre> text), [Apply] [Cancel].
  // Orphans tab: simple table of object_type / handle.
  // Use the same card/table/button class names as embeddings-page.tsx so styling matches.
  ...
}
```

(The implementer fills in the JSX following `embeddings-page.tsx` styling; all data/actions come from the hooks above. Mutation errors surface via the page's standard toast/error pattern — copy whatever `embeddings-page.tsx` does for its refresh action.)

Register the route in `frontend/src/app/router.tsx`:

```tsx
const InternalLinksPage = lazy(() =>
  import("../routes/internal-links-page").then((m) => ({ default: m.InternalLinksPage }))
);
// in the routes array:
{ path: "/internal-links", element: shell(<InternalLinksPage />) },
```

Add the nav entry next to the existing Embeddings link.

- [ ] **Step 3: Verify frontend builds and typechecks**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors, build succeeds

- [ ] **Step 4: Manual smoke test**

Run: `./start_app.sh`, open `http://127.0.0.1:8000/app/internal-links`, confirm summary cards render and Rebuild starts the pipeline (progress visible in summary after refetch).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/internal-links-page.tsx frontend/src/hooks/use-internal-links.ts frontend/src/app/router.tsx frontend/src/components/
git commit -m "feat: internal links page with suggestion queue and orphan report"
```

---

### Task 10: Detail-page "Link opportunities" card

**Files:**
- Create: `frontend/src/components/link-opportunities-card.tsx`
- Modify: `frontend/src/routes/product-detail-page.tsx`, `frontend/src/routes/content-detail-page.tsx` (collections), `frontend/src/routes/article-detail-page.tsx` — render the card in each page's sidebar/secondary column

- [ ] **Step 1: Build the card component**

```tsx
import {
  useApplySuggestion,
  useDismissSuggestion,
  useLinkSuggestions,
} from "../hooks/use-internal-links";

export function LinkOpportunitiesCard(props: { sourceType: string; sourceHandle: string }) {
  const { data, isLoading } = useLinkSuggestions({
    sourceType: props.sourceType,
    sourceHandle: props.sourceHandle,
  });
  const apply = useApplySuggestion();
  const dismiss = useDismissSuggestion();
  const top = (data ?? []).slice(0, 3);
  if (isLoading || top.length === 0) return null;
  // Card titled "Link opportunities": one row per suggestion -> target handle, kind badge,
  // Apply (phrase_wrap only — ai_woven rows link to /internal-links instead) and Dismiss buttons.
  // Match the card idiom already used on the detail pages.
  ...
}
```

- [ ] **Step 2: Mount it on the three detail pages**

In each detail page, render `<LinkOpportunitiesCard sourceType="product" sourceHandle={handle} />` (type `"collection"` on the collection detail view, `"blog_article"` with the composite `blogHandle/articleHandle` on the article page) in the existing secondary-info column.

- [ ] **Step 3: Verify build**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: clean

- [ ] **Step 4: Manual smoke test**

Open a product detail page with pending suggestions; confirm the card shows up to 3 rows, Apply pushes and the row disappears, Dismiss removes it.

- [ ] **Step 5: Full test suite + commit**

```bash
.venv/bin/python -m pytest tests/ -q
cd frontend && npm test -- --run && cd ..
git add frontend/src/components/link-opportunities-card.tsx frontend/src/routes/
git commit -m "feat: link opportunities card on detail pages"
```

---

## Plan Self-Review Notes

- **Spec coverage:** schema (T1), graph + orphans (T2, T4, T7), anchor detection (T3), scoring/suppression/caps (T4), apply with push-before-mutate ordering (T5), lazy AI weave with diff data (T6), API (T7), post-sync trigger (T8), dedicated page (T9), detail cards (T10). Settings-page tunable for `SIM_THRESHOLD` deferred — constant in v1 (YAGNI; spec lists it as "tunable in Settings", revisit if defaults feel wrong — recorded as a conscious cut).
- **Injection points for tests:** `related_fn`, `push_fn`, `call_ai_fn` keep all network/AI out of unit tests.
- **Consistency check:** `ai_anchor_html` stores the FULL revised body (T5 apply and T6 weave agree); `_SOURCE_META` is shared by apply and weave; suggestion sources exclude `page` (spec) while graph parses all four types (spec).
