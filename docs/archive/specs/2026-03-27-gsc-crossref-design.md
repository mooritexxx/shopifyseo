# Phase 2: GSC Cross-Referencing — Design Spec

## Overview

Enrich approved target keywords with Google Search Console ranking data. Match each keyword against GSC queries to determine current ranking position, clicks, and impressions. Categorize keywords by ranking status (Quick Win, Striking Distance, Low Visibility, Not Ranking) to prioritize content efforts.

## 1. Matching Logic

For each approved target keyword, match against GSC queries using two strategies in priority order:

1. **Exact match** — `target_keyword.lower() == gsc_query.lower()`
2. **Contains match** — target keyword is a substring of a GSC query, or GSC query is a substring of the target keyword

When multiple GSC rows match a single target keyword, aggregate:
- Position: take the **best** (lowest average position)
- Clicks: **sum** across all matching rows
- Impressions: **sum** across all matching rows

## 2. Ranking Status Categories

Based on best matched position:

| Status | Position Range | Color |
|--------|---------------|-------|
| Quick Win | 11–20 | Green |
| Striking Distance | 21–50 | Yellow |
| Low Visibility | 50+ | Orange |
| Not Ranking | No GSC match | Gray |

Note: Keywords at position 1–10 are already on page 1. These get a status of "Ranking" with a blue badge.

## 3. Backend

### Service Function

New function in `backend/app/services/keyword_research.py`:

```python
def cross_reference_gsc(conn) -> dict:
```

Steps:
1. Load target keywords from `service_settings` (key: `target_keywords`)
2. Query `gsc_query_rows` — aggregate by unique query: `MIN(position)` as best position, `SUM(clicks)`, `SUM(impressions)`
3. For each target keyword, find best GSC match (exact first, then contains)
4. Enrich with `gsc_position`, `gsc_clicks`, `gsc_impressions`, `ranking_status`
5. Unmatched keywords get `ranking_status: "not_ranking"` and null GSC fields
6. Save enriched data back to `service_settings`
7. Return the enriched keyword payload

### Endpoint

`POST /api/keywords/target/gsc-crossref` — triggers the cross-reference, returns enriched data. Fast DB-only operation, no SSE needed.

Route placed before `{keyword}/status` to avoid path conflicts.

## 4. Frontend

### New Columns in Target Keywords Table

After Opportunity, before Status:
- **Position** — `gsc_position` rounded to 1 decimal, "—" if null
- **Clicks** — `gsc_clicks`, "—" if null
- **Imp.** — `gsc_impressions`, "—" if null
- **Ranking** — color-coded badge per ranking status

### Ranking Badge Colors

- "Ranking" (1–10): `bg-blue-100 text-blue-700`
- "Quick Win" (11–20): `bg-green-100 text-green-700`
- "Striking Distance" (21–50): `bg-yellow-100 text-yellow-700`
- "Low Visibility" (50+): `bg-orange-100 text-orange-700`
- "Not Ranking" (no match): `bg-slate-100 text-slate-500`

### New Filter Group

Ranking Status pills: All | Ranking | Quick Win | Striking Distance | Low Visibility | Not Ranking

### New Button

"Cross-reference GSC" button next to "Run keyword research" button. Uses `useMutation` with simple loading state (no SSE).

### Schema Updates

Add to `targetKeywordSchema`:
```typescript
gsc_position: z.number().nullable().optional(),
gsc_clicks: z.number().nullable().optional(),
gsc_impressions: z.number().nullable().optional(),
ranking_status: z.string().nullable().optional(),
```

## 5. Files to Modify

| File | Changes |
|------|---------|
| `backend/app/services/keyword_research.py` | Add `cross_reference_gsc()` function |
| `backend/app/routers/keywords.py` | Add `POST /target/gsc-crossref` endpoint |
| `frontend/src/routes/keywords-page.tsx` | Add GSC columns, ranking badge, filter group, cross-ref button, schema fields |
| `tests/test_keyword_research.py` | Tests for matching logic and ranking status classification |

## What This Does NOT Include

- Automatic GSC refresh (user triggers GSC sync separately via the existing sync flow)
- Historical position tracking (just current snapshot)
- Per-page URL matching (matches by query text only, not URL)
