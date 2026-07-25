# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Fixed

- **Trend column was empty on every catalog table.** `trend` was populated by `gsc_page_trend_map` and read correctly by the frontend, but no Pydantic response model declared it — and FastAPI's `response_model` drops undeclared keys silently, so every row arrived without a trend. Added `TrendPayload` (`backend/app/schemas/trend.py`) to `ProductListItem`, `ProductDetailPayload`, `ContentListItem`, `ContentDetailPayload` (also serves article detail), and `AllArticleListItem`. This also restores sorting by the Trend column, which had nothing to sort on. Carriers default to an empty trend rather than `null`, because the frontend's `trendSchema.optional()` accepts `undefined` but rejects `null`.
- Added `tests/test_trend_response_contract.py`, which asserts the field survives the HTTP boundary rather than the service call — a service-level assertion passed for the entire time the bug was live.

### Changed

- **Performance pass across the catalog, dashboard, and AI context paths — no change to any API response.** Verified by diffing 72 captured service outputs before/after (all identical), plus the existing suites (544 Python, 36 frontend).
  - `_fetch_competitor_gaps` went from ~4.4 s to under 0.1 ms per object via expression indexes on `LOWER(keyword)` for `keyword_metrics`, `keyword_page_map`, and `competitor_keyword_gaps`; the joins compare `LOWER(a) = LOWER(b)`, which no plain column index can serve. `object_context` overall: ~6.1 s → ~285 ms.
  - Schema migration and settings mirroring now run once per DB path instead of on every connection (`open_db_connection`: 15 ms → 1.6 ms). Settings saves and OAuth callbacks still re-apply env mirroring, so nothing goes stale.
  - Product/content list and SEO-fact reads select only the columns they use, and each list request scans its table once instead of twice (`GET /api/products`: ~430 ms → ~95 ms; product detail over HTTP: ~20 ms). `products.raw_json` is 11.5 MB of a 16 MB table and was previously read on every list request.
  - `fetch_seo_facts` no longer loads every stored `seo_recommendations` row (4.4 MB of `details_json` for products) to populate a `build_seo_fact` argument that no field reads.
  - `gsc_page_trend_map` takes an optional `keys=` filter; detail views pass a single key instead of aggregating all of `gsc_page_daily` (48 ms → 0.35 ms).
  - `get_dashboard_summary` computes its GSC/GA4 and index rollups with SQL aggregates (`fetch_signal_totals`, `fetch_index_status_counts`, `fetch_catalog_meta_metrics`) rather than materializing a fact dict per catalog object, and no longer derives the six GSC/GA4 metric keys twice.
  - `find_cannibalization_candidates` selects qualifying pairs with NumPy instead of iterating ~533k pairs in Python, and memoizes per-object query sets (3.9× at the default 0.85 threshold; never slower across the API-clamped 0.5–1.0 range).
  - `_cannibalization_risk` batches its `keyword_page_map` lookups into one indexed query per chunk (194 ms → 27 ms across 495 clusters). `LOWER()` stays on both sides in SQL because Python's `str.lower()` case-folds non-ASCII that SQLite's `LOWER()` does not.
  - Product and content list tables sort client-side over the already-fetched result set, so clicking a column header no longer refetches ~1 MB; ordering is verified identical to the server for all 17 sort keys in both directions. `DataTable` rows are memoized (830-row re-sort: 1,508 ms → 698 ms) with all rows still in the DOM, so browser find-in-page keeps working.
  - `QueryClient` sets a default `staleTime` of 30 s so route remounts stop refetching; `invalidateQueries` still refreshes immediately after mutations.

### Added

- `frontend/src/lib/list-sort.ts` — shared client-side ordering for the product/content list tables, mirroring the backend sorters, with unit coverage.
- `TECHNICAL_DOC.md` gains a **Performance Invariants** section and an **Indexes worth knowing** subsection recording the constraints above and their measured cost if broken.

- Settings UI connection status badges and “Test connection” for Shopify Admin (`POST /api/settings/shopify-test`), with optional credential overrides from the form.
- DataForSEO validation accepts optional login/password in the request body (values from the form before save).
- Open-source contributor docs: `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates, `docs/ARCHITECTURE.md`, `Makefile`, and CI workflow for a minimal API smoke test and frontend typecheck.
