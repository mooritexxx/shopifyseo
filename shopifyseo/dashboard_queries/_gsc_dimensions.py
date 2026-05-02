"""Tier B GSC dimensional rows: query x country|device|searchAppearance.

Read paths for the per-object detail views. Tier A (page+query totals)
lives in the gsc_query_rows table; this module deals with the richer
breakdown table.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ._basic_fetchers import _row_factory


def fetch_gsc_query_dimension_rows(
    conn: sqlite3.Connection, object_type: str, object_handle: str
) -> list[dict[str, Any]]:
    """Rows from gsc_query_dimension_rows (query × country | device | searchAppearance)."""
    cur = _row_factory(conn).execute(
        """
        SELECT query, dimension_kind, dimension_value, clicks, impressions, ctr, position, fetched_at
        FROM gsc_query_dimension_rows
        WHERE object_type = ? AND object_handle = ?
        ORDER BY impressions DESC
        """,
        (object_type, object_handle),
    )
    return [dict(row) for row in cur.fetchall()]


def object_keys_with_dimensional_gsc(
    conn: sqlite3.Connection,
    keys: list[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Return (object_type, object_handle) pairs that have at least one Tier B dimensional row."""
    if not keys:
        return set()
    out: set[tuple[str, str]] = set()
    by_type: dict[str, list[str]] = {}
    for ot, h in keys:
        h = (h or "").strip()
        if not h:
            continue
        by_type.setdefault(ot, []).append(h)
    chunk_size = 400
    conn_rf = _row_factory(conn)
    try:
        for ot, handles in by_type.items():
            uniq = list(dict.fromkeys(handles))
            for i in range(0, len(uniq), chunk_size):
                chunk = uniq[i : i + chunk_size]
                placeholders = ",".join("?" * len(chunk))
                cur = conn_rf.execute(
                    f"""
                    SELECT DISTINCT object_handle FROM gsc_query_dimension_rows
                    WHERE object_type = ? AND object_handle IN ({placeholders})
                    """,
                    (ot, *chunk),
                )
                for row in cur.fetchall():
                    out.add((ot, row["object_handle"]))
    except sqlite3.OperationalError:
        return set()
    return out


def build_gsc_segment_summary_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up cached dimensional GSC rows for product/content detail API + UI."""
    empty: dict[str, Any] = {
        "fetched_at": None,
        "device_mix": [],
        "top_countries": [],
        "search_appearances": [],
        "top_pairs": [],
    }
    if not rows:
        return empty

    fts = [int(r["fetched_at"]) for r in rows if r.get("fetched_at") is not None]
    fetched_at = max(fts) if fts else None

    def rollup_for_kind(kind: str, limit: int) -> tuple[list[dict[str, Any]], int]:
        acc: dict[str, dict[str, int]] = {}
        for r in rows:
            if (r.get("dimension_kind") or "") != kind:
                continue
            v = (r.get("dimension_value") or "").strip()
            if not v:
                continue
            if v not in acc:
                acc[v] = {"clicks": 0, "impressions": 0}
            acc[v]["clicks"] += int(r.get("clicks") or 0)
            acc[v]["impressions"] += int(r.get("impressions") or 0)
        total_imp = sum(x["impressions"] for x in acc.values()) or 1
        items = [
            {
                "segment": k,
                "clicks": v["clicks"],
                "impressions": v["impressions"],
                "share": round(v["impressions"] / total_imp, 4),
            }
            for k, v in acc.items()
        ]
        items.sort(key=lambda x: x["impressions"], reverse=True)
        return items[:limit], total_imp

    device_mix, _ = rollup_for_kind("device", 10)
    top_countries, _ = rollup_for_kind("country", 12)
    search_appearances, _ = rollup_for_kind("searchAppearance", 12)

    sorted_rows = sorted(rows, key=lambda x: int(x.get("impressions") or 0), reverse=True)
    top_pairs = [
        {
            "query": r.get("query") or "",
            "dimension_kind": r.get("dimension_kind") or "",
            "dimension_value": r.get("dimension_value") or "",
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "position": float(r.get("position") or 0),
        }
        for r in sorted_rows[:20]
    ]

    return {
        "fetched_at": fetched_at,
        "device_mix": device_mix,
        "top_countries": top_countries,
        "search_appearances": search_appearances,
        "top_pairs": top_pairs,
    }
