# Article Idea Lifecycle — Approve, Draft, Track Performance

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a full lifecycle for article ideas — approve → create draft in Shopify → track GSC performance — so every published article traces back to its originating idea.

**Architecture:** 5 sequential tasks touching the full stack. The `article_ideas` table gets 3 new columns (`linked_article_handle`, `linked_blog_handle`, `shopify_article_id`) and a real status machine (`idea → approved → drafting → published`). The existing `generate-draft-stream` endpoint gains an optional `idea_id` field and writes back the article link after creation. `fetch_article_ideas()` gains a LEFT JOIN on `blog_articles` to surface live GSC performance. The frontend gets an Approve button, status-aware card rendering, and a performance panel.

**Tech Stack:** Python/SQLite (`shopifyseo/`), FastAPI/Pydantic (`backend/`), React/TypeScript/Zod (`frontend/`)

**Test runner:** `PYTHONPATH=. /opt/anaconda3/bin/pytest <test_file> -v`

**Frontend build:** `cd frontend && npm run rebuild`

---

## Files Changed

| File | Change |
|------|--------|
| `shopifyseo/dashboard_store.py` | Add 3 new columns to `article_ideas` via `_ensure_columns` |
| `shopifyseo/dashboard_queries.py` | Add `update_article_idea_status`, `link_idea_to_article`; update `fetch_article_ideas` with performance JOIN; extend `queued_keywords` to all active statuses |
| `backend/app/routers/article_ideas.py` | Add `PATCH /{id}/approve` and `PATCH /{id}/status` endpoints |
| `backend/app/routers/blogs.py` | Modify `_run_generate_article_draft` to write back idea link when `idea_id` provided |
| `backend/app/schemas/article_ideas.py` | Add 5 new fields to `ArticleIdeaItem` |
| `backend/app/schemas/blog.py` | Add `idea_id: int \| None` to `ArticleGenerateDraftRequest` |
| `frontend/src/types/api.ts` | Add 5 new fields to `articleIdeaSchema` |
| `frontend/src/lib/run-article-draft-stream.ts` | Add `idea_id?: number` to `ArticleDraftStreamPayload` |
| `frontend/src/routes/article-ideas-page.tsx` | Approve button, status tabs, performance panel, pass `idea_id` to draft modal |
| `tests/test_article_idea_lifecycle.py` | New: tests for update, link, performance JOIN |

---

## Task 1: DB Schema — Add 3 New Columns + Status Machine

**Files:**
- Modify: `shopifyseo/dashboard_store.py` (around line 383, after the existing `_ensure_columns` block for `article_ideas`)
- Test: `tests/test_article_idea_lifecycle.py`

The `article_ideas` table needs three columns to track which article an idea produced. Status already exists (`TEXT DEFAULT 'idea'`) but only `'idea'` and `'rejected'` are used. No schema change needed for status — just extend the application layer.

New columns:
- `linked_article_handle` — handle of the blog article in `blog_articles` (e.g. `'best-disposable-vapes-canada'`)
- `linked_blog_handle` — which blog it belongs to (e.g. `'news'`)
- `shopify_article_id` — Shopify GID (e.g. `'gid://shopify/OnlineStoreArticle/123456'`) for deep-linking

### Step 1: Write failing test

Create `tests/test_article_idea_lifecycle.py`:

