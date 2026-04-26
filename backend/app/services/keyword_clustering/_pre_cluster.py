"""Embedding-based pre-clustering of approved keywords into LLM-sized buckets.

Takes the canonicals (post-dedupe) and splits them into coherent buckets so the
downstream LLM clustering step can run one call per bucket in parallel. Each
bucket is a list of keyword dicts.

Strategy:
  1. Seed buckets from `parent_topic` groups on each keyword. That field is a legacy
     column name: when metrics are ingested via DataForSEO, it is set from
     `keyword_properties.core_keyword` (see `dataforseo_client`). Google Ads–only
     refresh does not populate it, so many rows may be orphans.
  2. For each orphan keyword with an embedding: assign to the bucket whose
     centroid is most similar, if cosine >= assign_threshold.
  3. Remaining orphans with embeddings: union-find merge on pairs with
     cosine >= merge_threshold; each connected component becomes a bucket.
  4. Orphans without embeddings: bundled into one fallback bucket (legacy
     behavior).
  5. If numpy or the embeddings table is missing/empty: returns the same-topic
     buckets plus a single orphan bucket, which is equivalent to chunking only
     by `parent_topic`.
"""
from __future__ import annotations

import logging
import sqlite3

from ._dedupe import _UnionFind
from ._helpers import _group_by_parent_topic

logger = logging.getLogger(__name__)

DEFAULT_ASSIGN_THRESHOLD = 0.7
DEFAULT_MERGE_THRESHOLD = 0.7


def pre_cluster(
    canonicals: list[dict],
    conn: sqlite3.Connection,
    *,
    assign_threshold: float = DEFAULT_ASSIGN_THRESHOLD,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> list[list[dict]]:
    """Split canonicals into buckets for parallel LLM clustering."""
    if not canonicals:
        return []
    if len(canonicals) < 2:
        return [list(canonicals)]

    groups, orphans = _group_by_parent_topic(canonicals)
    seed_buckets: list[list[dict]] = [list(kws) for kws in groups.values()]

    try:
        import numpy as np
        from shopifyseo.embedding_store import _load_embedding_matrix
    except Exception:
        logger.warning("numpy/embedding_store unavailable; pre_cluster uses parent_topic only")
        return _finalize_buckets(seed_buckets, orphans, extra_buckets=[])

    try:
        matrix, meta = _load_embedding_matrix(conn, object_types=["keyword"])
    except Exception:
        logger.exception("Failed to load keyword embedding matrix; pre_cluster falls back")
        return _finalize_buckets(seed_buckets, orphans, extra_buckets=[])

    if matrix.shape[0] == 0:
        return _finalize_buckets(seed_buckets, orphans, extra_buckets=[])

    handle_to_row: dict[str, int] = {}
    for row_idx, m in enumerate(meta):
        handle = m.get("object_handle") or ""
        if handle and handle not in handle_to_row:
            handle_to_row[handle] = row_idx

    def embed(kw: dict):
        row = handle_to_row.get(kw.get("keyword", ""))
        if row is None:
            return None
        v = matrix[row].astype(np.float32)
        return v / (np.linalg.norm(v) + 1e-10)

    centroids: list = []
    for bucket in seed_buckets:
        vecs = [v for v in (embed(k) for k in bucket) if v is not None]
        if not vecs:
            centroids.append(None)
        else:
            c = np.mean(vecs, axis=0)
            centroids.append(c / (np.linalg.norm(c) + 1e-10))

    assigned_extras: list[list[dict]] = [[] for _ in seed_buckets]
    unassigned_embedded: list[dict] = []
    no_embed_orphans: list[dict] = []

    for kw in orphans:
        v = embed(kw)
        if v is None:
            no_embed_orphans.append(kw)
            continue
        best_idx, best_sim = -1, -1.0
        for i, c in enumerate(centroids):
            if c is None:
                continue
            sim = float(v @ c)
            if sim > best_sim:
                best_sim, best_idx = sim, i
        if best_idx >= 0 and best_sim >= assign_threshold:
            assigned_extras[best_idx].append(kw)
        else:
            unassigned_embedded.append(kw)

    merged_seed_buckets = [
        seed + extra for seed, extra in zip(seed_buckets, assigned_extras)
    ]

    extra_buckets: list[list[dict]] = []
    if unassigned_embedded:
        vecs = np.vstack([embed(k) for k in unassigned_embedded])
        n = len(unassigned_embedded)
        sim = vecs @ vecs.T
        uf = _UnionFind(n)
        iu, ju = np.triu_indices(n, k=1)
        for p in np.where(sim[iu, ju] >= merge_threshold)[0]:
            uf.union(int(iu[p]), int(ju[p]))
        groups_by_root: dict[int, list[dict]] = {}
        for idx in range(n):
            groups_by_root.setdefault(uf.find(idx), []).append(unassigned_embedded[idx])
        extra_buckets.extend(groups_by_root.values())

    return _finalize_buckets(merged_seed_buckets, no_embed_orphans, extra_buckets)


def _finalize_buckets(
    seed_buckets: list[list[dict]],
    no_embed_orphans: list[dict],
    extra_buckets: list[list[dict]],
) -> list[list[dict]]:
    out: list[list[dict]] = [b for b in seed_buckets if b]
    out.extend(b for b in extra_buckets if b)
    if no_embed_orphans:
        out.append(no_embed_orphans)
    return out
