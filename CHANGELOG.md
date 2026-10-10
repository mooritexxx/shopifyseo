# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Fixed

- **Opportunity Inbox skips queries for objects that are not live on the Online Store.** `GET /api/opportunities` and `/api/opportunities/stats` now exclude `gsc_query_rows` whose catalog object exists but is unpublished/draft/unreachable (same `_live_where` definition as the Overview counters; a 301-redirected article is unpublished). Rows for objects missing from the catalog are kept. On live data the default list drops 31 of 422 rows (4 unpublished articles, 3,056 of 18,897 impressions), including `many-cigarettes-pack-understanding-smoking`. Dismiss-with-reason is not part of this change.
- **Overview completion tiles count live items only.** `catalog_completion` used the all-rows totals (pages 19, articles 93) against live-only missing-meta counts, so 3 unpublished pages (empty `seo_description`) showed as "complete" (19/19) and 8 unpublished articles inflated the articles total. New `dq.fetch_live_counts` (same `_live_where` definition as the missing-meta counters) supplies the totals: pages 16/16, articles 85/85; products (882) and collections (78) are unchanged. `counts` in `/api/summary` is unchanged.
- **Test-only: three `tests/test_api.py` tests no longer depend on the live DB or a stale stub.** The two targeted-refresh update tests use a stub connection that supports `record_applied` (no reviewed task, no writes); `test_page_detail_contract` and the product-detail GSC-queries test seed a temp DB instead of relying on a live "contact" page / existing products (the latter used to skip silently on an empty CI DB); `test_sqlite_utf8` builds its own invalid-UTF-8 catalog instead of skipping unless the live `shopify_catalog.sqlite3` has a corrupt row 42. The three `--deselect` flags in `.github/workflows/ci.yml` can now be removed (they pass without them); that workflow edit is a separate follow-up because the push token has no `workflow` scope. No app code changed.
- **Postgres cutover float precision and list-order ties.** pgloader CAST
  SQLite `REAL` / `FLOAT` / `DOUBLE` / `DOUBLE PRECISION` to quoted
  `"double precision"` `using float-to-string` (the unquoted multi-word
  target does not parse in pgloader 3.6.10). Values such as
  `gsc_position` 6.682926829268292 survive the load (cutover attempt 2
  mapped them to 4-byte `real`). `verify_values.py` compares every SQLite
  REAL-affinity column to the loaded catalog (`data_type` + exact float
  equality). `ALTER TABLE … ADD COLUMN` on PG uses the same mapping.
  Graph-stats, orphans, clusters, and cannibalization add a deterministic
  final tie-break so equal sort keys match on SQLite and PG.
  Cannibalization `object_a` is the smaller `(type, handle)` — that can
  swap a/b vs main on SQLite when the embedding matrix is ordered.
  `443` vs `443.0` is `SUM(bigint)` → PG `numeric` loaded as float, not
  SQLite REAL affinity; API output is not rewritten.

### Added

