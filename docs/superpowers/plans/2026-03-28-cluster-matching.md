# Cluster-to-Page Matching & Content Generation Context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Match keyword clusters to existing Shopify pages during cluster generation, and inject matched cluster data as context into the SEO content generation pipeline.

**Architecture:** A second LLM call at the end of `generate_clusters()` matches clusters to collections/pages/blog articles. A new helper `_load_cluster_context()` formats matched cluster data for injection into `prompt_context()`. Two new API endpoints support manual match overrides and dropdown data. Frontend cluster cards display match status with an override dropdown.

**Tech Stack:** Python/FastAPI (backend), React/TanStack Query/Zod (frontend), SQLite, structured JSON LLM calls via existing `_call_ai()`

**Spec:** `docs/superpowers/specs/2026-03-28-cluster-matching-and-context-design.md`

---

## File Structure

| File | Role |
|------|------|
| `backend/app/services/keyword_clustering.py` | **Modify** — Add `MATCHING_SCHEMA`, `_match_clusters_to_pages()`, `_load_cluster_context()`, update `generate_clusters()` |
| `backend/app/routers/clusters.py` | **Modify** — Add `PATCH /match` and `GET /match-options` endpoints |
| `shopifyseo/dashboard_ai_engine_parts/context.py` | **Modify** — Include `cluster_seo_context` key in `prompt_context()` output |
| `shopifyseo/dashboard_ai_engine_parts/prompts.py` | **Modify** — Pass `cluster_seo_context` through in `slim_single_field_prompt_context()` for seo_title and seo_description |
| `shopifyseo/dashboard_ai_engine_parts/generation.py` | **Modify** — Call `_load_cluster_context()` and inject into context dict |
| `frontend/src/lib/api.ts` | **Modify** — Add `patchJson()` helper |
| `frontend/src/routes/keywords-page.tsx` | **Modify** — Update cluster schema, add match display row, add override dropdown |
| `tests/test_keyword_clustering.py` | **Modify** — Add tests for `_load_cluster_context()` |

---

### Task 1: `_load_cluster_context()` — Tests and Implementation

**Files:**
- Modify: `tests/test_keyword_clustering.py`
- Modify: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Write failing tests for `_load_cluster_context`**

