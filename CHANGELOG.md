# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

- **Restore endpoint for dismissed internal-link suggestions.** `POST /api/internal-links/suggestions/{id}/restore` accepts a reason and actor, restores dismissed suggestions back to "suggested" status, and logs the action in an audit table (`link_suggestion_restore_audit`). Restored suggestions survive subsequent rebuild pipelines via the audit table lookup.

- **`preview_only` flag for manual-weave.** `POST /api/internal-links/suggestions/{id}/manual-weave` accepts an optional `preview_only: true` flag that validates the edit and returns a preview without persisting to the database. When enabled, the response includes `preview_only: true` and `preview_token: null`.

- **Manual-weave endpoint for ai_woven internal-link suggestions.** `POST /api/internal-links/suggestions/{id}/manual-weave` accepts a hand-written sentence addition without running AI generation. The addition must be append-only (no changes to the original sentence), contain exactly one `<a>` link to the suggestion target, pass content compliance checks (anchor quality, numbers, banned wording, stock/availability claims, TVPA flavour), and respect the 8-link cap. Returns a preview token for the standard apply flow. No AI or LLM code path is reachable from this endpoint.

### Fixed

- **Internal-link apply/reconcile now tolerates whitespace differences between block-level HTML tags.** Shopify's editor may normalize whitespace (newlines, spaces) between block elements (p, div, h1-h6, ul, ol, li, table, etc.) when saving. The new `html_equivalent(a, b)` function strips inter-block whitespace before comparing, so reconcile no longer fails on harmless formatting changes. Preformatted elements (pre, textarea, script, style) preserve their whitespace. Text content, attributes, and inline spacing remain strictly compared.

- **AI insert locator matching now normalizes quotes and dashes and supports prefix matching.** Curly quotes (`''""`), em-dashes (`—`), and HTML entities (`&nbsp;`, etc.) are normalized to straight quotes, hyphens, and decoded characters before matching. Locators at least 40 characters long match via prefix if no exact match is found. New distinct error codes `insert_locator_no_match` and `insert_locator_ambiguous` replace the generic `link_conflict` for clearer debugging.

- **Overview missing-meta counts now only include items live on the Online Store.** Products with `status != 'ACTIVE'` or empty `online_store_url`, collections with `api_unreachable = 1`, pages with `is_published = 0`, and articles with `is_published = 0` are now excluded from the "Needs attention" counters on the Overview. The pages sync now stores `is_published` and `published_at` from the Admin API; existing pages remain counted (NULL treated as "unknown/live") until the next pages sync fills the column.

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
