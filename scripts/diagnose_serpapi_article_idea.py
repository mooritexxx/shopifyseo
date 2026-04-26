#!/usr/bin/env python3
"""Print SerpAPI snapshot diagnostics for an article idea (same code path as Refresh SERP data).

Usage (from repo root):
  PYTHONPATH=. python scripts/diagnose_serpapi_article_idea.py
  PYTHONPATH=. python scripts/diagnose_serpapi_article_idea.py --idea-id 65
  PYTHONPATH=. python scripts/diagnose_serpapi_article_idea.py --keyword "pod vape"

If ``raw['error']`` is set, the app still returns HTTP 200 from refresh but stores empty
JSON — that matches an empty UI with a generic success banner.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _mask(s: str, keep: int = 4) -> str:
    if not s:
        return "(empty)"
    if len(s) <= keep * 2:
        return "***"
    return s[:keep] + "…" + s[-keep:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--idea-id", type=int, default=None, help="article_ideas.id (uses primary_keyword)")
    parser.add_argument("--keyword", type=str, default="", help="override query (skip idea row)")
    parser.add_argument("--db", type=Path, default=None, help="SQLite path (default: app DB_PATH)")
    parser.add_argument("--raw-json", action="store_true", help="print first 3000 chars of SerpAPI JSON (no key values)")
    args = parser.parse_args()

    from shopifyseo.audience_questions_api import (
        _serpapi_fetch_google_serp_snapshot,
        fetch_serpapi_primary_keyword_snapshot,
    )
    from shopifyseo import dashboard_google as dg
    import shopifyseo.market_context as mc
    from shopifyseo.dashboard_store import DB_PATH, ensure_dashboard_schema

    db_path = args.db or DB_PATH
    print(f"Database: {db_path}")
    if not Path(db_path).is_file():
        print("ERROR: database file not found. Set --db or SHOPIFY_CATALOG_DB_PATH.")
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)

    pk = (args.keyword or "").strip()
    if not pk and args.idea_id is not None:
        row = conn.execute(
            "SELECT id, primary_keyword, suggested_title FROM article_ideas WHERE id = ?",
            (args.idea_id,),
        ).fetchone()
        if not row:
            print(f"ERROR: no article_ideas row for id={args.idea_id}")
            conn.close()
            return 1
        pk = (row["primary_keyword"] or "").strip()
        print(f"Idea id={row['id']}")
        print(f"  suggested_title: {row['suggested_title']!r}")
        print(f"  primary_keyword: {pk!r}")
    elif not pk:
        print("ERROR: pass --idea-id N or --keyword \"...\"")
        conn.close()
        return 1

    key = (dg.get_service_setting(conn, "serpapi_api_key") or "").strip()
    print(f"\nserpapi_api_key (from service_settings): {_mask(key)}  (len={len(key)})")
    if not key:
        print(
            "\n>>> No key in DB — refresh will save empty lists. Add under Settings → Integrations.\n"
        )

    loc = mc.serpapi_google_search_params(conn)
    print(f"Localization (gl, hl, google_domain): {loc}")

    qa, pages, aio, rel, err, raw, loc_used = _serpapi_fetch_google_serp_snapshot(
        key, pk, localization=loc
    )
    print("\n=== First HTTP: engine=google (same as refresh) ===")
    print(f"localization used for main SERP + PAA expansion: {loc_used}")
    print(f"internal error string: {err!r}")
    print(f"parsed audience_questions: {len(qa)}")
    print(f"parsed top_ranking_pages: {len(pages)}")
    print(f"parsed related_searches: {len(rel)}")
    print(f"ai_overview present: {aio is not None}")

    if isinstance(raw, dict):
        api_err = raw.get("error")
        print(f"\nraw JSON 'error' field: {api_err!r}")
        if api_err and str(api_err).strip():
            print(
                ">>> SerpAPI returned an error in the JSON body (often still HTTP 200). "
                "The app then stores empty PAA/organics — UI looks blank.\n"
            )
        for block in ("related_questions", "organic_results", "inline_images"):
            b = raw.get(block)
            print(f"raw['{block}']: type={type(b).__name__} len={len(b) if isinstance(b, list) else 'n/a'}")

        tokens = 0
        for item in (raw.get("related_questions") or []):
            if isinstance(item, dict) and (item.get("next_page_token") or "").strip():
                tokens += 1
        print(f"PAA items with next_page_token: {tokens}")

        if args.raw_json:
            redacted = {k: v for k, v in raw.items() if k != "search_metadata"}
            blob = json.dumps(redacted, ensure_ascii=False, indent=2)[:3000]
            print(f"\n--- raw response (trimmed, {len(blob)} chars) ---\n{blob}")

    snap = fetch_serpapi_primary_keyword_snapshot(conn, pk, expand_paa=True)
    print("\n=== fetch_serpapi_primary_keyword_snapshot(expand_paa=True) ===")
    se = snap.get("serpapi_error") if isinstance(snap, dict) else None
    if se:
        print(f"  serpapi_error (refresh would fail with this): {se!r}")
    print(f"  audience_questions: {len(snap.get('audience_questions') or [])}")
    print(f"  top_ranking_pages: {len(snap.get('top_ranking_pages') or [])}")
    print(f"  related_searches: {len(snap.get('related_searches') or [])}")
    print(f"  paa_expansion layers: {len(snap.get('paa_expansion') or [])}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