```python
"""Tests for the article idea lifecycle: approve, link to article, performance tracking."""
import sqlite3

import pytest

from shopifyseo.dashboard_queries import (
    delete_article_idea,
    fetch_article_ideas,
    link_idea_to_article,
    save_article_ideas,
    update_article_idea_status,
)
from shopifyseo.dashboard_store import ensure_dashboard_schema


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    ensure_dashboard_schema(c)
    return c


@pytest.fixture
def saved_idea_id(conn: sqlite3.Connection) -> int:
    ids = save_article_ideas(
        conn,
        [
            {
                "suggested_title": "Best Disposable Vapes Canada 2025",
                "brief": "A buying guide for Canadian vapers.",
                "primary_keyword": "best disposable vapes canada",
                "supporting_keywords": [],
                "search_intent": "commercial",
                "content_format": "buying_guide",
                "estimated_monthly_traffic": 60,
                "linked_cluster_id": None,
                "linked_cluster_name": "",
                "linked_collection_handle": "disposable-vapes",
                "linked_collection_title": "Disposable Vapes",
                "source_type": "cluster_gap",
                "gap_reason": "Quick win at pos 14.",
                "total_volume": 1200,
                "avg_difficulty": 28.5,
                "opportunity_score": 75.0,
                "dominant_serp_features": "",
                "content_format_hints": "",
                "linked_keywords_json": "[]",
            }
        ],
    )
    return ids[0]


def test_schema_has_new_columns(conn: sqlite3.Connection):
    cols = {row[1] for row in conn.execute("PRAGMA table_info(article_ideas)").fetchall()}
    for expected in ["linked_article_handle", "linked_blog_handle", "shopify_article_id"]:
        assert expected in cols, f"Missing column: {expected}"


def test_update_article_idea_status(conn: sqlite3.Connection, saved_idea_id: int):
    updated = update_article_idea_status(conn, saved_idea_id, "approved")
    assert updated is True
    ideas = fetch_article_ideas(conn)
    idea = next(i for i in ideas if i["id"] == saved_idea_id)
    assert idea["status"] == "approved"


def test_update_nonexistent_idea_returns_false(conn: sqlite3.Connection):
    updated = update_article_idea_status(conn, 9999, "approved")
    assert updated is False


def test_link_idea_to_article(conn: sqlite3.Connection, saved_idea_id: int):
    result = link_idea_to_article(
        conn,
        idea_id=saved_idea_id,
        article_handle="best-disposable-vapes-canada",
        blog_handle="news",
        shopify_article_id="gid://shopify/OnlineStoreArticle/999",
    )
    assert result is True
    ideas = fetch_article_ideas(conn)
    idea = next(i for i in ideas if i["id"] == saved_idea_id)
    assert idea["status"] == "drafting"
    assert idea["linked_article_handle"] == "best-disposable-vapes-canada"
    assert idea["linked_blog_handle"] == "news"
    assert idea["shopify_article_id"] == "gid://shopify/OnlineStoreArticle/999"


def test_fetch_ideas_includes_new_fields_with_defaults(conn: sqlite3.Connection, saved_idea_id: int):
    ideas = fetch_article_ideas(conn)
    idea = next(i for i in ideas if i["id"] == saved_idea_id)
    assert idea["linked_article_handle"] == ""
    assert idea["linked_blog_handle"] == ""
    assert idea["shopify_article_id"] == ""
    assert idea["perf_gsc_clicks"] is None
    assert idea["perf_gsc_impressions"] is None
    assert idea["perf_gsc_position"] is None
```

### Step 2: Run to confirm it fails

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_lifecycle.py -v
```

Expected: 5 failures — columns don't exist, functions don't exist.

### Step 3: Add `_ensure_columns` call in `shopifyseo/dashboard_store.py`

In `shopifyseo/dashboard_store.py`, find the existing `_ensure_columns` call for `article_ideas` (around line 369). Add a second call immediately after it (before the final `conn.commit()`):

```python
    _ensure_columns(
        conn,
        "article_ideas",
        {
            "linked_article_handle": "TEXT NOT NULL DEFAULT ''",
            "linked_blog_handle": "TEXT NOT NULL DEFAULT ''",
            "shopify_article_id": "TEXT NOT NULL DEFAULT ''",
        },
    )
```

### Step 4: Run schema test only

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_lifecycle.py::test_schema_has_new_columns -v
```

Expected: PASSED.

### Step 5: Commit

```bash
git add shopifyseo/dashboard_store.py tests/test_article_idea_lifecycle.py
git commit -m "feat(ideas): add linked_article_handle, linked_blog_handle, shopify_article_id columns"
```

