"""Regression evidence from the Sep 25/26/30 FAQ trial and Sep 29 copy cleanup."""
import html
import logging
import sqlite3

import pytest

from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    append_server_generated_faqpage_jsonld,
    count_distinct_approved_product_links,
    extract_faqpage_question_names_from_body,
    strip_html_for_compliance_search,
    validate_article_draft_compliance,
)
from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import (
    filter_final_article_content,
    filter_paa_questions,
)
from shopifyseo.dashboard_store import ensure_dashboard_schema

REPORTED_QUESTIONS = [
    'Are there any vape flavours banned in Canada?',
    'Are Flavor Beast vapes safe?',
    'What is the grossest vape flavour?',
    'What is the rarest vape flavour?',
    "What is vaper's tongue?",
    'Can a dentist tell if you vape?',
    'Is 1 Puff of a Vape Equal to One Cigarette?',
    'What is the most popular Fogger vape flavour?',
    'Are Fog Vapes Strong?',
    'What Flavours of Mr Fog Vapes Are Available?',
    'How Many Puffs Does Fog Pro X Have?',
    'What Are the Best Fog Vape Flavours?',
    'Is beast mode vape good?',
    'What is the best vape in the world?',
    'What are the benefits of Beast Mode?',
    'Which is the most-selling vape?',
]


@pytest.mark.parametrize('question', REPORTED_QUESTIONS)
def test_reported_questions_filtered_from_input_and_final_h3(question, caplog):
    with caplog.at_level(logging.INFO):
        assert filter_paa_questions([{'question': question}], target_brand='Fog Pro X') == []
        result, before, after = filter_final_article_content(
            '<section><h2>FAQ</h2><h3>' + html.escape(question) + '</h3><p>Bad answer.</p></section>'
            '<h2>Compatibility</h2><p>Keep this.</p>', target_brand='Fog Pro X')
    assert before == 1 and after == 0
    assert 'Bad answer' not in result and 'Keep this' in result
    assert '<section></section>' in result
    assert 'reason=' in caplog.text


@pytest.mark.parametrize('copy', [
    'These devices offer harm reduction.', 'These are harm-reduction tools.',
    'This device is less harmful.', 'A safer alternative for adults.',
    'If you are currently exploring smoke-free alternatives, see our range.',
    'This makes the transition to vaping much easier for former smokers.',
    'It helps you cut down.', 'Try NRT to manage cravings.',
    'It satisfies the cravings of thousands of Canadians every single day.',
    'This helps you quit smoking.', 'Vaping is safe.',
    'A safe vape for your lungs.',
])
def test_health_claims_removed_from_body_blocks(copy):
    body = f'<div><h2>Overview</h2><p>{html.escape(copy)}</p><p>USB-C charging.</p></div>'
    result, _, _ = filter_final_article_content(body)
    assert copy not in strip_html_for_compliance_search(result)
    assert result == '<div><h2>Overview</h2><p>USB-C charging.</p></div>'


@pytest.mark.parametrize('copy', [
    'Use the supplied cable for safe battery charging.',
    'The Smoke-Free Ontario Act sets location rules.',
    'Products authorized as nicotine replacement therapy (NRT) are sold through pharmacies.',
    'Try strawberry for your flavour cravings.',
    'The manufacturer rates this device for roughly 50K puffs.',
    'Switching between devices changes the charging cable you need.',
    'Flavor Beast offers several flavours.',
])
def test_preserve_handling_regulatory_capacity_and_brand_text(copy):
    body = f'<h2>Product details</h2><p>{copy}</p>'
    assert filter_final_article_content(body)[0] == body


def test_inline_claims_removed_without_losing_adjacent_links():
    body = '<p>A smoke-<strong>free</strong> alternative.</p><p><a href="/products/a">Keep</a></p>'
    assert filter_final_article_content(body)[0] == '<p><a href="/products/a">Keep</a></p>'


def test_normalized_h2_h3_dedupe_and_schema_rebuild_are_idempotent():
    body = ('<h2>Which flavors are available?</h2><p>Berry and mint.</p>'
            '<h2>FAQ</h2><h3>Which FLAVOURS are available!</h3><p>Duplicate answer.</p>'
            '<h2>Helpful questions before you choose</h2>'
            '<h3>Which flavours are available?</h3><p>Duplicate again.</p>'
            '<h3>How do I charge it?</h3><p>Use the supplied cable.</p>')
    result, before, after = filter_final_article_content(body)
    assert (before, after) == (4, 2)
    assert 'Duplicate' not in result
    assert 'Which flavours are available?' in result
    result, items = append_server_generated_faqpage_jsonld(result, filter_by_h3_headings=False)
    assert len(items) == 2
    assert extract_faqpage_question_names_from_body(result) == ['Which flavours are available?', 'How do I charge it?']
    assert filter_final_article_content(result)[0] == result


PRODUCT_MAP = {f'/products/p{i}': f'https://example.com/products/p{i}' for i in range(3)}
PRODUCT_LINKS = ''.join(f'<a href="{url}">Product</a>' for url in PRODUCT_MAP.values())
FILLER = '<p>' + ('Product details. ' * 1000) + '</p>'


