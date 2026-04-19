# Technical Documentation

Authoritative inventory of **current** code artifacts for **Shopify Agentic SEO** (self-hosted SEO operations for Shopify). Canonical paths are under this repo; for HTTP shapes, cross-check **OpenAPI** at `/docs` when in doubt. Ignore paths: `node_modules/`, `frontend/dist/`, `tests/`, `__pycache__/`.

---

## System Architecture

Merchants run a **single-process** app: **FastAPI** (`uvicorn`) serves JSON under `/api/...` and static **Vite + React** assets under `/app/` (same origin, default `http://127.0.0.1:8000`). Catalog and SEO state live in **SQLite** (`sqlite3`); the `**shopifyseo`** package holds sync, Google clients, AI, embeddings, and DB helpers. **Background work** (catalog sync, AI jobs, some research) uses **daemon threads**, not Redis/Celery.


| Layer              | Technology                                                                                                                                             | Location                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------- |
| Frontend           | React 19, Vite 6, TypeScript, Tailwind, TanStack Query 5, React Router 7, Radix UI, Recharts, Zod                                                      | `frontend/src/`                                                             |
| Backend API        | FastAPI, Pydantic, Starlette                                                                                                                           | `backend/app/`                                                              |
| Domain / sync / AI | Python 3.10+, `requests`, Pillow, NumPy                                                                                                                | `shopifyseo/`                                                               |
| Database           | SQLite (WAL), schema via `CREATE TABLE` / `ALTER` on connect                                                                                           | Default file `shopify_catalog.sqlite3` (override `SHOPIFY_CATALOG_DB_PATH`) |
| Caching            | SQLite `google_api_cache`; in-process dicts (e.g. GSC summaries in `shopifyseo/dashboard_google/_gsc.py`); HTTP `Cache-Control: no-store` on SPA shell | No Redis                                                                    |


**Router registration** (`backend/app/main.py`): `article_ideas`, `dashboard`, `products`, `content`, `blogs`, `keywords`, `clusters`, `operations`, `status`, `sidekick`, `actions`, `ai_stream`, `auth`, `embeddings`, `image_seo`, `google_ads_lab`.

**Lifespan:** on startup, reconciles PageSpeed denormalized columns from SQLite cache (`refresh_pagespeed_columns_from_cache_for_all_cached_objects`). **Exception handlers:** `HTTPException` → JSON `{ ok, error }`; `sqlite3.DatabaseError` → 503 with recovery hint. **No CORS middleware** (same-origin SPA). **No API-key/JWT** on routes; **Google OAuth** only for Search Console (`/auth/google/...`).

---

## Data Flow

1. **Settings:** Operator configures Shopify, Google, AI, DataForSEO, etc. via `GET/POST /api/settings` → values persist in `service_settings` and mapped keys override `os.environ` (`shopifyseo/dashboard_config.py`).
2. **Catalog sync:** `POST /api/sync` starts a **background thread** (`shopifyseo/dashboard_actions`) → Shopify Admin GraphQL/REST → rows in catalog tables (`products`, `collections`, `pages`, `blogs`, `blog_articles`, metafields, images, etc.) plus `sync_runs`.
3. **Sync ETA learning:** Each successful sync appends one snapshot to `service_settings.sync_eta_run_history_v2` (last **10** runs): per-module wall `duration_s` and `units` (Shopify kinds, `gsc`, `ga4`, `index`, `pagespeed`, `structured`, etc.). [`compute_sync_eta_seconds`](shopifyseo/dashboard_actions/sync_eta.py) uses **weighted** `sum(duration)/sum(units)` per module across those runs (plus live blend where applicable). Legacy `sync_eta_seconds_per_unit_v1` is unused.
4. **Signals:** Sync (and refreshes) pull **GSC, GA4, URL Inspection, PageSpeed** into SQLite (`SEO_SIGNAL_COLUMNS` on entities, `google_api_cache`, GSC fact tables).
5. **UI:** React app (basename `/app`) calls `/api/...` with TanStack Query; long AI work uses `**GET /api/ai-stream?job_id=`** (SSE) and/or polling `**GET /api/ai-status**`. Sync drawer ETA uses [`useSmoothSyncEta`](frontend/src/hooks/use-smooth-sync-eta.ts) so the countdown is smoothed (stretched decay, capped upward slew) from `GET /api/sync-status`’s `eta_seconds`.
6. **Writebacks:** SEO edits use services that call `**shopifyseo/dashboard_live_updates`** (GraphQL) to push meta/content to Shopify.

---

## API Routes (With Contracts)

**Contract shorthand:** Most JSON routes return `**{ "ok": true, "data": … }`** or `**{ "ok": false, "error": { "code", "message" } }**` (`backend/app/schemas/common.py`). Keyword/cluster/usage routes may use `dict` responses but keep the same top-level `ok` / `data` pattern. **Exact field shapes:** matching module under `backend/app/schemas/` (e.g. `product.py`, `blog.py`). **SSE:** `text/event-stream` for AI stream, article draft stream, cluster generate, competitor research, target research, target metrics refresh.

### Dashboard, status, actions, AI stream


