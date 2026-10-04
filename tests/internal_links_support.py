import sqlite3
from unittest.mock import Mock

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links.apply import preview_suggestion, apply_suggestion

BASE = 'https://s.com'
# OLD must have sentences that are 40+ chars for locator matching
# First sentence: 42 chars, Second sentence: 52 chars
OLD = '<p>We love ceramic tanks and all they offer.</p><p>This is the original second sentence with more text.</p>'


def database(path=':memory:'):
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)
    conn.execute("INSERT INTO products (shopify_id, handle, title, status, description_html,tags_json,options_json,raw_json,synced_at) VALUES ('gid://shopify/Product/1','source','Source','ACTIVE',?,'[]','[]','{}','now')", (OLD,))
    conn.execute("INSERT INTO collections (shopify_id, handle, title,raw_json,synced_at) VALUES ('gid://shopify/Collection/2','ceramic-tanks','Ceramic Tanks','{}','now')")
    conn.execute("INSERT INTO link_suggestions (source_type,source_handle,target_type,target_handle,kind,anchor_phrase,created_at) VALUES ('product','source','collection','ceramic-tanks','phrase_wrap','ceramic tanks',1)")
    conn.commit()
    return conn


class Shopify:
    def __init__(self, body=OLD):
        self.body = body
        self.fetch = Mock(side_effect=lambda *_: self.body)
        self.push = Mock(side_effect=self._push)

    def _push(self, source_type, row, body):
        self.body = body
        return body


def preview(conn, live, sid=1):
    return preview_suggestion(conn, sid, BASE, fetch_fn=live.fetch)


def apply(conn, live, sid=1, token=None):
    if token is None:
        token = preview(conn, live, sid)['preview_token']
    return apply_suggestion(conn, sid, BASE, preview_token_value=token, fetch_fn=live.fetch, push_fn=live.push)
