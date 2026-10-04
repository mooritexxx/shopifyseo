import hashlib
import sqlite3
import pytest
from internal_links_support import BASE, OLD, Shopify, apply, database
from shopifyseo.dashboard_store import _migrate_link_suggestions_check_constraint
from shopifyseo.internal_links.apply import undo_suggestion, reconcile_suggestion, remove_link_from_html
from shopifyseo.internal_links.safety import LinkConflict, body_hash as _hash_body


def test_undo_restores_exact_snapshot_and_local_body():
    conn=database(); live=Shopify(OLD + '<img src="/x.jpg">')
    before=live.body
    apply(conn,live)
    result=undo_suggestion(conn,1,BASE,fetch_fn=live.fetch,push_fn=live.push)
    assert result['status']=='undone' and live.body==before
    assert conn.execute('SELECT description_html FROM products').fetchone()[0]==before
    assert conn.execute('SELECT COUNT(*) FROM internal_links').fetchone()[0]==0


def test_undo_blocks_later_edits():
    conn=database(); live=Shopify(); apply(conn,live)
    live.body += '<p>Newer work.</p>'
    with pytest.raises(LinkConflict,match='newer work'):
        undo_suggestion(conn,1,BASE,fetch_fn=live.fetch,push_fn=live.push)
    assert live.push.call_count==1
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0]=='applied'


def test_legacy_undo_without_backup_cannot_overwrite_live():
    conn=database(); live=Shopify()
    conn.execute("UPDATE link_suggestions SET status='applied'"); conn.commit()
    with pytest.raises(LinkConflict,match='no backup'):
        undo_suggestion(conn,1,BASE,fetch_fn=live.fetch,push_fn=live.push)
    live.push.assert_not_called()


def test_undo_timeout_can_be_reconciled_without_second_write():
    conn=database(); live=Shopify(); apply(conn,live)
    def timeout(*args): live._push(*args); raise TimeoutError()
    live.push.side_effect=timeout
    with pytest.raises(TimeoutError): undo_suggestion(conn,1,BASE,fetch_fn=live.fetch,push_fn=live.push)
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0]=='undo_needs_reconciliation'
    assert reconcile_suggestion(conn,1,BASE,fetch_fn=live.fetch)['status']=='undone'
    assert live.push.call_count==2 and live.body==OLD


def test_undo_tolerates_entity_normalization():
    """Undo proceeds when live body differs only by entity encoding (&#x27; vs ')."""
    conn = database()
    live = Shopify()
    
    # Apply the link
    apply(conn, live)
    applied_body = live.body
    
    # Simulate Shopify normalizing &#x27; to ' in the applied body
    # The stored new_body has the link, but live has normalized entities
    normalized_body = applied_body.replace("'", "&#x27;")  # Shopify might return either form
    
    # Actually, test the reverse: stored has entities, live has decoded
    # Insert an apostrophe into the body and then test both directions
    
    # Let's modify the stored snapshot to have entities
    conn.execute(
        "UPDATE link_body_snapshots SET new_body = replace(new_body, \"'\", '&#x27;')"
    )
    conn.commit()
    
    # Live has decoded apostrophes (natural in browser)
    # The stored snapshot has &#x27; but live has '
    # This should still allow undo because html_equivalent normalizes entities
    
    result = undo_suggestion(conn, 1, BASE, fetch_fn=live.fetch, push_fn=live.push)
    assert result['status'] == 'undone'


def test_undo_still_blocks_real_content_changes():
    """Undo is still blocked when there are real content changes, not just entity encoding."""
    conn = database()
    live = Shopify()
    
    # Apply the link
    apply(conn, live)
    
    # Simulate real content change (not just entity normalization)
    live.body = live.body.replace('ceramic tanks', 'MODIFIED CONTENT')
    
    with pytest.raises(LinkConflict, match='newer work'):
        undo_suggestion(conn, 1, BASE, fetch_fn=live.fetch, push_fn=live.push)
    
    # Snapshot should remain in 'applied' state
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0] == 'applied'