@pytest.mark.parametrize('links,count', [
    ('<a href="/collections/a">A</a><a href="/collections/b">B</a>', 0),
    ('<a href="/products/p0">A</a>' * 3, 1),
    ('<a href="/products/p0?variant=1">A</a><a href="/products/p0/#foo">B</a>', 1),
    ('<a href="https://foreign.example/products/p0">A</a>', 0),
    ('<a href="//foreign.example/products/p0">A</a>', 0),
    ('<a href="/products/unapproved">A</a>', 0),
    (PRODUCT_LINKS, 3),
])
def test_distinct_product_link_gate(links, count):
    mapping = {**PRODUCT_MAP, '/collections/a': 'https://example.com/collections/a', '/collections/b': 'https://example.com/collections/b'}
    assert count_distinct_approved_product_links(links, mapping) == count
    gaps = validate_article_draft_compliance(
        body_html=FILLER + links, require_faqpage_ld=False, secondary_urls=[],
        primary_keyword_for_body=None, path_to_canonical=mapping, min_product_links=3)
    assert bool(gaps) == (count < 3)
    if gaps:
        assert 'distinct approved product URLs' in gaps[0]


@pytest.fixture
def conn(monkeypatch):
    from shopifyseo.dashboard_ai_engine_parts import config
    monkeypatch.setattr(config, '_STORE_IDENTITY_CACHE', None)
    from shopifyseo.dashboard_queries import _urls
    monkeypatch.setattr(_urls, '_BASE_URL_CACHE', None)
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    ensure_dashboard_schema(connection)
    connection.execute("INSERT INTO service_settings (key, value) VALUES ('store_custom_domain', 'https://example.com')")
    connection.executemany(
        "INSERT INTO products (handle, title, tags_json, options_json, raw_json, synced_at) VALUES (?, ?, '[]', '[]', '{}', '')",
        [(f'p{i}', f'Product {i}') for i in range(3)])
    connection.commit()
    from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings
    monkeypatch.setattr(_article_draft, 'ai_settings', lambda c: {**ai_settings(c), 'article_draft_phased': False})
    yield connection
    connection.close()


def payload(body):
    return dict(title='Fog Pro X product guide', seo_title='Fog Pro X product guide', seo_description='Product details.', body=body)


@pytest.mark.parametrize('resume', [False, True])
def test_generation_and_resume_filter_after_faq_answer_append(conn, monkeypatch, resume):
    # The second requested question is missing, forcing _append_faq_answers to add
    # a Helpful questions block. Its AI answer contains a prohibited health claim.
    body = PRODUCT_LINKS + FILLER + '<h2>FAQ</h2><h3>Which flavours are available?</h3><p>Berry.</p>'
    body += '<h3>Are Flavor Beast vapes safe?</h3><p>Rejected.</p>'
    calls = []
    def ai(*args, stage='', **kwargs):
        calls.append(stage)
        if stage == 'article_draft':
            return payload(body)
        if stage == 'article_draft_faq_repair':
            return {'answers': ['A smoke-free alternative for former smokers.']}
        raise AssertionError(stage)
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    result = _article_draft.generate_article_draft(
        conn, 'Fog Pro X', idea_serp_context={'audience_questions': [
            {'question': 'Which flavours are available?'}, {'question': 'How do I charge Fog Pro X?'}]},
        resume_run=payload(body) if resume else None)
    assert 'article_draft_faq_repair' in calls
    assert ('article_draft' not in calls) == resume
    assert 'smoke-free' not in result['body'] and 'Rejected.' not in result['body']
    assert extract_faqpage_question_names_from_body(result['body']) == ['Which flavours are available?']


@pytest.mark.parametrize('mode', ['all_faq_rejected', 'zero_products', 'two_products', 'links_removed_with_claim'])
def test_generation_hard_fails_after_repairs(conn, monkeypatch, mode):
    if mode == 'all_faq_rejected':
        body = PRODUCT_LINKS + FILLER + '<h3>Is beast mode vape good?</h3><p>Yes.</p>'
        expected = 'All FAQ candidates were rejected'
    else:
        links = '' if mode == 'zero_products' else PRODUCT_LINKS.replace('<a href="https://example.com/products/p2">Product</a>', '')
        if mode == 'links_removed_with_claim':
            links = '<p>A smoke-free alternative: ' + PRODUCT_LINKS + '</p>'
        body = FILLER + links
        expected = '3 distinct approved product URLs'
    monkeypatch.setattr(_article_draft, '_call_ai', lambda *args, stage='', **kwargs: payload(body) if stage == 'article_draft' else {'append_html': ''})
    with pytest.raises(RuntimeError, match=expected):
        _article_draft.generate_article_draft(conn, 'Fog Pro X')


def test_repair_cannot_reintroduce_rejected_faq_or_duplicates(conn, monkeypatch):
    original = PRODUCT_LINKS + '<p>' + ('Details. ' * 1000) + '</p><h3>Which flavours are available?</h3><p>Berry.</p>'
    appended = FILLER + '<h2>Helpful questions before you choose</h2><h3>Which flavors are available?</h3><p>Duplicate.</p><h3>Can a dentist tell if you vape?</h3><p>Reject.</p>'
    monkeypatch.setattr(_article_draft, '_call_ai', lambda *args, stage='', **kwargs: payload(original) if stage == 'article_draft' else {'append_html': appended})
    result = _article_draft.generate_article_draft(conn, 'Fog Pro X')
    assert 'Duplicate.' not in result['body'] and 'Reject.' not in result['body']
    assert extract_faqpage_question_names_from_body(result['body']) == ['Which flavours are available?']