| Method | Path               | Request                                         | Response                           | Purpose                              |
| ------ | ------------------ | ----------------------------------------------- | ---------------------------------- | ------------------------------------ |
| GET    | `/api/summary`     | Query: GSC period / segment params (see router) | `{ ok, data }` — dashboard summary | Overview metrics, rollups, top pages |
| GET    | `/api/sync-status` | —                                               | `{ ok, data }`                     | Catalog sync progress / state        |
| GET    | `/api/ai-status`   | `?job_id=` optional                             | `{ ok, data }`                     | AI job status                        |
| POST   | `/api/ai-stop`     | Body: job id                                    | `{ ok, data }`                     | Cancel AI job                        |
| GET    | `/api/store-info`  | —                                               | `{ ok, data }`                     | Store URL, name, timezone, etc.      |
| POST   | `/api/sync`        | Body: scope, scopes, force flags                | `{ ok, data }`                     | Start catalog sync                   |
| POST   | `/api/sync/stop`   | —                                               | `{ ok, data }`                     | Request sync cancel                  |
| GET    | `/api/ai-stream`   | `?job_id=`                                      | SSE                                | AI job event stream                  |


### Operations / settings / usage


| Method | Path                              | Request                        | Response       | Purpose                                 |
| ------ | --------------------------------- | ------------------------------ | -------------- | --------------------------------------- |
| GET    | `/api/google-signals`             | —                              | `{ ok, data }` | GSC/GA4-related signals for settings UI |
| POST   | `/api/google-signals/site`        | Body: selected site / property | `{ ok, data }` | Save Search Console site / GA4 property |
| POST   | `/api/google-signals/refresh`     | —                              | `{ ok, data }` | Refresh cached Google summary           |
| GET    | `/api/settings`                   | —                              | `{ ok, data }` | Read settings payload                   |
| POST   | `/api/settings`                   | Body: partial settings         | `{ ok, data }` | Save settings                           |
| POST   | `/api/settings/ai-test`           | —                              | `{ ok, data }` | Test AI provider                        |
| POST   | `/api/settings/image-model-test`  | —                              | `{ ok, data }` | Test image generation model             |
| POST   | `/api/settings/vision-model-test` | —                              | `{ ok, data }` | Test vision model                       |
| POST   | `/api/settings/google-ads-test`   | —                              | `{ ok, data }` | Test Google Ads API                     |
| GET    | `/api/settings/shopify-shop-info` | —                              | `{ ok, data }` | Shopify shop metadata                   |
| POST   | `/api/settings/shopify-test`      | —                              | `{ ok, data }` | Test Shopify Admin API                  |
| POST   | `/api/settings/ollama-models`     | Body                           | `{ ok, data }` | List Ollama models                      |
| POST   | `/api/settings/anthropic-models`  | Body                           | `{ ok, data }` | List Anthropic models                   |
| POST   | `/api/settings/gemini-models`     | Body                           | `{ ok, data }` | List Gemini models                      |
| POST   | `/api/settings/openrouter-models` | Body                           | `{ ok, data }` | List OpenRouter models                  |
| GET    | `/api/usage/summary`              | `?days=`                       | `{ ok, data }` | API usage / cost summary                |


### Products


| Method | Path                                            | Request                                        | Response       | Purpose                         |
| ------ | ----------------------------------------------- | ---------------------------------------------- | -------------- | ------------------------------- |
| GET    | `/api/products`                                 | Query: search, sort, pagination, focus filters | `{ ok, data }` | Product list                    |
| GET    | `/api/products/{handle}`                        | Query: `gsc_period`                            | `{ ok, data }` | Product detail + signals + `gsc_queries` (top Search Console queries for the URL, same cache window as GSC cards) |
| POST   | `/api/products/{handle}/refresh`                | —                                              | `{ ok, data }` | Refresh signals / cached data   |
| POST   | `/api/products/{handle}/generate-ai`            | —                                              | `{ ok, data }` | Start full AI generation job    |
| POST   | `/api/products/{handle}/regenerate-field`       | Body: field key, options                       | `{ ok, data }` | Regenerate one SEO field (sync) |
| POST   | `/api/products/{handle}/regenerate-field/start` | Body                                           | `{ ok, data }` | Start background field regen    |
| POST   | `/api/products/{handle}/update`                 | Body: SEO edits                                | `{ ok, data }` | Persist local SEO edits         |
| POST   | `/api/products/{handle}/inspection-link`        | —                                              | `{ ok, data }` | URL Inspection link             |


### Content (collections & pages)


| Method | Path                                               | Request | Response       | Purpose                             |
| ------ | -------------------------------------------------- | ------- | -------------- | ----------------------------------- |
| GET    | `/api/collections`                                 | Query   | `{ ok, data }` | List collections                    |
| GET    | `/api/pages`                                       | Query   | `{ ok, data }` | List pages                          |
| GET    | `/api/collections/{handle}`                        | Query: `gsc_period` | `{ ok, data }` | Collection detail + `gsc_queries`   |
| GET    | `/api/pages/{handle}`                              | Query: `gsc_period` | `{ ok, data }` | Page detail + `gsc_queries`           |
| POST   | `/api/collections/{handle}/update`                 | Body    | `{ ok, data }` | Update collection SEO               |
| POST   | `/api/pages/{handle}/update`                       | Body    | `{ ok, data }` | Update page SEO                     |
| POST   | `/api/collections/{handle}/inspection-link`        | —       | `{ ok, data }` | Inspection link                     |
| POST   | `/api/pages/{handle}/inspection-link`              | —       | `{ ok, data }` | Inspection link                     |
| POST   | `/api/collections/{handle}/refresh`                | —       | `{ ok, data }` | Refresh collection                  |
| POST   | `/api/pages/{handle}/refresh`                      | —       | `{ ok, data }` | Refresh page                        |
| POST   | `/api/collections/{handle}/generate-ai`            | —       | `{ ok, data }` | Start AI for collection             |
| POST   | `/api/pages/{handle}/generate-ai`                  | —       | `{ ok, data }` | Start AI for page                   |
| POST   | `/api/collections/{handle}/regenerate-field`       | Body    | `{ ok, data }` | Regenerate field (sync)             |
| POST   | `/api/pages/{handle}/regenerate-field`             | Body    | `{ ok, data }` | Regenerate field (sync)             |
| POST   | `/api/collections/{handle}/regenerate-field/start` | Body    | `{ ok, data }` | Start field regen (async)           |
| POST   | `/api/pages/{handle}/regenerate-field/start`       | Body    | `{ ok, data }` | Start field regen (async)           |
| POST   | `/api/collections/save-meta`                       | —       | `{ ok, data }` | Push all collection meta to Shopify |
| POST   | `/api/pages/save-meta`                             | —       | `{ ok, data }` | Push all page meta to Shopify       |


