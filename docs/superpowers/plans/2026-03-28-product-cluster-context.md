# Product Cluster Keyword Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable product AI generation (body, seo_title, seo_description) to receive cluster keyword context by reverse-looking up which clusters relate to a product via vendor match and collection membership.

**Architecture:** Extract shared formatting logic from `_load_cluster_context()` into `_format_cluster_context()`, add new `_find_clusters_for_product()` reverse-lookup function, and wire the product fallback path in `generation.py`. All changes in 2 files + tests.

**Tech Stack:** Python, SQLite, pytest

---

### Task 1: Extract `_format_cluster_context()` and add tests

**Files:**
- Modify: `backend/app/services/keyword_clustering.py:345-412`
- Modify: `tests/test_keyword_clustering.py`

- [ ] **Step 1: Write tests for `_format_cluster_context()`**

Add to the import block in `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import (
    _build_clustering_prompt,
    _check_keyword_coverage,
    _compute_cluster_stats,
    _detect_vendor,
    _format_cluster_context,
    _group_by_parent_topic,
    _load_cluster_context,
    get_cluster_detail,
    load_clusters,
)
```

Add these tests at the end of the file:

```python
def test_format_cluster_context_single_cluster():
    """Formats a single cluster with keyword metrics."""
    clusters = [
        {
            "name": "Elf Bar Disposable Vapes",
            "content_type": "collection_page",
            "primary_keyword": "elf bar canada",
            "content_brief": "Comprehensive collection page for Elf Bar disposable vapes.",
            "keywords": ["elf bar canada", "elf bar vape", "elf bar review"],
        },
    ]
    target_data = {
        "items": [
            {"keyword": "elf bar canada", "volume": 1200, "difficulty": 35},
            {"keyword": "elf bar vape", "volume": 800, "difficulty": 25},
            {"keyword": "elf bar review", "volume": 400, "difficulty": 20},
        ]
    }
    result = _format_cluster_context(clusters, target_data)
    assert result is not None
    assert "Elf Bar Disposable Vapes" in result
    assert "elf bar canada" in result
    assert "1200" in result
    assert "35" in result
    assert "elf bar vape" in result
    assert "collection_page" in result


def test_format_cluster_context_multiple_clusters():
    """Formats multiple clusters separated by blank lines."""
    clusters = [
        {
            "name": "Cluster A",
            "content_type": "collection_page",
            "primary_keyword": "kw a",
            "content_brief": "Brief A.",
            "keywords": ["kw a"],
        },
        {
            "name": "Cluster B",
            "content_type": "blog_post",
            "primary_keyword": "kw b",
            "content_brief": "Brief B.",
            "keywords": ["kw b"],
        },
    ]
    target_data = {"items": []}
    result = _format_cluster_context(clusters, target_data)
    assert result is not None
    assert "Cluster A" in result
    assert "Cluster B" in result
    # Two clusters separated by double newline
    assert "\n\n" in result


def test_format_cluster_context_empty_list():
    """Returns None for empty cluster list."""
    result = _format_cluster_context([], {"items": []})
    assert result is None


def test_format_cluster_context_missing_metrics():
    """Keywords not in target_data get 0 for volume and difficulty."""
    clusters = [
        {
            "name": "Test",
            "content_type": "blog_post",
            "primary_keyword": "unknown kw",
            "content_brief": "Brief.",
            "keywords": ["unknown kw", "another unknown"],
        },
    ]
    target_data = {"items": []}
    result = _format_cluster_context(clusters, target_data)
    assert result is not None
    assert "volume: 0" in result
    assert "difficulty: 0" in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_format_cluster_context_single_cluster -v`
Expected: FAIL with ImportError (cannot import `_format_cluster_context`)

- [ ] **Step 3: Extract `_format_cluster_context()` from `_load_cluster_context()`**

In `backend/app/services/keyword_clustering.py`, add the new function **before** `_load_cluster_context` (before line 345):

```python
def _format_cluster_context(
    matched_clusters: list[dict],
    target_data: dict,
) -> str | None:
    """Format matched clusters into a context string for the LLM prompt.

    Builds a human-readable block per cluster listing primary keyword (with
    volume/difficulty), supporting keywords, content angle, and recommended
    content type.  Returns None when *matched_clusters* is empty.
    """
    if not matched_clusters:
        return None

    # Build keyword metrics lookup from target keywords
    kw_map: dict[str, dict] = {}
    for item in target_data.get("items") or []:
        kw_map[item.get("keyword", "").lower()] = item

    sections: list[str] = []
    for cluster in matched_clusters:
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

Then refactor `_load_cluster_context` to use it. Replace lines 382-412 (the `if not matched: return None` block through end of function) with:

```python
    if not matched:
        return None

    return _format_cluster_context(matched, target_data)
