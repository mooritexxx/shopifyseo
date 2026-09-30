"""Safety regressions use real local schemas and mocked Shopify transport only."""
import json
import sqlite3
from unittest.mock import Mock

import pytest

from internal_links_support import BASE, OLD, Shopify, apply, database, preview
from shopifyseo.internal_links import apply as service, shopify_io
from shopifyseo.internal_links.apply import wrap_phrase_in_html
from shopifyseo.internal_links.safety import LinkConflict, build_edit, guard_edit, body_hash


def test_wrap_phrase_wraps_first_eligible_occurrence_only():
    html = '<h2>ceramic tanks</h2><!-- ceramic tanks --><p title="ceramic tanks"><a href="/x">ceramic tanks</a> Love ceramic tanks. More ceramic tanks.</p>'
    new = wrap_phrase_in_html(html, 'ceramic tanks', BASE + '/collections/ceramic-tanks')
    assert new == html.replace('Love ceramic tanks.', 'Love <a href="https://s.com/collections/ceramic-tanks">ceramic tanks</a>.')
    assert wrap_phrase_in_html('<p>nothing</p>', 'ceramic tanks', 'u') is None


def test_preview_has_no_writes_and_apply_uses_live_html():
    conn = database()
    live = Shopify(OLD + '<img src="/live.jpg"><a href="https://external.example/source">Reference</a>')
    before = conn.total_changes
    plan = preview(conn, live)
    assert conn.total_changes == before and plan['allowed'] and not plan['text_diff']
    live.push.assert_not_called()
    result = apply(conn, live, token=plan['preview_token'])
    assert result['status'] == 'applied'
    assert live.body == plan['new_html']
    assert 'live.jpg' in live.body and 'https://external.example/source' in live.body
    assert conn.execute('SELECT description_html FROM products').fetchone()[0] == live.body
    assert conn.execute('SELECT COUNT(*) FROM internal_links').fetchone()[0] == 1


def test_snapshot_committed_before_push_and_no_transaction_during_network(tmp_path):
    path = tmp_path / 'backup.sqlite'
    conn = database(path)
    live = Shopify()
    def push(*args):
        assert not conn.in_transaction
        other = sqlite3.connect(path)
        backup = other.execute('SELECT old_body,status FROM link_body_snapshots').fetchone()
        other.close()
        assert backup == (OLD, 'prepared')
        return live._push(*args)
    live.push.side_effect = push
    apply(conn, live)


@pytest.mark.parametrize('change', ['body', 'token', 'suggestion', 'target', 'expired'])
def test_stale_or_tampered_preview_is_rejected(change, monkeypatch):
    conn = database()
    live = Shopify()
    token = preview(conn, live)['preview_token']
    if change == 'body': live.body += '<p>New live work.</p>'
    if change == 'token': token += 'x'
    if change == 'suggestion': conn.execute("UPDATE link_suggestions SET anchor_phrase='Original'"); conn.commit()
    if change == 'target': conn.execute("UPDATE link_suggestions SET target_handle='missing'"); conn.commit()
    if change == 'expired': monkeypatch.setattr('shopifyseo.internal_links.safety.time.time', lambda: 9999999999)
    with pytest.raises(LinkConflict): apply(conn, live, token=token)
    live.push.assert_not_called()


def test_apply_requires_preview_even_with_mock_writer():
    conn = database(); live = Shopify()
    with pytest.raises(LinkConflict, match='Preview'):
        service.apply_suggestion(conn, 1, BASE, fetch_fn=live.fetch, push_fn=live.push)
    live.push.assert_not_called()


def test_live_changes_after_reservation_block_push():
    conn = database(); live = Shopify()
    token = preview(conn, live)['preview_token']
    live.fetch.side_effect = [OLD, OLD + '<p>Concurrent edit</p>']
    with pytest.raises(LinkConflict, match='changed'): apply(conn, live, token=token)
    live.push.assert_not_called()
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0] == 'failed'


def test_suggestion_changed_during_final_live_read_blocks_push():
    conn = database()
    live = Shopify()
    token = preview(conn, live)['preview_token']
    live.fetch.reset_mock()

    def fetch(*_):
        if live.fetch.call_count == 2:
            conn.execute("UPDATE link_suggestions SET anchor_phrase = 'Original'")
            conn.commit()
        return OLD

    live.fetch.side_effect = fetch
    with pytest.raises(LinkConflict, match='Suggestion changed'):
        apply(conn, live, token=token)
    live.push.assert_not_called()


def test_changed_catalog_identity_keeps_backup_without_overwriting_local_body():
    conn = database()
    live = Shopify()

    def push(*args):
        conn.execute("UPDATE products SET shopify_id = 'gid://shopify/Product/999'")
        conn.commit()
        return live._push(*args)

    live.push.side_effect = push
    with pytest.raises(LinkConflict, match='identity changed'):
        apply(conn, live)
    assert conn.execute('SELECT description_html FROM products').fetchone()[0] == OLD
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0] == 'needs_reconciliation'


