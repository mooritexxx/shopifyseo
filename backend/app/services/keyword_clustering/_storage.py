"""Cluster database persistence — read/write clusters and keywords tables."""
import json
import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CLUSTERS_KEY = "keyword_clusters"
TARGET_KEY = "target_keywords"


def _migrate_json_to_db(conn: sqlite3.Connection) -> None:
    """One-time migration: move cluster JSON from service_settings to DB tables.

    Idempotent — only runs if JSON key exists and clusters table is empty.
    """
    row = conn.execute(
        "SELECT value FROM service_settings WHERE key = ?", (CLUSTERS_KEY,)
    ).fetchone()
    if not row or not row[0]:
        return

    count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    if count > 0:
        conn.execute("DELETE FROM service_settings WHERE key = ?", (CLUSTERS_KEY,))
        conn.commit()
        return

    try:
        data = json.loads(row[0])
    except json.JSONDecodeError:
        conn.execute("DELETE FROM service_settings WHERE key = ?", (CLUSTERS_KEY,))
        conn.commit()
        return

    clusters = data.get("clusters") or []
    generated_at = data.get("generated_at") or datetime.now(timezone.utc).isoformat()
    cluster_cols = {row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()}

    for cluster in clusters:
        sm = cluster.get("suggested_match")
        match_type = sm.get("match_type") if sm else None
        match_handle = sm.get("match_handle", "") if sm else None
        match_title = sm.get("match_title", "") if sm else None
        inner_stats = cluster.get("stats") or {}
        dsf = (
            cluster.get("dominant_serp_features")
            or inner_stats.get("dominant_serp_features")
            or ""
        )
        cfh = (
            cluster.get("content_format_hints")
            or inner_stats.get("content_format_hints")
            or ""
        )
        ac = cluster.get("avg_cps")
        if ac is None:
            ac = inner_stats.get("avg_cps")
        avg_cps = float(ac) if ac is not None else 0.0

        if "priority_score" in cluster_cols:
            conn.execute(
                """INSERT INTO clusters
                   (name, content_type, primary_keyword, content_brief,
                    total_volume, avg_difficulty, avg_opportunity, priority_score,
                    dominant_serp_features, content_format_hints, avg_cps,
                    match_type, match_handle, match_title, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cluster.get("name", "Unnamed"),
                    cluster.get("content_type", "blog_post"),
                    cluster.get("primary_keyword", ""),
                    cluster.get("content_brief", ""),
                    cluster.get("total_volume", 0),
                    cluster.get("avg_difficulty", 0.0),
                    cluster.get("avg_opportunity", 0.0),
                    cluster.get("priority_score", cluster.get("avg_opportunity", 0.0)),
                    (dsf or "").strip(),
                    (cfh or "").strip(),
                    avg_cps,
                    match_type,
                    match_handle,
                    match_title,
                    generated_at,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO clusters
                   (name, content_type, primary_keyword, content_brief,
                    total_volume, avg_difficulty, avg_opportunity,
                    dominant_serp_features, content_format_hints, avg_cps,
                    match_type, match_handle, match_title, generated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cluster.get("name", "Unnamed"),
                    cluster.get("content_type", "blog_post"),
                    cluster.get("primary_keyword", ""),
                    cluster.get("content_brief", ""),
                    cluster.get("total_volume", 0),
                    cluster.get("avg_difficulty", 0.0),
                    cluster.get("avg_opportunity", 0.0),
                    (dsf or "").strip(),
                    (cfh or "").strip(),
                    avg_cps,
                    match_type,
                    match_handle,
                    match_title,
                    generated_at,
                ),
            )
        cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for kw in cluster.get("keywords", []):
            conn.execute(
                "INSERT OR IGNORE INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
                (cluster_id, kw),
            )

    conn.execute("DELETE FROM service_settings WHERE key = ?", (CLUSTERS_KEY,))
    conn.commit()


def _cluster_stats_from_row(row: sqlite3.Row) -> dict:
    """Rebuild stats dict for prompts / API from a clusters table row."""
    keys = row.keys()
    stats: dict[str, str | float] = {}
    if "dominant_serp_features" in keys:
        dsf = (row["dominant_serp_features"] or "").strip()
        if dsf:
            stats["dominant_serp_features"] = dsf
    if "content_format_hints" in keys:
        cfh = (row["content_format_hints"] or "").strip()
        if cfh:
            stats["content_format_hints"] = cfh
    if "avg_cps" in keys and row["avg_cps"] is not None:
        stats["avg_cps"] = float(row["avg_cps"])
    return stats


def load_clusters(conn: sqlite3.Connection) -> dict:
    """Load clusters from DB tables. Migrates JSON data on first call if needed."""
    _migrate_json_to_db(conn)

    cluster_cols = {row[1] for row in conn.execute("PRAGMA table_info(clusters)").fetchall()}
    order_expr = (
        "COALESCE(NULLIF(priority_score, 0), avg_opportunity) DESC, avg_opportunity DESC"
        if "priority_score" in cluster_cols
        else "avg_opportunity DESC"
    )
    rows = conn.execute(f"SELECT * FROM clusters ORDER BY {order_expr}").fetchall()

    if not rows:
        return {"clusters": [], "generated_at": None}

    kw_rows = conn.execute("SELECT cluster_id, keyword FROM cluster_keywords").fetchall()
    kw_map: dict[int, list[str]] = {}
    for kw_row in kw_rows:
        kw_map.setdefault(kw_row[0], []).append(kw_row[1])

    clusters = []
    generated_at = None
    for row in rows:
        cluster_id = row["id"]
        match_type = row["match_type"]

        if match_type is None:
            suggested_match = None
        elif match_type == "new":
            suggested_match = {"match_type": "new", "match_handle": "", "match_title": ""}
        else:
            suggested_match = {
                "match_type": match_type,
                "match_handle": row["match_handle"] or "",
                "match_title": row["match_title"] or "",
            }

        keywords = kw_map.get(cluster_id, [])
        cluster_dict = {
            "id": cluster_id,
            "name": row["name"],
            "content_type": row["content_type"],
            "primary_keyword": row["primary_keyword"],
            "content_brief": row["content_brief"],
            "keywords": keywords,
            "keyword_count": len(keywords),
            "total_volume": row["total_volume"],
            "avg_difficulty": row["avg_difficulty"],
            "avg_opportunity": row["avg_opportunity"],
            "priority_score": (
                row["priority_score"] if "priority_score" in cluster_cols and row["priority_score"] else row["avg_opportunity"]
            ),
            "suggested_match": suggested_match,
        }
        stats = _cluster_stats_from_row(row)
        if stats:
            cluster_dict["stats"] = stats
        clusters.append(cluster_dict)
        if generated_at is None:
            generated_at = row["generated_at"]

    return {"clusters": clusters, "generated_at": generated_at}
