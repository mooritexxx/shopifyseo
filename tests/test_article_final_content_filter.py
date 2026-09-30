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
    original = FILLER + '<h3>Which flavours are available?</h3><p>Berry.</p>'
    appended = PRODUCT_LINKS + '<h2>Helpful questions before you choose</h2><h3>Which flavors are available?</h3><p>Duplicate.</p><h3>Can a dentist tell if you vape?</h3><p>Reject.</p>'
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
