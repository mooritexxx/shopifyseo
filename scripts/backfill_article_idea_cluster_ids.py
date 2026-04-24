#!/usr/bin/env python3
"""Backfill article_ideas.linked_cluster_id for rows where it is NULL.

Uses exact, deterministic matches only (no title heuristics):
  1) clusters.primary_keyword equals idea.primary_keyword (case-insensitive, trimmed)
  2) cluster_keywords.keyword equals idea.primary_keyword (same normalization)
  3) If idea.linked_cluster_name is non-empty: clusters.name equals it (same normalization)

If step 1 returns multiple cluster ids, the smallest id wins. Step 2 uses the matched row's cluster_id.

Dry-run by default; pass --apply to write. When linked_cluster_name is empty, it is filled from
the matched cluster's name (existing non-empty names are left unchanged).
"""

import argparse
import sqlite3
from pathlib import Path


def default_db_path() -> Path:
    return Path(__file__).resolve().parents[1] / "shopify_catalog.sqlite3"


def resolve_cluster_id(
    conn: sqlite3.Connection, pk: str, linked_name: str
) -> int | None:
    cur = conn.execute(
        """
        SELECT MIN(id) FROM clusters
        WHERE LOWER(TRIM(primary_keyword)) = LOWER(TRIM(?))
        """,
        (pk,),
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        return int(row[0])

    cur = conn.execute(
        """
        SELECT cluster_id FROM cluster_keywords
        WHERE LOWER(TRIM(keyword)) = LOWER(TRIM(?))
        ORDER BY cluster_id ASC
        LIMIT 1
        """,
        (pk,),
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        return int(row[0])

    name = (linked_name or "").strip()
    if not name:
        return None

    cur = conn.execute(
        """
        SELECT MIN(id) FROM clusters
        WHERE LOWER(TRIM(name)) = LOWER(TRIM(?))
        """,
        (name,),
    )
    row = cur.fetchone()
    if row and row[0] is not None:
        return int(row[0])

    return None


def cluster_display_name(conn: sqlite3.Connection, cluster_id: int) -> str:
    cur = conn.execute("SELECT name FROM clusters WHERE id = ?", (cluster_id,))
    row = cur.fetchone()
    return (row[0] or "") if row else ""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=default_db_path(), help="SQLite catalog path")
    p.add_argument(
        "--apply",
        action="store_true",
        help="Perform updates (default: dry-run listing only)",
    )
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        """
        SELECT id, primary_keyword, linked_cluster_name
        FROM article_ideas
        WHERE linked_cluster_id IS NULL
        ORDER BY id
        """
    )
    orphans = cur.fetchall()

    planned: list[tuple[int, int, str]] = []
    unmapped: list[tuple[int, str]] = []

    for r in orphans:
        iid = int(r["id"])
        pk = r["primary_keyword"] or ""
        lname = r["linked_cluster_name"] or ""
        cid = resolve_cluster_id(conn, pk, lname)
        if cid is None:
            unmapped.append((iid, pk))
        else:
            planned.append((iid, cid, cluster_display_name(conn, cid)))

    print(f"Orphans (linked_cluster_id IS NULL): {len(orphans)}")
    print(f"Mappable with exact rules: {len(planned)}")
    print(f"Still unmapped: {len(unmapped)}")

    for iid, cid, cname in planned:
        print(f"  idea {iid} -> cluster {cid} ({cname!r})")

    if unmapped:
        print("Unmapped (needs manual link or new cluster keyword):")
        for iid, pk in unmapped:
            print(f"  idea {iid}: {pk!r}")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to write.")
        return

    for iid, cid, cname in planned:
        conn.execute(
            """
            UPDATE article_ideas
            SET linked_cluster_id = ?,
                linked_cluster_name = CASE
                    WHEN TRIM(COALESCE(linked_cluster_name, '')) = '' THEN ?
                    ELSE linked_cluster_name
                END
            WHERE id = ? AND linked_cluster_id IS NULL
            """,
            (cid, cname, iid),
        )
    conn.commit()
    print(f"\nApplied {len(planned)} update(s).")


if __name__ == "__main__":
    main()