### Blogs & articles


| Method | Path                                                                  | Request | Response       | Purpose                                      |
| ------ | --------------------------------------------------------------------- | ------- | -------------- | -------------------------------------------- |
| GET    | `/api/articles`                                                       | Query   | `{ ok, data }` | Flat article list                            |
| GET    | `/api/articles/{blog_handle}/{article_handle}`                        | Query: `gsc_period` | `{ ok, data }` | Article detail + `gsc_queries`             |
| GET    | `/api/articles/{blog_handle}/{article_handle}/keyword-coverage`       | —       | `{ ok, data }` | Target-keyword coverage                      |
| POST   | `/api/articles/{blog_handle}/{article_handle}/update`                 | Body    | `{ ok, data }` | Update article SEO/content                   |
| PATCH  | `/api/articles/{blog_handle}/{article_handle}/publish`                | Body    | `{ ok, data }` | Publish / unpublish                          |
| POST   | `/api/articles/{blog_handle}/{article_handle}/inspection-link`        | —       | `{ ok, data }` | Inspection link                              |
| POST   | `/api/articles/{blog_handle}/{article_handle}/refresh`                | —       | `{ ok, data }` | Refresh article                              |
| POST   | `/api/articles/{blog_handle}/{article_handle}/generate-ai`            | —       | `{ ok, data }` | Start AI for article                         |
| POST   | `/api/articles/{blog_handle}/{article_handle}/regenerate-field`       | Body    | `{ ok, data }` | Regenerate field (sync)                      |
| POST   | `/api/articles/{blog_handle}/{article_handle}/regenerate-field/start` | Body    | `{ ok, data }` | Start field regen                            |
| GET    | `/api/blogs`                                                          | —       | `{ ok, data }` | Blog list                                    |
| GET    | `/api/blogs/shopify-ids`                                              | —       | `{ ok, data }` | Shopify GIDs for blogs                       |
| GET    | `/api/blogs/{blog_handle}/articles`                                   | —       | `{ ok, data }` | Articles in one blog                         |
| POST   | `/api/articles/generate-draft`                                        | Body    | `{ ok, data }` | AI draft → Shopify draft + DB                |
| POST   | `/api/articles/generate-draft-stream`                                 | Body    | SSE            | Same with progress events                    |
| POST   | `/api/articles/create`                                                | Body    | `{ ok, data }` | Create draft/published article via Admin API |


### Article ideas


| Method | Path                                       | Request | Response       | Purpose                                                |
| ------ | ------------------------------------------ | ------- | -------------- | ------------------------------------------------------ |
| GET    | `/api/article-ideas`                       | —       | `{ ok, data }` | List ideas (newest first)                              |
| POST   | `/api/article-ideas/generate`              | Body    | `{ ok, data }` | Gap analysis + AI; save ideas; optional embedding sync |
| DELETE | `/api/article-ideas/{idea_id}`             | —       | `{ ok, data }` | Delete idea                                            |
| PATCH  | `/api/article-ideas/{idea_id}/approve`     | —       | `{ ok, data }` | Approve idea                                           |
| PATCH  | `/api/article-ideas/{idea_id}/status`      | Body    | `{ ok, data }` | Set status                                             |
| PATCH  | `/api/article-ideas/bulk-status`           | Body    | `{ ok, data }` | Bulk status update                                     |
| GET    | `/api/article-ideas/{idea_id}/performance` | —       | `{ ok, data }` | Performance for linked articles                        |


### Keywords & clusters


