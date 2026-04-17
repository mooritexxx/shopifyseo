# Keyword Research Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the keyword research pipeline from one Ahrefs source to four, and add a Competitors tab for managing competitor domains.

**Architecture:** Three new Ahrefs API call functions are added to `keyword_research.py` alongside the existing `_call_ahrefs_related_terms`. The `run_research` function is updated to call all four sources sequentially, combining results before dedup. A new Competitors tab and CRUD endpoints manage competitor domains stored in `service_settings`.

**Tech Stack:** FastAPI, SQLite (service_settings JSON), React/TypeScript, TanStack Query, Zod, Ahrefs REST API v3

---

## File Map

| File | Role |
|------|------|
| `backend/app/services/keyword_research.py` | Add 3 new API call functions, update `run_research` pipeline |
| `backend/app/routers/keywords.py` | Add competitor domain CRUD endpoints |
| `frontend/src/routes/keywords-page.tsx` | Add Competitors tab with domain list UI |
| `tests/test_keyword_research.py` | Update merge test, add tests for pipeline helpers |

---

### Task 1: Competitor Domain CRUD Endpoints

**Files:**
- Modify: `backend/app/routers/keywords.py`

- [ ] **Step 1: Add competitor domain endpoints**

Add these three endpoints to `backend/app/routers/keywords.py`. Place them after the seed keyword endpoints and before the target keyword endpoints. Add a `CompetitorAddRequest` model, a `COMPETITOR_KEY` constant, and helper functions.

```python
# Add at top of file, after SEED_KEY = "seed_keywords"
COMPETITOR_KEY = "competitor_domains"


class CompetitorAddRequest(BaseModel):
    domain: str


def _load_competitors(conn: sqlite3.Connection) -> list[str]:
    raw = get_service_setting(conn, COMPETITOR_KEY, "[]")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def _save_competitors(conn: sqlite3.Connection, domains: list[str]) -> None:
    set_service_setting(conn, COMPETITOR_KEY, json.dumps(domains))


@router.get("/competitors", response_model=dict)
def get_competitors():
    conn = open_db_connection()
    try:
        items = _load_competitors(conn)
        return {"ok": True, "data": {"items": items, "total": len(items)}}
    finally:
        conn.close()


@router.post("/competitors", response_model=dict)
def add_competitor(payload: CompetitorAddRequest):
    domain = payload.domain.strip().lower()
    if not domain:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain cannot be empty")
    conn = open_db_connection()
    try:
        items = _load_competitors(conn)
        if domain in items:
            return {"ok": True, "data": {"items": items, "total": len(items)}}
        items.append(domain)
        _save_competitors(conn, items)
        return {"ok": True, "data": {"items": items, "total": len(items)}}
    finally:
        conn.close()


@router.delete("/competitors/{domain:path}", response_model=dict)
def delete_competitor(domain: str):
    conn = open_db_connection()
    try:
        items = _load_competitors(conn)
        filtered = [d for d in items if d != domain.lower()]
        if len(filtered) == len(items):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found")
        _save_competitors(conn, filtered)
        return {"ok": True, "data": {"items": filtered, "total": len(filtered)}}
    finally:
        conn.close()
```

- [ ] **Step 2: Verify endpoints register**

Run:
```bash
python -c "
from backend.app.main import app
routes = [r.path for r in app.routes if hasattr(r, 'path')]
comp = [r for r in routes if 'competitor' in r]
print('Competitor routes:', comp)
"
```

Expected: Three routes — GET, POST, DELETE for `/api/keywords/competitors`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/keywords.py
git commit -m "feat: add competitor domain CRUD endpoints"
```

---

### Task 2: Competitors Tab in Frontend

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

- [ ] **Step 1: Add competitors tab config and schema**

In `keywords-page.tsx`, update the `tabs` array to include the competitors tab between seed and target. Add a Zod schema for the competitors payload.

Replace the existing `tabs` constant (around line 44-55):

```typescript
const competitorPayloadSchema = z.object({
  items: z.array(z.string()),
  total: z.number()
});

