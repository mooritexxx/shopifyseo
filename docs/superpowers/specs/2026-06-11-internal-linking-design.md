# Internal Linking Engine — Design

Date: 2026-06-11
Status: Approved

## Goal

Reuse the existing embedding infrastructure to suggest internal links across the
catalog (blog articles, product descriptions, collection descriptions as sources),
detect orphan pages, and recommend links from high-traffic pages to commercial
("money") pages. Suggestions are reviewed and applied with one click; applying
pushes the updated body to Shopify through the existing live-update path.

## Decisions

- **Write mode:** one-click apply with human review (no bulk auto-apply).
- **Link sources:** blog articles, product descriptions, collection descriptions.
  Static pages are out of scope for v1.
- **Insertion:** prefer wrapping an existing phrase (`phrase_wrap`, copy never
  changes); fall back to AI-woven sentence (`ai_woven`), flagged distinctly in the
  UI with a before/after diff.
- **Prioritization:** traffic-weighted — similarity × source GSC traffic × target
  commercial value (product/collection targets above blog targets; orphan targets
  boosted).
- **Link graph:** v1 builds the full internal link graph from all body_html to
  power orphan detection and already-linked suppression.
- **Architecture:** precomputed suggestion table built by a post-sync background
  thread; AI anchor text generated lazily at review time (approach A).
- **UI:** dedicated Internal Links page plus a "Link opportunities" card on
  product/collection/article detail pages.

## Data model

### `internal_links` (current link graph)

One row per internal `<a>` found in any catalog `body_html` (products,
collections, pages, blog_articles). Rebuilt after each catalog sync.

| Column | Notes |
| --- | --- |
| `source_type`, `source_handle` | entity owning the body |
| `target_type`, `target_handle` | resolved from href via allowlist path logic |
| `anchor_text` | text content of the `<a>` |
| `href` | original href as found |

URL resolution reuses `object_url_with_base` and the path-normalization logic in
`shopifyseo/dashboard_queries/_urls.py`. External/mailto/tel links are ignored.
Orphan = published entity with zero incoming rows.

### `link_suggestions` (work queue)

| Column | Notes |
| --- | --- |
| `source_type`, `source_handle` | where the link is inserted |
| `target_type`, `target_handle` | link destination |
| `kind` | `phrase_wrap` or `ai_woven` |
| `anchor_phrase` | exact body phrase to wrap (null for `ai_woven` until generated) |
| `ai_anchor_html` | lazily generated woven sentence (null until review) |
| `score` | ranking score (see Scoring) |
| `status` | `suggested` → `applied` or `dismissed` |
| `created_at`, `applied_at` | timestamps |

Unique index on (source_type, source_handle, target_type, target_handle); re-runs
upsert and never resurrect `dismissed` rows.

## Suggestion pipeline

Background daemon thread launched after catalog sync completes (same pattern as
the embeddings refresh), with progress exposed via a
`internal_link_sync_progress()`-style accessor. Steps:

1. **Rebuild graph** — parse all bodies, fill `internal_links`.
2. **Candidate pairs** — per source entity, `retrieve_related_by_handle()` top-10
   across products/collections/pages/articles; similarity threshold 0.55
   (tunable in Settings).
3. **Suppression** — drop self-links, pairs already in `internal_links`,
   previously dismissed suggestions, and draft/unpublished targets.
4. **Anchor detection** — case-insensitive search of source body text for target
   title, target keywords, and cluster keywords mapped to the target. Match ⇒
   `phrase_wrap` with the matched phrase; no match ⇒ `ai_woven` placeholder.
5. **Scoring** — `similarity × traffic_weight × target_value` where
   `traffic_weight` derives from the source's GSC clicks/impressions (existing
   SEO signal columns) and `target_value` weights product/collection targets
   above blog targets, with a boost for orphan targets.

Caps: max 5 pending outgoing suggestions per source page, max 15 incoming per
target.

## Apply flow

- **phrase_wrap:** server-side and deterministic. Find the first occurrence of
  `anchor_phrase` in the body outside existing `<a>` and heading tags, wrap it in
  an `<a>` with the canonical URL, run `sanitize_article_internal_links`, push to
  Shopify via `dashboard_live_updates`, update the local row, insert into
  `internal_links`, mark `applied`.
- **ai_woven:** on review, the AI engine (existing model-per-task settings)
  generates the rewritten or new sentence; the UI shows a before/after diff of
  the affected sentence, badged "modifies copy". Apply follows the same push
  path.
- **Failure handling:** if the Shopify push fails, status stays `suggested` and
  the error surfaces in the UI. Local DB updates only after Shopify accepts —no
  half-applied state.

## API — new `internal_links` router

All responses use the `{ ok, data } / { ok, error }` envelope.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/internal-links/summary` | graph stats, orphan count, suggestion counts |
| GET | `/api/internal-links/orphans` | orphan list with traffic data |
| GET | `/api/internal-links/suggestions` | queue; filters: source_type, handle, status, sort |
| POST | `/api/internal-links/suggestions/{id}/generate-anchor` | lazy AI weave |
| POST | `/api/internal-links/suggestions/{id}/apply` | apply and push |
| POST | `/api/internal-links/suggestions/{id}/dismiss` | dismiss |
| POST | `/api/internal-links/rebuild` | manual pipeline trigger |

## UI

- **Internal Links page** (new route in `frontend/src/routes/`): summary cards
  (total internal links, orphan count, pending suggestions); tabs for
  Suggestions (score-sorted table, inline apply/dismiss, diff modal for
  `ai_woven`), Orphans, and per-page graph stats.
- **Detail-page card:** "Link opportunities" on product, collection, and article
  detail views — top 3 pending suggestions for that entity plus incoming/
  outgoing link counts, with the same apply/dismiss actions.

## Testing

Pytest, in-memory SQLite, following `tests/test_internal_link_allowlist.py`
conventions:

- Graph parser: relative/absolute URL normalization, ignores external/mailto,
  maps paths to entity types correctly.
- Anchor detection: matches titles/keywords; never matches inside existing
  `<a>` or heading tags.
- Suppression: existing links, dismissed rows, self-links, unpublished targets.
- Scoring: ordering reflects traffic and target weights; caps enforced.
- Apply: phrase_wrap wraps first eligible occurrence only; re-apply is a no-op;
  Shopify failure leaves status `suggested`.
- API contract tests for each route; AI weave tested with a faked engine
  response.
