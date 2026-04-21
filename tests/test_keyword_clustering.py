import json
import sqlite3

import pytest

from backend.app.services.keyword_clustering import (
    _build_clustering_prompt,
    _check_keyword_coverage,
    _get_matched_cluster_keywords,
    _keyword_coverage_detail,
    _compute_cluster_stats,
    _detect_vendor,
    _find_clusters_for_product,
    _format_cluster_context,
    _group_by_parent_topic,
    _load_cluster_context,
    compute_seo_gaps,
    enrich_clusters_with_coverage,
    get_cluster_detail,
    load_clusters,
)


def _make_test_db() -> sqlite3.Connection:
    """Create an in-memory DB with the cluster tables."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            content_type TEXT NOT NULL,
            primary_keyword TEXT NOT NULL,
            content_brief TEXT NOT NULL,
            total_volume INTEGER NOT NULL DEFAULT 0,
            avg_difficulty REAL NOT NULL DEFAULT 0.0,
            avg_opportunity REAL NOT NULL DEFAULT 0.0,
            match_type TEXT,
            match_handle TEXT,
            match_title TEXT,
            generated_at TEXT NOT NULL,
            dominant_serp_features TEXT DEFAULT '',
            content_format_hints TEXT DEFAULT '',
            avg_cps REAL DEFAULT 0.0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cluster_keywords (
            cluster_id INTEGER NOT NULL,
            keyword TEXT NOT NULL,
            PRIMARY KEY (cluster_id, keyword),
            FOREIGN KEY (cluster_id) REFERENCES clusters(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS products (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            vendor TEXT,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT,
            online_store_url TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            seo_title TEXT,
            seo_description TEXT,
            description_html TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS collection_products (
            collection_shopify_id TEXT NOT NULL,
            product_shopify_id TEXT NOT NULL,
            product_handle TEXT,
            product_title TEXT,
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (collection_shopify_id, product_shopify_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            shopify_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            handle TEXT NOT NULL UNIQUE,
            seo_title TEXT,
            seo_description TEXT,
            body TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS blog_articles (
            shopify_id TEXT PRIMARY KEY,
            blog_shopify_id TEXT NOT NULL,
            blog_handle TEXT NOT NULL,
            title TEXT NOT NULL,
            handle TEXT NOT NULL,
            seo_title TEXT,
            seo_description TEXT,
            body TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS service_settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    return conn


def test_cluster_tables_exist():
    """Verify the test DB helper creates the expected tables."""
    conn = _make_test_db()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    assert "clusters" in tables
    assert "cluster_keywords" in tables
    conn.close()


def test_cluster_cascade_delete():
    """Deleting a cluster cascades to cluster_keywords."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, generated_at) VALUES (?, ?, ?, ?, ?)",
        ("Test", "blog_post", "kw1", "Brief", "2026-01-01T00:00:00Z"),
    )
    cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "kw1"))
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "kw2"))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM cluster_keywords").fetchone()[0] == 2
    conn.execute("DELETE FROM clusters WHERE id = ?", (cluster_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM cluster_keywords").fetchone()[0] == 0
    conn.close()


def test_group_by_parent_topic_basic():
    keywords = [
        {"keyword": "alpine canada", "parent_topic": "alpine", "volume": 100},
        {"keyword": "alpine review", "parent_topic": "alpine", "volume": 80},
        {"keyword": "protein bar canada", "parent_topic": "protein bar", "volume": 200},
        {"keyword": "best travel bottle", "parent_topic": None, "volume": 150},
    ]
    groups, orphans = _group_by_parent_topic(keywords)
    assert len(groups) == 2
    assert len(groups["alpine"]) == 2
    assert len(groups["protein bar"]) == 1
    assert len(orphans) == 1
    assert orphans[0]["keyword"] == "best travel bottle"


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


def test_compute_cluster_stats_basic():
    all_keywords_map = {
        "alpine canada": {"volume": 100, "difficulty": 20, "opportunity": 80.0},
        "alpine review": {"volume": 80, "difficulty": 30, "opportunity": 60.0},
        "alpine bottle": {"volume": 50, "difficulty": 10, "opportunity": 90.0},
    }
    stats = _compute_cluster_stats(
        ["alpine canada", "alpine review", "alpine bottle"], all_keywords_map
    )
    assert stats["keyword_count"] == 3
    assert stats["total_volume"] == 230
    assert stats["avg_difficulty"] == 20.0
    assert stats["avg_opportunity"] == 76.7


def test_compute_cluster_stats_missing_keyword():
    """Keywords not found in the map are silently skipped."""
    all_keywords_map = {
        "alpine canada": {"volume": 100, "difficulty": 20, "opportunity": 80.0},
    }
    stats = _compute_cluster_stats(
        ["alpine canada", "nonexistent keyword"], all_keywords_map
    )
    assert stats["keyword_count"] == 1
    assert stats["total_volume"] == 100


def test_compute_cluster_stats_empty():
    stats = _compute_cluster_stats([], {})
    assert stats["keyword_count"] == 0
    assert stats["total_volume"] == 0
    assert stats["avg_difficulty"] == 0.0
    assert stats["avg_opportunity"] == 0.0


def test_compute_cluster_stats_serp_features_list_and_dict():
    """serp_features from provider JSON may be a list or dict, not a comma string."""
    all_keywords_map = {
        "kw1": {
            "volume": 10,
            "difficulty": 5,
            "opportunity": 50.0,
            "serp_features": ["people_also_ask", "featured_snippet"],
            "content_format_hint": ["faq", "video"],
        },
        "kw2": {
            "volume": 10,
            "difficulty": 5,
            "opportunity": 50.0,
            "serp_features": {"people_also_ask": 2, "video": 1, "thin": 0},
        },
    }
    stats = _compute_cluster_stats(["kw1", "kw2"], all_keywords_map)
    assert stats["keyword_count"] == 2
    dsf = stats["dominant_serp_features"]
    assert "people_also_ask" in dsf
    assert stats["content_format_hints"]


def test_build_clustering_prompt_returns_system_and_user():
    groups = {
        "alpine": [
            {"keyword": "alpine canada", "volume": 100, "difficulty": 20,
             "opportunity": 80.0, "intent": "commercial", "content_type": "Comparison / Buying guide",
             "parent_topic": "alpine", "ranking_status": "not_ranking"},
        ],
    }
    orphans = [
        {"keyword": "best travel bottle", "volume": 150, "difficulty": 30,
         "opportunity": 70.0, "intent": "commercial", "content_type": "Comparison / Buying guide",
         "parent_topic": None, "ranking_status": "quick_win"},
    ]
    system_prompt, user_prompt = _build_clustering_prompt(groups, orphans)
    assert "SEO" in system_prompt
    assert "cluster" in system_prompt.lower()
    assert "alpine canada" in user_prompt
    assert "best travel bottle" in user_prompt


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


def test_load_cluster_context_match_found():
    """Returns formatted string when a cluster matches the handle/type."""
    clusters_data = {
        "clusters": [
            {
                "name": "Alpine Travel Bottles",
                "content_type": "collection_page",
                "primary_keyword": "alpine canada",
                "content_brief": "Comprehensive collection page for Alpine travel bottles.",
                "keywords": ["alpine canada", "alpine bottle", "alpine review"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "alpine",
                    "match_title": "Alpine",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": "alpine canada", "status": "approved", "volume": 1200, "difficulty": 35, "opportunity": 80.0},
            {"keyword": "alpine bottle", "status": "approved", "volume": 800, "difficulty": 25, "opportunity": 70.0},
            {"keyword": "alpine review", "status": "approved", "volume": 400, "difficulty": 20, "opportunity": 60.0},
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "alpine")
    assert result is not None
    assert "Alpine Travel Bottles" in result
    assert "alpine canada" in result
    assert "1200" in result
    assert "collection_page" in result


def test_load_cluster_context_no_match():
    """Returns None when no cluster matches the handle."""
    clusters_data = {
        "clusters": [
            {
                "name": "Alpine Travel Bottles",
                "content_type": "collection_page",
                "primary_keyword": "alpine canada",
                "content_brief": "...",
                "keywords": ["alpine canada"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "alpine",
                    "match_title": "Alpine",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {"items": []}
    result = _load_cluster_context(clusters_data, target_data, "collection", "disposable-bottles")
    assert result is None


def test_load_cluster_context_null_suggested_match():
    """Gracefully skips clusters with null suggested_match."""
    clusters_data = {
        "clusters": [
            {
                "name": "Some Cluster",
                "content_type": "blog_post",
                "primary_keyword": "protein bar",
                "content_brief": "...",
                "keywords": ["protein bar"],
                "suggested_match": None,
            },
            {
                "name": "Alpine",
                "content_type": "collection_page",
                "primary_keyword": "alpine canada",
                "content_brief": "Alpine collection.",
                "keywords": ["alpine canada"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "alpine",
                    "match_title": "Alpine",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": "alpine canada", "status": "approved", "volume": 1200, "difficulty": 35, "opportunity": 80.0},
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "alpine")
    assert result is not None
    assert "Alpine" in result


def test_load_cluster_context_product_returns_none():
    """Products don't match clusters — always returns None."""
    clusters_data = {
        "clusters": [
            {
                "name": "Alpine",
                "content_type": "collection_page",
                "primary_keyword": "alpine",
                "content_brief": "...",
                "keywords": ["alpine"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "alpine",
                    "match_title": "Alpine",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {"items": []}
    result = _load_cluster_context(clusters_data, target_data, "product", "alpine-bc10000")
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
                    "match_handle": "alpine",
                    "match_title": "Alpine",
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
    result = _load_cluster_context(clusters_data, target_data, "collection", "alpine")
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
                "name": "Travel Bottles",
                "content_type": "collection_page",
                "primary_keyword": "travel bottle canada",
                "content_brief": "All travel bottles.",
                "keywords": ["travel bottle canada", "cheap travel bottle"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "bottles",
                    "match_title": "Bottles",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {
        "items": [
            {"keyword": "travel bottle canada", "status": "approved", "volume": 2000, "difficulty": 40, "opportunity": 75.0},
            {"keyword": "cheap travel bottle", "status": "approved", "volume": 500, "difficulty": 15, "opportunity": 85.0},
        ]
    }
    result = _load_cluster_context(clusters_data, target_data, "collection", "bottles")
    assert result is not None
    assert "2000" in result
    assert "40" in result
    assert "500" in result


def test_load_cluster_context_empty_clusters():
    """Returns None when clusters list is empty."""
    clusters_data = {"clusters": [], "generated_at": None}
    target_data = {"items": []}
    result = _load_cluster_context(clusters_data, target_data, "collection", "alpine")
    assert result is None


def test_load_cluster_context_type_mismatch():
    """Returns None when match_type doesn't correspond to object_type."""
    clusters_data = {
        "clusters": [
            {
                "name": "Alpine",
                "content_type": "collection_page",
                "primary_keyword": "alpine",
                "content_brief": "...",
                "keywords": ["alpine"],
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "alpine",
                    "match_title": "Alpine",
                },
            },
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    }
    target_data = {"items": []}
    # Asking for page type but cluster matched to collection
    result = _load_cluster_context(clusters_data, target_data, "page", "alpine")
    assert result is None


# --- _check_keyword_coverage tests ---


def test_keyword_coverage_basic():
    """Finds exact substring matches in content."""
    keywords = ["alpine canada", "alpine bottle", "best travel"]
    content = "Buy Alpine Canada travel bottles. The Alpine Bottle line is popular."
    found, total = _check_keyword_coverage(keywords, content)
    assert total == 3
    assert found == 2  # "alpine canada" and "alpine bottle" found, "best travel" not


def test_keyword_coverage_case_insensitive():
    keywords = ["NOVA Filters", "nova loop"]
    content = "Shop nova filters and the NOVA Loop starter kit."
    found, total = _check_keyword_coverage(keywords, content)
    assert found == 2
    assert total == 2


def test_keyword_coverage_empty_content():
    keywords = ["alpine", "bottle"]
    content = ""
    found, total = _check_keyword_coverage(keywords, content)
    assert found == 0
    assert total == 2


def test_keyword_coverage_empty_keywords():
    found, total = _check_keyword_coverage([], "some content here")
    assert found == 0
    assert total == 0


def test_keyword_coverage_html_stripped():
    """HTML tags should not interfere with matching."""
    keywords = ["nova filters"]
    content = "<h2>Buy <strong>NOVA Filters</strong> in Canada</h2>"
    found, total = _check_keyword_coverage(keywords, content)
    assert found == 1


def test_keyword_coverage_split_phrase_does_not_count():
    """Only the full phrase substring counts; scattered words do not."""
    keywords = ["how to charge a bottle"]
    content = "<p>Here is how you can charge your bottle pen safely in Canada.</p>"
    found, total = _check_keyword_coverage(keywords, content)
    assert total == 1
    assert found == 0


def test_keyword_coverage_detail_exact_phrase_only():
    content = "<p>Shop nova bottle canada — official site with filters and deals.</p>"
    d = _keyword_coverage_detail(["nova filters canada", "nova bottle canada"], content)
    assert d["found"] == 1
    assert d["keywords_found"] == ["nova bottle canada"]
    assert d["keywords_missing"] == ["nova filters canada"]


# --- _detect_vendor tests ---


def test_detect_vendor_in_cluster_name():
    vendor_map = {"elfbar": {"name": "ELFBAR", "product_count": 67}}
    result = _detect_vendor("Alpine Travel Bottles", ["alpine canada"], vendor_map)
    assert result is None  # "elfbar" not in "alpine travel bottles" as substring
    vendor_map2 = {"alpine": {"name": "Alpine", "product_count": 67}}
    result2 = _detect_vendor("Alpine Travel Bottles", ["alpine canada"], vendor_map2)
    assert result2 == {"name": "Alpine", "product_count": 67}


def test_detect_vendor_in_keywords():
    vendor_map = {"nova": {"name": "NOVA", "product_count": 9}}
    result = _detect_vendor("Brand Collection", ["nova canada", "nova bottle"], vendor_map)
    assert result == {"name": "NOVA", "product_count": 9}


def test_detect_vendor_no_match():
    vendor_map = {"nova": {"name": "NOVA", "product_count": 9}}
    result = _detect_vendor("Travel Bottles Guide", ["travel bottle", "cheap bottle"], vendor_map)
    assert result is None


# --- load_clusters / _migrate_json_to_db tests ---


def test_load_clusters_from_db():
    conn = _make_test_db()
    conn.execute(
        """INSERT INTO clusters
           (name, content_type, primary_keyword, content_brief, total_volume, avg_difficulty, avg_opportunity,
            dominant_serp_features, content_format_hints, avg_cps,
            match_type, match_handle, match_title, generated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "Alpine",
            "collection_page",
            "alpine canada",
            "Alpine collection.",
            500,
            20.0,
            75.0,
            "People also ask, Video",
            "Long-form guide",
            1.25,
            "collection",
            "alpine",
            "Alpine",
            "2026-03-28T00:00:00Z",
        ),
    )
    cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "alpine canada"))
    conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, "alpine bottle"))
    conn.commit()
    data = load_clusters(conn)
    assert len(data["clusters"]) == 1
    c = data["clusters"][0]
    assert c["id"] == cluster_id
    assert c["name"] == "Alpine"
    assert c["keywords"] == ["alpine canada", "alpine bottle"]
    assert c["keyword_count"] == 2
    assert c["suggested_match"] == {"match_type": "collection", "match_handle": "alpine", "match_title": "Alpine"}
    assert data["generated_at"] == "2026-03-28T00:00:00Z"
    assert c["stats"]["dominant_serp_features"] == "People also ask, Video"
    assert c["stats"]["content_format_hints"] == "Long-form guide"
    assert c["stats"]["avg_cps"] == 1.25
    conn.close()


def test_load_clusters_null_match():
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, generated_at) VALUES (?, ?, ?, ?, ?)",
        ("Test", "blog_post", "kw1", "Brief", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    data = load_clusters(conn)
    assert data["clusters"][0]["suggested_match"] is None
    conn.close()


def test_load_clusters_new_match():
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO clusters (name, content_type, primary_keyword, content_brief, match_type, match_handle, match_title, generated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("Test", "blog_post", "kw1", "Brief", "new", "", "", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    data = load_clusters(conn)
    assert data["clusters"][0]["suggested_match"] == {"match_type": "new", "match_handle": "", "match_title": ""}
    conn.close()


def test_load_clusters_empty_db():
    conn = _make_test_db()
    data = load_clusters(conn)
    assert data == {"clusters": [], "generated_at": None}
    conn.close()


def test_migrate_json_to_db():
    conn = _make_test_db()
    json_data = json.dumps({
        "clusters": [
            {
                "name": "Alpine",
                "content_type": "collection_page",
                "primary_keyword": "alpine canada",
                "content_brief": "Alpine collection.",
                "keywords": ["alpine canada", "alpine bottle"],
                "keyword_count": 2,
                "total_volume": 500,
                "avg_difficulty": 20.0,
                "avg_opportunity": 75.0,
                "dominant_serp_features": "FAQ, Featured snippet",
                "content_format_hints": "Comparison table",
                "avg_cps": 0.42,
                "suggested_match": {
                    "match_type": "collection",
                    "match_handle": "alpine",
                    "match_title": "Alpine",
                },
            }
        ],
        "generated_at": "2026-03-28T00:00:00Z",
    })
    conn.execute("INSERT INTO service_settings (key, value) VALUES (?, ?)", ("keyword_clusters", json_data))
    conn.commit()
    data = load_clusters(conn)
    assert len(data["clusters"]) == 1
    c = data["clusters"][0]
    assert c["name"] == "Alpine"
    assert c["keywords"] == ["alpine canada", "alpine bottle"]
    assert c["suggested_match"]["match_type"] == "collection"
    row = conn.execute("SELECT value FROM service_settings WHERE key = ?", ("keyword_clusters",)).fetchone()
    assert row is None
    assert conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM cluster_keywords").fetchone()[0] == 2
    assert c["stats"]["dominant_serp_features"] == "FAQ, Featured snippet"
    assert c["stats"]["content_format_hints"] == "Comparison table"
    assert c["stats"]["avg_cps"] == 0.42
    conn.close()


def test_migrate_no_json_no_data():
    conn = _make_test_db()
    data = load_clusters(conn)
    assert data == {"clusters": [], "generated_at": None}
    conn.close()


# --- get_cluster_detail tests ---


def _insert_cluster(conn, name, content_type="collection_page", primary_keyword=None, content_brief="Test brief.", keywords=None, match_type=None, match_handle=None, match_title=None) -> int:
    """Helper to insert a cluster and its keywords. Returns cluster id."""
    kws = keywords or []
    pk = primary_keyword if primary_keyword is not None else (kws[0] if kws else "test-keyword")
    conn.execute(
        """INSERT INTO clusters
           (name, content_type, primary_keyword, content_brief,
            total_volume, avg_difficulty, avg_opportunity,
            dominant_serp_features, content_format_hints, avg_cps,
            match_type, match_handle, match_title, generated_at)
           VALUES (?, ?, ?, ?, 0, 0.0, 0.0, '', '', 0.0, ?, ?, ?, '2026-03-28T00:00:00Z')""",
        (name, content_type, pk, content_brief, match_type, match_handle, match_title),
    )
    cluster_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    for kw in kws:
        conn.execute("INSERT INTO cluster_keywords (cluster_id, keyword) VALUES (?, ?)", (cluster_id, kw))
    conn.commit()
    return cluster_id


def test_detail_with_suggested_match():
    """Collection match appears in related_urls with coverage."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova canada",
                          "NOVA collection.", ["nova canada", "nova bottle"],
                          match_type="collection", match_handle="nova", match_title="NOVA")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "NOVA", "nova", "NOVA Bottles Canada", "Buy NOVA bottle filters", "<p>NOVA Canada collection</p>"),
    )
    conn.commit()
    result = get_cluster_detail(conn, cid)
    assert result["cluster"]["name"] == "NOVA Brand"
    urls = result["related_urls"]
    assert len(urls) >= 1
    match_url = [u for u in urls if u["source"] == "suggested_match"]
    assert len(match_url) == 1
    assert match_url[0]["url_type"] == "collection"
    assert match_url[0]["handle"] == "nova"
    assert match_url[0]["keyword_coverage"]["total"] == 2
    kc = match_url[0]["keyword_coverage"]
    assert "keywords_found" in kc and "keywords_missing" in kc
    assert set(kc["keywords_found"]) | set(kc["keywords_missing"]) == {"nova canada", "nova bottle"}
    conn.close()


def test_detail_vendor_products():
    """Vendor products appear with source 'vendor'."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova canada",
                          "NOVA collection.", ["nova canada", "nova loop"],
                          match_type="new", match_handle="", match_title="")
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "NOVA Loop 9K", "nova-loop-9k", "NOVA", "NOVA Loop", "Loop bottle", "<p>NOVA Loop 9K device</p>"),
    )
    conn.commit()
    result = get_cluster_detail(conn, cid)
    vendor_urls = [u for u in result["related_urls"] if u["source"] == "vendor"]
    assert len(vendor_urls) == 1
    assert vendor_urls[0]["handle"] == "nova-loop-9k"
    assert vendor_urls[0]["url_type"] == "product"
    conn.close()


def test_detail_collection_products():
    """Products in matched collection appear with source 'collection_products'."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Bottles", "collection_page", "travel bottle",
                          "Travel bottles.", ["travel bottle", "cheap travel"],
                          match_type="collection", match_handle="bottles", match_title="Bottles")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "Bottles", "bottles", "Travel Bottles", "Buy travel bottles", "<p>Cheap travel bottles</p>"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "Summit Pro", "summit-pro", "APEX", "Summit Pro", "Travel bottle", "<p>Summit Pro disposable</p>"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "summit-pro", "Summit Pro", "2026-03-28"),
    )
    conn.commit()
    result = get_cluster_detail(conn, cid)
    cp_urls = [u for u in result["related_urls"] if u["source"] == "collection_products"]
    assert len(cp_urls) == 1
    assert cp_urls[0]["handle"] == "summit-pro"
    conn.close()


def test_detail_deduplication():
    """Product via vendor+collection appears once with 'vendor' source (higher priority)."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova canada",
                          "NOVA collection.", ["nova canada"],
                          match_type="collection", match_handle="nova", match_title="NOVA")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "NOVA", "nova", "NOVA", "", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "NOVA Loop", "nova-loop", "NOVA", "", "", ""),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "nova-loop", "NOVA Loop", "2026-03-28"),
    )
    conn.commit()
    result = get_cluster_detail(conn, cid)
    product_urls = [u for u in result["related_urls"] if u["url_type"] == "product"]
    assert len(product_urls) == 1
    assert product_urls[0]["source"] == "vendor"
    conn.close()


def test_detail_cluster_not_found():
    """Raises ValueError for nonexistent cluster id."""
    conn = _make_test_db()
    with pytest.raises(ValueError):
        get_cluster_detail(conn, 9999)
    conn.close()


def test_detail_no_related_urls():
    """Cluster with match_type 'new' and no vendor returns empty related_urls."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "New Topic", "blog_post", "bottle guide",
                          "Guide.", ["bottle guide"],
                          match_type="new", match_handle="", match_title="")
    result = get_cluster_detail(conn, cid)
    assert result["related_urls"] == []
    conn.close()


def test_detail_none_match_skips_suggested():
    """match_type NULL means no suggested match URL."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Orphan", "blog_post", "random kw",
                          "Brief.", ["random kw"])
    result = get_cluster_detail(conn, cid)
    match_urls = [u for u in result["related_urls"] if u["source"] == "suggested_match"]
    assert len(match_urls) == 0
    conn.close()


def test_detail_product_coverage_uses_title():
    """Product coverage includes title field (4 fields total)."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova loop",
                          "NOVA.", ["nova loop"],
                          match_type="new", match_handle="", match_title="")
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "NOVA Loop 9K", "nova-loop-9k", "NOVA", "Bottle Device", "A great bottle", "<p>Premium device</p>"),
    )
    conn.commit()
    result = get_cluster_detail(conn, cid)
    vendor_urls = [u for u in result["related_urls"] if u["source"] == "vendor"]
    assert len(vendor_urls) == 1
    assert vendor_urls[0]["keyword_coverage"]["found"] == 1
    kc = vendor_urls[0]["keyword_coverage"]
    assert kc["keywords_found"] == ["nova loop"]
    assert kc["keywords_missing"] == []
    conn.close()


def test_detail_sorted_by_coverage():
    """Related URLs are sorted by coverage found descending."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova canada",
                          "NOVA.", ["nova canada", "nova bottle", "nova loop"],
                          match_type="new", match_handle="", match_title="")
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p1", "NOVA Canada Bottle", "nova-vape", "NOVA", "NOVA Canada", "NOVA bottle device", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("p2", "Bold 8K Device", "nova-bold", "NOVA", "Bold Device", "Premium", ""),
    )
    conn.commit()
    result = get_cluster_detail(conn, cid)
    urls = result["related_urls"]
    assert len(urls) == 2
    assert urls[0]["keyword_coverage"]["found"] >= urls[1]["keyword_coverage"]["found"]
    conn.close()


def test_format_cluster_context_single_cluster():
    """Formats a single cluster with keyword metrics."""
    clusters = [
        {
            "name": "Alpine Travel Bottles",
            "content_type": "collection_page",
            "primary_keyword": "alpine canada",
            "content_brief": "Comprehensive collection page for Alpine travel bottles.",
            "keywords": ["alpine canada", "alpine bottle", "alpine review"],
        },
    ]
    target_data = {
        "items": [
            {"keyword": "alpine canada", "volume": 1200, "difficulty": 35},
            {"keyword": "alpine bottle", "volume": 800, "difficulty": 25},
            {"keyword": "alpine review", "volume": 400, "difficulty": 20},
        ]
    }
    result = _format_cluster_context(clusters, target_data)
    assert result is not None
    assert "Alpine Travel Bottles" in result
    assert "alpine canada" in result
    assert "1200" in result
    assert "35" in result
    assert "alpine bottle" in result
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


# --- _find_clusters_for_product tests ---


def test_find_clusters_for_product_vendor_match():
    """Finds cluster when product vendor appears in cluster name."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova canada",
                          "NOVA collection.", ["nova canada", "nova bottle"],
                          match_type="collection", match_handle="nova", match_title="NOVA")
    clusters_data = {"clusters": [
        {"id": cid, "name": "NOVA Brand", "content_type": "collection_page",
         "primary_keyword": "nova canada", "content_brief": "NOVA collection.",
         "keywords": ["nova canada", "nova bottle"],
         "suggested_match": {"match_type": "collection", "match_handle": "nova", "match_title": "NOVA"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "nova-loop-9k", "NOVA", clusters_data)
    assert len(result) == 1
    assert result[0]["name"] == "NOVA Brand"
    conn.close()


def test_find_clusters_for_product_collection_membership():
    """Finds cluster via collection membership when product is in matched collection."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Travel Bottles", "collection_page", "travel bottle",
                          "Travel bottles.", ["travel bottle", "cheap travel"],
                          match_type="collection", match_handle="bottles", match_title="Bottles")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        ("col1", "Bottles", "bottles", "", "", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        ("p1", "Summit Pro", "summit-pro", "APEX"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "summit-pro", "Summit Pro", "2026-03-28"),
    )
    conn.commit()
    clusters_data = {"clusters": [
        {"id": cid, "name": "Travel Bottles", "content_type": "collection_page",
         "primary_keyword": "travel bottle", "content_brief": "Travel bottles.",
         "keywords": ["travel bottle", "cheap travel"],
         "suggested_match": {"match_type": "collection", "match_handle": "bottles", "match_title": "Bottles"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "summit-pro", "APEX", clusters_data)
    assert len(result) == 1
    assert result[0]["name"] == "Travel Bottles"
    conn.close()


def test_find_clusters_for_product_deduplication():
    """Same cluster found via vendor and collection appears only once."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "NOVA Brand", "collection_page", "nova canada",
                          "NOVA collection.", ["nova canada"],
                          match_type="collection", match_handle="nova", match_title="NOVA")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle) VALUES (?, ?, ?)",
        ("col1", "NOVA", "nova"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        ("p1", "NOVA Loop", "nova-loop", "NOVA"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "nova-loop", "NOVA Loop", "2026-03-28"),
    )
    conn.commit()
    clusters_data = {"clusters": [
        {"id": cid, "name": "NOVA Brand", "content_type": "collection_page",
         "primary_keyword": "nova canada", "content_brief": "NOVA collection.",
         "keywords": ["nova canada"],
         "suggested_match": {"match_type": "collection", "match_handle": "nova", "match_title": "NOVA"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "nova-loop", "NOVA", clusters_data)
    assert len(result) == 1
    conn.close()


def test_find_clusters_for_product_no_matches():
    """Returns empty list when no clusters relate to the product."""
    conn = _make_test_db()
    _insert_cluster(conn, "Alpine", "collection_page", "alpine",
                    "Alpine.", ["alpine"],
                    match_type="collection", match_handle="alpine", match_title="Alpine")
    clusters_data = {"clusters": [
        {"id": 1, "name": "Alpine", "content_type": "collection_page",
         "primary_keyword": "alpine", "content_brief": "Alpine.",
         "keywords": ["alpine"],
         "suggested_match": {"match_type": "collection", "match_handle": "alpine", "match_title": "Alpine"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "some-other-product", "UnknownBrand", clusters_data)
    assert result == []
    conn.close()


def test_find_clusters_for_product_caps_at_three():
    """Returns at most 3 clusters even if more match."""
    conn = _make_test_db()
    clusters_list = []
    for i in range(5):
        cid = _insert_cluster(conn, f"NOVA Cluster {i}", "collection_page", f"nova kw{i}",
                              f"Brief {i}.", [f"nova kw{i}"])
        clusters_list.append({
            "id": cid, "name": f"NOVA Cluster {i}", "content_type": "collection_page",
            "primary_keyword": f"nova kw{i}", "content_brief": f"Brief {i}.",
            "keywords": [f"nova kw{i}"],
            "suggested_match": None,
        })
    clusters_data = {"clusters": clusters_list, "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "nova-loop", "NOVA", clusters_data)
    assert len(result) == 3
    conn.close()


def test_find_clusters_for_product_empty_vendor_uses_collection():
    """Empty vendor skips vendor path but still finds via collection membership."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Travel Bottles", "collection_page", "travel bottle",
                          "Travel bottles.", ["travel bottle"],
                          match_type="collection", match_handle="bottles", match_title="Bottles")
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle) VALUES (?, ?, ?)",
        ("col1", "Bottles", "bottles"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor) VALUES (?, ?, ?, ?)",
        ("p1", "Some Bottle", "some-vape", ""),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id, product_handle, product_title, synced_at) VALUES (?, ?, ?, ?, ?)",
        ("col1", "p1", "some-vape", "Some Bottle", "2026-03-28"),
    )
    conn.commit()
    clusters_data = {"clusters": [
        {"id": cid, "name": "Travel Bottles", "content_type": "collection_page",
         "primary_keyword": "travel bottle", "content_brief": "Travel bottles.",
         "keywords": ["travel bottle"],
         "suggested_match": {"match_type": "collection", "match_handle": "bottles", "match_title": "Bottles"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "some-vape", "", clusters_data)
    assert len(result) == 1
    assert result[0]["name"] == "Travel Bottles"
    conn.close()


def test_find_clusters_for_product_not_in_db():
    """Product handle not in DB returns empty (collection path finds nothing)."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "Travel Bottles", "collection_page", "travel bottle",
                          "Travel bottles.", ["travel bottle"],
                          match_type="collection", match_handle="bottles", match_title="Bottles")
    clusters_data = {"clusters": [
        {"id": cid, "name": "Travel Bottles", "content_type": "collection_page",
         "primary_keyword": "travel bottle", "content_brief": "Travel bottles.",
         "keywords": ["travel bottle"],
         "suggested_match": {"match_type": "collection", "match_handle": "bottles", "match_title": "Bottles"}},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "nonexistent-product", "NoBrand", clusters_data)
    assert result == []
    conn.close()


def test_find_clusters_for_product_short_vendor_skipped():
    """Vendor shorter than 3 characters skips vendor path."""
    conn = _make_test_db()
    cid = _insert_cluster(conn, "BC Bottles", "collection_page", "bc bottle",
                          "BC bottles.", ["bc bottle"])
    clusters_data = {"clusters": [
        {"id": cid, "name": "BC Bottles", "content_type": "collection_page",
         "primary_keyword": "bc bottle", "content_brief": "BC bottles.",
         "keywords": ["bc bottle"],
         "suggested_match": None},
    ], "generated_at": "2026-03-28T00:00:00Z"}

    result = _find_clusters_for_product(conn, "some-product", "BC", clusters_data)
    assert result == []
    conn.close()


# --- enrich_clusters_with_coverage aggregate tests ---


def test_enrich_coverage_includes_vendor_products():
    """Coverage should find keywords that appear in vendor product content but not in the collection."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "Greenleaf XROS", "greenleaf-xros", "Greenleaf", "", "", "<p>best greenleaf filter kit for beginners</p>"),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Greenleaf Brand",
        keywords=["greenleaf filter kit", "greenleaf xros", "greenleaf canada"],
        match_type="collection",
        match_handle="greenleaf-collection",
        match_title="Greenleaf Collection",
    )
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "Greenleaf Collection", "greenleaf-collection", "", "", "<p>Browse our selection</p>"),
    )
    conn.commit()

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] >= 1
    assert cov["total"] == 3


def test_enrich_coverage_includes_collection_products():
    """Coverage should find keywords in products that belong to the matched collection."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "Filter Kits", "filter-kits", "", "", "<p>All filter kits</p>"),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "BOLT Nord", "bolt-nord", "BOLT", "BOLT Nord Filter Kit", "", "<p>best bolt filter kit for beginners</p>"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id) VALUES (?, ?)",
        (200, 100),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Filter Kits",
        keywords=["bolt filter kit", "filter kits canada", "best filter kit"],
        match_type="collection",
        match_handle="filter-kits",
        match_title="Filter Kits",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] >= 1
    assert cov["total"] == 3


def test_enrich_coverage_deduplicates_products():
    """A product found via both vendor and collection membership should not double its content."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "Greenleaf", "greenleaf-collection", "", "", ""),
    )
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "Greenleaf XROS", "greenleaf-xros", "Greenleaf", "", "", "<p>greenleaf xros filter</p>"),
    )
    conn.execute(
        "INSERT INTO collection_products (collection_shopify_id, product_shopify_id) VALUES (?, ?)",
        (200, 100),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Greenleaf Brand",
        keywords=["greenleaf xros"],
        match_type="collection",
        match_handle="greenleaf-collection",
        match_title="Greenleaf",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] == 1
    assert cov["total"] == 1


def test_enrich_coverage_vendor_only_when_no_page_match():
    """With match_type 'new', aggregate still includes vendor product content."""
    conn = _make_test_db()
    conn.execute(
        "INSERT INTO products (shopify_id, title, handle, vendor, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (100, "Sierra Pulse", "sierra-pulse", "Sierra", "", "", "<p>sierra travel bottle canada</p>"),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Sierra Brand",
        keywords=["sierra travel", "sierra canada"],
        match_type="new",
        match_handle="",
        match_title="",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] >= 1
    assert cov["total"] == 2


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
        "INSERT INTO collections (shopify_id, title, handle, seo_title, seo_description, description_html) VALUES (?, ?, ?, ?, ?, ?)",
        (200, "Alpine", "alpine", "Alpine Bottles", "Buy alpine travel bottle", "<p>alpine canada best prices</p>"),
    )
    conn.commit()

    cluster_id = _insert_cluster(
        conn,
        name="Alpine Brand",
        keywords=["alpine", "alpine canada", "alpine travel"],
        match_type="collection",
        match_handle="alpine",
        match_title="Alpine",
    )

    data = load_clusters(conn)
    enriched = enrich_clusters_with_coverage(conn, data)
    cluster = next(c for c in enriched["clusters"] if c["id"] == cluster_id)
    cov = cluster["keyword_coverage"]
    assert cov is not None
    assert cov["found"] >= 2
    assert cov["total"] == 3


# ---------------------------------------------------------------------------
# compute_seo_gaps
# ---------------------------------------------------------------------------


def _kw_metrics(*entries):
    """Build a keyword_metrics dict from (keyword, opportunity, ranking_status) tuples."""
    return {kw.lower(): {"opportunity": opp, "ranking_status": rs} for kw, opp, rs in entries}


def test_compute_seo_gaps_partition():
    """Keywords found in content go to already_present; missing ones to must_consider."""
    keywords = ["nova bottle canada", "nova filters", "nova 5k"]
    content = {"body": "<p>Buy nova filters at the best price.</p>"}
    metrics = _kw_metrics(
        ("nova bottle canada", 80, "quick_win"),
        ("nova filters", 50, "ranking"),
        ("nova 5k", 60, "not_ranking"),
    )
    result = compute_seo_gaps(keywords, content, metrics)
    assert result is not None
    assert result["already_present"] == ["nova filters"]
    mc_kws = [m["keyword"] for m in result["must_consider"]]
    assert "nova bottle canada" in mc_kws
    assert "nova 5k" in mc_kws
    assert "nova filters" not in mc_kws
    assert result["coverage_ratio"] == "1/3"


def test_compute_seo_gaps_sort_by_opportunity():
    """Higher opportunity keywords sort first."""
    keywords = ["kw_low", "kw_high", "kw_mid"]
    content = {"body": ""}
    metrics = _kw_metrics(
        ("kw_low", 10, "not_ranking"),
        ("kw_high", 90, "not_ranking"),
        ("kw_mid", 50, "not_ranking"),
    )
    result = compute_seo_gaps(keywords, content, metrics)
    mc = [m["keyword"] for m in result["must_consider"]]
    assert mc == ["kw_high", "kw_mid", "kw_low"]


def test_compute_seo_gaps_ranking_boost():
    """quick_win and striking_distance get +20 boost, sorting above higher-opportunity not_ranking."""
    keywords = ["high_nr", "low_qw"]
    content = {"body": ""}
    metrics = _kw_metrics(
        ("high_nr", 70, "not_ranking"),   # sort score = 70
        ("low_qw", 55, "quick_win"),       # sort score = 55 + 20 = 75
    )
    result = compute_seo_gaps(keywords, content, metrics)
    mc = [m["keyword"] for m in result["must_consider"]]
    assert mc[0] == "low_qw"
    assert mc[1] == "high_nr"


def test_compute_seo_gaps_primary_keyword_guarantee():
    """Primary keyword always appears first in must_consider even with low opportunity."""
    keywords = ["primary_kw", "high_opp_kw", "mid_opp_kw"]
    content = {"body": ""}
    metrics = _kw_metrics(
        ("primary_kw", 5, "not_ranking"),
        ("high_opp_kw", 90, "not_ranking"),
        ("mid_opp_kw", 50, "not_ranking"),
    )
    result = compute_seo_gaps(keywords, content, metrics, primary_keyword="primary_kw")
    assert result["must_consider"][0]["keyword"] == "primary_kw"
    assert result["primary_keyword"] == "primary_kw"


def test_compute_seo_gaps_cap_at_8():
    """Only top 8 missing keywords appear in must_consider."""
    keywords = [f"kw_{i}" for i in range(15)]
    content = {"body": ""}
    metrics = {f"kw_{i}": {"opportunity": 100 - i, "ranking_status": "not_ranking"} for i in range(15)}
    result = compute_seo_gaps(keywords, content, metrics)
    assert len(result["must_consider"]) == 8
    assert result["must_consider"][0]["keyword"] == "kw_0"
    assert result["must_consider"][7]["keyword"] == "kw_7"


def test_compute_seo_gaps_all_covered_returns_none():
    """Returns None when all cluster keywords are already present."""
    keywords = ["bottle pen", "travel bottle"]
    content = {"body": "<p>Buy a bottle pen or travel bottle today.</p>"}
    metrics = _kw_metrics(("bottle pen", 50, "ranking"), ("travel bottle", 40, "ranking"))
    result = compute_seo_gaps(keywords, content, metrics)
    assert result is None


def test_compute_seo_gaps_empty_content():
    """All keywords are missing when content is empty."""
    keywords = ["kw1", "kw2", "kw3"]
    content = {"title": "", "body": ""}
    metrics = _kw_metrics(("kw1", 30, "not_ranking"), ("kw2", 20, "not_ranking"), ("kw3", 10, "not_ranking"))
    result = compute_seo_gaps(keywords, content, metrics)
    assert result is not None
    assert len(result["must_consider"]) == 3
    assert len(result["already_present"]) == 0
    assert result["coverage_ratio"] == "0/3"


def test_compute_seo_gaps_accepted_fields_count_as_present():
    """Content from accepted sibling fields counts toward coverage (simulates field regen)."""
    keywords = ["alpine", "alpine canada", "alpine 5000 puffs"]
    content_with_accepted = {
        "title": "Alpine BC5000",
        "seo_title": "Alpine Canada — Buy Alpine Bottles Online",
        "seo_description": "",
        "body": "",
    }
    metrics = _kw_metrics(
        ("alpine", 70, "ranking"),
        ("alpine canada", 80, "quick_win"),
        ("alpine 5000 puffs", 60, "not_ranking"),
    )
    result = compute_seo_gaps(keywords, content_with_accepted, metrics)
    assert result is not None
    assert "alpine" in result["already_present"]
    assert "alpine canada" in result["already_present"]
    mc_kws = [m["keyword"] for m in result["must_consider"]]
    assert "alpine 5000 puffs" in mc_kws


def test_compute_seo_gaps_missing_metrics_default():
    """Keywords not in keyword_metrics get opportunity=0 and not_ranking."""
    keywords = ["unknown_kw"]
    content = {"body": ""}
    result = compute_seo_gaps(keywords, content, {})
    assert result is not None
    mc = result["must_consider"]
    assert len(mc) == 1
    assert mc[0]["opportunity"] == 0
    assert mc[0]["ranking_status"] == "not_ranking"


# ---------------------------------------------------------------------------
# _get_matched_cluster_keywords
# ---------------------------------------------------------------------------


def test_get_matched_cluster_keywords_direct_match():
    """Returns formatted context, keywords, primary_keyword, and kw_map for a direct match."""
    clusters_data = {
        "clusters": [
            {
                "id": 1,
                "name": "NOVA Brand",
                "primary_keyword": "nova bottle",
                "keywords": ["nova bottle", "nova filters", "nova canada"],
                "content_brief": "Brand overview",
                "content_type": "collection",
                "suggested_match": {"match_type": "collection", "match_handle": "nova"},
            },
        ],
    }
    target_data = {
        "items": [
            {"keyword": "nova bottle", "volume": 1000, "difficulty": 20, "opportunity": 80},
            {"keyword": "nova filters", "volume": 500, "difficulty": 10, "opportunity": 60},
            {"keyword": "nova canada", "volume": 300, "difficulty": 5, "opportunity": 50},
        ],
    }
    formatted, all_kws, primary_kw, kw_map = _get_matched_cluster_keywords(
        clusters_data, target_data, "collection", "nova",
    )
    assert formatted is not None
    assert "nova bottle" in formatted
    assert set(all_kws) == {"nova bottle", "nova filters", "nova canada"}
    assert primary_kw == "nova bottle"
    assert "nova bottle" in kw_map


def test_get_matched_cluster_keywords_no_match():
    """Returns empty tuple values when no clusters match."""
    clusters_data = {"clusters": []}
    target_data = {"items": []}
    formatted, all_kws, primary_kw, kw_map = _get_matched_cluster_keywords(
        clusters_data, target_data, "collection", "nonexistent",
    )
    assert formatted is None
    assert all_kws == []
    assert primary_kw == ""
    assert kw_map == {}


def test_generate_clusters_reads_approved_from_db(monkeypatch):
    """keyword_metrics is the source of truth for 'approved' — a stale
    target_keywords JSON blob must not leak into the LLM prompt."""
    import time as _time
    from backend.app.services.keyword_clustering import _generation
    from shopifyseo import market_context
    from shopifyseo.dashboard_store import ensure_dashboard_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)

    now = int(_time.time())
    conn.executemany(
        "INSERT INTO keyword_metrics (keyword, volume, difficulty, opportunity, "
        "intent, content_type_label, parent_topic, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("db-approved-a", 100, 20, 50.0, "commercial", "Product / Collection page", "topic-x", "approved", now),
            ("db-approved-b", 80, 15, 40.0, "commercial", "Product / Collection page", "topic-x", "approved", now),
            ("db-dismissed", 90, 30, 30.0, "commercial", "Product / Collection page", "topic-x", "dismissed", now),
            ("db-new", 70, 10, 20.0, "commercial", "Product / Collection page", "topic-x", "new", now),
        ],
    )
    # Stale JSON blob: claims a totally different keyword is approved.
    conn.execute(
        "INSERT INTO service_settings (key, value) VALUES ('target_keywords', ?)",
        (json.dumps({"items": [
            {"keyword": "json-stale-kw", "status": "approved", "volume": 999,
             "difficulty": 1, "opportunity": 100.0, "intent": "commercial",
             "content_type": "Blog / Guide", "parent_topic": "topic-x"},
        ]}),),
    )
    conn.commit()

    captured: dict[str, list] = {}

    def fake_call_ai(*, settings, provider, model, messages, timeout, json_schema, stage):
        captured.setdefault(stage, []).append(messages)
        if stage == "clustering":
            return {"clusters": [{
                "name": "Test Cluster",
                "content_type": "collection_page",
                "primary_keyword": "db-approved-a",
                "content_brief": "brief",
                "keywords": ["db-approved-a", "db-approved-b"],
            }]}
        return {"matches": []}

    monkeypatch.setattr(_generation, "_call_ai", fake_call_ai)
    monkeypatch.setattr(
        _generation, "ai_settings",
        lambda c: {"generation_provider": "anthropic", "generation_model": "claude-x", "timeout": 60},
    )
    monkeypatch.setattr(_generation, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(market_context, "get_primary_country_code", lambda c: "CA")
    monkeypatch.setattr(market_context, "country_display_name", lambda c: "Canadian")

    result = _generation.generate_clusters(conn)

    assert len(result["clusters"]) == 1
    assert "clustering" in captured, "LLM clustering call should have happened"

    user_prompt = captured["clustering"][0][1]["content"]
    assert "db-approved-a" in user_prompt
    assert "db-approved-b" in user_prompt
    assert "db-dismissed" not in user_prompt
    assert "db-new" not in user_prompt
    assert "json-stale-kw" not in user_prompt, (
        "clustering must read from keyword_metrics, not the service_settings JSON blob"
    )

    cluster_rows = conn.execute("SELECT name FROM clusters").fetchall()
    assert len(cluster_rows) == 1
    assert cluster_rows[0][0] == "Test Cluster"
    conn.close()


# ---------------------------------------------------------------------------
# Phase 2: embedding-based near-duplicate collapse
# ---------------------------------------------------------------------------


def _make_dedupe_db() -> sqlite3.Connection:
    """In-memory DB with the full dashboard schema (includes embeddings)."""
    from shopifyseo.dashboard_store import ensure_dashboard_schema
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    return conn


def _insert_keyword_embedding(
    conn: sqlite3.Connection,
    keyword: str,
    vec: list[float],
) -> None:
    """Insert a fake keyword embedding into the embeddings table."""
    import struct
    blob = struct.pack(f"{len(vec)}f", *vec)
    conn.execute(
        "INSERT INTO embeddings (object_type, object_handle, chunk_index, text_hash, "
        "model_version, embedding, source_text_preview, token_count, updated_at) "
        "VALUES ('keyword', ?, 0, 'h', 'test', ?, ?, 0, '2026-04-19T00:00:00Z')",
        (keyword, blob, keyword),
    )
    conn.commit()


def _kw(keyword: str, opportunity: float = 50.0, volume: int = 100, **extra) -> dict:
    return {"keyword": keyword, "opportunity": opportunity, "volume": volume, **extra}


def test_collapse_near_duplicates_under_two_passes_through():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    canonicals, aliases = collapse_near_duplicates([_kw("only")], conn)
    assert [c["keyword"] for c in canonicals] == ["only"]
    assert aliases == {}


def test_collapse_near_duplicates_no_embeddings_passthrough():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    approved = [_kw("a"), _kw("b")]
    canonicals, aliases = collapse_near_duplicates(approved, conn)
    assert sorted(c["keyword"] for c in canonicals) == ["a", "b"]
    assert aliases == {}


def test_collapse_near_duplicates_similar_pair_collapses():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    # Nearly-parallel vectors — cosine ≈ 1.0
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "vape pens", [0.999, 0.001, 0.0, 0.0])

    approved = [
        _kw("vape pen", opportunity=90.0, volume=500),
        _kw("vape pens", opportunity=60.0, volume=300),
    ]
    canonicals, aliases = collapse_near_duplicates(approved, conn, threshold=0.95)
    assert [c["keyword"] for c in canonicals] == ["vape pen"]
    assert aliases == {"vape pen": ["vape pens"]}


def test_collapse_near_duplicates_distinct_pair_no_collapse():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    # Orthogonal vectors — cosine = 0
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "protein bar", [0.0, 1.0, 0.0, 0.0])

    approved = [_kw("vape pen"), _kw("protein bar")]
    canonicals, aliases = collapse_near_duplicates(approved, conn, threshold=0.95)
    assert sorted(c["keyword"] for c in canonicals) == ["protein bar", "vape pen"]
    assert aliases == {}


def test_collapse_near_duplicates_picks_highest_opportunity_canonical():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.999, 0.001])
    _insert_keyword_embedding(conn, "c", [0.998, 0.002])

    approved = [
        _kw("a", opportunity=10.0),
        _kw("b", opportunity=50.0),  # highest → canonical
        _kw("c", opportunity=20.0),
    ]
    canonicals, aliases = collapse_near_duplicates(approved, conn, threshold=0.95)
    assert [c["keyword"] for c in canonicals] == ["b"]
    assert set(aliases["b"]) == {"a", "c"}


def test_collapse_near_duplicates_canonical_tiebreak_by_volume():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.999, 0.001])

    approved = [
        _kw("a", opportunity=50.0, volume=100),
        _kw("b", opportunity=50.0, volume=500),  # higher volume wins
    ]
    canonicals, _ = collapse_near_duplicates(approved, conn, threshold=0.95)
    assert [c["keyword"] for c in canonicals] == ["b"]


def test_collapse_near_duplicates_missing_embedding_stays_singleton():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.999, 0.001])
    # "c" has no embedding — should pass through as its own canonical.

    approved = [_kw("a"), _kw("b"), _kw("c")]
    canonicals, aliases = collapse_near_duplicates(approved, conn, threshold=0.95)

    names = sorted(c["keyword"] for c in canonicals)
    assert "c" in names
    # a and b collapse to one; c is its own entry → 2 canonicals total.
    assert len(canonicals) == 2


def test_collapse_near_duplicates_threshold_override_via_service_settings():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    from shopifyseo.dashboard_google import set_service_setting
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.85, 0.527])  # cosine ≈ 0.85

    # Default threshold (0.95) wouldn't merge these; override to 0.80.
    set_service_setting(conn, "clustering_dedupe_threshold", "0.80")

    approved = [_kw("a", opportunity=70.0), _kw("b", opportunity=40.0)]
    canonicals, aliases = collapse_near_duplicates(approved, conn)
    assert [c["keyword"] for c in canonicals] == ["a"]
    assert aliases == {"a": ["b"]}


def test_collapse_near_duplicates_threshold_of_one_disables():
    """Threshold ≥ 1.0 short-circuits; nothing collapses."""
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.999, 0.001])

    approved = [_kw("a"), _kw("b")]
    canonicals, aliases = collapse_near_duplicates(approved, conn, threshold=1.0)
    assert sorted(c["keyword"] for c in canonicals) == ["a", "b"]
    assert aliases == {}


def test_collapse_near_duplicates_bad_setting_falls_back_to_default():
    from backend.app.services.keyword_clustering._dedupe import collapse_near_duplicates
    from shopifyseo.dashboard_google import set_service_setting
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.999, 0.001])

    set_service_setting(conn, "clustering_dedupe_threshold", "not-a-number")

    approved = [_kw("a", opportunity=70.0), _kw("b", opportunity=40.0)]
    canonicals, aliases = collapse_near_duplicates(approved, conn)
    # Default (0.95) still collapses the near-identicals.
    assert [c["keyword"] for c in canonicals] == ["a"]


def test_generate_clusters_expands_aliases_into_cluster_keywords(monkeypatch):
    """LLM sees only the canonical; the cluster's output keywords include aliases."""
    import time as _time
    from backend.app.services.keyword_clustering import _generation
    from shopifyseo import market_context

    conn = _make_dedupe_db()
    now = int(_time.time())
    conn.executemany(
        "INSERT INTO keyword_metrics (keyword, volume, difficulty, opportunity, "
        "intent, content_type_label, parent_topic, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)",
        [
            ("vape pen", 500, 20, 90.0, "commercial", "Product / Collection page", "vape", now),
            ("vape pens", 300, 20, 60.0, "commercial", "Product / Collection page", "vape", now),
            ("protein bar", 400, 30, 80.0, "commercial", "Product / Collection page", "nutrition", now),
        ],
    )
    conn.commit()

    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "vape pens", [0.999, 0.001, 0.0])
    _insert_keyword_embedding(conn, "protein bar", [0.0, 0.0, 1.0])

    captured: dict[str, list] = {}

    def fake_call_ai(*, settings, provider, model, messages, timeout, json_schema, stage):
        captured.setdefault(stage, []).append(messages)
        if stage == "clustering":
            prompt_text = messages[1]["content"]
            out = []
            if "vape pen" in prompt_text:
                out.append({
                    "name": "Vape Pens",
                    "content_type": "collection_page",
                    "primary_keyword": "vape pen",
                    "content_brief": "brief",
                    "keywords": ["vape pen"],
                })
            if "protein bar" in prompt_text:
                out.append({
                    "name": "Protein Bars",
                    "content_type": "collection_page",
                    "primary_keyword": "protein bar",
                    "content_brief": "brief",
                    "keywords": ["protein bar"],
                })
            return {"clusters": out}
        return {"matches": []}

    monkeypatch.setattr(_generation, "_call_ai", fake_call_ai)
    monkeypatch.setattr(
        _generation, "ai_settings",
        lambda c: {"generation_provider": "anthropic", "generation_model": "claude-x", "timeout": 60},
    )
    monkeypatch.setattr(_generation, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(market_context, "get_primary_country_code", lambda c: "CA")
    monkeypatch.setattr(market_context, "country_display_name", lambda c: "Canadian")

    result = _generation.generate_clusters(conn)

    # Phase 3 may split into multiple buckets; find the prompt that mentions vape.
    vape_prompt = next(
        msgs[1]["content"] for msgs in captured["clustering"]
        if "vape pen" in msgs[1]["content"]
    )
    # LLM only saw the canonical, not the alias.
    assert "vape pens" not in vape_prompt

    vape_cluster = next(c for c in result["clusters"] if c["name"] == "Vape Pens")
    assert set(k.lower() for k in vape_cluster["keywords"]) == {"vape pen", "vape pens"}

    protein_cluster = next(c for c in result["clusters"] if c["name"] == "Protein Bars")
    assert [k.lower() for k in protein_cluster["keywords"]] == ["protein bar"]

    # Persisted cluster_keywords should also contain the alias.
    rows = conn.execute(
        "SELECT keyword FROM cluster_keywords "
        "JOIN clusters ON clusters.id = cluster_keywords.cluster_id "
        "WHERE clusters.name = 'Vape Pens' ORDER BY keyword"
    ).fetchall()
    assert [r[0] for r in rows] == ["vape pen", "vape pens"]
    conn.close()


# ---------------------------------------------------------------------------
# Phase 3: pre-clustering + parallel LLM chunking
# ---------------------------------------------------------------------------


def test_pre_cluster_under_two_returns_trivial():
    from backend.app.services.keyword_clustering._pre_cluster import pre_cluster
    conn = _make_dedupe_db()
    assert pre_cluster([], conn) == []
    single = [_kw("only", **{"parent_topic": "vape"})]
    assert pre_cluster(single, conn) == [single]


def test_pre_cluster_groups_by_parent_topic_without_embeddings():
    """With no embeddings, parent_topic groups become seed buckets + orphan bucket."""
    from backend.app.services.keyword_clustering._pre_cluster import pre_cluster
    conn = _make_dedupe_db()
    approved = [
        _kw("vape pen", parent_topic="vape"),
        _kw("vape mod", parent_topic="vape"),
        _kw("protein bar", parent_topic="nutrition"),
        _kw("random", parent_topic=""),  # orphan
    ]
    buckets = pre_cluster(approved, conn)
    bucket_keywords = [sorted(k["keyword"] for k in b) for b in buckets]
    # Expect 3 buckets: vape, nutrition, orphans
    assert sorted(bucket_keywords) == [
        ["protein bar"],
        ["random"],
        ["vape mod", "vape pen"],
    ]


def test_pre_cluster_assigns_orphan_to_best_centroid():
    """An orphan that's embedding-similar to a seed bucket joins it."""
    from backend.app.services.keyword_clustering._pre_cluster import pre_cluster
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "vape mod", [0.95, 0.05, 0.0])
    _insert_keyword_embedding(conn, "vape juice", [0.9, 0.1, 0.0])  # orphan, close to vape
    _insert_keyword_embedding(conn, "protein bar", [0.0, 0.0, 1.0])

    approved = [
        _kw("vape pen", parent_topic="vape"),
        _kw("vape mod", parent_topic="vape"),
        _kw("vape juice", parent_topic=""),
        _kw("protein bar", parent_topic="nutrition"),
    ]
    buckets = pre_cluster(approved, conn, assign_threshold=0.7)
    vape_bucket = next(b for b in buckets if any(k["keyword"] == "vape pen" for k in b))
    assert {"vape juice", "vape mod", "vape pen"} <= {k["keyword"] for k in vape_bucket}


def test_pre_cluster_merges_similar_orphans_into_new_bucket():
    """Orphans that don't match any seed but are similar to each other cluster together."""
    from backend.app.services.keyword_clustering._pre_cluster import pre_cluster
    conn = _make_dedupe_db()
    # Seed bucket (nutrition) — intentionally distant from the orphans.
    _insert_keyword_embedding(conn, "protein bar", [0.0, 0.0, 1.0])
    # Two orphans close to each other but far from "protein bar".
    _insert_keyword_embedding(conn, "wicker basket", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "wicker hamper", [0.95, 0.05, 0.0])

    approved = [
        _kw("protein bar", parent_topic="nutrition"),
        _kw("wicker basket", parent_topic=""),
        _kw("wicker hamper", parent_topic=""),
    ]
    buckets = pre_cluster(approved, conn, assign_threshold=0.7, merge_threshold=0.7)
    wicker_bucket = next(b for b in buckets if any(k["keyword"] == "wicker basket" for k in b))
    assert {k["keyword"] for k in wicker_bucket} == {"wicker basket", "wicker hamper"}


def test_pre_cluster_orphans_without_embeddings_fall_back_to_one_bucket():
    """Embedding-less orphans land together in a fallback bucket (legacy behavior)."""
    from backend.app.services.keyword_clustering._pre_cluster import pre_cluster
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0])
    # "stray-a" and "stray-b" have no embedding.
    approved = [
        _kw("vape pen", parent_topic="vape"),
        _kw("stray-a", parent_topic=""),
        _kw("stray-b", parent_topic=""),
    ]
    buckets = pre_cluster(approved, conn)
    fallback = next(b for b in buckets if any(k["keyword"] == "stray-a" for k in b))
    assert {k["keyword"] for k in fallback} == {"stray-a", "stray-b"}


def test_generate_clusters_parallel_calls_llm_per_bucket(monkeypatch):
    """Two parent_topic groups → two LLM clustering calls, each with its own payload."""
    import time as _time
    from backend.app.services.keyword_clustering import _generation
    from shopifyseo import market_context

    conn = _make_dedupe_db()
    now = int(_time.time())
    conn.executemany(
        "INSERT INTO keyword_metrics (keyword, volume, difficulty, opportunity, "
        "intent, content_type_label, parent_topic, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)",
        [
            ("vape pen", 500, 20, 90.0, "commercial", "collection_page", "vape", now),
            ("vape mod", 300, 20, 60.0, "commercial", "collection_page", "vape", now),
            ("protein bar", 400, 30, 80.0, "commercial", "collection_page", "nutrition", now),
            ("protein powder", 200, 30, 50.0, "commercial", "collection_page", "nutrition", now),
        ],
    )
    conn.commit()

    captured_prompts: list[str] = []

    def fake_call_ai(*, settings, provider, model, messages, timeout, json_schema, stage):
        if stage != "clustering":
            return {"matches": []}
        prompt_text = messages[1]["content"]
        captured_prompts.append(prompt_text)
        out = []
        if "vape" in prompt_text:
            out.append({
                "name": "Vape",
                "content_type": "collection_page",
                "primary_keyword": "vape pen",
                "content_brief": "brief",
                "keywords": ["vape pen", "vape mod"],
            })
        if "protein" in prompt_text:
            out.append({
                "name": "Protein",
                "content_type": "collection_page",
                "primary_keyword": "protein bar",
                "content_brief": "brief",
                "keywords": ["protein bar", "protein powder"],
            })
        return {"clusters": out}

    monkeypatch.setattr(_generation, "_call_ai", fake_call_ai)
    monkeypatch.setattr(
        _generation, "ai_settings",
        lambda c: {"generation_provider": "anthropic", "generation_model": "claude-x", "timeout": 60},
    )
    monkeypatch.setattr(_generation, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(market_context, "get_primary_country_code", lambda c: "CA")
    monkeypatch.setattr(market_context, "country_display_name", lambda c: "Canadian")

    result = _generation.generate_clusters(conn)

    # Two buckets → two clustering calls.
    assert len(captured_prompts) == 2
    # Prompts should be disjoint — each bucket only sees its own keywords.
    vape_prompt = next(p for p in captured_prompts if "vape pen" in p)
    protein_prompt = next(p for p in captured_prompts if "protein bar" in p)
    assert "protein bar" not in vape_prompt
    assert "vape pen" not in protein_prompt

    cluster_names = {c["name"] for c in result["clusters"]}
    assert cluster_names == {"Vape", "Protein"}
    conn.close()


def test_generate_clusters_cross_bucket_dedupe_by_primary_keyword(monkeypatch):
    """Same primary_keyword in two buckets → collapse, keeping higher-opportunity version."""
    import time as _time
    from backend.app.services.keyword_clustering import _generation
    from shopifyseo import market_context

    conn = _make_dedupe_db()
    now = int(_time.time())
    conn.executemany(
        "INSERT INTO keyword_metrics (keyword, volume, difficulty, opportunity, "
        "intent, content_type_label, parent_topic, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)",
        [
            ("vape pen", 500, 20, 90.0, "commercial", "collection_page", "topic-a", now),
            ("vape mod", 300, 20, 60.0, "commercial", "collection_page", "topic-a", now),
            ("shared primary", 400, 30, 80.0, "commercial", "collection_page", "topic-b", now),
            ("other term", 200, 30, 50.0, "commercial", "collection_page", "topic-b", now),
        ],
    )
    conn.commit()

    def fake_call_ai(*, settings, provider, model, messages, timeout, json_schema, stage):
        if stage != "clustering":
            return {"matches": []}
        prompt_text = messages[1]["content"]
        # Both buckets return a cluster with the SAME primary_keyword, different opportunity.
        if "vape pen" in prompt_text:
            return {"clusters": [{
                "name": "Topic A",
                "content_type": "collection_page",
                "primary_keyword": "vape pen",  # higher opportunity wins
                "content_brief": "brief",
                "keywords": ["vape pen", "vape mod"],
            }]}
        if "shared primary" in prompt_text:
            return {"clusters": [{
                "name": "Topic B",
                "content_type": "collection_page",
                "primary_keyword": "vape pen",  # COLLISION with bucket A
                "content_brief": "brief",
                "keywords": ["shared primary", "other term"],
            }]}
        return {"clusters": []}

    monkeypatch.setattr(_generation, "_call_ai", fake_call_ai)
    monkeypatch.setattr(
        _generation, "ai_settings",
        lambda c: {"generation_provider": "anthropic", "generation_model": "claude-x", "timeout": 60},
    )
    monkeypatch.setattr(_generation, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(market_context, "get_primary_country_code", lambda c: "CA")
    monkeypatch.setattr(market_context, "country_display_name", lambda c: "Canadian")

    result = _generation.generate_clusters(conn)

    # Dedupe by primary_keyword (lowercased) — only one "vape pen" cluster survives.
    primaries = [c["primary_keyword"].lower() for c in result["clusters"]]
    assert primaries.count("vape pen") == 1
    # Winner is the higher-opportunity cluster — avg_opportunity comes from keyword_metrics.
    winner = next(c for c in result["clusters"] if c["primary_keyword"].lower() == "vape pen")
    assert winner["avg_opportunity"] == 75.0  # mean(90, 60) from bucket A
    conn.close()


def test_generate_clusters_legacy_mode_single_llm_call(monkeypatch):
    """clustering_mode=legacy falls back to one LLM call over all canonicals."""
    import time as _time
    from backend.app.services.keyword_clustering import _generation
    from shopifyseo import market_context
    from shopifyseo.dashboard_google import set_service_setting

    conn = _make_dedupe_db()
    now = int(_time.time())
    conn.executemany(
        "INSERT INTO keyword_metrics (keyword, volume, difficulty, opportunity, "
        "intent, content_type_label, parent_topic, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)",
        [
            ("a", 500, 20, 90.0, "commercial", "collection_page", "t1", now),
            ("b", 400, 30, 80.0, "commercial", "collection_page", "t2", now),
        ],
    )
    conn.commit()

    set_service_setting(conn, "clustering_mode", "legacy")

    captured_prompts: list[str] = []

    def fake_call_ai(*, settings, provider, model, messages, timeout, json_schema, stage):
        if stage != "clustering":
            return {"matches": []}
        captured_prompts.append(messages[1]["content"])
        return {"clusters": [{
            "name": "All",
            "content_type": "collection_page",
            "primary_keyword": "a",
            "content_brief": "brief",
            "keywords": ["a", "b"],
        }]}

    monkeypatch.setattr(_generation, "_call_ai", fake_call_ai)
    monkeypatch.setattr(
        _generation, "ai_settings",
        lambda c: {"generation_provider": "anthropic", "generation_model": "claude-x", "timeout": 60},
    )
    monkeypatch.setattr(_generation, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(market_context, "get_primary_country_code", lambda c: "CA")
    monkeypatch.setattr(market_context, "country_display_name", lambda c: "Canadian")

    _generation.generate_clusters(conn)

    # Legacy mode: exactly one LLM call with both keywords in the same prompt.
    assert len(captured_prompts) == 1
    assert "a" in captured_prompts[0] and "b" in captured_prompts[0]
    conn.close()


# ---------------------------------------------------------------------------
# Post-processing: merge near-duplicate clusters + fold singletons
# ---------------------------------------------------------------------------


def _cluster(name: str, primary: str, keywords: list[str], avg_opportunity: float = 50.0) -> dict:
    return {
        "name": name,
        "primary_keyword": primary,
        "content_type": "collection_page",
        "content_brief": "",
        "keywords": list(keywords),
        "keyword_count": len(keywords),
        "total_volume": 100 * len(keywords),
        "avg_difficulty": 20.0,
        "avg_opportunity": avg_opportunity,
        "avg_cps": 0.0,
        "dominant_serp_features": "",
        "content_format_hints": "",
    }


def _km(keyword: str, opportunity: float = 50.0, volume: int = 100) -> dict:
    return {
        "keyword": keyword,
        "opportunity": opportunity,
        "volume": volume,
        "difficulty": 20,
    }


def test_merge_similar_clusters_collapses_near_identical_primaries():
    from backend.app.services.keyword_clustering._postprocess import merge_similar_clusters
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "geek bar", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "geek bars flavours", [0.99, 0.01, 0.0])
    _insert_keyword_embedding(conn, "arizer solo", [0.0, 1.0, 0.0])

    clusters = [
        _cluster("Geek Bar A", "geek bar", ["geek bar", "geek bar flavors"], avg_opportunity=90.0),
        _cluster("Geek Bar B", "geek bars flavours", ["geek bars flavours"], avg_opportunity=40.0),
        _cluster("Arizer", "arizer solo", ["arizer solo"], avg_opportunity=50.0),
    ]
    keywords_map = {
        "geek bar": _km("geek bar", 90.0),
        "geek bar flavors": _km("geek bar flavors", 80.0),
        "geek bars flavours": _km("geek bars flavours", 40.0),
        "arizer solo": _km("arizer solo", 50.0),
    }
    out = merge_similar_clusters(clusters, conn, keywords_map, threshold=0.85)

    # The two geek-bar clusters collapse into one winner (higher avg_opportunity).
    names = {c["name"] for c in out}
    assert "Geek Bar A" in names
    assert "Geek Bar B" not in names
    assert "Arizer" in names

    winner = next(c for c in out if c["name"] == "Geek Bar A")
    assert set(k.lower() for k in winner["keywords"]) == {
        "geek bar", "geek bar flavors", "geek bars flavours"
    }


def test_merge_similar_clusters_leaves_distinct_primaries_alone():
    from backend.app.services.keyword_clustering._postprocess import merge_similar_clusters
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "protein bar", [0.0, 0.0, 1.0])

    clusters = [
        _cluster("Vape", "vape pen", ["vape pen"]),
        _cluster("Protein", "protein bar", ["protein bar"]),
    ]
    keywords_map = {"vape pen": _km("vape pen"), "protein bar": _km("protein bar")}
    out = merge_similar_clusters(clusters, conn, keywords_map, threshold=0.85)
    assert {c["name"] for c in out} == {"Vape", "Protein"}


def test_merge_similar_clusters_missing_embeddings_passthrough():
    from backend.app.services.keyword_clustering._postprocess import merge_similar_clusters
    conn = _make_dedupe_db()
    # No embeddings inserted — cluster primaries have no vectors.
    clusters = [
        _cluster("A", "foo", ["foo"]),
        _cluster("B", "foo 2", ["foo 2"]),
    ]
    keywords_map = {"foo": _km("foo"), "foo 2": _km("foo 2")}
    out = merge_similar_clusters(clusters, conn, keywords_map, threshold=0.85)
    assert {c["name"] for c in out} == {"A", "B"}


def test_merge_similar_clusters_transitive_chain_merges_all():
    """A~B, B~C, A not directly ~C → all three collapse via union-find."""
    from backend.app.services.keyword_clustering._postprocess import merge_similar_clusters
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "b", [0.93, 0.37])
    _insert_keyword_embedding(conn, "c", [0.75, 0.66])  # sim(a,c) < 0.85, sim(b,c) ≥ 0.85

    clusters = [
        _cluster("A", "a", ["a"], avg_opportunity=90.0),
        _cluster("B", "b", ["b"], avg_opportunity=70.0),
        _cluster("C", "c", ["c"], avg_opportunity=50.0),
    ]
    keywords_map = {k: _km(k) for k in ["a", "b", "c"]}
    out = merge_similar_clusters(clusters, conn, keywords_map, threshold=0.85)
    assert len(out) == 1
    assert set(k.lower() for k in out[0]["keywords"]) == {"a", "b", "c"}


def test_fold_singletons_folds_into_similar_cluster():
    from backend.app.services.keyword_clustering._postprocess import fold_singletons
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "vape pens", [0.98, 0.02, 0.0])
    _insert_keyword_embedding(conn, "vape mod", [0.95, 0.05, 0.0])
    _insert_keyword_embedding(conn, "yoga mat", [0.0, 0.0, 1.0])

    clusters = [
        _cluster("Vapes", "vape pen", ["vape pen", "vape pens"]),  # size 2, target
        _cluster("Solo Mod", "vape mod", ["vape mod"]),  # singleton, should fold
        _cluster("Yoga", "yoga mat", ["yoga mat"]),  # singleton, no similar neighbor
    ]
    keywords_map = {k: _km(k) for k in ["vape pen", "vape pens", "vape mod", "yoga mat"]}
    out = fold_singletons(clusters, conn, keywords_map, threshold=0.75)
    names = {c["name"] for c in out}
    assert "Vapes" in names
    assert "Solo Mod" not in names  # absorbed
    assert "Yoga" in names  # stays — no similar target

    vapes = next(c for c in out if c["name"] == "Vapes")
    assert set(k.lower() for k in vapes["keywords"]) == {"vape pen", "vape pens", "vape mod"}


def test_fold_singletons_keeps_unique_singletons():
    """Singleton with no similar non-singleton neighbor stays as-is."""
    from backend.app.services.keyword_clustering._postprocess import fold_singletons
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0])
    _insert_keyword_embedding(conn, "yoga mat", [0.0, 1.0])

    clusters = [
        _cluster("Vapes", "vape pen", ["vape pen", "other"]),  # size 2, target
        _cluster("Yoga", "yoga mat", ["yoga mat"]),  # singleton, too far
    ]
    keywords_map = {k: _km(k) for k in ["vape pen", "other", "yoga mat"]}
    out = fold_singletons(clusters, conn, keywords_map, threshold=0.75)
    assert {c["name"] for c in out} == {"Vapes", "Yoga"}


def test_fold_singletons_two_similar_singletons_no_target_both_stay():
    """Two singletons similar to each other but no non-singleton target → neither folds."""
    from backend.app.services.keyword_clustering._postprocess import fold_singletons
    conn = _make_dedupe_db()
    _insert_keyword_embedding(conn, "a", [1.0, 0.0])
    _insert_keyword_embedding(conn, "a2", [0.99, 0.01])

    clusters = [
        _cluster("A", "a", ["a"]),
        _cluster("A2", "a2", ["a2"]),
    ]
    keywords_map = {"a": _km("a"), "a2": _km("a2")}
    out = fold_singletons(clusters, conn, keywords_map, threshold=0.75)
    assert {c["name"] for c in out} == {"A", "A2"}


def test_fold_singletons_missing_embedding_passthrough():
    from backend.app.services.keyword_clustering._postprocess import fold_singletons
    conn = _make_dedupe_db()
    clusters = [
        _cluster("A", "a", ["a", "a2"]),
        _cluster("B", "b", ["b"]),
    ]
    keywords_map = {"a": _km("a"), "a2": _km("a2"), "b": _km("b")}
    out = fold_singletons(clusters, conn, keywords_map, threshold=0.75)
    assert {c["name"] for c in out} == {"A", "B"}


def test_generate_clusters_runs_post_processing(monkeypatch):
    """End-to-end: duplicate-primary + singleton both get cleaned up."""
    import time as _time
    from backend.app.services.keyword_clustering import _generation
    from shopifyseo import market_context

    conn = _make_dedupe_db()
    now = int(_time.time())
    conn.executemany(
        "INSERT INTO keyword_metrics (keyword, volume, difficulty, opportunity, "
        "intent, content_type_label, parent_topic, status, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?)",
        [
            ("vape pen", 500, 20, 90.0, "commercial", "collection_page", "t1", now),
            ("vape pens", 300, 20, 80.0, "commercial", "collection_page", "t1", now),
            ("vape mod", 200, 20, 40.0, "commercial", "collection_page", "t2", now),
            ("protein bar", 400, 30, 70.0, "commercial", "collection_page", "t3", now),
        ],
    )
    conn.commit()
    # vape pen / vape pens sit above the 0.95 dedupe threshold → collapse together.
    _insert_keyword_embedding(conn, "vape pen", [1.0, 0.0, 0.0])
    _insert_keyword_embedding(conn, "vape pens", [0.99, 0.01, 0.0])
    # vape mod sits ABOVE fold (0.75) but BELOW merge (0.85) and dedupe (0.95) vs vape pen.
    _insert_keyword_embedding(conn, "vape mod", [0.80, 0.60, 0.0])
    _insert_keyword_embedding(conn, "protein bar", [0.0, 0.0, 1.0])

    def fake_call_ai(*, settings, provider, model, messages, timeout, json_schema, stage):
        if stage != "clustering":
            return {"matches": []}
        prompt_text = messages[1]["content"]
        out = []
        # bucket t1 (vape pen / vape pens) returns a cluster
        if "vape pen" in prompt_text:
            out.append({
                "name": "Vapes",
                "content_type": "collection_page",
                "primary_keyword": "vape pen",
                "content_brief": "",
                "keywords": ["vape pen", "vape pens"],
            })
        # bucket t2 (vape mod) returns a singleton with a near-duplicate primary
        if "vape mod" in prompt_text:
            out.append({
                "name": "Vape Mod",
                "content_type": "collection_page",
                "primary_keyword": "vape mod",
                "content_brief": "",
                "keywords": ["vape mod"],
            })
        # bucket t3 (protein bar) — topically unrelated, stays alone
        if "protein bar" in prompt_text:
            out.append({
                "name": "Protein",
                "content_type": "collection_page",
                "primary_keyword": "protein bar",
                "content_brief": "",
                "keywords": ["protein bar"],
            })
        return {"clusters": out}

    monkeypatch.setattr(_generation, "_call_ai", fake_call_ai)
    monkeypatch.setattr(
        _generation, "ai_settings",
        lambda c: {"generation_provider": "anthropic", "generation_model": "claude-x", "timeout": 60},
    )
    monkeypatch.setattr(_generation, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(market_context, "get_primary_country_code", lambda c: "CA")
    monkeypatch.setattr(market_context, "country_display_name", lambda c: "Canadian")

    result = _generation.generate_clusters(conn)
    names = {c["name"] for c in result["clusters"]}

    # "Vape Mod" singleton should be folded into the vape cluster.
    assert "Vape Mod" not in names
    # The vape cluster survives and now contains vape mod too.
    vapes = next(c for c in result["clusters"] if "vape pen" in [k.lower() for k in c["keywords"]])
    assert "vape mod" in [k.lower() for k in vapes["keywords"]]
    # Protein (semantically distant) stays as its own cluster even though singleton.
    assert "Protein" in names
    conn.close()
