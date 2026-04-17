# Article Idea Generator Enrichment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich the article idea generator with all available DB signals — new data fetching, richer prompt context, expanded output schema, and updated UI.

**Architecture:** 6 sequential tasks touching the full stack: DB schema → data fetching → prompt engineering → save/fetch persistence → backend API schema → frontend UI. Each task is independently commitable and builds on the last.

**Tech Stack:** Python/SQLite (shopifyseo/), FastAPI/Pydantic (backend/), React/TypeScript/Zod (frontend/)

**Test runner:** `PYTHONPATH=. /opt/anaconda3/bin/pytest <test_file> -v`

**Frontend build:** `cd frontend && npm run rebuild` (run after any frontend change)

**Backend restart:** Kill port 8000, then `PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000`

---

## Files Changed

| File | Change |
|------|--------|
| `shopifyseo/dashboard_store.py` | Add 9 new columns to `article_ideas` table via `_ensure_columns` |
| `shopifyseo/dashboard_queries.py` | Enrich `fetch_article_idea_inputs()`, `save_article_ideas()`, `fetch_article_ideas()` |
| `shopifyseo/dashboard_ai_engine_parts/generation.py` | Enrich prompt, expand JSON output schema, update cleaning logic |
| `backend/app/schemas/article_ideas.py` | Add 9 new fields to `ArticleIdeaItem` Pydantic model |
| `frontend/src/types/api.ts` | Add 9 new fields to `articleIdeaSchema` Zod schema |
| `frontend/src/routes/article-ideas-page.tsx` | Display new fields in `IdeaCard`, update generate button text |
| `tests/test_article_idea_inputs.py` | Extend with tests for all new data fields |
| `tests/test_article_idea_save_fetch.py` | New: round-trip test for save/fetch with new columns |

---

## Task 1: DB Schema — Add New Columns to `article_ideas`

**Files:**
- Modify: `shopifyseo/dashboard_store.py` (around line 368, after the `CREATE TABLE IF NOT EXISTS article_ideas` block)
- Test: `tests/test_article_idea_inputs.py`

The `article_ideas` table currently stores no quantitative metrics. We add 9 columns that will be populated at generation time. All use `_ensure_columns` so existing rows get NULLs that default to `0`/`''`.

- [ ] **Step 1: Write the failing test**

Add this test to `tests/test_article_idea_inputs.py` (after the existing imports and fixture):

```python
def test_article_ideas_schema_has_new_columns(idea_conn: sqlite3.Connection):
    cols = {
        row[1]
        for row in idea_conn.execute("PRAGMA table_info(article_ideas)").fetchall()
    }
    for expected in [
        "total_volume", "avg_difficulty", "opportunity_score",
        "dominant_serp_features", "content_format_hints",
        "content_format", "source_type", "linked_keywords_json",
        "estimated_monthly_traffic",
    ]:
        assert expected in cols, f"Missing column: {expected}"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_article_ideas_schema_has_new_columns -v
```

Expected: FAILED — columns don't exist yet.

- [ ] **Step 3: Add the `_ensure_columns` call in `ensure_dashboard_schema`**

In `shopifyseo/dashboard_store.py`, find the line `conn.commit()` at the end of `ensure_dashboard_schema` (around line 369). Insert this block **immediately before** that `conn.commit()`:

```python
    _ensure_columns(
        conn,
        "article_ideas",
        {
            "total_volume": "INTEGER NOT NULL DEFAULT 0",
            "avg_difficulty": "REAL NOT NULL DEFAULT 0.0",
            "opportunity_score": "REAL NOT NULL DEFAULT 0.0",
            "dominant_serp_features": "TEXT NOT NULL DEFAULT ''",
            "content_format_hints": "TEXT NOT NULL DEFAULT ''",
            "content_format": "TEXT NOT NULL DEFAULT ''",
            "source_type": "TEXT NOT NULL DEFAULT 'cluster_gap'",
            "linked_keywords_json": "TEXT NOT NULL DEFAULT '[]'",
            "estimated_monthly_traffic": "INTEGER NOT NULL DEFAULT 0",
        },
    )
```

- [ ] **Step 4: Run tests to confirm both pass**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/dashboard_store.py tests/test_article_idea_inputs.py
git commit -m "feat(article-ideas): add 9 enrichment columns to article_ideas table"
```

---

## Task 2: Data Fetching — Enrich `fetch_article_idea_inputs()`

**Files:**
- Modify: `shopifyseo/dashboard_queries.py` (function `fetch_article_idea_inputs`, lines 964–1273)
- Test: `tests/test_article_idea_inputs.py`

This task wires up all new data sources to the fetching layer. Make each change in sequence within the single function.

### 2a — Add `word_count` and `first_seen` to top_keywords per cluster

- [ ] **Step 1: Write failing test**

Add to `tests/test_article_idea_inputs.py`:

```python
def test_top_keywords_have_word_count_and_first_seen(idea_conn: sqlite3.Connection):
    conn = idea_conn
    conn.execute(
        """
        INSERT INTO clusters
          (name, content_type, primary_keyword, content_brief,
           total_volume, avg_difficulty, avg_opportunity,
           match_type, match_handle, match_title, generated_at)
        VALUES ('WC Cluster', 'blog_post', 'wc kw', 'Brief.',
                1000, 20.0, 60.0, NULL, NULL, NULL, '2026-01-01T00:00:00Z')
        """
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cid, "wc kw"))
    conn.execute(
        """
        INSERT INTO keyword_metrics
          (keyword, volume, difficulty, opportunity, word_count, first_seen,
           traffic_potential, global_volume, updated_at)
        VALUES ('wc kw', 500, 15, 40.0, 1800, '2024-01-01', 350, 9200, 0)
        """
    )
    conn.commit()
    data = fetch_article_idea_inputs(conn)
    cg = next(c for c in data["cluster_gaps"] if c["name"] == "WC Cluster")
    kw = next(k for k in cg["top_keywords"] if k["keyword"] == "wc kw")
    assert kw["word_count"] == 1800
    assert kw["first_seen"] == "2024-01-01"
    assert kw["traffic_potential"] == 350
    assert kw["global_volume"] == 9200
    conn.close()
```

- [ ] **Step 2: Run to confirm it fails**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_top_keywords_have_word_count_and_first_seen -v
```

Expected: FAILED — `word_count` key missing from keyword dict.

- [ ] **Step 3: Update the per-cluster keyword SELECT**

In `shopifyseo/dashboard_queries.py`, find the inner `conn.execute` that selects cluster keywords (around line 1016). Replace that SQL and the `top_keywords` dict builder:

The SQL currently ends with `km.serp_features`. Add four more columns — `word_count`, `first_seen`, `traffic_potential`, `global_volume`:

```python
        kw_rows = conn.execute(
            """
            SELECT ck.keyword,
                   COALESCE(km.volume, 0)               AS volume,
                   COALESCE(km.difficulty, 0)            AS difficulty,
                   COALESCE(km.cpc, 0.0)                 AS cpc,
                   COALESCE(km.intent, 'informational')  AS intent,
                   COALESCE(km.ranking_status, 'not_ranking') AS ranking_status,
                   km.gsc_position,
                   COALESCE(km.opportunity, 0.0)         AS opportunity,
                   km.clicks,
                   km.cps,
                   km.content_format_hint,
                   km.serp_features,
                   km.word_count,
                   km.first_seen,
                   COALESCE(km.traffic_potential, 0)     AS traffic_potential,
                   COALESCE(km.global_volume, 0)         AS global_volume
            FROM cluster_keywords ck
            LEFT JOIN keyword_metrics km ON LOWER(km.keyword) = LOWER(ck.keyword)
            WHERE ck.cluster_id = ?
            ORDER BY km.opportunity DESC NULLS LAST
            LIMIT 5
            """,
            (cluster_id,),
        ).fetchall()
```

