"""Partial SEO writes must not replace Shopify's whole seo object with blanks.

Shopify product/collection ``seo`` is replaced as a unit. Sending only
``seo.description`` clears ``seo.title``. These tests lock the fill-from-current
behavior on the product API, the CLI, and omitted collection/page bodies.
"""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.seo_quality import build_seo_input


CURRENT_SEO_TITLE = "Current Product SEO Title"
CURRENT_BODY = "<p>Existing product body that must not be wiped.</p>"
CURRENT_TAGS = '["bestseller", "vape"]'
NEW_DESCRIPTION = (
    "Shop this flavour online today with fast Canadian shipping plus expert "
    "support for vapers everywhere across Canada now."
)


def _ok_product_update():
    return {
        "data": {
            "productUpdate": {
                "product": {"id": "gid://shopify/Product/1", "handle": "sample-product"},
                "userErrors": [],
            }
        }
    }


def _ok_collection_update():
    return {
        "data": {
            "collectionUpdate": {
                "collection": {"id": "gid://shopify/Collection/1"},
                "userErrors": [],
            }
        }
    }


def _ok_page_update():
    return {
        "data": {
            "pageUpdate": {
                "page": {"id": "gid://shopify/Page/1"},
                "userErrors": [],
            }
        }
    }


def _mutation_variables(mock_gql, needle: str) -> dict:
    for args, _kwargs in mock_gql.call_args_list:
        query = args[0]
        variables = args[1] if len(args) > 1 else {}
        if needle in query:
            return variables
    raise AssertionError(f"No GraphQL call contained {needle!r}: {mock_gql.call_args_list}")


class TestBuildSeoInput:
    def test_fills_omitted_title_from_current(self):
        seo = build_seo_input(
            {"title": CURRENT_SEO_TITLE, "description": "Old desc"},
            title=None,
            description=NEW_DESCRIPTION,
        )
        assert seo == {"title": CURRENT_SEO_TITLE, "description": NEW_DESCRIPTION}

    def test_raises_when_clearing_nonempty_title(self):
        with pytest.raises(ValueError, match="seo.title"):
            build_seo_input(
                {"title": CURRENT_SEO_TITLE, "description": "Old desc"},
                title="",
                description=NEW_DESCRIPTION,
            )

    def test_allows_empty_when_current_is_already_empty(self):
        seo = build_seo_input({"title": "", "description": ""}, title="", description="")
        assert seo == {"title": "", "description": ""}


def _insert_product(conn, handle="sample-product"):
    shopify_id = "gid://shopify/Product/1"
    conn.execute(
        """
        INSERT INTO products (
            shopify_id, handle, title, vendor, status, seo_title, seo_description,
            description_html, tags_json, product_type, total_inventory,
            online_store_url, options_json, raw_json, synced_at
        ) VALUES (?, ?, ?, 'TestBrand', 'ACTIVE', ?, ?, ?, ?, 'Vape', 100,
                  ?, '[]', '{}', datetime('now'))
        """,
        (
            shopify_id,
            handle,
            "Sample Product",
            CURRENT_SEO_TITLE,
            "Existing product SEO description stored locally.",
            CURRENT_BODY,
            CURRENT_TAGS,
            f"https://example.com/products/{handle}",
        ),
    )
    conn.commit()
    return shopify_id


