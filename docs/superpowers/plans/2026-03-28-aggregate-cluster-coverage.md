# Aggregate Keyword Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cluster card coverage badges aggregate keyword presence across ALL related URLs (suggested_match + vendor products + collection products), not just the suggested_match page.

**Architecture:** Modify `enrich_clusters_with_coverage()` in `keyword_clustering.py` to collect content from all discovery sources before running keyword check. Same 3-source chain as `get_cluster_detail()`.

**Tech Stack:** Python, SQLite, pytest

---

### Task 1: Expand `enrich_clusters_with_coverage()` to aggregate content from all related URLs

**Files:**
- Modify: `backend/app/services/keyword_clustering.py:149-225`
- Test: `tests/test_keyword_clustering.py`

- [ ] **Step 1: Write failing tests for aggregate coverage**

Add these tests to `tests/test_keyword_clustering.py`:

```python
def test_enrich_coverage_includes_vendor_products():
    """Coverage should find keywords that appear in vendor product content but not in the collection."""
    conn = _make_test_db()
    # Insert a vendor product with a keyword in its description
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "vaporesso-xros", "Vaporesso XROS", "Vaporesso", "", "", "<p>best vaporesso pod system for beginners</p>"),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Vaporesso Brand",
        keywords=["vaporesso pod system", "vaporesso xros", "vaporesso canada"],
        match_type="collection",
        match_handle="vaporesso-collection",
        match_title="Vaporesso Collection",
    )
    # Insert matching collection with NO keyword content
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "vaporesso-collection", "Vaporesso Collection", "", "", "<p>Browse our selection</p>"),
    )
    conn.commit()

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    # "vaporesso pod system" found in product description, "vaporesso xros" found in product title/handle content
    assert cov is not None
    assert cov["found"] >= 1  # At least "vaporesso pod system" from the product
    assert cov["total"] == 3


def test_enrich_coverage_includes_collection_products():
    """Coverage should find keywords in products that belong to the matched collection."""
    conn = _make_test_db()
    # Insert collection
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "pod-systems", "Pod Systems", "", "", "<p>All pod systems</p>"),
    )
    # Insert product in collection with keyword content
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "smok-nord", "SMOK Nord", "SMOK", "SMOK Nord Pod Kit", "", "<p>best smok pod kit for beginners</p>"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id) VALUES (?, ?)",
        (200, 100),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Pod Systems",
        keywords=["smok pod kit", "pod systems canada", "best pod kit"],
        match_type="collection",
        match_handle="pod-systems",
        match_title="Pod Systems",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    # "smok pod kit" found in product content, "best pod kit" partial match
    assert cov["found"] >= 1
    assert cov["total"] == 3


def test_enrich_coverage_deduplicates_products():
    """A product found via both vendor and collection membership should not double its content."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "vaporesso-collection", "Vaporesso", "", "", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "vaporesso-xros", "Vaporesso XROS", "Vaporesso", "", "", "<p>vaporesso xros pod</p>"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id) VALUES (?, ?)",
        (200, 100),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Vaporesso Brand",
        keywords=["vaporesso xros"],
        match_type="collection",
        match_handle="vaporesso-collection",
        match_title="Vaporesso",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] == 1
    assert cov["total"] == 1


def test_enrich_coverage_no_related_urls_returns_none():
    """Cluster with match_type 'new' and no vendor should return None coverage."""
    conn = _make_test_db()
    cluster_id = _insert_cluster(
        conn,
        name="Random Topic",
        keywords=["random keyword"],
        match_type="new",
        match_handle="",
        match_title="",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    assert cluster["keyword_coverage"] is None


def test_enrich_coverage_no_regression_collection_only():
    """Cluster matched to a collection with keywords in collection content still works."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "elf-bar", "Elf Bar", "Elf Bar Vapes", "Buy elf bar disposable vape", "<p>elf bar canada best prices</p>"),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Elf Bar Brand",
        keywords=["elf bar", "elf bar canada", "elf bar disposable"],
        match_type="collection",
        match_handle="elf-bar",
        match_title="Elf Bar",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] >= 2  # "elf bar" and "elf bar canada" at minimum
    assert cov["total"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_keyword_clustering.py -v -k "test_enrich_coverage_includes_vendor or test_enrich_coverage_includes_collection_products or test_enrich_coverage_deduplicates or test_enrich_coverage_no_related_urls or test_enrich_coverage_no_regression"`