Update the `top_keywords` list comprehension (around line 1039). Column indices: [0]=keyword [1]=volume [2]=difficulty [3]=cpc [4]=intent [5]=ranking_status [6]=gsc_position [7]=opportunity [8]=clicks [9]=cps [10]=content_format_hint [11]=serp_features [12]=word_count [13]=first_seen [14]=traffic_potential [15]=global_volume:

```python
        top_keywords = [
            {
                "keyword": kw[0],
                "volume": int(kw[1] or 0),
                "difficulty": int(kw[2] or 0),
                "cpc": round(float(kw[3] or 0), 2),
                "intent": kw[4],
                "ranking_status": kw[5],
                "gsc_position": round(float(kw[6]), 1) if kw[6] is not None else None,
                "clicks": round(float(kw[8] or 0), 1) if kw[8] is not None else None,
                "cps": round(float(kw[9] or 0), 2) if kw[9] is not None else None,
                "content_format_hint": kw[10] or "",
                "serp_features_compact": kw[11][:80] if kw[11] else "",
                "word_count": int(kw[12]) if kw[12] is not None else None,
                "first_seen": kw[13] or None,
                "traffic_potential": int(kw[14] or 0),
                "global_volume": int(kw[15] or 0),
            }
            for kw in kw_rows
        ]
```

- [ ] **Step 4: Run the new test to confirm it passes**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_top_keywords_have_word_count_and_first_seen -v
```

Expected: PASSED.

### 2b — Add `competitor_position` and `competitor_url` to competitor gaps

- [ ] **Step 5: Write failing test**

Add to `tests/test_article_idea_inputs.py`:

```python
def test_competitor_gaps_have_position_and_url(idea_conn: sqlite3.Connection):
    conn = idea_conn
    conn.execute(
        """
        INSERT INTO competitor_keyword_gaps
          (keyword, competitor_domain, volume, difficulty, traffic_potential,
           gap_type, competitor_position, competitor_url, updated_at)
        VALUES ('rival kw', 'rival.com', 300, 25, 100, 'they_rank_we_dont', 3, 'https://rival.com/page', 0)
        """
    )
    conn.execute(
        """
        INSERT INTO keyword_metrics (keyword, volume, difficulty, intent, opportunity, updated_at)
        VALUES ('rival kw', 300, 25, 'informational', 60.0, 0)
        """
    )
    conn.commit()
    data = fetch_article_idea_inputs(conn)
    gaps = data["competitor_gaps"]
    assert any(g["keyword"] == "rival kw" for g in gaps)
    gap = next(g for g in gaps if g["keyword"] == "rival kw")
    assert gap["competitor_position"] == 3
    assert gap["competitor_url"] == "https://rival.com/page"
    conn.close()
```

- [ ] **Step 6: Run to confirm it fails**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_competitor_gaps_have_position_and_url -v
```

Expected: FAILED.

- [ ] **Step 7: Update competitor_keyword_gaps SELECT**

In `fetch_article_idea_inputs`, find the competitor_gap_rows query (around line 1177). Replace with:

```python
        competitor_gap_rows = conn.execute(
            """
            SELECT ckg.keyword, ckg.competitor_domain, ckg.volume,
                   ckg.difficulty, ckg.traffic_potential, ckg.gap_type,
                   km.content_format_hint, km.intent,
                   ckg.competitor_position, ckg.competitor_url
            FROM competitor_keyword_gaps ckg
            LEFT JOIN keyword_metrics km ON LOWER(ckg.keyword) = LOWER(km.keyword)
            WHERE COALESCE(km.intent, 'informational') = 'informational'
              AND ckg.volume > 50
            ORDER BY ckg.volume DESC
            LIMIT 40
            """
        ).fetchall()
        raw_competitor = [
            {
                "keyword": r[0],
                "competitor_domain": r[1],
                "volume": int(r[2] or 0),
                "difficulty": int(r[3] or 0),
                "traffic_potential": int(r[4] or 0),
                "gap_type": r[5],
                "content_format_hint": r[6] or "",
                "intent": r[7] or "informational",
                "competitor_position": r[8],
                "competitor_url": r[9] or "",
            }
            for r in competitor_gap_rows
        ]
```

- [ ] **Step 8: Run the new test to confirm it passes**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_competitor_gaps_have_position_and_url -v
```

Expected: PASSED.

### 2c — Add `traffic_value` to competitor winning content; add `ga4_sessions` to collection gaps

- [ ] **Step 9: Update competitor winning content SELECT**

Find the `winning_content_rows` query (around line 1216). Replace:

```python
        winning_content_rows = conn.execute(
            """
            SELECT competitor_domain, url, top_keyword, top_keyword_volume,
                   estimated_traffic, traffic_value, page_type
            FROM competitor_top_pages
            WHERE estimated_traffic > 0
            ORDER BY estimated_traffic DESC
            LIMIT 15
            """
        ).fetchall()
        competitor_winning_content = [
            {
                "competitor": r[0],
                "url_path": r[1].split("/", 3)[-1] if "/" in r[1] else r[1],
                "keyword": r[2],
                "volume": int(r[3] or 0),
                "traffic": int(r[4] or 0),
                "traffic_value": int(r[5] or 0),
                "page_type": r[6] or "",
            }
            for r in winning_content_rows
        ]
```

- [ ] **Step 10: Update collection gaps SELECT to include ga4_sessions**

Find the `collection_gaps` query (around line 1089). Replace:

```python
    collection_gaps = conn.execute(
        """
        SELECT col.handle, col.title,
               COALESCE(col.gsc_impressions, 0) AS gsc_impressions,
               COALESCE(col.gsc_clicks, 0)      AS gsc_clicks,
               COALESCE(col.gsc_position, 0.0)  AS gsc_position,
               COALESCE(col.ga4_sessions, 0)    AS ga4_sessions
        FROM collections col
        WHERE COALESCE(col.gsc_impressions, 0) > 200
        AND NOT EXISTS (
            SELECT 1 FROM blog_articles ba
            WHERE LOWER(ba.title) LIKE '%' || LOWER(col.title) || '%'
               OR LOWER(ba.body)  LIKE '%' || col.handle || '%'
        )
        ORDER BY col.gsc_impressions DESC
        LIMIT 8
        """
    ).fetchall()
```

Update the `collection_gaps` dict in the `return` statement (around line 1245):

```python
        "collection_gaps": [
            {
                "handle": r[0],
                "title": r[1],
                "gsc_impressions": int(r[2]),
                "gsc_clicks": int(r[3]),
                "gsc_position": round(float(r[4] or 0), 1),
                "ga4_sessions": int(r[5] or 0),
            }
            for r in collection_gaps
        ],
```

### 2d — Add `keyword_page_map` coverage per cluster; test it

- [ ] **Step 11: Write failing test for keyword_page_map enrichment**

Add to `tests/test_article_idea_inputs.py`:

```python
def test_cluster_gaps_have_existing_page_from_keyword_page_map(idea_conn: sqlite3.Connection):
    conn = idea_conn
    conn.execute(
        """
        INSERT INTO clusters
          (name, content_type, primary_keyword, content_brief,
           total_volume, avg_difficulty, avg_opportunity,
           match_type, match_handle, match_title, generated_at)
        VALUES ('KPM Cluster', 'blog_post', 'kpm kw', 'Brief.',
                800, 20.0, 50.0, NULL, NULL, NULL, '2026-01-01T00:00:00Z')
        """
    )
    cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cid, "kpm kw"))
    conn.execute(
        """
        INSERT INTO keyword_page_map
          (keyword, object_type, object_handle, gsc_position, is_primary, updated_at)
        VALUES ('kpm kw', 'collection', 'vapes', 7.5, 1, 0)
        """
    )
    conn.commit()
    data = fetch_article_idea_inputs(conn)
    cg = next(c for c in data["cluster_gaps"] if c["name"] == "KPM Cluster")
    assert cg["existing_page"] is not None
    assert cg["existing_page"]["object_type"] == "collection"
    assert cg["existing_page"]["object_handle"] == "vapes"
    assert cg["existing_page"]["gsc_position"] == 7.5
    conn.close()
```

- [ ] **Step 11b: Run to confirm it fails**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_cluster_gaps_have_existing_page_from_keyword_page_map -v
```

