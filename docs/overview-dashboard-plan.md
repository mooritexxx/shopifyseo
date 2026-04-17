# Overview dashboard redesign

Plan for a focused SEO performance dashboard. Audience: **solo operator**. North star: **better Google rankings and more search clicks**. Primary entities: **products, collections, pages**; **blog articles** secondary.

## Decisions (locked)

- **No onboarding CTAs** on overview (no “Connect Search Console”, “Sync property”, “Last import”). Missing data → **omit or neutral copy** only.
- **No attention queue** on overview: **`top_opportunities` removed** from `/api/summary` and UI (no ranked “fix next” URL list).
- **`indexing_candidates` removed** from `/api/summary` until a feature needs it.
- **Period comparison — equal number of days:**  
  - **Month-to-date:** current month days **1…today** vs previous month **1…same ordinal day** (same **N**).  
  - **Full months:** entire previous calendar month vs month before.  
  Label any window that is not matched-day.

## Visual direction: Metorik-inspired analytics

[Metorik](https://metorik.com) (WooCommerce analytics) is a good **UX reference**, not a pixel copy:

| Metorik pattern | Our application |
|-----------------|-----------------|
| Bold purple/indigo hero, high contrast | Gradient hero (`#2a1f5e` → `#5746d9` → `#155eef`), white CTAs |
| Large **tabular KPI** numbers, clear labels | `KpiCard` row: GSC clicks/impressions/CTR, GA4 sessions/views |
| **Primary chart** + **activity** side panel | Recharts **bar chart** (catalog entity counts) + **Recent syncs** |
| Calm white cards, soft shadow, purple accents | Border `#e8e4f8`, shadow `rgba(87,70,217,0.06)` |
| Honest subtitles (“what this metric is”) | Copy explains URL-level rollup vs **property** GSC when connected |

**Visualization stack:** **`recharts`** — catalog **`BarChart`**, property GSC **`LineChart`** (dual Y: clicks + impressions).

## Current state (vs plan)

| Plan area | Status |
|-----------|--------|
| Phase 0 cleanup | Done — AI learning module removed from overview and `/api/summary` |
| Phase 1 timezone + site GSC + `gsc_site` on `/api/summary` | Done |
| Phase 1 **GA4** site-level matched periods | Done — `ga4_site` on summary (daily sessions/views, same windows as GSC) |
| Phase 1 **catalog completion %** | Done — `catalog_completion` on summary + overview progress bars |
| Phase 2 period UI + deltas + line chart | Done for GSC |
| Phase 2 **indexing rollup** / **sparklines** | Done — rollup on summary; SVG KPI sparklines from `gsc_site` / `ga4_site` daily series |
| Phase 3 QA (contract shape, DST, chart perf) | Done — stricter `/api/summary` tests; calendar edge + anchor mock; chart memoization + `staleTime` + `isAnimationActive={false}` |

**`/api/summary` today:** `counts`, `metrics`, `recent_runs`, **`gsc_site`** (includes **`url_segment`**), **`ga4_site`**, **`indexing_rollup`**, **`catalog_completion`**, **`overview_goals`**. Query: `gsc_period=mtd|full_months`, **`gsc_segment=all|products|collections|pages|blogs`** (GSC property block only; invalid values normalize to `all`).

## Prior baseline note

- **Tracked URL** GSC/GA4 sums remain in `metrics` for entities in the DB; **property** GSC uses Search Console API + matched-day windows when Google is connected.

## Phase 0 — Cleanup

- [x] Remove **Top opportunities** from API, schemas, tests, and overview UI.
- [x] Remove **`indexing_candidates`** from summary (see above).
- [x] Remove static **focus cards**; **hero** replaced with analytics-oriented strip + CTAs (Catalog, Google Signals).
- [x] **KPI strip + bar chart** (Metorik-style) using current metrics.
- [x] **SEO debt:** collections + pages missing meta in **SEO debt snapshot**; articles missing meta in footer line; **catalog completion** bars for forward-looking %.
- [x] **AI learning** removed from overview and summary API (was engine activity + lesson marks).

## Phase 1 — Data (highest leverage next)

- [x] **Timezone** for calendar boundaries — `DASHBOARD_TZ` env (default `America/Vancouver`); anchor = **yesterday** in that zone.
- [x] **Site-level GSC** — matched-day **MTD** vs prior month slice, and **last two full calendar months**; daily `date` dimension; SQLite cache `search_console_overview` (1h TTL); invalidated on manual GSC refresh.
- [x] **`/api/summary`** — `gsc_site` object + query param `gsc_period=mtd|full_months`.
- [x] **GA4** matched-day property overview — `ga4_property_overview` cache; `get_ga4_property_overview_cached`; overview KPI + line chart; invalidated on GA4 refresh.
- [x] **Catalog progress:** `catalog_completion` — `%` meta-complete + counts; products include **thin_body**; articles row links to `/articles`.
- [x] URL-summed `metrics.gsc_*` **relabeled** on overview as “Tracked URLs (database)”; property totals are primary when connected.

## Phase 2 — UI (after Phase 1 data)

- [x] **Period selector** on overview (`mtd` | `full_months`).
- [x] **Delta badges** on property clicks/impressions KPIs (% vs prior window).
- [x] **Line chart** (dual Y) for daily clicks/impressions in the current window.
- [x] **Indexing rollup** — `indexing_rollup` from stored inspection fields on all synced entity types; overview KPI row + by-type breakdown.
- [x] **Sparklines** in property GSC/GA4 KPI cards — `MiniSparkline` (SVG) from existing daily `series` (clicks, impressions, daily CTR; sessions, views, views/session).

## Phase 3 — QA

- [x] Stricter contract: `gsc_site.series` row shape, `ga4_site.series`, `catalog_completion` segment keys, `indexing_rollup.by_type` buckets, `overview_goals` keys (`tests/test_api.py`).
- [x] Month-boundary tests: leap-year February trim, first-of-month MTD (`tests/test_gsc_overview_calendar.py`).
- [x] Anchor “yesterday” in dashboard TZ via mocked `datetime.now` (DST-safe civil date math).
- [x] GSC overview cache key includes URL segment (`tests/test_gsc_overview_cache_key.py`).
- [x] Chart performance: memoized line series, shared tooltip style object, Recharts `isAnimationActive={false}` on property lines; React Query `staleTime: 60_000` on summary.

## Further improvements (backlog)

1. **Goal lines** — [x] Optional **daily** targets via env: `OVERVIEW_GOAL_GSC_DAILY_CLICKS`, `OVERVIEW_GOAL_GSC_DAILY_IMPRESSIONS`, `OVERVIEW_GOAL_GA4_DAILY_SESSIONS`, `OVERVIEW_GOAL_GA4_DAILY_VIEWS` → `overview_goals` + Recharts `ReferenceLine`.  
2. **Segment toggles** — [x] GSC **page** `contains` filters: `/products/`, `/collections/`, `/pages/`, `/blogs/`; cache key per segment.  
3. **Export** — [x] Client CSV from hero **Export CSV** (counts, metrics, property rollups, indexing totals).  
4. **Shopify** — [x] Copy on GA4-unavailable card pointing to **Shopify Admin → Analytics** (not duplicated metrics).  
5. **Dark mode** — [x] `darkMode: "media"` + `dark:` tokens on overview KPI cards / charts / panels.  
6. **Accessibility** — [x] Hero `aria-labelledby`, chart regions `role="img"` + `aria-label`, KPI `role="group"` labels.  
7. **Mobile** — [x] Horizontal scroll + snap for property KPI rows on small screens.  
8. **Caching** — [x] React Query `staleTime` for summary (60s).

## Implementation order (updated)

| Step | Task |
|------|------|
| S1 | [x] Remove opportunities queue; Metorik-style layout + Recharts catalog chart. |
| S2 | Backend: calendar + matched-day **site-level** GSC (+ optional GA4) + **daily series**. |
| S3 | Overview: **deltas**, period selector, **line/area** charts from real series. |
| S4 | [x] Catalog **completion %** + articles on overview (`catalog_completion`). |
| S5 | [x] Sparklines; goals (env + reference lines); CSV export; a11y + mobile + dark pass. |

## Reintroducing `indexing_candidates`

Restore `build_indexing_candidates` in `overview_metrics.py`, add fields to `DashboardSummary` + `summarySchema`, compute in `get_dashboard_summary`, render only where needed (e.g. a future **Indexing** subpage—not the overview queue).