```

The full `_load_cluster_context` after refactor:

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

    return _format_cluster_context(matched, target_data)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS (existing `_load_cluster_context` tests unchanged + 4 new `_format_cluster_context` tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py tests/test_keyword_clustering.py
git commit -m "refactor: extract _format_cluster_context from _load_cluster_context"
```

---

### Task 2: Implement `_find_clusters_for_product()` and add tests

**Files:**
- Modify: `backend/app/services/keyword_clustering.py`
- Modify: `tests/test_keyword_clustering.py`

- [ ] **Step 1: Write tests for `_find_clusters_for_product()`**

Add `_find_clusters_for_product` to the import block in `tests/test_keyword_clustering.py`:

```python
from backend.app.services.keyword_clustering import (
    _build_clustering_prompt,
    _check_keyword_coverage,
    _compute_cluster_stats,
    _detect_vendor,
    _find_clusters_for_product,
    _format_cluster_context,
    _group_by_parent_topic,
    _load_cluster_context,
    get_cluster_detail,
    load_clusters,
)
```

Add these tests at the end of the file:

```python
def test_find_clusters_for_product_vendor_match():
    """Finds cluster when product vendor appears in cluster name."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth canada",
                          "STLTH collection.", ["stlth canada", "stlth vape"],
                          match_type="collection", match_handle="stlth", match_title="STLTH")
    clusters_data = {"clusters": [
        {"id": cid, "name": "STLTH Brand", "content_type": "collection_page",
         "primary_keyword": "stlth canada", "content_brief": "STLTH collection.",
         "keywords": ["stlth canada", "stlth vape"],
         "suggested_match": {"match_type": "collection", "match_handle": "stlth", "match_title": "STLTH"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "stlth-loop-9k", "STLTH", clusters_data)
    assert len(result) == 1
    assert result[0]["name"] == "STLTH Brand"
    conn.close()


def test_find_clusters_for_product_collection_membership():
    """Finds cluster via collection membership when product is in matched collection."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Disposable Vapes", "collection_page", "disposable vape",
                          "Disposable vapes.", ["disposable vape", "cheap disposable"],
                          match_type="collection", match_handle="disposables", match_title="Disposables")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "Disposables", "disposables", "", "", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        ("p1", "Allo Ultra", "allo-ultra", "ALLO"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "allo-ultra", "Allo Ultra", "2026-03-28"),
    )
    conn.commit()
    clusters_data = {"clusters": [
        {"id": cid, "name": "Disposable Vapes", "content_type": "collection_page",
         "primary_keyword": "disposable vape", "content_brief": "Disposable vapes.",
         "keywords": ["disposable vape", "cheap disposable"],
         "suggested_match": {"match_type": "collection", "match_handle": "disposables", "match_title": "Disposables"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "allo-ultra", "ALLO", clusters_data)
    assert len(result) == 1
    assert result[0]["name"] == "Disposable Vapes"
    conn.close()


def test_find_clusters_for_product_deduplication():
    """Same cluster found via vendor and collection appears only once."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "STLTH Brand", "collection_page", "stlth canada",
                          "STLTH collection.", ["stlth canada"],
                          match_type="collection", match_handle="stlth", match_title="STLTH")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle) VALUES (?, ?, ?)",
        ("col1", "STLTH", "stlth"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        ("p1", "STLTH Loop", "stlth-loop", "STLTH"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "stlth-loop", "STLTH Loop", "2026-03-28"),
    )
    conn.commit()
    clusters_data = {"clusters": [
        {"id": cid, "name": "STLTH Brand", "content_type": "collection_page",
         "primary_keyword": "stlth canada", "content_brief": "STLTH collection.",
         "keywords": ["stlth canada"],
         "suggested_match": {"match_type": "collection", "match_handle": "stlth", "match_title": "STLTH"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "stlth-loop", "STLTH", clusters_data)
    assert len(result) == 1
    conn.close()


def test_find_clusters_for_product_no_matches():
    """Returns empty list when no clusters relate to the product."""
    conn = _make_test_db()
    _insert_cluster(conn, "Elf Bar", "collection_page", "elf bar",
                    "Elf Bar.", ["elf bar"],
                    match_type="collection", match_handle="elf-bar", match_title="Elf Bar")
    clusters_data = {"clusters": [
        {"id": 1, "name": "Elf Bar", "content_type": "collection_page",
         "primary_keyword": "elf bar", "content_brief": "Elf Bar.",
         "keywords": ["elf bar"],
         "suggested_match": {"match_type": "collection", "match_handle": "elf-bar", "match_title": "Elf Bar"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "some-other-product", "UnknownBrand", clusters_data)
    assert result == []
    conn.close()


def test_find_clusters_for_product_caps_at_three():
    """Returns at most 3 clusters even if more match."""
    conn = _make_test_db()
    clusters_list = []
    for i in range(5):
        cid = _insert_cluster(conn, f"STLTH Cluster {i}", "collection_page", f"stlth kw{i}",
                              f"Brief {i}.", [f"stlth kw{i}"])
        clusters_list.append({
            "id": cid, "name": f"STLTH Cluster {i}", "content_type": "collection_page",
            "primary_keyword": f"stlth kw{i}", "content_brief": f"Brief {i}.",
            "keywords": [f"stlth kw{i}"],
            "suggested_match": None,
        })
    clusters_data = {"clusters": clusters_list, "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "stlth-loop", "STLTH", clusters_data)
    assert len(result) == 3
    conn.close()


def test_find_clusters_for_product_empty_vendor_uses_collection():
    """Empty vendor skips vendor path but still finds via collection membership."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Disposable Vapes", "collection_page", "disposable vape",
                          "Disposable vapes.", ["disposable vape"],
                          match_type="collection", match_handle="disposables", match_title="Disposables")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle) VALUES (?, ?, ?)",
        ("col1", "Disposables", "disposables"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        ("p1", "Some Vape", "some-vape", ""),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "some-vape", "Some Vape", "2026-03-28"),
    )
    conn.commit()
    clusters_data = {"clusters": [
        {"id": cid, "name": "Disposable Vapes", "content_type": "collection_page",
         "primary_keyword": "disposable vape", "content_brief": "Disposable vapes.",
         "keywords": ["disposable vape"],
         "suggested_match": {"match_type": "collection", "match_handle": "disposables", "match_title": "Disposables"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "some-vape", "", clusters_data)
    assert len(result) == 1
    assert result[0]["name"] == "Disposable Vapes"
    conn.close()


def test_find_clusters_for_product_not_in_db():
    """Product handle not in DB returns empty (collection path finds nothing)."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Disposable Vapes", "collection_page", "disposable vape",
                          "Disposable vapes.", ["disposable vape"],
                          match_type="collection", match_handle="disposables", match_title="Disposables")
    clusters_data = {"clusters": [
        {"id": cid, "name": "Disposable Vapes", "content_type": "collection_page",
         "primary_keyword": "disposable vape", "content_brief": "Disposable vapes.",
         "keywords": ["disposable vape"],
         "suggested_match": {"match_type": "collection", "match_handle": "disposables", "match_title": "Disposables"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "nonexistent-product", "NoBrand", clusters_data)
    assert result == []
    conn.close()


def test_find_clusters_for_product_short_vendor_skipped():
    """Vendor shorter than 3 characters skips vendor path."""
    conn = _make_test_db()
    # Cluster name contains "BC" but vendor is too short to match
    cid = _insert_cluster(conn, "BC Vapes", "collection_page", "bc vape",
                          "BC vapes.", ["bc vape"])
    clusters_data = {"clusters": [
        {"id": cid, "name": "BC Vapes", "content_type": "collection_page",
         "primary_keyword": "bc vape", "content_brief": "BC vapes.",
         "keywords": ["bc vape"],
         "suggested_match": None},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "some-product", "BC", clusters_data)
    assert result == []
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py::test_find_clusters_for_product_vendor_match -v`
Expected: FAIL with ImportError (cannot import `_find_clusters_for_product`)

