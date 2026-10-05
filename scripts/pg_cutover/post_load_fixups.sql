-- Post-pgloader data fixups. Idempotent. Does NOT delete cluster_keywords
-- orphans (that path is --delete-cluster-orphans on scripts/pg_cutover.sh).
--
-- REPORT: keyword_metrics.updated_at (and sibling epoch INTEGER columns) may
-- arrive as text if SQLite stored CURRENT_TIMESTAMP. Convert those leftovers
-- to bigint unix seconds; unparseable / empty values become 0.

DO $$
DECLARE
    rec record;
    stmt text;
BEGIN
    FOR rec IN
        SELECT *
        FROM (VALUES
            ('keyword_metrics', 'updated_at'),
            ('keyword_page_map', 'updated_at'),
            ('competitor_keyword_gaps', 'updated_at'),
            ('competitor_profiles', 'updated_at'),
            ('competitor_profiles', 'authority_updated_at'),
            ('competitor_top_pages', 'updated_at'),
            ('site_authority', 'checked_at'),
            ('article_ideas', 'created_at'),
            ('article_ideas', 'serp_refreshed_at'),
            ('idea_articles', 'created_at'),
            ('article_draft_runs', 'created_at'),
            ('article_draft_runs', 'updated_at'),
            ('link_suggestions', 'created_at'),
            ('link_suggestions', 'applied_at'),
            ('link_suggestion_events', 'created_at'),
            ('link_body_snapshots', 'created_at'),
            ('link_body_snapshots', 'updated_at'),
            ('link_suggestion_restore_audit', 'restored_at'),
            ('google_api_cache', 'fetched_at'),
            ('google_api_cache', 'expires_at'),
            ('gsc_query_rows', 'fetched_at'),
            ('gsc_query_dimension_rows', 'fetched_at'),
            ('service_tokens', 'expires_at'),
            ('robots_snapshots', 'first_seen_at'),
            ('robots_snapshots', 'last_seen_at')
        ) AS t(tbl, col)
    LOOP
        IF to_regclass(format('%I', rec.tbl)) IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = rec.tbl
              AND column_name = rec.col
        ) THEN
            CONTINUE;
        END IF;

        -- Mixed-type leftover: column is still text/varchar after pgloader.
        IF EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = rec.tbl
              AND column_name = rec.col
              AND data_type IN ('text', 'character varying', 'character')
        ) THEN
            stmt := format(
                'ALTER TABLE %I ADD COLUMN IF NOT EXISTS %I bigint',
                rec.tbl, rec.col || '_cutover_epoch'
            );
            EXECUTE stmt;
            stmt := format(
                $f$
                UPDATE %I SET %I = CASE
                    WHEN %I IS NULL OR btrim(%I) = '' THEN 0
                    WHEN btrim(%I) ~ '^-?[0-9]+$' THEN btrim(%I)::bigint
                    WHEN %I ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN
                        EXTRACT(EPOCH FROM %I::timestamp)::bigint
                    ELSE 0
                END
                $f$,
                rec.tbl, rec.col || '_cutover_epoch',
                rec.col, rec.col,
                rec.col, rec.col,
                rec.col, rec.col
            );
            EXECUTE stmt;
            EXECUTE format('ALTER TABLE %I DROP COLUMN %I', rec.tbl, rec.col);
            EXECUTE format(
                'ALTER TABLE %I RENAME COLUMN %I TO %I',
                rec.tbl, rec.col || '_cutover_epoch', rec.col
            );
        END IF;
    END LOOP;
END
$$;

-- Empty strings in numeric columns that survived as text-castable leftovers.
-- No-op when the column is already a number type (assignment cast fails closed
-- only if a non-empty non-numeric remains — verify_values.py flags those).