Expected: FAILED — `existing_page` key does not exist yet.

- [ ] **Step 11c: Add existing_page enrichment after the cluster_gaps loop**

In `fetch_article_idea_inputs`, find the line `# 2. Collections with impressions > 200` (around line 1088). Insert this block **immediately before** it:

```python
    # Enrich cluster_gaps with keyword_page_map: find the best-ranking existing page
    # for each cluster's primary keyword. This tells the AI whether a keyword is
    # already served by a product/collection page (→ write a supporting article)
    # or has no ranking page at all (→ write a standalone article).
    for cg in cluster_gaps:
        try:
            kpm_row = conn.execute(
                """
                SELECT object_type, object_handle, COALESCE(gsc_position, 999) AS pos
                FROM keyword_page_map
                WHERE LOWER(keyword) = LOWER(?)
                ORDER BY pos ASC
                LIMIT 1
                """,
                (cg["primary_keyword"],),
            ).fetchone()
            if kpm_row:
                cg["existing_page"] = {
                    "object_type": kpm_row[0],
                    "object_handle": kpm_row[1],
                    "gsc_position": round(float(kpm_row[2]), 1) if kpm_row[2] < 900 else None,
                }
            else:
                cg["existing_page"] = None
        except Exception:
            cg["existing_page"] = None
```

