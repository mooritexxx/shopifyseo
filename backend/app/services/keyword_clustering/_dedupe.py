"""Embedding-based near-duplicate collapse for approved keywords.

Collapses near-identical keyword variants (e.g. "vape pen" / "vape pens") into a
single canonical before the LLM clustering pass sees them. Aliases are returned
alongside so callers can stitch them back onto the winning cluster.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from shopifyseo.dashboard_google import get_service_setting

logger = logging.getLogger(__name__)

DEDUPE_THRESHOLD_KEY = "clustering_dedupe_threshold"
DEFAULT_THRESHOLD = 0.95
MIN_THRESHOLD = 0.80
MAX_THRESHOLD = 1.0


def _resolve_threshold(conn: sqlite3.Connection) -> float:
    raw = get_service_setting(conn, DEDUPE_THRESHOLD_KEY, "")
    if not raw:
        return DEFAULT_THRESHOLD
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid %s=%r; falling back to %.2f",
            DEDUPE_THRESHOLD_KEY, raw, DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD
    if val < MIN_THRESHOLD or val > MAX_THRESHOLD:
        logger.warning(
            "%s=%.3f out of range [%.2f, %.2f]; falling back to %.2f",
            DEDUPE_THRESHOLD_KEY, val, MIN_THRESHOLD, MAX_THRESHOLD, DEFAULT_THRESHOLD,
        )
        return DEFAULT_THRESHOLD
    return val


def _pick_canonical(items: list[dict]) -> dict:
    return sorted(
        items,
        key=lambda it: (
            -float(it.get("opportunity") or 0.0),
            -int(it.get("volume") or 0),
            len(it.get("keyword", "")),
            it.get("keyword", ""),
        ),
    )[0]


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        ri, rj = self.find(i), self.find(j)
        if ri != rj:
            self.parent[ri] = rj


def collapse_near_duplicates(
    approved: list[dict],
    conn: sqlite3.Connection,
    threshold: float | None = None,
) -> tuple[list[dict], dict[str, list[str]]]:
    """Collapse embedding-similar approved keywords into canonical+aliases.

    Returns (canonicals, alias_map) where alias_map[canonical_keyword] is the
    list of absorbed aliases. Keywords without embeddings pass through as
    their own canonical (never dropped).
    """
    if len(approved) < 2:
        return list(approved), {}

    thr = threshold if threshold is not None else _resolve_threshold(conn)
    if thr >= 1.0:
        return list(approved), {}

    try:
        import numpy as np
        from shopifyseo.embedding_store import _load_embedding_matrix
    except Exception:
        logger.warning("numpy/embedding_store unavailable; dedupe passthrough")
        return list(approved), {}

    try:
        matrix, meta = _load_embedding_matrix(conn, object_types=["keyword"])
    except Exception:
        logger.exception("Failed to load keyword embedding matrix; dedupe passthrough")
        return list(approved), {}

    if matrix.shape[0] == 0:
        return list(approved), {}

    handle_to_row: dict[str, int] = {}
    for row_idx, m in enumerate(meta):
        handle = m.get("object_handle") or ""
        if handle and handle not in handle_to_row:
            handle_to_row[handle] = row_idx

    embedded_items: list[dict] = []
    embedded_rows: list[int] = []
    orphans: list[dict] = []
    for item in approved:
        kw = item.get("keyword", "")
        row = handle_to_row.get(kw)
        if row is None:
            orphans.append(item)
        else:
            embedded_items.append(item)
            embedded_rows.append(row)

    if len(embedded_items) < 2:
        return list(approved), {}

    sub = matrix[embedded_rows].astype(np.float32)
    norms = np.linalg.norm(sub, axis=1, keepdims=True) + 1e-10
    sub_norm = sub / norms
    sim = sub_norm @ sub_norm.T

    n = len(embedded_items)
    uf = _UnionFind(n)
    iu, ju = np.triu_indices(n, k=1)
    pairs = np.where(sim[iu, ju] >= thr)[0]
    for p in pairs:
        uf.union(int(iu[p]), int(ju[p]))

    groups: dict[int, list[int]] = {}
    for idx in range(n):
        root = uf.find(idx)
        groups.setdefault(root, []).append(idx)

    canonicals: list[dict] = []
    alias_map: dict[str, list[str]] = {}
    for members in groups.values():
        member_items = [embedded_items[i] for i in members]
        if len(member_items) == 1:
            canonicals.append(member_items[0])
            continue
        canonical = _pick_canonical(member_items)
        canonicals.append(canonical)
        aliases = [
            it["keyword"]
            for it in member_items
            if it["keyword"] != canonical["keyword"]
        ]
        if aliases:
            alias_map[canonical["keyword"]] = aliases

    canonicals.extend(orphans)
    return canonicals, alias_map
