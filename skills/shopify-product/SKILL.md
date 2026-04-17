---
name: shopify-product
description: Audit Shopify product SEO using the local SEO database as the primary read source. Generate recommendation records, content suggestions, and optimization priorities without writing to Shopify. Use when the user wants product audits, product recommendations, product content suggestions, or product SEO prioritization.
---

# Shopify Product SEO

Use this skill when the user wants product SEO analysis and recommendations, not live writes.

## Goal

Generate product-level SEO recommendations that can be reviewed in the dashboard and applied manually later.

Primary recommendation outputs:
- SEO title suggestions
- meta description suggestions
- body/content direction
- internal-link recommendations
- priority and rationale

## Operating Model

- `DB-first`
- `read-only`
- `recommendation-focused`
- `manual Shopify updates happen later outside this skill`

Do not write to Shopify from this skill.

## Primary Data Sources

Read in this order:
1. `shopify_catalog.sqlite3` in the project root (set via `SHOPIFY_CATALOG_DB_PATH` env var or the default project root path)
2. raw cache tables and query tables in the same DB
3. Shopify Admin API only if freshness must be checked or explicitly requested
4. CSV exports only as fallback or for comparison/migration work

Prefer the structured fields already stored on `products`, plus:
- `gsc_query_rows`
- `seo_recommendations`
- workflow state
- competitor pressure
- latest summarized SEO columns already stored on the product row

The skill should consider all available product fact fields when generating recommendations, including:
- current SEO title and SEO description
- current body/content depth
- tags and collection memberships
- GSC clicks, impressions, CTR, average position, and query rows
- GA4 sessions, true per-URL views, and engagement
- index status, coverage, and canonical state
- PageSpeed performance and SEO scores
- workflow state and recommendation history
- competitor pressure and internal-link gaps

Unless the user explicitly narrows scope, audit the full product catalog and generate recommendation records for all products in scope.

## Workflow

### 1. Run a preflight audit

Check:
- products in scope
- missing or weak SEO title/meta/body
- index state
- GSC impressions / CTR / position
- GA4 sessions / per-URL views / engagement
- PageSpeed status
- internal-link gaps
- competitor pressure
- existing recommendation history

### 2. Use structured SEO facts first

Prefer facts already stored on product rows:
- latest summarized Google signals
- index status
- PageSpeed summary
- workflow state
- latest recommendation snapshot
- recommendation history
- internal-link opportunities
- competitor pressure

Use raw cache or live refresh only if:
- the row is stale
- the user explicitly asks to refresh
- a required fact is missing

### 3. Generate recommendations, not writes

Produce:
- recommendation summary
- suggested SEO title
- suggested meta description
- suggested body/content direction
- internal-link suggestions
- evidence for why this recommendation matters

### 4. Store recommendation output

Recommendation output should be saved into:
- `seo_recommendations`

Prefer category:
- `content_brief`

### 5. Recommendation standards

Prefer:
- exact commercial phrasing
- evidence-driven prioritization
- strong transactional alignment
- internal-link clarity
- adult-consumer compliance

### 6. Evidence-driven priority rules

Prioritize products when signals show:
- high impressions + low CTR
- position `8-20`
- indexed but weak engagement
- high per-URL views but weak search visibility
- competitor pressure + thin body copy
- important product with weak internal links

### 7. Output

Default output:
- recommendation records in DB
- concise report to the user summarizing:
  - products reviewed
  - top opportunities
  - products needing title/meta rewrites
  - products needing content depth
  - products needing internal links

## Quality Bar

Before finishing, confirm:
- no Shopify writes were performed
- DB facts were used as the primary source
- stale-data concerns were called out if relevant
- recommendation records were created or refreshed
- outputs are suitable for manual review in the dashboard
