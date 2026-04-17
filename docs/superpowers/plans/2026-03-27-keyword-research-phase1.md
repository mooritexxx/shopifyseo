# Keyword Research Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand seed keywords into a researched target keyword list via Ahrefs API, with scoring, intent classification, and a full UI for filtering/approving keywords.

**Architecture:** Backend service calls Ahrefs REST API v3 in batches, scores and classifies results, stores in DB. Frontend displays a sortable/filterable table with bulk approve/dismiss actions. Ahrefs API token stored in settings alongside other credentials.

**Tech Stack:** Python/FastAPI, Ahrefs REST API v3, React/TypeScript, Tailwind CSS, TanStack Query, Zod

---

## File Structure

### New Files
| File | Responsibility |
|---|---|
| `backend/app/services/keyword_research.py` | Ahrefs HTTP calls, batching, deduplication, scoring, intent classification, merge logic |
| `tests/test_keyword_research.py` | Unit tests for scoring, intent classification, dedup, merge |

### Modified Files
| File | Changes |
|---|---|
| `backend/app/schemas/operations.py` | Add `ahrefs_api_token` to `SettingsValuesPayload` and `SettingsUpdatePayload` |
| `backend/app/services/dashboard_service.py` | Add `AHREFS_API_TOKEN` / `ahrefs_api_token` to settings env mapping |
| `backend/app/routers/keywords.py` | Add target keyword endpoints: research, get, status, bulk-status |
| `frontend/src/routes/keywords-page.tsx` | Build TargetKeywordsPanel: table, filters, bulk actions |
| `frontend/src/routes/settings-page.tsx` | Add Ahrefs section to integrations tab |

---

### Task 1: Add Ahrefs API Token to Settings

**Files:**
- Modify: `backend/app/schemas/operations.py:51-76` (SettingsValuesPayload)
- Modify: `backend/app/schemas/operations.py:107-131` (SettingsUpdatePayload)
- Modify: `backend/app/services/dashboard_service.py:443` (settings env mapping)
- Modify: `frontend/src/routes/settings-page.tsx:182-187` (integrations tab sections)

- [ ] **Step 1: Add `ahrefs_api_token` to `SettingsValuesPayload`**

In `backend/app/schemas/operations.py`, add after the `shopify_client_secret` line:

```python
class SettingsValuesPayload(BaseModel):
    shopify_shop: str = ""
    shopify_api_version: str = ""
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    ahrefs_api_token: str = ""          # <-- add this line
    google_client_id: str = ""
    # ... rest unchanged
```

- [ ] **Step 2: Add `ahrefs_api_token` to `SettingsUpdatePayload`**

Same file, add after `shopify_client_secret` in the update payload:

```python
class SettingsUpdatePayload(BaseModel):
    shopify_shop: str = ""
    shopify_api_version: str = ""
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    ahrefs_api_token: str = ""          # <-- add this line
    google_client_id: str = ""
    # ... rest unchanged
```

- [ ] **Step 3: Add env mapping in `dashboard_service.py`**

In `backend/app/services/dashboard_service.py`, in the `get_settings_data` function, add after the `("SHOPIFY_CLIENT_SECRET", "shopify_client_secret")` line:

```python
            ("SHOPIFY_CLIENT_SECRET", "shopify_client_secret"),
            ("AHREFS_API_TOKEN", "ahrefs_api_token"),       # <-- add this line
            ("GOOGLE_CLIENT_ID", "google_client_id"),
```

- [ ] **Step 4: Add Ahrefs section to Settings frontend integrations tab**

In `frontend/src/routes/settings-page.tsx`, in the `tabSections.integrations` array, add a new section after "Provider Credentials":

```typescript
    integrations: [
      {
        title: "Provider Credentials",
        description: "Store the keys and endpoints used by your AI providers.",
        fields: ["openai_api_key", "gemini_api_key", "anthropic_api_key", "openrouter_api_key", "ollama_api_key", "ollama_base_url"]
      },
      {
        title: "Ahrefs",
        description: "API token for keyword research. Get yours from Ahrefs API settings.",
        fields: ["ahrefs_api_token"]
      }
    ],
```

- [ ] **Step 5: Verify settings page loads**

