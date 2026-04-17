# SEO Database Blueprint

This document is an index of the SEO-relevant data points collected and stored in the local SQLite database.

- Primary DB file: `shopify_catalog.sqlite3`
- Logical schema owners:
  - Shopify catalog sync: `shopifyseo/shopify_catalog_sync/*`
  - SEO + signals enrichment: `shopifyseo/dashboard_store.py`
  - Google cache + tokens/settings: `shopifyseo/dashboard_google.py`
  - Keyword/competitor research: `backend/app/services/keyword_research.py`
  - Cluster generation: `backend/app/services/keyword_clustering.py`
  - Article idea pipeline: `shopifyseo/dashboard_article_ideas.py`

---

## 1) Core catalog entities (the SEO surfaces)

### `products`
Data points collected/stored:
- Identity: `shopify_id`, `legacy_resource_id`, `handle`, `title`
- Commercial context: `vendor`, `product_type`, `status`, inventory fields
- Content/SEO fields: `description_html`, `seo_title`, `seo_description`, `tags_json`
- Product attributes/metafield extracts: `battery_size`, `charging_port`, `coil`, `custom_collection`, `device_type`, `nicotine_strength`, `puff_count`, `size`
- Taxonomy/metaobjects: `*_refs_json` and resolved `*_labels_json` fields (battery type, coil connection, color pattern, style, flavor, vaping style)
- Storefront + payload snapshots: `online_store_url`, `options_json`, `featured_image_json`, `raw_json`, `synced_at`
- SEO signal denormalized columns (shared pattern):  
  `gsc_*`, `ga4_*`, `index_*`, `google_canonical`, `pagespeed_*`, `seo_signal_updated_at`

### `collections`
Data points:
- Identity/content: `shopify_id`, `handle`, `title`, `description_html`
- SEO meta: `seo_title`, `seo_description`
- Shopify rule logic snapshot: `rule_set_json`
- Raw payload + sync metadata: `raw_json`, `synced_at`
- Same denormalized SEO signal columns as products

### `pages`
Data points:
- Identity/content: `shopify_id`, `handle`, `title`, `body`
- SEO meta: `seo_title`, `seo_description`
- Raw payload + sync metadata: `raw_json`, `synced_at`
- Same denormalized SEO signal columns as products

### `blogs` and `blog_articles`
`blogs` stores blog-level metadata (`title`, `handle`, policy/tags, raw payload).  
`blog_articles` stores:
- Identity: `shopify_id`, `blog_shopify_id`, `blog_handle`, `handle`, `title`
- Content/meta: `body`, `summary`, `seo_title`, `seo_description`, `tags_json`, `author_name`, publish flags/dates
- Media/raw payload: `image_json`, `raw_json`, `synced_at`
- Same denormalized SEO signal columns as products

### Relationship/detail tables
- `product_variants`: SKU/price/inventory/options/image for product variants
- `product_images`: image URL, dimensions, alt text (critical for image SEO audits)
- `product_metafields`: all product metafields (namespace/key/value)
- `collection_metafields`: all collection metafields
- `collection_products`: collection membership map (`collection` <-> `product`)
- `shopify_metaobjects`: referenced metaobjects for attribute labels and display names

---

## 2) Search/traffic/indexing/performance signals

## Per-object denormalized signal fields
Stored directly on: `products`, `collections`, `pages`, `blog_articles`.

Data points:
- **GSC per URL aggregate**: `gsc_clicks`, `gsc_impressions`, `gsc_ctr`, `gsc_position`, `gsc_last_fetched_at`
- **GA4 per URL aggregate**: `ga4_sessions`, `ga4_views`, `ga4_avg_session_duration`, `ga4_last_fetched_at`
- **URL Inspection/indexing**: `index_status`, `index_coverage`, `google_canonical`, `index_last_fetched_at`
- **PageSpeed**: `pagespeed_performance`, `pagespeed_seo`, `pagespeed_status`, `pagespeed_last_fetched_at`
- **Last enrichment stamp**: `seo_signal_updated_at`

## Query-level GSC detail tables

### `gsc_query_rows`
Data points:
- Dimensions: `object_type`, `object_handle`, `url`, `query`
- Metrics: `clicks`, `impressions`, `ctr`, `position`
- Freshness: `fetched_at`, `updated_at`
- Typical `object_type` values: `product`, `collection`, `page`, `blog_article`
- Legacy/cleanup compatibility: `blog` may still appear in historical rows and is still handled by prune logic

Purpose:
- Keeps top-query granularity per URL/object for gap discovery and keyword/page mapping.

### `gsc_query_dimension_rows`
Data points:
- Dimensions: `object_type`, `object_handle`, `query`, `dimension_kind`, `dimension_value`
  - `dimension_kind` in practice: `country`, `device`, `searchAppearance`
- Metrics: `clicks`, `impressions`, `ctr`, `position`
- Freshness: `fetched_at`, `updated_at`

Purpose:
- Segment-level GSC intelligence (geo/device/SERP appearance mix).

### `google_api_cache`
Data points:
- Cache addressing: `cache_key`, `cache_type`, `object_type`, `object_handle`, `url`, `strategy`
- Cached payload: `payload_json`
- Cache lifecycle: `fetched_at`, `expires_at`, `updated_at`

Cache types include:
- `search_console_summary`, `search_console_url`, `search_console_overview`
- `gsc_property_country`, `gsc_property_device`, `gsc_property_search_appearance` (+ `_prev`)
- `ga4_summary`, `ga4_property_overview`
- `url_inspection`, `pagespeed`