- [ ] **Step 3: Implement `_find_clusters_for_product()`**

Add this function to `backend/app/services/keyword_clustering.py`, after `_format_cluster_context` and before `_load_cluster_context`:

```python
_MIN_VENDOR_LENGTH = 3


def _find_clusters_for_product(
    conn: sqlite3.Connection,
    product_handle: str,
    product_vendor: str,
    clusters_data: dict,
) -> list[dict]:
    """Reverse-lookup: find up to 3 clusters related to a product.

    Discovery paths (priority order):
    1. Vendor match — cluster name or keywords contain the product vendor name
    2. Collection membership — product belongs to a collection that a cluster
       points to via suggested_match

    Deduplicates by cluster id.  Vendor matches appear first (higher priority).
    """
    clusters = clusters_data.get("clusters") or []
    if not clusters:
        return []

    matched: list[dict] = []
    seen_ids: set[int] = set()

    # --- Path 1: Vendor match ---
    vendor_lower = product_vendor.strip().lower()
    if vendor_lower and len(vendor_lower) >= _MIN_VENDOR_LENGTH:
        for cluster in clusters:
            if len(matched) >= 3:
                break
            cid = cluster.get("id")
            if cid in seen_ids:
                continue
            name_lower = cluster.get("name", "").lower()
            kws_lower = [kw.lower() for kw in cluster.get("keywords", [])]
            if vendor_lower in name_lower or any(vendor_lower in kw for kw in kws_lower):
                matched.append(cluster)
                seen_ids.add(cid)

    # --- Path 2: Collection membership ---
    if len(matched) < 3:
        collection_handles = {
            row[0]
            for row in conn.execute(
                """SELECT c.handle FROM collections c
                   JOIN collection_products cp ON c.shopify_id = cp.collection_shopify_id
                   JOIN products p ON p.shopify_id = cp.product_shopify_id
                   WHERE p.handle = ?""",
                (product_handle,),
            ).fetchall()
        }
        if collection_handles:
            for cluster in clusters:
                if len(matched) >= 3:
                    break
                cid = cluster.get("id")
                if cid in seen_ids:
                    continue
                sm = cluster.get("suggested_match")
                if not sm:
                    continue
                if sm.get("match_type") == "collection" and sm.get("match_handle") in collection_handles:
                    matched.append(cluster)
                    seen_ids.add(cid)

    return matched
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS (41 existing + 4 format + 8 find = 53 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py tests/test_keyword_clustering.py
git commit -m "feat: add _find_clusters_for_product reverse-lookup function"
```

