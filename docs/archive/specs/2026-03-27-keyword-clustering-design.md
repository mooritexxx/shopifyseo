# Phase 3: Keyword Clustering & Content Mapping — Design Spec

## Overview

Group approved target keywords into topic clusters using a hybrid approach: the `parent_topic` field on each keyword (ingested as **DataForSEO** `keyword_properties.core_keyword` in `dataforseo_client`; the DB column name is historical) for initial grouping, then LLM refinement to merge similar groups, assign orphans, and generate content-ready cluster metadata. Each cluster becomes a future content brief for AI content generation.

## 1. Clustering Logic

### Pass 1: Parent Topic Grouping

Group approved keywords by their `parent_topic` field (from **DataForSEO** `core_keyword` when using DataForSEO; empty if never filled). Keywords with `null` or empty `parent_topic` become "orphans" for LLM assignment.

### Pass 2: LLM Refinement

Send the initial groups + orphans to the user's configured AI model (`ai_generation_provider` / `ai_generation_model` from settings). The LLM:

1. **Assigns orphans** to existing groups or creates new groups for them
2. **Merges groups** that are too similar (e.g., "elf bar review" and "elf bar canada" → single "Elf Bar" cluster)
3. **Names each cluster** with a clear, descriptive label
4. **Selects a primary keyword** per cluster (highest search opportunity)
5. **Recommends content type** per cluster: one of `"collection_page"`, `"product_page"`, `"blog_post"`, `"buying_guide"`, `"landing_page"`
6. **Writes a content brief** per cluster: 1-2 sentence description of what the page should cover, targeting intent

### LLM Call

Uses the existing AI engine via `_call_ai()` from `shopifyseo/dashboard_ai_engine_parts/generation.py`. Structured JSON output via `json_schema` with `strict: True`.

**System prompt:** Instructs the LLM that it's an SEO content strategist. Provides context: these are keywords for a Canadian online vape store. Each cluster should map to one page on the website. Clusters should be large enough to justify a page (2+ keywords) but focused enough that one page can rank for all keywords in the cluster.

**User prompt:** JSON payload of keyword groups with metrics (keyword, volume, difficulty, opportunity, intent, content_type, parent_topic, ranking_status).

**If the keyword list is large (200+ keywords):** Batch into chunks of ~150 keywords per LLM call, then merge results. This prevents context window issues.

### LLM Output Schema

```json
{
  "name": "clustering_result",
  "strict": true,
  "schema": {
    "type": "object",
    "properties": {
      "clusters": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": { "type": "string" },
            "content_type": { "type": "string" },
            "primary_keyword": { "type": "string" },
            "content_brief": { "type": "string" },
            "keywords": {
              "type": "array",
              "items": { "type": "string" }
            }
          },
          "required": ["name", "content_type", "primary_keyword", "content_brief", "keywords"],
          "additionalProperties": false
        }
      }
    },
    "required": ["clusters"],
    "additionalProperties": false
  }
}
```

## 2. Backend

### New Service File

`backend/app/services/keyword_clustering.py` — keeps clustering logic separate from keyword research.

#### Functions

**`generate_clusters(conn, on_progress) -> dict`**

Steps:
1. Load approved target keywords from `service_settings` (key: `target_keywords`, filter `status == "approved"`)
2. Validate AI settings via `_require_provider_credentials()`
3. Group keywords by `parent_topic` → dict of groups + orphans list
4. Build LLM prompt with groups and orphan keywords
5. Call `_call_ai()` with structured JSON schema
6. Parse LLM response, compute stats per cluster (total_volume, avg_difficulty, avg_opportunity, keyword_count) by looking up each keyword's metrics from the approved keywords data
7. Sort clusters by total opportunity descending
8. Save to `service_settings` (key: `keyword_clusters`) with `generated_at` timestamp
9. Return the cluster payload

**`load_clusters(conn) -> dict`**

Load saved clusters from `service_settings`. Returns `{"clusters": [...], "generated_at": "..."}` or `{"clusters": [], "generated_at": null}` if none exist.

**`_group_by_parent_topic(keywords) -> tuple[dict[str, list], list]`**

Pure function. Groups keyword dicts by `parent_topic` (DataForSEO `core_keyword` when that pipeline ran). Returns `(groups_dict, orphans_list)`.

**`_build_clustering_prompt(groups, orphans) -> tuple[str, str]`**