Run the backend and frontend dev servers. Navigate to Settings > Integrations. Confirm the "Ahrefs" section appears with the `ahrefs_api_token` field. Save a test value and reload — confirm it persists.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/operations.py backend/app/services/dashboard_service.py frontend/src/routes/settings-page.tsx
git commit -m "feat: add Ahrefs API token to settings page"
```

---

### Task 2: Keyword Research Service — Core Logic

**Files:**
- Create: `backend/app/services/keyword_research.py`
- Create: `tests/test_keyword_research.py`

- [ ] **Step 1: Write tests for scoring and intent classification**

Create `tests/test_keyword_research.py`:

```python
from backend.app.services.keyword_research import (
    classify_intent,
    compute_opportunity,
    normalize_opportunity_scores,
)


def test_compute_opportunity_low_difficulty_high_volume():
    score = compute_opportunity(volume=1000, traffic_potential=2000, difficulty=5)
    assert score > 0
    # Low difficulty should produce high raw score
    assert score > compute_opportunity(volume=1000, traffic_potential=2000, difficulty=50)


def test_compute_opportunity_zero_volume():
    score = compute_opportunity(volume=0, traffic_potential=500, difficulty=10)
    assert score == 0.0


def test_compute_opportunity_none_traffic():
    score = compute_opportunity(volume=500, traffic_potential=None, difficulty=10)
    # Should treat None traffic_potential as volume (fallback)
    assert score > 0


def test_classify_intent_transactional_wins():
    intents = {"informational": True, "commercial": True, "transactional": True, "navigational": False, "branded": False}
    intent, content_type = classify_intent(intents)
    assert intent == "transactional"
    assert content_type == "Product / Collection page"


def test_classify_intent_commercial():
    intents = {"informational": True, "commercial": True, "transactional": False, "navigational": False, "branded": False}
    intent, content_type = classify_intent(intents)
    assert intent == "commercial"
    assert content_type == "Comparison / Buying guide"


def test_classify_intent_informational_only():
    intents = {"informational": True, "commercial": False, "transactional": False, "navigational": False, "branded": False}
    intent, content_type = classify_intent(intents)
    assert intent == "informational"
    assert content_type == "Blog / Guide"


def test_classify_intent_branded():
    intents = {"informational": False, "commercial": False, "transactional": False, "navigational": False, "branded": True}
    intent, content_type = classify_intent(intents)
    assert intent == "branded"
    assert content_type == "Brand page"


def test_classify_intent_none():
    intent, content_type = classify_intent(None)
    assert intent == "informational"
    assert content_type == "Blog / Guide"


def test_normalize_opportunity_scores():
    items = [
        {"opportunity_raw": 100},
        {"opportunity_raw": 50},
        {"opportunity_raw": 0},
    ]
    normalize_opportunity_scores(items)
    assert items[0]["opportunity"] == 100.0
    assert items[1]["opportunity"] == 50.0
    assert items[2]["opportunity"] == 0.0


def test_normalize_opportunity_scores_all_zero():
    items = [{"opportunity_raw": 0}, {"opportunity_raw": 0}]
    normalize_opportunity_scores(items)
    assert items[0]["opportunity"] == 0.0
    assert items[1]["opportunity"] == 0.0
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
pytest tests/test_keyword_research.py -v
```

Expected: ImportError — module does not exist yet.

- [ ] **Step 3: Write tests for deduplication and merge logic**

Append to `tests/test_keyword_research.py`:

```python
from backend.app.services.keyword_research import deduplicate_results, merge_with_existing


def test_deduplicate_results():
    raw = [
        {"keyword": "vape canada", "volume": 100, "seed": "seed1"},
        {"keyword": "Vape Canada", "volume": 200, "seed": "seed2"},
        {"keyword": "other keyword", "volume": 50, "seed": "seed1"},
    ]
    deduped = deduplicate_results(raw)
    assert len(deduped) == 2
    # Should keep the one with higher volume
    vape = next(r for r in deduped if r["keyword"].lower() == "vape canada")
    assert vape["volume"] == 200
    assert set(vape["seed_keywords"]) == {"seed1", "seed2"}


