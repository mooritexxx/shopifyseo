# Keyword Clustering & Content Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group approved target keywords into topic clusters using Ahrefs parent_topic + LLM refinement, displayed as content-ready cards in a new Clusters tab.

**Architecture:** Two-pass hybrid clustering — first group by `parent_topic` field, then send to the user's configured LLM for refinement (merge similar groups, assign orphans, generate content briefs). Backend service in a new file, new router for endpoints, new frontend tab with card layout.

**Tech Stack:** Python/FastAPI (backend), React/TypeScript/TanStack Query (frontend), existing AI engine (`_call_ai` from `shopifyseo/dashboard_ai_engine_parts/generation.py`), Zod validation.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `backend/app/services/keyword_clustering.py` | **Create** — Pure clustering logic: group by parent_topic, build LLM prompt, compute stats, orchestrate generation |
| `backend/app/routers/clusters.py` | **Create** — GET /api/keywords/clusters, POST /api/keywords/clusters/generate (SSE) |
| `backend/app/main.py` | **Modify** — Register clusters router |
| `frontend/src/routes/keywords-page.tsx` | **Modify** — Add Clusters tab + ClustersPanel component |
| `tests/test_keyword_clustering.py` | **Create** — Tests for pure functions (grouping, stats, prompt building) |

---

### Task 1: Pure Functions — Group by Parent Topic

**Files:**
- Create: `tests/test_keyword_clustering.py`
- Create: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Write failing tests for `_group_by_parent_topic`**

Create `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import _group_by_parent_topic


def test_group_by_parent_topic_basic():
    keywords = [
        {"keyword": "elf bar canada", "parent_topic": "elf bar", "volume": 100},
        {"keyword": "elf bar review", "parent_topic": "elf bar", "volume": 80},
        {"keyword": "vape juice canada", "parent_topic": "vape juice", "volume": 200},
        {"keyword": "best disposable vape", "parent_topic": None, "volume": 150},
    ]
    groups, orphans = _group_by_parent_topic(keywords)
    assert len(groups) == 2
    assert len(groups["elf bar"]) == 2
    assert len(groups["vape juice"]) == 1
    assert len(orphans) == 1
    assert orphans[0]["keyword"] == "best disposable vape"


def test_group_by_parent_topic_empty_string_is_orphan():
    keywords = [
        {"keyword": "random kw", "parent_topic": "", "volume": 50},
    ]
    groups, orphans = _group_by_parent_topic(keywords)
    assert len(groups) == 0
    assert len(orphans) == 1


def test_group_by_parent_topic_empty_input():
    groups, orphans = _group_by_parent_topic([])
    assert groups == {}
    assert orphans == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `_group_by_parent_topic`**

Create `backend/app/services/keyword_clustering.py`:

```python
import json
import logging
import sqlite3
from datetime import datetime, timezone

from shopifyseo.dashboard_google import get_service_setting, set_service_setting

logger = logging.getLogger(__name__)

CLUSTERS_KEY = "keyword_clusters"
TARGET_KEY = "target_keywords"