Add these tests at the bottom of `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import _load_cluster_context


def test_load_cluster_context_match_found():
    """Returns formatted string when a cluster matches the handle/type."""
    clusters_data = {
        "clusters": [
            {
                "name": "Elf Bar Disposable Vapes",
                "content_type": "collection_page",
                "primary_keyword": "elf bar canada",
                "content_brief": "Comprehensive collection page for Elf Bar disposable vapes.",
                "keywords": ["elf bar canada", "elf bar vape", "elf bar review"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": "elf bar canada", "status": "approved", "volume": 1200, "difficulty": 35, "opportunity": 80.0},
            {"keyword": "elf bar vape", "status": "approved", "volume": 800, "difficulty": 25, "opportunity": 70.0},
            {"keyword": "elf bar review", "status": "approved", "volume": 400, "difficulty": 20, "opportunity": 60.0},
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "elf-bar")
    assert result is not None
    assert "Elf Bar Disposable Vapes" in result
    assert "elf bar canada" in result
    assert "1200" in result
    assert "collection_page" in result


def test_load_cluster_context_no_match():
    """Returns None when no cluster matches the handle."""
    clusters_data = {
        "clusters": [
            {
                "name": "Elf Bar Disposable Vapes",
                "content_type": "collection_page",
                "primary_keyword": "elf bar canada",
                "content_brief": "...",
                "keywords": ["elf bar canada"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {"items": []}
    result = _load_cluster_context(clusters_data, target_data, "collection", "disposable-vapes")
    assert result is None


def test_load_cluster_context_null_suggested_match():
    """Gracefully skips clusters with null suggested_match."""
    clusters_data = {
        "clusters": [
            {
                "name": "Some Cluster",
                "content_type": "blog_post",
                "primary_keyword": "vape juice",
                "content_brief": "...",
                "keywords": ["vape juice"],
                "suggested_match": None,
            },
            {
                "name": "Elf Bar",
                "content_type": "collection_page",
                "primary_keyword": "elf bar canada",
                "content_brief": "Elf Bar collection.",
                "keywords": ["elf bar canada"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": "elf bar canada", "status": "approved", "volume": 1200, "difficulty": 35, "opportunity": 80.0},
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "elf-bar")
    assert result is not None
    assert "Elf Bar" in result


def test_load_cluster_context_product_returns_none():
    """Products don't match clusters — always returns None."""
    clusters_data = {
        "clusters": [
            {
                "name": "Elf Bar",
                "content_type": "collection_page",
                "primary_keyword": "elf bar",
                "content_brief": "...",
                "keywords": ["elf bar"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {"items": []}
    result = _load_cluster_context(clusters_data, target_data, "product", "elf-bar-bc10000")
    assert result is None


def test_load_cluster_context_cap_at_three():
    """Caps at 3 clusters even if more match the same page."""
    clusters_data = {
        "clusters": [
            {
                "name": f"Cluster {i}",
                "content_type": "collection_page",
                "primary_keyword": f"kw{i}",
                "content_brief": f"Brief {i}.",
                "keywords": [f"kw{i}"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            }
            for i in range(5)
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": f"kw{i}", "status": "approved", "volume": 100, "difficulty": 10, "opportunity": 50.0}
            for i in range(5)
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "elf-bar")
    assert result is not None
    assert "Cluster 0" in result
    assert "Cluster 1" in result
    assert "Cluster 2" in result
    assert "Cluster 3" not in result


def test_load_cluster_context_keyword_metrics():
    """Includes volume and difficulty from target keywords data."""
    clusters_data = {
        "clusters": [
            {
                "name": "Disposable Vapes",
                "content_type": "collection_page",
                "primary_keyword": "disposable vape canada",
                "content_brief": "All disposable vapes.",
                "keywords": ["disposable vape canada", "cheap disposable vape"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "disposables",
                    "match_title": "Disposables",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": "disposable vape canada", "status": "approved", "volume": 2000, "difficulty": 40, "opportunity": 75.0},
            {"keyword": "cheap disposable vape", "status": "approved", "volume": 500, "difficulty": 15, "opportunity": 85.0},
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "disposables")
    assert result is not None
    assert "2000" in result
    assert "40" in result
    assert "500" in result


def test_load_cluster_context_empty_clusters():
    """Returns None when clusters list is empty."""
    clusters_data = {"clusters": [], "generated_at": None}
    target_data = {"items": []}
    result = _load_cluster_context(clusters_data, target_data, "collection", "elf-bar")
    assert result is None


def test_load_cluster_context_type_mismatch():
    """Returns None when match_type doesn't correspond to object_type."""
    clusters_data = {
        "clusters": [
            {
                "name": "Elf Bar",
                "content_type": "collection_page",
                "primary_keyword": "elf bar",
                "content_brief": "...",
                "keywords": ["elf bar"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "elf-bar",
                    "match_title": "Elf Bar",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {"items": []}
    # Asking for page type but cluster matched to collection
    result = _load_cluster_context(clusters_data, target_data, "page", "elf-bar")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py -v -k "load_cluster_context"`
Expected: FAIL — `ImportError: cannot import name '_load_cluster_context'`

- [ ] **Step 3: Implement `_load_cluster_context`**

Add this function to `backend/app/services/keyword_clustering.py`, after the `load_clusters()` function (after line 123):