| Method | Path                                             | Request           | Response       | Purpose                                        |
| ------ | ------------------------------------------------ | ----------------- | -------------- | ---------------------------------------------- |
| GET    | `/api/keywords/seed`                             | —                 | `{ ok, data }` | Load seed keywords                             |
| POST   | `/api/keywords/seed`                             | Body              | `{ ok, data }` | Save seeds                                     |
| POST   | `/api/keywords/seed/generate`                    | —                 | `{ ok, data }` | Auto-generate seeds from catalog               |
| DELETE | `/api/keywords/seed/{keyword}`                   | —                 | `{ ok, data }` | Remove one seed                                |
| GET    | `/api/keywords/competitors`                      | —                 | `{ ok, data }` | Competitor list + profiles + pending suggestions |
| GET    | `/api/keywords/competitors/{domain:path}/detail` | Path: full domain | `{ ok, data }` | Competitor detail                              |
| POST   | `/api/keywords/competitors`                      | Body              | `{ ok, data }` | Add competitor                                 |
| PUT    | `/api/keywords/competitors/discovery-seed`       | Body: `{ url }`   | `{ ok, data }` | Save competitor-discovery seed URL             |
| POST   | `/api/keywords/competitors/discover-from-seed`   | Body: `{ url? }`  | `{ ok, data }` | Run DataForSEO SERP discovery → pending list   |
| POST   | `/api/keywords/competitors/pending/clear`        | —                 | `{ ok, data }` | Clear all pending competitor suggestions       |
| POST   | `/api/keywords/competitors/pending/{domain:path}/approve` | — | `{ ok, data }` | Approve pending suggestion → active competitor |
| POST   | `/api/keywords/competitors/pending/{domain:path}/reject` | — | `{ ok, data }` | Drop a pending suggestion                      |
| POST   | `/api/keywords/competitors/research`             | Body              | SSE            | Site Explorer–style research                   |
| DELETE | `/api/keywords/competitors/{domain:path}`        | —                 | `{ ok, data }` | Remove competitor + blocklist                  |
| GET    | `/api/keywords/target`                           | —                 | `{ ok, data }` | Target keyword set                             |
| POST   | `/api/keywords/target/research`                  | Body              | SSE            | Keywords Explorer research                     |
| POST   | `/api/keywords/target/validate-dataforseo`       | Body              | `{ ok, data }` | Validate DataForSEO + locale                   |
| POST   | `/api/keywords/target/gsc-crossref`              | Body              | `{ ok, data }` | Cross-reference targets with GSC               |
| POST   | `/api/keywords/target/refresh-metrics`           | Body              | SSE            | Refresh volume/difficulty/CPC                  |
| PATCH  | `/api/keywords/target/bulk-status`               | Body              | `{ ok, data }` | Bulk status (`new` / `approved` / `dismissed`) |
| PATCH  | `/api/keywords/target/{keyword}/status`          | Body              | `{ ok, data }` | Single keyword status                          |
| GET    | `/api/keywords/clusters`                         | —                 | `{ ok, data }` | Clusters + coverage                            |
| POST   | `/api/keywords/clusters/generate`                | Body              | SSE            | Generate clusters                              |
| GET    | `/api/keywords/clusters/match-options`           | —                 | `{ ok, data }` | Page match override options                    |
| GET    | `/api/keywords/clusters/{cluster_id}/detail`     | —                 | `{ ok, data }` | Cluster detail                                 |
| PATCH  | `/api/keywords/clusters/match`                   | Body              | `{ ok, data }` | Override cluster → page match                  |


### Embeddings, image SEO, Sidekick, Google Ads lab, Auth


| Method | Path                                                            | Request                           | Response       | Purpose                                    |
| ------ | --------------------------------------------------------------- | --------------------------------- | -------------- | ------------------------------------------ |
| GET    | `/api/embeddings/status`                                        | —                                 | `{ ok, data }` | Embedding store stats                      |
| POST   | `/api/embeddings/refresh`                                       | —                                 | `{ ok, data }` | Full embedding sync (background)           |
| GET    | `/api/embeddings/similar/{object_type}/{handle:path}`           | —                                 | `{ ok, data }` | Semantic neighbors                         |
| GET    | `/api/embeddings/semantic-keywords/{object_type}/{handle:path}` | —                                 | `{ ok, data }` | Semantic keyword matches                   |
| GET    | `/api/embeddings/competitive-gaps/{object_type}/{handle:path}`  | —                                 | `{ ok, data }` | Competitor gap suggestions                 |
| GET    | `/api/embeddings/cannibalization`                               | Query: threshold                  | `{ ok, data }` | Cannibalization pairs                      |
| GET    | `/api/image-seo/product-images`                                 | Query: pagination                 | `{ ok, data }` | Image SEO rows + summary                   |
| POST   | `/api/image-seo/suggest-alt`                                    | Body                              | `{ ok, data }` | Vision-based alt suggestion                |
| POST   | `/api/image-seo/product-images/draft`                           | Body                              | `{ ok, data }` | Draft optimization steps                   |
| POST   | `/api/image-seo/product-images/optimize`                        | Body                              | `{ ok, data }` | Apply optimization to Shopify              |
| POST   | `/api/sidekick/chat`                                            | Body: resource context + messages | `{ ok, data }` | Sidekick SEO chat + optional field updates |
| GET    | `/api/google-ads-lab/context`                                   | —                                 | `{ ok, data }` | Lab UI context                             |
| POST   | `/api/google-ads-lab/invoke`                                    | Body: RPC name + payload          | `{ ok, data }` | Proxy Keyword Planner–style Ads RPCs       |
| GET    | `/auth/google/start`                                            | —                                 | Redirect       | Start Google OAuth                         |
| GET    | `/auth/google/callback`                                         | Query: OAuth params               | Redirect       | OAuth callback → SPA settings              |


### App shell (non-API)


| Method | Path            | Request | Response     | Purpose                               |
| ------ | --------------- | ------- | ------------ | ------------------------------------- |
| GET    | `/`             | —       | 307 redirect | Redirect to `/app/`                   |
| GET    | `/app/{path}`   | —       | `index.html` | SPA shell (`Cache-Control: no-store`) |
| —      | `/app/assets/`* | —       | Static files | Vite build assets                     |


---

## Services

Backend orchestration lives in `backend/app/services/` and delegates to `shopifyseo/*`.


