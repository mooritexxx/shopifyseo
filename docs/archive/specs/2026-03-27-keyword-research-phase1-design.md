# Phase 1: Keyword Research Expansion Pipeline

**Date:** 2026-03-27
**Status:** Approved
**Scope:** Backend pipeline + API + frontend Target Keywords tab

## Overview

Expand 25 deduplicated seed keywords into a researched target keyword list using Ahrefs `related-terms` API (country: CA). Display results in a sortable, filterable table with opportunity scoring and intent classification. Users can approve or dismiss keywords to curate a working list for future content generation.

## Architecture

### Pipeline Flow

```
[Seed Keywords (DB)]
  → Batch into ~5 groups of ~5 seeds
  → Ahrefs related-terms API (CA, also_rank_for)
  → Deduplicate by keyword (lowercase)
  → Filter: volume > 0, difficulty ≤ 70
  → Compute opportunity score
  → Classify intent → content type
  → Save to DB as target_keywords
```

### Ahrefs API Usage

The backend calls the **Ahrefs REST API v3** directly via HTTP. The API token is stored in `service_settings` with key `ahrefs_api_token` and loaded at runtime like other credentials. A new field is added to the Settings page Integrations tab.

- **REST endpoint:** `GET https://api.ahrefs.com/v3/keywords-explorer/related-terms`
- **Auth header:** `Authorization: Bearer {ahrefs_api_token}`
- **Country:** CA
- **Terms mode:** `also_rank_for` (keywords that top-ranking pages also rank for)
- **Fields requested:** `keyword, volume, difficulty, traffic_potential, intents, parent_topic, cpc`
- **Batch strategy:** ~5 seeds per comma-separated `keywords` param, ~5 HTTP calls total
- **Limit per call:** 500 results, ordered by `volume:desc`
- **Filter (Ahrefs where clause):** `volume >= 10 AND difficulty <= 70`
- **Estimated cost:** ~15-20K units per run (budget: 150K/month)

### Opportunity Score

```
opportunity = (volume * traffic_potential) / (difficulty + 1)^2
```

Normalized to 0-100 scale after computation. Rewards low-difficulty keywords with real traffic potential.

### Intent Classification

Ahrefs returns boolean flags: `informational`, `commercial`, `transactional`, `navigational`, `branded`, `local`.

Primary intent = the first true flag in priority order: transactional > commercial > informational > navigational > branded.

Mapping to content type:

| Primary Intent | Content Type |
|---|---|
| informational | Blog / Guide |
| commercial | Comparison / Buying guide |
| transactional | Product / Collection page |
| navigational | Brand page |
| branded | Brand page |

## Data Model

### Target Keyword Record

```
keyword: str              — the keyword string
volume: int               — monthly search volume (CA)
difficulty: int            — Ahrefs KD score (0-100)
traffic_potential: int     — estimated traffic if ranking #1
cpc: int                  — cost per click (USD cents)
intent: str               — primary intent label
intent_raw: dict          — full Ahrefs intent flags object
content_type: str         — recommended content type
parent_topic: str | null  — Ahrefs parent topic grouping
opportunity: float        — computed score (0-100)
seed_keywords: list[str]  — which seed(s) generated this result
status: str               — "new" | "approved" | "dismissed"
```

### Storage

Stored as a JSON blob in `service_settings` table with key `target_keywords`. Includes a metadata wrapper:

```json
{
  "last_run": "2026-03-27T14:30:00Z",
  "unit_cost": 18500,
  "items": [ ... ]
}
```

## API Endpoints

All under `/api/keywords/target`.

### POST `/api/keywords/target/research`

Triggers the Ahrefs expansion pipeline.

- Reads seed keywords from DB
- Reads Ahrefs API token from settings
- Batches seeds and calls Ahrefs REST API
- Deduplicates, filters, scores, classifies
- Merges with existing data (preserves approved/dismissed statuses)
- Saves to DB with timestamp
- Returns the full target keywords payload

Response: `{ ok: true, data: { last_run, unit_cost, items: [...], total: int } }`

This endpoint may take 15-30 seconds. The frontend shows a spinner.

**Re-run behavior:** When research is re-run, existing keywords that still appear in results keep their current `status` (approved/dismissed). New keywords get status "new". Keywords that no longer appear in results are removed.

### GET `/api/keywords/target`

Returns saved target keywords.

Response: `{ ok: true, data: { last_run, unit_cost, items: [...], total: int } }`

### PATCH `/api/keywords/target/{keyword}/status`

Update a single keyword's status.

Body: `{ "status": "approved" | "dismissed" | "new" }`

Response: `{ ok: true, data: { keyword, status } }`

### PATCH `/api/keywords/target/bulk-status`

Update multiple keywords' status at once.

Body: `{ "keywords": ["kw1", "kw2"], "status": "approved" | "dismissed" | "new" }`

Response: `{ ok: true, data: { updated: int } }`

## Frontend — Target Keywords Tab

### Header Bar

- **"Run keyword research" button** — Sparkles icon, shows "Researching..." with spinner while running. Disabled during execution.
- **Last run timestamp** — "Last run: Mar 27, 2026" or "Never run" if no data.
- **Result count** — "342 keywords found"

### Filter Row

Three filter groups, all client-side filtering:

1. **Intent pills:** All | Informational | Commercial | Transactional | Branded
2. **Status pills:** All | New | Approved | Dismissed
3. **Difficulty pills:** All | Easy (0-20) | Medium (21-50) | Hard (51-70)

### Data Table

Sortable columns:

| Column | Type | Notes |
|---|---|---|
| Keyword | string | Primary column |
| Volume | number | Monthly CA search volume |
| Difficulty | number | Color badge: green ≤ 20, yellow 21-50, red 51+ |
| Traffic Pot. | number | Traffic potential |
| CPC | number | Displayed as dollars (cents / 100) |
| Intent | string | Badge with intent label |
| Content Type | string | Recommended content format |
| Opportunity | number | Badge: high (≥ 70), medium (30-69), low (< 30) |
| Status | action | Dropdown or buttons: approve / dismiss / reset |

Default sort: `opportunity:desc` (best opportunities first).

### Bulk Actions

Checkbox column on each row. When 1+ rows selected, show action bar:
- "Approve selected" button
- "Dismiss selected" button

### Empty State

When no research has been run: dashed border box with message "No target keywords yet — click Run keyword research to expand your seeds."

## What This Does NOT Include

- Keyword clustering (Phase 4)
- GSC cross-referencing for current rankings (Phase 2)
- "Create blog from keyword" action (Phase 5)
- Automatic re-runs or scheduling

## Files to Create/Modify

### New Files
- `backend/app/services/keyword_research.py` — Ahrefs HTTP calls, batching, scoring, intent classification, merge logic

### Modified Files
- `backend/app/routers/keywords.py` — add target keyword endpoints (research, get, status, bulk-status)
- `backend/app/schemas/operations.py` — add `ahrefs_api_token` to `SettingsValuesPayload` and `SettingsUpdatePayload`
- `backend/app/services/dashboard_service.py` — add `AHREFS_API_TOKEN` / `ahrefs_api_token` to settings env mapping
- `frontend/src/routes/keywords-page.tsx` — build out TargetKeywordsPanel with table, filters, bulk actions
- `frontend/src/routes/settings-page.tsx` — add Ahrefs API token field to Integrations tab
