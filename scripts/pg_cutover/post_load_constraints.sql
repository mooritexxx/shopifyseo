-- Post-load indexes and foreign keys.
-- FKs are created NOT VALID so existing orphans (cluster_keywords, etc.)
-- do not fail the load. VALIDATE is a separate --validate-fks step.
--
-- This file never DELETEs rows.

-- Performance-invariant LOWER(keyword) expression indexes.
-- Guarded so a partial fixture / interrupted load can still apply this file.
DO $$
BEGIN
    IF to_regclass('keyword_metrics') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_keyword_metrics_keyword_lower ON keyword_metrics (LOWER(keyword))';
    END IF;
    IF to_regclass('keyword_page_map') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_keyword_page_map_keyword_lower ON keyword_page_map (LOWER(keyword))';
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_keyword_page_map_object ON keyword_page_map (object_type, object_handle)';
    END IF;
    IF to_regclass('competitor_keyword_gaps') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_competitor_gaps_keyword_lower ON competitor_keyword_gaps (LOWER(keyword))';
    END IF;
    IF to_regclass('gsc_page_daily') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_gsc_page_daily_object ON gsc_page_daily (object_type, object_handle, date)';
    END IF;
    IF to_regclass('gsc_query_dimension_rows') IS NOT NULL THEN
        EXECUTE 'CREATE INDEX IF NOT EXISTS idx_gsc_query_dimension_lookup ON gsc_query_dimension_rows (object_type, object_handle, dimension_kind)';
    END IF;
    IF to_regclass('rank_jobs') IS NOT NULL THEN
        EXECUTE $i$CREATE UNIQUE INDEX IF NOT EXISTS rank_one_running ON rank_jobs (status) WHERE status = 'running'$i$;
    END IF;
    IF to_regclass('link_body_snapshots') IS NOT NULL THEN
        EXECUTE $i$
            CREATE UNIQUE INDEX IF NOT EXISTS idx_link_body_active_write
            ON link_body_snapshots (source_type, shopify_id)
            WHERE status IN (
                'prepared',
                'needs_reconciliation',
                'undo_prepared',
                'undo_needs_reconciliation'
            )
        $i$;
    END IF;
END
$$;

DO $$
DECLARE
    rec record;
    ddl text;
BEGIN
    FOR rec IN
        SELECT *
        FROM (VALUES
            ('cluster_keywords_cluster_id_fkey',
             'cluster_keywords', 'cluster_id', 'clusters', 'id', 'CASCADE'),
            ('idea_articles_idea_id_fkey',
             'idea_articles', 'idea_id', 'article_ideas', 'id', 'CASCADE'),
            ('product_variants_product_shopify_id_fkey',
             'product_variants', 'product_shopify_id', 'products', 'shopify_id', 'CASCADE'),
            ('product_images_product_shopify_id_fkey',
             'product_images', 'product_shopify_id', 'products', 'shopify_id', 'CASCADE'),
            ('product_metafields_product_shopify_id_fkey',
             'product_metafields', 'product_shopify_id', 'products', 'shopify_id', 'CASCADE'),
            ('collection_metafields_collection_shopify_id_fkey',
             'collection_metafields', 'collection_shopify_id', 'collections', 'shopify_id', 'CASCADE'),
            ('collection_products_collection_shopify_id_fkey',
             'collection_products', 'collection_shopify_id', 'collections', 'shopify_id', 'CASCADE'),
            ('collection_products_product_shopify_id_fkey',
             'collection_products', 'product_shopify_id', 'products', 'shopify_id', 'CASCADE'),
            ('blog_articles_blog_shopify_id_fkey',
             'blog_articles', 'blog_shopify_id', 'blogs', 'shopify_id', 'CASCADE'),
            ('rank_checks_keyword_id_fkey',
             'rank_checks', 'keyword_id', 'tracked_keywords', 'id', 'NO ACTION'),
            ('rank_checks_job_id_fkey',
             'rank_checks', 'job_id', 'rank_jobs', 'id', 'NO ACTION'),
            ('rank_requests_job_id_fkey',
             'rank_requests', 'job_id', 'rank_jobs', 'id', 'NO ACTION'),
            ('rank_requests_keyword_id_fkey',
             'rank_requests', 'keyword_id', 'tracked_keywords', 'id', 'NO ACTION'),
            ('link_suggestion_events_suggestion_id_fkey',
             'link_suggestion_events', 'suggestion_id', 'link_suggestions', 'id', 'NO ACTION')
        ) AS t(conname, tbl, col, reftbl, refcol, ondel)
    LOOP
        IF to_regclass(format('%I', rec.tbl)) IS NULL
           OR to_regclass(format('%I', rec.reftbl)) IS NULL THEN
            CONTINUE;
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.conname = rec.conname
              AND n.nspname = current_schema()
        ) THEN
            CONTINUE;
        END IF;
        ddl := format(
            'ALTER TABLE %I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES %I(%I) ON DELETE %s NOT VALID',
            rec.tbl, rec.conname, rec.col, rec.reftbl, rec.refcol, rec.ondel
        );
        EXECUTE ddl;
    END LOOP;
END
$$;