| Name                                        | File                                             | Purpose                                                                                                                       | Dependencies                                                                                                                                |
| ------------------------------------------- | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Dashboard / summary / generic orchestration | `backend/app/services/dashboard_service.py`      | Summary, sync/AI coordination, refresh/regenerate/start-AI for object types, Sidekick entry, re-exports settings/Google tests | `shopifyseo.dashboard_*`, `sidekick`, local helpers                                                                                         |
| Product flows                               | `backend/app/services/product_service.py`        | List/detail, refresh, AI, updates, inspection                                                                                 | `dashboard_actions`, `dashboard_queries`, `dashboard_ai`, `dashboard_live_updates`, `dashboard_store`, `_catalog_helpers`, `object_signals` |
| Article / blog flows                        | `backend/app/services/article_service.py`        | Blog/article list, detail, update                                                                                             | `dashboard_actions`, `dashboard_live_updates`, `dashboard_queries`, `dashboard_store`, `_catalog_helpers`                                   |
| Collections / pages                         | `backend/app/services/content_service.py`        | List/detail/update, bulk meta save                                                                                            | `dashboard_actions`, `dashboard_live_updates`, `dashboard_queries`, `dashboard_store`, `_catalog_helpers`                                   |
| Settings                                    | `backend/app/services/settings_service.py`       | Read/write settings, probes, Shopify/Google/AI tests                                                                          | `dashboard_ai`, `dashboard_google`, `dashboard_config`, `dashboard_http`, `shopify_admin`                                                   |
| Google signals UI                           | `backend/app/services/google_signals_service.py` | GSC/GA4 cache payloads for operations                                                                                         | `dashboard_google`, `gsc_overview_calendar`, `index_status`                                                                                 |
| Store info                                  | `backend/app/services/store_info_service.py`     | Store URL, name, market, timezone                                                                                             | `dashboard_queries`, `dashboard_google`                                                                                                     |
| Overview metrics                            | `backend/app/services/overview_metrics.py`       | Simple GSC/GA4 aggregates                                                                                                     | Fact rows / helpers                                                                                                                         |
| Catalog completion                          | `backend/app/services/catalog_completion.py`     | Meta completion % by segment                                                                                                  | SQLite reads                                                                                                                                |
| Indexing rollup                             | `backend/app/services/indexing_rollup.py`        | URL Inspection buckets by entity                                                                                              | `shopifyseo.dashboard_status`                                                                                                               |
| Index status                                | `backend/app/services/index_status.py`           | Re-export cache/index helpers                                                                                                 | `shopifyseo.dashboard_status`                                                                                                               |
| Object signals                              | `backend/app/services/object_signals.py`         | Detail/signals helpers                                                                                                        | `shopifyseo.dashboard_detail_common`                                                                                                        |
| Catalog helpers                             | `backend/app/services/_catalog_helpers.py`       | Shared sort/segment/detail/inspection; `gsc_queries_from_detail` serializes per-URL GSC query rows for catalog detail APIs      | `dashboard_google`, `dashboard_actions`, `dashboard_queries`, `object_signals`, `index_status`                                              |
| GSC calendar                                | `backend/app/services/gsc_overview_calendar.py`  | Date windows in dashboard TZ                                                                                                  | `DASHBOARD_TZ`, `zoneinfo`                                                                                                                  |
| Google Ads lab                              | `backend/app/services/google_ads_lab_service.py` | Lab context + Ads REST proxy                                                                                                  | `dashboard_google`, `dashboard_config`, `dashboard_http`                                                                                    |
| Keyword research                            | `backend/app/services/keyword_research/`         | Seeds, competitor discovery/research, DataForSEO, targets, metrics refresh. Modules: `__init__` (public API), `research_runner` (seed + competitor + gap flows), `dataforseo_client`, `keyword_db`, `keyword_utils`, `competitor_blocklist` | `dashboard_google`, `dashboard_http`, `embedding_store`, `api_usage`, etc.                                                                  |
| Keyword clustering                          | `backend/app/services/keyword_clustering/`       | Cluster storage, AI generation, match overrides. Modules: `_crud`, `_storage`, `_generation`, `_context`, `_gaps`, `_helpers` | `dashboard_queries`, `dashboard_google`, `dashboard_ai_engine_parts`, `embedding_store`                                                     |
| Image SEO                                   | `backend/app/services/image_seo_service/`        | List rows, alt suggest, draft/apply. Modules: `__init__`, `_catalog`, `_optimizer`                                            | `dashboard_ai_engine_parts`, `dashboard_store`, `product_image_seo`, `shopify_catalog_sync`, image cache                                    |


**Middleware:** none registered; auth is OAuth-only for Google (no global API auth middleware).

---

## Custom Hooks


| Name                | File                                                       | Purpose                          | Key Behavior                                                            |
| ------------------- | ---------------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------- |
| `useStoreInfo`      | `frontend/src/hooks/use-store-info.ts`                     | Load store metadata              | React Query → `GET /api/store-info`, 5m stale                           |
| `useStoreUrl`       | `frontend/src/hooks/use-store-info.ts`                     | Convenience string for store URL | Derived from `useStoreInfo`                                             |
| `useAiStream`       | `frontend/src/hooks/use-ai-stream.ts`                      | Subscribe to AI SSE              | `EventSource` on `/api/ai-stream?job_id=`; merges events until terminal |
| `useAiJobStatus`    | `frontend/src/hooks/use-ai-job-status.ts`                  | Poll AI job                      | `GET /api/ai-status` every 1.5s while running                           |
| `useAiJobStepClock` | `frontend/src/hooks/use-ai-job-step-clock.ts`              | Per-step elapsed UI              | Resets when step/stage changes while running                            |
| `useSyncEventLog`   | `frontend/src/components/shell/sync/use-sync-event-log.ts` | Sync drawer log lines            | Timestamped lines, cap 48                                               |
| `useSmoothSyncEta`  | `frontend/src/hooks/use-smooth-sync-eta.ts`                 | Sync ETA display (drawer)      | 250ms tick; smooths raw `eta_seconds` (stretch + slew caps)              |


