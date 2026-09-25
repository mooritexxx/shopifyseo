"""Tests for partial article update functionality.

Verifies that:
1. Metadata-only updates leave body, seo_title, seo_description untouched
2. New fields (author_name, summary, featured_image_alt) reach Shopify
3. Alt-only updates return a warning about Shopify image re-upload
4. Existing full update flows still work
5. The featured_image_alt column exists in the real schema
"""
import pytest
from unittest.mock import MagicMock, patch, call

from backend.app.schemas.content import ArticleUpdatePayload


class TestArticleUpdatePayloadSchema:
    """Test the ArticleUpdatePayload schema."""

    def test_all_fields_optional(self):
        """All fields should default to None."""
        payload = ArticleUpdatePayload()
        assert payload.title is None
        assert payload.seo_title is None
        assert payload.seo_description is None
        assert payload.body_html is None
        assert payload.workflow_status is None
        assert payload.workflow_notes is None
        assert payload.author_name is None
        assert payload.summary is None
        assert payload.featured_image_alt is None

    def test_partial_payload(self):
        """Should allow setting only some fields."""
        payload = ArticleUpdatePayload(
            workflow_status="Ready",
            workflow_notes="Approved for publish",
        )
        assert payload.workflow_status == "Ready"
        assert payload.workflow_notes == "Approved for publish"
        assert payload.title is None
        assert payload.body_html is None

    def test_exclude_none_serialization(self):
        """model_dump(exclude_none=True) should omit None fields."""
        payload = ArticleUpdatePayload(
            title="New Title",
            author_name="Vapely",
        )
        data = payload.model_dump(exclude_none=True)
        assert data == {"title": "New Title", "author_name": "Vapely"}
        assert "body_html" not in data
        assert "seo_title" not in data

    def test_new_fields_present(self):
        """Schema should have new fields: author_name, summary, featured_image_alt."""
        payload = ArticleUpdatePayload(
            author_name="Vapely",
            summary="Article excerpt",
            featured_image_alt="A vape product image",
        )
        assert payload.author_name == "Vapely"
        assert payload.summary == "Article excerpt"
        assert payload.featured_image_alt == "A vape product image"


