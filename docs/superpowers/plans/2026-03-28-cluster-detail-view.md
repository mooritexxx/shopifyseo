# Cluster Detail View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate cluster storage from JSON blob to DB tables with stable IDs, then add a cluster detail page showing all auto-discovered related URLs with per-URL keyword coverage.

**Architecture:** New `clusters` and `cluster_keywords` DB tables replace the JSON blob in `service_settings`. A one-time migration function converts existing data. A new `get_cluster_detail()` service function discovers related URLs via suggested match, vendor products, and collection products, computing keyword coverage for each. A new frontend route `/keywords/clusters/:id` renders the detail page.

**Tech Stack:** Python/SQLite (backend), React/TypeScript/Zod/TanStack Query (frontend), pytest (tests)

---

## File Structure

### Create
- `frontend/src/routes/cluster-detail-page.tsx` — cluster detail page component

### Modify
- `shopifyseo/dashboard_store.py` — add `clusters` + `cluster_keywords` table creation
- `backend/app/services/keyword_clustering.py` — migrate all functions to DB, add `_detect_vendor()`, `_migrate_json_to_db()`, `get_cluster_detail()`
- `backend/app/routers/clusters.py` — add detail endpoint, change match endpoint to use `cluster_id`
- `shopifyseo/dashboard_ai_engine_parts/generation.py` — update cluster context caller
- `frontend/src/app/router.tsx` — register cluster detail route
- `frontend/src/routes/keywords-page.tsx` — add `id` to schema, make name a Link, update match mutation
- `tests/test_keyword_clustering.py` — update and add tests

---

### Task 1: Create DB Tables

**Files:**
- Modify: `shopifyseo/dashboard_store.py:49-103`
- Test: `tests/test_keyword_clustering.py`

- [ ] **Step 1: Write the failing test for table existence**

Add to `tests/test_keyword_clustering.py`:

```python
import sqlite3


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with the cluster tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            content_type TEXT NOT NULL,
            primary_keyword TEXT NOT NULL,
            content_brief TEXT NOT NULL,
            total_volume INTEGER NOT NULL DEFAULT 0,
            avg_difficulty REAL NOT NULL DEFAULT 0.0,
            avg_opportunity REAL NOT NULL DEFAULT 0.0,
            match_type TEXT,
            match_handle TEXT,
            match_title TEXT,
            generated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_keywords (
            cluster_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            PRIMARY KEY (cluster_id, keyword),
            FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            vendor TEXT,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT,
            online_store_url TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_products (
            collection_shopify_id TEXT NOT NULL,
            product_shopify_id TEXT NOT NULL,
            product_handle TEXT,
            product_title TEXT,
            synced_at TEXT NOT NULL,
            PRIMARY KEY (collection_shopify_id, product_shopify_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            seo_title TEXT,
            seo_description TEXT,
            body TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blog_articles (
            shopify_id TEXT PRIMARY KEY,
            blog_shopify_id TEXT NOT NULL,
            blog_handle TEXT NOT NULL,
            title TEXT NOT NULL,
            handle TEXT NOT NULL,
            seo_title TEXT,
            seo_description TEXT,
            body TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def test_cluster_tables_exist():
    """Verify the test DB helper creates the expected tables."""
    conn = _make_test_db()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    assert "clusters" in tables
    assert "cluster_keywords" in tables
    conn.close()


def test_cluster_cascade_delete():
    """Deleting a cluster cascades to cluster_keywords."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, generated_at) VALUES (?, ?, ?, ?, ?)",
        ("Test", "blog_post", "kw1", "Brief", "2026-01-01T00:00:00Z"),
    )
    cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "kw1"))
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "kw2"))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM cluster_keywords").fetchone()[0] == 2
    conn.execute("DELETE FROM clusters WHERE id = ?", (cluster_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM cluster_keywords").fetchone()[0] == 0
    conn.close()
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py::test_cluster_tables_exist tests/test_keyword_clustering.py::test_cluster_cascade_delete -v`
Expected: PASS (these test the helper itself, not production code)

- [ ] **Step 3: Add table creation to `ensure_dashboard_schema()`**

In `shopifyseo/dashboard_store.py`, add after the `service_settings` CREATE TABLE block (after line ~89):

```python
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            content_type TEXT NOT NULL,
            primary_keyword TEXT NOT NULL,
            content_brief TEXT NOT NULL,
            total_volume INTEGER NOT NULL DEFAULT 0,
            avg_difficulty REAL NOT NULL DEFAULT 0.0,
            avg_opportunity REAL NOT NULL DEFAULT 0.0,
            match_type TEXT,
            match_handle TEXT,
            match_title TEXT,
            generated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cluster_keywords (
            cluster_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            PRIMARY KEY (cluster_id, keyword),
            FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
        )
        """
    )
```

- [ ] **Step 4: Run all existing tests to verify nothing breaks**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All 21 existing tests + 2 new tests PASS

- [ ] **Step 5: Commit**

```bash
git add shopifyseo/dashboard_store.py tests/test_keyword_clustering.py
git commit -m "feat: add clusters and cluster_keywords DB tables"
```

---

### Task 2: Extract `_detect_vendor()` Helper and Migrate `load_clusters()` + `_migrate_json_to_db()`

**Files:**
- Modify: `backend/app/services/keyword_clustering.py:115-174`
- Test: `tests/test_keyword_clustering.py`

- [ ] **Step 1: Write tests for `_detect_vendor()`**

