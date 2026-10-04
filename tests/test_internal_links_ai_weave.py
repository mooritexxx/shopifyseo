import json
import pytest
from internal_links_support import BASE, OLD, Shopify, database
from shopifyseo.internal_links.ai_weave import generate_ai_anchor
from shopifyseo.internal_links.safety import LinkConflict, body_hash, AI_TYPES_KEY


def test_generates_structured_edit_from_live_body():
    """AI response with insert_sentence mode is validated and persisted."""
    conn = database()
    live = Shopify(OLD + '<p>New live text for testing purposes here.</p>')
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    # The locator must be 40+ chars
    locator = 'This is the original second sentence with more text.'
    
    def ai(messages, schema):
        # Prompt contains extracted paragraphs, not raw HTML
        # OLD = '<p>We love ceramic tanks and all they offer.</p><p>This is the original second sentence with more text.</p>'
        content = messages[0]['content']
        assert 'We love ceramic tanks and all they offer.' in content
        assert locator in content
        assert 'New live text for testing purposes here.' in content
        assert 'revised_body' not in schema['schema']['properties']
        # Return valid insert_sentence mode response
        return {
            'anchor_phrase': 'ceramic tanks',
            'insert_sentence': 'Check out our ceramic tanks collection.',
            'insert_after_text': locator
        }
    
    result = generate_ai_anchor(conn, 1, BASE, call_ai_fn=ai, fetch_fn=live.fetch)
    row = conn.execute('SELECT ai_edit_json,ai_anchor_html,source_body_hash FROM link_suggestions').fetchone()
    edit = json.loads(row['ai_edit_json'])
    assert edit['anchor_phrase'] == 'ceramic tanks'
    assert edit['insert_sentence'] == 'Check out our ceramic tanks collection.'
    assert edit['insert_after_text'] == locator
    assert row['ai_anchor_html'] is None
    assert row['source_body_hash'] == body_hash(live.body)
    live.push.assert_not_called()


def test_ai_woven_requires_insert_sentence_mode():
    """ai_woven suggestions require insert_sentence and insert_after_text."""
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    # Return phrase_wrap-only response (missing insert_sentence)
    with pytest.raises(LinkConflict, match='insert_sentence'):
        generate_ai_anchor(
            conn, 1, BASE,
            call_ai_fn=lambda *_: {'anchor_phrase': 'ceramic tanks'},
            fetch_fn=live.fetch
        )


@pytest.mark.parametrize('body', ['<p>Different sentence.</p>', '<p>Love ceramic tanks.</p>'])
def test_ai_rewrites_and_truncation_are_rejected(body):
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    with pytest.raises(LinkConflict) as err:
        generate_ai_anchor(conn, 1, BASE, call_ai_fn=lambda *_: {'revised_body': body}, fetch_fn=live.fetch)
    assert err.value.detail['text_diff']
    assert conn.execute('SELECT ai_edit_json FROM link_suggestions').fetchone()[0] is None
    live.push.assert_not_called()


@pytest.mark.parametrize('source', ['collection', 'page'])
def test_ai_disabled_for_collections_and_pages(source):
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven',source_type=?", (source,))
    conn.commit()
    with pytest.raises(LinkConflict, match='disabled'):
        generate_ai_anchor(conn, 1, BASE, call_ai_fn=lambda *_: {}, fetch_fn=live.fetch)
    live.fetch.assert_not_called()


def test_empty_type_setting_disables_every_type():
    conn = database()
    live = Shopify()
    conn.execute('INSERT INTO service_settings(key,value) VALUES (?,?)', (AI_TYPES_KEY, ''))
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    with pytest.raises(LinkConflict, match='disabled'):
        generate_ai_anchor(conn, 1, BASE, call_ai_fn=lambda *_: {}, fetch_fn=live.fetch)