---

## Task 2: Backend Queries — Update Functions + Performance JOIN

**Files:**
- Modify: `shopifyseo/dashboard_queries.py`
- Test: `tests/test_article_idea_lifecycle.py`

This task adds `update_article_idea_status()`, `link_idea_to_article()`, updates `fetch_article_ideas()` to return the 3 new columns plus a LEFT JOIN performance read from `blog_articles`, and extends the `queued_keywords` filter to cover all non-rejected, non-idea statuses.

### Step 1: Add `update_article_idea_status` to `dashboard_queries.py`

Find the `delete_article_idea` function (around line 1544). Add immediately after it:

```python
def update_article_idea_status(
    conn: sqlite3.Connection, idea_id: int, new_status: str
) -> bool:
    """Update the status of a single article idea. Returns True if a row was updated."""
    cur = conn.execute(
        "UPDATE article_ideas SET status = ? WHERE id = ?",
        (new_status, idea_id),
    )
    conn.commit()
    return cur.rowcount > 0


def link_idea_to_article(
    conn: sqlite3.Connection,
    idea_id: int,
    article_handle: str,
    blog_handle: str,
    shopify_article_id: str,
) -> bool:
    """Write the article FK back to an idea and set status='drafting'.
    Returns True if a row was updated."""
    cur = conn.execute(
        """
        UPDATE article_ideas
           SET linked_article_handle = ?,
               linked_blog_handle    = ?,
               shopify_article_id    = ?,
               status                = 'drafting'
         WHERE id = ?
        """,
        (article_handle, blog_handle, shopify_article_id, idea_id),
    )
    conn.commit()
    return cur.rowcount > 0
```

### Step 2: Update `fetch_article_ideas` to return new columns + performance JOIN

Find `fetch_article_ideas` (around line 1488). Replace the entire function:

```python
def fetch_article_ideas(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all stored article ideas, newest first, with live GSC performance where available."""
    rows = conn.execute(
        """
        SELECT ai.id, ai.suggested_title, ai.brief, ai.primary_keyword,
               ai.supporting_keywords,
               ai.search_intent, ai.linked_cluster_id, ai.linked_cluster_name,
               ai.linked_collection_handle, ai.linked_collection_title,
               ai.gap_reason, ai.status, ai.created_at,
               COALESCE(ai.content_format, '')              AS content_format,
               COALESCE(ai.estimated_monthly_traffic, 0)   AS estimated_monthly_traffic,
               COALESCE(ai.source_type, 'cluster_gap')     AS source_type,
               COALESCE(ai.total_volume, 0)                AS total_volume,
               COALESCE(ai.avg_difficulty, 0.0)            AS avg_difficulty,
               COALESCE(ai.opportunity_score, 0.0)         AS opportunity_score,
               COALESCE(ai.dominant_serp_features, '')     AS dominant_serp_features,
               COALESCE(ai.content_format_hints, '')       AS content_format_hints,
               COALESCE(ai.linked_keywords_json, '[]')     AS linked_keywords_json,
               COALESCE(ai.linked_article_handle, '')      AS linked_article_handle,
               COALESCE(ai.linked_blog_handle, '')         AS linked_blog_handle,
               COALESCE(ai.shopify_article_id, '')         AS shopify_article_id,
               ba.gsc_clicks        AS perf_gsc_clicks,
               ba.gsc_impressions   AS perf_gsc_impressions,
               ba.gsc_position      AS perf_gsc_position
        FROM article_ideas ai
        LEFT JOIN blog_articles ba
               ON ba.handle      = ai.linked_article_handle
              AND ba.blog_handle = ai.linked_blog_handle
              AND ai.linked_article_handle != ''
        ORDER BY ai.created_at DESC, ai.id DESC
        """
    ).fetchall()
    result = []
    for r in rows:
        try:
            keywords = json.loads(r[4] or "[]")
        except (json.JSONDecodeError, TypeError):
            keywords = []
        try:
            linked_kws = json.loads(r[21] or "[]")
        except (json.JSONDecodeError, TypeError):
            linked_kws = []
        result.append(
            {
                "id": r[0],
                "suggested_title": r[1],
                "brief": r[2],
                "primary_keyword": r[3] or "",
                "supporting_keywords": keywords,
                "search_intent": r[5] or "informational",
                "linked_cluster_id": r[6],
                "linked_cluster_name": r[7] or "",
                "linked_collection_handle": r[8] or "",
                "linked_collection_title": r[9] or "",
                "gap_reason": r[10] or "",
                "status": r[11] or "idea",
                "created_at": r[12],
                "content_format": r[13] or "",
                "estimated_monthly_traffic": int(r[14] or 0),
                "source_type": r[15] or "cluster_gap",
                "total_volume": int(r[16] or 0),
                "avg_difficulty": round(float(r[17] or 0.0), 1),
                "opportunity_score": round(float(r[18] or 0.0), 1),
                "dominant_serp_features": r[19] or "",
                "content_format_hints": r[20] or "",
                "linked_keywords_json": linked_kws,
                "linked_article_handle": r[22] or "",
                "linked_blog_handle": r[23] or "",
                "shopify_article_id": r[24] or "",
                "perf_gsc_clicks": int(r[25]) if r[25] is not None else None,
                "perf_gsc_impressions": int(r[26]) if r[26] is not None else None,
                "perf_gsc_position": round(float(r[27]), 1) if r[27] is not None else None,
            }
        )
    return result
```