def test_merge_with_existing_preserves_status():
    existing = [
        {"keyword": "vape canada", "status": "approved", "volume": 100},
        {"keyword": "old keyword", "status": "dismissed", "volume": 50},
    ]
    new_items = [
        {"keyword": "vape canada", "status": "new", "volume": 200},
        {"keyword": "fresh keyword", "status": "new", "volume": 300},
    ]
    merged = merge_with_existing(existing, new_items)
    assert len(merged) == 2  # "old keyword" removed (not in new results)
    vape = next(r for r in merged if r["keyword"] == "vape canada")
    assert vape["status"] == "approved"  # preserved
    assert vape["volume"] == 200  # updated metrics
    fresh = next(r for r in merged if r["keyword"] == "fresh keyword")
    assert fresh["status"] == "new"
```

- [ ] **Step 4: Implement the service**

Create `backend/app/services/keyword_research.py`:

```python
import json
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlencode

from shopifyseo.dashboard_google import get_service_setting, set_service_setting
from shopifyseo.dashboard_http import HttpRequestError, request_json

logger = logging.getLogger(__name__)

AHREFS_BASE = "https://api.ahrefs.com/v3"
TARGET_KEY = "target_keywords"

INTENT_PRIORITY = ["transactional", "commercial", "informational", "navigational", "branded"]
INTENT_TO_CONTENT = {
    "transactional": "Product / Collection page",
    "commercial": "Comparison / Buying guide",
    "informational": "Blog / Guide",
    "navigational": "Brand page",
    "branded": "Brand page",
}


def compute_opportunity(volume: int, traffic_potential: int | None, difficulty: int) -> float:
    v = volume or 0
    tp = traffic_potential if traffic_potential else v
    d = difficulty or 0
    if v == 0:
        return 0.0
    return (v * tp) / ((d + 1) ** 2)


def normalize_opportunity_scores(items: list[dict]) -> None:
    if not items:
        return
    max_raw = max(item.get("opportunity_raw", 0) for item in items)
    for item in items:
        raw = item.get("opportunity_raw", 0)
        item["opportunity"] = round((raw / max_raw) * 100, 1) if max_raw > 0 else 0.0


def classify_intent(intents: dict | None) -> tuple[str, str]:
    if not intents:
        return "informational", INTENT_TO_CONTENT["informational"]
    for intent_key in INTENT_PRIORITY:
        if intents.get(intent_key):
            return intent_key, INTENT_TO_CONTENT[intent_key]
    return "informational", INTENT_TO_CONTENT["informational"]