def test_generation_cannot_change_plan_during_apply(tmp_path):
    """Concurrent generation while apply is in progress fails."""
    import sqlite3
    from internal_links_support import apply

    path = tmp_path / 'generation.sqlite'
    conn = database(path)
    live = Shopify()
    
    # Locator must be 40+ chars
    locator = 'This is the original second sentence with more text.'
    
    # Use insert_sentence mode for the original edit
    original_edit = json.dumps({
        'anchor_phrase': 'ceramic tanks',
        'insert_sentence': 'Check out our ceramic tanks collection.',
        'insert_after_text': locator
    })
    conn.execute("UPDATE link_suggestions SET kind='ai_woven', ai_edit_json=?", (original_edit,))
    conn.commit()

    def push(*args):
        other = sqlite3.connect(path)
        other.row_factory = sqlite3.Row
        try:
            with pytest.raises(LinkConflict, match='changed during generation'):
                generate_ai_anchor(
                    other, 1, BASE,
                    call_ai_fn=lambda *_: {
                        'anchor_phrase': 'different anchor',
                        'insert_sentence': 'A different sentence with different anchor.',
                        'insert_after_text': locator
                    },
                    fetch_fn=live.fetch
                )
            assert other.execute('SELECT ai_edit_json FROM link_suggestions').fetchone()[0] == original_edit
        finally:
            other.close()
        return live._push(*args)

    live.push.side_effect = push
    assert apply(conn, live)['status'] == 'applied'


def test_retry_on_ai_error():
    """AI is retried once on retriable error codes."""
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    # Locator must be 40+ chars
    locator = 'This is the original second sentence with more text.'
    
    call_count = [0]
    
    def ai_with_retry(messages, schema):
        call_count[0] += 1
        if call_count[0] == 1:
            # First call: return invalid response (missing insert_sentence)
            return {'anchor_phrase': 'ceramic tanks'}
        else:
            # Second call: return valid response
            return {
                'anchor_phrase': 'ceramic tanks',
                'insert_sentence': 'Check out our ceramic tanks collection.',
                'insert_after_text': locator
            }
    
    result = generate_ai_anchor(conn, 1, BASE, call_ai_fn=ai_with_retry, fetch_fn=live.fetch)
    assert call_count[0] == 2  # Two AI calls total
    assert result['edit']['anchor_phrase'] == 'ceramic tanks'


def test_max_two_ai_calls():
    """At most 2 AI calls per invocation."""
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    call_count = [0]
    
    def always_fail(messages, schema):
        call_count[0] += 1
        return {'anchor_phrase': 'ceramic tanks'}  # Always missing insert_sentence
    
    with pytest.raises(LinkConflict, match='insert_sentence'):
        generate_ai_anchor(conn, 1, BASE, call_ai_fn=always_fail, fetch_fn=live.fetch)
    
    assert call_count[0] == 2  # Exactly two calls, then fail


def test_log_truncation():
    """Logging truncates long AI fields to 200 chars."""
    from shopifyseo.internal_links.ai_weave import _truncate
    
    short = "short text"
    assert _truncate(short, 200) == short
    
    long = "x" * 300
    truncated = _truncate(long, 200)
    assert len(truncated) == 200
    assert truncated.endswith("...")


def test_anchor_word_count_validation():
    """Anchor phrase must be 2-6 words."""
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    locator = 'This is the original second sentence with more text.'
    
    # Single word anchor
    with pytest.raises(LinkConflict, match='2-6 words') as exc:
        generate_ai_anchor(
            conn, 1, BASE,
            call_ai_fn=lambda *_: {
                'anchor_phrase': 'tanks',
                'insert_sentence': 'Check out tanks here.',
                'insert_after_text': locator
            },
            fetch_fn=live.fetch
        )
    assert exc.value.code == 'ai_anchor_word_count'


def test_anchor_appears_once_in_sentence():
    """Anchor phrase must appear exactly once in insert_sentence."""
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    locator = 'This is the original second sentence with more text.'
    
    # Anchor appears twice
    with pytest.raises(LinkConflict, match='more than once') as exc:
        generate_ai_anchor(
            conn, 1, BASE,
            call_ai_fn=lambda *_: {
                'anchor_phrase': 'ceramic tanks',
                'insert_sentence': 'Our ceramic tanks are better than other ceramic tanks.',
                'insert_after_text': locator
            },
            fetch_fn=live.fetch
        )
    assert exc.value.code == 'ai_anchor_twice'


def test_html_in_output_rejected():
    """HTML in AI output fields is rejected."""
    conn = database()
    live = Shopify()
    conn.execute("UPDATE link_suggestions SET kind='ai_woven'")
    conn.commit()
    
    locator = 'This is the original second sentence with more text.'
    
    with pytest.raises(LinkConflict, match='HTML') as exc:
        generate_ai_anchor(
            conn, 1, BASE,
            call_ai_fn=lambda *_: {
                'anchor_phrase': '<b>ceramic tanks</b>',
                'insert_sentence': 'Check out our ceramic tanks.',
                'insert_after_text': locator
            },
            fetch_fn=live.fetch
        )
    assert exc.value.code == 'ai_html_in_output'
