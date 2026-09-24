# GSC dimensional analytics (country, device, searchAppearance, page+query)

## Problem

Search performance in the app is almost entirely **aggregated**: per-URL totals from `searchAnalytics/query` (single `page` row + top `query` rows stored in `gsc_query_rows`). We do not persist **how** performance splits by **country**, **device**, **search appearance**, or an explicit **page × query** key beyond the current “queries for this URL” list (query-only; page is implied as the entity URL).

That limits:

- **Prioritization** — cannot spot “mobile CTR collapse” or “CA-only demand” without leaving the app.
- **Keywords / clusters** — cluster cards cannot reflect segment-specific demand.
- **AI generation** — `prompt_context` / `condensed_context` in `shopifyseo/dashboard_ai_engine_parts/context.py` expose query rows and clusters but not segment breakdowns.
- **Sidekick** — `shopifyseo/sidekick.py` builds its system prompt from **`build_sidekick_context_block`**, which today uses the **serialized detail** payload from `get_product_detail` / `get_content_detail` / `get_blog_article_detail` only; it does **not** call `object_context()`. GSC segments will not reach Sidekick unless we **add them to detail responses** and/or extend `build_sidekick_context_block`.

## Goals

1. **Cheapest wins:** extend `searchAnalytics/query` only (no new Google products).
2. **Two tiers of data** — **property-level** breakdowns (bounded API cost) and **per-entity** enrichment (expensive, capped).
3. **Backward compatible** — existing `gsc_query_rows`, entity columns (`gsc_clicks`, etc.), and overview property charts keep working; new data is additive.
4. **Quota-safe** — explicit caps, caching, reuse of existing refresh/throttle patterns.
5. **Honest date labeling** — property Tier A and per-URL Tier B may use **different** date windows today (see §Time windows); UI and docs must state the range for each block.

## Non-goals

- BigQuery export, bulk Search Console UI exports, or Indexing API.
- Replacing GA4 for sessions; GSC remains search-centric.
- Real-time or sub-daily granularity beyond what GSC returns.
- Write operations (sitemaps submit, etc.).
- Storing or displaying **per-query user-level** data (GSC does not provide this; country/device are aggregated).

## Constraints (API)