```python
def _load_cluster_context(
    clusters_data: dict,
    target_data: dict,
    object_type: str,
    handle: str,
) -> str | None:
    """Format matched cluster keywords as a context string for content generation.

    Takes pre-loaded data dicts (not a db connection) so the function is pure
    and testable. Returns None if no clusters match or object_type is 'product'.
    """
    if object_type == "product":
        return None

    clusters = clusters_data.get("clusters") or []
    if not clusters:
        return None

    # Build keyword metrics lookup from target keywords
    kw_map: dict[str, dict] = {}
    for item in target_data.get("items") or []:
        kw_map[item.get("keyword", "").lower()] = item

    # Find matching clusters
    matched: list[dict] = []
    for cluster in clusters:
        sm = cluster.get("suggested_match")
        if not sm:
            continue
        if sm.get("match_handle") != handle:
            continue
        if sm.get("match_type") != object_type:
            continue
        matched.append(cluster)
        if len(matched) >= 3:
            break

    if not matched:
        return None

    # Format context string
    sections: list[str] = []
    for cluster in matched:
        primary_kw = cluster.get("primary_keyword", "")
        primary_metrics = kw_map.get(primary_kw.lower(), {})
        primary_vol = primary_metrics.get("volume", 0) or 0
        primary_diff = primary_metrics.get("difficulty", 0) or 0

        supporting = []
        for kw in cluster.get("keywords", []):
            if kw.lower() == primary_kw.lower():
                continue
            m = kw_map.get(kw.lower(), {})
            vol = m.get("volume", 0) or 0
            diff = m.get("difficulty", 0) or 0
            supporting.append(f"{kw} (vol: {vol}, diff: {diff})")

        lines = [
            f'SEO Target Keywords (from cluster "{cluster.get("name", "")}"):',
            f'- Primary keyword: "{primary_kw}" (volume: {primary_vol}, difficulty: {primary_diff})',
        ]
        if supporting:
            lines.append(f"- Supporting keywords: {', '.join(supporting)}")
        lines.append(f"- Content angle: {cluster.get('content_brief', '')}")
        lines.append(f"- Recommended content type: {cluster.get('content_type', '')}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py -v -k "load_cluster_context"`
Expected: All 8 new tests PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All 16 tests PASS (8 existing + 8 new)

- [ ] **Step 6: Commit**

```bash
git add tests/test_keyword_clustering.py backend/app/services/keyword_clustering.py && git commit -m "feat: add _load_cluster_context with tests for cluster-to-generation context"
```

---

### Task 2: `_match_clusters_to_pages()` and `generate_clusters()` Integration

**Files:**
- Modify: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Add `MATCHING_SCHEMA` constant**

Add after the existing `CLUSTERING_SCHEMA` constant (after line 160) in `backend/app/services/keyword_clustering.py`:

```python
MATCHING_SCHEMA = {
    "name": "matching_result",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cluster_name": {"type": "string"},
                        "match_type": {"type": "string"},
                        "match_handle": {"type": "string"},
                        "match_title": {"type": "string"},
                    },
                    "required": ["cluster_name", "match_type", "match_handle", "match_title"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["matches"],
        "additionalProperties": False,
    },
}
```

- [ ] **Step 2: Add `_match_clusters_to_pages()` function**

Add after the `MATCHING_SCHEMA` constant:

```python
def _match_clusters_to_pages(
    conn: sqlite3.Connection,
    clusters: list[dict],
    settings: dict,
) -> list[dict]:
    """Match clusters to existing Shopify pages using LLM.

    Returns the clusters list with 'suggested_match' populated.
    On LLM failure, returns clusters with suggested_match = None.
    """
    # 1. Query existing pages
    collections = conn.execute("SELECT handle, title FROM collections ORDER BY title").fetchall()
    pages = conn.execute("SELECT handle, title FROM pages ORDER BY title").fetchall()
    articles = conn.execute(
        "SELECT blog_handle, handle, title FROM blog_articles ORDER BY blog_handle, title"
    ).fetchall()

    # 2. Build available pages list
    available: list[dict] = []
    for row in collections:
        available.append({"type": "collection", "handle": row[0], "title": row[1]})
    for row in pages:
        available.append({"type": "page", "handle": row[0], "title": row[1]})
    for row in articles:
        composite_handle = f"{row[0]}/{row[1]}"
        available.append({"type": "blog_article", "handle": composite_handle, "title": row[2]})

    # 3. If no pages exist, skip matching
    if not available:
        for c in clusters:
            c["suggested_match"] = None
        return clusters

    # 4. Build matching prompt
    cluster_summaries = [
        {
            "name": c["name"],
            "content_type": c.get("content_type", ""),
            "primary_keyword": c.get("primary_keyword", ""),
            "keywords": c.get("keywords", [])[:10],
        }
        for c in clusters
    ]

    system_prompt = (
        "You are an SEO strategist matching keyword clusters to existing website pages.\n\n"
        "For each cluster, pick the best matching page from the available pages list, "
        "or mark it as 'new' if no existing page is a good fit.\n\n"
        "Guidelines:\n"
        "- Prefer matching content_type to page type: collection_page clusters → collections, "
        "blog_post/buying_guide clusters → blog articles, landing_page clusters → pages.\n"
        "- Match based on topical relevance between cluster keywords and page title/handle.\n"
        "- If no existing page covers the cluster's topic well, set match_type to 'new' "
        "with empty match_handle and match_title.\n\n"
        "match_type must be one of: 'collection', 'page', 'blog_article', 'new'.\n"
        "When match_type is 'new', set match_handle and match_title to empty strings."
    )

    user_prompt = (
        "Clusters to match:\n"
        + json.dumps(cluster_summaries, indent=2)
        + "\n\nAvailable pages:\n"
        + json.dumps(available, indent=2)
    )

    # 5. Call LLM
    provider = settings["generation_provider"]
    model = settings["generation_model"]

    try:
        llm_result = _call_ai(
            settings=settings,
            provider=provider,
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            timeout=settings["timeout"],
            json_schema=MATCHING_SCHEMA,
            stage="cluster_matching",
        )
    except Exception:
        logger.exception("Cluster-to-page matching failed; clusters saved without matches")
        for c in clusters:
            c["suggested_match"] = None
        return clusters

    # 6. Apply matches to clusters
    matches_by_name: dict[str, dict] = {}
    for m in llm_result.get("matches", []):
        matches_by_name[m.get("cluster_name", "")] = m

    matched_count = 0
    for c in clusters:
        m = matches_by_name.get(c["name"])
        if m and m.get("match_type") != "new":
            c["suggested_match"] = {
                "match_type": m["match_type"],
                "match_handle": m["match_handle"],
                "match_title": m["match_title"],
            }
            matched_count += 1
        elif m and m.get("match_type") == "new":
            c["suggested_match"] = {
                "match_type": "new",
                "match_handle": "",
                "match_title": "",
            }
        else:
            c["suggested_match"] = None

    return clusters
```

- [ ] **Step 3: Update `generate_clusters()` to call matching**

In `generate_clusters()`, replace the save + done section (lines 238-244) with:

```python
    # 7. Match clusters to existing pages
    progress("Matching clusters to existing pages…")
    try:
        clusters = _match_clusters_to_pages(conn, clusters, settings)
    except Exception:
        logger.exception("Matching step failed; saving clusters without matches")
        for c in clusters:
            if "suggested_match" not in c:
                c["suggested_match"] = None

    matched_count = sum(
        1 for c in clusters
        if c.get("suggested_match") and c["suggested_match"].get("match_type") not in (None, "new")
    )

    # 8. Save
    payload = {
        "clusters": clusters,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    set_service_setting(conn, CLUSTERS_KEY, json.dumps(payload))
    progress(f"Done — {len(clusters)} clusters generated, {matched_count} matched to existing pages")

    return payload
```

