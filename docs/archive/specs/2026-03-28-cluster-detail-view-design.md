# Cluster Detail View Design

## Goal

Two changes in one spec:

1. **Migrate cluster storage from JSON blob to DB tables** — gives clusters stable IDs, simplifies mutations, and enables relational queries.
2. **Add a cluster detail page** — shows all auto-discovered related URLs (collections, products, pages, blog articles) with per-URL keyword coverage, so the user can see which pages need AI regeneration.

## Part 1: DB Migration

### Why

Clusters are currently a JSON blob in `service_settings` (key: `keyword_clusters`). This causes:
- **Fragile indexing** — accessing clusters by array position breaks if clusters are regenerated
- **Expensive mutations** — updating one cluster's match requires load→parse→modify→serialize→save of the entire blob
- **No relational queries** — can't query "which clusters point to this collection?" without loading everything

Moving to proper tables gives each cluster a stable integer `id`, makes match updates a single `UPDATE`, and enables the detail page to use `/keywords/clusters/:id` as a permanent URL.

### New Tables

Added via `ensure_dashboard_schema()` in `shopifyseo/dashboard_store.py` using `CREATE TABLE IF NOT EXISTS` (same pattern as all existing tables).

```sql
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
);

CREATE TABLE IF NOT EXISTS cluster_keywords (
  cluster_id INTEGER NOT NULL,
  keyword TEXT NOT NULL,
  PRIMARY KEY (cluster_id, keyword),
  FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
);
```

Notes:
- `match_type` is nullable — `NULL` means never matched, `"new"` means new content, `"none"` means explicitly cleared, `"collection"` / `"page"` / `"blog_article"` means matched to existing content.
- `keyword_count` is not stored — derived as `COUNT(*)` from `cluster_keywords`.
- `generated_at` is the same for all clusters in a generation batch.

### Functions to Migrate

All in `backend/app/services/keyword_clustering.py`:

**`generate_clusters()`** — currently saves JSON blob via `set_service_setting()`. Change to:
1. `DELETE FROM clusters` (cascade deletes `cluster_keywords` automatically — `PRAGMA foreign_keys = ON` is set in `ensure_schema()`)
2. `INSERT INTO clusters` for each cluster, capturing `lastrowid` as the stable `id`
3. `INSERT INTO cluster_keywords` for each keyword in each cluster
4. Remove the `set_service_setting(conn, CLUSTERS_KEY, ...)` call
5. Return data with `id` included in each cluster dict so the SSE `done` event sends usable IDs to the frontend

**`load_clusters()`** — currently parses JSON blob. Change to:
1. `SELECT * FROM clusters ORDER BY avg_opportunity DESC` — one query for all clusters
2. `SELECT cluster_id, keyword FROM cluster_keywords` — one query for all keywords (avoids N+1)
3. Group keywords by `cluster_id` in Python
4. Build cluster dicts with `id` included in each
5. **Match field mapping:** if `match_type` is `NULL` → `suggested_match: None`. If `match_type` is `"new"` → `suggested_match: {"match_type": "new", "match_handle": "", "match_title": ""}`. Otherwise → `suggested_match: {"match_type": ..., "match_handle": ..., "match_title": ...}`.
6. Return shape: `{"clusters": [...], "generated_at": ...}` — same structure as before but each cluster now includes `id`

Note: `AUTOINCREMENT` IDs never reset between regenerations. After 3 regenerations of 20 clusters, IDs would be 41-60. This is fine — IDs are opaque identifiers, not positions.

**`update_cluster_match()`** — currently does blob surgery. Change to:
1. `UPDATE clusters SET match_type = ?, match_handle = ?, match_title = ? WHERE id = ?`
2. For `match_type = "none"`: set `match_type`, `match_handle`, and `match_title` all to `NULL` (frontend treats `suggested_match: null` as "no match" regardless of why)
3. Accept `cluster_id` (integer) instead of `cluster_index`