---

## Screens / Pages

Router: `frontend/src/app/router.tsx` — `basename: "/app"`. Full browser paths = `/app` + route.


| Name                 | Route                                    | Purpose                          | API areas used                                  |
| -------------------- | ---------------------------------------- | -------------------------------- | ----------------------------------------------- |
| OverviewPage         | `/`                                      | Dashboard overview               | `/api/summary`, sync/status                     |
| ProductsPage         | `/products`                              | Product list                     | `/api/products`                                 |
| ProductDetailPage    | `/products/:handle`                      | Product SEO + signals + Sidekick + Top search queries (GSC) | `/api/products/{handle}`, AI stream, inspection |
| ContentListPage      | `/collections`, `/pages`                 | List collections or pages        | `/api/collections`, `/api/pages`                |
| ContentDetailPage    | `/collections/:handle`, `/pages/:handle` | Collection/page SEO + Sidekick + Top search queries (GSC)   | Content routes, AI, inspection                  |
| BlogsPage            | `/blogs`                                 | Blog list                        | `/api/blogs`                                    |
| BlogArticlesPage     | `/blogs/:blogHandle`                     | Articles in blog                 | `/api/blogs/.../articles`                       |
| ArticlesPage         | `/articles`                              | All articles                     | `/api/articles`                                 |
| ArticleDetailPage    | `/articles/:blogHandle/:articleHandle`   | Article SEO + Sidekick + Top search queries (GSC) | Article routes, draft stream                    |
| KeywordsPage         | `/keywords`                              | Keyword research UI              | `/api/keywords/`*                               |
| ClusterDetailPage    | `/keywords/clusters/:id`                 | Single cluster                   | `/api/keywords/clusters/...`                    |
| CompetitorDetailPage | `/keywords/competitors/:domain`          | Competitor drill-down            | `/api/keywords/competitors/...`                 |
| ArticleIdeasPage     | `/article-ideas`                         | Ideas list                       | `/api/article-ideas`                            |
| IdeaDetailPage       | `/article-ideas/:ideaId`                 | Idea detail                      | `/api/article-ideas/...`                        |
| GoogleAdsLabPage     | `/google-ads-lab`                        | Ads Keyword Planner lab          | `/api/google-ads-lab/*`                         |
| EmbeddingsPage       | `/embeddings`                            | Embeddings tools                 | `/api/embeddings/*`                             |
| ImageSeoPage         | `/image-seo`                             | Product image SEO                | `/api/image-seo/*`                              |
| ApiUsagePage         | `/api-usage`                             | Usage / cost                     | `/api/usage/summary`                            |
| SettingsPage         | `/settings`                              | Integrations + models            | `/api/settings`, tests, `/api/google-signals`   |


**Shell:** `frontend/src/components/shell/app-shell.tsx` wraps routes with nav, sync controls, `SidekickProvider`.

---

## Database Tables

SQLite; schema built in `shopifyseo/shopify_catalog_sync/db.py`, `shopifyseo/dashboard_store.py`, `shopifyseo/dashboard_google/_cache.py`. No Alembic/SQLAlchemy ORM.


| Table                                                                    | Purpose                                    | Key fields / notes                               | Relationships                                    |
| ------------------------------------------------------------------------ | ------------------------------------------ | ------------------------------------------------ | ------------------------------------------------ |
| `sync_runs`                                                              | Sync job audit                             | Status, counts, errors                           | —                                                |
| `products`                                                               | Product catalog + denormalized SEO signals | `shopify_id` PK; GSC/GA4/index/PageSpeed columns | ← variants, images, metafields                   |
| `product_variants`                                                       | Variants                                   | FK → `products`                                  |                                                  |
| `product_images`                                                         | Gallery rows                               | FK → `products`                                  |                                                  |
| `product_metafields`                                                     | Metafields                                 | FK → `products`                                  |                                                  |
| `collections`                                                            | Collections + signals                      | `shopify_id` PK                                  | ← `collection_metafields`, `collection_products` |
| `collection_products`                                                    | Collection–product membership              | FKs                                              | → collections, products                          |
| `pages`                                                                  | Online Store pages + signals               | `shopify_id`                                     |                                                  |
| `blogs`, `blog_articles`                                                 | Blogs and articles + signals               | Article unique `(blog_shopify_id, handle)`       | articles → blogs                                 |
| `shopify_metaobjects`                                                    | Cached metaobjects                         |                                                  |                                                  |
| `product_image_file_cache`                                               | Local image cache metadata                 | `image_shopify_id` PK                            |                                                  |
| `seo_workflow_states`                                                    | Per-object workflow                        | `(object_type, handle)` PK                       |                                                  |
| `service_tokens`                                                         | OAuth tokens                               | `service`                                        |                                                  |
| `service_settings`                                                       | App settings key/value                     | Mirrors env for runtime                          |                                                  |
| `clusters`, `cluster_keywords`                                           | Keyword clusters                           | `cluster_keywords.cluster_id` → `clusters`       |                                                  |
| `gsc_query_rows`, `gsc_query_dimension_rows`                             | GSC query storage                          | Per-URL row cap **20** via [`shopifyseo/gsc_query_limits.py`](shopifyseo/gsc_query_limits.py) (aligned with GSC API fetch, AI context SQL, embedding bundle); dimension rows keyed by `dimension_kind`/`dimension_value` | → entities via object keys                       |
| `seo_recommendations`                                                    | AI/SEO recs                                | Per object                                       |                                                  |
| `keyword_metrics`, `keyword_research_runs`, `keyword_page_map`           | Research + mapping                         | Maps keyword ↔ `object_type`/`object_handle`     |                                                  |
| `competitor_profiles`, `competitor_top_pages`, `competitor_keyword_gaps` | Competitor analysis                        | Domain-scoped; `competitor_top_pages` carries `top_keyword_volume`, `top_keyword_position`, `page_type` | |
| `article_ideas`                                                          | Gap-analysis article ideas                 | `id` PK; `linked_cluster_id`, `status`, `linked_article_handle` legacy 1:1 fields | ← `idea_articles`                                |
| `idea_articles`                                                          | N:M idea ↔ article mapping                 | `(idea_id, blog_handle, article_handle)` unique; `angle_label` for multi-angle drafts | → `article_ideas`                                 |
| `article_target_keywords`                                                | Keywords per article                       | `(blog_handle, article_handle, keyword)` unique; `is_primary`, `source` | → `blog_articles` via handles                    |
| `embeddings`                                                             | Vector chunks                              | `(object_type, object_handle, chunk_index)` PK; `object_type=gsc_queries` bundles **title + canonical URL + top queries** for that entity handle |                                                  |
| `api_usage_log`                                                          | API cost / usage lines                     |                                                  |                                                  |
| `google_api_cache`                                                       | Cached Google API JSON                     | `cache_key`, TTL `expires_at`                    | Optional object refs                             |


