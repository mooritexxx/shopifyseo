# Phase 2: GSC Cross-Referencing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich approved target keywords with Google Search Console ranking data and categorize by ranking status.

**Architecture:** A new `cross_reference_gsc()` function in `keyword_research.py` loads target keywords and GSC query data from SQLite, fuzzy-matches them, enriches each keyword with position/clicks/impressions/ranking_status, and saves back. A new endpoint exposes this. The frontend adds GSC columns, a ranking badge, a filter group, and a trigger button.

**Tech Stack:** FastAPI, SQLite, React/TypeScript, TanStack Query, Zod, Tailwind CSS

---

## File Map

| File | Role |
|------|------|
| `backend/app/services/keyword_research.py` | Add `classify_ranking_status()` and `cross_reference_gsc()` |
| `backend/app/routers/keywords.py` | Add `POST /target/gsc-crossref` endpoint |
| `frontend/src/routes/keywords-page.tsx` | Add GSC columns, ranking badge, filter group, cross-ref button |
| `tests/test_keyword_research.py` | Tests for matching logic and ranking classification |

---

### Task 1: Ranking Status Classifier and GSC Matching Logic

**Files:**
- Modify: `backend/app/services/keyword_research.py`
- Modify: `tests/test_keyword_research.py`

- [ ] **Step 1: Write tests for `classify_ranking_status`**

Add to `tests/test_keyword_research.py`:

```python
from backend.app.services.keyword_research import classify_ranking_status


def test_classify_ranking_status_page_one():
    assert classify_ranking_status(5.0) == "ranking"


def test_classify_ranking_status_quick_win():
    assert classify_ranking_status(15.0) == "quick_win"


def test_classify_ranking_status_striking_distance():
    assert classify_ranking_status(35.0) == "striking_distance"


def test_classify_ranking_status_low_visibility():
    assert classify_ranking_status(60.0) == "low_visibility"


def test_classify_ranking_status_none():
    assert classify_ranking_status(None) == "not_ranking"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_keyword_research.py::test_classify_ranking_status_page_one -v
```
Expected: FAIL — `ImportError: cannot import name 'classify_ranking_status'`

- [ ] **Step 3: Implement `classify_ranking_status`**

Add to `backend/app/services/keyword_research.py`, after the `INTENT_TO_CONTENT` dict (around line 22):

```python
def classify_ranking_status(position: float | None) -> str:
    if position is None:
        return "not_ranking"
    if position <= 10:
        return "ranking"
    if position <= 20:
        return "quick_win"
    if position <= 50:
        return "striking_distance"
    return "low_visibility"
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
python -m pytest tests/test_keyword_research.py -k "classify_ranking" -v
```
Expected: All 5 pass.

- [ ] **Step 5: Write tests for `match_gsc_queries`**

Add to `tests/test_keyword_research.py`:

```python
from backend.app.services.keyword_research import match_gsc_queries


def test_match_gsc_exact():
    gsc_data = {
        "elf bar canada": {"position": 15.0, "clicks": 3, "impressions": 50},
        "vape juice": {"position": 30.0, "clicks": 1, "impressions": 20},
    }
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is not None
    assert result["position"] == 15.0
    assert result["clicks"] == 3


def test_match_gsc_contains_target_in_query():
    gsc_data = {
        "best elf bar vape canada": {"position": 12.0, "clicks": 5, "impressions": 100},
    }
    result = match_gsc_queries("elf bar", gsc_data)
    assert result is not None
    assert result["position"] == 12.0


def test_match_gsc_contains_query_in_target():
    gsc_data = {
        "vape": {"position": 8.0, "clicks": 10, "impressions": 200},
    }
    result = match_gsc_queries("disposable vape canada", gsc_data)
    assert result is not None
    assert result["position"] == 8.0


def test_match_gsc_best_position_wins():
    gsc_data = {
        "elf bar canada": {"position": 25.0, "clicks": 2, "impressions": 30},
        "elf bar canada review": {"position": 12.0, "clicks": 5, "impressions": 80},
    }
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is not None
    assert result["position"] == 12.0
    assert result["clicks"] == 7
    assert result["impressions"] == 110


def test_match_gsc_no_match():
    gsc_data = {
        "something unrelated": {"position": 5.0, "clicks": 10, "impressions": 100},
    }
    result = match_gsc_queries("elf bar canada", gsc_data)
    assert result is None
```