class TestProductApiDescriptionOnly:
    def test_description_only_preserves_seo_title_and_omits_body_tags(
        self, testdb, monkeypatch
    ):
        from backend.app.main import app
        from backend.app import db as db_module
        from backend.app.services import product_service
        from shopifyseo import dashboard_live_updates

        conn = testdb.connect()
        ensure_dashboard_schema(conn)
        _insert_product(conn)
        conn.close()

        monkeypatch.setattr(db_module, "open_db_connection", testdb.connect)
        monkeypatch.setattr(product_service, "open_db_connection", testdb.connect)

        with (
            patch.object(dashboard_live_updates, "graphql_request") as mock_gql,
            patch.object(dashboard_live_updates, "sync_product"),
        ):
            mock_gql.return_value = _ok_product_update()
            client = TestClient(app)
            response = client.post(
                "/api/products/sample-product/update",
                json={"seo_description": NEW_DESCRIPTION},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["ok"] is True

        variables = _mutation_variables(mock_gql, "productUpdate")
        product_input = variables["input"]
        assert product_input["seo"]["title"] == CURRENT_SEO_TITLE
        assert product_input["seo"]["description"] == NEW_DESCRIPTION
        assert "descriptionHtml" not in product_input
        assert "tags" not in product_input

        saved = testdb.connect().execute(
            "SELECT seo_title, seo_description, description_html, tags_json FROM products WHERE handle = ?",
            ("sample-product",),
        ).fetchone()
        assert saved["seo_title"] == CURRENT_SEO_TITLE
        assert saved["seo_description"] == NEW_DESCRIPTION
        assert saved["description_html"] == CURRENT_BODY
        assert saved["tags_json"] == CURRENT_TAGS


class TestCliDescriptionOnly:
    def test_product_cli_description_only_sends_current_title(self):
        from shopifyseo.shopify_admin import update_product_seo

        args = argparse.Namespace(
            id="gid://shopify/Product/1",
            seo_title=None,
            seo_description=NEW_DESCRIPTION,
            body_html="",
            tags="",
            allow_clear=False,
        )
        with patch("shopifyseo.shopify_admin.graphql_request") as mock_gql:
            mock_gql.side_effect = [
                {
                    "data": {
                        "product": {
                            "seo": {
                                "title": CURRENT_SEO_TITLE,
                                "description": "Old description",
                            }
                        }
                    }
                },
                _ok_product_update(),
            ]
            update_product_seo(args)

        variables = _mutation_variables(mock_gql, "productUpdate")
        product_input = variables["input"]
        assert product_input["seo"]["title"] == CURRENT_SEO_TITLE
        assert product_input["seo"]["description"] == NEW_DESCRIPTION
        assert "descriptionHtml" not in product_input
        assert "tags" not in product_input

    def test_collection_cli_description_only_sends_current_title(self):
        from shopifyseo.shopify_admin import update_collection_seo

        args = argparse.Namespace(
            id="gid://shopify/Collection/1",
            title="",
            seo_title=None,
            seo_description=NEW_DESCRIPTION,
            body_html="",
            allow_clear=False,
        )
        with patch("shopifyseo.shopify_admin.graphql_request") as mock_gql:
            mock_gql.side_effect = [
                {
                    "data": {
                        "collection": {
                            "seo": {
                                "title": "Current Collection SEO Title",
                                "description": "Old collection description",
                            }
                        }
                    }
                },
                _ok_collection_update(),
            ]
            update_collection_seo(args)

        variables = _mutation_variables(mock_gql, "collectionUpdate")
        collection_input = variables["input"]
        assert collection_input["seo"]["title"] == "Current Collection SEO Title"
        assert collection_input["seo"]["description"] == NEW_DESCRIPTION
        assert "descriptionHtml" not in collection_input

    def test_allow_clear_sends_blank_description_and_keeps_title(self):
        from shopifyseo.shopify_admin import update_product_seo

        args = argparse.Namespace(
            id="gid://shopify/Product/1",
            seo_title=None,
            seo_description="",
            body_html="",
            tags="",
            allow_clear=True,
        )
        with patch("shopifyseo.shopify_admin.graphql_request") as mock_gql:
            mock_gql.side_effect = [
                {
                    "data": {
                        "product": {
                            "seo": {
                                "title": CURRENT_SEO_TITLE,
                                "description": "Old description",
                            }
                        }
                    }
                },
                _ok_product_update(),
            ]
            update_product_seo(args)

        product_input = _mutation_variables(mock_gql, "productUpdate")["input"]
        assert product_input["seo"]["title"] == CURRENT_SEO_TITLE
        assert product_input["seo"]["description"] == ""

    def test_blank_description_without_allow_clear_raises(self):
        from shopifyseo.shopify_admin import update_product_seo

        args = argparse.Namespace(
            id="gid://shopify/Product/1",
            seo_title=None,
            seo_description="",
            body_html="",
            tags="",
            allow_clear=False,
        )
        with patch("shopifyseo.shopify_admin.graphql_request") as mock_gql:
            mock_gql.return_value = {
                "data": {
                    "product": {
                        "seo": {
                            "title": CURRENT_SEO_TITLE,
                            "description": "Old description",
                        }
                    }
                }
            }
            with pytest.raises(ValueError, match="seo.description"):
                update_product_seo(args)


class TestLiveUpdateOmitsUnchangedBody:
    def test_collection_omits_description_html_when_body_is_none(self):
        from shopifyseo import dashboard_live_updates

        with (
            patch.object(dashboard_live_updates, "graphql_request") as mock_gql,
            patch.object(dashboard_live_updates, "sync_collection"),
        ):
            def fake_gql(query, variables=None):
                if "metafieldsSet" in query:
                    return {"data": {"metafieldsSet": {"metafields": [], "userErrors": []}}}
                return _ok_collection_update()

            mock_gql.side_effect = fake_gql
            dashboard_live_updates.live_update_collection(
                "/tmp/test.db",
                "gid://shopify/Collection/1",
                "Disposable Vapes",
                "Disposable Vapes Canada",
                "Shop disposable vapes in Canada.",
                None,
            )

        collection_input = _mutation_variables(mock_gql, "collectionUpdate")["input"]
        assert "descriptionHtml" not in collection_input
        assert collection_input["title"] == "Disposable Vapes"

    def test_page_omits_body_when_body_is_none(self):
        from shopifyseo import dashboard_live_updates

        with (
            patch.object(dashboard_live_updates, "graphql_request") as mock_gql,
            patch.object(dashboard_live_updates, "sync_page"),
        ):
            mock_gql.return_value = _ok_page_update()
            dashboard_live_updates.live_update_page(
                "/tmp/test.db",
                "gid://shopify/Page/1",
                "Contact",
                "Contact Us",
                "Get in touch with our team.",
                None,
            )

        page_input = _mutation_variables(mock_gql, "pageUpdate")["page"]
        assert "body" not in page_input
        assert page_input["title"] == "Contact"