Add to `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import _detect_vendor


def test_detect_vendor_in_cluster_name():
    vendor_map = {"elfbar": {"name": "ELFBAR", "product_count": 67}}
    result = _detect_vendor("Elf Bar Disposable Vapes", ["elf bar canada"], vendor_map)
    assert result is None  # "elfbar" not in "elf bar disposable vapes" as substring
    # But with actual vendor name matching:
    vendor_map2 = {"elf bar": {"name": "Elf Bar", "product_count": 67}}
    result2 = _detect_vendor("Elf Bar Disposable Vapes", ["elf bar canada"], vendor_map2)
    assert result2 == {"name": "Elf Bar", "product_count": 67}


def test_detect_vendor_in_keywords():
    vendor_map = {"stlth": {"name": "STLTH", "product_count": 9}}
    result = _detect_vendor("Brand Collection", ["stlth canada", "stlth vape"], vendor_map)
    assert result == {"name": "STLTH", "product_count": 9}


def test_detect_vendor_no_match():
    vendor_map = {"stlth": {"name": "STLTH", "product_count": 9}}
    result = _detect_vendor("Disposable Vapes Guide", ["disposable vape", "cheap vape"], vendor_map)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_detect_vendor_in_cluster_name tests/test_keyword_clustering.py::test_detect_vendor_in_keywords tests/test_keyword_clustering.py::test_detect_vendor_no_match -v`
