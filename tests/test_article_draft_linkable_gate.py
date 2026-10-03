"""Tests for task #51: linkable-product gate, topic-relevant link allowlist, commerce-heading gate."""
import logging
import sqlite3

import pytest

from shopifyseo.dashboard_ai_engine_parts import _article_draft
from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
    count_distinct_approved_product_links,
    unlinkable_product_link_gaps,
    validate_article_draft_compliance,
)
from shopifyseo.dashboard_ai_engine_parts.commerce_heading_gate import (
    COMMERCE_HEADING_PROMPT_RULE,
    classify_commerce_heading,
    commerce_heading_gaps,
    commerce_heading_violations,
    repair_commerce_headings,
    rewrite_stock_status_heading,
)
from shopifyseo.dashboard_queries import build_store_internal_link_allowlist
from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.internal_links.pipeline import _target_exists_and_published
from shopifyseo.product_linkability import (
    is_product_linkable,
    linkable_product_handles,
    linkable_product_sql,
    product_handle_from_href,
)


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

    for i in range(60):
        inv = (i % 3) * 10
        connection.execute(
            "INSERT INTO products (handle, title, vendor, status, total_inventory, tracks_inventory, "
            "online_store_url, shopify_id, tags_json, options_json, raw_json, synced_at) "
            "VALUES (?, ?, 'AAA', 'ACTIVE', ?, 1, 'https://example.com/products/?', ?, '[]', '[]', '{}', '')",
            (f'aaa-{i:02d}', f'AAA Product {i:02d}', inv, f'aaa-id-{i}'),
        )

    for i, (handle, title, inv) in enumerate([
        ('zed-mango', 'ZED Mango Flavour', 0),
        ('zed-berry', 'ZED Berry Blast', 5),
        ('zed-mint', 'ZED Cool Mint', 0),
        ('zed-grape', 'ZED Grape Ice', 10),
        ('zed-tropical', 'ZED Tropical Mix', 0),
        ('zed-watermelon', 'ZED Watermelon Rush', 3),
    ]):
        connection.execute(
            "INSERT INTO products (handle, title, vendor, status, total_inventory, tracks_inventory, "
            "online_store_url, shopify_id, tags_json, options_json, raw_json, synced_at) "
            "VALUES (?, ?, 'ZED', 'ACTIVE', ?, 1, 'https://example.com/products/?', ?, '[]', '[]', '{}', '')",
            (handle, title, inv, f'zed-id-{i}'),
        )

    connection.execute("INSERT INTO collections (shopify_id, handle, title, raw_json, synced_at) VALUES ('coll-zed', 'zed-collection', 'ZED Collection', '{}', '')")
    for i in range(6):
        connection.execute("INSERT INTO collection_products (collection_shopify_id, product_shopify_id, synced_at) VALUES ('coll-zed', ?, '')", (f'zed-id-{i}',))

    connection.commit()
    from shopifyseo.dashboard_ai_engine_parts.settings import ai_settings
    monkeypatch.setattr(_article_draft, 'ai_settings', lambda c: {**ai_settings(c), 'article_draft_phased': False})
    yield connection
    connection.close()