- [ ] **Step 6: Run tests to verify they fail**

Run:
```bash
python -m pytest tests/test_keyword_research.py::test_match_gsc_exact -v
```
Expected: FAIL — `ImportError: cannot import name 'match_gsc_queries'`

- [ ] **Step 7: Implement `match_gsc_queries`**

Add to `backend/app/services/keyword_research.py`, after `classify_ranking_status`:

```python
def match_gsc_queries(keyword: str, gsc_data: dict[str, dict]) -> dict | None:
    """Match a keyword against aggregated GSC queries.

    Args:
        keyword: Target keyword string.
        gsc_data: Dict mapping lowercase query → {"position": float, "clicks": int, "impressions": int}

    Returns:
        {"position": best_pos, "clicks": total_clicks, "impressions": total_imps} or None.
    """
    kw = keyword.lower()
    matches: list[dict] = []
    for query, metrics in gsc_data.items():
        if kw == query or kw in query or query in kw:
            matches.append(metrics)
    if not matches:
        return None
    best_position = min(m["position"] for m in matches)
    total_clicks = sum(m["clicks"] for m in matches)
    total_impressions = sum(m["impressions"] for m in matches)
    return {
        "position": best_position,
        "clicks": total_clicks,
        "impressions": total_impressions,
    }
```

- [ ] **Step 8: Run all tests**

Run:
```bash
python -m pytest tests/test_keyword_research.py -v
```
Expected: All tests pass (14 existing + 10 new = 24 total).

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/keyword_research.py tests/test_keyword_research.py
git commit -m "feat: add GSC matching logic and ranking status classifier"
```

---

### Task 2: `cross_reference_gsc` Service Function

**Files:**
- Modify: `backend/app/services/keyword_research.py`

- [ ] **Step 1: Implement `cross_reference_gsc`**

Add to `backend/app/services/keyword_research.py`, after `match_gsc_queries` and before `_batch_seeds`:

```python
def cross_reference_gsc(conn: sqlite3.Connection) -> dict:
    """Enrich target keywords with GSC ranking data."""
    data = load_target_keywords(conn)
    items = data.get("items", [])
    if not items:
        return data

    # Aggregate GSC queries: best position, sum clicks, sum impressions
    rows = conn.execute("""
        SELECT
            LOWER(query) as query,
            MIN(position) as best_position,
            SUM(clicks) as total_clicks,
            SUM(impressions) as total_impressions
        FROM gsc_query_rows
        GROUP BY LOWER(query)
    """).fetchall()

    gsc_data: dict[str, dict] = {}
    for row in rows:
        gsc_data[row[0]] = {
            "position": row[1],
            "clicks": row[2],
            "impressions": row[3],
        }

    for item in items:
        match = match_gsc_queries(item["keyword"], gsc_data)
        if match:
            item["gsc_position"] = round(match["position"], 1)
            item["gsc_clicks"] = match["clicks"]
            item["gsc_impressions"] = match["impressions"]
            item["ranking_status"] = classify_ranking_status(match["position"])
        else:
            item["gsc_position"] = None
            item["gsc_clicks"] = None
            item["gsc_impressions"] = None
            item["ranking_status"] = "not_ranking"

    data["gsc_crossref_at"] = datetime.now(timezone.utc).isoformat()
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    return data
```

- [ ] **Step 2: Run existing tests to verify nothing broke**

Run:
```bash
python -m pytest tests/test_keyword_research.py -v
```
Expected: All 24 tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/services/keyword_research.py
git commit -m "feat: add cross_reference_gsc service function"
```

---

### Task 3: GSC Cross-Reference API Endpoint

**Files:**
- Modify: `backend/app/routers/keywords.py`

- [ ] **Step 1: Add the endpoint**