Expected: FAIL with ImportError (function doesn't exist yet)

- [ ] **Step 3: Implement `_detect_vendor()`**

Add to `backend/app/services/keyword_clustering.py` (after `_check_keyword_coverage`, before `enrich_clusters_with_coverage`):

```python
def _detect_vendor(
    cluster_name: str,
    cluster_keywords: list[str],
    vendor_map: dict[str, dict],
) -> dict | None:
    """Detect if a cluster matches a product vendor/brand.

    Checks if any vendor name (lowercased key) appears as a substring in the
    cluster name or any of its keywords. Returns the vendor info dict or None.
    """
    name_lower = cluster_name.lower()
    kws_lower = [kw.lower() for kw in cluster_keywords]
    for vendor_lower, vendor_info in vendor_map.items():
        if vendor_lower in name_lower or any(vendor_lower in kw for kw in kws_lower):
            return vendor_info
    return None
```

- [ ] **Step 4: Run `_detect_vendor` tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py::test_detect_vendor_in_cluster_name tests/test_keyword_clustering.py::test_detect_vendor_in_keywords tests/test_keyword_clustering.py::test_detect_vendor_no_match -v`
Expected: PASS

- [ ] **Step 5: Write tests for DB-based `load_clusters()` and `_migrate_json_to_db()`**

Add to `tests/test_keyword_clustering.py`:

```python
import json

from backend.app.services.keyword_clustering import load_clusters


def test_load_clusters_from_db():
    """load_clusters reads from the clusters table."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, total_volume, avg_difficulty, avg_opportunity, match_type, match_handle, match_title, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Elf Bar", "collection_page", "elf bar canada", "Elf Bar collection.", 500, 20.0, 75.0, "collection", "elf-bar", "Elf Bar", "2026-03-28T00:00:00Z"),
    )
    cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "elf bar canada"))
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "elf bar vape"))
    conn.commit()

    data = load_clusters(conn)
    assert len(data["clusters"]) == 1
    c = data["clusters"][0]
    assert c["id"] == cluster_id
    assert c["name"] == "Elf Bar"
    assert c["keywords"] == ["elf bar canada", "elf bar vape"]
    assert c["keyword_count"] == 2
    assert c["suggested_match"] == {"match_type": "collection", "match_handle": "elf-bar", "match_title": "Elf Bar"}
    assert data["generated_at"] == "2026-03-28T00:00:00Z"
    conn.close()


def test_load_clusters_null_match():
    """Clusters with NULL match_type return suggested_match as None."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, generated_at) VALUES (?, ?, ?, ?, ?)",
        ("Test", "blog_post", "kw1", "Brief", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    data = load_clusters(conn)
    assert data["clusters"][0]["suggested_match"] is None
    conn.close()


def test_load_clusters_new_match():
    """Clusters with match_type 'new' return proper suggested_match shape."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, match_type, match_handle, match_title, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Test", "blog_post", "kw1", "Brief", "new", "", "", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    data = load_clusters(conn)
    assert data["clusters"][0]["suggested_match"] == {"match_type": "new", "match_handle": "", "match_title": ""}
    conn.close()


def test_load_clusters_empty_db():
    """Returns empty list when no clusters in DB."""
    conn = _make_test_db()
    data = load_clusters(conn)
    assert data == {"clusters": [], "generated_at": None}
    conn.close()


def test_migrate_json_to_db():
    """JSON data in service_settings is migrated to DB tables on load."""
    conn = _make_test_db()
    json_data = json.dumps({
        "clusters": [
            {
                "name": "Elf Bar",
                "content_type": "collection_page",
                "primary_keyword": "elf bar canada",
                "content_brief": "Elf Bar collection.",
                "keywords": ["elf bar canada", "elf bar vape"],
                "keyword_count": 2,
                "total_volume": 500,
                "avg_difficulty": 20.0,
                "avg_opportunity": 75.0,
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            }
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    })
    conn.execute("INSERT INTO service_settings (key, value) VALUES (?, ?)", ("keyword_clusters", json_data))
    conn.commit()

    data = load_clusters(conn)
    assert len(data["clusters"]) == 1
    c = data["clusters"][0]
    assert c["name"] == "Elf Bar"
    assert c["keywords"] == ["elf bar canada", "elf bar vape"]
    assert c["suggested_match"]["match_type"] == "collection"

    # JSON key should be deleted
    row = conn.execute("SELECT value FROM service_settings WHERE key = ?", ("keyword_clusters",)).fetchone()
    assert row is None

    # Data should be in tables
    assert conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM cluster_keywords").fetchone()[0] == 2
    conn.close()


def test_migrate_no_json_no_data():
    """No JSON and no DB data returns empty result."""
    conn = _make_test_db()
    data = load_clusters(conn)
    assert data == {"clusters": [], "generated_at": None}
    conn.close()
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_load_clusters_from_db tests/test_keyword_clustering.py::test_migrate_json_to_db -v`
Expected: FAIL (load_clusters still reads from JSON)

- [ ] **Step 7: Implement `_migrate_json_to_db()` and rewrite `load_clusters()`**

Replace `load_clusters()` in `backend/app/services/keyword_clustering.py`:

```python
def _migrate_json_to_db(conn: sqlite3.Connection) -> None:
    """One-time migration: move cluster JSON from service_settings to DB tables.

    Idempotent — only runs if JSON key exists and clusters table is empty.
    """
    row = conn.execute(
        "SELECT value FROM service_settings WHERE key = ?", (CLUSTERS_KEY,)
    ).fetchone()
    if not row or not row[0]:
        return

    # Only migrate if tables are empty
    count = conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
    if count > 0:
        # Tables already have data; just clean up the JSON key
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

    for cluster in clusters:
        sm = cluster.get("suggested_match")
        match_type = sm.get("match_type") if sm else None
        match_handle = sm.get("match_handle", "") if sm else None
        match_title = sm.get("match_title", "") if sm else None

        conn.execute(
            """INSERT INTO clusters
               (name, content_type, primary_keyword, content_brief,
                total_volume, avg_difficulty, avg_opportunity,
                match_type, match_handle, match_title, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster.get("name", "Unnamed"),
                cluster.get("content_type", "blog_post"),
                cluster.get("primary_keyword", ""),
                cluster.get("content_brief", ""),
                cluster.get("total_volume", 0),
                cluster.get("avg_difficulty", 0.0),
                cluster.get("avg_opportunity", 0.0),
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


def load_clusters(conn: sqlite3.Connection) -> dict:
    """Load clusters from DB tables. Migrates JSON data on first call if needed."""
    _migrate_json_to_db(conn)

    rows = conn.execute(
        "SELECT * FROM clusters ORDER BY avg_opportunity DESC"
    ).fetchall()

    if not rows:
        return {"clusters": [], "generated_at": None}

    # Batch-load all keywords (avoids N+1)
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
        clusters.append({
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
            "suggested_match": suggested_match,
        })
        if generated_at is None:
            generated_at = row["generated_at"]

    return {"clusters": clusters, "generated_at": generated_at}
```

- [ ] **Step 8: Update `enrich_clusters_with_coverage()` to use `_detect_vendor()`**

Replace the inline vendor detection in `enrich_clusters_with_coverage()` (lines 164-174):

```python
    for cluster in clusters:
        # --- Vendor detection ---
        matched_vendor = _detect_vendor(
            cluster.get("name", ""),
            cluster.get("keywords", []),
            vendor_map,
        )
        cluster["matched_vendor"] = matched_vendor
```

- [ ] **Step 9: Run all tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS (existing pure function tests unchanged, new DB tests pass)

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/keyword_clustering.py tests/test_keyword_clustering.py
git commit -m "feat: migrate load_clusters to DB tables with JSON migration"
```

---

### Task 3: Migrate `generate_clusters()` and `update_cluster_match()` to DB

**Files:**
- Modify: `backend/app/services/keyword_clustering.py:471-624`
- Modify: `backend/app/routers/clusters.py:82-105`

- [ ] **Step 1: Rewrite `generate_clusters()` save step**

Replace the save section (lines 561-569) in `generate_clusters()`:

```python
    # 8. Save to DB
    generated_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM clusters")  # CASCADE deletes cluster_keywords
    for cluster in clusters:
        sm = cluster.get("suggested_match")
        match_type = sm.get("match_type") if sm else None
        match_handle = sm.get("match_handle", "") if sm else None
        match_title = sm.get("match_title", "") if sm else None

        conn.execute(
            """INSERT INTO clusters
               (name, content_type, primary_keyword, content_brief,
                total_volume, avg_difficulty, avg_opportunity,
                match_type, match_handle, match_title, generated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster["name"],
                cluster.get("content_type", "blog_post"),
                cluster.get("primary_keyword", ""),
                cluster.get("content_brief", ""),
                cluster.get("total_volume", 0),
                cluster.get("avg_difficulty", 0.0),
                cluster.get("avg_opportunity", 0.0),
                match_type,
                match_handle,
                match_title,
                generated_at,
            ),
        )
        cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        cluster["id"] = cluster_id
        for kw in cluster.get("keywords", []):
            conn.execute(
                "INSERT OR IGNORE INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)",
                (cluster_id, kw),
            )
    conn.commit()

    progress(f"Done — {len(clusters)} clusters generated, {matched_count} matched to existing pages")

    return {"clusters": clusters, "generated_at": generated_at}
```

Also remove the `set_service_setting` import usage for CLUSTERS_KEY (the `get_service_setting` import is still needed for `TARGET_KEY` in `generate_clusters` and for `_migrate_json_to_db`).

- [ ] **Step 2: Rewrite `update_cluster_match()`**

Replace the entire function:

```python
def update_cluster_match(
    conn: sqlite3.Connection,
    cluster_id: int,
    match_type: str,
    match_handle: str,
    match_title: str,
) -> dict:
    """Update suggested_match for a single cluster by ID. Returns updated clusters payload."""
    row = conn.execute("SELECT id FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    if not row:
        raise ValueError(f"Cluster with id {cluster_id} not found")

    if match_type == "none":
        conn.execute(
            "UPDATE clusters SET match_type = NULL, match_handle = NULL, match_title = NULL WHERE id = ?",
            (cluster_id,),
        )
    else:
        conn.execute(
            "UPDATE clusters SET match_type = ?, match_handle = ?, match_title = ? WHERE id = ?",
            (match_type, match_handle, match_title, cluster_id),
        )
    conn.commit()

    return load_clusters(conn)
```

- [ ] **Step 3: Update the router to use `cluster_id`**

In `backend/app/routers/clusters.py`, change `MatchUpdateBody`:

```python
class MatchUpdateBody(BaseModel):
    cluster_id: int
    match_type: str
    match_handle: str
    match_title: str
```

And update `patch_cluster_match`:

```python
@router.patch("/match", response_model=dict)
def patch_cluster_match(body: MatchUpdateBody):
    """Override the suggested_match for a single cluster."""
    conn = open_db_connection()
    try:
        data = update_cluster_match(
            conn,
            cluster_id=body.cluster_id,
            match_type=body.match_type,
            match_handle=body.match_handle,
            match_title=body.match_title,
        )
        return {"ok": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        conn.close()
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py backend/app/routers/clusters.py
git commit -m "feat: migrate generate_clusters and update_cluster_match to DB"
```

---

### Task 4: Update `generation.py` Caller and Frontend Schema

**Files:**
- Modify: `shopifyseo/dashboard_ai_engine_parts/generation.py:1029-1041`
- Modify: `frontend/src/routes/keywords-page.tsx:57-76,830-832,987,1103-1104`

- [ ] **Step 1: Update the cluster context caller in `generation.py`**

Replace lines 1029-1041 in `shopifyseo/dashboard_ai_engine_parts/generation.py`:

```python
    # Load cluster context for matched pages
    try:
        from backend.app.services.keyword_clustering import load_clusters, _load_cluster_context
        clusters_data = load_clusters(conn)
        from shopifyseo.dashboard_google import get_service_setting as _get_ss
        target_raw = _get_ss(conn, "target_keywords", "{}")
        target_data = json.loads(target_raw) if target_raw else {}
        cluster_ctx = _load_cluster_context(clusters_data, target_data, object_type, handle)
        if cluster_ctx:
            context["cluster_seo_context"] = cluster_ctx
    except Exception:
        logger.debug("Failed to load cluster context; proceeding without it")
```

- [ ] **Step 2: Update the frontend `clusterSchema` to include `id`**

In `frontend/src/routes/keywords-page.tsx`, add `id` to `clusterSchema`:

```typescript
const clusterSchema = z.object({
  id: z.number(),
  name: z.string(),
  content_type: z.string(),
  primary_keyword: z.string(),
  content_brief: z.string(),
  keywords: z.array(z.string()),
  total_volume: z.number(),
  avg_difficulty: z.number(),
  avg_opportunity: z.number(),
  keyword_count: z.number(),
  suggested_match: matchSchema.nullable().optional(),
  keyword_coverage: z.object({
    found: z.number(),
    total: z.number(),
  }).nullable().optional(),
  matched_vendor: z.object({
    name: z.string(),
    product_count: z.number(),
  }).nullable().optional(),
});
```

- [ ] **Step 3: Update the match mutation to send `cluster_id`**

In `frontend/src/routes/keywords-page.tsx`, change the `matchMutation` (around line 830):

```typescript
  const matchMutation = useMutation({
    mutationFn: (vars: { cluster_id: number; match_type: string; match_handle: string; match_title: string }) =>
      patchJson("/api/keywords/clusters/match", clustersPayloadSchema, vars),
```

And update the mutation call site (around line 1103):

```typescript
                                  matchMutation.mutate({
                                    cluster_id: cluster.id,
```

- [ ] **Step 4: Make cluster name a clickable Link**

In `frontend/src/routes/keywords-page.tsx`, change the cluster name `<h3>` (around line 987):

```typescript
                    <Link
                      to={`/keywords/clusters/${cluster.id}`}
                      className="text-base font-semibold text-ink hover:text-blue-600 hover:underline"
                    >
                      {cluster.name}
                    </Link>
```

- [ ] **Step 5: Run TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 6: Run backend tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS

- [ ] **Step 7: Commit**

```bash
git add shopifyseo/dashboard_ai_engine_parts/generation.py frontend/src/routes/keywords-page.tsx
git commit -m "feat: update generation.py caller and frontend schema for DB clusters"
```

---

### Task 5: Implement `get_cluster_detail()` Service Function

**Files:**
- Modify: `backend/app/services/keyword_clustering.py`
- Test: `tests/test_keyword_clustering.py`

- [ ] **Step 1: Write tests for `get_cluster_detail()`**

Add to `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import get_cluster_detail
import pytest


def _insert_cluster(conn, name, content_type, primary_keyword, content_brief, keywords, match_type=None, match_handle=None, match_title=None) -> int:
    """Helper to insert a cluster and its keywords. Returns cluster id."""
    conn.execute(
        """INSERT INTO clusters
           (name, content_type, primary_keyword, content_brief,
            total_volume, avg_difficulty, avg_opportunity,
            match_type, match_handle, match_title, generated_at)
           VALUES (?, ?, ?, ?, 0, 0.0, 0.0, ?, ?, ?, '2026-03-28T00:00:00Z')""",
        (name, content_type, primary_keyword, content_brief, match_type, match_handle, match_title),
    )
    cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for kw in keywords:
        conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, kw))
    conn.commit()
    return cluster_id


def test_detail_with_suggested_match():
    """Collection match appears in related_urls with coverage."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth canada",
                          "STLTH collection.", ["stlth canada", "stlth vape"],
                          match_type="collection", match_handle="stlth", match_title="STLTH")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "STLTH", "stlth", "STLTH Vapes Canada", "Buy STLTH vape pods", "<p>STLTH Canada collection</p>"),
    )
    conn.commit()

    result = get_cluster_detail(conn, cid)
    assert result["cluster"]["name"] == "STLTH Brand"
    urls = result["related_urls"]
    assert len(urls) >= 1
    match_url = [u for u in urls if u["source"] == "suggested_match"]
    assert len(match_url) == 1
    assert match_url[0]["url_type"] == "collection"
    assert match_url[0]["handle"] == "stlth"
    assert match_url[0]["keyword_coverage"]["total"] == 2
    conn.close()


def test_detail_vendor_products():
    """Vendor products appear with source 'vendor'."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth canada",
                          "STLTH collection.", ["stlth canada", "stlth loop"],
                          match_type="new", match_handle="", match_title="")
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "STLTH Loop 9K", "stlth-loop-9k", "STLTH", "STLTH Loop", "Loop vape", "<p>STLTH Loop 9K device</p>"),
    )
    conn.commit()

    result = get_cluster_detail(conn, cid)
    vendor_urls = [u for u in result["related_urls"] if u["source"] == "vendor"]
    assert len(vendor_urls) == 1
    assert vendor_urls[0]["handle"] == "stlth-loop-9k"
    assert vendor_urls[0]["url_type"] == "product"
    conn.close()


def test_detail_collection_products():
    """Products in matched collection appear with source 'collection_products'."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Disposables", "collection_page", "disposable vape",
                          "Disposable vapes.", ["disposable vape", "cheap disposable"],
                          match_type="collection", match_handle="disposables", match_title="Disposables")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "Disposables", "disposables", "Disposable Vapes", "Buy disposable vapes", "<p>Cheap disposable vapes</p>"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "Allo Ultra", "allo-ultra", "ALLO", "Allo Ultra", "Disposable vape", "<p>Allo Ultra disposable</p>"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "allo-ultra", "Allo Ultra", "2026-03-28"),
    )
    conn.commit()

    result = get_cluster_detail(conn, cid)
    cp_urls = [u for u in result["related_urls"] if u["source"] == "collection_products"]
    assert len(cp_urls) == 1
    assert cp_urls[0]["handle"] == "allo-ultra"
    conn.close()


def test_detail_deduplication():
    """Product via vendor+collection appears once with 'vendor' source (higher priority)."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth canada",
                          "STLTH collection.", ["stlth canada"],
                          match_type="collection", match_handle="stlth", match_title="STLTH")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "STLTH", "stlth", "STLTH", "", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "STLTH Loop", "stlth-loop", "STLTH", "", "", ""),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "stlth-loop", "STLTH Loop", "2026-03-28"),
    )
    conn.commit()

    result = get_cluster_detail(conn, cid)
    product_urls = [u for u in result["related_urls"] if u["url_type"] == "product"]
    assert len(product_urls) == 1
    assert product_urls[0]["source"] == "vendor"
    conn.close()


def test_detail_cluster_not_found():
    """Raises ValueError for nonexistent cluster id."""
    conn = _make_test_db()
    with pytest.raises(ValueError):
        get_cluster_detail(conn, 9999)
    conn.close()


def test_detail_no_related_urls():
    """Cluster with match_type 'new' and no vendor returns empty related_urls."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "New Topic", "blog_post", "vape guide",
                          "Guide.", ["vape guide"],
                          match_type="new", match_handle="", match_title="")
    result = get_cluster_detail(conn, cid)
    assert result["related_urls"] == []
    conn.close()


def test_detail_none_match_skips_suggested():
    """match_type NULL means no suggested match URL."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Orphan", "blog_post", "random kw",
                          "Brief.", ["random kw"])
    result = get_cluster_detail(conn, cid)
    match_urls = [u for u in result["related_urls"] if u["source"] == "suggested_match"]
    assert len(match_urls) == 0
    conn.close()


def test_detail_product_coverage_uses_title():
    """Product coverage includes title field (4 fields total)."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth loop",
                          "STLTH.", ["stlth loop"],
                          match_type="new", match_handle="", match_title="")
    # Keyword "stlth loop" only in product title, not in other fields
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "STLTH Loop 9K", "stlth-loop-9k", "STLTH", "Vape Device", "A great vape", "<p>Premium device</p>"),
    )
    conn.commit()

    result = get_cluster_detail(conn, cid)
    vendor_urls = [u for u in result["related_urls"] if u["source"] == "vendor"]
    assert len(vendor_urls) == 1
    assert vendor_urls[0]["keyword_coverage"]["found"] == 1
    conn.close()


def test_detail_sorted_by_coverage():
    """Related URLs are sorted by coverage found descending."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth canada",
                          "STLTH.", ["stlth canada", "stlth vape", "stlth loop"],
                          match_type="new", match_handle="", match_title="")
    # Product with 2 keyword matches
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "STLTH Canada Vape", "stlth-vape", "STLTH", "STLTH Canada", "STLTH vape device", ""),
    )
    # Product with 0 keyword matches
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p2", "Bold 8K Device", "stlth-bold", "STLTH", "Bold Device", "Premium", ""),
    )
    conn.commit()

    result = get_cluster_detail(conn, cid)
    urls = result["related_urls"]
    assert len(urls) == 2
    assert urls[0]["keyword_coverage"]["found"] >= urls[1]["keyword_coverage"]["found"]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_detail_with_suggested_match tests/test_keyword_clustering.py::test_detail_cluster_not_found -v`
Expected: FAIL with ImportError

- [ ] **Step 3: Implement `get_cluster_detail()`**

Add to `backend/app/services/keyword_clustering.py`:

```python
def get_cluster_detail(conn: sqlite3.Connection, cluster_id: int) -> dict:
    """Load a single cluster with all auto-discovered related URLs and coverage.

    Discovery chain (priority order for deduplication):
    1. Suggested match (collection/page/blog_article)
    2. Vendor products (via matched_vendor)
    3. Collection products (via collection_products join)

    Raises ValueError if cluster_id not found.
    """
    row = conn.execute("SELECT * FROM clusters WHERE id = ?", (cluster_id,)).fetchone()
    if not row:
        raise ValueError(f"Cluster with id {cluster_id} not found")

    keywords = [
        r[0] for r in conn.execute(
            "SELECT keyword FROM cluster_keywords WHERE cluster_id = ?", (cluster_id,)
        ).fetchall()
    ]

    match_type = row["match_type"]
    match_handle = row["match_handle"] or ""
    match_title = row["match_title"] or ""

    if match_type is None:
        suggested_match = None
    elif match_type == "new":
        suggested_match = {"match_type": "new", "match_handle": "", "match_title": ""}
    else:
        suggested_match = {
            "match_type": match_type,
            "match_handle": match_handle,
            "match_title": match_title,
        }

    # Detect vendor
    vendor_rows = conn.execute(
        "SELECT vendor, COUNT(*) FROM products WHERE vendor IS NOT NULL AND vendor != '' GROUP BY vendor"
    ).fetchall()
    vendor_map: dict[str, dict] = {}
    for vr in vendor_rows:
        vendor_map[vr[0].lower()] = {"name": vr[0], "product_count": vr[1]}
    matched_vendor = _detect_vendor(row["name"], keywords, vendor_map)

    cluster = {
        "id": row["id"],
        "name": row["name"],
        "content_type": row["content_type"],
        "primary_keyword": row["primary_keyword"],
        "content_brief": row["content_brief"],
        "keywords": keywords,
        "keyword_count": len(keywords),
        "total_volume": row["total_volume"],
        "avg_difficulty": row["avg_difficulty"],
        "avg_opportunity": row["avg_opportunity"],
        "suggested_match": suggested_match,
        "matched_vendor": matched_vendor,
    }

    # --- Discovery chain ---
    related: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add_url(url_type: str, handle: str, title: str, source: str, content: str) -> None:
        key = (url_type, handle)
        if key in seen:
            return
        seen.add(key)
        found, total = _check_keyword_coverage(keywords, content)
        related.append({
            "url_type": url_type,
            "handle": handle,
            "title": title,
            "source": source,
            "keyword_coverage": {"found": found, "total": total},
        })

    # 1. Suggested match
    if match_type and match_type not in ("new", "none"):
        if match_type == "collection":
            r = conn.execute(
                "SELECT seo_title, seo_description, description_html FROM collections WHERE handle = ?",
                (match_handle,),
            ).fetchone()
            if r:
                content = " ".join(r[i] or "" for i in range(3))
                _add_url("collection", match_handle, match_title, "suggested_match", content)
        elif match_type == "page":
            r = conn.execute(
                "SELECT seo_title, seo_description, body FROM pages WHERE handle = ?",
                (match_handle,),
            ).fetchone()
            if r:
                content = " ".join(r[i] or "" for i in range(3))
                _add_url("page", match_handle, match_title, "suggested_match", content)
        elif match_type == "blog_article":
            parts = match_handle.split("/", 1)
            if len(parts) == 2:
                r = conn.execute(
                    "SELECT seo_title, seo_description, body FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                    (parts[0], parts[1]),
                ).fetchone()
                if r:
                    content = " ".join(r[i] or "" for i in range(3))
                    _add_url("blog_article", match_handle, match_title, "suggested_match", content)

    # 2. Vendor products
    if matched_vendor:
        vendor_products = conn.execute(
            "SELECT handle, title, seo_title, seo_description, description_html FROM products WHERE LOWER(vendor) = ?",
            (matched_vendor["name"].lower(),),
        ).fetchall()
        for vp in vendor_products:
            content = " ".join(vp[i] or "" for i in range(1, 5))  # title + seo_title + seo_description + description_html
            _add_url("product", vp[0], vp[1], "vendor", content)

    # 3. Collection products (if match is a collection)
    if match_type == "collection" and match_handle:
        cp_rows = conn.execute(
            """SELECT p.handle, p.title, p.seo_title, p.seo_description, p.description_html
               FROM products p
               JOIN collection_products cp ON p.shopify_id = cp.product_shopify_id
               JOIN collections c ON cp.collection_shopify_id = c.shopify_id
               WHERE c.handle = ?""",
            (match_handle,),
        ).fetchall()
        for cp in cp_rows:
            content = " ".join(cp[i] or "" for i in range(1, 5))
            _add_url("product", cp[0], cp[1], "collection_products", content)

    # Sort by coverage descending
    related.sort(key=lambda u: u["keyword_coverage"]["found"], reverse=True)

    return {"cluster": cluster, "related_urls": related}
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py tests/test_keyword_clustering.py
git commit -m "feat: implement get_cluster_detail with discovery chain and coverage"
```

---

### Task 6: Add Detail Endpoint to Router

**Files:**
- Modify: `backend/app/routers/clusters.py`

- [ ] **Step 1: Add the detail endpoint**

Add the import and endpoint to `backend/app/routers/clusters.py`:

```python
from backend.app.services.keyword_clustering import (
    enrich_clusters_with_coverage,
    generate_clusters,
    get_cluster_detail,
    get_match_options,
    load_clusters,
    update_cluster_match,
)
```

Add this endpoint after the existing `get_clusters` endpoint:

```python
@router.get("/{cluster_id}/detail", response_model=dict)
def get_cluster_detail_view(cluster_id: int):
    """Return cluster detail with all related URLs and coverage."""
    conn = open_db_connection()
    try:
        data = get_cluster_detail(conn, cluster_id)
        return {"ok": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    finally:
        conn.close()
```

- [ ] **Step 2: Run backend tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/clusters.py
git commit -m "feat: add GET /clusters/{cluster_id}/detail endpoint"
```

---

### Task 7: Create Frontend Cluster Detail Page

**Files:**
- Create: `frontend/src/routes/cluster-detail-page.tsx`
- Modify: `frontend/src/app/router.tsx`

- [ ] **Step 1: Create the cluster detail page component**

Create `frontend/src/routes/cluster-detail-page.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { getJson } from "../lib/api";
import { Skeleton } from "../components/ui/skeleton";

const matchSchema = z.object({
  match_type: z.string(),
  match_handle: z.string(),
  match_title: z.string(),
});

const clusterSchema = z.object({
  id: z.number(),
  name: z.string(),
  content_type: z.string(),
  primary_keyword: z.string(),
  content_brief: z.string(),
  keywords: z.array(z.string()),
  total_volume: z.number(),
  avg_difficulty: z.number(),
  avg_opportunity: z.number(),
  keyword_count: z.number(),
  suggested_match: matchSchema.nullable().optional(),
  keyword_coverage: z.object({
    found: z.number(),
    total: z.number(),
  }).nullable().optional(),
  matched_vendor: z.object({
    name: z.string(),
    product_count: z.number(),
  }).nullable().optional(),
});

const relatedUrlSchema = z.object({
  url_type: z.string(),
  handle: z.string(),
  title: z.string(),
  source: z.string(),
  keyword_coverage: z.object({
    found: z.number(),
    total: z.number(),
  }),
});

const clusterDetailPayloadSchema = z.object({
  cluster: clusterSchema,
  related_urls: z.array(relatedUrlSchema),
});

const CONTENT_TYPE_COLORS: Record<string, string> = {
  collection_page: "bg-blue-100 text-blue-700",
  product_page: "bg-purple-100 text-purple-700",
  blog_post: "bg-green-100 text-green-700",
  buying_guide: "bg-amber-100 text-amber-700",
  landing_page: "bg-rose-100 text-rose-700",
};

const CONTENT_TYPE_LABELS: Record<string, string> = {
  collection_page: "Collection Page",
  product_page: "Product Page",
  blog_post: "Blog Post",
  buying_guide: "Buying Guide",
  landing_page: "Landing Page",
};

function coverageColor(found: number, total: number) {
  if (total === 0) return "bg-slate-100 text-slate-600";
  const pct = found / total;
  if (pct >= 0.5) return "bg-green-100 text-green-700";
  if (pct >= 0.25) return "bg-yellow-100 text-yellow-700";
  return "bg-red-100 text-red-700";
}

function sourceLabel(source: string) {
  switch (source) {
    case "suggested_match": return "Match";
    case "vendor": return "Vendor";
    case "collection_products": return "Collection";
    default: return source;
  }
}

function sourceColor(source: string) {
  switch (source) {
    case "suggested_match": return "bg-blue-50 text-blue-600";
    case "vendor": return "bg-purple-50 text-purple-600";
    case "collection_products": return "bg-amber-50 text-amber-600";
    default: return "bg-slate-50 text-slate-600";
  }
}

function typeLabel(urlType: string) {
  switch (urlType) {
    case "collection": return "Collection";
    case "product": return "Product";
    case "page": return "Page";
    case "blog_article": return "Blog Article";
    default: return urlType;
  }
}

function detailLink(urlType: string, handle: string) {
  switch (urlType) {
    case "collection": return `/collections/${handle}`;
    case "product": return `/products/${handle}`;
    case "page": return `/pages/${handle}`;
    case "blog_article": {
      const [blogHandle, articleHandle] = handle.split("/", 2);
      return `/articles/${blogHandle}/${articleHandle}`;
    }
    default: return "#";
  }
}

export function ClusterDetailPage() {
  const { id = "" } = useParams();

  const query = useQuery({
    queryKey: ["cluster-detail", id],
    queryFn: () => getJson(`/api/keywords/clusters/${id}/detail`, clusterDetailPayloadSchema),
    enabled: !!id,
  });

  if (query.isLoading) {
    return (
      <div className="space-y-6 pb-10">
        <Skeleton className="h-5 w-32 rounded-lg" />
        <Skeleton className="h-48 rounded-[24px]" />
        <Skeleton className="h-64 rounded-[24px]" />
      </div>
    );
  }

  if (query.error || !query.data) {
    return (
      <div className="space-y-4">
        <Link to="/keywords" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-ink">
          <ArrowLeft className="h-4 w-4" /> Back to Keywords
        </Link>
        <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">
          {(query.error as Error)?.message || "Could not load cluster."}
        </div>
      </div>
    );
  }

  const { cluster, related_urls } = query.data;
  const contentColor = CONTENT_TYPE_COLORS[cluster.content_type] ?? "bg-slate-100 text-slate-600";
  const contentLabel = CONTENT_TYPE_LABELS[cluster.content_type] ?? cluster.content_type;

  return (
    <div className="space-y-6 pb-10">
      {/* Back link */}
      <Link to="/keywords" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Back to Keywords
      </Link>

      {/* Cluster info card */}
      <div className="rounded-xl border border-line bg-white p-6 space-y-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-ink">{cluster.name}</h1>
            <span className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${contentColor}`}>
              {contentLabel}
            </span>
            {cluster.matched_vendor && (
              <span className="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700 whitespace-nowrap">
                {cluster.matched_vendor.name} · {cluster.matched_vendor.product_count} products
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-slate-700">Primary: {cluster.primary_keyword}</p>
          <p className="text-sm text-slate-500">{cluster.content_brief}</p>
        </div>

        {/* Stats row */}
        <div className="flex flex-wrap gap-4 text-sm text-slate-500">
          <span>
            Volume: <span className="font-medium text-ink">{cluster.total_volume.toLocaleString()}</span>
          </span>
          <span>
            Avg difficulty: <span className="font-medium text-ink">{cluster.avg_difficulty}</span>
          </span>
          <span>
            Avg opportunity: <span className="font-medium text-ink">{cluster.avg_opportunity}</span>
          </span>
          <span>
            Keywords: <span className="font-medium text-ink">{cluster.keyword_count}</span>
          </span>
        </div>

        {/* Suggested match */}
        <div className="flex items-center gap-2 text-sm">
          {cluster.suggested_match ? (
            cluster.suggested_match.match_type === "new" ? (
              <span className="inline-flex items-center gap-1">
                <span className="text-slate-400">→</span>
                <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">New content</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1">
                <span className="text-slate-400">→</span>
                <Link
                  to={detailLink(cluster.suggested_match.match_type, cluster.suggested_match.match_handle)}
                  className="text-blue-600 hover:text-blue-800 hover:underline"
                >
                  {cluster.suggested_match.match_title}
                </Link>
                <span className="text-xs text-slate-400">
                  ({typeLabel(cluster.suggested_match.match_type)})
                </span>
              </span>
            )
          ) : (
            <span className="text-slate-400">→ No match suggested</span>
          )}
        </div>
      </div>

      {/* Related URLs section */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold text-ink">Related URLs ({related_urls.length})</h2>

        {related_urls.length === 0 ? (
          <div className="rounded-xl border border-line bg-white p-6 text-center text-sm text-slate-400">
            No related URLs discovered for this cluster.
          </div>
        ) : (
          <div className="rounded-xl border border-line bg-white overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-slate-500">
                  <th className="px-4 py-3">Title</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Source</th>
                  <th className="px-4 py-3 text-right">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {related_urls.map((url) => {
                  const cov = url.keyword_coverage;
                  return (
                    <tr key={`${url.url_type}-${url.handle}`} className="border-b border-line last:border-0">
                      <td className="px-4 py-3">
                        <Link
                          to={detailLink(url.url_type, url.handle)}
                          className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          {url.title}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-slate-600">{typeLabel(url.url_type)}</td>
                      <td className="px-4 py-3">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${sourceColor(url.source)}`}>
                          {sourceLabel(url.source)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${coverageColor(cov.found, cov.total)}`}>
                          {cov.found}/{cov.total}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Register the route in `router.tsx`**

In `frontend/src/app/router.tsx`, add the lazy import:

```typescript
const ClusterDetailPage = lazy(() => import("../routes/cluster-detail-page").then((module) => ({ default: module.ClusterDetailPage })));
```

Add the route after the `/keywords` route:

```typescript
    {
      path: "/keywords/clusters/:id",
      element: shell(<ClusterDetailPage />)
    },
```

- [ ] **Step 3: Run TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Run all backend tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/cluster-detail-page.tsx frontend/src/app/router.tsx
git commit -m "feat: add cluster detail page with related URLs table"
```

---

### Task 8: Final Integration Test

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS (should be ~40+ tests total)

- [ ] **Step 2: Run TypeScript check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Verify no unused imports or dead code**

Check that `set_service_setting` is still imported (needed by `_migrate_json_to_db` indirectly — but actually `_migrate_json_to_db` uses `conn.execute` directly, so check if we can remove the `set_service_setting` import). The `get_service_setting` import is still needed for `TARGET_KEY` in `generate_clusters()`.

Run: `python -c "from backend.app.services.keyword_clustering import load_clusters, generate_clusters, update_cluster_match, get_cluster_detail, _detect_vendor, enrich_clusters_with_coverage, get_match_options"`
Expected: No errors

- [ ] **Step 4: Commit if any cleanup was needed**

```bash
git add -A
git commit -m "chore: final cleanup for cluster detail view"
```