---

### Task 3: Wire product cluster context in `generation.py`

**Files:**
- Modify: `shopifyseo/dashboard_ai_engine_parts/generation.py:1030-1038`

- [ ] **Step 1: Update the import and add product fallback**

In `shopifyseo/dashboard_ai_engine_parts/generation.py`, replace the cluster context block (lines 1030-1038):

```python
    # Load cluster context for matched pages
    try:
        from backend.app.services.keyword_clustering import load_clusters, _load_cluster_context
        clusters_data = load_clusters(conn)
        from shopifyseo.dashboard_google import get_service_setting as _get_ss
        target_raw = _get_ss(conn, "target_keywords", "{}")
        target_data = json.loads(target_raw) if target_raw else {}
        cluster_ctx = _load_cluster_context(clusters_data, target_data, object_type, handle)
        if cluster_ctx:
            context["cluster_seo_context"] = cluster_ctx
    except Exception:
        logger.debug("Failed to load cluster context; proceeding without it")
```

With:

```python
    # Load cluster context for matched pages (and products via reverse-lookup)
    try:
        from backend.app.services.keyword_clustering import (
            load_clusters, _load_cluster_context,
            _find_clusters_for_product, _format_cluster_context,
        )
        clusters_data = load_clusters(conn)
        from shopifyseo.dashboard_google import get_service_setting as _get_ss
        target_raw = _get_ss(conn, "target_keywords", "{}")
        target_data = json.loads(target_raw) if target_raw else {}
        cluster_ctx = _load_cluster_context(clusters_data, target_data, object_type, handle)

        # Product fallback: reverse-lookup clusters via vendor + collection membership
        if not cluster_ctx and object_type == "product":
            vendor = (context.get("detail") or {}).get("product", {}).get("vendor", "")
            matched = _find_clusters_for_product(conn, handle, vendor, clusters_data)
            cluster_ctx = _format_cluster_context(matched, target_data)

        if cluster_ctx:
            context["cluster_seo_context"] = cluster_ctx
    except Exception:
        logger.debug("Failed to load cluster context; proceeding without it")
```

- [ ] **Step 2: Verify the import works**

Run: `python -c "from backend.app.services.keyword_clustering import load_clusters, _load_cluster_context, _find_clusters_for_product, _format_cluster_context; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run all backend tests**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add shopifyseo/dashboard_ai_engine_parts/generation.py
git commit -m "feat: wire product cluster keyword context into AI generation"
```

---

### Task 4: Final verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: All tests PASS (should be 53 total)

- [ ] **Step 2: Verify no unused imports**

Run: `python -c "from backend.app.services.keyword_clustering import _format_cluster_context, _find_clusters_for_product, _load_cluster_context, load_clusters, generate_clusters, update_cluster_match, get_cluster_detail, _detect_vendor, enrich_clusters_with_coverage, get_match_options; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Run TypeScript check (no frontend changes, sanity check)**

Run: `cd frontend && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit if any cleanup was needed**

```bash
git add -A
git commit -m "chore: final cleanup for product cluster context"
```
