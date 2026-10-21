"""Editor write paths.

These mirror UI saves into the local catalog DB. Product writes skip ``None``
(omitted) fields; empty title/body/tags still leave those columns untouched.
Collection/page helpers keep ``CASE WHEN ? != '' THEN ? ELSE col END``.
All commit on success.
"""
from __future__ import annotations

import json
from typing import Any


def apply_saved_product_fields_from_editor(
    conn: Any,
    shopify_id: str,
    *,
    title: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    body_html: str | None = None,
    tags: str | None = None,
) -> None:
    """Update product fields in the local DB after an editor save.

    ``None`` means the field was not provided and must be left untouched.
    Empty title/body/tags still skip the column (same as the previous
    ``CASE WHEN ? != ''`` behavior). Provided SEO fields are written as-is.
    """
    updates: list[str] = []
    params: list[Any] = []

    if title:
        updates.append("title = ?")
        params.append(title)
    if seo_title is not None:
        updates.append("seo_title = ?")
        params.append(seo_title)
    if seo_description is not None:
        updates.append("seo_description = ?")
        params.append(seo_description)
    if body_html:
        updates.append("description_html = ?")
        params.append(body_html)
    if tags is not None and tags.strip():
        updates.append("tags_json = ?")
        params.append(json.dumps([t.strip() for t in tags.split(",") if t.strip()]))

    if not updates:
        return

    params.append(shopify_id)
    conn.execute(
        f"UPDATE products SET {', '.join(updates)} WHERE shopify_id = ?",
        params,
    )
    conn.commit()


def apply_saved_collection_fields_from_editor(
    conn: Any,
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
    conn: Any,
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
    conn: Any,
    shopify_id: str,
    *,
    title: str | None = None,
    seo_title: str | None = None,
    seo_description: str | None = None,
    body_html: str | None = None,
    author_name: str | None = None,
    summary: str | None = None,
    featured_image_alt: str | None = None,
) -> None:
    """Update blog article fields in the local DB after an editor save.

    Partial update semantics: fields set to None are not updated.
    Only fields with non-None values are written to the database.
    """
    updates: list[str] = []
    params: list[str | None] = []

    if title is not None:
        updates.append("title = ?")
        params.append(title)

    if seo_title is not None:
        updates.append("seo_title = ?")
        params.append(seo_title)

    if seo_description is not None:
        updates.append("seo_description = ?")
        params.append(seo_description)

    if body_html is not None:
        updates.append("body = ?")
        params.append(body_html)

    if author_name is not None:
        updates.append("author_name = ?")
        params.append(author_name)

    if summary is not None:
        updates.append("summary = ?")
        params.append(summary)

    if featured_image_alt is not None:
        updates.append("featured_image_alt = ?")
        params.append(featured_image_alt)

    if not updates:
        return

    params.append(shopify_id)
    conn.execute(
        f"UPDATE blog_articles SET {', '.join(updates)} WHERE shopify_id = ?",
        params,
    )
    conn.commit()


def set_workflow_state(
    conn: Any,
    object_type: str,
    handle: str,
    status: str = "Needs fix",
    notes: str = "",
) -> None:
    """Upsert the workflow state for an object."""
    conn.execute(
        """
        INSERT INTO seo_workflow_states (object_type, handle, status, notes, updated_at)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(object_type, handle) DO UPDATE SET
            status = excluded.status,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """,
        (object_type, handle, status or "Needs fix", notes or ""),
    )
    conn.commit()