- [ ] **Step 4: Run existing tests to verify nothing broke**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All 16 tests PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py && git commit -m "feat: add cluster-to-page matching via LLM in generate_clusters"
```

---

### Task 3: PATCH and GET Endpoints in Router

**Files:**
- Modify: `backend/app/routers/clusters.py`
- Modify: `backend/app/services/keyword_clustering.py`

- [ ] **Step 1: Add `get_match_options()` and `update_cluster_match()` service functions**

Add at the bottom of `backend/app/services/keyword_clustering.py`:

```python
def get_match_options(conn: sqlite3.Connection) -> list[dict]:
    """Return flat list of available pages for the match override dropdown."""
    options: list[dict] = [
        {"match_type": "new", "match_handle": "", "match_title": "New content"},
        {"match_type": "none", "match_handle": "", "match_title": "No match"},
    ]

    collections = conn.execute("SELECT handle, title FROM collections ORDER BY title").fetchall()
    for row in collections:
        options.append({"match_type": "collection", "match_handle": row[0], "match_title": row[1]})

    pages = conn.execute("SELECT handle, title FROM pages ORDER BY title").fetchall()
    for row in pages:
        options.append({"match_type": "page", "match_handle": row[0], "match_title": row[1]})

    articles = conn.execute(
        "SELECT blog_handle, handle, title FROM blog_articles ORDER BY blog_handle, title"
    ).fetchall()
    for row in articles:
        options.append({
            "match_type": "blog_article",
            "match_handle": f"{row[0]}/{row[1]}",
            "match_title": row[2],
        })

    return options


def update_cluster_match(
    conn: sqlite3.Connection,
    cluster_index: int,
    match_type: str,
    match_handle: str,
    match_title: str,
) -> dict:
    """Update suggested_match for a single cluster by index. Returns updated clusters payload."""
    data = load_clusters(conn)
    clusters = data.get("clusters", [])

    if cluster_index < 0 or cluster_index >= len(clusters):
        raise ValueError(f"Invalid cluster_index {cluster_index}. Valid range: 0-{len(clusters) - 1}")

    if match_type == "none":
        clusters[cluster_index]["suggested_match"] = None
    else:
        clusters[cluster_index]["suggested_match"] = {
            "match_type": match_type,
            "match_handle": match_handle,
            "match_title": match_title,
        }

    set_service_setting(conn, CLUSTERS_KEY, json.dumps(data))
    return data
```

- [ ] **Step 2: Add the new endpoints to the router**

Replace the full content of `backend/app/routers/clusters.py` with:

```python
import json
import queue
import threading

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.db import open_db_connection
from backend.app.services.keyword_clustering import (
    generate_clusters,
    get_match_options,
    load_clusters,
    update_cluster_match,
)

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


@router.get("/match-options", response_model=dict)
def get_cluster_match_options():
    """Return available pages for the match override dropdown."""
    conn = open_db_connection()
    try:
        options = get_match_options(conn)
        return {"ok": True, "data": {"options": options}}
    finally:
        conn.close()


class MatchUpdateBody(BaseModel):
    cluster_index: int
    match_type: str
    match_handle: str
    match_title: str


@router.patch("/match", response_model=dict)
def patch_cluster_match(body: MatchUpdateBody):
    """Override the suggested_match for a single cluster."""
    conn = open_db_connection()
    try:
        data = update_cluster_match(
            conn,
            cluster_index=body.cluster_index,
            match_type=body.match_type,
            match_handle=body.match_handle,
            match_title=body.match_title,
        )
        return {"ok": True, "data": data}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    finally:
        conn.close()
```

- [ ] **Step 3: Run existing tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All 16 tests PASS

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/keyword_clustering.py backend/app/routers/clusters.py && git commit -m "feat: add PATCH /match and GET /match-options endpoints for cluster overrides"
```

---

### Task 4: Inject Cluster Context into Content Generation Pipeline

**Files:**
- Modify: `shopifyseo/dashboard_ai_engine_parts/context.py:484-542`
- Modify: `shopifyseo/dashboard_ai_engine_parts/prompts.py:338-418` and `251-308`
- Modify: `shopifyseo/dashboard_ai_engine_parts/generation.py:1028-1063`

- [ ] **Step 1: Add `cluster_seo_context` to `prompt_context()` output**

