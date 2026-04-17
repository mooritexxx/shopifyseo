---
name: shopify-page
description: Audit Shopify page SEO using the local SEO database as the primary read source. Generate page recommendation records, metadata suggestions, content direction, and internal-link recommendations without writing to Shopify. Use when the user wants brand-page or static-page SEO audits and recommendations.
---

# Shopify Page SEO

Use this skill when the user wants static page or brand page SEO recommendations, not live page edits.

## Goal

Generate page-focused SEO recommendations that can be reviewed in the dashboard and applied manually later.

Primary outputs:
- SEO title suggestions
- meta description suggestions
- body/content direction
- internal-link recommendations
- page priority and rationale

## Operating Model

- `DB-first`
- `read-only`
- `recommendation-focused`
- no direct Shopify writes

## Primary Data Sources

Read in this order:
1. `shopify_catalog.sqlite3` in the project root (set via `SHOPIFY_CATALOG_DB_PATH` env var or the default project root path)
2. structured SEO fields on `pages`
3. related products / related collections from the DB
4. recommendation history and workflow state
5. Shopify Admin API or live page HTML only for freshness checks or verification

## Workflow

### 1. Run a page preflight audit

Check:
- title/meta completeness
- body depth
- GSC and GA4 signals
- index state
- PageSpeed summary
- related collections/products
- internal-link gaps
- competitor pressure
- recommendation history

### 2. Use structured page facts first

Prefer page-row structured signals plus related-object context from the DB.

The skill should consider all available page fact fields when generating recommendations, including:
- current SEO title and SEO description
- current body depth
- related collections and related products
- GSC clicks, impressions, CTR, and average position
- GA4 sessions, views, and engagement
- index status, coverage, and canonical state
- PageSpeed performance and SEO scores
- workflow state and recommendation history
- competitor pressure and internal-link gaps

### 3. Generate recommendations, not writes

Produce:
- recommendation summary
- suggested SEO title
- suggested meta description
- suggested body/content direction
- internal-link suggestions
- evidence-backed priority
- explicit current-vs-recommended deltas where possible

### 4. Store recommendation output

Save page recommendations into:
- `seo_recommendations`

### 5. Evidence-driven priority rules

Prioritize pages when signals show:
- high impressions + low CTR
- position `8-20`
- indexed but weak engagement
- competitor pressure + thin page body
- weak links into important collections or products

### 6. Output

Default output:
- recommendation records in DB
- concise report to the user summarizing:
  - pages reviewed
  - highest-priority pages
  - pages needing metadata rewrites
  - pages needing content depth
  - pages needing internal links

## Quality Bar

Before finishing, confirm:
- no Shopify writes were performed
- DB facts were the main source
- related collection/product context was used
- recommendation records were created or refreshed
- output is suitable for dashboard review and manual application
