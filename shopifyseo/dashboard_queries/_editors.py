"""Editor write paths.

These mirror UI saves into the local catalog DB. Each helper updates only
the fields the editor exposes (other columns are left untouched via SQL
``CASE WHEN ? != '' THEN ? ELSE col END``). All commit on success.
"""
from __future__ import annotations

import json
import sqlite3


def apply_saved_product_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    body_html: str = "",
    tags: str = "",
) -> None:
    """Update product fields in the local DB after an editor save."""
    tags_json_value = json.dumps([t.strip() for t in tags.split(",") if t.strip()]) if tags.strip() else ""
    conn.execute(
        """
        UPDATE products SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            description_html = CASE WHEN ? != '' THEN ? ELSE description_html END,
            tags_json = CASE WHEN ? != '' THEN ? ELSE tags_json END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            body_html, body_html,
            tags_json_value, tags_json_value,
            shopify_id,
        ),
    )
    conn.commit()


def apply_saved_collection_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    description_html: str = "",
) -> None:
    """Update collection fields in the local DB after an editor save."""
    conn.execute(
        """
        UPDATE collections SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            description_html = CASE WHEN ? != '' THEN ? ELSE description_html END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            description_html, description_html,
            shopify_id,
        ),
    )
    conn.commit()


def apply_saved_page_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    body_html: str = "",
) -> None:
    """Update page fields in the local DB after an editor save."""
    conn.execute(
        """
        UPDATE pages SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            body = CASE WHEN ? != '' THEN ? ELSE body END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            body_html, body_html,
            shopify_id,
        ),
    )
    conn.commit()


def apply_saved_blog_article_fields_from_editor(
    conn: sqlite3.Connection,
    shopify_id: str,
    *,
    title: str = "",
    seo_title: str = "",
    seo_description: str = "",
    body_html: str = "",
) -> None:
    """Update blog article fields in the local DB after an editor save."""
    conn.execute(
        """
        UPDATE blog_articles SET
            title = CASE WHEN ? != '' THEN ? ELSE title END,
            seo_title = ?,
            seo_description = ?,
            body = CASE WHEN ? != '' THEN ? ELSE body END
        WHERE shopify_id = ?
        """,
        (
            title, title,
            seo_title,
            seo_description,
            body_html, body_html,
            shopify_id,
        ),
    )
    conn.commit()


def set_workflow_state(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    status: str = "Needs fix",
    notes: str = "",
) -> None:
    """Upsert the workflow state for an object."""
    conn.execute(
        """
        INSERT INTO seo_workflow_states (object_type, handle, status, notes, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(object_type, handle) DO UPDATE SET
            status = excluded.status,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (object_type, handle, status or "Needs fix", notes or ""),
    )
    conn.commit()