In `shopifyseo/dashboard_ai_engine_parts/context.py`, the `prompt_context()` function returns a dict at line 519-542. Add `cluster_seo_context` to the returned dict. Find the return statement (line 519) and add the key:

Replace line 536 (before `"catalog_title_examples"`):

```python
        "seo_context": condensed_context(context),
```

with:

```python
        "seo_context": condensed_context(context),
        "cluster_seo_context": context.get("cluster_seo_context"),
```

- [ ] **Step 2: Pass cluster context through `slim_single_field_prompt_context` for seo_title**

In `shopifyseo/dashboard_ai_engine_parts/prompts.py`, in the `slim_single_field_prompt_context()` function, the seo_title return block is at line 411-418. Add the cluster context to it.

Replace the return dict for seo_title (starting at line 411):

```python
    return {
        "object_type": object_type,
        "primary_object": slim_primary,
        "seo_context": slim_seo_context,
        # Accepted titles from same brand/model family — concrete format anchors
        # for the generator and reviewer to match the established catalog pattern.
        "catalog_title_examples": full_context.get("catalog_title_examples") or [],
    }
```

with:

```python
    result = {
        "object_type": object_type,
        "primary_object": slim_primary,
        "seo_context": slim_seo_context,
        # Accepted titles from same brand/model family — concrete format anchors
        # for the generator and reviewer to match the established catalog pattern.
        "catalog_title_examples": full_context.get("catalog_title_examples") or [],
    }
    cluster_ctx = full_context.get("cluster_seo_context")
    if cluster_ctx:
        result["cluster_seo_context"] = cluster_ctx
    return result
```

- [ ] **Step 3: Pass cluster context through `_slim_seo_description_context`**

In `shopifyseo/dashboard_ai_engine_parts/prompts.py`, at the end of `_slim_seo_description_context()` (line 304-308), replace:

```python
    return {
        "object_type": object_type,
        "primary_object": slim_primary,
        "seo_context": slim_seo_context,
    }
```

with:

```python
    result = {
        "object_type": object_type,
        "primary_object": slim_primary,
        "seo_context": slim_seo_context,
    }
    cluster_ctx = full_context.get("cluster_seo_context")
    if cluster_ctx:
        result["cluster_seo_context"] = cluster_ctx
    return result
```

- [ ] **Step 4: Call `_load_cluster_context` in `generate_recommendation()`**

In `shopifyseo/dashboard_ai_engine_parts/generation.py`, add the import at the top of the file (after the existing imports from this package):

```python
from backend.app.services.keyword_clustering import _load_cluster_context
```

`generation.py` already imports `json` at the module level and `get_service_setting` is available via `shopifyseo.dashboard_google`. Check the existing imports — if `get_service_setting` is not already imported, add it to the imports from `shopifyseo.dashboard_google`.

In `generate_recommendation()`, after line 1028 (`context = object_context(conn, object_type, handle)`) and before line 1033 (`signal_narrative_precomputed = None`), add:

```python
    # Load cluster context for matched pages
    try:
        from shopifyseo.dashboard_google import get_service_setting as _get_ss
        clusters_raw = _get_ss(conn, "keyword_clusters", "{}")
        target_raw = _get_ss(conn, "target_keywords", "{}")
        clusters_data = json.loads(clusters_raw) if clusters_raw else {}
        target_data = json.loads(target_raw) if target_raw else {}
        cluster_ctx = _load_cluster_context(clusters_data, target_data, object_type, handle)
        if cluster_ctx:
            context["cluster_seo_context"] = cluster_ctx
    except Exception:
        logger.debug("Failed to load cluster context; proceeding without it")
```

Note: If `get_service_setting` is already imported at the module level, use that directly instead of the local import alias.

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add shopifyseo/dashboard_ai_engine_parts/context.py shopifyseo/dashboard_ai_engine_parts/prompts.py shopifyseo/dashboard_ai_engine_parts/generation.py && git commit -m "feat: inject cluster SEO context into content generation pipeline"
```

---

### Task 5: Frontend — Schema Update and `patchJson` Helper

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/routes/keywords-page.tsx:1-65`