def test_external_product_path_is_not_laundered_by_sanitizer():
    body = '<a href="https://foreign.example/products/p0">External</a>'
    cleaned = _article_draft.sanitize_article_internal_links(body, path_to_canonical=PRODUCT_MAP, base_url='https://example.com')
    assert cleaned == 'External'


def test_non_anchor_href_does_not_count():
    assert count_distinct_approved_product_links('<div href="/products/p0">Fake</div>', PRODUCT_MAP) == 0


def test_legacy_health_heading_filter_preserved():
    body = '<h2>Health Considerations</h2><p>Remove this section.</p><h2>Charging</h2><p>Keep.</p>'
    assert filter_final_article_content(body)[0] == '<h2>Charging</h2><p>Keep.</p>'


def test_stream_surfaces_validation_error(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.routers import blogs
    def fail(*args, **kwargs):
        raise RuntimeError('Article must link to at least 3 distinct approved product URLs (currently 0).')
    monkeypatch.setattr(blogs, '_run_generate_article_draft', fail)
    response = TestClient(app).post('/api/articles/generate-draft-stream', json={
        'blog_id': 'gid://shopify/Blog/1', 'blog_handle': 'canada', 'topic': 'Fog Pro X'})
    assert response.status_code == 200
    assert 'event: error' in response.text
    assert '3 distinct approved product URLs' in response.text
    assert 'event: done' not in response.text


def test_phased_batches_are_filtered_after_assembly(conn, monkeypatch):
    from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings
    monkeypatch.setattr(_article_draft, 'ai_settings', lambda c: {**ai_settings(c), 'article_draft_phased': True})
    def ai(*args, stage='', json_schema=None, **kwargs):
        if stage == 'article_draft_outline':
            return {**payload(''), 'sections': [
                {'heading': f'Section {i}', 'level': 'h2', 'beats': 'Product details.'} for i in range(8)]}
        if stage == 'article_draft_section':
            count = json_schema['schema']['properties']['html_blocks']['minItems']
            block = PRODUCT_LINKS + '<h2>Product details</h2><p>' + ('Details. ' * 250) + '</p>'
            block += '<h3>Which flavours are available?</h3><p>Berry.</p><h3>What is the best vape in the world?</h3><p>Reject.</p>'
            return {'html_blocks': [block] * count}
        raise AssertionError(stage)
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    result = _article_draft.generate_article_draft(conn, 'Fog Pro X')
    assert 'Reject.' not in result['body']
    assert result['body'].count('<h3>Which flavours are available?</h3>') == 1
    assert extract_faqpage_question_names_from_body(result['body']) == ['Which flavours are available?']


def test_punctuationless_survivor_is_in_schema():
    result, _, _ = filter_final_article_content('<h2>How to charge your device</h2><p>Use its USB-C port.</p>')
    _, items = append_server_generated_faqpage_jsonld(result, filter_by_h3_headings=False)
    assert [item['question'] for item in items] == ['How to charge your device']


def test_battery_word_does_not_exempt_a_health_promise():
    body = '<p>Vaping is safe and this device has a large battery.</p>'
    assert filter_final_article_content(body)[0] == ''


def test_preserve_specific_charging_question():
    body = '<h3>Is it safe to charge my vape?</h3><p>Follow the manufacturer instructions.</p>'
    assert filter_final_article_content(body)[0] == body


@pytest.mark.parametrize('question', [
    'Are Geek Bars healthy?', 'Can I vape with RSV?', 'Can you vape with RSV?',
    'Is vaping ok for diabetics?', 'Is vaping suitable for those with diabetes?',
    'Does vaping affect cholesterol levels?', 'Do vapes affect cholesterol?',
    'Does vaping increase LDL cholesterol?', 'Can kiwi lower triglycerides?',
    'Can vaping trigger lupus?', 'Does Vaping Affect Implantation?',
    'Can vaping increase creatinine levels?', 'Does vaping lower cortisol?',
    'Can you vape with emphysema?', 'Does vaping worsen emphysema?',
    'Is Vaping Better than Smoking?', 'Is it better to smoke or vape?',
    'Can expired vape juice hurt you?', 'What are the symptoms of vape juice poisoning?',
    'What is the best flavour for a disposable vape?',
    'What is the best flavor for a disposable vape?',
])
def test_remaining_reported_health_and_bait_questions(question):
    assert filter_paa_questions([{'question': question}], target_brand='Geek Bar') == []
    result, before, after = filter_final_article_content(f'<h3>{question}</h3><p>Remove answer.</p>', target_brand='Geek Bar')
    assert (before, after) == (1, 0)
    assert 'Remove answer' not in result


@pytest.mark.parametrize('sentence', [
    'Cut down on refills.', 'Cut down the wick.', 'Satisfy dessert cravings.',
    'The craving for something cold.', 'Decide which device is better for you.',
    'Check smoke-free building rules.', 'Your vape stays safe from heat.',
    'Keep your e-liquid safer by storing it upright.',
])
def test_review_false_positives_are_preserved(sentence):
    body = f'<p>{sentence}</p>'
    assert filter_final_article_content(body)[0] == body


@pytest.mark.parametrize('question', [
    'Are STLTH 60K flavours good for beginners?',
    'Is it safe to leave my vape in a hot car?',
    'How should I store vape juice around children and pets?',
])
def test_on_topic_replacement_questions_survive(question):
    assert filter_paa_questions([{'question': question}], target_brand='STLTH 60K')
    body = f'<h3>{question}</h3><p>Follow the manufacturer instructions.</p>'
    assert filter_final_article_content(body, target_brand='STLTH 60K')[0] == body


@pytest.mark.parametrize('body', [
    '<h1>A smoke-free alternative.</h1>', '<h4>A smoke-free alternative.</h4>',
    '<h5>A smoke-free alternative.</h5>', '<h6>A smoke-free alternative.</h6>',
    '<section>A smoke-free alternative.</section>', '<dd>A smoke-free alternative.</dd>',
    '<dt>A smoke-free alternative.</dt>', '<figcaption>A smoke-free alternative.</figcaption>',
    '<div>A smoke-free alternative.<p>Product details.</p></div>',
    'A smoke-free alternative.<p>Product details.</p>',
    '<section><span>A smoke-<b>free</b> alternative.</span><p>Product details.</p></section>',
])
def test_filter_and_validator_cover_same_visible_units(body):
    from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import article_health_claims
    assert article_health_claims(body)
    result, _, _ = filter_final_article_content(body)
    assert not article_health_claims(result)
    gaps = validate_article_draft_compliance(
        body_html=result + FILLER, require_faqpage_ld=False, secondary_urls=[],
        primary_keyword_for_body=None, path_to_canonical={}, check_health_claims=True)
    assert gaps == []


def test_sentence_removal_preserves_balanced_markup_and_clean_product_links():
    body = '<p>A smoke-<strong>free</strong> alternative. See <a href="/products/p0"><em>Product</em></a> for USB-C charging.</p>'
    result, _, _ = filter_final_article_content(body)
    assert 'smoke-' not in result and '<strong>' not in result
    assert '<a href="/products/p0"><em>Product</em></a>' in result
    assert result.startswith('<p>') and result.endswith('</p>')
    assert count_distinct_approved_product_links(result, PRODUCT_MAP) == 1


def test_bait_only_source_does_not_require_faq(conn, monkeypatch):
    monkeypatch.setattr(_article_draft, '_call_ai', lambda *args, **kwargs: payload(PRODUCT_LINKS + FILLER))
    result = _article_draft.generate_article_draft(conn, 'Fog Pro X', idea_serp_context={
        'audience_questions': [{'question': q} for q in REPORTED_QUESTIONS]})
    assert extract_faqpage_question_names_from_body(result['body']) == []


def test_product_repair_uses_same_brand_and_ignores_stock(conn, monkeypatch):
    """Test that product repair selects same-brand products, ignoring stock status.

    The OOS product (p0) should be included in repair targets. Products are ordered
    by title, not by stock level. Stock/inventory must never affect linkability.
    """
    conn.execute("UPDATE products SET vendor = 'Fog', total_inventory = 5, status = 'ACTIVE'")
    conn.execute("UPDATE products SET total_inventory = 0 WHERE handle = 'p0'")
    conn.execute("INSERT INTO products (handle,title,vendor,status,tags_json,options_json,raw_json,synced_at) VALUES ('unrelated','Unrelated','Other','ACTIVE','[]','[]','{}','')")
    conn.commit()
    calls = []
    def ai(*args, stage='', **kwargs):
        calls.append(stage)
        assert stage == 'article_draft'
        return payload(FILLER)
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    result = _article_draft.generate_article_draft(conn, 'Fog Pro X')
    body = result['body']
    assert calls == ['article_draft']
    assert '/products/unrelated' not in body
    assert count_distinct_approved_product_links(body, PRODUCT_MAP) == 3
    assert '/products/p0' in body


def test_collection_evidence_is_required_for_collection_repair(conn):
    conn.execute("UPDATE products SET shopify_id = handle, vendor = 'Different vendor'")
    conn.execute("INSERT INTO collections (shopify_id, handle, title, raw_json, synced_at) VALUES ('c1', 'focus', 'Focus', '{}', '')")
    conn.executemany("INSERT INTO collection_products (collection_shopify_id,product_shopify_id,synced_at) VALUES ('c1',?,'')", [('p0',), ('p1',)])
    conn.commit()
    targets = [dict(type='product', handle=f'p{i}', title=f'Product {i}', url=PRODUCT_MAP[f'/products/p{i}']) for i in range(3)]
    selected = _article_draft.relevant_product_repair_targets(conn, 'Unknown brand', dict(type='collection', handle='focus'), targets)
    assert {t['handle'] for t in selected} == {'p0', 'p1'}
    assert _article_draft.relevant_product_repair_targets(conn, 'Unknown brand', None, targets) == []


def test_insufficient_relevant_products_cannot_be_padded_by_ai(conn, monkeypatch):
    conn.execute("UPDATE products SET vendor = 'Other', status = 'ACTIVE'")
    conn.execute("UPDATE products SET vendor = 'Fog' WHERE handle = 'p0'")
    conn.commit()
    def ai(*args, stage='', **kwargs):
        return payload(FILLER) if stage == 'article_draft' else {'append_html': PRODUCT_LINKS.replace('https://example.com', '')}
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    with pytest.raises(RuntimeError, match='Only 1 relevant approved product URLs'):
        _article_draft.generate_article_draft(conn, 'Fog Pro X')


def test_failed_body_is_saved_and_resume_preserves_rejected_faq_gate(conn, monkeypatch, tmp_path):
    from shopifyseo import dashboard_store as store
    path = tmp_path / 'draft.sqlite3'
    disk = sqlite3.connect(path)
    conn.backup(disk)
    disk.row_factory = sqlite3.Row
    run_id = store.create_article_draft_run(disk, {'topic': 'Fog Pro X'})
    def connect():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(store, 'db_connect', connect)
    body = PRODUCT_LINKS + FILLER + '<h3>Is beast mode vape good?</h3><p>Reject.</p>'
    stages = []
    def ai(*args, stage='', **kwargs):
        stages.append(stage)
        return payload(body) if stage == 'article_draft' else {'append_html': ''}
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    with pytest.raises(RuntimeError, match='All FAQ candidates were rejected'):
        _article_draft.generate_article_draft(disk, 'Fog Pro X', draft_run_id=run_id)
    with connect() as read:
        saved = store.get_article_draft_run(read, run_id)
    assert 'Product details.' in saved['body']
    assert saved['validation_summary']['had_faq_candidates']
    assert not saved['checkpoints']['content']['validated']
    assert saved['checkpoints']['pre_validation']['body'] == body
    with pytest.raises(RuntimeError, match='All FAQ candidates were rejected'):
        _article_draft.generate_article_draft(connect(), 'Fog Pro X', draft_run_id=run_id, resume_run=saved)
    assert stages.count('article_draft') == 1
    def repaired(*args, stage='', **kwargs):
        assert stage == 'article_draft_append_repair'
        return {'append_html': '<h3>Which flavours are available?</h3><p>Berry.</p>'}
    monkeypatch.setattr(_article_draft, '_call_ai', repaired)
    result = _article_draft.generate_article_draft(connect(), 'Fog Pro X', draft_run_id=run_id, resume_run=saved)
    assert 'Which flavours are available?' in result['body']
    with connect() as read:
        validated = store.get_article_draft_run(read, run_id)
    assert validated['checkpoints']['content']['validated']
    assert validated['validation_summary']['ok']


def test_entity_and_unicode_health_phrases_use_same_filter_and_validator():
    from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import article_health_claims
    body = '<p>Harm&nbsp;reduction. A smoke‑free alternative.</p><p>Keep this.</p>'
    assert len(article_health_claims(body)) == 2
    result = filter_final_article_content(body)[0]
    assert result == '<p>Keep this.</p>'
    assert not article_health_claims(result)


def test_question_selection_keeps_target_brand_and_filters_before_cap():
    from shopifyseo.dashboard_ai_engine_parts.serp_draft_context import select_required_paa_questions_for_draft
    ctx = {'audience_questions': [
        {'question': 'Are Fog vapes strong?'}, {'question': 'What flavours of Mr Fog are available?'},
        {'question': 'How do I charge Fog Pro X?'}]}
    assert select_required_paa_questions_for_draft(ctx, max_questions=1, target_brand='Fog Pro X') == ['How do I charge Fog Pro X?']
    branded = {'audience_questions': [{'question': 'How do I charge Geek Bar?'}]}
    assert select_required_paa_questions_for_draft(branded, target_brand='Geek Bar') == ['How do I charge Geek Bar?']


# Verbatim old/replacement question pairs from the supplied Sep 29 report.
HEALTH_LOG_QUESTION_PAIRS = [("What Is a Geek Bar? A Canadian Vaper's FAQ", 'Are Geek Bars healthy?', 'Who can buy Geek Bars in Canada?'),
 ('How Long Does Vape Juice Last? Storage and Freshness Guide',
  'Can I vape with RSV?',
  'Where should I store vape juice?'),
 ('How Long Does Vape Juice Last? Storage and Freshness Guide',
  'Is vaping ok for diabetics?',
  'Does nicotine strength affect shelf life?'),
 ('Rechargeable Vape Pen Issues: A Troubleshooting FAQ',
  'Does vaping affect cholesterol levels?',
  'How long does a rechargeable vape pen battery last?'),
 ('Rechargeable Vape Pen Issues: A Troubleshooting FAQ',
  'Is vaping suitable for those with diabetes?',
  'Why is my vape pen blinking?'),
 ('Rechargeable Vape Pen Issues: A Troubleshooting FAQ',
  'What should I know about RSV and vaping?',
  'How long does a vape pen take to charge?'),
 ('Rechargeable Vape Pen Issues: A Troubleshooting FAQ',
  'Can I vape with RSV?',
  'Why does my vape taste burnt?'),
 ('Rechargeable Vape Pen Issues: A Troubleshooting FAQ',
  'Is vaping ok for diabetics?',
  'Why is my vape pen leaking?'),
 ('Rechargeable Vape Pen Issues: A Troubleshooting FAQ',
  'Do vapes affect cholesterol?',
  'Can I leave my vape pen charging overnight?'),
 ('Best Strawberry Kiwi Vapes in Canada: Top Flavour Profiles',
  'Can kiwi lower triglycerides?',
  'Does strawberry kiwi come in iced versions?'),
 ('Best Strawberry Kiwi Vapes in Canada: Top Flavour Profiles',
  'Is kiwi good for lung infection?',
  'What nicotine strength are strawberry kiwi vapes?'),
 ('Top Tips for Using Your First Vape Pen: A Beginner’s Guide',
  'Does vaping increase LDL cholesterol?',
  'How do I know when my disposable is empty?'),
 ('Top Tips for Using Your First Vape Pen: A Beginner’s Guide',
  'Can vaping trigger lupus?',
  'Why is my vape pen not hitting?'),
 ('Top Tips for Using Your First Vape Pen: A Beginner’s Guide',
  'Is vaping ok for diabetics?',
  'What nicotine strength should a beginner choose?'),
 ('Top Tips for Using Your First Vape Pen: A Beginner’s Guide',
  'Is vaping safe for someone with COPD?',
  'Can I refill a disposable vape?'),
 ('Understanding Vape Pen Rechargeable Technology',
  'Can I Vape with RSV?',
  'How Do I Charge a Rechargeable Vape Pen Properly?'),
 ('Understanding Vape Pen Rechargeable Technology',
  'Does Vaping Affect Implantation?',
  'How Often Should I Clean My Vape Pen?'),
 ('Understanding Vape Pen Rechargeable Technology',
  'Is Vaping Ok for Diabetics?',
  'When Should I Replace My Vape Pen Battery?'),
 ('How to Use a Vape Pen: A Beginner’s Guide to Success',
  'Can vaping increase creatinine levels?',
  'How long does a vape pen take to charge?'),
 ('Vape Accessories in Canada: Essential Add-ons for Your Device',
  'Can vaping increase creatinine levels?',
  'Do I need a special charger for my vape?'),
 ('Vape Accessories in Canada: Essential Add-ons for Your Device',
  'Can I vape with RSV?',
  'What accessories do I need for a disposable vape?'),
 ('Vape Accessories in Canada: Essential Add-ons for Your Device',
  'Does vaping lower cortisol?',
  'Are vape accessories universal?'),
 ('Essential Vape Accessories for Canadian Vapers: A Complete Guide',
  'Can you vape with emphysema?',
  'How often should I replace my vape coil?'),
 ('Essential Vape Accessories for Canadian Vapers: A Complete Guide',
  'Can vaping increase creatinine levels?',
  'Do I need a spare battery?'),
 ('Essential Vape Accessories for Canadian Vapers: A Complete Guide',
  'Does vaping lower cortisol?',
  'How do I clean my vape accessories?'),
 ('Vape Canada Online: A Complete Shopping Guide for Canadians',
  'Is Vaping Better than Smoking?',
  'How do I choose a nicotine strength?'),
 ('Vape Canada Online: A Complete Shopping Guide for Canadians',
  'Common Questions: Are Vapes Safe for Celiacs?',
  'Common Questions: What Is in Vape E-Liquid?'),
 ('Vape Canada Online: A Complete Shopping Guide for Canadians',
  'Is it better to smoke or vape?',
  'Can I buy vapes online in Canada?'),
 ('Best Vape for Heavy Smokers: Transitioning in Canada',
  'What Vape is Closest to Smoking a Cigarette?',
  'What Is a Mouth-to-Lung (MTL) Vape?'),
 ('Best Vape for Heavy Smokers: Transitioning in Canada',
  'What vape is the closest to smoking a cigarette?',
  'Should I choose a disposable or a pod system?'),
 ('Best Vape for Heavy Smokers: Transitioning in Canada',
  'Does vaping lower cortisol?',
  'How long does a high-capacity disposable last?'),
 ('Best Vape for Heavy Smokers: Transitioning in Canada',
  'Can vaping increase creatinine levels?',
  'What nicotine strength do most disposables use?'),
 ('Best Vape for Heavy Smokers: Transitioning in Canada',
  'Does vaping worsen emphysema?',
  'Are pod systems cheaper than disposables?'),
 ('Can Vape Juice Go Bad? A Guide to E-Liquid Freshness',
  'Can expired vape juice hurt you?',
  'Should I replace expired vape juice?'),
 ('Can Vape Juice Go Bad? A Guide to E-Liquid Freshness',
  'Can I vape with RSV?',
  'Does vape juice need to be refrigerated?'),
 ('Can Vape Juice Go Bad? A Guide to E-Liquid Freshness',
  'What are the symptoms of vape juice poisoning?',
  'How should I store vape juice around children and pets?'),
 ('Vape Types Explained: Finding Your Perfect Canadian Device',
  'Does vaping lower cortisol?',
  'How long does a disposable vape last?'),
 ('Vape Types Explained: Finding Your Perfect Canadian Device',
  'Does vaping increase LDL cholesterol?',
  'Should I choose a disposable, a pod system or a refillable kit?'),
 ('Why Vapes Are So Expensive: Understanding Market Costs',
  'Is Vaping OK for Diabetics?',
  'Does a More Expensive Vape Last Longer?'),
 ('Legal Vaping Age in Canada: A Guide to Compliance',
  'Is 1000 puffs of a vape a day bad?',
  'How many puffs are in a disposable vape?'),
 ('A Guide to Vuse Canada: Flavours and Device Maintenance',
  'How many cigarettes is one Vuse pod equal to?',
  'How long does a Vuse pod last?'),
 ('A Guide to Vuse Canada: Flavours and Device Maintenance',
  'How many cigarettes is 1 Vuse pod equal to?',
  'How many puffs are in a Vuse pod?'),
 ('A Guide to Vuse Canada: Flavours and Device Maintenance',
  'Is smoking Vuse bad for you?',
  'Who can buy Vuse products in Canada?'),
 ('A Guide to Vuse Canada: Flavours and Device Maintenance',
  'Can lungs heal after 3 years of vaping?',
  'How do I keep my Vuse device working well?'),
 ('Zyn Nicotine Pouches in Canada: A Shopper’s Buying Guide',
  'Is ZYN safer than smoking?',
  'Is ZYN legal to buy in Canada?'),
 ('Zyn Nicotine Pouches in Canada: A Shopper’s Buying Guide',
  'Where can I find authorized nicotine replacement options?',
  'Where are authorized nicotine pouches sold in Canada?')]


@pytest.mark.parametrize('topic,old,new', HEALTH_LOG_QUESTION_PAIRS)
def test_full_health_report_question_pairs(topic, old, new):
    from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import question_drop_reason
    assert question_drop_reason(old, topic), old
    # Older cleanup replacements included two puff questions; the newer explicit
    # no-puff-question contract takes precedence over those historical replacements.
    if 'puffs' not in new.lower():
        assert question_drop_reason(new, topic) is None, new


def test_checkpoint_write_failure_stops_before_filtering(conn, monkeypatch):
    from shopifyseo import dashboard_store as store
    monkeypatch.setattr(_article_draft, '_call_ai', lambda *args, **kwargs: payload(PRODUCT_LINKS + FILLER))
    def cannot_save():
        raise OSError('Checkpoint storage unavailable')
    def must_not_filter(*args, **kwargs):
        pytest.fail('Validation must not start without a saved draft')
    monkeypatch.setattr(store, 'db_connect', cannot_save)
    monkeypatch.setattr(_article_draft, 'filter_final_article_content', must_not_filter)
    with pytest.raises(RuntimeError, match='Could not save the draft checkpoint'):
        _article_draft.generate_article_draft(conn, 'Fog Pro X', draft_run_id='test-run')


def test_repair_selection_does_not_mutate_catalog_or_allowlist_contract(conn):
    from shopifyseo.dashboard_queries import build_store_internal_link_allowlist
    conn.execute("UPDATE products SET vendor = 'Fog', status = 'ACTIVE'")
    conn.commit()
    before = build_store_internal_link_allowlist(conn, 'https://example.com')
    changes = conn.total_changes
    selected = _article_draft.relevant_product_repair_targets(conn, 'Fog Pro X', None, before[0])
    after = build_store_internal_link_allowlist(conn, 'https://example.com')
    assert before == after
    assert conn.total_changes == changes
    assert len(selected) == 3
    assert all(set(t) == {'type', 'handle', 'title', 'url'} for t in selected)


BAIT_QUESTION_FAMILIES = {
    'is_good_question': [
        'Is beast mode a good vape?', 'Is Flavour Beast a good brand?',
        'Are Beast Mode vapes good quality?', 'Is Beast Mode vape good or bad?',
        'Is STLTH a good vape?', '1. Is beast mode vape good?',
        'Q: Is beast mode vape good?', '“Is beast mode vape good?”',
    ],
    'best_brand_question': [
        'What is the best pod vape in the world?', 'What is the No. 1 vape in Canada?',
        'Which pod brand is best?', 'What is the best disposable in Canada?',
        'What is the best pod vape in 2026?',
        'What is the best refillable pod vape system in Canada?',
        'What is the best 6000 vape?', 'What are the best nicotine salt brands in Canada?',
        'What is the top selling vape?', 'What is the top rated vape?',
        'What is the #1 vape?', 'What is the top–rated vape?',
    ],
    'benefits_question': [
        'What benefits does Beast Mode Max 2 offer?',
        'What are the advantages of Beast Mode Max 2?', 'Why is Beast Mode Max 2 beneficial?',
    ],
    'longest_lasting_question': [
        'What is the longest lasting disposable vape in Canada?',
        'What is the longest-lasting disposable vape?', 'Which disposable vape lasts the longest?',
        'What is the longest‑lasting disposable vape?',
        'What disposable vape has the longest battery life?',
    ],
    'medical_question': [
        'What is a good device to quit vaping?', 'How hard is it to quit vaping?',
        'How hard is quitting vaping?',
    ],
}


@pytest.mark.parametrize('tag', ['h2', 'h3', 'h4'])
@pytest.mark.parametrize('reason,question', [
    (reason, q) for reason, questions in BAIT_QUESTION_FAMILIES.items() for q in questions
])
def test_bait_families_across_all_filters_and_validator(reason, question, tag, caplog):
    from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import (
        article_denied_question_headings, filter_and_dedupe_helpful_questions, question_drop_reason,
    )
    assert question_drop_reason(question) == reason
    with caplog.at_level(logging.INFO):
        assert filter_paa_questions([{'question': question}]) == []
        assert reason in caplog.text
        caplog.clear()
        assert filter_and_dedupe_helpful_questions([question]) == []
        assert reason in caplog.text
        caplog.clear()
        body = f'<{tag}>{html.escape(question)}</{tag}><p>Reject answer.</p>'
        assert article_denied_question_headings(body) == [question]
        result, before, after = filter_final_article_content(body)
        assert (result, before, after) == ('', 1, 0)
        assert reason in caplog.text
    kwargs = dict(require_faqpage_ld=False, secondary_urls=[], primary_keyword_for_body=None,
                  path_to_canonical={})
    gap = f"Visible FAQ question still matches the FAQ denylist: '{question}'."
    assert gap in validate_article_draft_compliance(body_html=FILLER + body, check_faq_questions=True, **kwargs)
    assert gap not in validate_article_draft_compliance(body_html=FILLER + body, **kwargs)
    assert not article_denied_question_headings(result)
    assert not validate_article_draft_compliance(body_html=FILLER + result, check_faq_questions=True, **kwargs)


MUST_KEEP_QUESTIONS = [
    'Can the STLTH 60K help me cut down on refills?', 'Which flavour helps with dessert cravings?',
    'Are STLTH 60K flavours good for beginners?', 'Is it safe to leave my vape in a hot car?',
    'Which vape has the best battery?', 'Is the STLTH 60K good for travel?',
    'What are the best flavours of STLTH 60K?', 'Is 20mg a good nicotine strength?',
    'What is the best nicotine strength for the STLTH 60K?',
    'What is the best way to store a disposable vape?', 'Which brands make 50K disposables?',
    'How long does a STLTH 60K last?', 'Which STLTH 60K mode lasts longer?',
    'Does the Max 2 benefit from a mesh coil?', 'Is the Beast Mode Max 2 rechargeable?',
    'Is the Beast Mode Max 2 good value compared with the Max 1?',
]


@pytest.mark.parametrize('question', MUST_KEEP_QUESTIONS)
@pytest.mark.parametrize('tag', ['h2', 'h3', 'h4'])
def test_useful_questions_survive_byte_identical(question, tag):
    from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import (
        article_denied_question_headings, filter_and_dedupe_helpful_questions, question_drop_reason,
    )
    assert question_drop_reason(question) is None
    item = {'question': question}
    assert filter_paa_questions([item]) == [item]
    assert filter_and_dedupe_helpful_questions([question]) == [question]
    body = f'<{tag}>{question}</{tag}><p>Product details.</p>'
    assert filter_final_article_content(body) == (body, 1, 1)
    assert article_denied_question_headings(body) == []


@pytest.mark.parametrize('question', [
    'FAQ 1. “Is beast mode vape good?”', '2) Is beast mode vape good?',
    'Q: &ldquo;Is beast mode vape good?&rdquo;', '1. Q: Is  beast\nmode vape good',
])
def test_question_prefixes_are_normalized_for_matching_only(question):
    from shopifyseo.dashboard_ai_engine_parts.faq_content_filter import question_drop_reason
    assert question_drop_reason(question) == 'is_good_question'
    assert filter_final_article_content(f'<h4>{question}</h4><p>Reject.</p>') == ('', 1, 0)


def test_bait_rules_do_not_touch_non_question_headings_or_prose():
    body = ('<h2>Longest-lasting battery in the Max 2 line</h2>'
            '<p>This device offers benefits like USB-C.</p>'
            '<h2>Advantages of the Max 2</h2><p>Good quality. Longest-lasting battery.</p>')
    assert filter_final_article_content(body) == (body, 0, 0)


@pytest.mark.parametrize('reason', BAIT_QUESTION_FAMILIES)
def test_new_bait_only_paa_does_not_require_faq(conn, monkeypatch, reason):
    def ai(*args, stage='', **kwargs):
        assert stage == 'article_draft'
        return payload(PRODUCT_LINKS + FILLER)
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    result = _article_draft.generate_article_draft(conn, 'Fog Pro X', idea_serp_context={
        'audience_questions': [{'question': q} for q in BAIT_QUESTION_FAMILIES[reason]]})
    assert not extract_faqpage_question_names_from_body(result['body'])


@pytest.mark.parametrize('approved_paa', [False, True])
def test_bait_only_faq_repairs_with_explicit_hint(conn, monkeypatch, approved_paa):
    useful = 'How do I charge Fog Pro X?'
    stages = []
    def ai(*args, stage='', **kwargs):
        stages.append(stage)
        if stage == 'article_draft':
            return payload(PRODUCT_LINKS + FILLER + '<h4>What benefits does Beast Mode Max 2 offer?</h4><p>Reject.</p>')
        if stage == 'article_draft_faq_repair':
            return {'answers': ['Vaping is safe.']}  # Force the existing all-rejected repair path.
        assert stage == 'article_draft_append_repair'
        prompt = args[3][-1]['content']
        assert 'on-topic <h3> question + <p> answer pairs' in prompt
        assert 'specs, flavours, nicotine strength, charging, compatibility or storage' in prompt
        assert 'best vape/brand, benefits/advantages, longest-lasting, health or cigarettes' in prompt
        approved_hint = f'Approved PAA questions: ["{useful}"]' if approved_paa else 'Approved PAA questions: []'
        assert approved_hint in prompt
        return {'append_html': f'<h3>{useful}</h3><p>Use the supplied cable.</p>'}
    monkeypatch.setattr(_article_draft, '_call_ai', ai)
    result = _article_draft.generate_article_draft(conn, 'Fog Pro X', idea_serp_context={
        'audience_questions': [{'question': useful}] if approved_paa else []})
    assert 'article_draft_append_repair' in stages
    assert extract_faqpage_question_names_from_body(result['body']) == [useful]
    assert 'Reject.' not in result['body']