def _conn_with_old_check_constraint() -> sqlite3.Connection:
    """Create a DB with the OLD CHECK constraint (without 'undone').
    
    This simulates a production DB created before PR #24 added the 'undone' status.
    """
    body = '<p>Love <a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>.</p>'
    body_hash = _hash_body(body)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Use the OLD schema that doesn't include 'undone' in the CHECK
    conn.executescript(
        """
        CREATE TABLE blog_articles (shopify_id TEXT, blog_handle TEXT, handle TEXT, title TEXT,
            body TEXT, is_published INTEGER DEFAULT 1, seo_title TEXT, seo_description TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE products (shopify_id TEXT, handle TEXT, title TEXT, status TEXT,
            description_html TEXT, seo_title TEXT, seo_description TEXT, tags_json TEXT DEFAULT '[]',
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE collections (shopify_id TEXT, handle TEXT, title TEXT, description_html TEXT,
            seo_title TEXT, seo_description TEXT, gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
        CREATE TABLE pages (shopify_id TEXT, handle TEXT, title TEXT, body TEXT,
            gsc_clicks INTEGER DEFAULT 0, gsc_impressions INTEGER DEFAULT 0);
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
        CREATE INDEX idx_link_suggestions_status ON link_suggestions (status, score);
        """
    )
    conn.execute(
        "INSERT INTO blog_articles (shopify_id, blog_handle, handle, title, body, seo_title, seo_description) "
        "VALUES ('gid://shopify/Article/1', 'news', 'post', 'Post', ?, 'st', 'sd')",
        (body,),
    )
    conn.execute("INSERT INTO collections (handle, title) VALUES ('ceramic-tanks', 'Ceramic Tanks')")
    conn.execute(
        "INSERT INTO link_suggestions (source_type, source_handle, target_type, target_handle, kind, "
        "anchor_phrase, source_body_hash, score, status, created_at, applied_at) VALUES "
        "('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'phrase_wrap', 'ceramic tanks', ?, 1.0, 'applied', 1, 1000)",
        (body_hash,),
    )
    conn.execute(
        "INSERT INTO internal_links (source_type, source_handle, target_type, target_handle, anchor_text, href) "
        "VALUES ('blog_article', 'news/post', 'collection', 'ceramic-tanks', 'ceramic tanks', 'https://s.com/collections/ceramic-tanks')"
    )
    conn.commit()
    return conn


def test_migration_adds_undone_to_check_constraint():
    """Test that _migrate_link_suggestions_check_constraint correctly updates the CHECK."""
    conn = _conn_with_old_check_constraint()
    
    # Verify old schema doesn't have 'undone'
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='link_suggestions'"
    ).fetchone()["sql"]
    assert "'undone'" not in table_sql
    
    # Run migration
    migrated = _migrate_link_suggestions_check_constraint(conn)
    assert migrated is True
    
    # Verify new schema has 'undone'
    table_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='link_suggestions'"
    ).fetchone()["sql"]
    assert "'undone'" in table_sql
    
    # Verify data is preserved
    row = conn.execute("SELECT * FROM link_suggestions WHERE status = 'applied'").fetchone()
    assert row is not None
    assert row["source_handle"] == "news/post"
    assert row["target_handle"] == "ceramic-tanks"
    
    # Verify index still exists
    idx = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_link_suggestions_status'"
    ).fetchone()
    assert idx is not None


def test_migration_is_idempotent():
    """Test that running migration multiple times is safe."""
    conn = _conn_with_old_check_constraint()
    
    # Run migration first time
    migrated1 = _migrate_link_suggestions_check_constraint(conn)
    assert migrated1 is True
    
    # Run migration second time - should be no-op
    migrated2 = _migrate_link_suggestions_check_constraint(conn)
    assert migrated2 is False
    
    # Data should still be there
    row = conn.execute("SELECT * FROM link_suggestions WHERE status = 'applied'").fetchone()
    assert row is not None


def test_migration_skips_when_table_missing():
    """Test that migration handles missing table gracefully."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    
    # No link_suggestions table
    migrated = _migrate_link_suggestions_check_constraint(conn)
    assert migrated is False