### Step 3: Update `queued_keywords` query in `fetch_article_idea_inputs`

Find the `queued_keywords` block in `fetch_article_idea_inputs` (around line 1577 — the block that queries `article_ideas WHERE status = 'idea'`). Replace with:

```python
    # 12. Queued ideas — avoid suggesting keywords already in any active stage of the pipeline
    try:
        queued_rows = conn.execute(
            """
            SELECT primary_keyword
            FROM article_ideas
            WHERE status IN ('idea', 'approved', 'drafting', 'published')
              AND primary_keyword != ''
            ORDER BY created_at DESC
            LIMIT 50
            """
        ).fetchall()
        queued_keywords = [r[0] for r in queued_rows]
    except Exception:
        queued_keywords = []
```

### Step 4: Run all lifecycle tests

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/test_article_idea_lifecycle.py -v
```

Expected: all 5 pass.

### Step 5: Run full test suite for regressions

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/ -x -q
```

Expected: all pass.

### Step 6: Commit

```bash
git add shopifyseo/dashboard_queries.py tests/test_article_idea_lifecycle.py
git commit -m "feat(ideas): add update_status, link_to_article, performance JOIN in fetch_article_ideas"
```

---

## Task 3: Backend API — Approve/Status Endpoints + Draft Writeback

**Files:**
- Modify: `backend/app/routers/article_ideas.py`
- Modify: `backend/app/routers/blogs.py` (`_run_generate_article_draft` + stream endpoint)
- Modify: `backend/app/schemas/article_ideas.py`
- Modify: `backend/app/schemas/blog.py`

### Step 1: Add `idea_id` to `ArticleGenerateDraftRequest` in `backend/app/schemas/blog.py`

Find `ArticleGenerateDraftRequest` (around line 62). Add one field:

```python
class ArticleGenerateDraftRequest(BaseModel):
    blog_id: str
    """Shopify GID for the target blog (e.g. 'gid://shopify/Blog/123')."""
    blog_handle: str
    """Blog handle in the local DB (e.g. 'news') — used for redirect after creation."""
    topic: str
    """The topic or working title for the new article."""
    keywords: list[str] = []
    """Optional target keywords to weave into the article."""
    author_name: str = ""
    slug_hint: str = ""
    """Optional URL handle source (topic-style phrase). If empty, slug is derived from the AI headline."""
    idea_id: int | None = None
    """If set, link the generated article back to this article idea and set its status to 'drafting'."""
```