Purpose:
- API cost/rate-limit control and fast dashboard reads.
- Note: `object_type` may include legacy `blog` cache rows in older datasets.

---

## 3) Keyword intelligence and opportunity modeling

### `keyword_metrics`
This is the central keyword intelligence table.

Data points:
- Keyword fundamentals: `keyword`, `volume`, `global_volume`, `difficulty`, `traffic_potential`, `cpc`, `clicks`, `cps`, `word_count`
- Intent and content mapping: `intent`, `intent_raw`, `content_type_label`, `content_format_hint`, `is_local`
- SERP/context: `serp_features`, `parent_topic`, `first_seen`, `serp_last_update`, `source_endpoint`
- Opportunity + pipeline state: `opportunity`, `ranking_status`, `status`, `updated_at`
- Our rank/performance: `gsc_position`, `gsc_clicks`, `gsc_impressions`
- Lineage: `seed_keywords`
- Competitor overlays: `competitor_domain`, `competitor_position`, `competitor_url`, `competitor_position_kind`

### `keyword_page_map`
Data points:
- `keyword` mapped to (`object_type`, `object_handle`)
- Source and metrics: `source`, `gsc_clicks`, `gsc_impressions`, `gsc_position`, `is_primary`, `updated_at`

Purpose:
- Tells which existing page currently captures each keyword.

### `keyword_research_runs`
Data points:
- Run metadata: `started_at`, `finished_at`, `endpoint`, `seed_or_domain`
- Ingestion stats: `rows_returned`, `rows_new`, `rows_updated`
- Outcome: `status`, `error_message`

Purpose:
- Optional run log for keyword research (reserved; older DBs may still show `ahrefs_research_runs` until `ensure_dashboard_schema` renames it once).

Note:
- Schema init renames legacy `ahrefs_research_runs` → `keyword_research_runs` when present.

---

## 4) Competitor intelligence

### `competitor_profiles`
Data points:
- Competitor identity: `domain`
- Overlap/market metrics: `keywords_common`, `keywords_they_have`, `keywords_we_have`, `share`, `traffic`
- Control/freshness: `is_manual`, `updated_at`

### `competitor_top_pages`
Data points:
- Competitor page: (`competitor_domain`, `url`)
- Leading keyword + strength: `top_keyword`, `top_keyword_volume`, `top_keyword_position`
- Page-level traffic footprint: `total_keywords`, `estimated_traffic`, `traffic_value`, `page_type`, `updated_at`

### `competitor_keyword_gaps`
Data points:
- Gap key: (`keyword`, `competitor_domain`)
- Competitor rank evidence: `competitor_position`, `competitor_url`
- Our side: `our_ranking_status`, `our_gsc_position`
- Opportunity metrics: `volume`, `difficulty`, `traffic_potential`, `gap_type`, `updated_at`

Purpose:
- Prioritizes “they rank and we do not / rank worse” opportunities.

---

## 5) Clustering, ideation, and execution tracking

### `clusters`
Data points:
- Cluster definition: `name`, `content_type`, `primary_keyword`, `content_brief`
- Cluster scoring: `total_volume`, `avg_difficulty`, `avg_opportunity`, `avg_cps`
- SERP/format guidance: `dominant_serp_features`, `content_format_hints`
- Existing-page match decision: `match_type`, `match_handle`, `match_title`
- Lifecycle: `generated_at`

### `cluster_keywords`
Data points:
- Cluster membership: (`cluster_id`, `keyword`)

### `article_ideas`
Data points:
- Idea core: `suggested_title`, `brief`, `primary_keyword`, `supporting_keywords`, `search_intent`
- Source/gap linkage: cluster and collection linkage fields, `gap_reason`, `source_type`
- Opportunity metadata: `total_volume`, `avg_difficulty`, `opportunity_score`, `estimated_monthly_traffic`
- SERP/content hints: `dominant_serp_features`, `content_format_hints`, `content_format`, `linked_keywords_json`
- Workflow linkage: `status`, `linked_article_handle`, `linked_blog_handle`, `shopify_article_id`, `created_at`

### `seo_recommendations`
Data points:
- Recommendation target: `object_type`, `object_handle`
- Recommendation content: `category`, `priority`, `summary`, `details_json`
- AI/system metadata: `source`, `status`, `model`, `prompt_version`, `error_message`
- Timestamps: `created_at`, `updated_at`

### `seo_workflow_states`
Data points:
- Execution state per object: (`object_type`, `handle`) -> `status`, `notes`, `updated_at`

Purpose:
- Tracks human/AI SEO work from recommendation to done.

---

## 6) Integrations, settings, auth, and sync telemetry

### `service_settings`
Stores configuration and mutable app state as key/value.

SEO-relevant keys commonly used:
- Search/analytics: `search_console_site`, `ga4_property_id`
- Keyword research state: `seed_keywords`, `target_keywords`, `competitor_domains`, `competitor_domain_blocklist`, `competitor_research_meta`
- Store identity context: `shopify_domain` (used in competitor discovery)
- API credentials/settings (integration dependent): `dataforseo_api_login`, `dataforseo_api_password`, `moz_api_token`, plus runtime AI/provider keys
- Runtime model/provider controls (stored and read by app): generation/sidekick/review/image provider+model keys, prompt profile/version, retry policy

### `service_tokens`
OAuth token storage (Google):
- `service`, `access_token`, `refresh_token`, `token_type`, `expires_at`, `scope`, `raw_json`, `updated_at`