class TestLiveUpdateArticlePartialUpdate:
    """Test live_update_article partial update behavior."""

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    @patch("shopifyseo.dashboard_live_updates.sync_article")
    def test_metadata_only_update_excludes_body(self, mock_sync, mock_gql):
        """A metadata-only update should NOT include body in the mutation."""
        from shopifyseo.dashboard_live_updates import live_update_article

        mock_gql.return_value = {
            "data": {
                "articleUpdate": {
                    "article": {"id": "gid://article/1", "handle": "test"},
                    "userErrors": [],
                }
            }
        }

        result, warnings = live_update_article(
            "/tmp/test.db",
            "gid://article/1",
            author_name="Vapely",
        )

        # Verify the mutation was called
        assert mock_gql.called
        mutation_vars = mock_gql.call_args[0][1]
        article_input = mutation_vars["article"]

        # body should NOT be in the input
        assert "body" not in article_input
        # author should be set
        assert article_input.get("author") == {"name": "Vapely"}
        # No warnings for non-image updates
        assert warnings == []

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    @patch("shopifyseo.dashboard_live_updates.sync_article")
    def test_full_update_includes_all_fields(self, mock_sync, mock_gql):
        """A full update should include all provided fields."""
        from shopifyseo.dashboard_live_updates import live_update_article

        mock_gql.return_value = {
            "data": {
                "articleUpdate": {
                    "article": {"id": "gid://article/1", "handle": "test"},
                    "userErrors": [],
                }
            }
        }

        result, warnings = live_update_article(
            "/tmp/test.db",
            "gid://article/1",
            title="New Title",
            seo_title="SEO Title",
            seo_description="SEO Desc",
            body_html="<p>Body</p>",
            author_name="Vapely",
            summary="Excerpt",
        )

        mutation_vars = mock_gql.call_args[0][1]
        article_input = mutation_vars["article"]

        assert article_input.get("title") == "New Title"
        assert article_input.get("body") == "<p>Body</p>"
        assert article_input.get("author") == {"name": "Vapely"}
        assert article_input.get("summary") == "Excerpt"
        assert "metafields" in article_input
        assert warnings == []

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    @patch("shopifyseo.dashboard_live_updates.sync_article")
    def test_empty_update_does_nothing(self, mock_sync, mock_gql):
        """An update with no fields should not make a Shopify request."""
        from shopifyseo.dashboard_live_updates import live_update_article

        result, warnings = live_update_article(
            "/tmp/test.db",
            "gid://article/1",
        )

        # Should return early without calling GraphQL
        assert not mock_gql.called
        assert result == {"article": None, "userErrors": []}
        assert warnings == []

    @patch("shopifyseo.dashboard_live_updates._fetch_article_image")
    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    @patch("shopifyseo.dashboard_live_updates.sync_article")
    def test_alt_only_update_reuses_existing_image(self, mock_sync, mock_gql, mock_fetch):
        """Alt-only update should fetch existing image and reuse its URL, but warn about re-upload."""
        from shopifyseo.dashboard_live_updates import live_update_article

        mock_fetch.return_value = {
            "url": "https://cdn.shopify.com/existing-image.jpg",
            "altText": "Old alt",
        }
        mock_gql.return_value = {
            "data": {
                "articleUpdate": {
                    "article": {"id": "gid://article/1", "handle": "test"},
                    "userErrors": [],
                }
            }
        }

        result, warnings = live_update_article(
            "/tmp/test.db",
            "gid://article/1",
            image_alt="New alt text",
        )

        # Should fetch existing image
        mock_fetch.assert_called_once_with("gid://article/1")

        # Should include image with existing URL and new alt
        mutation_vars = mock_gql.call_args[0][1]
        article_input = mutation_vars["article"]
        assert article_input.get("image") == {
            "url": "https://cdn.shopify.com/existing-image.jpg",
            "altText": "New alt text",
        }

        # Should return warning about Shopify re-upload bug
        assert len(warnings) == 1
        assert "re-uploaded" in warnings[0].lower()
        assert "shopify" in warnings[0].lower()
        assert "community.shopify.dev" in warnings[0]

    @patch("shopifyseo.dashboard_live_updates._fetch_article_image")
    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    @patch("shopifyseo.dashboard_live_updates.sync_article")
    def test_alt_only_update_no_existing_image(self, mock_sync, mock_gql, mock_fetch):
        """Alt-only update with no existing image should be a no-op for image."""
        from shopifyseo.dashboard_live_updates import live_update_article

        mock_fetch.return_value = None  # No existing image
        mock_gql.return_value = {
            "data": {
                "articleUpdate": {
                    "article": {"id": "gid://article/1", "handle": "test"},
                    "userErrors": [],
                }
            }
        }

        result, warnings = live_update_article(
            "/tmp/test.db",
            "gid://article/1",
            title="Title",  # Include something so the update happens
            image_alt="New alt text",
        )

        # Should have fetched image to check
        mock_fetch.assert_called_once()

        # Should NOT include image in payload (no existing image to update)
        mutation_vars = mock_gql.call_args[0][1]
        article_input = mutation_vars["article"]
        assert "image" not in article_input

        # No warning when no image was updated
        assert warnings == []

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    @patch("shopifyseo.dashboard_live_updates.sync_article")
    def test_new_image_with_alt(self, mock_sync, mock_gql):
        """New image URL with alt should work as before, no warning."""
        from shopifyseo.dashboard_live_updates import live_update_article

        mock_gql.return_value = {
            "data": {
                "articleUpdate": {
                    "article": {"id": "gid://article/1", "handle": "test"},
                    "userErrors": [],
                }
            }
        }

        result, warnings = live_update_article(
            "/tmp/test.db",
            "gid://article/1",
            image_url="https://example.com/new-image.jpg",
            image_alt="New image alt",
        )

        mutation_vars = mock_gql.call_args[0][1]
        article_input = mutation_vars["article"]
        assert article_input.get("image") == {
            "url": "https://example.com/new-image.jpg",
            "altText": "New image alt",
        }

        # No warning when explicitly providing a new image URL
        assert warnings == []


