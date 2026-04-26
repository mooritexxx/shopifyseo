"""keyword_clustering package — re-exports all public symbols for backward compatibility.

Internal layout:
  _helpers.py    — pure utility functions (no DB or AI deps)
  _gaps.py       — SEO gap computation and cluster coverage enrichment
  _storage.py    — cluster DB read/write (load_clusters, migration)
  _context.py    — cluster context lookup for LLM prompts
  _generation.py — AI-driven cluster generation and page-matching
  _crud.py       — CRUD operations (get_match_options, update/get cluster)
"""

from ._crud import get_cluster_detail, get_match_options, update_cluster_match
from ._dedupe import collapse_near_duplicates
from ._gaps import compute_seo_gaps, enrich_clusters_with_coverage
from ._generation import generate_clusters
from ._postprocess import fold_singletons, merge_similar_clusters
from ._planning import parse_keyword_tier, repair_and_enrich_clusters
from ._pre_cluster import pre_cluster
from ._scoring import cluster_priority_score, select_primary_keyword
from ._helpers import (
    _build_clustering_prompt,
    _check_keyword_coverage,
    _compute_cluster_stats,
    _detect_vendor,
    _group_by_parent_topic,
    _keyword_coverage_detail,
)
from ._context import (
    _find_clusters_for_product,
    _format_cluster_context,
    _get_matched_cluster_keywords,
    _load_cluster_context,
)
from ._storage import load_clusters

__all__ = [
    # Public API
    "generate_clusters",
    "collapse_near_duplicates",
    "pre_cluster",
    "merge_similar_clusters",
    "fold_singletons",
    "repair_and_enrich_clusters",
    "parse_keyword_tier",
    "cluster_priority_score",
    "select_primary_keyword",
    "load_clusters",
    "enrich_clusters_with_coverage",
    "compute_seo_gaps",
    "get_match_options",
    "update_cluster_match",
    "get_cluster_detail",
    # Semi-public (used by other services)
    "_get_matched_cluster_keywords",
    "_load_cluster_context",
    "_find_clusters_for_product",
    "_format_cluster_context",
    "_build_clustering_prompt",
    "_check_keyword_coverage",
    "_compute_cluster_stats",
    "_detect_vendor",
    "_group_by_parent_topic",
    "_keyword_coverage_detail",
]