- [ ] **Step 1: Add `patchJson` to `api.ts`**

In `frontend/src/lib/api.ts`, add after the `postJson` function (after line 63):

```typescript
export function patchJson<T extends z.ZodTypeAny>(path: string, schema: T, body?: unknown) {
  return request(path, schema, {
    method: "PATCH",
    body: body ? JSON.stringify(body) : undefined
  });
}
```

- [ ] **Step 2: Update cluster schema with `suggested_match`**

In `frontend/src/routes/keywords-page.tsx`, replace the cluster schemas (lines 50-65):

```typescript
const matchSchema = z.object({
  match_type: z.string(),
  match_handle: z.string(),
  match_title: z.string(),
});

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
  suggested_match: matchSchema.nullable().optional(),
});

const clustersPayloadSchema = z.object({
  clusters: z.array(clusterSchema),
  generated_at: z.string().nullable(),
});
```

- [ ] **Step 3: Add match options schema**

After the `clustersPayloadSchema`, add:

```typescript
const matchOptionSchema = z.object({
  match_type: z.string(),
  match_handle: z.string(),
  match_title: z.string(),
});

const matchOptionsPayloadSchema = z.object({
  options: z.array(matchOptionSchema),
});
```

- [ ] **Step 4: Update import to include `patchJson`**

Replace the import line (line 10):

```typescript
import { getJson, postJson } from "../lib/api";
```

with:

```typescript
import { getJson, patchJson, postJson } from "../lib/api";
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/routes/keywords-page.tsx && git commit -m "feat: add matchSchema to cluster schema and patchJson helper"
```

---

### Task 6: Frontend — Match Display on Cluster Cards

**Files:**
- Modify: `frontend/src/routes/keywords-page.tsx`

This task adds the match display row below the content brief on each cluster card, and the "Change" button that toggles the override dropdown.

- [ ] **Step 1: Add `Link` import**

Add `Link` to the existing React Router imports. Check if `Link` is already imported — if not, add at the top of the file:

```typescript
import { Link } from "react-router-dom";
```

Also add `ChevronDown` to the lucide-react import:

```typescript
import { Check, ChevronDown, LoaderCircle, Plus, Sparkles, Trash2, X } from "lucide-react";
```

- [ ] **Step 2: Add match display state and queries inside ClustersPanel**

Inside the `ClustersPanel` function (after the existing state declarations around line 778), add:

```typescript
  const [editingMatchIndex, setEditingMatchIndex] = useState<number | null>(null);

  const matchOptionsQuery = useQuery({
    queryKey: ["cluster-match-options"],
    queryFn: () => getJson("/api/keywords/clusters/match-options", matchOptionsPayloadSchema),
    enabled: editingMatchIndex !== null,
  });

  const matchMutation = useMutation({
    mutationFn: (vars: { cluster_index: number; match_type: string; match_handle: string; match_title: string }) =>
      patchJson("/api/keywords/clusters", clustersPayloadSchema, vars),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["keyword-clusters"] });
      setEditingMatchIndex(null);
    },
  });
```

Note: The `patchJson` call path should be `/api/keywords/clusters/match` — make sure the path is correct:

```typescript
      patchJson("/api/keywords/clusters/match", clustersPayloadSchema, vars),
```

- [ ] **Step 3: Add match display row to each cluster card**

Inside the cluster card JSX, after the content brief paragraph (after line 954 which has `<p className="text-sm text-slate-500">{cluster.content_brief}</p>`), add the match display row. Find the closing `</div>` of the card header section (line 955 `</div>`) and add inside the card (after the header div closes, before the stats row):