class TestApplySavedBlogArticleFieldsPartialUpdate:
    """Test apply_saved_blog_article_fields_from_editor partial update."""

    def test_partial_update_only_updates_provided_fields(self, tmp_path):
        """Only provided fields should be updated in the DB."""
        import sqlite3
        from shopifyseo.dashboard_queries._editors import apply_saved_blog_article_fields_from_editor

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE blog_articles (
                shopify_id TEXT PRIMARY KEY,
                title TEXT,
                seo_title TEXT,
                seo_description TEXT,
                body TEXT,
                author_name TEXT,
                summary TEXT,
                featured_image_alt TEXT
            )
        """)
        conn.execute("""
            INSERT INTO blog_articles VALUES (
                'gid://article/1',
                'Original Title',
                'Original SEO Title',
                'Original SEO Desc',
                'Original Body',
                'Original Author',
                'Original Summary',
                'Original Alt'
            )
        """)
        conn.commit()

        # Update only title and author_name
        apply_saved_blog_article_fields_from_editor(
            conn,
            "gid://article/1",
            title="New Title",
            author_name="New Author",
        )

        row = conn.execute(
            "SELECT * FROM blog_articles WHERE shopify_id = ?",
            ("gid://article/1",),
        ).fetchone()

        # Updated fields
        assert row[1] == "New Title"  # title
        assert row[5] == "New Author"  # author_name

        # Unchanged fields
        assert row[2] == "Original SEO Title"  # seo_title
        assert row[3] == "Original SEO Desc"  # seo_description
        assert row[4] == "Original Body"  # body
        assert row[6] == "Original Summary"  # summary
        assert row[7] == "Original Alt"  # featured_image_alt

        conn.close()

    def test_no_update_when_no_fields_provided(self, tmp_path):
        """No update should happen when no fields are provided."""
        import sqlite3
        from shopifyseo.dashboard_queries._editors import apply_saved_blog_article_fields_from_editor

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE blog_articles (
                shopify_id TEXT PRIMARY KEY,
                title TEXT
            )
        """)
        conn.execute("INSERT INTO blog_articles VALUES ('gid://article/1', 'Title')")
        conn.commit()

        # Call with no fields
        apply_saved_blog_article_fields_from_editor(conn, "gid://article/1")

        # Should not error and title should be unchanged
        row = conn.execute("SELECT title FROM blog_articles").fetchone()
        assert row[0] == "Title"

        conn.close()


class TestFetchArticleImage:
    """Test _fetch_article_image helper."""

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    def test_returns_image_when_exists(self, mock_gql):
        """Should return image dict when article has an image."""
        from shopifyseo.dashboard_live_updates import _fetch_article_image

        mock_gql.return_value = {
            "data": {
                "article": {
                    "image": {
                        "url": "https://cdn.shopify.com/image.jpg",
                        "altText": "Alt text",
                    }
                }
            }
        }

        result = _fetch_article_image("gid://article/1")
        assert result == {
            "url": "https://cdn.shopify.com/image.jpg",
            "altText": "Alt text",
        }

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    def test_returns_none_when_no_image(self, mock_gql):
        """Should return None when article has no image."""
        from shopifyseo.dashboard_live_updates import _fetch_article_image

        mock_gql.return_value = {
            "data": {
                "article": {
                    "image": None
                }
            }
        }

        result = _fetch_article_image("gid://article/1")
        assert result is None

    @patch("shopifyseo.dashboard_live_updates.graphql_request")
    def test_returns_none_on_error(self, mock_gql):
        """Should return None on GraphQL error."""
        from shopifyseo.dashboard_live_updates import _fetch_article_image

        mock_gql.side_effect = Exception("API error")

        result = _fetch_article_image("gid://article/1")
        assert result is None


