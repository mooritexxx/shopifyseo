import json
import pytest
from internal_links_support import BASE, OLD, Shopify, database
from shopifyseo.internal_links.ai_weave import generate_ai_anchor
from shopifyseo.internal_links.safety import LinkConflict, body_hash, AI_TYPES_KEY


def test_generates_structured_edit_from_live_body():
    conn = database(); live = Shopify(OLD + '<p>New live text.</p>')
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'"); conn.commit()
    def ai(messages, schema):
        # The prompt now contains extracted paragraphs, not raw HTML
        prompt = messages[0]['content']
        assert 'Love ceramic tanks' in prompt  # Paragraph content visible
        assert 'Original second sentence' in prompt
        assert 'New live text' in prompt
        assert 'revised_body' not in schema['schema']['properties']
        # Return a valid insert-sentence mode response
        return {
            'anchor_phrase': 'ceramic tanks',
            'insert_sentence': 'Check out ceramic tanks for options.',
            'insert_after_text': 'Love ceramic tanks.'
        }
    result = generate_ai_anchor(conn,1,BASE,call_ai_fn=ai,fetch_fn=live.fetch)
    row = conn.execute('SELECT ai_edit_json,ai_anchor_html,source_body_hash FROM link_suggestions').fetchone()
    assert json.loads(row['ai_edit_json'])['anchor_phrase'] == 'ceramic tanks'
    assert row['ai_anchor_html'] is None and row['source_body_hash']==body_hash(live.body)
    live.push.assert_not_called()


@pytest.mark.parametrize('body', ['<p>Different sentence.</p>', '<p>Love ceramic tanks.</p>'])
def test_ai_rewrites_and_truncation_are_rejected(body):
    conn = database(); live=Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'"); conn.commit()
    with pytest.raises(LinkConflict) as err:
        generate_ai_anchor(conn,1,BASE,call_ai_fn=lambda *_: {'revised_body':body},fetch_fn=live.fetch)
    assert err.value.detail['text_diff']
    assert conn.execute('SELECT ai_edit_json FROM link_suggestions').fetchone()[0] is None
    live.push.assert_not_called()


@pytest.mark.parametrize('source', ['collection','page'])
def test_ai_disabled_for_collections_and_pages(source):
    conn=database(); live=Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven',source_type=?",(source,)); conn.commit()
    with pytest.raises(LinkConflict, match='disabled'):
        generate_ai_anchor(conn,1,BASE,call_ai_fn=lambda *_: {},fetch_fn=live.fetch)
    live.fetch.assert_not_called()


def test_empty_type_setting_disables_every_type():
    conn=database(); live=Shopify()
    conn.execute('INSERT INTO service_settings(key,value) VALUES (?,?)',(AI_TYPES_KEY,''))
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'"); conn.commit()
    with pytest.raises(LinkConflict, match='disabled'):
        generate_ai_anchor(conn,1,BASE,call_ai_fn=lambda *_: {},fetch_fn=live.fetch)


def test_generation_cannot_change_plan_during_apply(tmp_path):
    import sqlite3
    from internal_links_support import apply

    path = tmp_path / 'generation.sqlite'
    conn = database(path)
    live = Shopify()
    original_edit = json.dumps({'anchor_phrase': 'ceramic tanks'})
    conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_edit_json=?", (original_edit,))
    conn.commit()

    def push(*args):
        other = sqlite3.connect(path)
        other.row_factory = sqlite3.Row
        try:
            # Use a 2-word anchor (required by new constraints)
            with pytest.raises(LinkConflict, match='changed during generation'):
                generate_ai_anchor(
                    other, 1, BASE, 
                    call_ai_fn=lambda *_: {
                        'anchor_phrase': 'Original tanks',  # 2 words now required
                        'insert_sentence': 'Try Original tanks today.',
                        'insert_after_text': 'Love ceramic tanks.'
                    }, 
                    fetch_fn=live.fetch
                )
            assert other.execute('SELECT ai_edit_json FROM link_suggestions').fetchone()[0] == original_edit
        finally:
            other.close()
        return live._push(*args)

    live.push.side_effect = push
    assert apply(conn, live)['status'] == 'applied'