**`enrich_clusters_with_coverage()`** — no change to its logic; it receives the cluster list from `load_clusters()` and enriches in-memory. The input shape stays the same.

**`_load_cluster_context()`** — currently takes a `clusters_data` dict (loaded from JSON elsewhere in `generation.py`). Change the caller in `generation.py` to:
1. Replace `get_service_setting(conn, "keyword_clusters", "{}")` + `json.loads()` with `load_clusters(conn)` to get `clusters_data`
2. Keep loading `target_keywords` from `service_settings` JSON as before (target keywords are NOT being migrated)
3. The `_load_cluster_context` function signature stays the same (still takes a dict, still pure)

**`_match_clusters_to_pages()`** — no change. It operates on in-memory cluster dicts and returns them with `suggested_match` populated. The caller (`generate_clusters`) writes the match data to DB columns instead of the JSON blob.

### API Changes

**`PATCH /api/keywords/clusters/match`** — change `MatchUpdateBody.cluster_index` to `MatchUpdateBody.cluster_id` (integer). The frontend sends the cluster's DB `id` instead of array position.

### Frontend Schema Change

The `clusterSchema` gains an `id` field:

```typescript
const clusterSchema = z.object({
  id: z.number(),
  // ... all existing fields unchanged
});
```

The match mutation sends `cluster_id` instead of `cluster_index`.

### Data Migration

On first load after deployment, `ensure_dashboard_schema()` creates the new tables (empty). A one-time migration function `_migrate_json_to_db(conn)` handles existing data:

1. Check if `service_settings` has a value for key `keyword_clusters`
2. If yes and the `clusters` table is empty: parse the JSON, insert all clusters and keywords into the new tables
3. Delete the `keyword_clusters` key from `service_settings`
4. If the `clusters` table already has data OR no JSON key exists: do nothing

This runs at the top of `load_clusters()`. It is idempotent — safe to call multiple times.

**Edge case:** If `generate_clusters()` is called after migration, it does `DELETE FROM clusters` + fresh inserts. This is correct — regeneration always replaces all clusters.

## Part 2: Cluster Detail Page

### Discovery Chain

Three sources, checked in priority order for deduplication:

1. **Suggested match** — the cluster's `match_type`/`match_handle` (collection, page, or blog_article). Included if `match_type` is not `NULL`, `"new"`, or `"none"`.
2. **Vendor products** — if the cluster has a `matched_vendor` (detected at read-time), query all products where `vendor` matches (case-insensitive).
3. **Collection products** — if the suggested match is a collection, query all products in that collection via the `collection_products` join table.

**Deduplication:** A product could appear via both vendor and collection_products. Deduplicate by `(url_type, handle)` tuple, keeping the first source encountered (suggested_match > vendor > collection_products).

### Keyword Coverage

Each discovered URL gets keyword coverage computed using the existing `_check_keyword_coverage()` function.

**Fields scanned per type:**
- **Products:** `title` + `seo_title` + `seo_description` + `description_html` (all four fields)
- **Collections:** `seo_title` + `seo_description` + `description_html` (three fields)
- **Pages:** `seo_title` + `seo_description` + `body` (three fields)
- **Blog articles:** `seo_title` + `seo_description` + `body` (three fields)

### Vendor Detection Helper

Extract `_detect_vendor()` from `enrich_clusters_with_coverage()` as a shared helper:

```python
def _detect_vendor(
    cluster_name: str,
    cluster_keywords: list[str],
    vendor_map: dict[str, dict],
) -> dict | None:
```

Both `enrich_clusters_with_coverage()` and `get_cluster_detail()` call this instead of duplicating the logic.

### Backend

#### New Endpoint

`GET /api/keywords/clusters/{cluster_id}/detail`

Added to `backend/app/routers/clusters.py`.

**Path parameter:** `cluster_id` (integer) — the cluster's DB primary key.

**Response (200):**