### `sync_runs`
Catalog sync telemetry:
- Start/end/status, per-entity sync counters, and error details
- Counter data points: `products_synced`, `variants_synced`, `images_synced`, `metafields_synced`, `collections_synced`, `collection_metafields_synced`, `pages_synced`, `collection_products_synced`, `blogs_synced`, `blog_articles_synced`

### `ai_lesson_marks` (live DB auxiliary table)
Data points:
- `issue`, `status`, `updated_at`

Notes:
- Present in the live schema but not defined in the current repository migration/create-table code.
- Treated as auxiliary metadata (not core SEO scoring/signals), but included here for completeness.

---

## 7) Current live footprint (snapshot)

Current row counts in `shopify_catalog.sqlite3` at blueprint time:
- `products`: 579
- `collections`: 49
- `pages`: 16
- `blogs`: 1
- `blog_articles`: 10
- `gsc_query_rows`: 78
- `gsc_query_dimension_rows`: 0
- `keyword_metrics`: 753
- `clusters`: 31
- `cluster_keywords`: 360
- `article_ideas`: 10
- `competitor_profiles`: 22
- `competitor_top_pages`: 535
- `competitor_keyword_gaps`: 204
- `seo_recommendations`: 2496
- `seo_workflow_states`: 401
- `google_api_cache`: 1323
- `ai_lesson_marks`: 3

---

## 8) Practical “data point index” by SEO function

- **Technical SEO**: index coverage/status, canonical, PageSpeed scores/status, URL inspection payloads
- **Organic performance**: GSC clicks/impressions/CTR/position at URL and query level
- **Engagement quality**: GA4 sessions/views/avg session duration per URL + property-level overview caches
- **On-page content quality**: SEO title/description, body length proxies, image alt text, metafields, product attributes
- **Keyword strategy**: keyword volume/difficulty/opportunity/intent/format hints + GSC rank overlays
- **Competitor strategy**: overlap profiles, competitor top pages, keyword gap matrix
- **Planning and execution**: clusters, cluster keywords, article ideas, recommendation history, workflow status/notes
- **Operational metadata**: sync run telemetry, service settings/tokens, auxiliary lesson marks

---

## 9) Appendix — Table ownership, refresh, staleness, and SEO use

| Table | Source system | Refresh trigger | Staleness / TTL semantics | Primary SEO use case |
|---|---|---|---|---|
| `products` | Shopify Admin GraphQL | `sync-products`, `sync-all`, single-object sync | Snapshot at `synced_at`; no TTL | Product page content + metadata baseline |
| `product_variants` | Shopify Admin GraphQL | Product sync | Snapshot at `synced_at`; no TTL | Variant schema/commercial context for content relevance |
| `product_images` | Shopify Admin GraphQL | Product sync | Snapshot at `synced_at`; no TTL | Image SEO audit (alt text, asset coverage) |
| `product_metafields` | Shopify Admin GraphQL | Product sync | Snapshot at `synced_at`; no TTL | Attribute enrichment for targeting and internal linking |
| `collections` | Shopify Admin GraphQL | `sync-collections`, `sync-all`, single-object sync | Snapshot at `synced_at`; no TTL | Collection page optimization baseline |
| `collection_metafields` | Shopify Admin GraphQL | Collection sync | Snapshot at `synced_at`; no TTL | Collection-specific SEO enrichment inputs |
| `collection_products` | Shopify Admin GraphQL | Collection sync | Snapshot at `synced_at`; no TTL | Mapping products to collection landing pages |
| `pages` | Shopify Admin GraphQL | `sync-pages`, `sync-all`, single-object sync | Snapshot at `synced_at`; no TTL | Static page optimization baseline |
| `blogs` | Shopify Admin GraphQL | `sync-blogs`, `sync-all`, single-blog sync | Snapshot at `synced_at`; no TTL | Blog container inventory and cleanup |
| `blog_articles` | Shopify Admin GraphQL | `sync-blogs`, `sync-all`, single-article sync | Snapshot at `synced_at`; no TTL | Article SEO surfaces + content performance |
| `shopify_metaobjects` | Shopify Admin GraphQL | Product sync (metaobject fetch/upsert) | Snapshot at `synced_at`; no TTL | Resolve taxonomy labels for product attributes |
| `google_api_cache` | Google APIs (GSC, GA4, URL Inspection, PSI) | On-demand reads with optional `refresh=true`; manual refresh actions | TTL by `cache_type`; hard metadata in `fetched_at`/`expires_at` | Cached source-of-truth payloads for signals/overview |
| `gsc_query_rows` | Google Search Console (URL query report) | Object signal refresh / bulk signal refresh | Not TTL-based; replaced per object refresh; `fetched_at` stamped | Query-level gap detection and keyword mapping |
| `gsc_query_dimension_rows` | Google Search Console (query x segment) | Object signal refresh when dimensional fetch enabled | Not TTL-based; replaced per object+dimension; `fetched_at` stamped | Country/device/search appearance segmentation |
| `service_tokens` | Google OAuth token exchange/refresh | OAuth callback + token refresh flow | `expires_at` controls validity; refresh-token backed | Auth continuity for GSC/GA4/PSI ingestion |
| `service_settings` | App/user settings writes | Settings save, keyword/competitor workflows, Google selection save | Last-write-wins key/value; no TTL | Integrations, targets, seeds, and runtime knobs |
| `keyword_metrics` | DataForSEO Labs + app enrichment + GSC crossref | Seed/competitor research runs, GSC crossref sync | `updated_at` per upsert; no TTL | Master keyword opportunity scoring and prioritization |
| `keyword_page_map` | Derived from `gsc_query_rows` | Keyword sync helpers after research/crossref | `updated_at` per upsert; no TTL | Map keyword demand to existing URL ownership |
| `keyword_research_runs` | Reserved run metadata (optional) | Research run lifecycle | Immutable run records; no TTL | Auditability of keyword ingestion quality/cost |
| `competitor_profiles` | DataForSEO competitors domain (+ manual domain set) | Competitor research pipeline | `updated_at` per domain; no TTL | Competitor overlap/traffic landscape |
| `competitor_top_pages` | DataForSEO relevant pages | Competitor research pipeline | `updated_at` per domain+URL; no TTL | Identify winning competitor content/page types |
| `competitor_keyword_gaps` | Derived from `keyword_metrics` competitor overlays | Competitor gap sync helper | `updated_at` per keyword+domain; no TTL | “They rank, we do not” opportunity backlog |
| `clusters` | AI clustering over approved keywords + DB stats | Cluster generation run | Fully regenerated on run; no TTL | Topic architecture and page-match recommendations |
| `cluster_keywords` | Derived from clustering output | Cluster generation run | Rebuilt with clusters; no TTL | Cluster membership and gap logic |
| `article_ideas` | AI + data-driven ideation pipeline | Idea generation and CRUD updates | Persistent workflow state; no TTL | Content pipeline from gap to article |
| `seo_recommendations` | App/AI recommendation generation | Per-object recommendation actions | Event-style append with statuses; no TTL | Optimization recommendations and history |
| `seo_workflow_states` | Human/app workflow updates | Status/note updates from UI/services | Upsert current state; no TTL | Track fix status across SEO objects |
| `sync_runs` | Catalog sync orchestrator | Each sync invocation | Immutable run log; no TTL | Operational QA on catalog freshness/completeness |
| `ai_lesson_marks` | Auxiliary app metadata (live DB) | App-internal write path (not in current repo schema code) | Unknown/auxiliary; no documented TTL | Non-core SEO metadata; completeness of DB inventory |