@pytest.mark.parametrize('replacement', ['<p>Reworded.</p>', '<p>Love ceramic tanks.</p>', OLD.replace('Original', 'Rewritten')])
def test_legacy_full_body_is_never_applied(replacement):
    conn = database(); live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_anchor_html=?", (replacement,)); conn.commit()
    with pytest.raises(LinkConflict, match='Legacy'): apply(conn, live, token='anything')
    live.push.assert_not_called()


def test_guard_rejects_text_and_invisible_html_changes():
    old = OLD + '<a href="https://source.example">Source</a><img src="/x.jpg">'
    edit = {'anchor_phrase': 'ceramic tanks'}
    url = BASE + '/collections/ceramic-tanks'
    new = build_edit(old, edit, url)
    for changed in [new.replace('Original', 'Rewritten'), new.replace('/x.jpg', '/y.jpg'), new.replace('https://source.example', 'https://other.example'), new.replace('<img src="/x.jpg">', '')]:
        with pytest.raises(LinkConflict): guard_edit(old, changed, edit, url)


def test_structured_sentence_is_spliced_once_preserving_every_original_byte():
    conn = database(); live = Shopify()
    edit = {'anchor_phrase': 'Ceramic Tanks', 'insert_sentence': 'Explore Ceramic Tanks for more options.', 'insert_after_text': 'Original second sentence.'}
    conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_edit_json=?", (json.dumps(edit),)); conn.commit()
    apply(conn, live)
    assert live.body.startswith(OLD)
    assert live.body == OLD + '<p>Explore <a href="https://s.com/collections/ceramic-tanks">Ceramic Tanks</a> for more options.</p>'


@pytest.mark.parametrize('edit', [
    {'anchor_phrase':'ceramic tanks','insert_sentence':'See ceramic tanks. Another sentence.','insert_after_text':'Love ceramic tanks.'},
    {'anchor_phrase':'ceramic tanks','insert_sentence':'See <img> ceramic tanks.','insert_after_text':'Love ceramic tanks.'},
    {'anchor_phrase':'ceramic tanks','insert_sentence':'See ceramic tanks.','insert_after_text':'Not in this body'},
])
def test_invalid_insertions_do_not_push(edit):
    conn = database(); live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_edit_json=?", (json.dumps(edit),)); conn.commit()
    assert not preview(conn, live)['allowed']
    with pytest.raises(LinkConflict): apply(conn, live, token='anything')
    live.push.assert_not_called()


def test_ambiguous_paragraph_is_rejected():
    with pytest.raises(LinkConflict, match='ambiguous'):
        build_edit('<p>Same.</p><p>Same.</p>', {'anchor_phrase':'tanks','insert_sentence':'Explore tanks.','insert_after_text':'Same.'}, BASE)


@pytest.mark.parametrize('source_type,resource,field', [('product','product','descriptionHtml'),('collection','collection','descriptionHtml'),('blog_article','article','body')])
def test_shopify_payload_is_body_only(source_type, resource, field, monkeypatch):
    graphql = Mock(return_value={'data': {resource+'Update': {resource: {field: '<p>Body</p>'}, 'userErrors': []}}})
    monkeypatch.setattr(shopify_io, 'graphql_request', graphql)
    assert shopify_io.push_body(source_type, {'shopify_id':'gid://object'}, '<p>Body</p>') == '<p>Body</p>'
    variables = graphql.call_args.args[1]
    if resource == 'article': assert variables == {'id':'gid://object','article':{'body':'<p>Body</p>'}}
    else: assert variables == {'input':{'id':'gid://object',field:'<p>Body</p>'}}
    assert graphql.call_count == 1


def test_timeout_does_not_repeat_write_and_can_reconcile():
    conn = database(); live = Shopify()
    def timed_out(*args): live._push(*args); raise TimeoutError('response lost')
    live.push.side_effect = timed_out
    with pytest.raises(TimeoutError): apply(conn, live)
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0] == 'needs_reconciliation'
    assert conn.execute('SELECT description_html FROM products').fetchone()[0] == OLD
    result = service.reconcile_suggestion(conn, 1, BASE, fetch_fn=live.fetch)
    assert result['status'] == 'applied' and live.push.call_count == 1
    assert conn.execute('SELECT description_html FROM products').fetchone()[0] == live.body


def test_failed_write_reconciles_without_mutating_content():
    conn = database(); live = Shopify()
    live.push.side_effect = RuntimeError('failed')
    with pytest.raises(RuntimeError): apply(conn, live)
    assert service.reconcile_suggestion(conn, 1, BASE, fetch_fn=live.fetch)['status'] == 'not_written'
    assert conn.execute('SELECT status FROM link_suggestions').fetchone()[0] == 'suggested'


