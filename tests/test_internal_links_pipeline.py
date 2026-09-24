"""Tests for the link suggestion pipeline: suppression, scoring, caps."""

import sqlite3

from shopifyseo.internal_links.pipeline import generate_link_suggestions


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]');
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT,
            description_html TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0,
            seo_title TEXT, seo_description TEXT, api_unreachable INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0, seo_title TEXT, seo_description TEXT);
        CREATE TABLE internal_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            anchor_text TEXT,
            href TEXT,
            UNIQUE (source_type, source_handle, target_type, target_handle, href)
        );
        CREATE TABLE link_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_type TEXT NOT NULL,
            source_handle TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_handle TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('phrase_wrap', 'ai_woven')),
            anchor_phrase TEXT,
            ai_anchor_html TEXT,
            source_body_hash TEXT,
            score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'suggested'
                CHECK (status IN ('suggested', 'applied', 'dismissed')),
            created_at INTEGER NOT NULL,
            applied_at INTEGER,
            UNIQUE (source_type, source_handle, target_type, target_handle)
        );
        CREATE TABLE keyword_page_map (
            keyword TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_handle TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'gsc',
            gsc_clicks INTEGER DEFAULT 0,
            gsc_impressions INTEGER DEFAULT 0,
            gsc_position REAL,
            is_primary INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (keyword, object_type, object_handle)
        );
        CREATE TABLE service_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, content_type TEXT,
            primary_keyword TEXT, content_brief TEXT, total_volume INTEGER DEFAULT 0,
            avg_difficulty REAL DEFAULT 0, avg_opportunity REAL DEFAULT 0, priority_score REAL DEFAULT 0,
            match_type TEXT, match_handle TEXT, match_title TEXT, generated_at TEXT
        );
        CREATE TABLE cluster_keywords (cluster_id INTEGER, keyword TEXT, PRIMARY KEY (cluster_id, keyword));
        """
    )
    return conn


def _seed(conn):
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('gid://shopify/Article/1', 'news', 'post', 'Post', '<p>All about ceramic tanks and more.</p>', 100)"
    )
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title) VALUES "
        "('gid://shopify/Collection/1', 'ceramic-tanks', 'Ceramic Tanks')"
    )
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status) VALUES "
        "('gid://shopify/Product/1', 'widget', 'Widget Pro', 'ACTIVE')"
    )
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status) VALUES "
        "('gid://shopify/Product/2', 'hidden', 'Hidden', 'DRAFT')"
    )
    conn.commit()


def _fake_related(conn, object_type, handle, top_k=10, type_quotas=None):
    if (object_type, handle) == ("blog_article", "news/post"):
        return [
            {"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9},
            {"object_type": "product", "object_handle": "widget", "score": 0.8},
            {"object_type": "product", "object_handle": "hidden", "score": 0.95},
            {"object_type": "product", "object_handle": "low-sim", "score": 0.2},
        ]
    return []


def test_generates_phrase_wrap_and_ai_woven_and_suppresses():
    conn = _conn()
    _seed(conn)
    n = generate_link_suggestions(conn, related_fn=_fake_related)
    rows = conn.execute(
        "SELECT * FROM link_suggestions ORDER BY score DESC"
    ).fetchall()
    handles = {(r["target_type"], r["target_handle"]) for r in rows}
    # draft product and below-threshold target suppressed
    assert ("product", "hidden") not in handles
    assert ("product", "low-sim") not in handles
    assert n == len(rows) == 2
    by_target = {r["target_handle"]: r for r in rows}
    # 'Ceramic Tanks' title appears in body text -> phrase_wrap
    assert by_target["ceramic-tanks"]["kind"] == "phrase_wrap"
    assert by_target["ceramic-tanks"]["anchor_phrase"] == "ceramic tanks"
    # 'Widget Pro' does not appear -> ai_woven placeholder
    assert by_target["widget"]["kind"] == "ai_woven"
    assert by_target["widget"]["anchor_phrase"] is None


def test_existing_link_and_dismissed_are_suppressed_and_rerun_is_stable():
    conn = _conn()
    _seed(conn)
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', '/collections/ceramic-tanks')"
    )
    # Pass rebuild_graph=False to keep the manually seeded internal_link
    generate_link_suggestions(conn, related_fn=_fake_related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle, status FROM link_suggestions").fetchall()
    assert [r["target_handle"] for r in rows] == ["widget"]
    conn.execute("UPDATE link_suggestions SET status = 'dismissed' WHERE target_handle = 'widget'")
    conn.commit()
    # Second run: dismissed suggestion should remain with its status, not be re-created
    generate_link_suggestions(conn, related_fn=_fake_related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle, status FROM link_suggestions").fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "dismissed"


def test_traffic_weighted_scoring_orders_high_traffic_sources_first():
    conn = _conn()
    _seed(conn)
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('gid://shopify/Article/2', 'news', 'quiet', 'Quiet', '<p>ceramic tanks here too</p>', 0)"
    )
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if object_type == "blog_article":
            return [{"object_type": "collection", "object_handle": "ceramic-tanks", "score": 0.9}]
        return []

    generate_link_suggestions(conn, related_fn=related)
    rows = conn.execute(
        "SELECT source_handle, score FROM link_suggestions ORDER BY score DESC"
    ).fetchall()
    assert rows[0]["source_handle"] == "news/post"  # 100 clicks beats 0 clicks
    assert rows[0]["score"] > rows[1]["score"]


def test_api_unreachable_collection_targets_are_skipped():
    """Collections marked as api_unreachable should not be suggested as targets."""
    conn = _conn()
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('gid://shopify/Article/1', 'news', 'post', 'Post', '<p>All about unreachable collection and more.</p>', 100)"
    )
    # API-unreachable collection: has shopify_id but marked as unreachable
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, api_unreachable) VALUES "
        "('gid://shopify/Collection/999', 'unreachable-collection', 'Unreachable Collection', 1)"
    )
    # Valid collection: has shopify_id and is reachable
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, api_unreachable) VALUES "
        "('gid://shopify/Collection/123', 'valid-collection', 'Valid Collection', 0)"
    )
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if (object_type, handle) == ("blog_article", "news/post"):
            return [
                {"object_type": "collection", "object_handle": "unreachable-collection", "score": 0.95},
                {"object_type": "collection", "object_handle": "valid-collection", "score": 0.85},
            ]
        return []

    n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle FROM link_suggestions").fetchall()
    handles = [r["target_handle"] for r in rows]
    # API-unreachable collection should be skipped, valid one should be suggested
    assert "unreachable-collection" not in handles
    assert "valid-collection" in handles
    assert n == 1


def test_null_shopify_id_collection_targets_are_skipped():
    """Collections with NULL shopify_id should not be suggested as targets."""
    conn = _conn()
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('gid://shopify/Article/1', 'news', 'post', 'Post', '<p>All about collections.</p>', 100)"
    )
    # Collection with NULL shopify_id
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title) VALUES (NULL, 'no-id-collection', 'NoId Collection')"
    )
    # Valid collection: has shopify_id
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title) VALUES ('gid://shopify/Collection/123', 'valid-collection', 'Valid Collection')"
    )
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if (object_type, handle) == ("blog_article", "news/post"):
            return [
                {"object_type": "collection", "object_handle": "no-id-collection", "score": 0.95},
                {"object_type": "collection", "object_handle": "valid-collection", "score": 0.85},
            ]
        return []

    n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle FROM link_suggestions").fetchall()
    handles = [r["target_handle"] for r in rows]
    assert "no-id-collection" not in handles
    assert "valid-collection" in handles
    assert n == 1


def test_archived_product_targets_are_skipped():
    """Products with ARCHIVED status should not be suggested as targets."""
    conn = _conn()
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, gsc_clicks) VALUES "
        "('gid://shopify/Article/1', 'news', 'post', 'Post', '<p>All about products.</p>', 100)"
    )
    # Archived product: has shopify_id but ARCHIVED status
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status) VALUES ('gid://shopify/Product/1', 'archived-product', 'Archived', 'ARCHIVED')"
    )
    # Valid product: has shopify_id and ACTIVE status
    conn.execute(
        "INSERT INTO products (shopify_id, handle, title, status) VALUES ('gid://shopify/Product/2', 'valid-product', 'Valid Product', 'ACTIVE')"
    )
    conn.commit()

    def related(conn_, object_type, handle, top_k=10, type_quotas=None):
        if (object_type, handle) == ("blog_article", "news/post"):
            return [
                {"object_type": "product", "object_handle": "archived-product", "score": 0.90},
                {"object_type": "product", "object_handle": "valid-product", "score": 0.85},
            ]
        return []

    n = generate_link_suggestions(conn, related_fn=related, rebuild_graph=False)
    rows = conn.execute("SELECT target_handle FROM link_suggestions").fetchall()
    handles = [r["target_handle"] for r in rows]
    # Archived products should be skipped
    assert "archived-product" not in handles
    assert "valid-product" in handles
    assert n == 1


def test_orphan_list_excludes_api_unreachable_collections():
    """Orphan target list should not include api_unreachable collections."""
    from shopifyseo.internal_links.pipeline import _orphan_targets

    conn = _conn()
    # API-unreachable collection
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, gsc_clicks, api_unreachable) VALUES "
        "('gid://shopify/Collection/999', 'unreachable-collection', 'Unreachable', 50, 1)"
    )
    # Valid collection: has shopify_id and is reachable
    conn.execute(
        "INSERT INTO collections (shopify_id, handle, title, gsc_clicks, api_unreachable) VALUES "
        "('gid://shopify/Collection/123', 'valid-collection', 'Valid', 30, 0)"
    )
    conn.commit()

    orphans = _orphan_targets(conn)
    handles = [h for _, h, _, _ in orphans]
    assert "unreachable-collection" not in handles
    assert "valid-collection" in handles