Expected: Some tests FAIL (vendor/collection product content not checked yet)

- [ ] **Step 3: Modify `enrich_clusters_with_coverage()` to aggregate content**

In `backend/app/services/keyword_clustering.py`, replace lines 179-223 (the keyword coverage section inside the for loop) with:

```python
        # --- Keyword coverage ---
        keywords = cluster.get("keywords", [])
        sm = cluster.get("suggested_match")

        # Collect all content from related URLs
        all_content_parts: list[str] = []
        seen_product_handles: set[str] = set()

        # Source 1: Suggested match page
        if sm and sm.get("match_type") not in (None, "new"):
            match_type = sm["match_type"]
            match_handle = sm["match_handle"]
            cache_key = (match_type, match_handle)

            if cache_key not in content_cache:
                if match_type == "collection":
                    row = conn.execute(
                        "SELECT seo_title, seo_description, description_html FROM collections WHERE handle = ?",
                        (match_handle,),
                    ).fetchone()
                elif match_type == "page":
                    row = conn.execute(
                        "SELECT seo_title, seo_description, body FROM pages WHERE handle = ?",
                        (match_handle,),
                    ).fetchone()
                elif match_type == "blog_article":
                    parts = match_handle.split("/", 1)
                    if len(parts) == 2:
                        row = conn.execute(
                            "SELECT seo_title, seo_description, body FROM blog_articles WHERE blog_handle = ? AND handle = ?",
                            (parts[0], parts[1]),
                        ).fetchone()
                    else:
                        row = None
                else:
                    row = None

                content_cache[cache_key] = (
                    " ".join(row[i] or "" for i in range(3)) if row else ""
                )

            page_content = content_cache[cache_key]
            if page_content:
                all_content_parts.append(page_content)

        # Source 2: Vendor products
        matched_vendor = cluster.get("matched_vendor")
        if matched_vendor:
            vendor_products = conn.execute(
                "SELECT handle, title, seo_title, seo_description, description_html FROM products WHERE LOWER(vendor) = ?",
                (matched_vendor["name"].lower(),),
            ).fetchall()
            for vp in vendor_products:
                if vp[0] in seen_product_handles:
                    continue
                seen_product_handles.add(vp[0])
                content = " ".join(vp[i] or "" for i in range(1, 5))
                if content.strip():
                    all_content_parts.append(content)

        # Source 3: Collection products (if match is a collection)
        if sm and sm.get("match_type") == "collection" and sm.get("match_handle"):
            cp_rows = conn.execute(
                """SELECT p.handle, p.title, p.seo_title, p.seo_description, p.description_html
                   FROM products p
                   JOIN collection_products cp ON p.shopify_id = cp.product_shopify_id
                   JOIN collections c ON cp.collection_shopify_id = c.shopify_id
                   WHERE c.handle = ?""",
                (sm["match_handle"],),
            ).fetchall()
            for cp in cp_rows:
                if cp[0] in seen_product_handles:
                    continue
                seen_product_handles.add(cp[0])
                content = " ".join(cp[i] or "" for i in range(1, 5))
                if content.strip():
                    all_content_parts.append(content)

        # Check coverage across all content
        if not all_content_parts:
            cluster["keyword_coverage"] = None
            continue

        combined_content = " ".join(all_content_parts)
        found, total = _check_keyword_coverage(keywords, combined_content)
        cluster["keyword_coverage"] = {"found": found, "total": total}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_keyword_clustering.py -v`
Expected: ALL tests pass (existing + new)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/keyword_clustering.py tests/test_keyword_clustering.py
git commit -m "feat: aggregate keyword coverage across all related URLs on cluster cards

Coverage badge now scans vendor products and collection products in addition
to the suggested_match page. A keyword counts as found if it appears in ANY
related URL's content."
```
