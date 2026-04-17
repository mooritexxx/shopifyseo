# Aggregate Keyword Coverage on Cluster Cards

## Problem

`enrich_clusters_with_coverage()` only checks the `suggested_match` page (collection/page/blog_article) for keyword presence. Products that belong to a cluster (via vendor match or collection membership) are not scanned. This means after generating SEO content for products, the cluster card still shows "0/15 keywords in content" even though keywords ARE present in product content.

The cluster detail page already shows per-URL coverage separately via `get_cluster_detail()`. The card just needs the aggregate.

## Design

### Modify: `enrich_clusters_with_coverage()`

Expand the function to scan all related URLs (same 3-source discovery chain as `get_cluster_detail()`) and aggregate keyword coverage via union.

**Current flow:**
1. Detect vendor (already done)
2. Check suggested_match page content only
3. Return `{"found": N, "total": M}`

**New flow:**
1. Detect vendor (already done, no change)
2. Collect ALL content from related URLs:
   - Suggested match page (collection/page/blog_article) — same as today
   - Vendor products — if `matched_vendor` is non-None, query `products WHERE LOWER(vendor) = ?` and include `title + seo_title + seo_description + description_html`
   - Collection products — if `match_type == "collection"`, query products via `collection_products` join and include same fields
3. Concatenate all content into one string, then run `_check_keyword_coverage()` once
4. Return `{"found": N, "total": M}` — same shape, but now aggregate

**Why concatenate instead of per-URL union:** Simpler and equivalent. `_check_keyword_coverage()` does substring matching on lowercased content. Concatenating all content into one string before checking means any keyword found in ANY URL's content will be detected. No need to track per-URL sets and merge.

**Deduplication of product content:** A product could appear via both vendor match AND collection membership. To avoid double-counting content (which wouldn't affect correctness since we're doing substring matching, but wastes memory), track seen product handles and skip duplicates.

### What doesn't change

- **`get_cluster_detail()`** — untouched, continues to show per-URL coverage on the detail page
- **`_check_keyword_coverage()`** — untouched, same substring matching
- **Frontend cluster card** — already renders `keyword_coverage.found` / `keyword_coverage.total`, no changes needed
- **Frontend cluster detail page** — already shows per-URL coverage table, no changes needed
- **API response shape** — `keyword_coverage` stays `{"found": N, "total": M}` or `null`

### Performance

Current: 1 DB query per cluster (for suggested_match content).
New: Up to 3 DB queries per cluster (suggested_match + vendor products + collection products).

This runs on the keywords list page which typically has 10-30 clusters. The additional queries are simple indexed lookups. Acceptable overhead.

**Batch optimization:** We already batch-load vendor data once. We can also batch-load all product content for known vendors and collection handles upfront to avoid N+1. But given the small cluster count (10-30), the simpler per-cluster approach is fine for now.

### Edge cases

- **No suggested_match** — vendor products and collection products still get checked
- **No vendor detected, no collection match** — falls back to suggested_match only (same as today)
- **Product appears via both vendor and collection** — content included once (dedup by handle)
- **Cluster with match_type "new"** — still returns `None` for coverage (same as today)

### Testing

1. Aggregate finds keywords across suggested_match + products (keyword in product but not collection)
2. Vendor products are included in aggregate
3. Collection products are included in aggregate
4. Deduplication — product in both vendor and collection counted once
5. No regression — cluster with only suggested_match still works
6. No related URLs — returns `None` (same as today)

Existing tests for `enrich_clusters_with_coverage()` should be updated to reflect the new behavior.
