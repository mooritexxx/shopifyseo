# Phase 4: Cluster-to-Page Matching & Content Generation Context — Design Spec

## Overview

Two connected features that bridge keyword clusters to actual content generation:

1. **Cluster-to-Page Matching** — During cluster generation, a second LLM call matches each cluster to an existing Shopify collection, page, or blog article (or marks it as "new content"). Users can override matches.
2. **Cluster Context in Content Generation** — When generating SEO content for a matched page, the system injects the cluster's keywords, content brief, and targeting data into the generation prompt.

## 1. Cluster-to-Page Matching

### Integration Point

Added as the final step in the existing `generate_clusters()` function in `backend/app/services/keyword_clustering.py`. Runs automatically after clusters are created and stats computed — no separate button.

### Matching Logic

New function: `_match_clusters_to_pages(conn, clusters, settings) -> list[dict]`

Steps:
1. Query all collection titles + handles from `collections` table
2. Query all page titles + handles from `pages` table
3. Query all blog article titles + handles from `blog_articles` table (composite handle: `blog_handle/article_handle`)
4. If no pages exist in any table, skip matching — all clusters get `suggested_match: null`
5. Build LLM prompt with cluster names/keywords/content_types and page titles/handles/types
6. Call `_call_ai()` with structured JSON schema
7. Return match suggestions

### LLM Output Schema

```json
{
  "name": "matching_result",
  "strict": true,
  "schema": {
    "type": "object",
    "properties": {
      "matches": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "cluster_name": { "type": "string" },
            "match_type": { "type": "string" },
            "match_handle": { "type": "string" },
            "match_title": { "type": "string" }
          },
          "required": ["cluster_name", "match_type", "match_handle", "match_title"],
          "additionalProperties": false
        }
      }
    },
    "required": ["matches"],
    "additionalProperties": false
  }
}
```

`match_type` values: `"collection"`, `"page"`, `"blog_article"`, `"new"`

When `match_type` is `"new"`, `match_handle` and `match_title` are empty strings.

For the manual override dropdown, `"none"` is also accepted — the backend sets `suggested_match` to `null` (clears the match).

### Cluster Data Shape Update

Each cluster gains a `suggested_match` field:

```json
{
  "name": "Elf Bar Disposable Vapes",
  "content_type": "collection_page",
  "primary_keyword": "elf bar canada",
  "content_brief": "...",
  "keywords": ["elf bar canada", "elf bar vape", ...],
  "total_volume": 4500,
  "avg_difficulty": 32,
  "avg_opportunity": 67.5,
  "keyword_count": 8,
  "suggested_match": {
    "match_type": "collection",
    "match_handle": "elf-bar",
    "match_title": "Elf Bar"
  }
}
```

If matching fails (LLM error), `suggested_match` is `null` — clusters still save successfully.

### Progress Messages

Added to the existing SSE stream during "Generate Clusters":
- "Matching clusters to existing pages…" (after stats computation)
- "Done — X clusters generated, Y matched to existing pages" (final message)

### Manual Override

**New endpoint:** `PATCH /api/keywords/clusters/match`

Request body:
```json
{
  "cluster_index": 0,
  "match_type": "collection",
  "match_handle": "elf-bar",
  "match_title": "Elf Bar"
}
```

Uses `cluster_index` (position in the clusters array) instead of cluster name in the URL to avoid URL encoding issues with spaces/special characters. Updates the `suggested_match` for that cluster in `service_settings`. Returns the updated cluster list.

### Notes

- Multiple clusters can match the same page — this is expected (e.g., "Elf Bar Models" and "Elf Bar Reviews" both match the Elf Bar collection). When generating content, all matched clusters are injected as context (capped at 3).
- The matching LLM prompt should prefer aligning `content_type` with page type: `collection_page` clusters → collections, `blog_post`/`buying_guide` clusters → blog articles or new content, `landing_page` clusters → pages.
- **Regeneration clears matches**: When "Generate Clusters" is run again, new clusters replace old ones entirely (including any manual overrides). The matching step runs fresh on the new clusters. This is expected — the user is explicitly requesting a fresh clustering.

## 2. Cluster Context in Content Generation

### Integration Point

Modified in `generate_recommendation()` in `shopifyseo/dashboard_ai_engine_parts/generation.py`, during context building (after `object_context()` returns and before `prompt_context()` is called).

### New Helper Function

`_load_cluster_context(conn, object_type, handle) -> str | None`

Located in `backend/app/services/keyword_clustering.py` (alongside existing clustering functions).

Steps:
1. Load clusters from `service_settings` (key: `keyword_clusters`)
2. Load target keywords from `service_settings` (key: `target_keywords`) to look up volume/difficulty per keyword
3. Find clusters where `suggested_match.match_handle == handle` AND `suggested_match.match_type` maps to the object type:
   - `"collection"` matches `object_type == "collection"`
   - `"page"` matches `object_type == "page"`
   - `"blog_article"` matches `object_type == "blog_article"`
   - `"product"` — no cluster matching (products are matched via collections)