def deduplicate_results(raw_items: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for item in raw_items:
        key = item["keyword"].lower()
        if key in seen:
            existing = seen[key]
            if item.get("volume", 0) > existing.get("volume", 0):
                seeds = existing.get("seed_keywords", set())
                seeds.update(item.get("seed_keywords", set()))
                item["seed_keywords"] = seeds
                seen[key] = item
            else:
                existing.setdefault("seed_keywords", set()).update(item.get("seed_keywords", set()))
        else:
            item["seed_keywords"] = set(item.get("seed_keywords", set()))
            seen[key] = item
    result = list(seen.values())
    for item in result:
        item["seed_keywords"] = sorted(item["seed_keywords"])
    return result


def merge_with_existing(existing: list[dict], new_items: list[dict]) -> list[dict]:
    status_map = {item["keyword"].lower(): item["status"] for item in existing}
    merged = []
    for item in new_items:
        key = item["keyword"].lower()
        if key in status_map:
            item["status"] = status_map[key]
        merged.append(item)
    return merged


def _batch_seeds(seeds: list[str], batch_size: int = 5) -> list[list[str]]:
    return [seeds[i:i + batch_size] for i in range(0, len(seeds), batch_size)]


def _call_ahrefs_related_terms(token: str, keywords: list[str]) -> tuple[list[dict], int]:
    select = "keyword,volume,difficulty,traffic_potential,intents,parent_topic,cpc"
    params = {
        "select": select,
        "country": "ca",
        "keywords": ",".join(keywords),
        "terms": "also_rank_for",
        "limit": 500,
        "order_by": "volume:desc",
        "where": json.dumps({
            "and": [
                {"field": "volume", "is": ["gte", 10]},
                {"field": "difficulty", "is": ["lte", 70]},
            ]
        }),
    }
    url = f"{AHREFS_BASE}/keywords-explorer/related-terms?{urlencode(params, safe=',')}"
    try:
        data = request_json(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    except HttpRequestError as exc:
        logger.error("Ahrefs API error: %s", exc)
        raise RuntimeError(f"Ahrefs API error: {exc}") from exc
    items = data.get("keywords", [])
    cost = data.get("apiUsageCosts", {}).get("units-cost-total-actual", 0)
    return items, cost


def run_research(conn: sqlite3.Connection) -> dict:
    token = get_service_setting(conn, "ahrefs_api_token")
    if not token:
        raise RuntimeError("Ahrefs API token not configured. Add it in Settings > Integrations.")

    seed_raw = get_service_setting(conn, "seed_keywords", "[]")
    try:
        seeds = json.loads(seed_raw)
    except json.JSONDecodeError:
        seeds = []
    if not seeds:
        raise RuntimeError("No seed keywords found. Add seeds in the Keywords > Seed Keywords tab.")

    seed_strings = [s["keyword"] for s in seeds]
    batches = _batch_seeds(seed_strings)

    all_raw: list[dict] = []
    total_cost = 0

    for batch in batches:
        items, cost = _call_ahrefs_related_terms(token, batch)
        for item in items:
            item["seed_keywords"] = set(batch)
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


def load_target_keywords(conn: sqlite3.Connection) -> dict:
    raw = get_service_setting(conn, TARGET_KEY, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not data.get("items"):
        return {"last_run": None, "unit_cost": 0, "items": [], "total": 0}
    return data


def update_keyword_status(conn: sqlite3.Connection, keyword: str, new_status: str) -> dict:
    data = load_target_keywords(conn)
    found = False
    for item in data["items"]:
        if item["keyword"].lower() == keyword.lower():
            item["status"] = new_status
            found = True
            break
    if not found:
        raise ValueError(f"Keyword not found: {keyword}")
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    return {"keyword": keyword, "status": new_status}


def bulk_update_status(conn: sqlite3.Connection, keywords: list[str], new_status: str) -> int:
    data = load_target_keywords(conn)
    keyword_set = {kw.lower() for kw in keywords}
    updated = 0
    for item in data["items"]:
        if item["keyword"].lower() in keyword_set:
            item["status"] = new_status
            updated += 1
    set_service_setting(conn, TARGET_KEY, json.dumps(data))
    return updated
```

- [ ] **Step 5: Run tests — confirm they pass**

```bash
pytest tests/test_keyword_research.py -v
```

Expected: All 11 tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/keyword_research.py tests/test_keyword_research.py
git commit -m "feat: add keyword research service with scoring, intent classification, and merge logic"
```

---

### Task 3: Target Keyword API Endpoints

**Files:**
- Modify: `backend/app/routers/keywords.py`

- [ ] **Step 1: Add Pydantic models for target keyword endpoints**

At the top of `backend/app/routers/keywords.py`, add imports and models after the existing `SeedKeywordsSaveRequest`:

```python
from backend.app.services.keyword_research import (
    bulk_update_status,
    load_target_keywords,
    run_research,
    update_keyword_status,
)

# ... after SeedKeywordsSaveRequest class ...

class KeywordStatusRequest(BaseModel):
    status: str  # "new" | "approved" | "dismissed"


class BulkStatusRequest(BaseModel):
    keywords: list[str]
    status: str  # "new" | "approved" | "dismissed"
```

- [ ] **Step 2: Add the four target keyword endpoints**

Append to `backend/app/routers/keywords.py`:

```python
TARGET_PREFIX = "/target"


@router.get(TARGET_PREFIX, response_model=dict)
def get_target_keywords():
    conn = open_db_connection()
    try:
        data = load_target_keywords(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.post(TARGET_PREFIX + "/research", response_model=dict)
def research_target_keywords():
    conn = open_db_connection()
    try:
        data = run_research(conn)
        return {"ok": True, "data": data}
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        conn.close()


@router.patch(TARGET_PREFIX + "/{keyword}/status", response_model=dict)
def patch_keyword_status(keyword: str, payload: KeywordStatusRequest):
    if payload.status not in ("new", "approved", "dismissed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
    conn = open_db_connection()
    try:
        result = update_keyword_status(conn, keyword, payload.status)
        return {"ok": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    finally:
        conn.close()


@router.patch(TARGET_PREFIX + "/bulk-status", response_model=dict)
def patch_bulk_status(payload: BulkStatusRequest):
    if payload.status not in ("new", "approved", "dismissed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
    conn = open_db_connection()
    try:
        updated = bulk_update_status(conn, payload.keywords, payload.status)
        return {"ok": True, "data": {"updated": updated}}
    finally:
        conn.close()
```

- [ ] **Step 3: Verify endpoints register**

```bash
python -c "from backend.app.routers.keywords import router; print([r.path for r in router.routes])"
```

Expected output includes: `/api/keywords/target`, `/api/keywords/target/research`, `/api/keywords/target/{keyword}/status`, `/api/keywords/target/bulk-status`

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/keywords.py
git commit -m "feat: add target keyword API endpoints (research, get, status, bulk-status)"
```

---

### Task 4: Frontend — Target Keywords Table and Filters

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

- [ ] **Step 1: Add Zod schemas for target keywords API**

At the top of `keywords-page.tsx`, add after the existing `seedPayloadSchema`:

```typescript
const targetKeywordSchema = z.object({
  keyword: z.string(),
  volume: z.number().nullable(),
  difficulty: z.number().nullable(),
  traffic_potential: z.number().nullable(),
  cpc: z.number().nullable(),
  intent: z.string(),
  intent_raw: z.record(z.boolean()).nullable(),
  content_type: z.string(),
  parent_topic: z.string().nullable(),
  opportunity: z.number(),
  seed_keywords: z.array(z.string()),
  status: z.string()
});

const targetPayloadSchema = z.object({
  last_run: z.string().nullable(),
  unit_cost: z.number(),
  items: z.array(targetKeywordSchema),
  total: z.number()
});
```

- [ ] **Step 2: Replace the TargetKeywordsPanel placeholder**

Replace the entire `TargetKeywordsPanel` function with the full implementation:

```typescript
function TargetKeywordsPanel() {
  const queryClient = useQueryClient();
  const [intentFilter, setIntentFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [difficultyFilter, setDifficultyFilter] = useState("all");
  const [sort, setSort] = useState("opportunity");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const query = useQuery({
    queryKey: ["target-keywords"],
    queryFn: () => getJson("/api/keywords/target", targetPayloadSchema)
  });

  const researchMutation = useMutation({
    mutationFn: () => postJson("/api/keywords/target/research", targetPayloadSchema),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["target-keywords"] });
      setSelected(new Set());
    }
  });

  const bulkStatusMutation = useMutation({
    mutationFn: (args: { keywords: string[]; status: string }) =>
      postJson("/api/keywords/target/bulk-status", z.object({ updated: z.number() }), args),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["target-keywords"] });
      setSelected(new Set());
    }
  });

  const statusMutation = useMutation({
    mutationFn: (args: { keyword: string; status: string }) =>
      fetch(`/api/keywords/target/${encodeURIComponent(args.keyword)}/status`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: args.status })
      }).then(() => {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["target-keywords"] })
  });

  const items = query.data?.items ?? [];
  const lastRun = query.data?.last_run;

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (intentFilter !== "all" && item.intent !== intentFilter) return false;
      if (statusFilter !== "all" && item.status !== statusFilter) return false;
      if (difficultyFilter === "easy" && (item.difficulty ?? 0) > 20) return false;
      if (difficultyFilter === "medium" && ((item.difficulty ?? 0) <= 20 || (item.difficulty ?? 0) > 50)) return false;
      if (difficultyFilter === "hard" && (item.difficulty ?? 0) <= 50) return false;
      return true;
    });
  }, [items, intentFilter, statusFilter, difficultyFilter]);

  const sorted = useMemo(() => {
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = a[sort as keyof typeof a] ?? 0;
      const bv = b[sort as keyof typeof b] ?? 0;
      if (typeof av === "number" && typeof bv === "number") {
        return sortDir === "desc" ? bv - av : av - bv;
      }
      return sortDir === "desc"
        ? String(bv).localeCompare(String(av))
        : String(av).localeCompare(String(bv));
    });
    return copy;
  }, [filtered, sort, sortDir]);

  function toggleSort(col: string) {
    if (sort === col) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSort(col);
      setSortDir(col === "keyword" || col === "intent" ? "asc" : "desc");
    }
  }

  function toggleSelect(keyword: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(keyword)) next.delete(keyword);
      else next.add(keyword);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selected.size === sorted.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(sorted.map((r) => r.keyword)));
    }
  }

  const diffBadge = (d: number | null) => {
    const v = d ?? 0;
    if (v <= 20) return "bg-emerald-100 text-emerald-700";
    if (v <= 50) return "bg-amber-100 text-amber-700";
    return "bg-red-100 text-red-700";
  };

  const oppBadge = (o: number) => {
    if (o >= 70) return "bg-emerald-100 text-emerald-700";
    if (o >= 30) return "bg-amber-100 text-amber-700";
    return "bg-slate-100 text-slate-500";
  };

  const intentBadge: Record<string, string> = {
    informational: "bg-blue-100 text-blue-700",
    commercial: "bg-purple-100 text-purple-700",
    transactional: "bg-emerald-100 text-emerald-700",
    navigational: "bg-slate-100 text-slate-600",
    branded: "bg-amber-100 text-amber-700"
  };

  const statusColors: Record<string, string> = {
    new: "text-slate-500",
    approved: "text-emerald-600",
    dismissed: "text-red-400"
  };

  return (
    <div className="rounded-[24px] border border-line/80 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-ink">Target Keywords</h3>
          <p className="mt-1 text-sm text-slate-500">
            {lastRun
              ? `Last run: ${new Date(lastRun).toLocaleDateString()} \u00b7 ${items.length} keywords found`
              : "No research data yet"}
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={researchMutation.isPending}
          onClick={() => researchMutation.mutate()}
        >
          <Sparkles className="mr-1.5 h-3.5 w-3.5" />
          {researchMutation.isPending ? "Researching\u2026" : "Run keyword research"}
        </Button>
      </div>

      {researchMutation.isError && (
        <p className="mt-3 text-sm text-red-600">{(researchMutation.error as Error).message}</p>
      )}

      {/* Filters */}
      {items.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-4">
          <FilterGroup
            label="Intent"
            value={intentFilter}
            onChange={setIntentFilter}
            options={[
              { id: "all", label: "All" },
              { id: "informational", label: "Informational" },
              { id: "commercial", label: "Commercial" },
              { id: "transactional", label: "Transactional" },
              { id: "branded", label: "Branded" }
            ]}
          />
          <FilterGroup
            label="Status"
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { id: "all", label: "All" },
              { id: "new", label: "New" },
              { id: "approved", label: "Approved" },
              { id: "dismissed", label: "Dismissed" }
            ]}
          />
          <FilterGroup
            label="Difficulty"
            value={difficultyFilter}
            onChange={setDifficultyFilter}
            options={[
              { id: "all", label: "All" },
              { id: "easy", label: "Easy (0-20)" },
              { id: "medium", label: "Medium (21-50)" },
              { id: "hard", label: "Hard (51-70)" }
            ]}
          />
        </div>
      )}

      {/* Bulk actions */}
      {selected.size > 0 && (
        <div className="mt-3 flex items-center gap-2 rounded-xl bg-[#f7f9fc] px-4 py-2">
          <span className="text-sm text-slate-500">{selected.size} selected</span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => bulkStatusMutation.mutate({ keywords: [...selected], status: "approved" })}
          >
            <Check className="mr-1 h-3.5 w-3.5" /> Approve
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => bulkStatusMutation.mutate({ keywords: [...selected], status: "dismissed" })}
          >
            <X className="mr-1 h-3.5 w-3.5" /> Dismiss
          </Button>
        </div>
      )}

      {/* Table */}
      {query.isLoading ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center text-sm text-slate-400">Loading\u2026</div>
      ) : items.length === 0 ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
          No target keywords yet \u2014 click "Run keyword research" to expand your seeds.
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-slate-400">
                <th className="px-2 py-2">
                  <input
                    type="checkbox"
                    checked={selected.size === sorted.length && sorted.length > 0}
                    onChange={toggleSelectAll}
                    className="rounded"
                  />
                </th>
                {[
                  { key: "keyword", label: "Keyword" },
                  { key: "volume", label: "Volume" },
                  { key: "difficulty", label: "KD" },
                  { key: "traffic_potential", label: "Traffic Pot." },
                  { key: "cpc", label: "CPC" },
                  { key: "intent", label: "Intent" },
                  { key: "content_type", label: "Content Type" },
                  { key: "opportunity", label: "Opportunity" },
                  { key: "status", label: "Status" }
                ].map((col) => (
                  <th
                    key={col.key}
                    className="cursor-pointer px-2 py-2 select-none"
                    onClick={() => toggleSort(col.key)}
                  >
                    {col.label}
                    {sort === col.key ? (sortDir === "desc" ? " \u25bc" : " \u25b2") : ""}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => (
                <tr key={row.keyword} className="border-b border-line/50 hover:bg-slate-50/50">
                  <td className="px-2 py-2">
                    <input
                      type="checkbox"
                      checked={selected.has(row.keyword)}
                      onChange={() => toggleSelect(row.keyword)}
                      className="rounded"
                    />
                  </td>
                  <td className="px-2 py-2 font-medium text-ink">{row.keyword}</td>
                  <td className="px-2 py-2 text-right">{row.volume?.toLocaleString() ?? "\u2014"}</td>
                  <td className="px-2 py-2 text-right">
                    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${diffBadge(row.difficulty)}`}>
                      {row.difficulty ?? "\u2014"}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-right">{row.traffic_potential?.toLocaleString() ?? "\u2014"}</td>
                  <td className="px-2 py-2 text-right">{row.cpc != null ? `$${(row.cpc / 100).toFixed(2)}` : "\u2014"}</td>
                  <td className="px-2 py-2">
                    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${intentBadge[row.intent] ?? "bg-slate-100 text-slate-500"}`}>
                      {row.intent}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-xs text-slate-600">{row.content_type}</td>
                  <td className="px-2 py-2 text-right">
                    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${oppBadge(row.opportunity)}`}>
                      {row.opportunity.toFixed(0)}
                    </span>
                  </td>
                  <td className="px-2 py-2">
                    <select
                      value={row.status}
                      onChange={(e) => statusMutation.mutate({ keyword: row.keyword, status: e.target.value })}
                      className={`rounded border border-line bg-transparent px-1.5 py-0.5 text-xs font-medium ${statusColors[row.status] ?? "text-slate-500"}`}
                    >
                      <option value="new">New</option>
                      <option value="approved">Approved</option>
                      <option value="dismissed">Dismissed</option>
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add the FilterGroup helper component**

Add before the `TargetKeywordsPanel` function:

```typescript
function FilterGroup({
  label,
  value,
  onChange,
  options
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { id: string; label: string }[];
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-slate-400">{label}:</span>
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          className={`rounded-full px-2.5 py-1 text-xs font-medium transition ${
            value === opt.id
              ? "bg-ink text-white"
              : "bg-slate-100 text-slate-500 hover:bg-slate-200"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Add missing imports**

Update the imports at the top of `keywords-page.tsx`:

```typescript
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Sparkles, X } from "lucide-react";
import { z } from "zod";

import { Card } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { getJson, postJson } from "../lib/api";
```

(Add `Check` to the lucide imports — the rest are already there.)

- [ ] **Step 5: Type-check**

```bash
cd frontend && npx tsc --noEmit --pretty
```

Expected: No errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx
git commit -m "feat: add target keywords table with filters, sorting, and bulk status actions"
```

---

### Task 5: End-to-End Verification

- [ ] **Step 1: Run all backend tests**

```bash
pytest tests/test_keyword_research.py -v
```

Expected: All tests pass.

- [ ] **Step 2: Start the app and verify full flow**

1. Navigate to Settings > Integrations. Enter the Ahrefs API token and save.
2. Navigate to Keywords > Seed Keywords. Confirm 25 seeds are visible.
3. Switch to Target Keywords tab. Click "Run keyword research".
4. Wait for results (~15-30 seconds).
5. Confirm the table populates with keywords showing volume, difficulty, intent, opportunity scores.
6. Test filtering by intent, status, and difficulty.
7. Test sorting by clicking column headers.
8. Test single status change via dropdown.
9. Test bulk select + approve/dismiss.
10. Refresh page — confirm data persists.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: keyword research Phase 1 — Ahrefs expansion pipeline with UI"
```