class TestUpdateBlogArticlePartialUpdate:
    """Test update_blog_article service function."""

    @patch("backend.app.services.article_service.refresh_object_structured_seo_data")
    @patch("backend.app.services.article_service.dq.set_workflow_state")
    @patch("backend.app.services.article_service.dq.apply_saved_blog_article_fields_from_editor")
    @patch("backend.app.services.article_service.live_update_article")
    @patch("backend.app.services.article_service.open_db_connection")
    @patch("backend.app.services.article_service.dq.fetch_blog_article_detail")
    def test_partial_update_passes_none_for_missing_fields(
        self, mock_fetch, mock_conn, mock_live, mock_db_update, mock_wf, mock_refresh
    ):
        """Partial update should pass None for missing fields."""
        from backend.app.services.article_service import update_blog_article

        mock_fetch.return_value = {
            "article": {"shopify_id": "gid://article/1"},
        }
        mock_conn.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.close = MagicMock()
        # live_update_article now returns (result, warnings)
        mock_live.return_value = ({}, [])

        # Only provide workflow_status
        payload = {"workflow_status": "Ready"}
        ok, msg, warnings = update_blog_article("blog", "article", payload)

        # Verify live_update_article was called with None for content fields
        mock_live.assert_called_once()
        call_kwargs = mock_live.call_args
        assert call_kwargs[1].get("title") is None
        assert call_kwargs[1].get("body_html") is None
        assert call_kwargs[1].get("seo_title") is None


class TestRealSchemaFeaturedImageAlt:
    """Test that featured_image_alt column exists in the real schema.

    These tests run against the actual schema migration to catch issues
    that mocked tests cannot detect (like the missing column bug in PR #32).
    """

    def test_featured_image_alt_column_exists_in_blog_articles(self, tmp_path):
        """Verify the featured_image_alt column is created by ensure_dashboard_schema."""
        import sqlite3
        from shopifyseo.dashboard_store import ensure_dashboard_schema

        db_path = tmp_path / "real_schema_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # Required by ensure_dashboard_schema

        # Run the real schema migration
        ensure_dashboard_schema(conn)

        # Check that blog_articles table has featured_image_alt column
        columns = {row[1] for row in conn.execute("PRAGMA table_info(blog_articles)")}
        assert "featured_image_alt" in columns, (
            "featured_image_alt column should exist in blog_articles table after migration. "
            f"Found columns: {columns}"
        )

        conn.close()

    def test_can_write_featured_image_alt_to_real_schema(self, tmp_path):
        """Verify we can write featured_image_alt to the real schema."""
        import sqlite3
        from shopifyseo.dashboard_store import ensure_dashboard_schema
        from shopifyseo.dashboard_queries._editors import apply_saved_blog_article_fields_from_editor

        db_path = tmp_path / "real_schema_test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # Required by ensure_dashboard_schema

        # Run the real schema migration
        ensure_dashboard_schema(conn)

        # Insert a minimal blog_articles row (required fields only)
        # First we need a blog row for the foreign key
        conn.execute("""
            INSERT INTO blogs (shopify_id, title, handle, tags_json, raw_json, synced_at)
            VALUES ('gid://blog/1', 'Test Blog', 'test-blog', '[]', '{}', datetime('now'))
        """)
        conn.execute("""
            INSERT INTO blog_articles (
                shopify_id, blog_shopify_id, blog_handle, title, handle,
                is_published, tags_json, raw_json, synced_at
            )
            VALUES (
                'gid://article/1', 'gid://blog/1', 'test-blog', 'Test Article', 'test-article',
                1, '[]', '{}', datetime('now')
            )
        """)
        conn.commit()

        # Now write featured_image_alt using the real function
        apply_saved_blog_article_fields_from_editor(
            conn,
            "gid://article/1",
            featured_image_alt="Test alt text for image",
        )

        # Verify it was written
        row = conn.execute(
            "SELECT featured_image_alt FROM blog_articles WHERE shopify_id = ?",
            ("gid://article/1",),
        ).fetchone()

        assert row[0] == "Test alt text for image", (
            f"featured_image_alt should be writable. Got: {row[0]}"
        )

        conn.close()

    def test_endpoint_returns_warnings_for_alt_only_update(self):
        """Verify the update response schema includes warnings field."""
        from backend.app.schemas.product import ProductActionResult

        # Create a response with warnings
        result = ProductActionResult(
            message="Article saved",
            warnings=["Warning about image re-upload"],
        )

        assert result.warnings == ["Warning about image re-upload"]

        # Verify warnings can be None
        result_no_warnings = ProductActionResult(message="Article saved")
        assert result_no_warnings.warnings is None