---

## Utilities & Constants

### Frontend (`frontend/src/lib/`)


| Name                                                | Purpose                            | File                                              |
| --------------------------------------------------- | ---------------------------------- | ------------------------------------------------- |
| `getJson` / `postJson` / `patchJson` / `deleteJson` | Typed fetch + `{ok,data}` handling | `frontend/src/lib/api.ts`                         |
| `formatHttpErrorDetail`                             | Parse error payloads for UI        | `frontend/src/lib/api.ts`                         |
| `runArticleDraftStream`                             | Consume article draft SSE          | `frontend/src/lib/run-article-draft-stream.ts`    |
| Slug helpers                                        | Align with backend slug rules      | `frontend/src/lib/seo-slug.ts`                    |
| GSC period helpers                                  | Period modes for charts/API        | `frontend/src/lib/gsc-period.ts`                  |
| Settings connection localStorage                    | Persist connection test state      | `frontend/src/lib/settings-connection-storage.ts` |
| Toast helpers                                       | Sonner wrappers                    | `frontend/src/lib/toast-utils.ts`                 |
| `cn` / class merge                                  | Tailwind class merging             | `frontend/src/lib/utils.ts`                       |
| Google Ads CSV helpers                              | Lab / export utilities             | `frontend/src/lib/google-ads-keywords-csv.ts`     |
| AI provider readiness                               | UI gating for models               | `frontend/src/lib/ai-provider-readiness.ts`       |


### Python (`shopifyseo/` — representative)


| Module                                | Purpose                               |
| ------------------------------------- | ------------------------------------- |
| `dashboard_http.py`                   | Shared HTTP session, errors           |
| `dashboard_config.py`                 | `RUNTIME_SETTING_KEYS`, env mapping   |
| `seo_slug.py`                         | Handle/slug normalization             |
| `market_context.py`                   | Primary market / locale constants     |
| `dashboard_ai_engine_parts/config.py` | Default models, limits, provider URLs |
| `dashboard_ai_engine_parts/prompts.py` | Full + slim prompt assembly; slim `seo_description` adds `gsc_query_highlights` (top GSC queries, JSON size cap) |
| `gsc_query_limits.py`                   | `GSC_PER_URL_QUERY_ROW_LIMIT` (20) shared by GSC fetch, context SQL, `gsc_queries` embeddings |
| `debug_session_log.py`                | NDJSON session log helper for Cursor-agent / debug captures |
| `dashboard_actions/_state.py`         | `SYNC_STATE`, `AI_JOBS`, locks        |


---

## Scripts (`scripts/`)


| Script                              | Purpose                                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------------------- |
| `dev-restart-local.sh`              | Developer convenience — restart local dev server / Vite build                             |
| `run_serp_competitors_from_seeds.py` | CLI runner for DataForSEO SERP-based competitor discovery from seed keywords             |


---

## External Integrations


| Service                                                 | Purpose                                            | How invoked                                                                                                                     | Sync / frequency                                 |
| ------------------------------------------------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| Shopify Admin API                                       | Catalog sync, articles, media, live SEO writebacks | GraphQL/REST in `shopifyseo/shopify_catalog_sync/`, `shopify_admin.py`, `dashboard_live_updates.py`, `shopify_product_media.py` | On `POST /api/sync` and refreshes                |
| Google OAuth + GSC + GA4 + Inspection + PageSpeed + Ads | Signals, analytics, lab                            | `shopifyseo/dashboard_google/`*, `GET /auth/google/*`                                                                           | On sync, refresh endpoints, and operator actions |
| DataForSEO                                              | Keyword/competitor research                        | `backend/app/services/keyword_research/` + keywords router                                                                      | On-demand + SSE streams                          |
| OpenAI / Anthropic / Gemini / OpenRouter / Ollama       | AI generation, review, images, vision              | `shopifyseo/dashboard_ai_engine_parts/providers.py` etc.                                                                        | Per generate/regenerate/Sidekick                 |
| Gemini embeddings                                       | Similarity, gaps, cannibalization                  | `shopifyseo/embedding_store.py`                                                                                                 | Sync + `/api/embeddings/refresh`                 |


