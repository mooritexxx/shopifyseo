-- Post-load indexes and foreign keys.
-- FKs are created NOT VALID so existing orphans (cluster_keywords, etc.)
-- do not fail the load. VALIDATE is a separate --validate-fks step.
--
-- This file never DELETEs rows.

-- pgloader names SQLite indexes idx_<oid>_<name>. Rename that to <name>
-- instead of CREATE INDEX IF NOT EXISTS, which left 31 duplicate indexes.
CREATE OR REPLACE FUNCTION shopifyseo_ensure_index(idx_name text, create_sql text)
RETURNS void
LANGUAGE plpgsql
AS $ensure$
DECLARE
    pgloader_name text;
BEGIN
    SELECT c.relname INTO pgloader_name
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'i'
      AND n.nspname = current_schema()
      AND c.relname ~ ('^idx_[0-9]+_' || idx_name || '$')
    LIMIT 1;

    IF pgloader_name IS NOT NULL THEN
        IF to_regclass(idx_name) IS NOT NULL AND pgloader_name IS DISTINCT FROM idx_name THEN
            EXECUTE format('DROP INDEX %I', idx_name);
        END IF;
        IF pgloader_name IS DISTINCT FROM idx_name THEN
            EXECUTE format('ALTER INDEX %I RENAME TO %I', pgloader_name, idx_name);
        END IF;
        RETURN;
    END IF;

    IF to_regclass(idx_name) IS NOT NULL THEN
        RETURN;
    END IF;

    EXECUTE create_sql;
END
$ensure$;

-- Performance-invariant LOWER(keyword) expression indexes (8 of 41).
-- Guarded so a partial fixture / interrupted load can still apply this file.
DO $$
BEGIN
    IF to_regclass('keyword_metrics') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'idx_keyword_metrics_keyword_lower',
            'CREATE INDEX idx_keyword_metrics_keyword_lower ON keyword_metrics (LOWER(keyword))'
        );
    END IF;
    IF to_regclass('keyword_page_map') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'idx_keyword_page_map_keyword_lower',
            'CREATE INDEX idx_keyword_page_map_keyword_lower ON keyword_page_map (LOWER(keyword))'
        );
        PERFORM shopifyseo_ensure_index(
            'idx_keyword_page_map_object',
            'CREATE INDEX idx_keyword_page_map_object ON keyword_page_map (object_type, object_handle)'
        );
    END IF;
    IF to_regclass('competitor_keyword_gaps') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'idx_competitor_gaps_keyword_lower',
            'CREATE INDEX idx_competitor_gaps_keyword_lower ON competitor_keyword_gaps (LOWER(keyword))'
        );
    END IF;
    IF to_regclass('gsc_page_daily') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'idx_gsc_page_daily_object',
            'CREATE INDEX idx_gsc_page_daily_object ON gsc_page_daily (object_type, object_handle, date)'
        );
    END IF;
    IF to_regclass('gsc_query_dimension_rows') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'idx_gsc_query_dimension_lookup',
            'CREATE INDEX idx_gsc_query_dimension_lookup ON gsc_query_dimension_rows (object_type, object_handle, dimension_kind)'
        );
    END IF;
    IF to_regclass('rank_jobs') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'rank_one_running',
            $i$CREATE UNIQUE INDEX rank_one_running ON rank_jobs (status) WHERE status = 'running'$i$
        );
    END IF;
    IF to_regclass('link_body_snapshots') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index(
            'idx_link_body_active_write',
            $i$
            CREATE UNIQUE INDEX idx_link_body_active_write
            ON link_body_snapshots (source_type, shopify_id)
            WHERE status IN (
                'prepared',
                'needs_reconciliation',
                'undo_prepared',
                'undo_needs_reconciliation'
            )
            $i$
        );
    END IF;
END
$$;

