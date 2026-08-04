# Project Index

**Navigational map — where things live.** Deliberately not an explanation of how they work:
that is [TECHNICAL_DOC.md](TECHNICAL_DOC.md) (routes, contracts, tables, invariants) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (runtime shape).

This file is **maintained incrementally**: anything that adds, moves, renames, or deletes a
module updates it in the same change. It is a map, not a source of truth — when it disagrees
with the code, the code wins and the map is the bug.

| Area | Files | Lines |
|---|---:|---:|
| `shopifyseo/` | 82 | ~33.3k |
| `frontend/src/` | 123 | ~29.5k |
| `backend/app/` | 71 | ~17.3k |
| `tests/` | 73 | ~12.1k |

---

## Entry points

| What | Where |
|---|---|
| Backend app | `backend/app/main.py` (FastAPI; registers all routers, lifespan, exception handlers) |
| DB connections | `backend/app/db.py` (`open_db_connection`, schema bootstrap per path) |
| Frontend root | `frontend/src/main.tsx` → `frontend/src/app/` (providers, router; SPA basename `/app`) |
| Run everything | `start_app.sh`; dev loop `scripts/dev-restart-local.sh` |
| Default database | `shopify_catalog.sqlite3` (override `SHOPIFY_CATALOG_DB_PATH`) |

---

## `backend/app/` — HTTP layer

- **`routers/`** — one module per surface: `products`, `content` (collections+pages), `blogs`,
  `article_ideas`, `keywords`, `clusters`, `dashboard`, `operations`, `status`, `actions`,
  `ai_stream`, `sidekick`, `embeddings`, `image_seo`, `google_ads_lab`, `auth`
- **`schemas/`** — Pydantic response models, one module per surface. `common.py` holds the
  `{ok, data}` / `{ok, error}` envelope; `trend.py` the shared trend payload
- **`services/`** — orchestration between routers and the `shopifyseo` package:
  - catalog: `product_service.py`, `content_service.py`, `article_service.py`, `_catalog_helpers.py`
  - dashboard: `dashboard_service.py`, `overview_metrics.py`, `catalog_completion.py`,
    `indexing_rollup.py`, `index_status.py`, `object_signals.py`, `gsc_overview_calendar.py`
  - research: `keyword_research/` (incl. `keyword_db.py`), `keyword_clustering/`
  - integrations: `google_signals_service.py`, `google_ads_lab_service.py`,
    `open_page_rank.py`, `settings_service.py`, `store_info_service.py`, `image_seo_service/`

## `shopifyseo/` — domain package

| Module | Responsibility |
|---|---|
| `dashboard_store.py` | Central store/query surface over SQLite |
| `dashboard_queries/` | Read paths — `_basic_fetchers`, `_seo_facts`, `_object_detail`, `_gsc_dimensions`, `_urls`, `_editors`, `_text_tokens` |
| `dashboard_actions/` | Background work — `_sync`, `_sync_queue`, `_sync_pagespeed`, `_ai`, `_state`, `_rpm_limiter` |
| `dashboard_ai_engine_parts/` | AI generation — `_article_draft`, `_article_ideas`, `prompts`, `context`, `generation`, `providers`, `qa`, `article_draft_compliance`, `serp_draft_context`, `settings`, `config`, `images/` |
| `dashboard_google/` | Google clients — `_gsc`, `_ga4`, `_ads`, `_auth`, `_cache` |
| `shopify_catalog_sync/` | Shopify pull — `products`, `collections`, `pages`, `blogs`, `queries`, `discovery`, `db`, `page_template_enrichment` |
| `dashboard_article_ideas.py` | Idea generation / gap analysis |
| `dashboard_live_updates.py` | Writebacks to Shopify (GraphQL) |
| `embedding_store.py` | Vector index storage and similarity |
| `dashboard_config.py` | Settings ↔ env mirroring |
| `sidekick.py` | Detail-page chat turns |
| `shopify_admin.py`, `dashboard_http.py` | Admin API and HTTP helpers |
| image pipeline | `product_image_seo.py`, `catalog_image_work.py`, `shopify_image_cache.py`, `shopify_product_media.py`, `html_images.py`, `theme_template_images.py` |
| misc | `api_usage.py`, `market_context.py`, `seo_slug.py`, `gsc_query_limits.py`, `sqlite_utf8.py`, `exceptions.py` |

## `frontend/src/` — React SPA

- **`routes/`** — one page per surface: `overview`, `products`, `content-list`, `content-detail`,
  `product-detail`, `articles`, `article-detail`, `blogs`, `blog-articles`, `article-ideas`,
  `idea-detail`, `keywords/`, `cluster-detail`, `competitor-detail`, `embeddings`,
  `image-seo/`, `google-ads-lab`, `api-usage`, `settings*`
- **`components/`** — `ui/` (incl. `data-table.tsx`), `shell/`, `overview/`, `settings/`,
  `sidekick/`, `gsc/`, plus progress panels and `paa-mindmap.tsx`
- **`lib/`** — `api.ts`, `list-sort.ts`, `gsc-period.ts`, `search-console.ts`,
  `run-article-draft-stream.ts`, `ai-provider-readiness.ts`, `seo-slug.ts`, `utils.ts`
- **`types/api.ts`** — shared API types (large; mirrors backend schemas)
- **`hooks/`**, **`app/`** (providers, router), **`test/`**

---

## Largest files (highest change risk)

| Lines | File |
|---:|---|
| 2573 | `shopifyseo/dashboard_ai_engine_parts/_article_draft.py` |
| 1843 | `shopifyseo/dashboard_store.py` |
| 1835 | `frontend/src/routes/idea-detail-page.tsx` |
| 1670 | `shopifyseo/dashboard_google/_gsc.py` |
| 1611 | `shopifyseo/dashboard_article_ideas.py` |
| 1367 | `shopifyseo/embedding_store.py` |
| 1366 | `shopifyseo/dashboard_actions/_sync.py` |
| 1310 | `shopifyseo/dashboard_ai_engine_parts/prompts.py` |
| 1270 | `frontend/src/types/api.ts` |
| 1249 | `frontend/src/routes/settings-page-fields.tsx` |

## Commands

```
make test-api                                  # fast API smoke
PYTHONPATH=. python3 -m pytest tests/ -q       # full python suite
ruff check . --fix && ruff format .            # python lint/format
cd frontend && npx tsc --noEmit                # typecheck
cd frontend && npm run build                   # build SPA (npm run rebuild clears cache)
./scripts/dev-restart-local.sh                 # stop :8000, build, serve
```

## Docs

`README.md` · `TECHNICAL_DOC.md` (canonical reference) · `AGENTS.md` (agent workflow) ·
`CONTRIBUTING.md` · `docs/ARCHITECTURE.md` · `docs/seo-database-blueprint.md` (schema index) ·
`docs/article-draft-data-pipeline.md` · `docs/archive/` (shipped design history)