```json
{
  "ok": true,
  "data": {
    "cluster": {
      "id": 7,
      "name": "STLTH Brand Collection",
      "content_type": "collection_page",
      "primary_keyword": "stlth canada",
      "content_brief": "Comprehensive collection page for STLTH devices...",
      "keywords": ["stlth canada", "stlth vape", "stlth pods"],
      "total_volume": 5200,
      "avg_difficulty": 25,
      "avg_opportunity": 72.0,
      "keyword_count": 18,
      "suggested_match": {
        "match_type": "collection",
        "match_handle": "stlth",
        "match_title": "STLTH"
      },
      "matched_vendor": {
        "name": "STLTH",
        "product_count": 9
      }
    },
    "related_urls": [
      {
        "url_type": "collection",
        "handle": "stlth",
        "title": "STLTH",
        "source": "suggested_match",
        "keyword_coverage": { "found": 4, "total": 18 }
      },
      {
        "url_type": "product",
        "handle": "stlth-loop-9k-pod-pack-blue-razz-ice",
        "title": "STLTH Loop 9K Pod Pack - Blue Razz Ice",
        "source": "vendor",
        "keyword_coverage": { "found": 2, "total": 18 }
      }
    ]
  }
}
```

**Error responses:**
- `404` if no cluster with that `id` exists

**`related_urls` fields:**
- `url_type`: `"collection"` | `"page"` | `"blog_article"` | `"product"`
- `handle`: the Shopify handle (for blog articles: `"{blog_handle}/{article_handle}"`)
- `title`: display title of the item
- `source`: `"suggested_match"` | `"vendor"` | `"collection_products"` — how the URL was discovered
- `keyword_coverage`: `{ "found": int, "total": int }` — count of cluster keywords found in the URL's content

**Sorting:** `related_urls` sorted by `keyword_coverage.found` descending (best coverage first).

#### New Service Function

`get_cluster_detail(conn, cluster_id)` in `backend/app/services/keyword_clustering.py`.

Steps:
1. `SELECT * FROM clusters WHERE id = ?` — raise `ValueError` if not found
2. `SELECT keyword FROM cluster_keywords WHERE cluster_id = ?` — get keywords list
3. Detect `matched_vendor` via `_detect_vendor()` helper
4. Build related URLs list via discovery chain:
   - If `match_type` is not `NULL`/`"new"`/`"none"`: load matched page content, compute coverage, add to list
   - If `matched_vendor` exists: query `SELECT handle, title, seo_title, seo_description, description_html FROM products WHERE LOWER(vendor) = ?`, compute coverage per product, add to list
   - If match is a collection: query products via `collection_products` join (`SELECT p.handle, p.title, p.seo_title, p.seo_description, p.description_html FROM products p JOIN collection_products cp ON p.shopify_id = cp.product_shopify_id JOIN collections c ON cp.collection_shopify_id = c.shopify_id WHERE c.handle = ?`), compute coverage, add to list
5. Deduplicate by `(url_type, handle)` — skip any already in list
6. Sort by `keyword_coverage.found` descending
7. Return `{"cluster": enriched_cluster_dict, "related_urls": [...]}`

The router endpoint catches `ValueError` and returns 404.

### Frontend

#### New Route

`/keywords/clusters/:id` → `ClusterDetailPage` component.

**New file:** `frontend/src/routes/cluster-detail-page.tsx`

**Router registration:** Add to `frontend/src/app/router.tsx` with lazy import, following existing pattern.

#### Page Layout

**Header section:** Cluster info card showing:
- "← Back to Keywords" link
- Cluster name (h1)
- Content type badge + vendor badge (if matched)
- Primary keyword
- Content brief
- Stats row: total volume, avg difficulty, avg opportunity, keyword count
- Suggested match with link + keyword coverage badge (same styling as current cluster cards)

