# GSC dimensional analytics — implementation plan

> **For agentic workers:** Implement task-by-task; use TDD where noted. Check boxes as you complete steps.

**Goal:** Add Search Console **dimensional** data (country, device, searchAppearance, and optional explicit page+query) via extended `searchAnalytics/query`, with **property-level** caches (Tier A) and **per-entity** storage (Tier B).

**Design reference:** `docs/superpowers/specs/2026-03-29-gsc-dimensional-analytics-design.md`

**Primary stack:** Python (`shopifyseo/dashboard_google.py`, `shopifyseo/dashboard_store.py`, `shopifyseo/dashboard_actions.py`, `backend/app/services`), SQLite, FastAPI, React.

---

## Phase 0 — Prerequisites & decisions

- [ ] **Step 0.1: Dimension matrix**

  - In a short comment in `dashboard_google.py` or a subsection in this plan, record **exact** JSON bodies that succeed against `searchAnalytics/query` for:
    - Property: `["country"]`, `["device"]`, `["searchAppearance"]`
    - Per-URL: `["query","country"]`, `["query","device"]`, `["query","searchAppearance"]` with page `equals` filter
    - Optional: `["page","query"]` with page filter vs existing `["query"]` only — note whether row totals diverge.
  - Confirm **max dimensions** per request for your account/API version.

- [ ] **Step 0.2: Date-window product choice**

  - Confirm **Option A vs B** from design spec (Tier A aligned to Overview MTD/full months vs Tier A 28d to match per-URL). Default in spec: **Option A** with Tier B labeled “Last 28 days.”

- [ ] **Step 0.3: Deep GSC scope (optional flag)**

  - Decide whether Tier B dimensional calls run on **every** `_refresh_object_gsc_into_table` or only when **`force_refresh`** / a new setting `deep_gsc_dimensions` is true — **strongly recommended** for large catalogs to avoid 3× API multiplication on bulk sync. Document default in Settings or dev-only env e.g. `GSC_DIMENSIONAL_FETCH=1`.

---

## Phase 1 — Property-level breakdowns (Tier A)

- [ ] **Task 1.1: Fetch helper in `shopifyseo/dashboard_google.py`**

  - Add `_fetch_gsc_property_breakdown(conn, site_url, start, end, dimensions: list[str], *, row_limit, order_metric)` → normalized rows `{ keys, clicks, impressions, ctr, position }`.
  - Use `google_api_post`; set **`orderBys`** (e.g. impressions desc) so `rowLimit` returns the **top** slice.
  - Allowlist dimension lists; catch HTTP errors and return structured `{ "error": "...", "rows": [] }` for that report only.

- [ ] **Task 1.2: Cache types + keys + TTL**

  - Add `CACHE_TTLS` entries: `gsc_property_country`, `gsc_property_device`, `gsc_property_search_appearance` (mirror `search_console_overview` unless product chooses longer).
  - Stable `cache_key`: include `site_url`, `cache_type`, and window (`period_mode` + `anchor_date` **or** `start_end` pair per Step 0.2).

- [ ] **Task 1.3: Orchestrator**

  - `get_gsc_property_breakdowns_cached(conn, site_url, period_mode, anchor, refresh=False)` loads/writes **three** cache rows (or one merged payload — if merged, single invalidation key).
  - On manual refresh path, call from the same place that refreshes overview (see Task 1.5).

- [ ] **Task 1.4: Cache invalidation**

  - Extend **`delete_search_console_overview_cache`** in `dashboard_google.py` to also `DELETE FROM google_api_cache WHERE cache_type IN (...)` for the three Tier A types — keeps Signals consistent with “refresh overview.”
  - Verify `clear_google_caches` still wipes everything (no code change expected).

- [ ] **Task 1.5: Wire manual refresh**

  - Update `refresh_google_summary` in `backend/app/services/dashboard_service.py` (and/or `operations` router scope) so Search Console refresh triggers Tier A fetch when `refresh=True`, not only summary/overview.
  - If overview uses `get_search_console_overview_cached(..., refresh=True)` elsewhere, ensure Tier A is refreshed together or document stagger.

