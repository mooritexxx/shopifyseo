"""Table catalog for cutover verify, fixups, and rollback delta."""

from __future__ import annotations

from typing import Any

# Tables whose INTEGER PRIMARY KEY is not an independent sequence.
# seo_change_events.task_id copies seo_opportunity_tasks.id.
SKIP_IDENTITY_TABLES = frozenset({"seo_change_events"})

# Declared INTEGER columns that live data has stored as ISO/text (SQLite affinity).
# REPORT: keyword_metrics.updated_at is the known text-in-int case; siblings share
# the same pattern (epoch INTEGER written with CURRENT_TIMESTAMP in older code).
INTEGER_EPOCH_COLUMNS: dict[str, tuple[str, ...]] = {
    "keyword_metrics": ("updated_at",),
    "keyword_page_map": ("updated_at",),
    "competitor_keyword_gaps": ("updated_at",),
    "competitor_profiles": ("updated_at", "authority_updated_at"),
    "competitor_top_pages": ("updated_at",),
    "site_authority": ("checked_at",),
    "article_ideas": ("created_at", "serp_refreshed_at"),
    "idea_articles": ("created_at",),
    "article_draft_runs": ("created_at", "updated_at"),
    "link_suggestions": ("created_at", "applied_at"),
    "link_suggestion_events": ("created_at",),
    "link_body_snapshots": ("created_at", "updated_at"),
    "link_suggestion_restore_audit": ("restored_at",),
    "google_api_cache": ("fetched_at", "expires_at"),
    "gsc_query_rows": ("fetched_at",),
    "gsc_query_dimension_rows": ("fetched_at",),
    "service_tokens": ("expires_at",),
    "robots_snapshots": ("first_seen_at", "last_seen_at"),
}

# (table, column, kind) for post-cutover drift export.
# kind is "epoch" (unix seconds / integer) or "text" (ISO or CURRENT_TIMESTAMP text).
TIMESTAMP_COLUMNS: dict[str, tuple[str, str]] = {
    "keyword_metrics": ("updated_at", "epoch"),
    "keyword_page_map": ("updated_at", "epoch"),
    "competitor_keyword_gaps": ("updated_at", "epoch"),
    "competitor_profiles": ("updated_at", "epoch"),
    "competitor_top_pages": ("updated_at", "epoch"),
    "site_authority": ("checked_at", "epoch"),
    "article_ideas": ("created_at", "epoch"),
    "idea_articles": ("created_at", "epoch"),
    "article_draft_runs": ("updated_at", "epoch"),
    "link_suggestions": ("created_at", "epoch"),
    "link_suggestion_events": ("created_at", "epoch"),
    "link_body_snapshots": ("updated_at", "epoch"),
    "link_suggestion_restore_audit": ("restored_at", "epoch"),
    "google_api_cache": ("updated_at", "text"),
    "gsc_query_rows": ("updated_at", "text"),
    "gsc_query_dimension_rows": ("updated_at", "text"),
    "gsc_page_daily": ("updated_at", "text"),
    "seo_workflow_states": ("updated_at", "text"),
    "service_tokens": ("updated_at", "text"),
    "service_settings": ("updated_at", "text"),
    "seo_recommendations": ("updated_at", "text"),
    "seo_opportunity_tasks": ("updated_at", "text"),
    "seo_change_events": ("applied_at", "text"),
    "embeddings": ("updated_at", "text"),
    "api_usage_log": ("created_at", "text"),
    "tracked_keywords": ("updated_at", "text"),
    "rank_jobs": ("created_at", "text"),
    "rank_checks": ("checked_at", "text"),
    "rank_requests": ("requested_at", "text"),
    "team_tasks": ("last_log_at", "text"),
    "team_task_events": ("at", "text"),
    "products": ("synced_at", "text"),
    "product_variants": ("synced_at", "text"),
    "product_images": ("synced_at", "text"),
    "product_metafields": ("synced_at", "text"),
    "product_image_file_cache": ("updated_at", "text"),
    "collections": ("synced_at", "text"),
    "collection_metafields": ("synced_at", "text"),
    "pages": ("synced_at", "text"),
    "blogs": ("synced_at", "text"),
    "blog_articles": ("synced_at", "text"),
    "collection_products": ("synced_at", "text"),
    "shopify_metaobjects": ("synced_at", "text"),
    "clusters": ("generated_at", "text"),
}