```tsx
              {/* Match display */}
              <div className="flex items-center gap-2 text-sm">
                {cluster.suggested_match ? (
                  cluster.suggested_match.match_type === "new" ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="text-slate-400">→</span>
                      <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                        New content
                      </span>
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1">
                      <span className="text-slate-400">→</span>
                      <Link
                        to={
                          cluster.suggested_match.match_type === "collection"
                            ? `/collections/${cluster.suggested_match.match_handle}`
                            : cluster.suggested_match.match_type === "page"
                            ? `/pages/${cluster.suggested_match.match_handle}`
                            : `/blog/${cluster.suggested_match.match_handle}`
                        }
                        className="text-blue-600 hover:text-blue-800 hover:underline"
                      >
                        {cluster.suggested_match.match_title}
                      </Link>
                      <span className="text-xs text-slate-400">
                        ({cluster.suggested_match.match_type === "blog_article"
                          ? "Blog Article"
                          : cluster.suggested_match.match_type === "collection"
                          ? "Collection"
                          : "Page"})
                      </span>
                    </span>
                  )
                ) : (
                  <span className="text-slate-400">→ No match suggested</span>
                )}
                <button
                  type="button"
                  onClick={() =>
                    setEditingMatchIndex(
                      editingMatchIndex === clusters.indexOf(cluster)
                        ? null
                        : clusters.indexOf(cluster)
                    )
                  }
                  className="text-xs font-medium text-blue-600 hover:text-blue-800"
                >
                  Change
                </button>
              </div>

              {/* Match override dropdown */}
              {editingMatchIndex === clusters.indexOf(cluster) && (
                <div className="rounded-lg border border-line bg-[#f7f9fc] p-3 max-h-60 overflow-y-auto">
                  {matchOptionsQuery.isLoading ? (
                    <p className="text-xs text-slate-400">Loading options…</p>
                  ) : matchOptionsQuery.data?.options ? (
                    <div className="space-y-1">
                      {["new", "none", "collection", "page", "blog_article"].map((type) => {
                        const group = matchOptionsQuery.data!.options.filter(
                          (o) => o.match_type === type
                        );
                        if (group.length === 0) return null;
                        const groupLabel =
                          type === "new"
                            ? null
                            : type === "none"
                            ? null
                            : type === "collection"
                            ? "Collections"
                            : type === "page"
                            ? "Pages"
                            : "Blog Articles";
                        return (
                          <div key={type}>
                            {groupLabel && (
                              <p className="text-xs font-semibold text-slate-500 mt-2 mb-1 px-2">
                                {groupLabel}
                              </p>
                            )}
                            {group.map((option) => (
                              <button
                                key={`${option.match_type}-${option.match_handle}`}
                                type="button"
                                disabled={matchMutation.isPending}
                                onClick={() =>
                                  matchMutation.mutate({
                                    cluster_index: clusters.indexOf(cluster),
                                    match_type: option.match_type,
                                    match_handle: option.match_handle,
                                    match_title: option.match_title,
                                  })
                                }
                                className="block w-full text-left rounded px-2 py-1 text-sm hover:bg-blue-50 disabled:opacity-50"
                              >
                                {option.match_title}
                                {option.match_type !== "new" && option.match_type !== "none" && (
                                  <span className="ml-1 text-xs text-slate-400">
                                    ({option.match_handle})
                                  </span>
                                )}
                              </button>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400">No options available</p>
                  )}
                </div>
              )}
```

- [ ] **Step 4: Verify the frontend compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/keywords-page.tsx && git commit -m "feat: add match display and override dropdown to cluster cards"
```

---

### Task 7: Final Integration Test and Progress Messages

**Files:**
- Verify: `backend/app/services/keyword_clustering.py` (progress messages)
- Verify: `frontend/src/routes/keywords-page.tsx` (SSE done message parsing)

- [ ] **Step 1: Verify progress message in `generate_clusters` includes match count**

Check that the final progress message in `generate_clusters()` now reads:
`f"Done — {len(clusters)} clusters generated, {matched_count} matched to existing pages"`

This was already done in Task 2. Verify by reading the file.

- [ ] **Step 2: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Run frontend type check**

Run: `cd frontend && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit if any remaining changes**

```bash
git status
```

If there are uncommitted changes, commit them with an appropriate message.