Pure function. Returns `(system_prompt, user_prompt)` for the LLM call.

**`_compute_cluster_stats(cluster_keywords, all_keywords_map) -> dict`**

Pure function. Given a list of keyword strings and a lookup map, computes `total_volume`, `avg_difficulty`, `avg_opportunity`, `keyword_count`.

### New Router File

`backend/app/routers/clusters.py`

**`GET /api/keywords/clusters`** — Returns saved clusters. Fast DB read.

**`POST /api/keywords/clusters/generate`** — SSE streaming endpoint. Triggers clustering with progress events:
- "Loading approved keywords..."
- "Grouping by parent topic... (X groups, Y orphans)"
- "Refining clusters with AI..."
- "Done — X clusters generated"

Then emits final `done` event with cluster data.

Register router in the FastAPI app alongside the keywords router.

### Cluster Data Shape (saved to service_settings)

```json
{
  "clusters": [
    {
      "name": "Elf Bar Products",
      "content_type": "collection_page",
      "primary_keyword": "elf bar canada",
      "content_brief": "Comprehensive collection page for Elf Bar disposable vapes available in Canada, covering popular models like BC10000 and GH23K with pricing and reviews.",
      "keywords": ["elf bar canada", "elf bar vape", "elf bar review", "elf bar bc10000"],
      "total_volume": 4500,
      "avg_difficulty": 32,
      "avg_opportunity": 67.5,
      "keyword_count": 4
    }
  ],
  "generated_at": "2026-03-27T14:30:00Z"
}
```

## 3. Frontend

### New Tab

Fourth tab in keywords page: **Seed Keywords | Competitors | Target Keywords | Clusters**

### Layout

Card-based grid. Each cluster is a card showing:

- **Cluster name** (bold, top of card)
- **Content type badge** — color-coded pill:
  - Collection Page: `bg-purple-100 text-purple-700`
  - Product Page: `bg-blue-100 text-blue-700`
  - Blog Post: `bg-green-100 text-green-700`
  - Buying Guide: `bg-yellow-100 text-yellow-700`
  - Landing Page: `bg-indigo-100 text-indigo-700`
- **Primary keyword** — displayed prominently below the name
- **Content brief** — 1-2 sentence description in muted text
- **Stats row** — keyword count, total volume, avg difficulty, avg opportunity
- **Keyword list** — collapsible. Each keyword shows volume, difficulty, opportunity, ranking status badge (reuse `RankingBadge` from target keywords table)

### Header

- "Generate Clusters" button (Sparkles icon) — same SSE pattern as research button
- Progress banner during generation
- Last generated timestamp
- Cluster count

### Sorting

Clusters sorted by total opportunity descending (most valuable first). No user-configurable sorting for v1.

### Empty State

When no clusters exist: "No clusters yet. Approve target keywords, then click 'Generate Clusters' to group them into content topics."

### Schema

Add to frontend types:

```typescript
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
});

const clustersResponseSchema = z.object({
  clusters: z.array(clusterSchema),
  generated_at: z.string().nullable(),
});
```

## 4. Files to Create / Modify

| File | Changes |
|------|---------|
| `backend/app/services/keyword_clustering.py` | **Create** — clustering logic, LLM prompt, stats computation |
| `backend/app/routers/clusters.py` | **Create** — GET and POST endpoints |
| `backend/app/main.py` | **Modify** — register clusters router |
| `frontend/src/routes/keywords-page.tsx` | **Modify** — add Clusters tab, ClustersPanel component, SSE generation |
| `tests/test_keyword_clustering.py` | **Create** — tests for grouping, stats computation, prompt building |

## 5. Testing

Unit tests for pure functions only (no LLM mocking needed):

- `_group_by_parent_topic` — correct grouping, orphan handling, empty input
- `_compute_cluster_stats` — correct totals/averages, empty cluster
- `_build_clustering_prompt` — returns non-empty system and user prompts, includes all keywords

Integration test for the endpoint is manual (requires AI credentials).

## 6. What This Does NOT Include

- Editing clusters (drag/drop, rename, merge/split) — read-only for v1
- Automatic content generation from clusters — clusters carry enough metadata (`content_brief`, `content_type`, `primary_keyword`, `keywords`) to feed into the content pipeline in a future phase
- Cannibalization detection — deferred to a future phase when URL-level mapping is available
- Re-clustering individual keywords — full regeneration only