def test_local_failure_after_remote_success_keeps_backup(monkeypatch):
    conn = database(); live = Shopify()
    with monkeypatch.context() as m:
        m.setattr(service, '_update_local', Mock(side_effect=sqlite3.OperationalError('local failure')))
        with pytest.raises(sqlite3.OperationalError): apply(conn, live)
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0] == 'needs_reconciliation'
    assert service.reconcile_suggestion(conn, 1, BASE, fetch_fn=live.fetch)['status'] == 'applied'
    assert live.push.call_count == 1


def test_sibling_edits_invalidated_and_concurrent_apply_blocked(tmp_path):
    path = tmp_path/'parallel.sqlite'; conn = database(path); live = Shopify()
    conn.execute("INSERT INTO collections (shopify_id,handle,title,raw_json,synced_at) VALUES ('gid://c/3','other','Other','{}','now')")
    conn.execute("INSERT INTO link_suggestions (source_type,source_handle,target_type,target_handle,kind,anchor_phrase,ai_edit_json,created_at) VALUES ('product','source','collection','other','ai_woven','Original','{\"anchor_phrase\":\"Original\"}',1)")
    conn.commit()
    second_token = preview(conn, live, 2)['preview_token']
    def concurrent(*args):
        other = sqlite3.connect(path); other.row_factory=sqlite3.Row
        try:
            with pytest.raises(LinkConflict, match='Another write'): apply(other, live, 2, second_token)
        finally: other.close()
        return live._push(*args)
    live.push.side_effect=concurrent
    apply(conn, live)
    sibling=conn.execute('SELECT source_body_hash,ai_edit_json FROM link_suggestions WHERE id=2').fetchone()
    assert sibling['source_body_hash']==body_hash(live.body) and sibling['ai_edit_json'] is None
    assert live.push.call_count==1


def test_auto_apply_uses_live_preview_guard_and_body_writer():
    from shopifyseo.internal_links.auto_apply import run_auto_apply
    conn=database(); live=Shopify(OLD+'<p>Live-only text.</p>')
    conn.execute("INSERT INTO service_settings(key,value) VALUES ('internal_link_auto_apply_enabled','1')")
    conn.execute('UPDATE link_suggestions SET score=1.5');conn.commit()
    result=run_auto_apply(conn,BASE,fetch_fn=live.fetch,push_fn=live.push)
    assert result['applied']==1 and 'Live-only text' in live.body
    assert conn.execute('SELECT old_body FROM link_body_snapshots').fetchone()[0]==OLD+'<p>Live-only text.</p>'
    assert conn.execute('SELECT event_type FROM link_suggestion_events').fetchone()[0]=='auto_apply'


def test_auto_apply_never_applies_ai_woven_even_if_enabled():
    from shopifyseo.internal_links.auto_apply import run_auto_apply
    conn=database(); live=Shopify()
    conn.execute("INSERT INTO service_settings(key,value) VALUES ('internal_link_auto_apply_enabled','1')")
    conn.execute("INSERT INTO service_settings(key,value) VALUES ('internal_link_auto_apply_kinds','ai_woven')")
    conn.execute("UPDATE link_suggestions SET score=2,kind='ai_woven',ai_edit_json='{\"anchor_phrase\":\"ceramic tanks\"}'");conn.commit()
    assert run_auto_apply(conn,BASE,fetch_fn=live.fetch,push_fn=live.push)['applied']==0
    live.push.assert_not_called()


def test_reconciliation_serializes_readers(tmp_path):
    path=tmp_path/'reconcile.sqlite'; conn=database(path); live=Shopify()
    live.push.side_effect=TimeoutError()
    with pytest.raises(TimeoutError): apply(conn,live)
    def fetch(*args):
        other=sqlite3.connect(path);other.row_factory=sqlite3.Row
        try:
            with pytest.raises(LinkConflict,match='still be running'):
                service.reconcile_suggestion(other,1,BASE,fetch_fn=lambda *_:OLD)
        finally: other.close()
        return OLD
    assert service.reconcile_suggestion(conn,1,BASE,fetch_fn=fetch)['status']=='not_written'


def test_changed_live_content_keeps_reconciliation_backup():
    conn=database();live=Shopify();live.push.side_effect=TimeoutError()
    with pytest.raises(TimeoutError): apply(conn,live)
    live.body=OLD+'<p>Someone else edited.</p>'
    with pytest.raises(LinkConflict,match='neither backup'):
        service.reconcile_suggestion(conn,1,BASE,fetch_fn=live.fetch)
    assert conn.execute('SELECT status FROM link_body_snapshots').fetchone()[0]=='needs_reconciliation'