**Related URLs section:**
- Section heading: "Related URLs (N)"
- Table with columns: Title (link), Type, Source, Coverage
- Title links to the item's detail page (`/collections/{handle}`, `/products/{handle}`, `/pages/{handle}`, `/articles/{blog_handle}/{article_handle}`)
- Type column: "Collection", "Product", "Page", "Blog Article"
- Source column: subtle badge — "Match", "Vendor", "Collection"
- Coverage column: `found/total` with green (>=50%), yellow (>=25%), red (<25%) coloring — same thresholds as existing badges
- Sorted by coverage descending (backend sorts, frontend preserves order)

**Loading state:** Skeleton card + skeleton table rows (follow existing detail page pattern).

**Error state:** Error card with message (follow existing detail page pattern).

**Empty state:** If `related_urls` is empty, show "No related URLs discovered for this cluster" message instead of an empty table.

**Navigation:**
- "← Back to Keywords" link at top, links to `/keywords`
- Cluster card name on keywords page becomes a `<Link>` to `/keywords/clusters/{id}`

#### Zod Schema

```typescript
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
```

## Files

### Create
- `frontend/src/routes/cluster-detail-page.tsx` ��� detail page component

### Modify
- `shopifyseo/dashboard_store.py` — add `clusters` and `cluster_keywords` table creation in `ensure_dashboard_schema()`
- `backend/app/services/keyword_clustering.py` — migrate all functions to DB, add `get_cluster_detail()`, extract `_detect_vendor()`, add JSON-to-DB migration helper
- `backend/app/routers/clusters.py` — add `GET /{cluster_id}/detail` endpoint, change `PATCH /match` to use `cluster_id`
- `shopifyseo/dashboard_ai_engine_parts/generation.py` — update `_load_cluster_context` caller to use `load_clusters(conn)` instead of raw JSON
- `frontend/src/app/router.tsx` — register new route
- `frontend/src/routes/keywords-page.tsx` — add `id` to schema, make cluster name a Link, change match mutation to use `cluster_id`
- `tests/test_keyword_clustering.py` — update existing tests for DB-based functions, add tests for `get_cluster_detail()` and `_detect_vendor()`

## Testing

### Backend Unit Tests

Tests use an in-memory SQLite connection with tables created via `ensure_dashboard_schema()` (or minimal `CREATE TABLE` statements for the tables under test). Test data is inserted directly via SQL.

**Existing tests to update:**
- Tests for `_group_by_parent_topic`, `_compute_cluster_stats`, `_build_clustering_prompt` — no change (pure functions, no DB)
- Tests for `_load_cluster_context` — no change (still takes dict input)
- Tests for `_check_keyword_coverage` — no change (pure function)

**New tests for `_detect_vendor()`:**
1. **Vendor in cluster name** — "Elf Bar Disposable Vapes" matches vendor "ELFBAR" (case-insensitive)
2. **Vendor in keywords** — cluster keywords contain vendor name
3. **No vendor match** — returns None

**New tests for `get_cluster_detail()`:**
1. **Basic detail with suggested match** — cluster matched to a collection, verify collection appears in related_urls with coverage computed from DB content
2. **Vendor products included** — cluster with matched vendor, verify all vendor products appear with source "vendor"
3. **Collection products included** — cluster matched to collection, verify products from `collection_products` appear with source "collection_products"
4. **Deduplication** — product exists via both vendor and collection_products, verify it appears once with source "vendor" (higher priority)
5. **Cluster not found** — raises ValueError for nonexistent id
6. **No related URLs** — cluster with match_type "new" and no vendor, verify empty related_urls list
7. **match_type "none" skips suggested match** — cluster with explicit "no match" does not include a suggested_match URL
8. **Coverage uses four fields for products** — product with keyword in title but not in other fields still gets found=1
9. **Sorting** — related_urls sorted by found count descending

**New tests for DB migration:**
1. **load_clusters migrates JSON data** — populate service_settings with JSON, call load_clusters, verify data in tables and JSON key removed
2. **load_clusters with empty tables and no JSON** — returns empty result

### TypeScript
- Type check passes with `npx tsc --noEmit`