# Primary key columns used when upserting a delta row into SQLite.
DELTA_TABLES: dict[str, tuple[str, ...]] = {
    "keyword_metrics": ("keyword",),
    "keyword_page_map": ("keyword", "object_type", "object_handle"),
    "competitor_keyword_gaps": ("keyword", "competitor_domain"),
    "competitor_profiles": ("domain",),
    "competitor_top_pages": ("competitor_domain", "url"),
    "site_authority": ("domain",),
    "article_ideas": ("id",),
    "idea_articles": ("id",),
    "article_draft_runs": ("id",),
    "link_suggestions": ("id",),
    "link_suggestion_events": ("id",),
    "link_body_snapshots": ("id",),
    "link_suggestion_restore_audit": ("id",),
    "google_api_cache": ("cache_key",),
    "gsc_query_rows": ("object_type", "object_handle", "query"),
    "gsc_query_dimension_rows": (
        "object_type",
        "object_handle",
        "query",
        "dimension_kind",
        "dimension_value",
    ),
    "gsc_page_daily": ("date", "page_url"),
    "seo_workflow_states": ("object_type", "handle"),
    "service_tokens": ("service",),
    "service_settings": ("key",),
    "seo_recommendations": ("id",),
    "seo_opportunity_tasks": ("id",),
    "seo_change_events": ("task_id",),
    "embeddings": ("object_type", "object_handle", "chunk_index"),
    "api_usage_log": ("id",),
    "tracked_keywords": ("id",),
    "rank_jobs": ("id",),
    "rank_checks": ("id",),
    "rank_requests": ("id",),
    "team_tasks": ("id",),
    "team_task_events": ("id",),
    "products": ("shopify_id",),
    "product_variants": ("shopify_id",),
    "product_images": ("shopify_id",),
    "product_metafields": ("shopify_id",),
    "product_image_file_cache": ("image_shopify_id",),
    "collections": ("shopify_id",),
    "collection_metafields": ("shopify_id",),
    "pages": ("shopify_id",),
    "blogs": ("shopify_id",),
    "blog_articles": ("shopify_id",),
    "collection_products": ("collection_shopify_id", "product_shopify_id"),
    "shopify_metaobjects": ("shopify_id",),
    "clusters": ("id",),
}

# (constraint_name, table, column, ref_table, ref_column, on_delete)
FOREIGN_KEYS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        "cluster_keywords_cluster_id_fkey",
        "cluster_keywords",
        "cluster_id",
        "clusters",
        "id",
        "CASCADE",
    ),
    (
        "idea_articles_idea_id_fkey",
        "idea_articles",
        "idea_id",
        "article_ideas",
        "id",
        "CASCADE",
    ),
    (
        "product_variants_product_shopify_id_fkey",
        "product_variants",
        "product_shopify_id",
        "products",
        "shopify_id",
        "CASCADE",
    ),
    (
        "product_images_product_shopify_id_fkey",
        "product_images",
        "product_shopify_id",
        "products",
        "shopify_id",
        "CASCADE",
    ),
    (
        "product_metafields_product_shopify_id_fkey",
        "product_metafields",
        "product_shopify_id",
        "products",
        "shopify_id",
        "CASCADE",
    ),
    (
        "collection_metafields_collection_shopify_id_fkey",
        "collection_metafields",
        "collection_shopify_id",
        "collections",
        "shopify_id",
        "CASCADE",
    ),
    (
        "collection_products_collection_shopify_id_fkey",
        "collection_products",
        "collection_shopify_id",
        "collections",
        "shopify_id",
        "CASCADE",
    ),
    (
        "collection_products_product_shopify_id_fkey",
        "collection_products",
        "product_shopify_id",
        "products",
        "shopify_id",
        "CASCADE",
    ),
    (
        "blog_articles_blog_shopify_id_fkey",
        "blog_articles",
        "blog_shopify_id",
        "blogs",
        "shopify_id",
        "CASCADE",
    ),
    (
        "rank_checks_keyword_id_fkey",
        "rank_checks",
        "keyword_id",
        "tracked_keywords",
        "id",
        "NO ACTION",
    ),
    (
        "rank_checks_job_id_fkey",
        "rank_checks",
        "job_id",
        "rank_jobs",
        "id",
        "NO ACTION",
    ),
    (
        "rank_requests_job_id_fkey",
        "rank_requests",
        "job_id",
        "rank_jobs",
        "id",
        "NO ACTION",
    ),
    (
        "rank_requests_keyword_id_fkey",
        "rank_requests",
        "keyword_id",
        "tracked_keywords",
        "id",
        "NO ACTION",
    ),
    (
        "link_suggestion_events_suggestion_id_fkey",
        "link_suggestion_events",
        "suggestion_id",
        "link_suggestions",
        "id",
        "NO ACTION",
    ),
)


def list_user_tables(conn: Any) -> list[str]:
    """Return ordinary user table names (no sqlite_/pg_ internals)."""
    from shopifyseo.db import Backend, backend_for_connection

    if backend_for_connection(conn) == Backend.POSTGRES:
        rows = conn.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = current_schema()
              AND tablename NOT LIKE 'pg_%'
              AND tablename NOT LIKE 'sql_%'
            ORDER BY tablename
            """
        ).fetchall()
        return [row[0] for row in rows]
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [row[0] for row in rows]