---

## 10) Full field dictionary

Generated from the live schema in `shopify_catalog.sqlite3`.

Legend:
- `type`: SQLite declared type
- `not_null`: 1 means NOT NULL constraint exists
- `pk`: 1-based primary-key position (0 means not part of PK)
- `default`: SQLite default value expression (if any)

### `keyword_research_runs`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `id` | `INTEGER` | 0 | 1 | `` |
| `started_at` | `INTEGER` | 1 | 0 | `` |
| `finished_at` | `INTEGER` | 0 | 0 | `` |
| `endpoint` | `TEXT` | 1 | 0 | `` |
| `seed_or_domain` | `TEXT` | 1 | 0 | `` |
| `rows_returned` | `INTEGER` | 0 | 0 | `0` |
| `rows_new` | `INTEGER` | 0 | 0 | `0` |
| `rows_updated` | `INTEGER` | 0 | 0 | `0` |
| `status` | `TEXT` | 1 | 0 | `'running'` |
| `error_message` | `TEXT` | 0 | 0 | `` |

### `ai_lesson_marks`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `issue` | `TEXT` | 0 | 1 | `` |
| `status` | `TEXT` | 1 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `article_ideas`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `id` | `INTEGER` | 0 | 1 | `` |
| `suggested_title` | `TEXT` | 1 | 0 | `` |
| `brief` | `TEXT` | 1 | 0 | `` |
| `primary_keyword` | `TEXT` | 1 | 0 | `''` |
| `supporting_keywords` | `TEXT` | 1 | 0 | `'[]'` |
| `search_intent` | `TEXT` | 1 | 0 | `'informational'` |
| `linked_cluster_id` | `INTEGER` | 0 | 0 | `` |
| `linked_cluster_name` | `TEXT` | 1 | 0 | `''` |
| `linked_collection_handle` | `TEXT` | 1 | 0 | `''` |
| `linked_collection_title` | `TEXT` | 1 | 0 | `''` |
| `gap_reason` | `TEXT` | 1 | 0 | `''` |
| `status` | `TEXT` | 1 | 0 | `'idea'` |
| `created_at` | `INTEGER` | 1 | 0 | `` |
| `total_volume` | `INTEGER` | 1 | 0 | `0` |
| `avg_difficulty` | `REAL` | 1 | 0 | `0.0` |
| `opportunity_score` | `REAL` | 1 | 0 | `0.0` |
| `dominant_serp_features` | `TEXT` | 1 | 0 | `''` |
| `content_format_hints` | `TEXT` | 1 | 0 | `''` |
| `content_format` | `TEXT` | 1 | 0 | `''` |
| `source_type` | `TEXT` | 1 | 0 | `'cluster_gap'` |
| `linked_keywords_json` | `TEXT` | 1 | 0 | `'[]'` |
| `estimated_monthly_traffic` | `INTEGER` | 1 | 0 | `0` |
| `linked_article_handle` | `TEXT` | 1 | 0 | `''` |
| `linked_blog_handle` | `TEXT` | 1 | 0 | `''` |
| `shopify_article_id` | `TEXT` | 1 | 0 | `''` |

