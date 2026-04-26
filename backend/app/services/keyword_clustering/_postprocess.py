"""Post-processing for LLM-generated clusters.

Two passes, both driven by cosine similarity over Gemini embeddings of each
cluster's primary_keyword:

  1. `merge_similar_clusters` — collapse clusters whose primary_keywords are
     near-identical in embedding space. Handles the case where parallel buckets
     each generated a cluster for what should have been one page.

  2. `fold_singletons` — fold single-keyword clusters into the most similar
     non-singleton neighbor when the similarity is high enough. Prevents the
     "hundreds of one-keyword clusters" problem without dropping approved
     keywords that are genuinely unique (no similar neighbor exists).

Both passes degrade gracefully when numpy or the embeddings table is
unavailable and leave clusters with missing primary-keyword embeddings alone.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from shopifyseo.dashboard_google import get_service_setting

from ._dedupe import _UnionFind
from ._helpers import _compute_cluster_stats
from ._planning import clusters_can_merge, load_entity_rules

logger = logging.getLogger(__name__)

CLUSTER_MERGE_THRESHOLD_KEY = "clustering_merge_threshold"
CLUSTER_FOLD_THRESHOLD_KEY = "clustering_fold_threshold"
DEFAULT_MERGE_THRESHOLD = 0.85
DEFAULT_FOLD_THRESHOLD = 0.75
MIN_THRESHOLD = 0.50
MAX_THRESHOLD = 1.0


def _resolve_threshold(conn: sqlite3.Connection, key: str, default: float) -> float:
    raw = get_service_setting(conn, key, "")
    if not raw:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %.2f", key, raw, default)
        return default
    if val < MIN_THRESHOLD or val > MAX_THRESHOLD:
        logger.warning(
            "%s=%.3f out of range [%.2f, %.2f]; using %.2f",
            key, val, MIN_THRESHOLD, MAX_THRESHOLD, default,
        )
        return default
    return val


def _load_primary_vectors(clusters: list[dict], conn: sqlite3.Connection):
    """Return (numpy_module, list[vec | None]) keyed positionally to clusters.

    Returns (None, None) if numpy or the embeddings table is unavailable.
    """
    try:
        import numpy as np
        from shopifyseo.embedding_store import _load_embedding_matrix
    except Exception:
        return None, None
    try:
        matrix, meta = _load_embedding_matrix(conn, object_types=["keyword"])
    except Exception:
        logger.exception("Failed to load keyword embedding matrix; post-process passthrough")
        return None, None
    if matrix.shape[0] == 0:
        return None, None

    handle_to_row: dict[str, int] = {}
    for i, m in enumerate(meta):
        handle = (m.get("object_handle") or "").lower()
        if handle and handle not in handle_to_row:
            handle_to_row[handle] = i

    vecs: list[Any] = []
    for c in clusters:
        pk = (c.get("primary_keyword") or "").lower().strip()
        row = handle_to_row.get(pk)
        if row is None:
            vecs.append(None)
            continue
        v = matrix[row].astype(np.float32)
        vecs.append(v / (np.linalg.norm(v) + 1e-10))
    return np, vecs


def _merge_two(winner: dict, loser: dict, keywords_map: dict[str, dict]) -> dict:
    seen: set[str] = set()
    merged_keywords: list[str] = []
    for k in (winner.get("keywords") or []) + (loser.get("keywords") or []):
        kl = k.lower()
        if kl not in seen:
            seen.add(kl)
            merged_keywords.append(k)
    stats = _compute_cluster_stats(
        [k.lower() for k in merged_keywords], keywords_map
    )
    return {**winner, "keywords": merged_keywords, **stats}


def merge_similar_clusters(
    clusters: list[dict],
    conn: sqlite3.Connection,
    keywords_map: dict[str, dict],
    *,
    threshold: float | None = None,
) -> list[dict]:
    """Collapse clusters whose primary_keyword embeddings are cos-similar.

    Winner per group = highest priority_score (falling back to avg_opportunity).
    Keyword lists are unioned and
    stats recomputed. Uses union-find so transitive similarity chains merge.
    """
    if len(clusters) < 2:
        return list(clusters)

    thr = threshold if threshold is not None else _resolve_threshold(
        conn, CLUSTER_MERGE_THRESHOLD_KEY, DEFAULT_MERGE_THRESHOLD
    )
    if thr >= 1.0:
        return list(clusters)

    np_mod, vecs = _load_primary_vectors(clusters, conn)
    if np_mod is None:
        return list(clusters)

    indexed = [i for i, v in enumerate(vecs) if v is not None]
    if len(indexed) < 2:
        return list(clusters)

    sub = np_mod.vstack([vecs[i] for i in indexed])
    sim = sub @ sub.T
    uf = _UnionFind(len(indexed))
    iu, ju = np_mod.triu_indices(len(indexed), k=1)
    entity_rules = load_entity_rules(conn)
    for p in np_mod.where(sim[iu, ju] >= thr)[0]:
        left_idx = indexed[int(iu[p])]
        right_idx = indexed[int(ju[p])]
        if clusters_can_merge(clusters[left_idx], clusters[right_idx], keywords_map, entity_rules):
            uf.union(int(iu[p]), int(ju[p]))

    groups: dict[int, list[int]] = {}
    for local in range(len(indexed)):
        groups.setdefault(uf.find(local), []).append(indexed[local])

    absorbed: set[int] = set()
    merged: list[dict] = []
    for members in groups.values():
        if len(members) == 1:
            continue
        group_clusters = [clusters[i] for i in members]
        winner = max(
            group_clusters,
            key=lambda c: float(c.get("priority_score") or c.get("avg_opportunity") or 0.0),
        )
        result = winner
        for other in group_clusters:
            if other is winner:
                continue
            result = _merge_two(result, other, keywords_map)
        merged.append(result)
        absorbed.update(members)

    out: list[dict] = []
    added_merged = False
    for i, c in enumerate(clusters):
        if i in absorbed:
            if not added_merged:
                out.extend(merged)
                added_merged = True
            continue
        out.append(c)
    if not added_merged and merged:
        out.extend(merged)
    return out


def fold_singletons(
    clusters: list[dict],
    conn: sqlite3.Connection,
    keywords_map: dict[str, dict],
    *,
    threshold: float | None = None,
) -> list[dict]:
    """Fold 1-keyword clusters into the most similar non-singleton neighbor.

    Singletons with no similar non-singleton neighbor (cos < threshold) remain
    untouched, so genuinely unique keywords are not dropped.
    """
    if len(clusters) < 2:
        return list(clusters)

    thr = threshold if threshold is not None else _resolve_threshold(
        conn, CLUSTER_FOLD_THRESHOLD_KEY, DEFAULT_FOLD_THRESHOLD
    )
    if thr >= 1.0:
        return list(clusters)

    singletons: list[int] = []
    targets: list[int] = []
    for i, c in enumerate(clusters):
        if len(c.get("keywords") or []) <= 1:
            singletons.append(i)
        else:
            targets.append(i)

    if not singletons or not targets:
        return list(clusters)

    np_mod, vecs = _load_primary_vectors(clusters, conn)
    if np_mod is None:
        return list(clusters)

    target_rows = [i for i in targets if vecs[i] is not None]
    if not target_rows:
        return list(clusters)
    target_matrix = np_mod.vstack([vecs[i] for i in target_rows])
    entity_rules = load_entity_rules(conn)

    target_lookup: dict[int, dict] = {i: dict(clusters[i]) for i in targets}
    absorbed: set[int] = set()

    for si in singletons:
        v = vecs[si]
        if v is None:
            continue
        sims = target_matrix @ v
        best_local = int(np_mod.argmax(sims))
        best_sim = float(sims[best_local])
        if best_sim < thr:
            continue
        best_target = target_rows[best_local]
        if not clusters_can_merge(clusters[best_target], clusters[si], keywords_map, entity_rules):
            continue
        target_lookup[best_target] = _merge_two(
            target_lookup[best_target], clusters[si], keywords_map
        )
        absorbed.add(si)

    out: list[dict] = []
    for i, c in enumerate(clusters):
        if i in absorbed:
            continue
        out.append(target_lookup.get(i, c))
    return out