---

## Business Context


| Topic            | Current state (manual)                                                                                                                                                                         |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Platform**     | Self-hosted **Shopify** SEO operations app (single-tenant per install).                                                                                                                        |
| **Primary goal** | Organic search visibility: catalog + content SEO workflows, GSC/GA4-informed prioritization, AI-assisted copy/meta, keyword and cluster tooling.                                               |
| **Key metrics**  | Surfaced in-app via GSC/GA4 rollups, indexing/PageSpeed signals, overview goals (optional env `OVERVIEW_GOAL_`*).                                                                              |
| **Constraints**  | **No built-in multi-user auth** on the API; relies on network access control for deployments. **No paid ads** requirement is a *business* constraint for some merchants, not enforced in code. |


---

## Incomplete Features / Known Gaps

- **Not inferred from `TODO` comments** in application source (none found in a quick `TODO|FIXME` scan of `*.py` / `*.ts` / `*.tsx` excluding tests).
- **Operator-maintained gaps:** any roadmap items should be recorded here when known.

---

## Tech Debt / Known Issues

- **Process model:** Sync and AI state live **in-memory** in the server process (`shopifyseo/dashboard_actions`); restarts lose in-flight job UI unless persisted paths recover.
- **Security:** API routes are **not** behind app-level JWT/API keys; treat as trusted-network or add a reverse proxy with auth for production.
- **AI HTTP timeouts:** Settings docs note a **fixed long timeout** for AI calls in engine code (verify `dashboard_ai_engine_parts` when tuning).
- **Moz:** `moz_api_token` may appear in settings mapping; confirm whether Moz APIs are fully wired before relying on them.

---

## Dependencies (External Packages)

### Python (pinned in `backend/requirements.txt`)


| Package   | Version | Purpose                    |
| --------- | ------- | -------------------------- |
| fastapi   | 0.123.5 | HTTP API                   |
| uvicorn   | 0.38.0  | ASGI server                |
| pydantic  | 2.12.5  | Request/response models    |
| starlette | 0.50.0  | ASGI toolkit (FastAPI dep) |
| httpx     | 0.28.1  | Async-capable HTTP client  |
| anyio     | 4.12.0  | Async I/O compatibility    |
| pillow    | 11.1.0  | Image handling             |
| requests  | ≥2.31.0 | HTTP for integrations      |
| numpy     | ≥1.24.0 | Numeric helpers            |


### Frontend (`frontend/package.json` ranges)


| Package                     | Version (range) | Purpose            |
| --------------------------- | --------------- | ------------------ |
| react / react-dom           | ^19.0.0         | UI                 |
| react-router-dom            | ^7.6.0          | Routing            |
| @tanstack/react-query       | ^5.68.0         | Server state       |
| @tanstack/react-virtual     | ^3.13.23        | Virtualized lists  |
| vite                        | ^6.2.0          | Build              |
| typescript                  | ~5.7.2          | Types              |
| tailwindcss                 | ^3.4.17         | Styling            |
| zod                         | ^3.25.76        | Runtime validation |
| recharts                    | ^2.15.4         | Charts             |
| sonner                      | ^2.0.7          | Toasts             |
| @tiptap/* (react, starter-kit, image, placeholder) | ^3.x     | Rich text                         |
| @radix-ui/react-* (checkbox, dialog, dropdown-menu, label, popover, progress, scroll-area, select, separator, slot, switch, tabs, tooltip) | ^1–2 | Primitives |
| lucide-react                | ^0.511.0        | Icons              |
| next-themes                 | ^0.4.6          | Theme mode toggling |
| clsx / tailwind-merge / cva | various         | Class utilities    |

**Dev/test:** `vitest` ^3, `@testing-library/{react,jest-dom,user-event}`, `jsdom`, `@vitejs/plugin-react`, `autoprefixer`, `postcss`.


---

## Environment Configs


| Source                                                | Role                                                                                                                                       |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `.env.example` (repo root)                            | Documents `SHOPIFY_`*, `GOOGLE_*`, AI keys, `DATAFORSEO_*`, optional Moz, `DASHBOARD_TZ`, `GSC_DIMENSIONAL_FETCH`, `OVERVIEW_GOAL_*`, etc. |
| `service_settings` + `shopifyseo/dashboard_config.py` | DB-stored settings; `apply_runtime_settings` mirrors selected keys into `os.environ`                                                       |
| `SHOPIFY_CATALOG_DB_PATH`                             | SQLite file path override                                                                                                                  |
| `DASHBOARD_TZ`                                        | Overview calendar default (`America/Vancouver` if unset)                                                                                   |
| `GSC_DIMENSIONAL_FETCH`                               | Dimensional GSC fetch toggle (`shopifyseo/dashboard_store.py`)                                                                             |


---

## Keeping This Doc in Sync

When adding a **router**, **service**, **table**, or **screen**, update the matching section in the **same change** as the code. Prefer verifying paths against `backend/app/routers/*.py`, `frontend/src/app/router.tsx`, and `shopifyseo/dashboard_store.py` / `shopify_catalog_sync/db.py`.