### `blog_articles`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `blog_shopify_id` | `TEXT` | 1 | 0 | `` |
| `blog_handle` | `TEXT` | 1 | 0 | `` |
| `title` | `TEXT` | 1 | 0 | `` |
| `handle` | `TEXT` | 1 | 0 | `` |
| `published_at` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 0 | 0 | `` |
| `is_published` | `INTEGER` | 1 | 0 | `0` |
| `body` | `TEXT` | 0 | 0 | `` |
| `summary` | `TEXT` | 0 | 0 | `` |
| `tags_json` | `TEXT` | 1 | 0 | `` |
| `author_name` | `TEXT` | 0 | 0 | `` |
| `seo_title` | `TEXT` | 0 | 0 | `` |
| `seo_description` | `TEXT` | 0 | 0 | `` |
| `image_json` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |
| `gsc_clicks` | `INTEGER` | 0 | 0 | `` |
| `gsc_impressions` | `INTEGER` | 0 | 0 | `` |
| `gsc_ctr` | `REAL` | 0 | 0 | `` |
| `gsc_position` | `REAL` | 0 | 0 | `` |
| `gsc_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `ga4_sessions` | `INTEGER` | 0 | 0 | `` |
| `ga4_views` | `INTEGER` | 0 | 0 | `` |
| `ga4_avg_session_duration` | `REAL` | 0 | 0 | `` |
| `ga4_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `index_status` | `TEXT` | 0 | 0 | `` |
| `index_coverage` | `TEXT` | 0 | 0 | `` |
| `google_canonical` | `TEXT` | 0 | 0 | `` |
| `index_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_performance` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_seo` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_status` | `TEXT` | 0 | 0 | `` |
| `pagespeed_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `seo_signal_updated_at` | `TEXT` | 0 | 0 | `` |

### `blogs`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `title` | `TEXT` | 1 | 0 | `` |
| `handle` | `TEXT` | 1 | 0 | `` |
| `created_at` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 0 | 0 | `` |
| `comment_policy` | `TEXT` | 0 | 0 | `` |
| `tags_json` | `TEXT` | 1 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `cluster_keywords`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `cluster_id` | `INTEGER` | 1 | 1 | `` |
| `keyword` | `TEXT` | 1 | 2 | `` |

### `clusters`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `id` | `INTEGER` | 0 | 1 | `` |
| `name` | `TEXT` | 1 | 0 | `` |
| `content_type` | `TEXT` | 1 | 0 | `` |
| `primary_keyword` | `TEXT` | 1 | 0 | `` |
| `content_brief` | `TEXT` | 1 | 0 | `` |
| `total_volume` | `INTEGER` | 1 | 0 | `0` |
| `avg_difficulty` | `REAL` | 1 | 0 | `0.0` |
| `avg_opportunity` | `REAL` | 1 | 0 | `0.0` |
| `match_type` | `TEXT` | 0 | 0 | `` |
| `match_handle` | `TEXT` | 0 | 0 | `` |
| `match_title` | `TEXT` | 0 | 0 | `` |
| `generated_at` | `TEXT` | 1 | 0 | `` |
| `dominant_serp_features` | `TEXT` | 0 | 0 | `` |
| `content_format_hints` | `TEXT` | 0 | 0 | `` |
| `avg_cps` | `REAL` | 0 | 0 | `` |

### `collection_metafields`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `collection_shopify_id` | `TEXT` | 1 | 0 | `` |
| `namespace` | `TEXT` | 1 | 0 | `` |
| `key` | `TEXT` | 1 | 0 | `` |
| `type` | `TEXT` | 0 | 0 | `` |
| `value` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `collection_products`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `collection_shopify_id` | `TEXT` | 1 | 1 | `` |
| `product_shopify_id` | `TEXT` | 1 | 2 | `` |
| `product_handle` | `TEXT` | 0 | 0 | `` |
| `product_title` | `TEXT` | 0 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `collections`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `title` | `TEXT` | 1 | 0 | `` |
| `handle` | `TEXT` | 1 | 0 | `` |
| `updated_at` | `TEXT` | 0 | 0 | `` |
| `description_html` | `TEXT` | 0 | 0 | `` |
| `seo_title` | `TEXT` | 0 | 0 | `` |
| `seo_description` | `TEXT` | 0 | 0 | `` |
| `rule_set_json` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |
| `gsc_clicks` | `INTEGER` | 0 | 0 | `` |
| `gsc_impressions` | `INTEGER` | 0 | 0 | `` |
| `gsc_ctr` | `REAL` | 0 | 0 | `` |
| `gsc_position` | `REAL` | 0 | 0 | `` |
| `gsc_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `ga4_sessions` | `INTEGER` | 0 | 0 | `` |
| `ga4_views` | `INTEGER` | 0 | 0 | `` |
| `ga4_avg_session_duration` | `REAL` | 0 | 0 | `` |
| `ga4_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `index_status` | `TEXT` | 0 | 0 | `` |
| `index_coverage` | `TEXT` | 0 | 0 | `` |
| `google_canonical` | `TEXT` | 0 | 0 | `` |
| `index_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_performance` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_seo` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_status` | `TEXT` | 0 | 0 | `` |
| `pagespeed_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `seo_signal_updated_at` | `TEXT` | 0 | 0 | `` |

### `competitor_keyword_gaps`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `keyword` | `TEXT` | 1 | 1 | `` |
| `competitor_domain` | `TEXT` | 1 | 2 | `` |
| `competitor_position` | `INTEGER` | 0 | 0 | `` |
| `competitor_url` | `TEXT` | 0 | 0 | `` |
| `our_ranking_status` | `TEXT` | 1 | 0 | `'not_ranking'` |
| `our_gsc_position` | `REAL` | 0 | 0 | `` |
| `volume` | `INTEGER` | 0 | 0 | `0` |
| `difficulty` | `INTEGER` | 0 | 0 | `0` |
| `traffic_potential` | `INTEGER` | 0 | 0 | `0` |
| `gap_type` | `TEXT` | 1 | 0 | `'they_rank_we_dont'` |
| `updated_at` | `INTEGER` | 1 | 0 | `0` |