### Step 2: Modify `_run_generate_article_draft` in `backend/app/routers/blogs.py`

Find `_run_generate_article_draft` (lines 45–119). Replace the section after `conn2.commit()` (around line 106) so the idea writeback is included before the return. Replace from `conn2 = open_db_connection()` through `return ArticleGenerateDraftResult(...)`:

```python
    conn2 = open_db_connection()
    try:
        upsert_blog_article_from_admin_create(
            conn2,
            article,
            blog_handle=payload.blog_handle,
            seo_title=generated["seo_title"],
            seo_description=generated["seo_description"],
        )
        if payload.idea_id is not None:
            dq.link_idea_to_article(
                conn2,
                idea_id=payload.idea_id,
                article_handle=article["handle"],
                blog_handle=payload.blog_handle,
                shopify_article_id=article["id"],
            )
        conn2.commit()
    finally:
        conn2.close()

    return ArticleGenerateDraftResult(
        id=article["id"],
        title=article["title"],
        handle=article["handle"],
        blog_handle=payload.blog_handle,
        blog_title=article.get("blog", {}).get("title", ""),
        is_published=article["isPublished"],
        seo_title=generated["seo_title"],
        seo_description=generated["seo_description"],
    )
```

### Step 3: Add approve and status endpoints to `backend/app/routers/article_ideas.py`

At the end of `article_ideas.py`, add:

```python
@router.patch("/{idea_id}/approve", response_model=SuccessResponse[dict])
def approve_idea(idea_id: int):
    """Mark an idea as approved, moving it into the editorial queue."""
    conn = open_db_connection()
    try:
        updated = dq.update_article_idea_status(conn, idea_id, "approved")
    finally:
        conn.close()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea not found")
    return success_response({"id": idea_id, "status": "approved"})


@router.patch("/{idea_id}/status", response_model=SuccessResponse[dict])
def update_idea_status(idea_id: int, new_status: str):
    """Update an idea's status. Valid values: idea, approved, drafting, published, rejected."""
    _valid = {"idea", "approved", "drafting", "published", "rejected"}
    if new_status not in _valid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status '{new_status}'. Must be one of: {sorted(_valid)}",
        )
    conn = open_db_connection()
    try:
        updated = dq.update_article_idea_status(conn, idea_id, new_status)
    finally:
        conn.close()
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Idea not found")
    return success_response({"id": idea_id, "status": new_status})
```

### Step 4: Update `ArticleIdeaItem` in `backend/app/schemas/article_ideas.py`

Replace the `ArticleIdeaItem` class:

```python
class ArticleIdeaItem(BaseModel):
    id: int
    suggested_title: str
    brief: str
    primary_keyword: str = ""
    supporting_keywords: list[str] = Field(default_factory=list)
    search_intent: str = "informational"
    content_format: str = ""
    estimated_monthly_traffic: int = 0
    linked_cluster_id: int | None = None
    linked_cluster_name: str = ""
    linked_collection_handle: str = ""
    linked_collection_title: str = ""
    source_type: str = "cluster_gap"
    gap_reason: str = ""
    status: str = "idea"
    created_at: int
    # Cluster metrics snapshotted at generation time
    total_volume: int = 0
    avg_difficulty: float = 0.0
    opportunity_score: float = 0.0
    dominant_serp_features: str = ""
    content_format_hints: str = ""
    linked_keywords_json: list = Field(default_factory=list)
    # Article link (set when draft is created)
    linked_article_handle: str = ""
    linked_blog_handle: str = ""
    shopify_article_id: str = ""
    # Live GSC performance (populated when article has GSC data)
    perf_gsc_clicks: int | None = None
    perf_gsc_impressions: int | None = None
    perf_gsc_position: float | None = None
```

### Step 5: Smoke-test the backend

Start the backend and verify the new endpoints respond:

```bash
# In one terminal:
PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000

# In another:
curl -s -X PATCH "http://127.0.0.1:8000/api/article-ideas/1/approve" | python3 -m json.tool
# Expected: {"success": true, "data": {"id": 1, "status": "approved"}} or 404 if no idea with id=1
```

### Step 6: Commit

```bash
git add backend/app/schemas/blog.py backend/app/schemas/article_ideas.py \
        backend/app/routers/article_ideas.py backend/app/routers/blogs.py
git commit -m "feat(ideas): approve/status endpoints + draft stream writes back idea link"
```

---

## Task 4: Frontend Types + Draft Payload

**Files:**
- Modify: `frontend/src/types/api.ts`
- Modify: `frontend/src/lib/run-article-draft-stream.ts`

### Step 1: Update `articleIdeaSchema` in `frontend/src/types/api.ts`

Find the `articleIdeaSchema` definition. Replace it:

```typescript
export const articleIdeaSchema = z.object({
  id: z.number(),
  suggested_title: z.string(),
  brief: z.string(),
  primary_keyword: z.string().default(""),
  supporting_keywords: z.array(z.string()).default([]),
  search_intent: z.string().default("informational"),
  content_format: z.string().default(""),
  estimated_monthly_traffic: z.number().default(0),
  linked_cluster_id: z.number().nullable().optional(),
  linked_cluster_name: z.string().default(""),
  linked_collection_handle: z.string().default(""),
  linked_collection_title: z.string().default(""),
  source_type: z.string().default("cluster_gap"),
  gap_reason: z.string().default(""),
  status: z.string().default("idea"),
  created_at: z.number(),
  total_volume: z.number().default(0),
  avg_difficulty: z.number().default(0),
  opportunity_score: z.number().default(0),
  dominant_serp_features: z.string().default(""),
  content_format_hints: z.string().default(""),
  linked_keywords_json: z.array(z.record(z.any())).default([]),
  // Article link
  linked_article_handle: z.string().default(""),
  linked_blog_handle: z.string().default(""),
  shopify_article_id: z.string().default(""),
  // Live GSC performance
  perf_gsc_clicks: z.number().nullable().optional(),
  perf_gsc_impressions: z.number().nullable().optional(),
  perf_gsc_position: z.number().nullable().optional(),
});
export type ArticleIdea = z.infer<typeof articleIdeaSchema>;
```

### Step 2: Add `idea_id` to `ArticleDraftStreamPayload` in `frontend/src/lib/run-article-draft-stream.ts`

Find the `ArticleDraftStreamPayload` type. Replace it:

```typescript
export type ArticleDraftStreamPayload = {
  blog_id: string;
  blog_handle: string;
  topic: string;
  keywords: string[];
  author_name: string;
  /** If non-empty, used as the source for the Shopify handle (slugified). If empty, handle comes from the AI headline. */
  slug_hint: string;
  /** If set, the generated article will be linked back to this idea and the idea status set to 'drafting'. */
  idea_id?: number;
};
```

No other changes to the file needed — the `idea_id` field will be serialized with the rest of the JSON body automatically.

### Step 3: Build frontend to check for type errors

```bash
cd frontend && npm run rebuild
```

Expected: clean build, no TypeScript errors.

### Step 4: Commit

```bash
git add frontend/src/types/api.ts frontend/src/lib/run-article-draft-stream.ts
git commit -m "feat(ideas): add lifecycle fields to articleIdeaSchema + idea_id to draft stream payload"
```

---

## Task 5: Frontend UI — Approve Button, Status Tabs, Performance Panel

**Files:**
- Modify: `frontend/src/routes/article-ideas-page.tsx`

Read the file carefully before editing — the component structure must be understood before replacing JSX sections. This task makes three targeted changes:

1. **Approve button** on the `IdeaCard` (alongside the existing Dismiss button)
2. **Status tabs** to filter ideas by lifecycle stage
3. **Performance panel** on cards where `linked_article_handle` is set
4. **Pass `idea_id` to the draft modal** so the backend can write back the link

### Step 1: Read the current file