- [ ] **Task 1.6: API exposure**

  - Extend **`get_google_signals_data`**: `gsc_property_breakdowns` including **`window: { start_date, end_date, period_mode? }`** and per-dimension rows + optional `errors[]`.
  - Optionally extend **`get_dashboard_summary`** with the same object **only** when read from cache (no network).

- [ ] **Task 1.7: Schemas**

  - Pydantic: extend Google Signals / summary response models under `backend/app/schemas/`.
  - Zod: `frontend/src/types/api.ts` for `googleSignalsSchema` / summary schema.

- [ ] **Task 1.8: Frontend — Google Signals**

  - `google-signals-page.tsx`: three tables or bars; **show date range** from payload; empty and error states.

- [ ] **Task 1.9: Tests**

  - Mock `google_api_post`: one breakdown type → cache write → read.
  - `GET /api/google-signals` includes `gsc_property_breakdowns` when cache seeded.
  - Test: `delete_search_console_overview_cache` removes Tier A cache types.

---

## Phase 2 — Per-entity storage (Tier B)

- [ ] **Task 2.1: Schema — `gsc_query_dimension_rows`**

  - In `ensure_dashboard_schema` (`dashboard_store.py`): `CREATE TABLE IF NOT EXISTS` + index on `(object_type, object_handle, dimension_kind)`.
  - Consider `ON DELETE` behavior: catalog deletes do not cascade today — acceptable; optional cleanup job out of scope.

- [ ] **Task 2.2: Fetch helpers — `dashboard_google.py`**

  - `fetch_gsc_url_query_dimensions(conn, site_url, page_url, start, end, second_dimension: str)` for `query` + `country|device|searchAppearance`.
  - Reuse same **28-day** dates as `get_search_console_url_detail` unless you implement window alignment (then one source of truth function for “URL report dates”).

- [ ] **Task 2.3: Refresh — `_refresh_object_gsc_into_table` only**

  - After existing logic that updates `gsc_query_rows` and entity columns from `get_search_console_url_detail`:
    - If deep fetch enabled (Task 0.3), for each `dimension_kind` run fetch → **DELETE** slice → INSERT top **M**.
  - **Do not** add dimensional fetches only to `_refresh_object_signals_into_table` without also calling from `_refresh_object_gsc_into_table` — **bulk** `refresh_gsc_signal_data_for_objects` only uses the latter.

- [ ] **Task 2.4: Optional page+query**

  - If Phase 0 says switch query fetch to `["page","query"]`: extend `get_search_console_url_detail` or add parallel call; migrate optional `page_url` on `gsc_query_rows` via `_ensure_columns` / `ALTER`.

- [ ] **Task 2.5: Partial failure & transactions**

  - Wrap per-URL dimensional updates so a failed **device** report does not wipe **country** rows (delete only after successful fetch for that kind, or use savepoint).

- [ ] **Task 2.6: API — detail payloads**

  - `dashboard_service.get_product_detail`, `get_content_detail`, etc.: attach `gsc_segment_summary` or grouped lists **capped** server-side.
  - Pydantic detail schemas in `backend/app/schemas/product.py`, `content.py`, etc.

- [ ] **Task 2.7: Frontend — detail**

  - Shared “Search segments” section on `product-detail-page.tsx` / `content-detail-page.tsx` / `article-detail-page.tsx` as applicable; subtitle **Last 28 days** if still on 28d window.

- [ ] **Task 2.8: Bulk sync observability (optional)**

  - If deep fetch default-on: increment `SYNC_STATE` error counter or extend `bulk_refresh_search_console` summary dict with `dimensional_errors`.

- [ ] **Task 2.9: Tests**

  - SQLite: refresh writes ≤ M rows; re-fetch replaces; failure leaves old country rows if using safe delete-after-success.
  - API detail test: capped `gsc_segment_summary`.

---

## Phase 3 — AI generation + Sidekick