const tabs = [
  {
    id: "seed",
    label: "Seed Keywords",
    description: "Core keywords you supply to define your topic clusters."
  },
  {
    id: "competitors",
    label: "Competitors",
    description: "Competitor domains to mine for organic keyword opportunities."
  },
  {
    id: "target",
    label: "Target Keywords",
    description: "Keywords related to your seeds — discovered and prioritised for content."
  }
] as const;
```

- [ ] **Step 2: Add CompetitorsPanel component**

Add this component after the `SeedKeywordsPanel` function (at the end of the file, before the closing). Also add `Trash2` to the lucide-react imports at the top.

```typescript
function CompetitorsPanel() {
  const queryClient = useQueryClient();
  const [newDomain, setNewDomain] = useState("");

  const query = useQuery({
    queryKey: ["competitor-domains"],
    queryFn: () => getJson("/api/keywords/competitors", competitorPayloadSchema)
  });

  const addMutation = useMutation({
    mutationFn: (domain: string) =>
      postJson("/api/keywords/competitors", competitorPayloadSchema, { domain }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setNewDomain("");
    }
  });

  const deleteMutation = useMutation({
    mutationFn: async (domain: string) => {
      const res = await fetch(`/api/keywords/competitors/${encodeURIComponent(domain)}`, {
        method: "DELETE"
      });
      if (!res.ok) throw new Error("Failed to delete");
      return res.json();
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["competitor-domains"] })
  });

  const items = query.data?.items ?? [];

  function handleAdd() {
    const d = newDomain.trim();
    if (!d) return;
    addMutation.mutate(d);
  }

  return (
    <div className="rounded-[24px] border border-line/80 bg-white p-5">
      <div>
        <h3 className="text-lg font-semibold text-ink">Competitor Domains</h3>
        <p className="mt-1 text-sm text-slate-500">
          Add competitor domains to mine their organic keywords during research.
        </p>
      </div>

      <div className="mt-5 flex gap-2">
        <input
          type="text"
          value={newDomain}
          onChange={(e) => setNewDomain(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="e.g. 180smoke.ca"
          className="flex-1 rounded-xl border border-line bg-[#f7f9fc] px-4 py-2.5 text-sm text-ink placeholder:text-slate-400 focus:border-ocean focus:outline-none focus:ring-1 focus:ring-ocean"
        />
        <Button variant="outline" size="sm" onClick={handleAdd} disabled={!newDomain.trim() || addMutation.isPending}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add
        </Button>
      </div>

      {query.isLoading ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center text-sm text-slate-400">
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
          No competitor domains added yet.
        </div>
      ) : (
        <div className="mt-5 space-y-2">
          {items.map((domain) => (
            <div
              key={domain}
              className="flex items-center justify-between rounded-xl border border-line bg-[#f7f9fc] px-4 py-3"
            >
              <span className="text-sm font-medium text-ink">{domain}</span>
              <button
                type="button"
                onClick={() => deleteMutation.mutate(domain)}
                disabled={deleteMutation.isPending}
                className="rounded-lg p-1.5 text-slate-400 transition hover:bg-red-50 hover:text-red-500"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Wire the tab rendering**

In the `KeywordsPage` component, update the tab grid to `md:grid-cols-3` and add the competitors panel render:

Change `md:grid-cols-2` to `md:grid-cols-3` in the grid div.

Add after `{activeTab === "seed" && <SeedKeywordsPanel />}`:
```typescript
{activeTab === "competitors" && <CompetitorsPanel />}
```

- [ ] **Step 4: Type-check**

Run:
```bash
cd frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx
git commit -m "feat: add Competitors tab with domain list management"
```

---

### Task 3: Expand Research Pipeline with Three New Ahrefs Sources

**Files:**
- Modify: `backend/app/services/keyword_research.py`
- Modify: `backend/app/routers/keywords.py`

- [ ] **Step 1: Add `_call_ahrefs_matching_terms` function**

Add this function after the existing `_call_ahrefs_related_terms` function in `keyword_research.py`:

```python
def _call_ahrefs_matching_terms(token: str, keywords: list[str], max_difficulty: int = 70) -> tuple[list[dict], int]:
    select = "keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc"
    params = {
        "select": select,
        "country": "ca",
        "keywords": ",".join(keywords),
        "limit": 500,
        "order_by": "volume:desc",
        "where": json.dumps({
            "and": [
                {"field": "volume", "is": ["gte", 10]},
                {"field": "difficulty", "is": ["lte", max_difficulty]},
            ]
        }),
    }
    url = f"{AHREFS_BASE}/keywords-explorer/matching-terms?{urlencode(params, safe=',')}"
    try:
        data = request_json(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except HttpRequestError as exc:
        logger.error("Ahrefs matching-terms error: %s", exc)
        raise RuntimeError(f"Ahrefs matching-terms error: {exc}") from exc
    items = data.get("keywords", [])
    cost = data.get("apiUsageCosts", {}).get("units-cost-total-actual", 0)
    return items, cost
```

- [ ] **Step 2: Add `_call_ahrefs_search_suggestions` function**

Add this function right after `_call_ahrefs_matching_terms`:

```python
def _call_ahrefs_search_suggestions(token: str, keywords: list[str], max_difficulty: int = 70) -> tuple[list[dict], int]:
    select = "keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc"
    params = {
        "select": select,
        "country": "ca",
        "keywords": ",".join(keywords),
        "limit": 500,
        "order_by": "volume:desc",
        "where": json.dumps({
            "and": [
                {"field": "volume", "is": ["gte", 5]},
                {"field": "difficulty", "is": ["lte", max_difficulty]},
            ]
        }),
    }
    url = f"{AHREFS_BASE}/keywords-explorer/search-suggestions?{urlencode(params, safe=',')}"
    try:
        data = request_json(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except HttpRequestError as exc:
        logger.error("Ahrefs search-suggestions error: %s", exc)
        raise RuntimeError(f"Ahrefs search-suggestions error: {exc}") from exc
    items = data.get("keywords", [])
    cost = data.get("apiUsageCosts", {}).get("units-cost-total-actual", 0)
    return items, cost
```

- [ ] **Step 3: Add `_call_ahrefs_competitor_keywords` function**

Add this function right after `_call_ahrefs_search_suggestions`:

```python
def _call_ahrefs_competitor_keywords(token: str, domain: str, max_difficulty: int = 70) -> tuple[list[dict], int]:
    select = "keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    params = {
        "select": select,
        "target": domain,
        "mode": "subdomains",
        "country": "ca",
        "date": today,
        "limit": 500,
        "order_by": "volume:desc",
        "where": json.dumps({
            "and": [
                {"field": "volume", "is": ["gte", 10]},
                {"field": "difficulty", "is": ["lte", max_difficulty]},
            ]
        }),
    }
    url = f"{AHREFS_BASE}/site-explorer/organic-keywords?{urlencode(params, safe=',')}"
    try:
        data = request_json(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except HttpRequestError as exc:
        logger.error("Ahrefs organic-keywords error for %s: %s", domain, exc)
        raise RuntimeError(f"Ahrefs organic-keywords error for {domain}: {exc}") from exc
    items = data.get("keywords", [])
    cost = data.get("apiUsageCosts", {}).get("units-cost-total-actual", 0)
    return items, cost
```

- [ ] **Step 4: Update `_call_ahrefs_related_terms` to accept `max_difficulty` parameter**

Update the existing function signature and filter:

Change:
```python
def _call_ahrefs_related_terms(token: str, keywords: list[str]) -> tuple[list[dict], int]:
```
To:
```python
def _call_ahrefs_related_terms(token: str, keywords: list[str], max_difficulty: int = 70) -> tuple[list[dict], int]:
```

And update the `where` filter to use the parameter:
```python
{"field": "difficulty", "is": ["lte", max_difficulty]},
```

- [ ] **Step 5: Update `run_research` to call all four sources**

Replace the entire `run_research` function with:

```python
def run_research(conn: sqlite3.Connection) -> dict:
    token = get_service_setting(conn, "ahrefs_api_token")
    if not token:
        raise RuntimeError("Ahrefs API token not configured. Add it in Settings > Integrations.")

    seed_raw = get_service_setting(conn, "seed_keywords", "[]")
    try:
        seeds = json.loads(seed_raw)
    except json.JSONDecodeError:
        seeds = []

    competitor_raw = get_service_setting(conn, "competitor_domains", "[]")
    try:
        competitors = json.loads(competitor_raw)
    except json.JSONDecodeError:
        competitors = []

    if not seeds and not competitors:
        raise RuntimeError("No seed keywords or competitor domains found. Add them in the Keywords page.")

    seed_strings = [s["keyword"] for s in seeds]
    batches = _batch_seeds(seed_strings) if seed_strings else []

    all_raw: list[dict] = []
    total_cost = 0

    # Source 1: Related terms (existing)
    for batch in batches:
        items, cost = _call_ahrefs_related_terms(token, batch)
        for item in items:
            item["seed_keywords"] = set(batch)
        all_raw.extend(items)
        total_cost += cost

    # Source 2: Matching terms
    for batch in batches:
        items, cost = _call_ahrefs_matching_terms(token, batch)
        for item in items:
            item["seed_keywords"] = set(batch)
        all_raw.extend(items)
        total_cost += cost

    # Source 3: Search suggestions
    for batch in batches:
        items, cost = _call_ahrefs_search_suggestions(token, batch)
        for item in items:
            item["seed_keywords"] = set(batch)
        all_raw.extend(items)
        total_cost += cost

    # Source 4: Competitor organic keywords
    for domain in competitors:
        items, cost = _call_ahrefs_competitor_keywords(token, domain)
        for item in items:
            item["seed_keywords"] = {domain}
        all_raw.extend(items)
        total_cost += cost

    deduped = deduplicate_results(all_raw)

    for item in deduped:
        intent, content_type = classify_intent(item.get("intents"))
        item["intent"] = intent
        item["intent_raw"] = item.pop("intents", None) or {}
        item["content_type"] = content_type
        item["opportunity_raw"] = compute_opportunity(
            volume=item.get("volume") or 0,
            traffic_potential=item.get("traffic_potential"),
            difficulty=item.get("difficulty") or 0,
        )
        item["status"] = "new"

    normalize_opportunity_scores(deduped)

    for item in deduped:
        item.pop("opportunity_raw", None)

    existing_raw = get_service_setting(conn, TARGET_KEY, "{}")
    try:
        existing_data = json.loads(existing_raw)
    except json.JSONDecodeError:
        existing_data = {}
    existing_items = existing_data.get("items", [])

    merged = merge_with_existing(existing_items, deduped)
    merged.sort(key=lambda x: x.get("opportunity", 0), reverse=True)

    result = {
        "last_run": datetime.now(timezone.utc).isoformat(),
        "unit_cost": total_cost,
        "items": merged,
        "total": len(merged),
    }
    set_service_setting(conn, TARGET_KEY, json.dumps(result))
    return result
```

- [ ] **Step 6: Update router to load competitors for research**

No change needed — `run_research` already loads competitors directly from the DB via `get_service_setting`.

- [ ] **Step 7: Run existing tests to verify nothing broke**

Run:
```bash
python -m pytest tests/test_keyword_research.py -v
```

Expected: All 12 tests pass. The pure-logic functions (scoring, intent, dedup, merge) are unchanged.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/keyword_research.py
git commit -m "feat: expand research pipeline with matching terms, search suggestions, and competitor organic keywords"
```

---

### Task 4: Tests and Verification

**Files:**
- Modify: `tests/test_keyword_research.py`

- [ ] **Step 1: Add test for batch_seeds helper**

```python
def test_batch_seeds_groups_of_five():
    from backend.app.services.keyword_research import _batch_seeds
    seeds = ["a", "b", "c", "d", "e", "f", "g"]
    batches = _batch_seeds(seeds)
    assert len(batches) == 2
    assert batches[0] == ["a", "b", "c", "d", "e"]
    assert batches[1] == ["f", "g"]


def test_batch_seeds_empty():
    from backend.app.services.keyword_research import _batch_seeds
    assert _batch_seeds([]) == []
```

- [ ] **Step 2: Run all tests**

Run:
```bash
python -m pytest tests/test_keyword_research.py -v
```

Expected: All 14 tests pass.

- [ ] **Step 3: Type-check frontend**

Run:
```bash
cd frontend && npx tsc --noEmit
```

Expected: No errors.

- [ ] **Step 4: Commit**

```bash
git add tests/test_keyword_research.py
git commit -m "test: add batch_seeds tests"
```