-- Secondary indexes the SQLite runtime bootstrap used to create (33 of 41).
-- Rename pgloader's idx_<oid>_<name> rather than creating a second copy.
DO $$
BEGIN
    IF to_regclass('products') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_products_vendor', 'CREATE INDEX idx_products_vendor ON products(vendor)');
        PERFORM shopifyseo_ensure_index('idx_products_status', 'CREATE INDEX idx_products_status ON products(status)');
    END IF;
    IF to_regclass('product_variants') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_variants_product', 'CREATE INDEX idx_variants_product ON product_variants(product_shopify_id)');
    END IF;
    IF to_regclass('product_images') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_images_product', 'CREATE INDEX idx_images_product ON product_images(product_shopify_id)');
    END IF;
    IF to_regclass('product_metafields') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_metafields_product', 'CREATE INDEX idx_metafields_product ON product_metafields(product_shopify_id)');
        PERFORM shopifyseo_ensure_index('idx_metafields_ns_key', 'CREATE INDEX idx_metafields_ns_key ON product_metafields(namespace, key)');
    END IF;
    IF to_regclass('collections') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_collections_handle', 'CREATE INDEX idx_collections_handle ON collections(handle)');
    END IF;
    IF to_regclass('collection_metafields') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_collection_metafields_collection', 'CREATE INDEX idx_collection_metafields_collection ON collection_metafields(collection_shopify_id)');
        PERFORM shopifyseo_ensure_index('idx_collection_metafields_ns_key', 'CREATE INDEX idx_collection_metafields_ns_key ON collection_metafields(namespace, key)');
    END IF;
    IF to_regclass('collection_products') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_collection_products_collection', 'CREATE INDEX idx_collection_products_collection ON collection_products(collection_shopify_id)');
        PERFORM shopifyseo_ensure_index('idx_collection_products_product', 'CREATE INDEX idx_collection_products_product ON collection_products(product_shopify_id)');
    END IF;
    IF to_regclass('pages') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_pages_handle', 'CREATE INDEX idx_pages_handle ON pages(handle)');
    END IF;
    IF to_regclass('blogs') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_blogs_handle', 'CREATE INDEX idx_blogs_handle ON blogs(handle)');
    END IF;
    IF to_regclass('blog_articles') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_blog_articles_blog', 'CREATE INDEX idx_blog_articles_blog ON blog_articles(blog_shopify_id)');
        PERFORM shopifyseo_ensure_index('idx_blog_articles_blog_handle', 'CREATE INDEX idx_blog_articles_blog_handle ON blog_articles(blog_handle, handle)');
    END IF;
    IF to_regclass('link_suggestions') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_link_suggestions_status', 'CREATE INDEX idx_link_suggestions_status ON link_suggestions (status, score)');
    END IF;
    IF to_regclass('embeddings') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_embeddings_type', 'CREATE INDEX idx_embeddings_type ON embeddings(object_type)');
    END IF;
    IF to_regclass('api_usage_log') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_api_usage_log_created', 'CREATE INDEX idx_api_usage_log_created ON api_usage_log(created_at)');
    END IF;
    IF to_regclass('internal_links') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_internal_links_target', 'CREATE INDEX idx_internal_links_target ON internal_links (target_type, target_handle)');
    END IF;
    IF to_regclass('link_suggestion_events') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_link_suggestion_events_created', 'CREATE INDEX idx_link_suggestion_events_created ON link_suggestion_events (created_at)');
        PERFORM shopifyseo_ensure_index('idx_link_suggestion_events_suggestion', 'CREATE INDEX idx_link_suggestion_events_suggestion ON link_suggestion_events (suggestion_id)');
    END IF;
    IF to_regclass('team_tasks') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('team_tasks_owner_status', 'CREATE INDEX team_tasks_owner_status ON team_tasks(owner, status)');
        PERFORM shopifyseo_ensure_index('team_tasks_stale', 'CREATE INDEX team_tasks_stale ON team_tasks(status, last_log_at)');
        PERFORM shopifyseo_ensure_index('team_tasks_completed', 'CREATE INDEX team_tasks_completed ON team_tasks(completed_at)');
    END IF;
    IF to_regclass('team_task_events') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('team_task_events_task', 'CREATE INDEX team_task_events_task ON team_task_events(task_id, id)');
        PERFORM shopifyseo_ensure_index('team_task_events_time', 'CREATE INDEX team_task_events_time ON team_task_events(at, id)');
    END IF;
    IF to_regclass('link_body_snapshots') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_link_body_suggestion', 'CREATE INDEX idx_link_body_suggestion ON link_body_snapshots(suggestion_id, id DESC)');
    END IF;
    IF to_regclass('link_suggestion_restore_audit') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_restore_audit_suggestion', 'CREATE INDEX idx_restore_audit_suggestion ON link_suggestion_restore_audit(suggestion_id)');
    END IF;
    IF to_regclass('google_api_cache') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('idx_google_api_cache_type', 'CREATE INDEX idx_google_api_cache_type ON google_api_cache(cache_type)');
        PERFORM shopifyseo_ensure_index('idx_google_api_cache_url', 'CREATE INDEX idx_google_api_cache_url ON google_api_cache(url)');
    END IF;
    IF to_regclass('robots_snapshots') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('robots_snapshot_url', 'CREATE INDEX robots_snapshot_url ON robots_snapshots(url, id DESC)');
    END IF;
    IF to_regclass('rank_checks') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('rank_checks_history', 'CREATE INDEX rank_checks_history ON rank_checks(keyword_id, checked_at DESC)');
    END IF;
    IF to_regclass('rank_requests') IS NOT NULL THEN
        PERFORM shopifyseo_ensure_index('rank_requests_month', 'CREATE INDEX rank_requests_month ON rank_requests(month)');
    END IF;
END
$$;

DROP FUNCTION shopifyseo_ensure_index(text, text);

-- Append-only guard for team_task_events (SQLite RAISE(ABORT) triggers).
DO $$
BEGIN
    IF to_regclass('team_task_events') IS NULL THEN
        RETURN;
    END IF;
    EXECUTE $fn$
        CREATE OR REPLACE FUNCTION team_task_events_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $body$
        BEGIN
            RAISE EXCEPTION 'Task history is append-only';
        END
        $body$;
    $fn$;
    DROP TRIGGER IF EXISTS team_events_no_update ON team_task_events;
    DROP TRIGGER IF EXISTS team_events_no_delete ON team_task_events;
    CREATE TRIGGER team_events_no_update
        BEFORE UPDATE ON team_task_events
        FOR EACH ROW EXECUTE FUNCTION team_task_events_append_only();
    CREATE TRIGGER team_events_no_delete
        BEFORE DELETE ON team_task_events
        FOR EACH ROW EXECUTE FUNCTION team_task_events_append_only();
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
