"""Aggregate URL Inspection-derived index status across synced catalog entities."""

from __future__ import annotations

from typing import Any

from shopifyseo.dashboard_status import index_status_bucket_from_strings

_OBJECT_TYPES = ("product", "collection", "page", "blog_article")


def build_indexing_rollup(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up index_status + index_coverage from SEO facts (same rules as detail Index panel)."""
    totals = {"indexed": 0, "not_indexed": 0, "needs_review": 0, "unknown": 0}
    by_type: dict[str, dict[str, int]] = {
        t: {"total": 0, "indexed": 0, "not_indexed": 0, "needs_review": 0, "unknown": 0} for t in _OBJECT_TYPES
    }

    for fact in facts:
        ot = fact.get("object_type") or ""
        if ot not in by_type:
            continue
        bucket = index_status_bucket_from_strings(
            str(fact.get("index_status") or ""),
            str(fact.get("index_coverage") or ""),
        )
        totals[bucket] += 1
        seg = by_type[ot]
        seg["total"] += 1
        seg[bucket] += 1

    return {
        "total": len(facts),
        "indexed": totals["indexed"],
        "not_indexed": totals["not_indexed"],
        "needs_review": totals["needs_review"],
        "unknown": totals["unknown"],
        "by_type": by_type,
    }
