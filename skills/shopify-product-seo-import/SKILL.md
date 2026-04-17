---
name: seo-products
description: Generate Shopify-ready product update CSVs from a Shopify export. Use when the user wants Body (HTML), SEO Title, SEO Description, and standardized Tags created or updated for products, especially from a Shopify template export.
---

# Shopify Product SEO Import

Use this skill when the user wants to update Shopify product SEO fields through CSV re-import.

## Goal

Produce a Shopify-ready update file that preserves the export/import structure while improving:
- `Body (HTML)`
- `SEO Title`
- `SEO Description`
- `Tags`

## Workflow

### 1. Run a preflight audit

Before generating updates, audit the selected scope first.

Check:
- how many products are in scope
- which first-row product fields are already populated
- which fields are missing
- whether tags are missing, mixed, or already normalized
- whether the current export appears newer than prior working files

Default preflight output should report:
- products in scope
- missing `Body (HTML)`
- missing `SEO Title`
- missing `SEO Description`
- missing `Tags`
- products with mixed or legacy tags in scope

### 2. Load the current source files

Read only the files needed for the request.

Primary input:
- Shopify product export CSV

Secondary inputs when present:
- `MASTER-SEO-PLAN.md`
- `product-content-gap-sheet.csv`
- `keyword-gap-summary.csv`
- `COLLECTION-AUDIT-REPORT.md`
- `collection-export.xlsx`
- competitor summary CSVs or saved sitemap snapshots

Use the secondary inputs to align the copy with the current SEO strategy rather than generating generic product text.

### 3. Preserve Shopify import safety

- Keep the original Shopify column structure intact.
- Preserve all rows for the selected products, including image rows.
- Update shared product fields only on the first row for each handle unless the user explicitly asks for broader changes.
- Do not silently alter handles, variant rows, or product identifiers.

### 4. Use collection and brand-page awareness

Do not generate product copy in isolation when collection or brand-page context is available.

When present, inspect:
- collection exports
- known brand pages
- model-level collection or page targets from planning files

Use that context to decide:
- which internal links should be added
- whether a product should reinforce a brand page, model collection, or both
- whether the body copy should support an existing shopping destination rather than inventing one

Internal-link preference order:
1. model collection page, if it exists
2. brand page or brand collection page
3. main shopping collection such as `disposable-vapes`

If the user only has brand pages and not brand collections, link to the brand pages rather than inventing shopping URLs.

### 5. Build SEO fields from the current strategy

When generating product copy, prefer these patterns:
- exact `brand + model + flavor + Canada`
- transactional language
- puff-count phrasing when relevant
- rechargeable phrasing when relevant
- visible specs from metafields
- internal links to relevant brand pages and shopping pages when safe to include

The title and description should be written for search and click-through, not for stuffing.
Never leave a truncated title or meta description in the final output. If a field is too long, rewrite it into a shorter complete phrase rather than cutting it off or leaving trailing ellipses.

Preferred metadata length targets:
- `SEO Title`: aim for `50-60` characters; hard ceiling around `65`
- `SEO Description`: aim for `140-155` characters

### 6. Enforce compliance and tone guardrails

For vape-related products:
- write for adult consumers only
- avoid medical, therapeutic, or cessation claims
- avoid health-improvement claims
- avoid youth-oriented language
- avoid risky claims that imply guaranteed outcomes

Preferred tone:
- commercially useful
- product-specific
- restrained
- clear

### 7. Standardize tags for store logic

Tags are for collection logic and merchandising support, not direct ranking.

When tags are included in scope, normalize them across the selected product set. Do not only fill blank tags if the selected products still contain mixed legacy tag styles. Prefer replacing inconsistent tag sets with one standard taxonomy for the requested scope.

Preferred tag groups:
- `brand_*`
- `model_*`
- `device_*`
- `puff_*`
- `nicotine_*`
- `rechargeable_yes` / `rechargeable_no`
- `flavor_*`
- `flavor_family_*`

Avoid loose tags like:
- `vape`
- `disposable`
- `pods`
- ad hoc flavor fragments with mixed capitalization

If the selected scope contains mixed legacy tags, normalize the full selected scope rather than only filling blank tags.

### 8. Support output modes

Supported modes:
- `missing-only`
  Fill blank fields only and preserve newer existing work.
- `normalize-scope`
  Normalize tags and other scoped fields consistently across the selected product set.
- `full-regenerate`
  Rewrite the selected fields across the full selected scope.

Choose the safest mode unless the user explicitly asks for broader regeneration.

### 9. Do a second-pass refinement

Do not stop at the first draft.

Review the generated rows and refine for:
- exact-match commercial phrasing
- cleaner title length
- complete meta descriptions
- stronger model-level intent
- better flavor differentiation
- consistency with competitor patterns already found in the workspace
- no trailing ellipses or cut-off fields
- title length stays inside the preferred target range whenever possible
- meta description length stays inside the preferred target range whenever possible

### 10. Output

Default outputs:
- one Shopify-ready CSV update file
- one short notes file describing scope, changed fields, and any caveats

If tags are in scope, prefer producing a normalized tag set for the whole requested product scope so the import leaves the catalog in a consistent state.

If the user wants safer rollout:
- create a subset import file for one model line first

If the user wants broader rollout:
- create a combined import file for the requested product set

The notes file should always include:
- source file used
- output mode used
- how many products were in scope
- which fields were changed
- whether existing SEO titles/descriptions were preserved or regenerated
- whether tags were normalized for the full scope
- whether keyword-volume data was available

## Script

Use the helper script for the structural work:
- `scripts/generate_shopify_seo_update.py`

Run `--help` first if needed.

Use the script to generate the initial CSV, then inspect and refine the result before presenting it as ready.

## Quality Bar

Before finishing, confirm:
- the file is still Shopify-import compatible
- selected products only were changed
- the first row for each handle has the intended SEO fields
- tags follow the standard taxonomy and were normalized consistently for the requested scope
- compliance guardrails were followed
- internal links respect the known collection/brand-page structure when available
- notes mention whether keyword-volume data was or was not available
- no `SEO Title` or `SEO Description` ends in an ellipsis or a cut-off fragment