In `backend/app/routers/keywords.py`, add the import for `cross_reference_gsc`:

Update the import block:
```python
from backend.app.services.keyword_research import (
    bulk_update_status,
    cross_reference_gsc,
    load_target_keywords,
    run_research,
    update_keyword_status,
)
```

Add the endpoint **after** the `research_target_keywords` SSE endpoint and **before** the `patch_bulk_status` endpoint:

```python
@router.post("/target/gsc-crossref", response_model=dict)
def gsc_crossref():
    conn = open_db_connection()
    try:
        data = cross_reference_gsc(conn)
        return {"ok": True, "data": data}
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        conn.close()
```

- [ ] **Step 2: Verify route is registered**

Run:
```bash
python -c "
from backend.app.main import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
gsc = [r for r in routes if 'gsc' in r]
print('GSC routes:', gsc)
"
```
Expected: `['/api/keywords/target/gsc-crossref']`

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/keywords.py
git commit -m "feat: add POST /target/gsc-crossref endpoint"
```

---

### Task 4: Frontend — GSC Columns, Badge, Filter, and Button

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

- [ ] **Step 1: Update `targetKeywordSchema` with GSC fields**

In `frontend/src/routes/keywords-page.tsx`, add four fields to `targetKeywordSchema` (around line 20-33):

```typescript
const targetKeywordSchema = z.object({
  keyword: z.string(),
  volume: z.number().nullable(),
  difficulty: z.number().nullable(),
  traffic_potential: z.number().nullable(),
  cpc: z.number().nullable(),
  intent: z.string().nullable(),
  intent_raw: z.record(z.boolean()).nullable().optional(),
  content_type: z.string().nullable(),
  parent_topic: z.string().nullable().optional(),
  opportunity: z.number().nullable(),
  seed_keywords: z.array(z.string()).optional(),
  gsc_position: z.number().nullable().optional(),
  gsc_clicks: z.number().nullable().optional(),
  gsc_impressions: z.number().nullable().optional(),
  ranking_status: z.string().nullable().optional(),
  status: z.string()
});
```

- [ ] **Step 2: Add `RankingBadge` component**

Add after the existing `IntentBadge` component (around line 139):

```typescript
const RANKING_COLORS: Record<string, string> = {
  ranking: "bg-blue-100 text-blue-700",
  quick_win: "bg-green-100 text-green-700",
  striking_distance: "bg-yellow-100 text-yellow-700",
  low_visibility: "bg-orange-100 text-orange-700",
  not_ranking: "bg-slate-100 text-slate-500"
};

const RANKING_LABELS: Record<string, string> = {
  ranking: "Ranking",
  quick_win: "Quick Win",
  striking_distance: "Striking Dist.",
  low_visibility: "Low Visibility",
  not_ranking: "Not Ranking"
};

function RankingBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-slate-400">—</span>;
  const color = RANKING_COLORS[status] ?? "bg-slate-100 text-slate-500";
  const label = RANKING_LABELS[status] ?? status;
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${color}`}>
      {label}
    </span>
  );
}
```

- [ ] **Step 3: Add ranking filter type and options**

Add after the existing `DIFFICULTY_OPTIONS` (around line 171):

```typescript
type RankingFilter = "all" | "ranking" | "quick_win" | "striking_distance" | "low_visibility" | "not_ranking";

const RANKING_OPTIONS: { value: RankingFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ranking", label: "Ranking" },
  { value: "quick_win", label: "Quick Win" },
  { value: "striking_distance", label: "Striking Dist." },
  { value: "low_visibility", label: "Low Visibility" },
  { value: "not_ranking", label: "Not Ranking" }
];
```

- [ ] **Step 4: Add ranking filter state and GSC crossref mutation in `TargetKeywordsPanel`**

Inside the `TargetKeywordsPanel` function, add ranking filter state after the existing `difficultyFilter` state:

```typescript
const [rankingFilter, setRankingFilter] = useState<RankingFilter>("all");
```

Add the GSC crossref mutation after `runResearch` function:

```typescript
const gscCrossrefMutation = useMutation({
  mutationFn: () => postJson("/api/keywords/target/gsc-crossref", targetPayloadSchema),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ["target-keywords"] })
});
```

- [ ] **Step 5: Update the filter logic**

In the `filtered` useMemo inside `TargetKeywordsPanel`, add the ranking filter. Find the existing `useMemo` that filters items and add the ranking check. The dependencies array must include `rankingFilter`.

Add to the filter chain (inside the existing `useMemo`):
```typescript
if (rankingFilter !== "all") {
  result = result.filter((item) => (item.ranking_status ?? "not_ranking") === rankingFilter);
}
```

Add `rankingFilter` to the dependency array of the useMemo.

- [ ] **Step 6: Add the GSC crossref button**

Next to the existing "Run keyword research" button, add:

```typescript
<Button
  variant="outline"
  size="sm"
  disabled={gscCrossrefMutation.isPending}
  onClick={() => gscCrossrefMutation.mutate()}
>
  <Sparkles className="mr-1.5 h-3.5 w-3.5" />
  {gscCrossrefMutation.isPending ? "Matching…" : "Cross-reference GSC"}
</Button>
```

- [ ] **Step 7: Add ranking filter group in the filters section**

After the existing difficulty filter group, add:

```typescript
<div className="flex items-center gap-2">
  <span className="text-xs font-medium text-slate-500">Ranking:</span>
  <FilterGroup options={RANKING_OPTIONS} value={rankingFilter} onChange={setRankingFilter} />
</div>
```

- [ ] **Step 8: Add GSC columns to the table header**

After the Opportunity `<th>` and before the Status `<th>`, add:

```typescript
<th
  className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink"
  onClick={() => toggleSort("gsc_position")}
>
  Position{sortIndicator("gsc_position")}
</th>
<th
  className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink"
  onClick={() => toggleSort("gsc_clicks")}
>
  Clicks{sortIndicator("gsc_clicks")}
</th>
<th
  className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink"
  onClick={() => toggleSort("gsc_impressions")}
>
  Imp.{sortIndicator("gsc_impressions")}
</th>
<th className="whitespace-nowrap pb-2 pr-3">Ranking</th>
```

- [ ] **Step 9: Add GSC columns to the table body**

After the Opportunity `<td>` and before the Status `<td>`, add:

```typescript
<td className="py-2.5 pr-3 text-slate-600">
  {item.gsc_position !== null && item.gsc_position !== undefined
    ? item.gsc_position.toFixed(1)
    : <span className="text-slate-400">—</span>}
</td>
<td className="py-2.5 pr-3 text-slate-600">
  {item.gsc_clicks !== null && item.gsc_clicks !== undefined
    ? item.gsc_clicks
    : <span className="text-slate-400">—</span>}
</td>
<td className="py-2.5 pr-3 text-slate-600">
  {item.gsc_impressions !== null && item.gsc_impressions !== undefined
    ? item.gsc_impressions
    : <span className="text-slate-400">—</span>}
</td>
<td className="py-2.5 pr-3">
  <RankingBadge status={item.ranking_status} />
</td>
```

- [ ] **Step 10: Type-check**

Run:
```bash
cd frontend && npx tsc --noEmit
```
Expected: No errors.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx
git commit -m "feat: add GSC columns, ranking badge, filter, and cross-reference button"
```

---

### Task 5: End-to-End Verification

**Files:** None (verification only)

- [ ] **Step 1: Run all backend tests**

Run:
```bash
python -m pytest tests/test_keyword_research.py -v
```
Expected: All 24 tests pass.

- [ ] **Step 2: Type-check frontend**

Run:
```bash
cd frontend && npx tsc --noEmit
```
Expected: No errors.

- [ ] **Step 3: Verify all routes registered**

Run:
```bash
python -c "
from backend.app.main import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
kw = [r for r in routes if 'keyword' in r or 'competitor' in r or 'gsc' in r]
for r in sorted(kw):
    print(r)
"
```
Expected: 12 routes including `/api/keywords/target/gsc-crossref`.