- **GSC breakdown fetch: real error reasons, throttle backoff, sync warnings; PageSpeed no longer uses the service-account token.** Dimensional `searchAnalytics` calls log Google `error.status` / `errors[0].reason` / a truncated `error.message` (never tokens or keys). Per-page country/device/searchAppearance fetches pace at ~8/s, retry 429 / quota 403 / leftover 5xx up to 4 attempts with 2/4/8s backoff (honoring `Retry-After`; urllib3 session retries are disabled on this path), trip a permission circuit breaker on the first non-throttle 403, and stop `searchAppearance` after 5 consecutive 400s with the same reason (`["query","searchAppearance"]` is not a supported Search Analytics grouping — [docs](https://developers.google.com/webmaster-tools/v1/how-tos/all-your-data)). Breaker/warning state is scoped to a `gsc_dimensional_run()`. Failed fetches keep previous rows. GSC sync summaries gain `warnings` (real failures only) / `warning_details` (includes `skipped` for deliberate searchAppearance skips; errors unchanged) and the status label appends `; N breakdown warning(s)`. `HttpRequestError` redacts `key=` / `api_key=` / `access_token=` from messages. PageSpeed uses OAuth `openid` when obtainable, otherwise public quota, plus optional `PAGESPEED_API_KEY`; the SA bearer is never sent.
- **Cron-free PG backup hook and Debian 17/main port claim.** After a box
  restart the packaged `17/main` cluster may grab 5432.
  `scripts/ensure-postgres.sh` stops only a positively identified Debian
  cluster (`postmaster.pid` + `/proc/<pid>/comm == postgres`, or
  `pg_lsclusters` status `online` with an exact data-dir match),
  otherwise starts the durable cluster on the next free port and writes
  `listen_port`. `start-app.sh` and `pg-nightly-backup.sh` share
  `scripts/lib/pg-listen-port.sh` (loopback only). Nightly dumps no longer
  depend on cron: `scripts/pg-backup-daemon.sh` (stale-dump check +
  cmdline-checked pidfile loop) is launched from `start-app.sh` when the
  live mark says postgres. Cron install remains for boxes that actually
  run `cron`.

- **Google service-account auth for Search Console and GA4.** GSC/GA4/URL Inspection/sync mint an RS256 JWT bearer token from `GOOGLE_SERVICE_ACCOUNT_FILE` (default `/home/box/secrets/google-sa.json`) when the file parses as a service-account JSON and `cryptography` is importable. OAuth (`/auth/google/start` + `/callback`, stored `service_tokens`) is unchanged and used when the key is missing or the token mint fails (mint failures cool down 120s so GSC/GA4 callers do not retry-storm). Google Ads keeps the OAuth token (`adwords`). `GET /api/google-signals` gains `mode` (`service_account` / `oauth` / null). Settings and sync readiness treat a minted service-account token as connected.
- **Durable PostgreSQL box runtime.** `scripts/ensure-postgres.sh` is reset-durable (`/home/box/pgdata/17/main`, box-writable `unix_socket_directories`, `create_main_cluster=false` before apt, `initdb` only when empty, refuse a busy port before init/start, no role/database create unless `--bootstrap` over the unix socket). `scripts/start-app.sh` peeks at the live mark first: no mark → SQLite (`DATABASE_URL` unset) and ensure-postgres is skipped unless `$PGDATA_DIR` already has `PG_VERSION` (then best-effort); with `/home/box/.config/shopifyseo/pg_live_cutover.json` ensure-postgres is fatal, then `pg.env` is sourced and a missing `DATABASE_URL` or unreachable Postgres is a hard error (a stale `tmp/pg-cutover-*/cutover_mark.json` does not count). `scripts/mark-pg-live.sh` writes/removes that mark. `scripts/pg-nightly-backup.sh` writes `*.dump.partial` and `mv`s on success (`pg_dump -Fc`, keep 7 by filename stamp); `scripts/install-pg-backup-cron.sh` is idempotent, warns if `cron` is not running, and is called from `start-app.sh`. PG path only: `LIKE` → `ILIKE` in `_translate_placeholders`. Runbook: [docs/pg-cutover.md](docs/pg-cutover.md).
- **PostgreSQL cutover tooling (plan 8).** In-repo dry-run / load runner (`scripts/pg_cutover.sh`) wraps pgloader, `post_load_fixups.sql` (including `keyword_metrics.updated_at` text-in-int), `NOT VALID` FKs, identity `setval`, `ANALYZE`, and count/value verifiers. `cluster_keywords` orphans are kept unless `--delete-cluster-orphans`. Rollback helper `scripts/pg_to_sqlite_delta.py` applies PG rows newer than a cutover mark to a SQLite **copy** (never the live file by default). `scripts/ensure-postgres.sh` is box-level PG17+pgvector install and is **not** on the uvicorn start path. Live deploy stays on SQLite; the tools do not set `DATABASE_URL`. Docs: [docs/pg-cutover.md](docs/pg-cutover.md).

- **Restore endpoint for dismissed internal-link suggestions.** `POST /api/internal-links/suggestions/{id}/restore` restores dismissed suggestions back to "suggested" status. Requires `X-Task-Token` authentication (401 if missing/invalid). Actor is derived from the token, not the request body. Reason is required (1-500 chars after strip, 422 on violations). Returns 404 for non-existent suggestions, 409 if not dismissed or has unfinished snapshot. Uses atomic conditional UPDATE with audit logging only on success. Restored suggestions survive rebuild only if their source/target pair remains valid (exists and is linkable); invalid pairs are cleaned up like normal suggestions. The audit table has no foreign key to allow deletes.

- **`preview_only` flag for manual-weave.** `POST /api/internal-links/suggestions/{id}/manual-weave` accepts an optional `preview_only: true` flag that validates the edit and returns a preview without persisting to the database. When enabled, the response includes `preview_only: true` and `preview_token: null`. Preview-only runs the same pending-snapshot check as a real submit (409 if a write is in progress).

- **Single-field regenerate now runs TVPA QA with retry-once-then-reject.** For products, collections, and blog articles, `generate_field_recommendation` (single-field regenerate) now checks the generated content for TVPA flavour violations. Category-group violations (candy, dessert, soda, energy drinks, cannabis) trigger one retry with corrective feedback; if violations persist, the request is rejected with a `RuntimeError`. Style-group violations (nostalgic, treat, testimonial, lifestyle) are returned as warnings only in the new `tvpa_flavour_warnings` field. Full generation already had this check; this extends it to single-field regeneration.

- **Product SEO titles are now deterministic: `<product name> | Vapely Canada`.** No AI is called for product `seo_title` generation — the title is built programmatically from the product name. This ensures the full product name is always preserved word-for-word, including separators like " - " and words like "Disposable Vape". Nothing is added (no '20mg' unless it's in the product name) and nothing is truncated. Titles over 60 characters produce a non-blocking warning. This applies to new AI copy only; existing live titles are not modified. **Product SEO titles are never auto-applied** — they are stored as pending recommendations only, requiring explicit manual apply.

- **Product meta descriptions must contain the full flavour name.** `validate_single_field` now checks that product `seo_description` contains the flavour extracted from the product title. Missing flavour raises a validation error, triggering the retry loop. Missing nicotine strength is a warning only (recorded in `flavour_strength_warnings`). Flavour matching is case-insensitive with `&` ≡ `and` and whitespace collapsed.

- **New module `shopifyseo/dashboard_ai_engine_parts/product_name_tokens.py`** provides pure functions for deterministic SEO title building and flavour/strength token extraction: `RequiredTokens`, `required_product_name_tokens`, `build_deterministic_seo_title`, `check_seo_title_format`, `check_meta_description_tokens`.

- **Refactored TVPA helpers** extracted from inline code in `generation.py`: `_tvpa_allowed_names(context, object_type)` builds the allowlist, `_tvpa_category_issues(text, allowed_names)` returns category-group violations, and `_build_tvpa_retry_feedback(text, allowed_names, field)` constructs corrective feedback. Full generation calls these helpers instead of inline logic.

- **`FieldRegenerateResult` schema** gains `tvpa_flavour_warnings: list[str]`, `flavour_strength_warnings: list[str]`, and `warnings: list[str]` (backward compatible, default empty lists).

- **Manual-weave endpoint for ai_woven internal-link suggestions.** `POST /api/internal-links/suggestions/{id}/manual-weave` accepts a hand-written sentence addition without running AI generation. The addition must be append-only (no changes to the original sentence), contain exactly one `<a>` link to the suggestion target, pass content compliance checks (anchor quality, numbers, banned wording, stock/availability claims, TVPA flavour), and respect the 8-link cap. Returns a preview token for the standard apply flow. No AI or LLM code path is reachable from this endpoint.

### Fixed

- **Internal-link apply/reconcile now tolerates ASCII whitespace differences between block-level HTML tags.** Shopify's editor may normalize whitespace (newlines, spaces, tabs, carriage returns, form feeds) between block elements (p, div, h1-h6, ul, ol, li, table, etc.) when saving. The new `html_equivalent(a, b)` function strips inter-block ASCII whitespace before comparing, so reconcile no longer fails on harmless formatting changes. **Non-breaking space (U+00A0, `&nbsp;`, `&#160;`), ideographic space (U+3000), and other Unicode whitespace are significant and will cause a reconciliation conflict if added/removed.** Tag parsing is quote-aware: `>` inside quoted attribute values does not end the tag. Custom elements like `<p-x>` are not treated as block-level. Preformatted elements (pre, textarea, script, style) preserve their whitespace. Text content, attributes, and inline spacing remain strictly compared.

- **AI insert locator matching now normalizes quotes and dashes and supports prefix matching.** Curly quotes (`''""`), em-dashes (`—`), and HTML entities (`&nbsp;`, etc.) are normalized to straight quotes, hyphens, and decoded characters before matching. Locators at least 40 characters long match via prefix if no exact match is found. Locators that normalize to empty or punctuation-only (e.g., `...`, `…`, `---`) are rejected with `insert_locator_no_match` and will never match empty/`&nbsp;` spacer paragraphs. New distinct error codes `insert_locator_no_match` and `insert_locator_ambiguous` replace the generic `link_conflict` for clearer debugging.

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
