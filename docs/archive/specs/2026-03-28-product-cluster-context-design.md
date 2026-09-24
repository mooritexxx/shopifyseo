# Product Cluster Keyword Context for AI Generation

## Problem

`_load_cluster_context()` explicitly returns `None` for products (line 356). This means when generating content for a product (body, seo_title, seo_description), the AI never receives cluster keyword context. Collections, pages, and blog articles already receive it.

We have the discovery infrastructure (`get_cluster_detail`) that knows which clusters relate to products via vendor match and collection membership. We need a lightweight reverse-lookup: given a product, find related clusters and format their keywords as context.

## Design

### New function: `_find_clusters_for_product()`

```python
def _find_clusters_for_product(
    conn: sqlite3.Connection,
    product_handle: str,
    product_vendor: str,
    clusters_data: dict,
) -> list[dict]:
```

**Purpose:** Reverse-lookup — find up to 3 clusters related to a product.

**Parameters:**
- `conn` — DB connection (needed for collection membership query)
- `product_handle` — the product's handle (for collection membership lookup)
- `product_vendor` — the product's vendor string (already loaded in `object_context`, avoids extra DB query)
- `clusters_data` — the already-loaded clusters dict from `load_clusters(conn)`

**Discovery paths (priority order for deduplication):**

1. **Vendor match** — if `product_vendor` is non-empty and at least 3 characters (guards against false-positive substring matches from very short vendor strings like "BC" or "Go"), scan `clusters_data["clusters"]` for any cluster where `vendor_lower` appears in the cluster name (lowercased) or any of its keywords (lowercased). Direct string check, no `_detect_vendor()` / vendor_map needed since we know the specific vendor.

2. **Collection membership** — query which collection handles contain this product:
   ```sql
   SELECT c.handle FROM collections c
   JOIN collection_products cp ON c.shopify_id = cp.collection_shopify_id
   JOIN products p ON p.shopify_id = cp.product_shopify_id
   WHERE p.handle = ?
   ```
   Then scan `clusters_data["clusters"]` for any cluster whose `suggested_match.match_handle` is in that set and `suggested_match.match_type == "collection"`.

**Deduplication:** Track seen cluster IDs. Vendor-matched clusters are added first (higher priority). Cap at 3.

**Returns:** List of cluster dicts (from `clusters_data`, just filtered).

### New function: `_format_cluster_context()`

```python
def _format_cluster_context(
    matched_clusters: list[dict],
    target_data: dict,
) -> str | None:
```

**Purpose:** Format matched clusters into a context string for the LLM prompt. Extracted from the existing formatting logic in `_load_cluster_context()` (lines 386-412).

**Parameters:**
- `matched_clusters` — list of cluster dicts (up to 3)
- `target_data` — target keywords data for volume/difficulty metrics lookup

**Returns:** Formatted string like:
```
SEO Target Keywords (from cluster "STLTH Brand"):
- Primary keyword: "stlth canada" (volume: 500, difficulty: 20)
- Supporting keywords: stlth vape (vol: 300, diff: 15), ...
- Content angle: STLTH collection page
- Recommended content type: collection_page
```

Returns `None` if `matched_clusters` is empty.

### Refactor: `_load_cluster_context()`

The existing function keeps its signature and behavior. Internally, the formatting section (lines 386-412) is replaced with a call to `_format_cluster_context()`. Still returns `None` for products — the product path lives in `generation.py`.

### Caller change: `generation.py`

In the cluster context block (lines 1030-1038), after the existing `_load_cluster_context` call:

```python
from backend.app.services.keyword_clustering import (
    load_clusters, _load_cluster_context, _find_clusters_for_product, _format_cluster_context
)
clusters_data = load_clusters(conn)
from shopifyseo.dashboard_google import get_service_setting as _get_ss
target_raw = _get_ss(conn, "target_keywords", "{}")
target_data = json.loads(target_raw) if target_raw else {}
cluster_ctx = _load_cluster_context(clusters_data, target_data, object_type, handle)

# Product fallback: reverse-lookup clusters via vendor + collection membership
if not cluster_ctx and object_type == "product":
    vendor = (context.get("detail") or {}).get("product", {}).get("vendor", "")
    matched = _find_clusters_for_product(conn, handle, vendor, clusters_data)
    cluster_ctx = _format_cluster_context(matched, target_data)

if cluster_ctx:
    context["cluster_seo_context"] = cluster_ctx
```

### What doesn't change

- **`prompts.py`** — all three field slim context builders (seo_title, seo_description, body) already pass through `cluster_seo_context` when present. No changes needed.
- **`get_cluster_detail()`** — untouched, serves a different purpose (detail page).
- **`_detect_vendor()`** — not used here. Direct string matching is simpler for single-vendor lookup.
- **Tags generation** — intentionally excluded (tags don't need SEO cluster keywords).

### Field coverage

Once `context["cluster_seo_context"]` is set for products, these fields automatically receive it:

| Field | How | Already wired? |
|---|---|---|
| seo_title | `slim_single_field_prompt_context` line 423-425 | Yes |
| seo_description | `_slim_seo_description_context` line 309-311 | Yes |
| body (description_html) | Full context passthrough (line 348) | Yes |
| tags | `_slim_tags_context` (no cluster ctx) | N/A (intentional) |

Single-field generation and full AI generation both benefit — they all go through the same `context` dict.

### Edge cases

- **Product with no vendor and not in any collection** — both paths return nothing, `_format_cluster_context` returns `None`, generation proceeds without cluster context (same as today).
- **Product with empty/NULL vendor** — vendor path skipped, collection path still runs.
- **No clusters generated yet** — `clusters_data["clusters"]` is empty, returns `None`.
- **Vendor name too short (e.g., "Go")** — guarded by minimum length of 3 characters. Vendors shorter than 3 chars skip the vendor path entirely; collection membership path still runs.

### Testing

Tests for `_find_clusters_for_product`:
1. Finds cluster via vendor match
2. Finds cluster via collection membership
3. Deduplicates (same cluster found via both paths)
4. Returns empty list when no matches
5. Caps at 3 clusters
6. Empty vendor skips vendor path, still finds via collection
7. Product not in DB returns empty
8. Vendor shorter than 3 chars skips vendor path

Tests for `_format_cluster_context`:
1. Formats single cluster with metrics
2. Formats multiple clusters
3. Returns None for empty list
4. Handles missing keyword metrics gracefully

Existing `_load_cluster_context` tests remain unchanged (still returns None for products).