def _group_by_parent_topic(
    keywords: list[dict],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Group keywords by parent_topic. Null/empty parent_topic → orphans."""
    groups: dict[str, list[dict]] = {}
    orphans: list[dict] = []
    for kw in keywords:
        topic = kw.get("parent_topic") or ""
        if not topic.strip():
            orphans.append(kw)
        else:
            groups.setdefault(topic.strip(), []).append(kw)
    return groups, orphans
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_keyword_clustering.py backend/app/services/keyword_clustering.py
git commit -m "feat: add _group_by_parent_topic for keyword clustering"
```

---

### Task 2: Pure Functions — Compute Cluster Stats

**Files:**
- Modify: `tests/test_keyword_clustering.py`
- Modify: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Write failing tests for `_compute_cluster_stats`**

Add to `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import _compute_cluster_stats


def test_compute_cluster_stats_basic():
    all_keywords_map = {
        "elf bar canada": {"volume": 100, "difficulty": 20, "opportunity": 80.0},
        "elf bar review": {"volume": 80, "difficulty": 30, "opportunity": 60.0},
        "elf bar vape": {"volume": 50, "difficulty": 10, "opportunity": 90.0},
    }
    stats = _compute_cluster_stats(
        ["elf bar canada", "elf bar review", "elf bar vape"], all_keywords_map
    )
    assert stats["keyword_count"] == 3
    assert stats["total_volume"] == 230
    assert stats["avg_difficulty"] == 20.0
    assert stats["avg_opportunity"] == 76.7


def test_compute_cluster_stats_missing_keyword():
    """Keywords not found in the map are silently skipped."""
    all_keywords_map = {
        "elf bar canada": {"volume": 100, "difficulty": 20, "opportunity": 80.0},
    }
    stats = _compute_cluster_stats(
        ["elf bar canada", "nonexistent keyword"], all_keywords_map
    )
    assert stats["keyword_count"] == 1
    assert stats["total_volume"] == 100


def test_compute_cluster_stats_empty():
    stats = _compute_cluster_stats([], {})
    assert stats["keyword_count"] == 0
    assert stats["total_volume"] == 0
    assert stats["avg_difficulty"] == 0.0
    assert stats["avg_opportunity"] == 0.0
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_compute_cluster_stats_basic -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_compute_cluster_stats`**

Add to `backend/app/services/keyword_clustering.py`:

```python
def _compute_cluster_stats(
    cluster_keywords: list[str], all_keywords_map: dict[str, dict]
) -> dict:
    """Compute aggregate stats for a cluster from keyword metrics."""
    found = [
        all_keywords_map[kw]
        for kw in cluster_keywords
        if kw in all_keywords_map
    ]
    count = len(found)
    if count == 0:
        return {
            "keyword_count": 0,
            "total_volume": 0,
            "avg_difficulty": 0.0,
            "avg_opportunity": 0.0,
        }
    total_volume = sum(item.get("volume", 0) or 0 for item in found)
    avg_difficulty = round(
        sum(item.get("difficulty", 0) or 0 for item in found) / count, 1
    )
    avg_opportunity = round(
        sum(item.get("opportunity", 0.0) or 0.0 for item in found) / count, 1
    )
    return {
        "keyword_count": count,
        "total_volume": total_volume,
        "avg_difficulty": avg_difficulty,
        "avg_opportunity": avg_opportunity,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_keyword_clustering.py backend/app/services/keyword_clustering.py
git commit -m "feat: add _compute_cluster_stats for keyword clustering"
```

---

### Task 3: Pure Functions — Build Clustering Prompt

**Files:**
- Modify: `tests/test_keyword_clustering.py`
- Modify: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Write failing tests for `_build_clustering_prompt`**

Add to `tests/test_keyword_clustering.py`:

```python
import json as _json

from backend.app.services.keyword_clustering import _build_clustering_prompt


def test_build_clustering_prompt_returns_system_and_user():
    groups = {
        "elf bar": [
            {"keyword": "elf bar canada", "volume": 100, "difficulty": 20,
             "opportunity": 80.0, "intent": "commercial", "content_type": "Comparison / Buying guide",
             "parent_topic": "elf bar", "ranking_status": "not_ranking"},
        ],
    }
    orphans = [
        {"keyword": "best disposable vape", "volume": 150, "difficulty": 30,
         "opportunity": 70.0, "intent": "commercial", "content_type": "Comparison / Buying guide",
         "parent_topic": None, "ranking_status": "quick_win"},
    ]
    system_prompt, user_prompt = _build_clustering_prompt(groups, orphans)
    assert "SEO" in system_prompt
    assert "cluster" in system_prompt.lower()
    # User prompt should contain the keyword data as JSON
    assert "elf bar canada" in user_prompt
    assert "best disposable vape" in user_prompt


def test_build_clustering_prompt_no_orphans():
    groups = {
        "topic a": [{"keyword": "kw1", "volume": 10, "difficulty": 5,
                      "opportunity": 50.0, "intent": "informational",
                      "content_type": "Blog / Guide", "parent_topic": "topic a",
                      "ranking_status": None}],
    }
    system_prompt, user_prompt = _build_clustering_prompt(groups, [])
    assert "kw1" in user_prompt
    assert len(system_prompt) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_build_clustering_prompt_returns_system_and_user -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `_build_clustering_prompt`**

Add to `backend/app/services/keyword_clustering.py`:

```python
def _build_clustering_prompt(
    groups: dict[str, list[dict]], orphans: list[dict]
) -> tuple[str, str]:
    """Build system and user prompts for LLM clustering refinement."""
    system_prompt = (
        "You are an SEO content strategist for a Canadian online vape store. "
        "You will receive keyword data organized into preliminary groups (by Ahrefs parent topic) "
        "plus a list of ungrouped orphan keywords.\n\n"
        "Your job:\n"
        "1. Assign every orphan keyword to an existing group OR create a new group for it.\n"
        "2. Merge groups that are too similar — they should share one page on the website.\n"
        "3. Each cluster should have 2+ keywords and be focused enough for one page to rank for all of them.\n"
        "4. For each final cluster, provide:\n"
        "   - name: A clear descriptive label (e.g. 'Elf Bar Disposable Vapes')\n"
        "   - content_type: One of 'collection_page', 'product_page', 'blog_post', 'buying_guide', 'landing_page'\n"
        "   - primary_keyword: The single keyword with the highest search opportunity in the cluster\n"
        "   - content_brief: 1-2 sentences describing what the page should cover and its target intent\n"
        "   - keywords: Array of all keyword strings in the cluster\n\n"
        "Return ONLY the structured JSON. Do not include any keywords that were not provided in the input."
    )

    def _kw_fields(kw: dict) -> dict:
        return {
            "keyword": kw.get("keyword", ""),
            "volume": kw.get("volume", 0),
            "difficulty": kw.get("difficulty", 0),
            "opportunity": kw.get("opportunity", 0.0),
            "intent": kw.get("intent", ""),
            "content_type": kw.get("content_type", ""),
            "ranking_status": kw.get("ranking_status"),
        }

    payload = {
        "groups": {
            topic: [_kw_fields(kw) for kw in kws]
            for topic, kws in groups.items()
        },
        "orphans": [_kw_fields(kw) for kw in orphans],
    }

    user_prompt = (
        "Here are the keyword groups and orphans to cluster:\n\n"
        + json.dumps(payload, indent=2)
    )
    return system_prompt, user_prompt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add tests/test_keyword_clustering.py backend/app/services/keyword_clustering.py
git commit -m "feat: add _build_clustering_prompt for LLM refinement"
```

---

### Task 4: Orchestration — generate_clusters and load_clusters

**Files:**
- Modify: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Implement `load_clusters`**

Add to `backend/app/services/keyword_clustering.py`:

```python
def load_clusters(conn: sqlite3.Connection) -> dict:
    """Load saved clusters from service_settings."""
    raw = get_service_setting(conn, CLUSTERS_KEY, "{}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    if not data.get("clusters"):
        return {"clusters": [], "generated_at": None}
    return data
```

- [ ] **Step 2: Implement the LLM output schema constant**

Add to `backend/app/services/keyword_clustering.py`:

```python
CLUSTERING_SCHEMA = {
    "name": "clustering_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "clusters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "content_type": {"type": "string"},
                        "primary_keyword": {"type": "string"},
                        "content_brief": {"type": "string"},
                        "keywords": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "name",
                        "content_type",
                        "primary_keyword",
                        "content_brief",
                        "keywords",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["clusters"],
        "additionalProperties": False,
    },
}
```

- [ ] **Step 3: Implement `generate_clusters`**

Add to `backend/app/services/keyword_clustering.py`:

```python
from typing import Callable

from shopifyseo.dashboard_ai_engine_parts.generation import (
    _call_ai,
    _require_provider_credentials,
    ai_settings,
)


def generate_clusters(
    conn: sqlite3.Connection,
    on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Generate keyword clusters from approved target keywords using LLM."""

    def progress(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    # 1. Load approved target keywords
    progress("Loading approved keywords…")
    raw = get_service_setting(conn, TARGET_KEY, "{}")
    try:
        target_data = json.loads(raw)
    except json.JSONDecodeError:
        target_data = {}

    all_items = target_data.get("items", [])
    approved = [item for item in all_items if item.get("status") == "approved"]

    if not approved:
        raise RuntimeError("No approved keywords to cluster. Approve target keywords first.")

    # 2. Validate AI settings
    settings = ai_settings(conn)
    provider = settings["generation_provider"]
    model = settings["generation_model"]
    _require_provider_credentials(settings, provider)

    # 3. Group by parent_topic
    groups, orphans = _group_by_parent_topic(approved)
    progress(
        f"Grouped by parent topic — {len(groups)} groups, {len(orphans)} orphans"
    )

    # 4. Build prompt and call LLM
    progress(f"Refining clusters with AI ({provider}/{model})…")
    system_prompt, user_prompt = _build_clustering_prompt(groups, orphans)

    llm_result = _call_ai(
        settings=settings,
        provider=provider,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        timeout=settings["timeout"],
        json_schema=CLUSTERING_SCHEMA,
        stage="clustering",
    )

    # 5. Compute stats per cluster
    keywords_map = {item["keyword"].lower(): item for item in approved}
    clusters = []
    for raw_cluster in llm_result.get("clusters", []):
        kw_list = [k for k in raw_cluster.get("keywords", []) if k.lower() in keywords_map]
        if not kw_list:
            continue
        stats = _compute_cluster_stats(
            [k.lower() for k in kw_list], keywords_map
        )
        clusters.append({
            "name": raw_cluster.get("name", "Unnamed Cluster"),
            "content_type": raw_cluster.get("content_type", "blog_post"),
            "primary_keyword": raw_cluster.get("primary_keyword", kw_list[0]),
            "content_brief": raw_cluster.get("content_brief", ""),
            "keywords": kw_list,
            **stats,
        })

    # 6. Sort by total opportunity descending
    clusters.sort(key=lambda c: c.get("avg_opportunity", 0), reverse=True)

    # 7. Save
    payload = {
        "clusters": clusters,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    set_service_setting(conn, CLUSTERS_KEY, json.dumps(payload))
    progress(f"Done — {len(clusters)} clusters generated")

    return payload
```

- [ ] **Step 4: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: 8 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py
git commit -m "feat: add generate_clusters and load_clusters orchestration"
```

---

### Task 5: Backend Router — Clusters Endpoints

**Files:**
- Create: `backend/app/routers/clusters.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Create the clusters router**

Create `backend/app/routers/clusters.py`:

```python
import json
import queue
import threading

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from backend.app.db import open_db_connection
from backend.app.services.keyword_clustering import generate_clusters, load_clusters

router = APIRouter(prefix="/api/keywords/clusters", tags=["clusters"])


@router.get("", response_model=dict)
def get_clusters():
    conn = open_db_connection()
    try:
        data = load_clusters(conn)
        return {"ok": True, "data": data}
    finally:
        conn.close()


@router.post("/generate")
def generate_keyword_clusters():
    """Stream clustering progress via SSE, then emit the final result."""
    q: queue.Queue[str | None] = queue.Queue()

    def on_progress(msg: str) -> None:
        q.put(msg)

    result_holder: dict = {}
    error_holder: list[str] = []

    def worker() -> None:
        conn = open_db_connection()
        try:
            data = generate_clusters(conn, on_progress=on_progress)
            result_holder["data"] = data
        except RuntimeError as exc:
            error_holder.append(str(exc))
        finally:
            conn.close()
            q.put(None)  # sentinel

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    def event_stream():
        while True:
            msg = q.get()
            if msg is None:
                break
            yield f"event: progress\ndata: {json.dumps({'message': msg})}\n\n"
        if error_holder:
            yield f"event: error\ndata: {json.dumps({'detail': error_holder[0]})}\n\n"
        elif "data" in result_holder:
            yield f"event: done\ndata: {json.dumps({'ok': True, 'data': result_holder['data']})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

- [ ] **Step 2: Register the router in main.py**

In `backend/app/main.py`, add the import after the keywords import (line 16):

```python
from backend.app.routers.clusters import router as clusters_router
```

And add the registration after `keywords_router` (after line 30):

```python
app.include_router(clusters_router)
```

- [ ] **Step 3: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/clusters.py backend/app/main.py
git commit -m "feat: add GET and POST /api/keywords/clusters endpoints"
```

---

### Task 6: Frontend — Add Clusters Tab and Schema

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

- [ ] **Step 1: Add the cluster Zod schema and types**

Near the top of `keywords-page.tsx`, after the existing `targetKeywordSchema`, add:

```typescript
const clusterSchema = z.object({
  name: z.string(),
  content_type: z.string(),
  primary_keyword: z.string(),
  content_brief: z.string(),
  keywords: z.array(z.string()),
  total_volume: z.number(),
  avg_difficulty: z.number(),
  avg_opportunity: z.number(),
  keyword_count: z.number(),
});

const clustersDataSchema = z.object({
  clusters: z.array(clusterSchema),
  generated_at: z.string().nullable(),
});

const clustersPayloadSchema = z.object({
  ok: z.boolean(),
  data: clustersDataSchema,
});
```

- [ ] **Step 2: Add the Clusters tab to the tabs array**

Update the `tabs` constant to add the 4th tab. Change the grid from `md:grid-cols-3` to `md:grid-cols-4`:

```typescript
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
  },
  {
    id: "clusters",
    label: "Clusters",
    description: "Keywords grouped into topic clusters for content planning."
  },
] as const;

type TabId = (typeof tabs)[number]["id"];
```

And update the grid class in the tab rendering:

```typescript
<div className="grid gap-2 md:grid-cols-4">
```

- [ ] **Step 3: Add the conditional rendering for ClustersPanel**

In the tab rendering section, add after the target tab:

```typescript
{activeTab === "clusters" && <ClustersPanel />}
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx
git commit -m "feat: add Clusters tab shell and schema to keywords page"
```

---

### Task 7: Frontend — ClustersPanel Component

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

- [ ] **Step 1: Add the content type badge colors constant**

Add near the other badge constants (like `RANKING_COLORS`):

```typescript
const CONTENT_TYPE_COLORS: Record<string, string> = {
  collection_page: "bg-purple-100 text-purple-700",
  product_page: "bg-blue-100 text-blue-700",
  blog_post: "bg-green-100 text-green-700",
  buying_guide: "bg-yellow-100 text-yellow-700",
  landing_page: "bg-indigo-100 text-indigo-700",
};

const CONTENT_TYPE_LABELS: Record<string, string> = {
  collection_page: "Collection Page",
  product_page: "Product Page",
  blog_post: "Blog Post",
  buying_guide: "Buying Guide",
  landing_page: "Landing Page",
};
```

- [ ] **Step 2: Implement ClustersPanel**

Add the `ClustersPanel` component:

```typescript
function ClustersPanel() {
  const queryClient = useQueryClient();

  const clustersQuery = useQuery({
    queryKey: ["keyword-clusters"],
    queryFn: () => getJson("/api/keywords/clusters", clustersPayloadSchema),
  });

  const [genStatus, setGenStatus] = useState<"idle" | "running" | "error">("idle");
  const [genProgress, setGenProgress] = useState("");
  const [genError, setGenError] = useState("");
  const [expandedClusters, setExpandedClusters] = useState<Set<string>>(new Set());

  function toggleExpanded(name: string) {
    setExpandedClusters((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function runClustering() {
    setGenStatus("running");
    setGenProgress("Starting clustering…");
    setGenError("");

    fetch("/api/keywords/clusters/generate", { method: "POST" })
      .then((res) => {
        const reader = res.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        function read(): Promise<void> {
          if (!reader) return Promise.resolve();
          return reader.read().then(({ done, value }) => {
            if (done) {
              setGenStatus("idle");
              setGenProgress("");
              return;
            }
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop() ?? "";
            let eventType = "";
            for (const line of lines) {
              if (line.startsWith("event: ")) {
                eventType = line.slice(7);
              } else if (line.startsWith("data: ")) {
                const data = JSON.parse(line.slice(6));
                if (eventType === "progress") {
                  setGenProgress(data.message);
                } else if (eventType === "done") {
                  setGenStatus("idle");
                  setGenProgress("");
                  queryClient.invalidateQueries({ queryKey: ["keyword-clusters"] });
                } else if (eventType === "error") {
                  setGenStatus("error");
                  setGenError(data.detail);
                  setGenProgress("");
                }
              }
            }
            return read();
          });
        }

        read();
      })
      .catch(() => {
        setGenStatus("error");
        setGenError("Network error — please try again.");
        setGenProgress("");
      });
  }

  const data = clustersQuery.data?.data;
  const clusters = data?.clusters ?? [];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={genStatus === "running"}
            onClick={runClustering}
            className="inline-flex items-center gap-2 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white hover:bg-ink/90 disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            {genStatus === "running" ? "Generating…" : "Generate Clusters"}
          </button>
          {data?.generated_at && (
            <span className="text-xs text-slate-400">
              Last generated: {new Date(data.generated_at).toLocaleString()}
            </span>
          )}
        </div>
        {clusters.length > 0 && (
          <span className="text-sm text-slate-500">{clusters.length} clusters</span>
        )}
      </div>

      {/* Progress banner */}
      {genStatus === "running" && genProgress && (
        <div className="rounded-lg bg-blue-50 px-4 py-3 text-sm text-blue-700">
          {genProgress}
        </div>
      )}

      {/* Error banner */}
      {genStatus === "error" && genError && (
        <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
          {genError}
        </div>
      )}

      {/* Empty state */}
      {clusters.length === 0 && genStatus !== "running" && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
          <p className="text-sm text-slate-500">
            No clusters yet. Approve target keywords, then click &quot;Generate Clusters&quot; to
            group them into content topics.
          </p>
        </div>
      )}

      {/* Cluster cards */}
      <div className="grid gap-4">
        {clusters.map((cluster) => {
          const isExpanded = expandedClusters.has(cluster.name);
          const contentColor =
            CONTENT_TYPE_COLORS[cluster.content_type] ?? "bg-slate-100 text-slate-600";
          const contentLabel =
            CONTENT_TYPE_LABELS[cluster.content_type] ?? cluster.content_type;

          return (
            <div
              key={cluster.name}
              className="rounded-xl border border-line bg-white p-5 space-y-3"
            >
              {/* Card header */}
              <div className="flex items-start justify-between">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-base font-semibold text-ink">{cluster.name}</h3>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${contentColor}`}
                    >
                      {contentLabel}
                    </span>
                  </div>
                  <p className="text-sm font-medium text-slate-700">
                    Primary: {cluster.primary_keyword}
                  </p>
                  <p className="text-sm text-slate-500">{cluster.content_brief}</p>
                </div>
              </div>

              {/* Stats row */}
              <div className="flex gap-6 text-xs text-slate-500">
                <span>
                  <span className="font-medium text-ink">{cluster.keyword_count}</span> keywords
                </span>
                <span>
                  <span className="font-medium text-ink">
                    {cluster.total_volume.toLocaleString()}
                  </span>{" "}
                  total volume
                </span>
                <span>
                  Avg difficulty:{" "}
                  <span className="font-medium text-ink">{cluster.avg_difficulty}</span>
                </span>
                <span>
                  Avg opportunity:{" "}
                  <span className="font-medium text-ink">{cluster.avg_opportunity}</span>
                </span>
              </div>

              {/* Expandable keyword list */}
              <button
                type="button"
                onClick={() => toggleExpanded(cluster.name)}
                className="text-xs font-medium text-blue-600 hover:text-blue-800"
              >
                {isExpanded ? "Hide keywords ▲" : `Show ${cluster.keyword_count} keywords ▼`}
              </button>

              {isExpanded && (
                <div className="rounded-lg border border-line bg-[#f7f9fc] overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line text-left text-xs text-slate-500">
                        <th className="px-3 py-2">Keyword</th>
                        <th className="px-3 py-2 text-right">Volume</th>
                        <th className="px-3 py-2 text-right">Difficulty</th>
                        <th className="px-3 py-2 text-right">Opportunity</th>
                        <th className="px-3 py-2">Ranking</th>
                      </tr>
                    </thead>
                    <tbody>
                      {cluster.keywords.map((kw) => (
                        <tr key={kw} className="border-b border-line last:border-0">
                          <td className="px-3 py-2 font-medium text-ink">{kw}</td>
                          <td className="px-3 py-2 text-right text-slate-600">—</td>
                          <td className="px-3 py-2 text-right text-slate-600">—</td>
                          <td className="px-3 py-2 text-right text-slate-600">—</td>
                          <td className="px-3 py-2">—</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

> **Note:** The keyword detail table shows "—" for now because clusters only store keyword strings. To show volume/difficulty/opportunity/ranking per keyword, we'll enrich the cluster data in the next task.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx
git commit -m "feat: add ClustersPanel with card layout, SSE generation, and expandable keyword lists"
```

---

### Task 8: Frontend — Enrich Keyword Details in Cluster Cards

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

The cluster cards need to show per-keyword metrics (volume, difficulty, opportunity, ranking) in the expanded table. The target keywords query already has this data — we just need to look it up.

- [ ] **Step 1: Update ClustersPanel to cross-reference target keywords**

Inside the `ClustersPanel` component, add a query for target keywords and build a lookup map:

```typescript
// Add inside ClustersPanel, after the clustersQuery
const targetQuery = useQuery({
  queryKey: ["target-keywords"],
  queryFn: () => getJson("/api/keywords/target", targetPayloadSchema),
});

const keywordMap = useMemo(() => {
  const items = targetQuery.data?.data?.items ?? [];
  const map = new Map<string, z.infer<typeof targetKeywordSchema>>();
  for (const item of items) {
    map.set(item.keyword.toLowerCase(), item);
  }
  return map;
}, [targetQuery.data]);
```

Add the `useMemo` import if not already present at the top of the file.

- [ ] **Step 2: Update the keyword table rows to use the lookup**

Replace the `<tbody>` section in the expanded keyword table:

```typescript
<tbody>
  {cluster.keywords.map((kw) => {
    const detail = keywordMap.get(kw.toLowerCase());
    return (
      <tr key={kw} className="border-b border-line last:border-0">
        <td className="px-3 py-2 font-medium text-ink">{kw}</td>
        <td className="px-3 py-2 text-right text-slate-600">
          {detail?.volume?.toLocaleString() ?? "—"}
        </td>
        <td className="px-3 py-2 text-right text-slate-600">
          {detail?.difficulty ?? "—"}
        </td>
        <td className="px-3 py-2 text-right text-slate-600">
          {detail?.opportunity != null
            ? detail.opportunity.toFixed(1)
            : "—"}
        </td>
        <td className="px-3 py-2">
          <RankingBadge status={detail?.ranking_status} />
        </td>
      </tr>
    );
  })}
</tbody>
```

- [ ] **Step 3: Verify frontend builds**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no TypeScript errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx
git commit -m "feat: enrich cluster keyword table with volume, difficulty, opportunity, ranking from target data"
```

---

### Task 9: Integration Test — End-to-End Manual Verification

- [ ] **Step 1: Run all unit tests**

Run: `python -m pytest tests/ -v`
Expected: All tests pass (existing keyword_research tests + new keyword_clustering tests)

- [ ] **Step 2: Start the app and test manually**

1. Start backend: `python -m backend.app.main` (or however the dev server runs)
2. Open the Keywords page in the browser
3. Verify the 4th tab "Clusters" appears
4. Click on Clusters tab — should show empty state
5. Click "Generate Clusters" button
6. Verify progress banner shows SSE messages
7. Verify cluster cards appear after generation completes
8. Verify each card shows: name, content type badge, primary keyword, content brief, stats
9. Expand a cluster — verify keyword table shows volume, difficulty, opportunity, ranking badge
10. Refresh page — verify clusters persist (loaded from service_settings)

- [ ] **Step 3: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix: address any issues found during integration testing"
```
