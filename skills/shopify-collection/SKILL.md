---
name: shopify-collection
description: Audit Shopify collection SEO using the local SEO database as the primary read source. Generate collection recommendation records, metadata suggestions, internal-link suggestions, and prioritization without writing to Shopify. Use when the user wants collection SEO audits, collection recommendations, or collection optimization planning.
---

# Shopify Collection SEO

Use this skill when the user wants collection SEO recommendations, not live collection edits.

## Goal

Generate collection-focused SEO recommendations that can be reviewed in the dashboard and applied manually later.

Primary outputs:
- SEO title suggestions
- meta description suggestions
- collection body/content direction
- internal-link recommendations
- collection priority and rationale

## Operating Model

- `DB-first`
- `read-only`
- `recommendation-focused`
- no direct Shopify writes

## Primary Data Sources

Read in this order:
1. `shopify_catalog.sqlite3` in the project root (set via `SHOPIFY_CATALOG_DB_PATH` env var or the default project root path)
2. structured collection SEO fields on `collections`
3. recommendation history and workflow state
4. Shopify Admin API only for freshness checks or explicit verification
5. public collection HTML only for verification when needed

## Workflow

### 1. Run a collection preflight audit

Check:
- collection metadata completeness
- product count and thin/empty collections
- GSC and GA4 signals
- index state
- PageSpeed summary
- competitor pressure
- internal-link gaps
- recommendation history

### 2. Use structured collection facts first

Prefer the structured fields on `collections` plus:
- collection product count
- collection memberships
- `seo_recommendations`
- workflow state

The skill should consider all available collection fact fields when generating recommendations, including:
- current SEO title and SEO description
- current collection body depth
- product count and collection composition
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
- suggested collection body direction
- internal-link suggestions
- priority and supporting evidence
- explicit current-vs-recommended deltas where possible

### 4. Store recommendation output

Save collection recommendations into:
- `seo_recommendations`

### 5. Evidence-driven priority rules

Prioritize collections when signals show:
- high impressions + low CTR
- position `8-20`
- indexed but weak engagement
- competitor pressure + thin collection copy
- empty or undersized collections
- weak internal linking from brand pages/products

### 6. Output

Default output:
- recommendation records in DB
- concise report to the user summarizing:
  - collections reviewed
  - highest-priority collections
  - collections needing metadata rewrites
  - collections needing stronger body copy
  - collections needing internal links

## Quality Bar

Before finishing, confirm:
- no Shopify writes were performed
- DB facts were the primary read source
- inventory-aware logic was used
- recommendation records were created or refreshed
- output is suitable for dashboard review and manual application