4. If no match found, return `None`
5. If found (cap at 3 clusters), format as a compact context string using keyword metrics from target keywords data:

```
SEO Target Keywords (from cluster "Elf Bar Disposable Vapes"):
- Primary keyword: "elf bar canada" (volume: 1200, difficulty: 35)
- Supporting keywords: elf bar vape (vol: 800), elf bar review (vol: 400), elf bar bc10000 (vol: 300)
- Content angle: Comprehensive collection page for Elf Bar disposable vapes available in Canada.
- Recommended content type: collection_page
```

### Injection Point

In `generate_recommendation()`, after `object_context()` builds the context dict and before `prompt_context()` formats it for LLM consumption. The cluster context is added as a new key (`cluster_seo_context`) in the `effective_context` dict, so it flows into the user prompt alongside existing signals (GSC queries, related items). This requires:

1. Calling `_load_cluster_context(conn, object_type, handle)` during context building
2. If it returns a string, adding it to the context dict as `effective_context["cluster_seo_context"]`
3. Updating `prompt_context()` in `context.py` to include the cluster context block when present
4. No changes to the field generation loop, review pass, or QA validation

### Size Budget

~150-300 tokens per matched cluster. If multiple clusters match the same page, include all (cap at 3 to be safe). Total cluster context injection stays under 900 tokens.

## 3. Frontend Changes

### Cluster Card Updates

Below the content brief on each cluster card, add a match display row:

**Matched to existing page:**
- "→ Elf Bar (Collection)" — clickable link to the page's detail view (`/collections/elf-bar`)
- Small "Change" text button next to it

**Matched as new content:**
- "→ New content" with a green badge (`bg-green-100 text-green-700`)
- Small "Change" text button

**No match (null):**
- "→ No match suggested" in muted text (`text-slate-400`)
- Small "Change" text button

### Match Override Dropdown

Clicking "Change" opens an inline select/dropdown populated with:
- "New content" option at the top
- "No match" option (clears the match, sets `suggested_match` to `null`)
- All collections (grouped under "Collections" header)
- All pages (grouped under "Pages" header)
- All blog articles (grouped under "Blog Articles" header)

Selecting an option fires `PATCH /api/keywords/clusters/match` with the cluster's index and updates the card.

### Data for Dropdown

New endpoint: `GET /api/keywords/clusters/match-options` — returns a flat list of available pages:

```json
{
  "options": [
    { "match_type": "new", "match_handle": "", "match_title": "New content" },
    { "match_type": "none", "match_handle": "", "match_title": "No match" },
    { "match_type": "collection", "match_handle": "elf-bar", "match_title": "Elf Bar" },
    { "match_type": "collection", "match_handle": "disposable-vapes", "match_title": "Disposable Vapes" },
    { "match_type": "page", "match_handle": "about-us", "match_title": "About Us" },
    { "match_type": "blog_article", "match_handle": "news/welcome", "match_title": "Welcome to Our Store" }
  ]
}
```

### Schema Update

Update `clusterSchema` in `keywords-page.tsx`:

```typescript
const matchSchema = z.object({
  match_type: z.string(),
  match_handle: z.string(),
  match_title: z.string(),
});

const clusterSchema = z.object({
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
});
```

## 4. Files to Create / Modify

| File | Changes |
|------|---------|
| `backend/app/services/keyword_clustering.py` | **Modify** — Add `_match_clusters_to_pages()`, `_load_cluster_context()`, matching LLM schema, update `generate_clusters()` to call matching |
| `backend/app/routers/clusters.py` | **Modify** — Add `PATCH /match` and `GET /match-options` endpoints |
| `shopifyseo/dashboard_ai_engine_parts/generation.py` | **Modify** — Call `_load_cluster_context()` during context building in `generate_recommendation()` |
| `shopifyseo/dashboard_ai_engine_parts/context.py` | **Modify** — Include `cluster_seo_context` in `prompt_context()` output when present |
| `frontend/src/routes/keywords-page.tsx` | **Modify** — Update cluster schema, add match display, add override dropdown |
| `tests/test_keyword_clustering.py` | **Modify** — Add tests for `_load_cluster_context()` |

## 5. Testing

Unit tests for pure functions:
- `_load_cluster_context` — returns formatted string when match found, `None` when no match
- `_load_cluster_context` — handles multiple clusters matching same page (caps at 3, includes all)
- `_load_cluster_context` — handles null `suggested_match` gracefully (skips those clusters)
- `_load_cluster_context` — returns `None` for `object_type == "product"` (products don't match clusters)
- `_load_cluster_context` — cross-references target keywords for volume/difficulty metrics

Integration testing is manual (requires AI credentials and Shopify data).

## 6. What This Does NOT Include

- Creating new Shopify pages/articles from "new content" clusters — deferred to a future phase
- Automatic content generation triggered by cluster matching — user still clicks "Generate AI" on the content detail page
- Editing cluster content briefs — read-only for now
- Tracking which clusters have had content generated — no status tracking beyond the match assignment