Read `frontend/src/routes/article-ideas-page.tsx` in full before making any changes. Note:
- Where `onDelete` is called and what calls it
- What props `IdeaCard` receives
- What the draft modal payload looks like (search for `runArticleDraftStream`)
- Where the ideas list is filtered/rendered

### Step 2: Add `CheckCircle2` and `ExternalLink` to the lucide-react import

Find the existing `import { ... } from "lucide-react"` line. Add `CheckCircle2` and `ExternalLink` to the list.

### Step 3: Add `approveIdea` API call helper alongside the existing `deleteIdea` helper

Find the pattern where the DELETE call to `/api/article-ideas/{id}` is made (likely in a `handleDelete` function or similar). Add a companion function immediately after it:

```typescript
async function approveIdea(id: number): Promise<void> {
  await fetch(`/api/article-ideas/${id}/approve`, { method: "PATCH" });
}
```

### Step 4: Add `onApprove` prop to `IdeaCard` and the Approve button

Find the `IdeaCard` component definition. Add `onApprove: (id: number) => void` to its props type alongside `onDelete`.

In the card header (where the Trash2 delete button is), add an Approve button to its left:

```tsx
<button
  type="button"
  onClick={() => onApprove(idea.id)}
  className="mt-0.5 shrink-0 rounded-full p-1.5 text-slate-400 transition hover:bg-emerald-50 hover:text-emerald-600"
  title="Approve idea"
>
  <CheckCircle2 size={15} />
</button>
```

**Note:** The Approve button should only appear when `idea.status === "idea"`. Wrap it:

```tsx
{idea.status === "idea" ? (
  <button
    type="button"
    onClick={() => onApprove(idea.id)}
    className="mt-0.5 shrink-0 rounded-full p-1.5 text-slate-400 transition hover:bg-emerald-50 hover:text-emerald-600"
    title="Approve idea"
  >
    <CheckCircle2 size={15} />
  </button>
) : null}
```

### Step 5: Add performance panel to `IdeaCard`

In `IdeaCard`, find where the gap reason box is rendered (the amber `bg-amber-50` div). Add a performance panel immediately before the footer div, shown only when `linked_article_handle` is set:

```tsx
{/* Performance panel — shown once the article is live and has GSC data */}
{idea.linked_article_handle ? (
  <div className="mx-6 mt-3 rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2.5">
    <div className="flex items-center justify-between gap-2">
      <div className="flex flex-wrap gap-3">
        {idea.perf_gsc_clicks != null ? (
          <div className="flex items-center gap-1.5 text-xs text-emerald-700">
            <TrendingUp size={11} />
            <span className="font-semibold">{idea.perf_gsc_clicks.toLocaleString()}</span>
            <span>clicks/mo</span>
          </div>
        ) : null}
        {idea.perf_gsc_impressions != null ? (
          <div className="flex items-center gap-1.5 text-xs text-emerald-700">
            <BarChart2 size={11} />
            <span className="font-semibold">{idea.perf_gsc_impressions.toLocaleString()}</span>
            <span>impressions</span>
          </div>
        ) : null}
        {idea.perf_gsc_position != null ? (
          <div className="flex items-center gap-1.5 text-xs text-emerald-700">
            <span className="text-emerald-500">pos:</span>
            <span className="font-semibold">{idea.perf_gsc_position.toFixed(1)}</span>
          </div>
        ) : (
          <span className="text-xs text-emerald-600 italic">Article created — awaiting GSC data</span>
        )}
      </div>
      <a
        href={`/articles/${idea.linked_blog_handle}/${idea.linked_article_handle}`}
        className="ml-auto flex items-center gap-1 text-xs text-emerald-700 underline-offset-2 hover:underline"
      >
        View article
        <ExternalLink size={10} />
      </a>
    </div>
  </div>
) : null}
```

### Step 6: Pass `idea_id` when calling `runArticleDraftStream` from the idea card

