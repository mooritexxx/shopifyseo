"""Write-time internal linking helpers for blog draft and AI body generation.

Phase C: Shared module for selecting internal link targets during content creation.
Uses the same scoring philosophy as the suggestion pipeline:
similarity × commercial target value × orphan boost.
"""
from __future__ import annotations

import logging
import math
import sqlite3
from typing import Any

from .pipeline import TARGET_VALUE, ORPHAN_BOOST, _orphan_target_set, _get_sim_threshold

logger = logging.getLogger(__name__)


def select_write_time_links(
    conn: sqlite3.Connection,
    source_type: str,
    source_handle: str,
    top_k: int = 5,
    min_score: float = 0.0,
    exclude_handles: set[str] | None = None,
    prefer_orphans: bool = True,
    prefer_commercial: bool = True,
) -> list[dict[str, Any]]:
    """Select internal link targets for write-time insertion (draft/AI body).
    
    Uses the same scoring logic as the suggestion pipeline:
    - similarity × traffic_weight × target_value × orphan_boost
    
    Args:
        conn: Database connection
        source_type: Type of source object ('blog_article', 'product', 'collection')
        source_handle: Handle of the source object
        top_k: Maximum number of links to return
        min_score: Minimum score threshold (default: similarity threshold from settings)
        exclude_handles: Set of handles to exclude from results
        prefer_orphans: Apply orphan boost to scoring
        prefer_commercial: Prefer product/collection targets over articles/pages
        
    Returns:
        List of dicts with: object_type, object_handle, title, url, score
    """
    from ..embedding_store import retrieve_related_by_handle
    from ..dashboard_queries._urls import object_url_with_base, _base_store_url
    
    if min_score <= 0:
        min_score = _get_sim_threshold(conn)
    
    exclude_handles = exclude_handles or set()
    base_url = _base_store_url(conn)
    
    try:
        related = retrieve_related_by_handle(conn, source_type, source_handle, top_k=top_k * 3)
    except Exception:
        logger.warning("retrieve_related_by_handle failed for %s/%s", source_type, source_handle, exc_info=True)
        return []
    
    orphans = _orphan_target_set(conn) if prefer_orphans else set()
    
    scored: list[dict[str, Any]] = []
    for cand in related:
        t_type = cand.get("object_type")
        t_handle = cand.get("object_handle")
        sim = float(cand.get("score") or 0)
        
        if t_type not in TARGET_VALUE or sim < min_score:
            continue
        if (source_type, source_handle) == (t_type, t_handle):
            continue
        if t_handle in exclude_handles:
            continue
        
        # Verify target exists and is reachable
        title = _get_target_title(conn, t_type, t_handle)
        if not title:
            continue
        
        # Commercial bias: products and collections are more valuable targets
        type_weight = TARGET_VALUE[t_type]
        if prefer_commercial and t_type in ("product", "collection"):
            type_weight *= 1.2
        
        score = sim * type_weight
        if prefer_orphans and (t_type, t_handle) in orphans:
            score *= ORPHAN_BOOST
        
        url = object_url_with_base(base_url, t_type, t_handle)
        scored.append({
            "object_type": t_type,
            "object_handle": t_handle,
            "title": title,
            "url": url,
            "score": score,
        })
    
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


def _get_target_title(conn: sqlite3.Connection, t_type: str, t_handle: str) -> str | None:
    """Get title for a target, returning None if not found or unreachable."""
    if t_type == "product":
        row = conn.execute(
            "SELECT title FROM products WHERE handle = ? AND (status IS NULL OR status = '' OR UPPER(status) = 'ACTIVE')",
            (t_handle,),
        ).fetchone()
    elif t_type == "collection":
        row = conn.execute(
            "SELECT title FROM collections WHERE handle = ? AND COALESCE(api_unreachable, 0) = 0",
            (t_handle,),
        ).fetchone()
    elif t_type == "page":
        row = conn.execute("SELECT title FROM pages WHERE handle = ?", (t_handle,)).fetchone()
    elif t_type == "blog_article":
        blog_h, _, article_h = t_handle.partition("/")
        row = conn.execute(
            "SELECT title FROM blog_articles WHERE blog_handle = ? AND handle = ? AND is_published = 1",
            (blog_h, article_h),
        ).fetchone()
    else:
        return None
    
    return row["title"] if row else None


def format_links_for_prompt(links: list[dict[str, Any]], max_links: int = 5) -> str:
    """Format selected links for inclusion in an AI prompt.
    
    Returns a JSON-like string suitable for prompt injection.
    """
    import json
    
    formatted = []
    for link in links[:max_links]:
        formatted.append({
            "type": link["object_type"],
            "title": link["title"],
            "url": link["url"],
        })
    
    return json.dumps(formatted, ensure_ascii=True)


def get_minimum_link_count(
    conn: sqlite3.Connection,
    content_type: str = "blog_article",
    has_primary_link: bool = False,
) -> int:
    """Get minimum internal link count for compliance.
    
    Phase C: Commercial guides should have ≥3 deep catalog links when candidates exist.
    
    Args:
        conn: Database connection
        content_type: Type of content being generated
        has_primary_link: Whether a primary link target is already specified
        
    Returns:
        Minimum number of internal links required
    """
    base = 1 if has_primary_link else 0
    
    if content_type == "blog_article":
        # Blog articles should have 2-4 internal links
        return base + 2
    elif content_type in ("product", "collection"):
        # Product/collection AI bodies: optional, but 1-2 if enabled
        return base + 1
    
    return base + 1


def prioritize_targets_for_write_time(
    conn: sqlite3.Connection,
    link_targets: list[dict],
) -> list[dict]:
    """Reorder link targets using internal-link pipeline scoring philosophy.
    
    Phase C: Called during article draft generation to prioritize orphan pages
    and high-value commercial targets in the approved_internal_link_targets list.
    
    Scoring adjustments:
    - Orphan pages get +0.5 boost (moved to top of their type group)
    - Products and collections are preferred over pages
    
    Args:
        conn: Database connection
        link_targets: List of dicts with keys: type, handle, title, url
        
    Returns:
        Reordered copy of link_targets with orphans prioritized
    """
    if not link_targets:
        return link_targets
    
    try:
        orphans = _orphan_target_set(conn)
    except Exception:
        logger.debug("Could not compute orphan set for write-time prioritization", exc_info=True)
        return link_targets
    
    def _target_key(t: dict) -> tuple:
        t_type = t.get("type", "")
        t_handle = t.get("handle", "")
        
        # Priority 1: Type ordering (products > collections > blog_article > page)
        type_priority = {"product": 0, "collection": 1, "blog_article": 2, "page": 3}.get(t_type, 4)
        
        # Priority 2: Orphan status (orphans first within type)
        is_orphan = (t_type, t_handle) in orphans
        orphan_priority = 0 if is_orphan else 1
        
        # Priority 3: Original order (lower index = higher priority)
        return (type_priority, orphan_priority, t.get("title", "").lower())
    
    return sorted(link_targets, key=_target_key)
