"""Generate internal link suggestions from embeddings, traffic, and the link graph."""
from __future__ import annotations

import hashlib
import logging
import math
import sqlite3
import threading
import time
from typing import Callable

from .anchors import find_anchor_phrase
from .graph import rebuild_internal_link_graph

logger = logging.getLogger(__name__)

DEFAULT_SIM_THRESHOLD = 0.55
MAX_OUTGOING = 5
MAX_INCOMING = 15
TARGET_VALUE = {"product": 1.5, "collection": 1.5, "page": 1.0, "blog_article": 0.8}
ORPHAN_BOOST = 1.25


def _get_sim_threshold(conn: sqlite3.Connection) -> float:
    """Load internal_link_sim_threshold from service_settings with 0.55 fallback."""
    try:
        from ..dashboard_google import get_service_setting

        val = get_service_setting(conn, "internal_link_sim_threshold", "")
        if val:
            return float(val)
    except Exception:
        pass
    return DEFAULT_SIM_THRESHOLD


def _hash_body(body: str) -> str:
    """SHA-256 hex digest of body text for stale detection."""
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()

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
    try:
        for kw in conn.execute(
            "SELECT keyword FROM keyword_page_map WHERE object_type = ? AND object_handle = ? "
            "ORDER BY is_primary DESC, gsc_clicks DESC LIMIT 10",
            (t_type, t_handle),
        ).fetchall():
            candidates.append(kw["keyword"])
    except Exception:
        pass
    try:
        for kw in conn.execute(
            """
            SELECT ck.keyword FROM cluster_keywords ck
            JOIN clusters c ON c.id = ck.cluster_id
            WHERE c.match_type = ? AND c.match_handle = ?
            LIMIT 20
            """,
            (t_type, t_handle),
        ).fetchall():
            if kw["keyword"] and kw["keyword"] not in candidates:
                candidates.append(kw["keyword"])
    except Exception:
        pass
    return candidates


def _orphan_targets(conn: sqlite3.Connection) -> list[tuple[str, str, int, int]]:
    """Return published entities with no inbound links, sorted by traffic (clicks+impressions desc).

    Returns list of (object_type, handle, gsc_clicks, gsc_impressions).
    """
    linked = {
        (r["target_type"], r["target_handle"])
        for r in conn.execute("SELECT DISTINCT target_type, target_handle FROM internal_links").fetchall()
    }
    orphans: list[tuple[str, str, int, int]] = []
    for r in conn.execute(
        "SELECT handle AS h, COALESCE(gsc_clicks, 0) AS clicks, COALESCE(gsc_impressions, 0) AS impr "
        "FROM products WHERE (status IS NULL OR status = '' OR UPPER(status) = 'ACTIVE')"
    ).fetchall():
        if ("product", r["h"]) not in linked:
            orphans.append(("product", r["h"], r["clicks"], r["impr"]))
    for r in conn.execute(
        "SELECT handle AS h, COALESCE(gsc_clicks, 0) AS clicks, COALESCE(gsc_impressions, 0) AS impr "
        "FROM collections"
    ).fetchall():
        if ("collection", r["h"]) not in linked:
            orphans.append(("collection", r["h"], r["clicks"], r["impr"]))
    for r in conn.execute(
        "SELECT handle AS h, COALESCE(gsc_clicks, 0) AS clicks, COALESCE(gsc_impressions, 0) AS impr "
        "FROM pages"
    ).fetchall():
        if ("page", r["h"]) not in linked:
            orphans.append(("page", r["h"], r["clicks"], r["impr"]))
    for r in conn.execute(
        "SELECT blog_handle || '/' || handle AS h, COALESCE(gsc_clicks, 0) AS clicks, COALESCE(gsc_impressions, 0) AS impr "
        "FROM blog_articles WHERE is_published = 1"
    ).fetchall():
        if ("blog_article", r["h"]) not in linked:
            orphans.append(("blog_article", r["h"], r["clicks"], r["impr"]))
    orphans.sort(key=lambda x: (x[2] + x[3], x[2]), reverse=True)
    return orphans


def _orphan_target_set(conn: sqlite3.Connection) -> set[tuple[str, str]]:
    """Return just (type, handle) pairs of orphans for internal scoring."""
    return {(t, h) for t, h, _c, _i in _orphan_targets(conn)}


def generate_link_suggestions(
    conn: sqlite3.Connection,
    related_fn: Callable | None = None,
    rebuild_graph: bool = True,
    base_url: str | None = None,
) -> int:
    """Run the full pipeline. Returns the number of suggestions inserted."""
    related_fn = related_fn or _default_related
    sim_threshold = _get_sim_threshold(conn)
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
        orphans = _orphan_target_set(conn)
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
            body_hash = _hash_body(body)
            for cand in related:
                t_type = cand.get("object_type")
                t_handle = cand.get("object_handle")
                sim = float(cand.get("score") or 0)
                if t_type not in TARGET_VALUE or sim < sim_threshold:
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
                    " source_body_hash, score, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (s_type, s_handle, t_type, t_handle, kind, phrase, body_hash, score, now),
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