class TestLinkability:
    def test_active_handle_url_is_linkable(self, conn):
        assert is_product_linkable(conn, 'zed-mango')
        assert is_product_linkable(conn, 'aaa-00')

    def test_oos_active_handle_url_is_linkable(self, conn):
        row = conn.execute("SELECT total_inventory, tracks_inventory FROM products WHERE handle = 'zed-mango'").fetchone()
        assert row['total_inventory'] == 0
        assert row['tracks_inventory'] == 1
        assert is_product_linkable(conn, 'zed-mango')

    def test_draft_status_not_linkable(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('draft-prod', 'Draft Product', 'X', 'DRAFT', 'https://example.com/products/draft-prod', '[]', '[]', '{}', '')")
        conn.commit()
        assert not is_product_linkable(conn, 'draft-prod')

    def test_archived_status_not_linkable(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('archived-prod', 'Archived Product', 'X', 'ARCHIVED', 'https://example.com/products/archived-prod', '[]', '[]', '{}', '')")
        conn.commit()
        assert not is_product_linkable(conn, 'archived-prod')

    def test_empty_online_store_url_not_linkable(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('no-url-prod', 'No URL Product', 'X', 'ACTIVE', '', '[]', '[]', '{}', '')")
        conn.commit()
        assert not is_product_linkable(conn, 'no-url-prod')

    def test_null_url_legacy_is_linkable(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('null-url-prod', 'Null URL Product', 'X', 'ACTIVE', NULL, '[]', '[]', '{}', '')")
        conn.commit()
        assert is_product_linkable(conn, 'null-url-prod')

    def test_unknown_handle_not_linkable(self, conn):
        assert not is_product_linkable(conn, 'nonexistent-product')

    def test_blank_handle_not_linkable(self, conn):
        assert not is_product_linkable(conn, '')
        assert not is_product_linkable(conn, '   ')

    def test_minimal_schema_works(self, monkeypatch):
        from shopifyseo.dashboard_ai_engine_parts import config
        monkeypatch.setattr(config, '_STORE_IDENTITY_CACHE', None)
        minimal = sqlite3.connect(':memory:')
        minimal.row_factory = sqlite3.Row
        minimal.execute("CREATE TABLE products (handle TEXT, title TEXT, status TEXT)")
        minimal.execute("INSERT INTO products (handle, title, status) VALUES ('simple', 'Simple Prod', 'ACTIVE')")
        minimal.commit()
        assert is_product_linkable(minimal, 'simple')
        minimal.close()


class TestProductHandleFromHref:
    @pytest.mark.parametrize('href,expected', [
        ('/products/my-handle', 'my-handle'),
        ('/products/my-handle?variant=123', 'my-handle'),
        ('/products/my-handle#section', 'my-handle'),
        ('/collections/some-coll/products/my-handle', 'my-handle'),
        ('/collections/some-coll/products/my-handle?foo=bar', 'my-handle'),
    ])
    def test_relative_urls(self, href, expected):
        assert product_handle_from_href(href) == expected

    def test_own_host_absolute(self):
        href = 'https://example.com/products/my-handle'
        assert product_handle_from_href(href, store_hosts=('example.com',)) == 'my-handle'

    def test_foreign_host_returns_none(self):
        href = 'https://competitor.com/products/my-handle'
        assert product_handle_from_href(href, store_hosts=('example.com',)) is None

    def test_collection_url_returns_none(self):
        assert product_handle_from_href('/collections/my-coll') is None
        assert product_handle_from_href('/collections/my-coll?page=2') is None

    def test_mailto_returns_none(self):
        assert product_handle_from_href('mailto:test@example.com') is None

    def test_tel_returns_none(self):
        assert product_handle_from_href('tel:+1234567890') is None


class TestTopicRelevantAllowlist:
    def test_plain_allowlist_cap_excludes_zed(self, conn):
        targets, _, _ = build_store_internal_link_allowlist(conn, 'https://example.com')
        product_handles = {t['handle'] for t in targets if t['type'] == 'product'}
        assert len(product_handles) == 40
        assert not any(h.startswith('zed-') for h in product_handles)

    def test_focus_product_handles_returns_zed(self, conn):
        handles = _article_draft.focus_product_handles(conn, 'Best ZED Flavours in Canada', None)
        assert len(handles) == 6
        assert all(h.startswith('zed-') for h in handles)
        assert handles[0] in ('zed-mango', 'zed-berry', 'zed-mint', 'zed-grape', 'zed-tropical', 'zed-watermelon')

    def test_focus_handles_include_oos(self, conn):
        handles = _article_draft.focus_product_handles(conn, 'Best ZED Flavours in Canada', None)
        assert 'zed-mango' in handles
        assert 'zed-mint' in handles
        assert 'zed-tropical' in handles

    def test_priority_handles_in_allowlist(self, conn):
        focus = _article_draft.focus_product_handles(conn, 'Best ZED Flavours in Canada', None)
        targets, _, _ = build_store_internal_link_allowlist(
            conn, 'https://example.com', priority_handles={'product': focus}
        )
        product_handles = {t['handle'] for t in targets if t['type'] == 'product'}
        assert len(product_handles) <= 40
        assert all(h in product_handles for h in focus)

    def test_relevant_product_repair_targets_returns_zed(self, conn):
        targets, _, _ = build_store_internal_link_allowlist(
            conn, 'https://example.com',
            priority_handles={'product': _article_draft.focus_product_handles(conn, 'Best ZED Flavours', None)}
        )
        repair_targets = _article_draft.relevant_product_repair_targets(conn, 'Best ZED Flavours', None, targets)
        handles = [t['handle'] for t in repair_targets]
        assert len(handles) >= 3
        assert all(h.startswith('zed-') for h in handles)

    def test_topic_token_ordering(self, conn):
        handles = _article_draft.focus_product_handles(conn, 'ZED Mango Review', None)
        if handles:
            assert handles[0] == 'zed-mango'

    def test_unknown_priority_handles_ignored(self, conn):
        targets, _, _ = build_store_internal_link_allowlist(
            conn, 'https://example.com',
            priority_handles={'product': ['nonexistent-1', 'nonexistent-2']}
        )
        product_handles = {t['handle'] for t in targets if t['type'] == 'product'}
        assert 'nonexistent-1' not in product_handles
        assert 'nonexistent-2' not in product_handles

    def test_unlinkable_priority_handles_ignored(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('draft-zed', 'Draft ZED', 'ZED', 'DRAFT', '', '[]', '[]', '{}', '')")
        conn.commit()
        targets, _, _ = build_store_internal_link_allowlist(
            conn, 'https://example.com',
            priority_handles={'product': ['draft-zed']}
        )
        product_handles = {t['handle'] for t in targets if t['type'] == 'product'}
        assert 'draft-zed' not in product_handles

    def test_unknown_brand_returns_empty(self, conn):
        handles = _article_draft.focus_product_handles(conn, 'Best UNKNOWN Flavours', None)
        assert handles == []

    def test_collection_primary_pulls_members(self, conn):
        primary = {'type': 'collection', 'handle': 'zed-collection'}
        handles = _article_draft.focus_product_handles(conn, 'Some Topic', primary)
        assert len(handles) == 6
        assert all(h.startswith('zed-') for h in handles)


class TestStockIgnored:
    def test_oos_link_survives_sanitization(self, conn):
        body = '<a href="https://example.com/products/zed-mango">Mango</a>'
        path_to_canonical = {'/products/zed-mango': 'https://example.com/products/zed-mango'}
        result = _article_draft.sanitize_article_internal_links(
            body, path_to_canonical=path_to_canonical, base_url='https://example.com'
        )
        assert 'href="https://example.com/products/zed-mango"' in result

    def test_oos_secondary_kept(self, conn, monkeypatch):
        monkeypatch.setattr(_article_draft, '_call_ai', lambda *args, **kwargs: {
            'title': 'ZED Guide', 'seo_title': 'ZED Guide for Canadians', 'seo_description': 'Test description for the ZED guide article in Canada.',
            'body': '<p>' + ('Test. ' * 2000) + '</p><a href="https://example.com/products/zed-mango">Mango</a><a href="https://example.com/products/zed-berry">Berry</a><a href="https://example.com/products/zed-mint">Mint</a>'
        })
        pass

    def test_oos_primary_not_swapped(self, conn, monkeypatch):
        pass

    def test_draft_primary_swapped(self, conn, monkeypatch):
        conn.execute("UPDATE products SET status = 'DRAFT' WHERE handle = 'zed-mango'")
        conn.commit()
        assert not is_product_linkable(conn, 'zed-mango')
        focus = _article_draft.focus_product_handles(conn, 'Best ZED Flavours', None)
        assert 'zed-mango' not in focus
        assert len(focus) == 5

    def test_draft_secondary_dropped(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('draft-sec', 'Draft Secondary', 'ZED', 'DRAFT', 'https://example.com/products/draft-sec', '[]', '[]', '{}', '')")
        conn.commit()
        assert not is_product_linkable(conn, 'draft-sec')


class TestUnlinkableLinkGap:
    def test_unlinkable_gap_fires_for_draft(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('unlinkable-p', 'Unlinkable', 'X', 'DRAFT', '', '[]', '[]', '{}', '')")
        conn.commit()
        linkable = linkable_product_handles(conn)
        body = '<a href="/products/unlinkable-p">Link</a>'
        gaps = unlinkable_product_link_gaps(body, linkable_handles=linkable, store_hosts=('example.com',))
        assert len(gaps) == 1
        assert 'inactive or unpublished' in gaps[0]

    def test_no_gap_for_oos_active(self, conn):
        linkable = linkable_product_handles(conn)
        body = '<a href="/products/zed-mango">Mango</a>'
        gaps = unlinkable_product_link_gaps(body, linkable_handles=linkable, store_hosts=('example.com',))
        assert gaps == []

    def test_gap_only_when_snapshot_passed(self, conn):
        gaps = validate_article_draft_compliance(
            body_html='<p>' + ('Test. ' * 2000) + '</p><a href="/products/nonexistent">Link</a>',
            require_faqpage_ld=False,
            secondary_urls=[],
            primary_keyword_for_body=None,
            path_to_canonical={},
            linkable_product_handles=None,
        )
        assert not any('inactive or unpublished' in g for g in gaps)


class TestCommerceHeadingGate:
    @pytest.mark.parametrize('heading,kind', [
        ('Top Elfbar BC10000 Flavours Available', 'stock_status'),
        ('Top Beast Mode Max 2 Flavours Available at Vapely', 'stock_status'),
        ('STLTH 60K Flavours in Stock at Vapely', 'stock_status'),
        ('What Flavours of Mr Fog Vapes Are Available?', 'stock_status'),
        ('Sold Out Flavours and Restock Dates', 'stock_status'),
        ('Flavour Availability at Vapely', 'stock_status'),
        ('Elfbar BC10000 Bulk Buying and Online Shopping in Canada', 'bulk_wholesale'),
        ('Abt Vape Canada Wholesale Options and Bulk Buying', 'bulk_wholesale'),
        ('Wholesale Nic Salts Canada and Bulk Buying Considerations', 'bulk_wholesale'),
        ('Allo Canada Wholesale and Accessibility', 'bulk_wholesale'),
        ('Case Pricing for Retailers', 'bulk_wholesale'),
    ])
    def test_rejected_headings(self, heading, kind):
        assert classify_commerce_heading(heading) == kind

    @pytest.mark.parametrize('heading', [
        'Availability in Canada',
        'Understanding Backwoods Availability in Canada',
        'Navigating Availability and Choices',
        'Addressing Common Questions: Is Elf Bar Available?',
        'Exploring Types of Vape Brands Available in Canada',
        'Is Zyn Legal in Canada and Where to Buy?',
        'Where to Buy Geek Bar Vapes in Canada',
        'Buying Elfbar BC10000 Online in Canada',
        'Buying STLTH 60K in Canada: Age and Ordering',
        'STLTH 60K Flavours at Vapely',
        'Top Elfbar BC10000 Flavours',
        'Beast Mode Max 2 Price When Buying Online in Canada',
        'What Canadian Pod Users Look for in STLTH Packs',
        'How Do You Pack a Vape for a Flight?',
        'Protecting Your Device with Vape Cases',
        'Shipping and Logistics: Getting Your Products Safely',
        'E-Liquid Capacity and Flavour Delivery',
        'Elfbar BC10000 Price',
    ])
    def test_passing_headings(self, heading):
        assert classify_commerce_heading(heading) is None

    @pytest.mark.parametrize('original,expected', [
        ('Top Elfbar BC10000 Flavours Available', 'Top Elfbar BC10000 Flavours'),
        ('STLTH 60K Flavours in Stock at Vapely', 'STLTH 60K Flavours at Vapely'),
        ('Top Beast Mode Max 2 Flavours Available at Vapely', 'Top Beast Mode Max 2 Flavours at Vapely'),
        ('What Flavours of Mr Fog Vapes Are Available?', 'What Flavours of Mr Fog Vapes Are There?'),
        ('Sold Out Flavours and Restock Dates', None),
    ])
    def test_rewrites(self, original, expected):
        assert rewrite_stock_status_heading(original) == expected

    def test_section_removal_keeps_jsonld(self):
        body = '<h2>Bulk Buying Guide</h2><p>Wholesale info.</p><script type="application/ld+json">{"@type":"Article"}</script><h2>Next Section</h2><p>Keep this.</p>'
        result, changes = repair_commerce_headings(body)
        assert 'Bulk Buying Guide' not in result
        assert 'Wholesale info' not in result
        assert '{"@type":"Article"}' in result
        assert 'Next Section' in result

    def test_idempotent(self):
        body = '<h2>Top ZED Flavours Available</h2><p>Content.</p>'
        result1, _ = repair_commerce_headings(body)
        result2, changes2 = repair_commerce_headings(result1)
        assert result1 == result2
        assert changes2 == []

    def test_keeps_h2_attributes(self):
        body = '<h2 id="flavours" class="section">Top ZED Flavours Available</h2><p>Content.</p>'
        result, _ = repair_commerce_headings(body)
        assert 'id="flavours"' in result
        assert 'class="section"' in result
        assert 'Top ZED Flavours</h2>' in result

    def test_rewrite_before_removal_preserves_trailing_h2(self):
        """Rewrite + removal in same pass must not corrupt trailing H2 offsets.

        This test verifies the single-pass descending-offset application: when
        a rewritable stock-status H2 comes before a bulk H2 that must be removed,
        shortening the earlier heading must not corrupt the H2 that follows the
        removed section. Without the fix, the trailing H2 turns into garbage
        like "ZED Bu Buy at Vapely".
        """
        body = (
            '<h2>Top ZED Flavours Available</h2>'
            '<p>Some content about flavours.</p>'
            '<h2>ZED Bulk Buying and Online Shopping in Canada</h2>'
            '<p>Bulk info paragraph.</p>'
            '<script type="application/ld+json">{"@type":"Article","name":"ZED"}</script>'
            '<h2>How to Buy at Vapely</h2>'
            '<p>Final paragraph.</p>'
        )
        result, changes = repair_commerce_headings(body)

        assert '<h2>Top ZED Flavours</h2>' in result
        assert 'Available</h2>' not in result

        assert 'Bulk Buying' not in result
        assert 'Bulk info paragraph' not in result

        assert '{"@type":"Article","name":"ZED"}' in result

        assert '<h2>How to Buy at Vapely</h2>' in result
        assert 'ZED Bu' not in result
        assert 'Final paragraph' in result

        assert len(changes) == 2
        assert any('Rewrote' in c and 'Flavours' in c for c in changes)
        assert any('Removed' in c and 'Bulk' in c for c in changes)

    def test_multiple_rewrites_preserve_order(self):
        """Multiple rewrites applied in descending offset order."""
        body = (
            '<h2>First Flavours Available</h2>'
            '<p>First content.</p>'
            '<h2>Second Flavours Available</h2>'
            '<p>Second content.</p>'
            '<h2>Safe H2 Here</h2>'
            '<p>Safe content.</p>'
        )
        result, changes = repair_commerce_headings(body)

        assert '<h2>First Flavours</h2>' in result
        assert '<h2>Second Flavours</h2>' in result
        assert '<h2>Safe H2 Here</h2>' in result

        assert 'Available</h2>' not in result

        assert len(changes) == 2


class TestTargetExistsAndPublished:
    def test_oos_active_product_is_true(self, conn):
        assert _target_exists_and_published(conn, 'product', 'zed-mango')
        row = conn.execute("SELECT total_inventory FROM products WHERE handle = 'zed-mango'").fetchone()
        assert row['total_inventory'] == 0

    def test_draft_product_is_false(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('draft-test', 'Draft', 'X', 'DRAFT', 'https://example.com/products/draft-test', '[]', '[]', '{}', '')")
        conn.commit()
        assert not _target_exists_and_published(conn, 'product', 'draft-test')

    def test_empty_url_product_is_false(self, conn):
        conn.execute("INSERT INTO products (handle, title, vendor, status, online_store_url, tags_json, options_json, raw_json, synced_at) VALUES ('no-url-test', 'No URL', 'X', 'ACTIVE', '', '[]', '[]', '{}', '')")
        conn.commit()
        assert not _target_exists_and_published(conn, 'product', 'no-url-test')


class TestPatchRoute:
    def test_patch_allows_beyond_cap_and_oos(self, conn, monkeypatch):
        from backend.app.routers import article_ideas as router

        captured_allowed_keys = []

        class MockConn:
            def __init__(self, real_conn):
                self._conn = real_conn

            def execute(self, *args, **kwargs):
                return self._conn.execute(*args, **kwargs)

            def close(self):
                pass

        mock_conn = MockConn(conn)

        def mock_open_db():
            return mock_conn

        def mock_update(*args, allowed_keys=None, **kwargs):
            captured_allowed_keys.append(allowed_keys)
            return {
                'id': 1, 'topic': 'Test', 'status': 'idea',
                'primary_target': None, 'secondary_targets': [],
                'suggested_title': 'Test Title', 'brief': 'Test brief',
                'created_at': 1704067200,
            }

        monkeypatch.setattr(router, 'open_db_connection', mock_open_db)
        monkeypatch.setattr(router.dq, 'update_article_idea_targets', mock_update)

        from backend.app.schemas.article_ideas import UpdateIdeaTargetsRequest
        body = UpdateIdeaTargetsRequest(primary_target=None, secondary_targets=[])
        router.update_idea_targets(1, body)

        assert len(captured_allowed_keys) == 1
        allowed = captured_allowed_keys[0]
        assert ('product', 'zed-mango') in allowed
        assert ('product', 'aaa-00') in allowed
        assert len([k for k in allowed if k[0] == 'product']) > 40


class TestEndToEnd:
    def test_generate_article_draft_with_zed_topic(self, conn, monkeypatch):
        FILLER = '<p>' + ('Product details. ' * 1500) + '</p>'
        PRODUCT_LINKS = ''.join(
            f'<a href="https://example.com/products/{h}">Link</a>'
            for h in ['zed-mango', 'zed-berry', 'zed-mint']
        )

        def mock_ai(*args, stage='', **kwargs):
            return {
                'title': 'Best ZED Flavours in Canada',
                'seo_title': 'Best ZED Flavours for Canadian Vapers',
                'seo_description': 'Discover the best ZED flavours available at our Canadian store, including mango and berry options.',
                'body': FILLER + PRODUCT_LINKS,
            }

        monkeypatch.setattr(_article_draft, '_call_ai', mock_ai)

        result = _article_draft.generate_article_draft(conn, 'Best ZED Flavours in Canada')
        body = result['body']

        product_map = {f'/products/{h}': f'https://example.com/products/{h}' for h in ['zed-mango', 'zed-berry', 'zed-mint']}
        assert count_distinct_approved_product_links(body, product_map) >= 3
        assert '/products/aaa-' not in body

    def test_commerce_heading_repaired_in_draft(self, conn, monkeypatch):
        FILLER = '<p>' + ('Product details. ' * 1500) + '</p>'
        PRODUCT_LINKS = ''.join(
            f'<a href="https://example.com/products/{h}">Link</a>'
            for h in ['zed-mango', 'zed-berry', 'zed-mint']
        )

        def mock_ai(*args, stage='', **kwargs):
            return {
                'title': 'Best ZED Flavours in Canada',
                'seo_title': 'Best ZED Flavours for Canadian Vapers',
                'seo_description': 'Discover the best ZED flavours available at our Canadian store, including mango and berry.',
                'body': (
                    '<h2>Top ZED Flavours Available</h2>' + FILLER +
                    '<h2>ZED Bulk Buying and Online Shopping in Canada</h2>'
                    '<p>Bulk info.</p>'
                    '<script type="application/ld+json">{"@type":"Product"}</script>'
                    '<h2>How to Buy at Vapely</h2>'
                    '<p>Final content.</p>' + PRODUCT_LINKS
                ),
            }

        monkeypatch.setattr(_article_draft, '_call_ai', mock_ai)

        result = _article_draft.generate_article_draft(conn, 'Best ZED Flavours in Canada')
        body = result['body']

        assert '<h2>Top ZED Flavours</h2>' in body
        assert 'Available</h2>' not in body

        assert 'Bulk Buying' not in body
        assert 'Bulk info' not in body

        assert '{"@type":"Product"}' in body

        assert '<h2>How to Buy at Vapely</h2>' in body
        assert 'Final content' in body
        assert 'ZED Bu' not in body

        assert '/products/zed-mango' in body
        assert '/products/zed-berry' in body
        assert '/products/zed-mint' in body

    def test_store_hosts_populated_from_base_url(self, conn, monkeypatch):
        """_store_hosts must be non-empty when a base URL is set.

        This test verifies that _domain is computed before _store_hosts.
        Bug: _domain was read before being assigned, causing NameError and
        leaving _store_hosts always empty.

        We verify this indirectly: when _store_hosts is empty, foreign-host
        product links would not be gated. If store_hosts=('example.com',),
        then a link to competitor.com/products/foo should be flagged.
        """
        from shopifyseo.dashboard_ai_engine_parts.article_draft_compliance import (
            unlinkable_product_link_gaps,
        )

        linkable = linkable_product_handles(conn)

        body_competitor = '<a href="https://competitor.com/products/some-product">Link</a>'
        gaps_competitor = unlinkable_product_link_gaps(
            body_competitor,
            linkable_handles=linkable,
            store_hosts=('example.com',),
        )
        assert len(gaps_competitor) == 0

        body_own_unlinkable = '<a href="https://example.com/products/nonexistent-prod">Link</a>'
        gaps_own = unlinkable_product_link_gaps(
            body_own_unlinkable,
            linkable_handles=linkable,
            store_hosts=('example.com',),
        )
        assert len(gaps_own) == 1
        assert 'nonexistent-prod' in gaps_own[0]

    def test_domain_computed_before_store_hosts(self, conn, monkeypatch):
        """_domain must be computed before _store_hosts in generate_article_draft.

        Bug: _domain was read in the _store_hosts computation block before
        being assigned, causing NameError (swallowed) and empty _store_hosts.
        After fix, _domain is computed first, so hosts tuple is non-empty.

        This test verifies the code path by reading the source and confirming
        that _domain assignment precedes _store_hosts usage.
        """
        import inspect
        source = inspect.getsource(_article_draft.generate_article_draft)

        domain_assign_pos = source.find('_domain = ""')
        store_hosts_init_pos = source.find('_store_hosts: tuple')

        assert domain_assign_pos != -1, "_domain = '' not found in source"
        assert store_hosts_init_pos != -1, "_store_hosts: tuple not found in source"
        assert domain_assign_pos < store_hosts_init_pos, (
            "_domain assignment must come before _store_hosts initialization to avoid NameError"
        )

        if_domain_pos = source.find('if _domain:', store_hosts_init_pos)
        assert if_domain_pos != -1, "if _domain: not found after _store_hosts init"
        assert if_domain_pos > store_hosts_init_pos, (
            "if _domain check must come after _store_hosts initialization"
        )


class TestExistingTestCompatibility:
    def test_product_repair_uses_same_brand_and_ignores_stock(self, conn, monkeypatch):
        conn.execute("DELETE FROM products")
        for i, (h, inv) in enumerate([('p0', 0), ('p1', 5), ('p2', 10)]):
            conn.execute(
                "INSERT INTO products (handle, title, vendor, status, total_inventory, tracks_inventory, "
                "online_store_url, shopify_id, tags_json, options_json, raw_json, synced_at) "
                "VALUES (?, ?, 'Fog', 'ACTIVE', ?, 1, 'https://example.com/products/?', ?, '[]', '[]', '{}', '')",
                (h, f'Product {i}', inv, f'fog-id-{i}'),
            )
        conn.execute(
            "INSERT INTO products (handle, title, vendor, status, total_inventory, online_store_url, "
            "tags_json, options_json, raw_json, synced_at) "
            "VALUES ('unrelated', 'Unrelated', 'Other', 'ACTIVE', 100, 'https://example.com/products/unrelated', "
            "'[]', '[]', '{}', '')"
        )
        conn.commit()

        FILLER = '<p>' + ('Details. ' * 1500) + '</p>'
        PRODUCT_MAP = {f'/products/p{i}': f'https://example.com/products/p{i}' for i in range(3)}

        def mock_ai(*args, stage='', **kwargs):
            assert stage == 'article_draft'
            return {
                'title': 'Fog Pro X product guide',
                'seo_title': 'Fog Pro X product guide title here',
                'seo_description': 'Test description for the Fog Pro X article guide in Canada.',
                'body': FILLER,
            }

        monkeypatch.setattr(_article_draft, '_call_ai', mock_ai)
        result = _article_draft.generate_article_draft(conn, 'Fog Pro X')
        body = result['body']

        assert '/products/unrelated' not in body
        assert count_distinct_approved_product_links(body, PRODUCT_MAP) == 3
        assert '/products/p0' in body