- **`searchAnalytics/query`** accepts a **list of dimensions**; not all combinations are valid. Invalid requests return 4xx — implementation must use a **verified allowlist** per [Search Analytics query](https://developers.google.com/webmaster-tools/v1/searchanalytics/query). Confirm at Phase 0 whether **more than two dimensions** per request are allowed for your chosen combos (many common reports use **one or two**).
- **`rowLimit`:** up to 25,000 rows per call; still use **low limits** where possible and **`orderBys`** (e.g. by impressions) so the API returns the **head** of the distribution, not an arbitrary slice.
- **searchAppearance** (and other dimensions) may return **empty** or sparse buckets for some properties or date ranges; UI and AI must treat that as “no data,” not failure.
- **page + query** explodes cardinality: persist **top N** per entity (and/or property-level aggregates only), never full cartesian dumps for every URL.

## Time windows (critical alignment)

Today the codebase uses **different** ranges:

| Surface | Approx. window | Code reference |
|---------|----------------|----------------|
| **Overview GSC** (`gsc_site`) | MTD or last two **full calendar months** (matched pairs) | `get_search_console_overview_cached` + `gsc_overview_calendar` |
| **Per-URL GSC cache** (`get_search_console_url_detail`) | **Last 28 days** (end = yesterday) | `dashboard_google.py`: `end_date = today - 1`, `start_date = end_date - 27` |
| **Property summary** (`fetch_search_console_summary`) | **Last 28 days** | Same 28-day pattern |

**Design decision (choose one and document in UI):**

- **Option A (recommended for v1 clarity):** Tier A property breakdowns use the **same calendar windows as Overview** (`mtd` / `full_months` + anchor) so Google Signals / optional Overview strips **match** the property chart narrative. Per-URL Tier B stays **28 days** until a deliberate migration aligns it — **label** Tier B as “Last 28 days” everywhere.
- **Option B:** Tier A also uses **28 days** so property + entity numbers are **comparable**, but then Tier A **does not** match Overview MTD charts — label clearly.
- **Option C (later):** Parameterize per-URL date range to match Overview (more API calls / cache keys).

The spec assumes **Option A** unless product explicitly chooses B.

## Design

### 0. Integration map (existing code)

| Concern | Location |
|---------|-----------|
| GSC POST helpers, per-URL fetch, overview | `shopifyseo/dashboard_google.py` |
| `google_api_cache` types + TTLs | `dashboard_google.py` `CACHE_TTLS`, `_write_cache_payload` |
| Invalidate overview cache | `delete_search_console_overview_cache` (deletes `search_console_overview` rows only) |
| Wipe **all** Google cache | `clear_google_caches` → `DELETE FROM google_api_cache` |
| Write `gsc_query_rows` + entity `gsc_*` columns | `shopifyseo/dashboard_store.py` → `_refresh_object_gsc_into_table` |
| Full signals (GSC+GA4+index+PSI) | `_refresh_object_signals_into_table` → includes GSC path above |
| Bulk GSC sync | `shopifyseo/dashboard_actions.py` `bulk_refresh_search_console` → `get_search_console_url_detail(..., refresh=True)` then `refresh_gsc_signal_data_for_objects` → **`_refresh_object_gsc_into_table` only** (not GA4/index/PageSpeed) |
| AI object context | `shopifyseo/dashboard_ai_engine_parts/context.py` `object_context` → `gsc_query_rows`, `condensed_context`, `prompt_context` |
| Sidekick system prompt | `shopifyseo/sidekick.py` `build_sidekick_context_block` (detail dict only) |
| Summary API | `backend/app/services/dashboard_service.py` `get_dashboard_summary`, `get_google_signals_data` |
| OAuth scopes | `webmasters.readonly` only (no change) |

**Implication:** Tier B dimensional writes must hook **`_refresh_object_gsc_into_table`** (and any code path that replaces it) so **bulk Search Console refresh** picks them up. If dimensional fetches are added only to `_refresh_object_signals_into_table`, **bulk GSC-only jobs would skip them** unless the bulk job is updated to call the same helper.

### 1. Property-level reports (Tier A)

**Purpose:** Feed Google Signals / optional Overview with whole-property splits without N× per-URL calls.

**Fetches** (dimension lists verified in Phase 0):

| Report id | Dimensions | Metrics | Typical use |
|-----------|------------|---------|-------------|
| `prop_country` | `country` | clicks, impressions, ctr, position | Top markets, geo hints for copy |
| `prop_device` | `device` | same | Mobile vs desktop SERP behavior |
| `prop_appearance` | `searchAppearance` | same | SERP feature mix |

**Request hygiene:** No `page` filter. Use **`orderBys`** descending on impressions (or clicks) and a modest **`rowLimit`** (e.g. 200–1000 per report).

**Storage:**

- **`google_api_cache`** with new `cache_type` values, e.g. `gsc_property_country`, `gsc_property_device`, `gsc_property_search_appearance`.
- **Cache key** must include: `site_url`, **window identity** (e.g. `period_mode` + `anchor_date` string matching overview, or explicit `start_date`/`end_date`), and report id — so MTD vs full months do not collide.

**TTL:** Match `search_console_overview` (60 * 60) or refresh in the same **manual refresh** transaction.

**Invalidation:**

- Extend **`delete_search_console_overview_cache`** (or add `delete_gsc_property_breakdown_cache`) so **“refresh GSC overview”** clears Tier A rows too; avoids stale country/device data next to fresh overview charts.
- Document that **`clear_google_caches`** (settings save) already wipes **all** `google_api_cache` including new types.

**API surface:**

- **`get_google_signals_data`:** attach `gsc_property_breakdowns` with `{ window: { start_date, end_date, period_mode? }, country: [...], device: [...], searchAppearance: [...], error?: str }` when cache hits.
- **`get_dashboard_summary`:** optional same block **only on cache read** — never block on live GSC.

### 2. Per-entity reports (Tier B)

**Purpose:** Detail pages, optional list flags, AI `object_context`, and **Sidekick** (via detail payload).

#### 2a. Page × query (refined)

**Today:** `get_search_console_url_detail` uses `dimensions: ["query"]` + page filter; `gsc_query_rows` stores query + metrics.

**Recommendation:** start **minimal** — optional **`page_url` column** on `gsc_query_rows` (from `keys[0]` when using `dimensions: ["page","query"]` + page filter) for future cannibalization checks; **or** keep `["query"]` only if `page+query` adds no new rows for single-URL filter (verify in Phase 0). If switching to `["page","query"]`, validate **aggregation** matches previous totals.

#### 2b. Query × country / device / searchAppearance (per URL)

**Fetch:** `searchAnalytics/query` with `dimensionFilterGroups` page **equals** entity URL and dimensions `["query","country"]`, `["query","device"]`, `["query","searchAppearance"]` (if allowed).

**Storage:** new table **`gsc_query_dimension_rows`**:

| Column | Notes |
|--------|--------|
| `object_type`, `object_handle` | Same semantics as `gsc_query_rows` |
| `query` | From keys |
| `dimension_kind` | `country` \| `device` \| `searchAppearance` |
| `dimension_value` | API string (country ISO code, device enum, appearance string) |
| clicks, impressions, ctr, position | From API |
| `fetched_at` | From cache meta |

**Primary key:** `(object_type, object_handle, query, dimension_kind, dimension_value)`.

**Index:** `(object_type, object_handle, dimension_kind)` for DELETE + SELECT.

**Limits:** Before insert, **DELETE** `WHERE object_type=? AND object_handle=? AND dimension_kind=?`; insert **top M** by impressions (e.g. M=50 per kind). Optionally cap with API `rowLimit` ≈ M.

**Refresh ordering:** Run **after** base `get_search_console_url_detail` refresh succeeds for that URL. **Partial failure:** log and skip; do **not** delete existing dimensional rows for that kind unless the new fetch succeeded (or use transaction).

**URL canonicalization:** GSC matching is sensitive to URL form (http/https, www, trailing slash). Reuse the **exact** `object_url()` string already used for `get_search_console_url_detail`; if mismatch is observed in QA, add a documented normalization step or “inspect canonical” from URL Inspection.

### 3. Blog articles

**Today:** `refresh_object_structured_seo_data` returns early for `blog_article`; no `gsc_*` columns on `blog_articles`. **`gsc_query_rows`** can still hold `blog_article` keys if something inserted them — today refresh does not.

**Phase 1:** Tier A only (property-wide).  
**Phase 2+:** Extend `_table_for_object_type` / `_refresh_object_gsc_into_table` for `blog_article` (composite handle `blog_handle/article_handle`), add SEO columns to `blog_articles` if list views need them, or read-only dimensional rows without denormalized aggregates.

### 4. AI: two surfaces

#### 4a. Generation (`context.py` + `prompts.py`)

- Load **`gsc_query_dimension_rows`** for the entity; compute **aggregates only**:
  - `gsc_device_mix` (impression-weighted),
  - `gsc_top_countries` (top 5 by impressions),
  - `gsc_search_appearances` (top 5),
  - optional `gsc_query_top_country_pairs` (top 5 query+country by impressions) if token budget allows.
- Inject into **`condensed_context` → `seo_fact_summary`** and/or **`prompt_context`** sibling keys so title/description/recommendation prompts can use them.
- **`prompts.py`:** one short **evidence-bound** paragraph when aggregates exist (cite only numbers present in context).

#### 4b. Sidekick (`sidekick.py`)

- **Either** add a compact **`gsc_segment_summary`** (or full `seo_context` mirror) to **detail API** responses from `dashboard_service.get_product_detail` / `get_content_detail` / `get_blog_article_detail`, **or** call `object_context` inside `build_sidekick_context_block` (requires `conn` — already available in `run_sidekick_turn`).
- Prefer **precomputed summary dict** in detail JSON to avoid duplicating DB logic and to keep Sidekick payload bounded.

**Phase note:** Original plan placed “AI” in Phase 3; Sidekick must be **explicit** in the same phase or a sub-task.

### 5. Frontend

| Surface | Behavior |
|---------|-----------|
| **Google Signals** | Tables or bars for Tier A; show **date range / period label** from API. |
| **Overview** | Optional KPI strip from Tier A cache; same labeling. |
| **Detail** | Collapsible “Search segments”; subtitle **“Last 28 days”** if Tier B stays on current URL window. |
| **List pages** | Optional badges via **flags** on list API (avoid large JSON). |
| **Keywords** | Phase 4: cluster / URL hints. |

### 6. Observability & ops

- **Logging:** Per-report failures (country vs device vs appearance) at WARNING with site + window; do not fail entire refresh for one dimension.
- **Bulk sync:** `bulk_refresh_search_console` already rate-limits; Tier B adds **3× API calls per URL** (or batched if Google allows) — **re-evaluate** `GSC_SYNC_RATE_LIMIT_PER_MINUTE` / workers or make dimensional fetch **optional** behind a flag or second sync pass (“deep GSC”).
- **`SYNC_STATE`:** Optional counters `gsc_dimensional_errors` for UI visibility.

### 7. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| API quota / latency | Tier B behind full signal or optional second job; strict row caps; `orderBys`; cache. |
| Date window confusion | §Time windows + explicit labels in API JSON. |
| Bulk job skips dimensional | Wire Tier B only in `_refresh_object_gsc_into_table` (§0). |
| Sidekick misses segments | §4b explicit wiring. |
| Invalid dimension pairs | Phase 0 matrix + unit tests; graceful skip. |
| Token bloat | Aggregates + top-N only in prompts. |
| Long query strings in PK | Rare; if needed, hash `query` for PK and store raw in column (future). |

## Success criteria

- Tier A visible on **Google Signals** (and/or Overview) with **correct date label**, after refresh.
- At least **product** detail shows Tier B segments after signal refresh; bulk GSC refresh populates dimensional rows.
- **Generation** prompts receive segment aggregates when data exists; no regression when absent.
- **Sidekick** receives a bounded segment summary when detail includes it (or block reads from DB).
- Existing overview charts and `gsc_query_rows` semantics preserved for users who do not enable deep refresh.

## Testing

- Mock `google_api_post`: parsing, cache key stability, invalid combo skip.
- SQLite: dimensional DELETE+insert truncation; transaction behavior on partial failure.
- **Regression:** `fetch_seo_facts`, `/api/summary`, `/api/google-signals` without new caches populated.
- **Contract:** Pydantic / Zod schema updates for new fields.