### `competitor_profiles`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `domain` | `TEXT` | 0 | 1 | `` |
| `keywords_common` | `INTEGER` | 0 | 0 | `0` |
| `keywords_they_have` | `INTEGER` | 0 | 0 | `0` |
| `keywords_we_have` | `INTEGER` | 0 | 0 | `0` |
| `share` | `REAL` | 0 | 0 | `0.0` |
| `traffic` | `INTEGER` | 0 | 0 | `0` |
| `is_manual` | `INTEGER` | 0 | 0 | `0` |
| `updated_at` | `INTEGER` | 1 | 0 | `0` |

### `competitor_top_pages`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `competitor_domain` | `TEXT` | 1 | 1 | `` |
| `url` | `TEXT` | 1 | 2 | `` |
| `top_keyword` | `TEXT` | 0 | 0 | `''` |
| `top_keyword_volume` | `INTEGER` | 0 | 0 | `0` |
| `top_keyword_position` | `INTEGER` | 0 | 0 | `0` |
| `total_keywords` | `INTEGER` | 0 | 0 | `0` |
| `estimated_traffic` | `INTEGER` | 0 | 0 | `0` |
| `traffic_value` | `INTEGER` | 0 | 0 | `0` |
| `page_type` | `TEXT` | 0 | 0 | `''` |
| `updated_at` | `INTEGER` | 1 | 0 | `0` |

### `google_api_cache`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `cache_key` | `TEXT` | 0 | 1 | `` |
| `cache_type` | `TEXT` | 1 | 0 | `` |
| `object_type` | `TEXT` | 0 | 0 | `` |
| `object_handle` | `TEXT` | 0 | 0 | `` |
| `url` | `TEXT` | 0 | 0 | `` |
| `strategy` | `TEXT` | 0 | 0 | `` |
| `payload_json` | `TEXT` | 1 | 0 | `` |
| `fetched_at` | `INTEGER` | 1 | 0 | `` |
| `expires_at` | `INTEGER` | 1 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `gsc_query_dimension_rows`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `object_type` | `TEXT` | 1 | 1 | `` |
| `object_handle` | `TEXT` | 1 | 2 | `` |
| `query` | `TEXT` | 1 | 3 | `` |
| `dimension_kind` | `TEXT` | 1 | 4 | `` |
| `dimension_value` | `TEXT` | 1 | 5 | `` |
| `clicks` | `INTEGER` | 0 | 0 | `` |
| `impressions` | `INTEGER` | 0 | 0 | `` |
| `ctr` | `REAL` | 0 | 0 | `` |
| `position` | `REAL` | 0 | 0 | `` |
| `fetched_at` | `INTEGER` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `gsc_query_rows`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `object_type` | `TEXT` | 1 | 1 | `` |
| `object_handle` | `TEXT` | 1 | 2 | `` |
| `url` | `TEXT` | 1 | 0 | `` |
| `query` | `TEXT` | 1 | 3 | `` |
| `clicks` | `INTEGER` | 0 | 0 | `` |
| `impressions` | `INTEGER` | 0 | 0 | `` |
| `ctr` | `REAL` | 0 | 0 | `` |
| `position` | `REAL` | 0 | 0 | `` |
| `fetched_at` | `INTEGER` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `keyword_metrics`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `keyword` | `TEXT` | 0 | 1 | `` |
| `volume` | `INTEGER` | 0 | 0 | `` |
| `difficulty` | `INTEGER` | 0 | 0 | `` |
| `traffic_potential` | `INTEGER` | 0 | 0 | `` |
| `cpc` | `REAL` | 0 | 0 | `` |
| `intent` | `TEXT` | 0 | 0 | `` |
| `content_type_label` | `TEXT` | 0 | 0 | `` |
| `intent_raw` | `TEXT` | 1 | 0 | `'{}'` |
| `parent_topic` | `TEXT` | 0 | 0 | `` |
| `opportunity` | `REAL` | 0 | 0 | `` |
| `seed_keywords` | `TEXT` | 1 | 0 | `'[]'` |
| `ranking_status` | `TEXT` | 1 | 0 | `'not_ranking'` |
| `gsc_position` | `REAL` | 0 | 0 | `` |
| `gsc_clicks` | `INTEGER` | 0 | 0 | `` |
| `gsc_impressions` | `INTEGER` | 0 | 0 | `` |
| `status` | `TEXT` | 1 | 0 | `'new'` |
| `updated_at` | `INTEGER` | 1 | 0 | `0` |
| `global_volume` | `INTEGER` | 0 | 0 | `` |
| `parent_volume` | `INTEGER` | 0 | 0 | `` |
| `clicks` | `REAL` | 0 | 0 | `` |
| `cps` | `REAL` | 0 | 0 | `` |
| `serp_features` | `TEXT` | 0 | 0 | `` |
| `word_count` | `INTEGER` | 0 | 0 | `` |
| `first_seen` | `TEXT` | 0 | 0 | `` |
| `serp_last_update` | `TEXT` | 0 | 0 | `` |
| `source_endpoint` | `TEXT` | 0 | 0 | `` |
| `competitor_domain` | `TEXT` | 0 | 0 | `` |
| `competitor_position` | `INTEGER` | 0 | 0 | `` |
| `competitor_url` | `TEXT` | 0 | 0 | `` |
| `competitor_position_kind` | `TEXT` | 0 | 0 | `` |
| `is_local` | `INTEGER` | 0 | 0 | `0` |
| `content_format_hint` | `TEXT` | 0 | 0 | `''` |