- [ ] **Step 11d: Run keyword_page_map test to confirm it passes**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py::test_cluster_gaps_have_existing_page_from_keyword_page_map -v
```

Expected: PASSED.

### 2e — Add vendor context, top organic pages, geo/device signals, rejected ideas, and queued-idea dedup

- [ ] **Step 12: Add five new data sources at the end of `fetch_article_idea_inputs`, before the `return` statement**

Find the `return {` statement (around line 1240). Insert immediately before it:

```python
    # 8. Vendor context: top brands by product count
    try:
        vendor_rows = conn.execute(
            """
            SELECT vendor, COUNT(*) AS product_count
            FROM products
            WHERE vendor IS NOT NULL AND TRIM(vendor) != ''
            GROUP BY vendor
            ORDER BY product_count DESC
            LIMIT 8
            """
        ).fetchall()
        vendor_context = [{"vendor": r[0], "product_count": int(r[1])} for r in vendor_rows]
    except Exception:
        vendor_context = []

    # 9. Top organic articles (by GSC clicks) — signals proven content categories
    try:
        top_article_rows = conn.execute(
            """
            SELECT title, blog_handle,
                   COALESCE(gsc_clicks, 0)      AS gsc_clicks,
                   COALESCE(gsc_impressions, 0) AS gsc_impressions
            FROM blog_articles
            WHERE gsc_clicks > 0 AND title IS NOT NULL
            ORDER BY gsc_clicks DESC
            LIMIT 5
            """
        ).fetchall()
        top_organic_articles = [
            {
                "title": r[0],
                "blog_handle": r[1],
                "gsc_clicks": int(r[2]),
                "gsc_impressions": int(r[3]),
            }
            for r in top_article_rows
        ]
    except Exception:
        top_organic_articles = []

    # 10. Geo/device signals from GSC dimensional rows
    try:
        country_rows = conn.execute(
            """
            SELECT dimension_value, SUM(impressions) AS total_impressions
            FROM gsc_query_dimension_rows
            WHERE dimension_kind = 'country'
            GROUP BY dimension_value
            ORDER BY total_impressions DESC
            LIMIT 5
            """
        ).fetchall()
        top_countries = [
            {"country": r[0], "impressions": int(r[1] or 0)} for r in country_rows
        ]
        device_rows = conn.execute(
            """
            SELECT dimension_value, SUM(impressions) AS total_impressions
            FROM gsc_query_dimension_rows
            WHERE dimension_kind = 'device'
            GROUP BY dimension_value
            ORDER BY total_impressions DESC
            """
        ).fetchall()
        device_split = [
            {"device": r[0], "impressions": int(r[1] or 0)} for r in device_rows
        ]
    except Exception:
        top_countries = []
        device_split = []

    # 11. Rejected ideas — avoid reprising dismissed topics
    try:
        rejected_rows = conn.execute(
            """
            SELECT suggested_title, primary_keyword
            FROM article_ideas
            WHERE status = 'rejected'
            ORDER BY created_at DESC
            LIMIT 20
            """
        ).fetchall()
        rejected_ideas = [
            {"title": r[0], "primary_keyword": r[1] or ""}
            for r in rejected_rows
        ]
    except Exception:
        rejected_ideas = []

    # 12. Queued ideas (status='idea') — avoid suggesting keywords already in the pipeline
    #     even if not yet drafted. Prevents the AI from reprising ideas that exist but
    #     haven't been published yet (title-only dedup misses these).
    try:
        queued_rows = conn.execute(
            """
            SELECT primary_keyword
            FROM article_ideas
            WHERE status = 'idea' AND primary_keyword != ''
            ORDER BY created_at DESC
            LIMIT 30
            """
        ).fetchall()
        queued_keywords = [r[0] for r in queued_rows]
    except Exception:
        queued_keywords = []
```

- [ ] **Step 13: Add the new keys to the `return` dict**

In the `return {` block, add after `"top_collections": [...]`:

```python
        "vendor_context": vendor_context,
        "top_organic_articles": top_organic_articles,
        "top_countries": top_countries,
        "device_split": device_split,
        "rejected_ideas": rejected_ideas,
        "queued_keywords": queued_keywords,
```

- [ ] **Step 14: Run all article idea tests to confirm everything still passes**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py -v
```

Expected: all pass (the new data sources return empty lists on test DB, no failures).

- [ ] **Step 15: Commit**

```bash
git add shopifyseo/dashboard_queries.py tests/test_article_idea_inputs.py
git commit -m "feat(article-ideas): enrich fetch_article_idea_inputs with all new data sources"
```

---

## Task 3: Prompt Engineering — Enrich `generate_article_ideas()`

**Files:**
- Modify: `shopifyseo/dashboard_ai_engine_parts/generation.py` (function `generate_article_ideas`, lines 1392–1662)

This task rewrites the context block builder and the JSON output schema inside `generate_article_ideas`. No new tests needed (the prompt result is AI-dependent), but the JSON schema changes are validated by the normalization step.

- [ ] **Step 0: Add `import datetime` at module level in `generation.py`**

`datetime` is NOT currently imported at module level in `generation.py`. Find the top-of-file imports block (lines 1–8, which currently reads `import copy / import json / import logging / import sqlite3 / import time`). Add `import datetime` after `import copy`:

```python
import copy
import datetime
import json
import logging
import sqlite3
import time
```

- [ ] **Step 1: Update cluster_lines builder to include content_brief, match info, existing_page, avg_opportunity, CPC badge, word_count, first_seen badge, traffic_potential, gsc_clicks, global_volume**

First change `[:8]` to `[:10]` in the sorted_clusters slice (around line 1411):

```python
    sorted_clusters = sorted(
        gap_data["cluster_gaps"][:10],
        key=lambda c: (c.get("has_ranking_opportunity", False), c.get("total_volume", 0)),
        reverse=True,
    )
```

Then find the `cluster_lines = []` section in `generate_article_ideas` (around line 1417). Replace the entire cluster context-building block (from `cluster_lines = []` through the last `cluster_lines.append` inside the inner for loop) with:

```python
    _ninety_days_ago = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()

    cluster_lines = []
    for c in sorted_clusters:
        vol = f"{c['total_volume']:,}" if c["total_volume"] else "?"
        opp_flag = " ⚡ RANKING OPPORTUNITY" if c.get("has_ranking_opportunity") else ""
        header = (
            f"- Cluster '{c['name']}' (id:{c['id']}) | {c.get('content_type', 'blog_post')} | "
            f"vol:{vol}/mo | avg KD:{c['avg_difficulty']} | avg opp:{c.get('avg_opportunity', 0):.0f}{opp_flag}"
        )
        cluster_lines.append(header)

        # content_brief — cluster's descriptive intent summary
        cb = (c.get("content_brief") or "").strip()
        if cb:
            cluster_lines.append(f"  Brief: {cb}")

        # existing_page — keyword_page_map coverage
        ep = c.get("existing_page")
        if ep:
            pos_str = f" pos:{ep['gsc_position']}" if ep.get("gsc_position") else ""
            cluster_lines.append(
                f"  Already ranking: {ep['object_type']} /{ep['object_handle']}{pos_str} — write as supporting editorial"
            )

        # match context — what existing store page this cluster maps to
        mt = c.get("match_type")
        if mt and mt != "new":
            cluster_lines.append(
                f"  Matched to: {mt} '{c.get('match_title', '')}' (/{c.get('match_handle', '')})"
            )

        agg_bits: list[str] = []
        dsf = (c.get("dominant_serp_features") or "").strip()
        if dsf:
            agg_bits.append(f"SERP mix: {dsf}")
        cfh_c = (c.get("content_format_hints") or "").strip()
        if cfh_c:
            agg_bits.append(f"suggested formats: {cfh_c}")
        ac_c = c.get("avg_cps")
        if ac_c is not None and float(ac_c) > 0:
            agg_bits.append(f"avg CPS: {float(ac_c):.2f}")
        if agg_bits:
            cluster_lines.append("  " + " | ".join(agg_bits))

        for kw in c.get("top_keywords", []):
            rs = kw["ranking_status"]
            if rs == "quick_win":
                badge = f"⚡ QUICK WIN pos:{kw['gsc_position']}"
            elif rs == "striking_distance":
                badge = f"📈 STRIKING DIST pos:{kw['gsc_position']}"
            elif rs == "ranking":
                badge = f"✅ RANKING pos:{kw['gsc_position']}"
            else:
                badge = "not ranking"
            cpc_val = kw.get("cpc") or 0.0
            cpc_str = f"CPC:${cpc_val:.2f}" if cpc_val else ""
            cpc_badge = " 💰 HIGH CPC" if float(cpc_val) >= 1.0 else ""
            parts = [
                f"  • {kw['keyword']}",
                f"vol:{kw['volume']:,}",
                f"KD:{kw['difficulty']}",
            ]
            if cpc_str:
                parts.append(f"{cpc_str}{cpc_badge}")
            parts.append(badge)
            kfmt = (kw.get("content_format_hint") or "").strip()
            if kfmt:
                parts.append(f"fmt:{kfmt[:70]}")
            kserp = (kw.get("serp_features_compact") or "").strip()
            if kserp:
                parts.append(f"serp:{kserp}")
            cps_kw = kw.get("cps")
            if cps_kw is not None and float(cps_kw) > 0:
                parts.append(f"CPS:{float(cps_kw):.2f}")
            clicks_kw = kw.get("clicks")
            if clicks_kw is not None and float(clicks_kw) > 0:
                parts.append(f"clicks:{int(clicks_kw)}/mo")
            tp = kw.get("traffic_potential")
            if tp:
                parts.append(f"tp:{tp:,}")
            wc = kw.get("word_count")
            if wc:
                parts.append(f"top-page-words:{wc}")
            gv = kw.get("global_volume") or 0
            lv = kw.get("volume") or 0
            if gv > lv * 3 and gv > 0:
                parts.append(f"global-vol:{gv:,}")
            fs = kw.get("first_seen") or ""
            # Truncate to date portion in case first_seen has a timestamp suffix
            if fs and fs[:10] >= _ninety_days_ago:
                parts.append("🆕 EMERGING")
            cluster_lines.append(" | ".join(parts))
```

- [ ] **Step 2: Update competitor gap context lines to include position and URL**

Find the `competitor_gap_lines` builder (around line 1482). Replace with:

```python
    competitor_gap_lines = []
    for cg in gap_data.get("competitor_gaps", [])[:8]:
        hint = f" | format:{cg['content_format_hint']}" if cg.get("content_format_hint") else ""
        pos_str = f" | their pos:{cg['competitor_position']}" if cg.get("competitor_position") else ""
        url_str = f" | their url:{cg['competitor_url']}" if cg.get("competitor_url") else ""
        tp_str = f" | tp:{cg['traffic_potential']:,}" if cg.get("traffic_potential") else ""
        competitor_gap_lines.append(
            f"- '{cg['keyword']}' | vol:{cg['volume']:,} | KD:{cg['difficulty']}{tp_str} | "
            f"competitor: {cg['competitor_domain']}{pos_str}{url_str}{hint}"
        )
```

- [ ] **Step 3: Update competitor winning content lines to include traffic_value**

Find the `winning_content_lines` builder (around line 1497). Replace with:

```python
    winning_content_lines = []
    for wc in gap_data.get("competitor_winning_content", [])[:10]:
        tv_str = f" | value:${wc['traffic_value']:,}" if wc.get("traffic_value") else ""
        pt_str = f" | type:{wc['page_type']}" if wc.get("page_type") else ""
        winning_content_lines.append(
            f"- {wc['competitor']}: /{wc['url_path']} | kw:'{wc['keyword']}' | "
            f"vol:{wc['volume']:,} | traffic:{wc['traffic']:,}{tv_str}{pt_str}"
        )
```

- [ ] **Step 4: Update collection gap lines to include ga4_sessions**

Find the `collection_lines` builder (around line 1468). Replace with:

```python
    collection_lines = []
    for col in gap_data["collection_gaps"][:6]:
        ga4_str = f" | {col['ga4_sessions']:,} GA4 sessions" if col.get("ga4_sessions") else ""
        collection_lines.append(
            f"- Collection '{col['title']}' (handle: {col['handle']}) | "
            f"{col['gsc_impressions']:,} impressions/mo | avg pos {col['gsc_position']}{ga4_str}"
        )
```

- [ ] **Step 5: Build four new context sections — vendor, top articles, geo, rejected**

Find the `existing_titles = [...]` line (around line 1504). Insert immediately before it:

```python
    # Vendor context block
    vendor_lines = []
    for v in gap_data.get("vendor_context", [])[:8]:
        vendor_lines.append(f"- {v['vendor']}: {v['product_count']} products")

    # Top organic articles (proven categories)
    top_article_lines = []
    for a in gap_data.get("top_organic_articles", [])[:5]:
        top_article_lines.append(
            f"- '{a['title']}' | {a['gsc_clicks']:,} clicks/mo"
        )

    # Geo/device signals
    geo_lines = []
    for c in gap_data.get("top_countries", [])[:5]:
        geo_lines.append(f"  {c['country']}: {c['impressions']:,} impressions")
    device_lines = []
    for d in gap_data.get("device_split", []):
        device_lines.append(f"  {d['device']}: {d['impressions']:,} impressions")

    # Rejected ideas — do not repeat
    rejected_lines = [
        f"- '{r['title']}'" + (f" (kw: {r['primary_keyword']})" if r.get("primary_keyword") else "")
        for r in gap_data.get("rejected_ideas", [])
    ]

    # Queued ideas (already in pipeline, not yet published) — avoid keyword duplication
    queued_kw_lines = [f"- {kw}" for kw in gap_data.get("queued_keywords", [])]
```

- [ ] **Step 6: Update the `context_block` to include all new sections**

Find the `context_block = "\n".join([...])` (around line 1508). Replace the entire block with:

```python
    context_block = "\n".join(
        [
            "=== KEYWORD CLUSTER GAPS (blog/buying-guide clusters with no article coverage) ===",
            "(⚡ QUICK WIN = ranking pos 11-20, one good article could reach page 1; "
            "📈 STRIKING DIST = pos 21-50, strong growth opportunity). "
            "Cluster lines include SERP mix, suggested formats, CPS, SERP hints, "
            "content brief, matched store page, existing page ranking, and word count benchmarks. "
            "🆕 EMERGING = keyword first seen within 90 days. 💰 HIGH CPC = $1+ per click.",
            "\n".join(cluster_lines) if cluster_lines else "(no cluster data available)",
            "",
            "=== COMPETITOR KEYWORD GAPS (informational keywords where competitors rank but we don't) ===",
            competitor_dedupe_note,
            "\n".join(competitor_gap_lines) if competitor_gap_lines else "(no competitor gap data)",
            "",
            "=== COMPETITOR WINNING CONTENT (top pages driving traffic for competitors) ===",
            "(Use this to understand what topics competitors succeed with. Do NOT link to competitor pages.)",
            "\n".join(winning_content_lines) if winning_content_lines else "(no competitor page data)",
            "",
            "=== COLLECTION GAPS (high-impression collections with no supporting article) ===",
            "\n".join(collection_lines) if collection_lines else "(no collection gap data)",
            "",
            "=== INFORMATIONAL QUERY GAPS (search queries landing on non-article pages) ===",
            "\n".join(query_lines) if query_lines else "(no GSC query data available)",
            "",
            "=== TOP VENDOR BRANDS (products in catalogue — use for brand-specific article angles) ===",
            "\n".join(vendor_lines) if vendor_lines else "(no vendor data)",
            "",
            "=== PROVEN CONTENT CATEGORIES (existing articles driving GSC traffic) ===",
            "(Write adjacent/deeper articles in these categories — proven audience interest.)",
            "\n".join(top_article_lines) if top_article_lines else "(no article traffic data)",
            "",
            "=== AUDIENCE GEOGRAPHY & DEVICE ===",
            "Top countries by impressions:",
            "\n".join(geo_lines) if geo_lines else "  (no geo data)",
            "Device split:",
            "\n".join(device_lines) if device_lines else "  (no device data)",
            "",
            "=== EXISTING ARTICLES (do NOT suggest these topics again) ===",
            "\n".join(f"- {t}" for t in existing_titles[:20]) if existing_titles else "(none yet)",
            "",
            "=== REJECTED IDEAS (do NOT suggest similar topics) ===",
            "\n".join(rejected_lines) if rejected_lines else "(none rejected)",
            "",
            "=== QUEUED ARTICLE IDEAS (primary keywords already in the pipeline — do NOT duplicate) ===",
            "\n".join(queued_kw_lines) if queued_kw_lines else "(none queued)",
            "",
            "=== TOP COLLECTIONS FOR INTERNAL LINKS ===",
            ", ".join(top_col_handles) if top_col_handles else "(none)",
        ]
    )
```

- [ ] **Step 6b: Update `system_msg` to acknowledge all new context types**

Find the `system_msg = (...)` block (around line 1538). Replace with:

```python
    system_msg = (
        "You are a senior SEO content strategist for a Shopify store. "
        "Your job is to identify high-impact article opportunities based on real keyword gaps, "
        "collection search demand, and informational queries that are landing on the wrong pages. "
        "You create specific, data-driven article briefs — not generic vape content. "
        "Use Canadian English spelling (flavours, vapour, favourite, colour, etc.). "
        "Every idea must be directly grounded in the gap data provided.\n"
        "Signal interpretation guide:\n"
        "- 'tp:N' = Ahrefs traffic potential at #1 rank — use this (not raw volume) for traffic estimates.\n"
        "- 'top-page-words:N' = average word count of top-ranking pages — match content depth accordingly.\n"
        "- 'global-vol:N' = global search volume >> local volume — evergreen, established topic, low risk.\n"
        "- '🆕 EMERGING' = keyword first seen within 90 days — timeliness is a ranking advantage.\n"
        "- '💰 HIGH CPC' = $1+ per click — commercially valuable, prioritise if writing commercial content.\n"
        "- 'Already ranking: ...' = primary keyword already has a ranking page — this article should be "
        "a supporting editorial that links to that page, not a competing standalone.\n"
        "- Matched to: ... = the cluster maps to an existing store page — this page should be the "
        "primary internal link target from the article.\n"
        "- Vendor brand data = use to suggest brand-specific buying guides and comparison articles.\n"
        "- Proven content categories = write adjacent or deeper articles in these categories.\n"
        "- Audience geography = incorporate Canadian provinces or regional context when volume is there.\n"
        "- Device split = if mobile impressions dominate, suggest shorter scannable formats.\n"
        "When clusters list SERP mix, suggested formats, or per-keyword format/SERP hints, align the "
        "article angle and content format (e.g. guide vs comparison vs FAQ-style) with those signals. "
        "Do not repeat existing articles, rejected ideas, or queued keywords. Do not invent statistics. "
        "IMPORTANT: Competitor data is provided solely for identifying content opportunities and keyword gaps. "
        "NEVER suggest linking to competitor websites in any article. Only link to the store's own pages."
    )
```

- [ ] **Step 7: Update `user_msg` to request 5 ideas and include `content_format` and `estimated_monthly_traffic`**

Find the `user_msg = (...)` block (around line 1552). Replace with:

```python
    user_msg = (
        "Based on the gap analysis below, generate exactly 5 high-impact article ideas for your store. "
        "Prioritise clusters marked ⚡ QUICK WIN or 📈 STRIKING DIST — these are keywords we already rank "
        "for on page 2/3 and a strong article could reach page 1 fast. "
        "Also consider competitor keyword gaps (informational keywords competitors rank for but we don't; "
        "these omit keywords already covered by clusters to avoid duplicate topics), "
        "collections with high impressions but no supporting editorial, "
        "and informational queries currently landing on product/collection pages.\n\n"
        "Use vendor brand data to suggest brand-specific buying guides. "
        "Use proven content categories to suggest adjacent/deeper articles. "
        "Use audience geography to suggest Canadian-market angles (e.g. province-specific, shipping/legal context). "
        "Use device split: if mobile impressions dominate, suggest shorter, scannable formats.\n\n"
        "For each idea, produce:\n"
        "- suggested_title: The H1 article headline (20–70 chars). Specific, keyword-led, Canadian English. "
        "No ALL CAPS. No vague parentheticals.\n"
        "- brief: 3–4 sentences. What the article covers, who it's for, what search intent it serves, "
        "and how it links to the store's catalog. Be editorial and specific — not generic. "
        "If the cluster has a quick-win keyword, mention that targeting it could move us to page 1.\n"
        "- primary_keyword: The single most important keyword this article targets. "
        "Prefer a ⚡ QUICK WIN keyword if one exists in the cluster.\n"
        "- supporting_keywords: Array of 3–5 supporting keywords from the same cluster or query gap.\n"
        "- search_intent: One of: 'informational', 'commercial', 'navigational'.\n"
        "- content_format: The best content format for this article. One of: "
        "'how_to', 'buying_guide', 'listicle', 'faq', 'comparison', 'review'. "
        "Choose based on SERP mix and content format hints in the cluster data.\n"
        "- estimated_monthly_traffic: Your rough estimate of monthly organic visits if ranking in top 5 "
        "for the primary keyword (integer, e.g. 60 for 1,200/mo volume × 5% CTR).\n"
        "- linked_cluster_id: Integer ID of the most relevant cluster from the data (or null).\n"
        "- linked_cluster_name: Name of that cluster (or empty string).\n"
        "- linked_collection_handle: The most relevant collection handle this article should link to "
        "(use handles from Top Collections list — e.g. 'disposable-vapes', 'vape-kits'). Empty string if none.\n"
        "- linked_collection_title: The human-readable title of that collection (or empty string).\n"
        "- source_type: What type of gap triggered this idea. One of: "
        "'cluster_gap', 'competitor_gap', 'collection_gap', 'query_gap'.\n"
        "- gap_reason: One concise sentence explaining the opportunity — include search volume and "
        "ranking position if available (e.g. 'Ranking pos 14 for \"best disposable vapes canada\" (1,200/mo) "
        "— one strong article could reach page 1').\n\n"
        "Return a JSON object with a single key 'ideas' containing an array of exactly 5 objects.\n\n"
        f"Gap analysis data:\n{context_block}"
    )
```

- [ ] **Step 8: Update `json_schema` to include new fields and require 5 items**

Find the `json_schema = {...}` block (around line 1582). Replace the `schema` dict's `items` object and array constraints:

```python
    json_schema = {
        "name": "article_ideas",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "ideas": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "suggested_title": {"type": "string", "minLength": 20, "maxLength": 70},
                            "brief": {"type": "string", "minLength": 80},
                            "primary_keyword": {"type": "string"},
                            "supporting_keywords": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "search_intent": {
                                "type": "string",
                                "enum": ["informational", "commercial", "navigational"],
                            },
                            "content_format": {
                                "type": "string",
                                "enum": ["how_to", "buying_guide", "listicle", "faq", "comparison", "review"],
                            },
                            "estimated_monthly_traffic": {"type": "integer"},
                            "linked_cluster_id": {"type": ["integer", "null"]},
                            "linked_cluster_name": {"type": "string"},
                            "linked_collection_handle": {"type": "string"},
                            "linked_collection_title": {"type": "string"},
                            "source_type": {
                                "type": "string",
                                "enum": ["cluster_gap", "competitor_gap", "collection_gap", "query_gap"],
                            },
                            "gap_reason": {"type": "string"},
                        },
                        "required": [
                            "suggested_title", "brief", "primary_keyword",
                            "supporting_keywords", "search_intent",
                            "content_format", "estimated_monthly_traffic",
                            "linked_cluster_id", "linked_cluster_name",
                            "linked_collection_handle", "linked_collection_title",
                            "source_type", "gap_reason",
                        ],
                        "additionalProperties": False,
                    },
                    "minItems": 5,
                    "maxItems": 5,
                }
            },
            "required": ["ideas"],
            "additionalProperties": False,
        },
    }
```

- [ ] **Step 10: Commit**

```bash
git add shopifyseo/dashboard_ai_engine_parts/generation.py
git commit -m "feat(article-ideas): enrich AI prompt with all new context + expand output schema to 5 ideas"
```

---

## Task 4: Persistence — Update Save, Fetch, and Generation Normalization

**Files:**
- Modify: `shopifyseo/dashboard_queries.py` (`save_article_ideas`, `fetch_article_ideas`)
- Modify: `shopifyseo/dashboard_ai_engine_parts/generation.py` (cleaning loop in `generate_article_ideas`)
- Create: `tests/test_article_idea_save_fetch.py`

### 4a — Update `generate_article_ideas` to snapshot cluster metrics and normalize new fields

- [ ] **Step 1: Update the cleaning loop in `generation.py`**

Find the `cleaned = []` and `for idea in ideas:` section (around line 1638). Replace the entire block from `cleaned = []` through `return cleaned`:

```python
    # Build a lookup so we can snapshot cluster metrics into each idea
    cluster_lookup = {c["id"]: c for c in gap_data["cluster_gaps"]}

    cleaned = []
    for idea in ideas:
        cid = idea.get("linked_cluster_id")
        if isinstance(cid, str):
            try:
                cid = int(cid)
            except (ValueError, TypeError):
                cid = None

        # Snapshot cluster-level metrics from gap_data (not from AI output)
        cluster_meta = cluster_lookup.get(cid, {}) if cid else {}
        total_volume = int(cluster_meta.get("total_volume") or 0)
        avg_difficulty = round(float(cluster_meta.get("avg_difficulty") or 0.0), 1)
        # Opportunity score: avg_opportunity boosted by 50% if cluster has ranking opportunity
        raw_opp = float(cluster_meta.get("avg_opportunity") or 0.0)
        opportunity_score = round(raw_opp * 1.5 if cluster_meta.get("has_ranking_opportunity") else raw_opp, 1)
        dominant_serp_features = str(cluster_meta.get("dominant_serp_features") or "")
        content_format_hints = str(cluster_meta.get("content_format_hints") or "")
        import json as _json
        linked_keywords_json = _json.dumps(cluster_meta.get("top_keywords") or [])

        cleaned.append(
            {
                "suggested_title": str(idea.get("suggested_title") or ""),
                "brief": str(idea.get("brief") or ""),
                "primary_keyword": str(idea.get("primary_keyword") or ""),
                "supporting_keywords": [str(k) for k in (idea.get("supporting_keywords") or [])],
                "search_intent": str(idea.get("search_intent") or "informational"),
                "content_format": str(idea.get("content_format") or ""),
                "estimated_monthly_traffic": int(idea.get("estimated_monthly_traffic") or 0),
                "linked_cluster_id": cid,
                "linked_cluster_name": str(idea.get("linked_cluster_name") or ""),
                "linked_collection_handle": str(idea.get("linked_collection_handle") or ""),
                "linked_collection_title": str(idea.get("linked_collection_title") or ""),
                "source_type": str(idea.get("source_type") or "cluster_gap"),
                "gap_reason": str(idea.get("gap_reason") or ""),
                # Snapshotted from cluster at generation time
                "total_volume": total_volume,
                "avg_difficulty": avg_difficulty,
                "opportunity_score": opportunity_score,
                "dominant_serp_features": dominant_serp_features,
                "content_format_hints": content_format_hints,
                "linked_keywords_json": linked_keywords_json,
            }
        )
    return cleaned
```

### 4b — Update `save_article_ideas` INSERT

- [ ] **Step 2: Write failing test**

Create `tests/test_article_idea_save_fetch.py`:

```python
"""Tests for save_article_ideas and fetch_article_ideas with new enrichment columns."""
import json
import sqlite3

import pytest

from shopifyseo.dashboard_queries import save_article_ideas, fetch_article_ideas
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    return conn


def test_save_and_fetch_article_ideas_round_trips_new_fields(db_conn):
    idea = {
        "suggested_title": "Best Disposable Vapes Canada 2025",
        "brief": "A 300-word buying guide for Canadian vapers.",
        "primary_keyword": "best disposable vapes canada",
        "supporting_keywords": ["cheap disposable vapes", "disposable vape canada"],
        "search_intent": "commercial",
        "content_format": "buying_guide",
        "estimated_monthly_traffic": 60,
        "linked_cluster_id": 1,
        "linked_cluster_name": "Disposable Vapes",
        "linked_collection_handle": "disposable-vapes",
        "linked_collection_title": "Disposable Vapes",
        "source_type": "cluster_gap",
        "gap_reason": "Ranking pos 14 for primary kw (1,200/mo) — strong quick win.",
        "total_volume": 1200,
        "avg_difficulty": 28.5,
        "opportunity_score": 75.0,
        "dominant_serp_features": "featured_snippet, people_also_ask",
        "content_format_hints": "buying_guide, listicle",
        "linked_keywords_json": json.dumps([{"keyword": "best disposable vapes canada", "volume": 1200}]),
    }
    ids = save_article_ideas(db_conn, [idea])
    assert len(ids) == 1

    fetched = fetch_article_ideas(db_conn)
    assert len(fetched) == 1
    row = fetched[0]

    assert row["suggested_title"] == "Best Disposable Vapes Canada 2025"
    assert row["content_format"] == "buying_guide"
    assert row["estimated_monthly_traffic"] == 60
    assert row["source_type"] == "cluster_gap"
    assert row["total_volume"] == 1200
    assert row["avg_difficulty"] == 28.5
    assert row["opportunity_score"] == 75.0
    assert row["dominant_serp_features"] == "featured_snippet, people_also_ask"
    assert row["content_format_hints"] == "buying_guide, listicle"
    kws = json.loads(row["linked_keywords_json"])
    assert kws[0]["keyword"] == "best disposable vapes canada"

    db_conn.close()
```

- [ ] **Step 3: Run to confirm it fails**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_save_fetch.py -v
```

Expected: FAILED — new columns not in INSERT.

- [ ] **Step 4: Update `save_article_ideas` in `dashboard_queries.py`**

Find the `save_article_ideas` function (around line 1276). Replace the entire INSERT statement and its parameters tuple:

```python
def save_article_ideas(conn: sqlite3.Connection, ideas: list[dict[str, Any]]) -> list[int]:
    """Persist a list of article idea dicts and return their new IDs."""
    import time

    now = int(time.time())
    ids = []
    for idea in ideas:
        supporting = json.dumps(idea.get("supporting_keywords") or [], ensure_ascii=False)
        cur = conn.execute(
            """
            INSERT INTO article_ideas
                (suggested_title, brief, primary_keyword, supporting_keywords,
                 search_intent, linked_cluster_id, linked_cluster_name,
                 linked_collection_handle, linked_collection_title,
                 gap_reason, status, created_at,
                 content_format, estimated_monthly_traffic, source_type,
                 total_volume, avg_difficulty, opportunity_score,
                 dominant_serp_features, content_format_hints, linked_keywords_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'idea', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idea.get("suggested_title", ""),
                idea.get("brief", ""),
                idea.get("primary_keyword", ""),
                supporting,
                idea.get("search_intent", "informational"),
                idea.get("linked_cluster_id"),
                idea.get("linked_cluster_name", ""),
                idea.get("linked_collection_handle", ""),
                idea.get("linked_collection_title", ""),
                idea.get("gap_reason", ""),
                now,
                idea.get("content_format", ""),
                int(idea.get("estimated_monthly_traffic") or 0),
                idea.get("source_type", "cluster_gap"),
                int(idea.get("total_volume") or 0),
                round(float(idea.get("avg_difficulty") or 0.0), 1),
                round(float(idea.get("opportunity_score") or 0.0), 1),
                idea.get("dominant_serp_features", ""),
                idea.get("content_format_hints", ""),
                idea.get("linked_keywords_json") or "[]",
            ),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    return ids
```

### 4c — Update `fetch_article_ideas` SELECT

- [ ] **Step 5: Update `fetch_article_ideas` in `dashboard_queries.py`**

Find the `fetch_article_ideas` function (around line 1312). Replace the SELECT and row-parsing logic:

```python
def fetch_article_ideas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all stored article ideas, newest first."""
    rows = conn.execute(
        """
        SELECT id, suggested_title, brief, primary_keyword, supporting_keywords,
               search_intent, linked_cluster_id, linked_cluster_name,
               linked_collection_handle, linked_collection_title,
               gap_reason, status, created_at,
               COALESCE(content_format, '')              AS content_format,
               COALESCE(estimated_monthly_traffic, 0)   AS estimated_monthly_traffic,
               COALESCE(source_type, 'cluster_gap')     AS source_type,
               COALESCE(total_volume, 0)                AS total_volume,
               COALESCE(avg_difficulty, 0.0)            AS avg_difficulty,
               COALESCE(opportunity_score, 0.0)         AS opportunity_score,
               COALESCE(dominant_serp_features, '')     AS dominant_serp_features,
               COALESCE(content_format_hints, '')       AS content_format_hints,
               COALESCE(linked_keywords_json, '[]')     AS linked_keywords_json
        FROM article_ideas
        ORDER BY created_at DESC, id DESC
        """
    ).fetchall()
    result = []
    for r in rows:
        try:
            keywords = json.loads(r[4] or "[]")
        except (json.JSONDecodeError, TypeError):
            keywords = []
        try:
            linked_kws = json.loads(r[21] or "[]")
        except (json.JSONDecodeError, TypeError):
            linked_kws = []
        result.append(
            {
                "id": r[0],
                "suggested_title": r[1],
                "brief": r[2],
                "primary_keyword": r[3] or "",
                "supporting_keywords": keywords,
                "search_intent": r[5] or "informational",
                "linked_cluster_id": r[6],
                "linked_cluster_name": r[7] or "",
                "linked_collection_handle": r[8] or "",
                "linked_collection_title": r[9] or "",
                "gap_reason": r[10] or "",
                "status": r[11] or "idea",
                "created_at": r[12],
                "content_format": r[13] or "",
                "estimated_monthly_traffic": int(r[14] or 0),
                "source_type": r[15] or "cluster_gap",
                "total_volume": int(r[16] or 0),
                "avg_difficulty": round(float(r[17] or 0.0), 1),
                "opportunity_score": round(float(r[18] or 0.0), 1),
                "dominant_serp_features": r[19] or "",
                "content_format_hints": r[20] or "",
                "linked_keywords_json": linked_kws,
            }
        )
    return result
```

- [ ] **Step 6: Run the round-trip test to confirm it passes**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_save_fetch.py -v
```

Expected: PASSED.

- [ ] **Step 7: Run all article idea tests**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py tests/test_article_idea_save_fetch.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add shopifyseo/dashboard_queries.py shopifyseo/dashboard_ai_engine_parts/generation.py tests/test_article_idea_save_fetch.py
git commit -m "feat(article-ideas): update save/fetch/generation to persist and return all enrichment fields"
```

---

## Task 5: Backend API Schema — Add New Fields to `ArticleIdeaItem`

**Files:**
- Modify: `backend/app/schemas/article_ideas.py`

- [ ] **Step 1: Replace `ArticleIdeaItem` model**

Full file replacement for `backend/app/schemas/article_ideas.py`:

```python
from pydantic import BaseModel, Field


class ArticleIdeaItem(BaseModel):
    id: int
    suggested_title: str
    brief: str
    primary_keyword: str = ""
    supporting_keywords: list[str] = Field(default_factory=list)
    search_intent: str = "informational"
    content_format: str = ""
    estimated_monthly_traffic: int = 0
    linked_cluster_id: int | None = None
    linked_cluster_name: str = ""
    linked_collection_handle: str = ""
    linked_collection_title: str = ""
    source_type: str = "cluster_gap"
    gap_reason: str = ""
    status: str = "idea"
    created_at: int
    # Cluster metrics snapshotted at generation time
    total_volume: int = 0
    avg_difficulty: float = 0.0
    opportunity_score: float = 0.0
    dominant_serp_features: str = ""
    content_format_hints: str = ""
    linked_keywords_json: list = Field(default_factory=list)


class ArticleIdeasPayload(BaseModel):
    items: list[ArticleIdeaItem]
    total: int
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/article_ideas.py
git commit -m "feat(article-ideas): add enrichment fields to ArticleIdeaItem Pydantic schema"
```

---

## Task 6: Frontend — Update Types and `IdeaCard` UI

**Files:**
- Modify: `frontend/src/types/api.ts` (around lines 417–437)
- Modify: `frontend/src/routes/article-ideas-page.tsx` (`IdeaCard` component and generate button text)

### 6a — Update Zod schema in `types/api.ts`

- [ ] **Step 1: Replace `articleIdeaSchema`**

Find the `articleIdeaSchema` definition (around line 417). Replace it:

```typescript
export const articleIdeaSchema = z.object({
  id: z.number(),
  suggested_title: z.string(),
  brief: z.string(),
  primary_keyword: z.string().default(""),
  supporting_keywords: z.array(z.string()).default([]),
  search_intent: z.string().default("informational"),
  content_format: z.string().default(""),
  estimated_monthly_traffic: z.number().default(0),
  linked_cluster_id: z.number().nullable().optional(),
  linked_cluster_name: z.string().default(""),
  linked_collection_handle: z.string().default(""),
  linked_collection_title: z.string().default(""),
  source_type: z.string().default("cluster_gap"),
  gap_reason: z.string().default(""),
  status: z.string().default("idea"),
  created_at: z.number(),
  total_volume: z.number().default(0),
  avg_difficulty: z.number().default(0),
  opportunity_score: z.number().default(0),
  dominant_serp_features: z.string().default(""),
  content_format_hints: z.string().default(""),
  linked_keywords_json: z.array(z.record(z.any())).default([]),
});
export type ArticleIdea = z.infer<typeof articleIdeaSchema>;
```

### 6b — Update `IdeaCard` in `article-ideas-page.tsx`

- [ ] **Step 2: Add new icon imports at the top of `article-ideas-page.tsx`**

Find the existing import from `lucide-react` (around line 5). Replace with:

```tsx
import {
  Lightbulb,
  RefreshCw,
  Trash2,
  Sparkles,
  TrendingUp,
  Tag,
  BookOpen,
  Layers3,
  AlertCircle,
  BarChart2,
  Zap,
  FileText
} from "lucide-react";
```

- [ ] **Step 3: Add helper maps for `content_format` and `source_type` (after `INTENT_LABELS`)**

After the `INTENT_LABELS` constant (around line 37), add:

```tsx
const FORMAT_LABELS: Record<string, string> = {
  how_to: "How-To",
  buying_guide: "Buying Guide",
  listicle: "Listicle",
  faq: "FAQ",
  comparison: "Comparison",
  review: "Review",
};

const SOURCE_LABELS: Record<string, { label: string; color: string }> = {
  cluster_gap: { label: "Cluster Gap", color: "bg-blue-50 text-blue-600 border-blue-200" },
  competitor_gap: { label: "Competitor Gap", color: "bg-rose-50 text-rose-600 border-rose-200" },
  collection_gap: { label: "Collection Gap", color: "bg-violet-50 text-violet-600 border-violet-200" },
  query_gap: { label: "Query Gap", color: "bg-amber-50 text-amber-700 border-amber-200" },
};
```

- [ ] **Step 4: Replace the `IdeaCard` component's return JSX**

Find the full `return (...)` inside `IdeaCard` (from line 70 through line 158). Replace with:

```tsx
  const formatLabel = FORMAT_LABELS[idea.content_format] ?? "";
  const source = SOURCE_LABELS[idea.source_type] ?? SOURCE_LABELS.cluster_gap;

  return (
    <div className="flex flex-col rounded-[20px] border border-[#e4ecf7] bg-white shadow-sm transition hover:shadow-md">
      {/* Card header */}
      <div className="flex items-start justify-between gap-3 px-6 pt-5 pb-0">
        <div className="flex items-start gap-3 min-w-0">
          <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#eef4ff]">
            <Lightbulb size={15} className="text-[#2e6be6]" />
          </div>
          <div className="min-w-0">
            <h3 className="text-base font-bold text-ink leading-snug">{idea.suggested_title}</h3>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              <span
                className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${intent.color}`}
              >
                {intent.label}
              </span>
              <span
                className={`inline-block rounded-full border px-2 py-0.5 text-xs font-medium ${source.color}`}
              >
                {source.label}
              </span>
              {formatLabel ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-600">
                  <FileText size={9} />
                  {formatLabel}
                </span>
              ) : null}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => onDelete(idea.id)}
          disabled={isDeleting}
          className="mt-0.5 shrink-0 rounded-full p-1.5 text-slate-400 transition hover:bg-red-50 hover:text-red-500 disabled:opacity-40"
          title="Dismiss idea"
        >
          <Trash2 size={15} />
        </button>
      </div>

      {/* Metrics row */}
      {(idea.total_volume > 0 || idea.opportunity_score > 0) ? (
        <div className="mx-6 mt-3 flex flex-wrap gap-3 rounded-xl bg-slate-50 border border-slate-100 px-3 py-2">
          {idea.total_volume > 0 ? (
            <div className="flex items-center gap-1.5 text-xs text-slate-600">
              <BarChart2 size={12} className="text-slate-400" />
              <span className="font-semibold text-slate-800">{idea.total_volume.toLocaleString()}</span>
              <span>searches/mo</span>
            </div>
          ) : null}
          {idea.avg_difficulty > 0 ? (
            <div className="flex items-center gap-1.5 text-xs text-slate-600">
              <span className="text-slate-400">KD:</span>
              <span
                className={`font-semibold ${
                  idea.avg_difficulty < 30
                    ? "text-emerald-600"
                    : idea.avg_difficulty < 60
                    ? "text-amber-600"
                    : "text-red-600"
                }`}
              >
                {idea.avg_difficulty.toFixed(0)}
              </span>
            </div>
          ) : null}
          {idea.opportunity_score > 0 ? (
            <div className="flex items-center gap-1.5 text-xs text-slate-600">
              <Zap size={12} className="text-amber-500" />
              <span className="font-semibold text-slate-800">{idea.opportunity_score.toFixed(0)}</span>
              <span>opp score</span>
            </div>
          ) : null}
          {idea.estimated_monthly_traffic > 0 ? (
            <div className="flex items-center gap-1.5 text-xs text-slate-600">
              <TrendingUp size={12} className="text-emerald-500" />
              <span className="font-semibold text-slate-800">~{idea.estimated_monthly_traffic}</span>
              <span>visits/mo est.</span>
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Brief */}
      <p className="mt-3 px-6 text-sm text-slate-600 leading-relaxed">{idea.brief}</p>

      {/* Gap reason */}
      {idea.gap_reason ? (
        <div className="mx-6 mt-3 flex items-start gap-2 rounded-xl bg-amber-50 border border-amber-100 px-3 py-2.5">
          <TrendingUp size={13} className="mt-0.5 shrink-0 text-amber-600" />
          <p className="text-xs text-amber-800">{idea.gap_reason}</p>
        </div>
      ) : null}

      {/* Keywords */}
      <div className="mt-4 px-6">
        <div className="flex flex-wrap gap-1.5">
          {idea.primary_keyword ? (
            <span className="inline-flex items-center gap-1 rounded-full bg-[#2e6be6]/8 px-2.5 py-1 text-xs font-semibold text-[#2e6be6]">
              <Tag size={10} />
              {idea.primary_keyword}
            </span>
          ) : null}
          {idea.supporting_keywords.slice(0, 4).map((kw) => (
            <span
              key={kw}
              className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600"
            >
              {kw}
            </span>
          ))}
        </div>
      </div>

      {/* Linked cluster + collection */}
      {(idea.linked_cluster_name || idea.linked_collection_title) ? (
        <div className="mt-3 flex flex-wrap gap-2 px-6">
          {idea.linked_cluster_name ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#c7d9f8] bg-[#f0f6ff] px-2.5 py-1 text-xs text-[#2e6be6]">
              <BookOpen size={11} />
              {idea.linked_cluster_name}
            </span>
          ) : null}
          {idea.linked_collection_title ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d1e8d4] bg-[#f0faf1] px-2.5 py-1 text-xs text-emerald-700">
              <Layers3 size={11} />
              {idea.linked_collection_title}
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Footer */}
      <div className="mt-4 flex items-center justify-between border-t border-[#edf2fa] px-6 py-4">
        <span className="text-xs text-slate-400">Generated {date}</span>
        <Button size="sm" onClick={() => onDraft(idea)}>
          <Sparkles size={13} />
          Draft article
        </Button>
      </div>
    </div>
  );
```

- [ ] **Step 5: Update "Generate 3 more" text → "Generate 5 more"**

Find the two places in `article-ideas-page.tsx` that say `"Generate 3 more"` and `"Generate 3 more"`. Replace both:

```tsx
// In the header button (around line 294):
{isGenerating ? "Analysing…" : "Generate 5 more"}

// In the footer paragraph (around line 359):
Generate 5 more
```

Also update the `EmptyState` description text and the `generate ideas` count in it. Find the `EmptyState` function (around line 161):

```tsx
      <p className="mt-2 max-w-sm text-sm text-slate-500">
        Click "Generate ideas" to analyse your keyword clusters, collection gaps, and GSC data — the AI will suggest 5 targeted articles to write.
      </p>
```

- [ ] **Step 6: Build frontend**

```bash
cd frontend && npm run rebuild
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types/api.ts frontend/src/routes/article-ideas-page.tsx
git commit -m "feat(article-ideas): update frontend types + IdeaCard with metrics, format, source badges"
```

---

## Final Verification

- [ ] **Run all article idea tests**

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_inputs.py tests/test_article_idea_save_fetch.py -v
```

Expected: all pass.

- [ ] **Restart backend and test generation end-to-end**

```bash
# Kill any process on port 8000, then:
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/app/` → navigate to Article Ideas → click "Generate 5 more". Verify:
1. Exactly 5 new ideas appear
2. Each card shows the metrics row (volume, KD, opp score)
3. Format badge (Buying Guide / How-To / etc.) appears
4. Source badge (Cluster Gap / Competitor Gap / etc.) appears
5. Gap reason box still appears
6. "Draft article" still works

- [ ] **Hard refresh browser (⌘⇧R) to clear cached assets**
