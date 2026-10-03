"""Durable backups and write reservations for internal-link body edits."""


def ensure_schema(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(link_suggestions)")}
    if "ai_edit_json" not in columns:
        conn.execute("ALTER TABLE link_suggestions ADD COLUMN ai_edit_json TEXT")
        # Legacy whole-body responses must never become applicable again.
        conn.execute("UPDATE link_suggestions SET ai_anchor_html = NULL WHERE kind = 'ai_woven'")
    conn.execute("""CREATE TABLE IF NOT EXISTS link_body_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        suggestion_id INTEGER NOT NULL,
        source_type TEXT NOT NULL,
        source_handle TEXT NOT NULL,
        shopify_id TEXT NOT NULL,
        old_body TEXT NOT NULL,
        new_body TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN
          ('prepared','applied','failed','needs_reconciliation','undo_prepared','undo_needs_reconciliation','undone')),
        error TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_link_body_active_write
        ON link_body_snapshots(source_type, shopify_id)
        WHERE status IN ('prepared','needs_reconciliation','undo_prepared','undo_needs_reconciliation')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_link_body_suggestion ON link_body_snapshots(suggestion_id, id DESC)")
    
    # Restore audit table: tracks when dismissed suggestions are restored
    # B8: No FOREIGN KEY - audit is append-only and must not block deletes
    conn.execute("""CREATE TABLE IF NOT EXISTS link_suggestion_restore_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        suggestion_id INTEGER NOT NULL,
        restored_at INTEGER NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_restore_audit_suggestion ON link_suggestion_restore_audit(suggestion_id)")