### `keyword_page_map`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `keyword` | `TEXT` | 1 | 1 | `` |
| `object_type` | `TEXT` | 1 | 2 | `` |
| `object_handle` | `TEXT` | 1 | 3 | `` |
| `source` | `TEXT` | 1 | 0 | `'gsc'` |
| `gsc_clicks` | `INTEGER` | 0 | 0 | `0` |
| `gsc_impressions` | `INTEGER` | 0 | 0 | `0` |
| `gsc_position` | `REAL` | 0 | 0 | `` |
| `is_primary` | `INTEGER` | 0 | 0 | `0` |
| `updated_at` | `INTEGER` | 1 | 0 | `0` |

### `pages`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `title` | `TEXT` | 1 | 0 | `` |
| `handle` | `TEXT` | 1 | 0 | `` |
| `updated_at` | `TEXT` | 0 | 0 | `` |
| `body` | `TEXT` | 0 | 0 | `` |
| `seo_title` | `TEXT` | 0 | 0 | `` |
| `seo_description` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |
| `gsc_clicks` | `INTEGER` | 0 | 0 | `` |
| `gsc_impressions` | `INTEGER` | 0 | 0 | `` |
| `gsc_ctr` | `REAL` | 0 | 0 | `` |
| `gsc_position` | `REAL` | 0 | 0 | `` |
| `gsc_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `ga4_sessions` | `INTEGER` | 0 | 0 | `` |
| `ga4_views` | `INTEGER` | 0 | 0 | `` |
| `ga4_avg_session_duration` | `REAL` | 0 | 0 | `` |
| `ga4_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `index_status` | `TEXT` | 0 | 0 | `` |
| `index_coverage` | `TEXT` | 0 | 0 | `` |
| `google_canonical` | `TEXT` | 0 | 0 | `` |
| `index_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_performance` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_seo` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_status` | `TEXT` | 0 | 0 | `` |
| `pagespeed_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `seo_signal_updated_at` | `TEXT` | 0 | 0 | `` |

### `product_images`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `product_shopify_id` | `TEXT` | 1 | 0 | `` |
| `alt_text` | `TEXT` | 0 | 0 | `` |
| `url` | `TEXT` | 1 | 0 | `` |
| `width` | `INTEGER` | 0 | 0 | `` |
| `height` | `INTEGER` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `product_metafields`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `product_shopify_id` | `TEXT` | 1 | 0 | `` |
| `namespace` | `TEXT` | 1 | 0 | `` |
| `key` | `TEXT` | 1 | 0 | `` |
| `type` | `TEXT` | 0 | 0 | `` |
| `value` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `product_variants`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `product_shopify_id` | `TEXT` | 1 | 0 | `` |
| `legacy_resource_id` | `TEXT` | 0 | 0 | `` |
| `title` | `TEXT` | 1 | 0 | `` |
| `sku` | `TEXT` | 0 | 0 | `` |
| `barcode` | `TEXT` | 0 | 0 | `` |
| `price` | `TEXT` | 0 | 0 | `` |
| `compare_at_price` | `TEXT` | 0 | 0 | `` |
| `position` | `INTEGER` | 0 | 0 | `` |
| `inventory_policy` | `TEXT` | 0 | 0 | `` |
| `inventory_quantity` | `INTEGER` | 0 | 0 | `` |
| `taxable` | `INTEGER` | 0 | 0 | `` |
| `selected_options_json` | `TEXT` | 1 | 0 | `` |
| `image_json` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `products`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `legacy_resource_id` | `TEXT` | 0 | 0 | `` |
| `title` | `TEXT` | 1 | 0 | `` |
| `handle` | `TEXT` | 1 | 0 | `` |
| `vendor` | `TEXT` | 0 | 0 | `` |
| `product_type` | `TEXT` | 0 | 0 | `` |
| `status` | `TEXT` | 0 | 0 | `` |
| `created_at` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 0 | 0 | `` |
| `published_at` | `TEXT` | 0 | 0 | `` |
| `description_html` | `TEXT` | 0 | 0 | `` |
| `tags_json` | `TEXT` | 1 | 0 | `` |
| `seo_title` | `TEXT` | 0 | 0 | `` |
| `seo_description` | `TEXT` | 0 | 0 | `` |
| `total_inventory` | `INTEGER` | 0 | 0 | `` |
| `tracks_inventory` | `INTEGER` | 0 | 0 | `` |
| `category_full_name` | `TEXT` | 0 | 0 | `` |
| `online_store_url` | `TEXT` | 0 | 0 | `` |
| `options_json` | `TEXT` | 1 | 0 | `` |
| `featured_image_json` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |
| `gsc_clicks` | `INTEGER` | 0 | 0 | `` |
| `gsc_impressions` | `INTEGER` | 0 | 0 | `` |
| `gsc_ctr` | `REAL` | 0 | 0 | `` |
| `gsc_position` | `REAL` | 0 | 0 | `` |
| `gsc_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `ga4_sessions` | `INTEGER` | 0 | 0 | `` |
| `ga4_views` | `INTEGER` | 0 | 0 | `` |
| `ga4_avg_session_duration` | `REAL` | 0 | 0 | `` |
| `ga4_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `index_status` | `TEXT` | 0 | 0 | `` |
| `index_coverage` | `TEXT` | 0 | 0 | `` |
| `google_canonical` | `TEXT` | 0 | 0 | `` |
| `index_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_performance` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_seo` | `INTEGER` | 0 | 0 | `` |
| `pagespeed_status` | `TEXT` | 0 | 0 | `` |
| `pagespeed_last_fetched_at` | `INTEGER` | 0 | 0 | `` |
| `seo_signal_updated_at` | `TEXT` | 0 | 0 | `` |
| `battery_size` | `TEXT` | 0 | 0 | `` |
| `charging_port` | `TEXT` | 0 | 0 | `` |
| `coil` | `TEXT` | 0 | 0 | `` |
| `custom_collection` | `TEXT` | 0 | 0 | `` |
| `device_type` | `TEXT` | 0 | 0 | `` |
| `nicotine_strength` | `TEXT` | 0 | 0 | `` |
| `puff_count` | `TEXT` | 0 | 0 | `` |
| `size` | `TEXT` | 0 | 0 | `` |
| `battery_type_refs_json` | `TEXT` | 0 | 0 | `` |
| `coil_connection_refs_json` | `TEXT` | 0 | 0 | `` |
| `color_pattern_refs_json` | `TEXT` | 0 | 0 | `` |
| `vaporizer_style_refs_json` | `TEXT` | 0 | 0 | `` |
| `e_liquid_flavor_refs_json` | `TEXT` | 0 | 0 | `` |
| `vaping_style_refs_json` | `TEXT` | 0 | 0 | `` |
| `battery_type_labels_json` | `TEXT` | 0 | 0 | `` |
| `coil_connection_labels_json` | `TEXT` | 0 | 0 | `` |
| `color_pattern_labels_json` | `TEXT` | 0 | 0 | `` |
| `vaporizer_style_labels_json` | `TEXT` | 0 | 0 | `` |
| `e_liquid_flavor_labels_json` | `TEXT` | 0 | 0 | `` |
| `vaping_style_labels_json` | `TEXT` | 0 | 0 | `` |

