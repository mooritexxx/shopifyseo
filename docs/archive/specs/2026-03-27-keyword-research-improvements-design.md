# Keyword Research Improvements — Design Spec

## Overview

Expand the Phase 1 keyword research pipeline from a single Ahrefs source to four, and add a Competitors tab for managing competitor domains. The goal is to surface more keyword types (long-tail, question-based, competitor gaps) while reusing the existing scoring, classification, and merge infrastructure.

## 1. Competitors Tab

### UI

A new tab on the Keywords page between Seed Keywords and Target Keywords:

**Seed Keywords | Competitors | Target Keywords**

The tab contains:
- A text input + "Add" button to enter a competitor domain (e.g., `180smoke.ca`)
- A table listing saved domains with a delete button per row
- Empty state: "No competitor domains added yet. Add domains to mine their organic keywords during research."

### Storage

Stored in `service_settings` with key `competitor_domains` as a JSON array of strings:
```json
["180smoke.ca", "dashvapes.com"]
```

### API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/keywords/competitors` | Return saved domains |
| POST | `/api/keywords/competitors` | Add a domain |
| DELETE | `/api/keywords/competitors/{domain}` | Remove a domain |

Response format follows existing pattern: `{"ok": true, "data": {"items": [...], "total": N}}`

## 2. Expanded Research Pipeline

The "Run Research" button triggers four Ahrefs API v3 sources sequentially. All results are combined into a single list before entering the existing dedup → score → classify → merge pipeline.

### Source 1: Related Terms (existing)

- **Endpoint:** `GET /keywords-explorer/related-terms`
- **Input:** Seed keywords, batched by 5
- **Params:** `terms=also_rank_for`, `country=ca`, `limit=500`, `order_by=volume:desc`
- **Filters:** volume ≥ 10, difficulty ≤ 70
- **Select:** `keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc`

### Source 2: Matching Terms (new)

- **Endpoint:** `GET /keywords-explorer/matching-terms`
- **Input:** Seed keywords, batched by 5
- **Params:** `country=ca`, `limit=500`, `order_by=volume:desc`
- **Filters:** volume ≥ 10, difficulty ≤ 70
- **Select:** `keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc`

Surfaces long-tail keyword variations that contain or relate to the seed phrases.

### Source 3: Search Suggestions (new)

- **Endpoint:** `GET /keywords-explorer/search-suggestions`
- **Input:** Seed keywords, batched by 5
- **Params:** `country=ca`, `limit=500`, `order_by=volume:desc`
- **Filters:** volume ≥ 5 (lower floor to capture valuable long-tail questions), difficulty ≤ 70
- **Select:** `keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc`

Surfaces autocomplete-style queries — good for question keywords like "how long does a disposable vape last."

### Source 4: Competitor Organic Keywords (new)

- **Endpoint:** `GET /site-explorer/organic-keywords`
- **Input:** Each competitor domain (one call per domain)
- **Params:** `target={domain}`, `mode=subdomains`, `country=ca`, `limit=500`, `order_by=volume:desc`, `date={today}`
- **Filters:** volume ≥ 10, difficulty ≤ 70
- **Select:** `keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc`

Mines keywords that competitor sites currently rank for in Canada. Skipped entirely if no competitor domains are saved. Each keyword's `seed_keywords` field records the competitor domain as the source.

### Pipeline Flow

```
Seeds ──→ Source 1 (related-terms)      ──┐
Seeds ──→ Source 2 (matching-terms)      ──┤
Seeds ──→ Source 3 (search-suggestions)  ──┼──→ Combine ──→ Dedup ──→ Score ──→ Classify ──→ Merge ──→ Save
Competitors ──→ Source 4 (organic-kw)    ──┘
```

### Unit Cost Estimate

- Sources 1-3: ~5 batches × 3 sources = ~15 calls → ~30-40K units
- Source 4: 1 call per competitor domain → ~5-10K units per domain
- Total estimate: ~40-60K units per full run (with 2-3 competitors)
- Budget: 150K/month allows ~2-3 full runs safely

### Merge Behavior

Unchanged from Phase 1:
- Deduplication by lowercase keyword, keeping highest-volume instance
- `seed_keywords` sets are merged across all sources
- Existing approved/dismissed statuses are preserved
- Keywords from previous runs that don't reappear are kept (additive merge)

## 3. Filter Adjustments

- **Difficulty cap:** Remains ≤70 default but extracted as a parameter in the research function for future configurability
- **Volume floor:** ≥10 for sources 1, 2, 4. ≥5 for source 3 (search suggestions) to capture long-tail questions

## Files to Modify

| File | Changes |
|------|---------|
| `backend/app/services/keyword_research.py` | Add three new API call functions, update `run_research` to call all four sources, extract difficulty cap as parameter |
| `backend/app/routers/keywords.py` | Add competitor domain CRUD endpoints |
| `frontend/src/routes/keywords-page.tsx` | Add Competitors tab with domain list management |
| `tests/test_keyword_research.py` | Update merge test, add tests for new source functions |

## What This Does NOT Include

- Per-source budget tracking or progress indicators
- UI controls for difficulty/volume thresholds
- MCP integration — all calls are direct Ahrefs REST API v3 via HTTP
- Question keyword detection heuristics (relies on Ahrefs search suggestions naturally surfacing these)