- [ ] **Task 3.1: `context.py`**

  - Query `gsc_query_dimension_rows` for entity; compute aggregates (`gsc_device_mix`, `gsc_top_countries`, `gsc_search_appearances`).
  - Add to **`condensed_context`** (`seo_fact_summary` or adjacent keys) and **`prompt_context`** if generation reads the latter.

- [ ] **Task 3.2: `prompts.py`**

  - Evidence-bound segment paragraph when aggregates non-empty.

- [ ] **Task 3.3: Sidekick — `shopifyseo/sidekick.py`**

  - Extend **`build_sidekick_context_block`** with a compact JSON section **`gsc_segment_summary`** sourced from **`detail`** (preferred), **or** load from DB in `run_sidekick_turn` using `conn` + handle — keep **< ~1–2k tokens**.

- [ ] **Task 3.4: Ensure detail includes summary**

  - If Sidekick reads from `detail` only, **`get_*_detail`** must populate `gsc_segment_summary` from DB (same aggregates as `object_context` or shared helper to avoid drift).

- [ ] **Task 3.5: Tests**

  - Context: seeded dimensional rows → aggregate dict.
  - Optional: snapshot test that `build_sidekick_context_block` contains segment section when `detail` includes summary.

---

## Phase 4 — Polish (optional)

- [ ] **Task 4.1: Overview KPI strip** — `overview-page.tsx` + `/api/summary` field from Tier A cache.

- [ ] **Task 4.2: List badges** — `gsc_segment_flags` on list endpoints (`list_content`, product list serializer).

- [ ] **Task 4.3: Blog articles** — composite handle + `_refresh_object_gsc_into_table` support; optional `blog_articles` SEO columns.

- [ ] **Task 4.4: Keywords / clusters** — surface segment hint when primary URL has rows.

- [ ] **Task 4.5: Align per-URL date window with Overview** — if product wants single narrative; new cache keys and `get_search_console_url_detail` date params.

---

## Verification checklist (before merge)

- [ ] Tier A cache invalidated alongside overview delete helper.
- [ ] Tier B wired through **`_refresh_object_gsc_into_table`**; bulk GSC run populates dimensional rows when deep fetch enabled.
- [ ] UI labels **date range** for Tier A vs Tier B.
- [ ] No synchronous unbounded GSC on **`/api/summary`** or list endpoints.
- [ ] **Sidekick** receives segment summary when data exists.
- [ ] `npm run build` and `pytest` green for touched tests.
- [ ] OAuth: still **`webmasters.readonly`** only.

---

## File touch list (expected)

| Area | Files |
|------|--------|
| GSC HTTP + cache + invalidation | `shopifyseo/dashboard_google.py` |
| Schema + refresh (Tier B) | `shopifyseo/dashboard_store.py` |
| Bulk sync | `shopifyseo/dashboard_actions.py` (optional flags / summary fields) |
| Refresh orchestration | `backend/app/services/dashboard_service.py`, `backend/app/routers/operations.py` |
| Detail + list API | `dashboard_service.py`, `backend/app/schemas/product.py`, `content.py`, `blog.py` |
| AI generation | `shopifyseo/dashboard_ai_engine_parts/context.py`, `prompts.py` |
| Sidekick | `shopifyseo/sidekick.py` |
| Frontend | `google-signals-page.tsx`, detail pages, `frontend/src/types/api.ts` |
| Tests | `tests/test_api.py`, new `tests/test_gsc_dimensional.py` (recommended) |

---

## Dependency graph (high level)

```text
Phase 0 (matrix + date decision + deep flag)
    → Phase 1 (Tier A: fetch, cache, invalidate, API, Signals UI)
    → Phase 2 (Tier B: table, fetch, _refresh_object_gsc_into_table, detail API, UI)
    → Phase 3 (aggregates → context/prompts + Sidekick + detail wiring)
    → Phase 4 (polish)
```

Phase 3 **depends** on Phase 2 aggregates existing in DB; Phase 3 **Sidekick** depends on Task 2.6/3.4 detail payload.