### `seo_recommendations`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `id` | `INTEGER` | 0 | 1 | `` |
| `object_type` | `TEXT` | 1 | 0 | `` |
| `object_handle` | `TEXT` | 1 | 0 | `` |
| `category` | `TEXT` | 1 | 0 | `` |
| `priority` | `TEXT` | 0 | 0 | `` |
| `summary` | `TEXT` | 1 | 0 | `` |
| `details_json` | `TEXT` | 0 | 0 | `` |
| `source` | `TEXT` | 1 | 0 | `'dashboard'` |
| `status` | `TEXT` | 1 | 0 | `'success'` |
| `model` | `TEXT` | 0 | 0 | `` |
| `prompt_version` | `TEXT` | 0 | 0 | `` |
| `error_message` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |
| `created_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `seo_workflow_states`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `object_type` | `TEXT` | 1 | 1 | `` |
| `handle` | `TEXT` | 1 | 2 | `` |
| `status` | `TEXT` | 1 | 0 | `` |
| `notes` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `service_settings`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `key` | `TEXT` | 0 | 1 | `` |
| `value` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `service_tokens`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `service` | `TEXT` | 0 | 1 | `` |
| `access_token` | `TEXT` | 0 | 0 | `` |
| `refresh_token` | `TEXT` | 0 | 0 | `` |
| `token_type` | `TEXT` | 0 | 0 | `` |
| `expires_at` | `INTEGER` | 0 | 0 | `` |
| `scope` | `TEXT` | 0 | 0 | `` |
| `raw_json` | `TEXT` | 0 | 0 | `` |
| `updated_at` | `TEXT` | 1 | 0 | `CURRENT_TIMESTAMP` |

### `shopify_metaobjects`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `shopify_id` | `TEXT` | 0 | 1 | `` |
| `type` | `TEXT` | 0 | 0 | `` |
| `handle` | `TEXT` | 0 | 0 | `` |
| `display_name` | `TEXT` | 0 | 0 | `` |
| `fields_json` | `TEXT` | 1 | 0 | `` |
| `raw_json` | `TEXT` | 1 | 0 | `` |
| `updated_at` | `TEXT` | 0 | 0 | `` |
| `synced_at` | `TEXT` | 1 | 0 | `` |

### `sync_runs`

| column | type | not_null | pk | default |
|---|---|---:|---:|---|
| `id` | `INTEGER` | 0 | 1 | `` |
| `started_at` | `TEXT` | 1 | 0 | `` |
| `finished_at` | `TEXT` | 0 | 0 | `` |
| `status` | `TEXT` | 1 | 0 | `` |
| `products_synced` | `INTEGER` | 1 | 0 | `0` |
| `variants_synced` | `INTEGER` | 1 | 0 | `0` |
| `images_synced` | `INTEGER` | 1 | 0 | `0` |
| `metafields_synced` | `INTEGER` | 1 | 0 | `0` |
| `error_message` | `TEXT` | 0 | 0 | `` |
| `collections_synced` | `INTEGER` | 1 | 0 | `0` |
| `collection_metafields_synced` | `INTEGER` | 1 | 0 | `0` |
| `pages_synced` | `INTEGER` | 1 | 0 | `0` |
| `collection_products_synced` | `INTEGER` | 1 | 0 | `0` |
| `blogs_synced` | `INTEGER` | 1 | 0 | `0` |
| `blog_articles_synced` | `INTEGER` | 1 | 0 | `0` |