Find where the draft modal submits and calls `runArticleDraftStream(...)`. The payload currently does NOT include `idea_id`. Find the call and add `idea_id: idea.id` to the payload. The modal is opened with an idea pre-selected (search for `onDraft` or the modal state). Ensure the `idea_id` from the selected idea flows through.

Specifically: find where the payload object is constructed for `runArticleDraftStream`. It will look like:
```typescript
{
  blog_id: ...,
  blog_handle: ...,
  topic: ...,
  keywords: ...,
  author_name: ...,
  slug_hint: ...,
}
```

Add `idea_id: selectedIdea?.id` (or equivalent depending on the modal state variable name) to that object.

### Step 7: Add status filter tabs above the ideas grid

Find where the ideas are rendered (the grid/list of `IdeaCard` components). Above that, add a tab row to filter by status. First, find where `ideas` is fetched/stored in component state.

Add a `statusFilter` state variable and tab UI. Insert before the grid:

```tsx
{/* Status filter tabs */}
const STATUS_TABS = [
  { value: "idea", label: "New Ideas" },
  { value: "approved", label: "Approved" },
  { value: "drafting", label: "Drafting" },
  { value: "published", label: "Published" },
] as const;
```

Add to the component state (near the other `useState` calls):
```tsx
const [statusFilter, setStatusFilter] = useState<string>("idea");
```

Filter the ideas array before rendering:
```tsx
const visibleIdeas = ideas.filter((idea) => idea.status === statusFilter);
```

Render the tab bar:
```tsx
<div className="flex gap-1 rounded-xl bg-slate-100 p-1 w-fit mb-4">
  {STATUS_TABS.map((tab) => {
    const count = ideas.filter((i) => i.status === tab.value).length;
    return (
      <button
        key={tab.value}
        type="button"
        onClick={() => setStatusFilter(tab.value)}
        className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
          statusFilter === tab.value
            ? "bg-white text-slate-900 shadow-sm"
            : "text-slate-500 hover:text-slate-700"
        }`}
      >
        {tab.label}
        {count > 0 ? (
          <span className="ml-1.5 rounded-full bg-slate-200 px-1.5 py-0.5 text-xs">{count}</span>
        ) : null}
      </button>
    );
  })}
</div>
```

**Use `visibleIdeas` instead of `ideas` when rendering the grid.**

### Step 8: Wire up `onApprove` handler in the parent component

Find where `IdeaCard` is rendered (where `onDelete` is passed). Add `onApprove`:

```tsx
onApprove={async (id) => {
  await approveIdea(id);
  // Reload ideas to reflect the new status
  loadIdeas(); // or whatever the existing refresh function is called
}}
```

### Step 9: Build frontend

```bash
cd frontend && npm run rebuild
```

Fix any TypeScript errors before committing.

### Step 10: Run full test suite

```bash
PYTHONPATH=. /opt/anaconda3/bin/pytest tests/ -x -q
```

Expected: all 145+ tests pass.

### Step 11: Commit

```bash
git add frontend/src/routes/article-ideas-page.tsx
git commit -m "feat(ideas): approve button, status tabs, draft writeback, performance panel"
```

---

## Final Verification Checklist

- [ ] `PYTHONPATH=. /opt/anaconda3/bin/pytest tests/ -q` — all pass
- [ ] Backend starts: `PYTHONPATH=. uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000`
- [ ] Navigate to Article Ideas page — "New Ideas" tab shows current ideas with Approve + Dismiss buttons
- [ ] Click Approve on an idea → idea disappears from "New Ideas", appears in "Approved" tab
- [ ] Click "Draft article" on an Approved idea → draft modal pre-fills from idea → stream completes → idea moves to "Drafting" tab with "View article" link
- [ ] In Drafting tab, the idea card shows "Article created — awaiting GSC data" until next GSC sync
- [ ] Generate new ideas — previously approved/drafting/published keywords do NOT appear as suggestions (queued_keywords covers all active statuses)
- [ ] Hard refresh browser (⌘⇧R) to clear asset cache
