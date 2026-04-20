import { z } from "zod";

const gscPeriodRollupSchema = z
  .object({
    start_date: z.string(),
    end_date: z.string(),
    clicks: z.number(),
    impressions: z.number(),
    ctr: z.number(),
    position: z.number().nullish()
  })
  .nullable();

const catalogSegmentSchema = z.object({
  total: z.number(),
  missing_meta: z.number(),
  meta_complete: z.number(),
  pct_meta_complete: z.number()
});

const productCatalogSegmentSchema = catalogSegmentSchema.extend({
  thin_body: z.number()
});

export const catalogCompletionSchema = z.object({
  products: productCatalogSegmentSchema,
  collections: catalogSegmentSchema,
  pages: catalogSegmentSchema,
  articles: catalogSegmentSchema
});

const indexingTypeBucketsSchema = z.object({
  total: z.number(),
  indexed: z.number(),
  not_indexed: z.number(),
  needs_review: z.number(),
  unknown: z.number()
});

export const indexingRollupSchema = z.object({
  total: z.number(),
  indexed: z.number(),
  not_indexed: z.number(),
  needs_review: z.number(),
  unknown: z.number(),
  by_type: z.record(z.string(), indexingTypeBucketsSchema)
});

export const gscSiteOverviewSchema = z.object({
  available: z.boolean(),
  timezone: z.string(),
  period_mode: z.string(),
  url_segment: z.string().default("all"),
  anchor_date: z.string(),
  error: z.string().nullish(),
  current: gscPeriodRollupSchema,
  previous: gscPeriodRollupSchema,
  deltas: z.record(z.string(), z.number().nullable()),
  series: z.array(
    z.object({
      date: z.string(),
      clicks: z.number(),
      impressions: z.number(),
      ctr_pct: z.number().optional().default(0),
      position: z.number().nullable().optional()
    })
  ),
  cache: z.object({
    label: z.string(),
    kind: z.string(),
    text: z.string(),
    meta: z.unknown().optional()
  })
});

const ga4PeriodRollupSchema = z
  .object({
    start_date: z.string(),
    end_date: z.string(),
    sessions: z.number(),
    views: z.number(),
    new_users: z.number().optional().default(0),
    avg_session_duration: z.number().optional().default(0),
    bounce_rate: z.number().optional().default(0)
  })
  .nullable();

export const ga4SiteOverviewSchema = z.object({
  available: z.boolean(),
  timezone: z.string(),
  period_mode: z.string(),
  anchor_date: z.string(),
  error: z.string().nullish(),
  current: ga4PeriodRollupSchema,
  previous: ga4PeriodRollupSchema,
  deltas: z.record(z.string(), z.number().nullable()),
  series: z.array(
    z.object({
      date: z.string(),
      sessions: z.number(),
      views: z.number()
    })
  ),
  cache: z.object({
    label: z.string(),
    kind: z.string(),
    text: z.string(),
    meta: z.unknown().optional()
  })
});

export const overviewGoalsSchema = z.object({
  gsc_daily_clicks: z.number().nullable(),
  gsc_daily_impressions: z.number().nullable(),
  ga4_daily_sessions: z.number().nullable(),
  ga4_daily_views: z.number().nullable()
});

/** Tier A property breakdowns from the GSC cache; summary reads cache only. */
const summaryGscBreakdownCacheSchema = z.object({
  label: z.string(),
  kind: z.string(),
  text: z.string(),
  meta: z.record(z.any()).nullable().optional()
});
const summaryGscBreakdownSliceSchema = z.object({
  rows: z.array(z.object({
    keys: z.array(z.string()).default([]),
    clicks: z.union([z.number(), z.string()]).optional().default(0),
    impressions: z.union([z.number(), z.string()]).optional().default(0),
    ctr: z.number().optional().default(0),
    position: z.number().optional().default(0)
  })),
  error: z.string(),
  cache: summaryGscBreakdownCacheSchema,
  top_bucket_impressions_pct_vs_prior: z.number().nullable().optional()
});
const summaryGscPropertyBreakdownsSchema = z.object({
  available: z.boolean(),
  period_mode: z.string(),
  anchor_date: z.string(),
  window: z.object({
    start_date: z.string(),
    end_date: z.string()
  }),
  country: summaryGscBreakdownSliceSchema,
  device: summaryGscBreakdownSliceSchema,
  searchAppearance: summaryGscBreakdownSliceSchema,
  errors: z.array(z.record(z.any())),
  error: z.string()
});

export const topOrganicPageSchema = z.object({
  entity_type: z.string(),
  handle: z.string(),
  title: z.string(),
  gsc_clicks: z.number(),
  gsc_impressions: z.number(),
  gsc_ctr: z.number(),
  gsc_position: z.number().nullable().optional(),
  url: z.string()
});

export type TopOrganicPage = z.infer<typeof topOrganicPageSchema>;

const summaryGscMetricRowSchema = z.object({
  keys: z.array(z.string()).default([]),
  clicks: z.union([z.number(), z.string()]).optional().default(0),
  impressions: z.union([z.number(), z.string()]).optional().default(0),
  ctr: z.number().optional().default(0),
  position: z.number().optional().default(0)
});

export const summarySchema = z.object({
  counts: z.object({
    products: z.number(),
    variants: z.number(),
    images: z.number(),
    product_metafields: z.number(),
    collections: z.number(),
    collection_metafields: z.number(),
    collection_products: z.number(),
    pages: z.number(),
    blogs: z.number(),
    blog_articles: z.number()
  }),
  metrics: z.object({
    collections_missing_meta: z.number(),
    pages_missing_meta: z.number(),
    products_missing_meta: z.number(),
    products_thin_body: z.number(),
    gsc_pages: z.number(),
    gsc_clicks: z.number(),
    gsc_impressions: z.number(),
    ga4_pages: z.number(),
    ga4_sessions: z.number(),
    ga4_views: z.number()
  }),
  recent_runs: z.array(z.object({
    id: z.number(),
    started_at: z.string().nullable().optional(),
    finished_at: z.string().nullable().optional(),
    status: z.string().nullable().optional(),
    products_synced: z.number().nullable().optional(),
    collections_synced: z.number().nullable().optional(),
    pages_synced: z.number().nullable().optional(),
    blogs_synced: z.number().nullable().optional(),
    blog_articles_synced: z.number().nullable().optional(),
    error_message: z.string().nullable().optional()
  })),
  /** Unix timestamp string set when a dashboard (sidebar) sync completes successfully */
  last_dashboard_sync_at: z.string().nullish(),
  gsc_site: gscSiteOverviewSchema,
  ga4_site: ga4SiteOverviewSchema,
  indexing_rollup: indexingRollupSchema,
  catalog_completion: catalogCompletionSchema,
  overview_goals: overviewGoalsSchema,
  gsc_property_breakdowns: summaryGscPropertyBreakdownsSchema,
  top_pages: z.array(topOrganicPageSchema).default([]),
  gsc_queries: z.array(summaryGscMetricRowSchema).default([]),
  gsc_pages: z.array(summaryGscMetricRowSchema).default([]),
  gsc_performance_period: z
    .object({
      start_date: z.string().default(""),
      end_date: z.string().default("")
    })
    .default({ start_date: "", end_date: "" }),
  gsc_performance_error: z.string().default("")
});

export const productListItemSchema = z.object({
  handle: z.string(),
  title: z.string(),
  vendor: z.string(),
  status: z.string(),
  updated_at: z.string().nullable().optional(),
  score: z.number(),
  priority: z.string(),
  reasons: z.array(z.string()),
  total_inventory: z.number(),
  body_length: z.number(),
  seo_title: z.string(),
  seo_description: z.string(),
  gsc_clicks: z.number(),
  gsc_impressions: z.number(),
  gsc_ctr: z.number(),
  gsc_position: z.number(),
  ga4_sessions: z.number(),
  ga4_views: z.number(),
  ga4_avg_session_duration: z.number(),
  index_status: z.string(),
  index_coverage: z.string(),
  google_canonical: z.string(),
  pagespeed_performance: z.number().nullable(),
  pagespeed_desktop_performance: z.number().nullable(),
  pagespeed_status: z.string(),
  workflow_status: z.string(),
  workflow_notes: z.string(),
  gsc_segment_flags: z
    .object({ has_dimensional: z.boolean() })
    .optional()
    .default({ has_dimensional: false })
});

export const productListSchema = z.object({
  items: z.array(productListItemSchema),
  total: z.number(),
  limit: z.number().nullable(),
  offset: z.number(),
  query: z.string(),
  sort: z.string(),
  direction: z.string(),
  focus: z.string().nullable().optional(),
  summary: z.object({
    visible_rows: z.number(),
    high_priority: z.number(),
    index_issues: z.number(),
    average_score: z.number()
  })
});

const gscSegmentRollupSchema = z.object({
  segment: z.string(),
  clicks: z.number(),
  impressions: z.number(),
  share: z.number()
});

export const gscSegmentSummarySchema = z.object({
  fetched_at: z.number().nullable().optional(),
  device_mix: z.array(gscSegmentRollupSchema),
  top_countries: z.array(gscSegmentRollupSchema),
  search_appearances: z.array(gscSegmentRollupSchema),
  top_pairs: z.array(
    z.object({
      query: z.string(),
      dimension_kind: z.string(),
      dimension_value: z.string(),
      clicks: z.number(),
      impressions: z.number(),
      position: z.number()
    })
  )
});

export const defaultGscSegmentSummary: z.infer<typeof gscSegmentSummarySchema> = {
  fetched_at: null,
  device_mix: [],
  top_countries: [],
  search_appearances: [],
  top_pairs: []
};

export type GscSegmentSummary = z.infer<typeof gscSegmentSummarySchema>;

export const gscQueryRowSchema = z.object({
  query: z.string(),
  clicks: z.number(),
  impressions: z.number(),
  ctr: z.number(),
  position: z.number()
});

export type GscQueryRow = z.infer<typeof gscQueryRowSchema>;

export const productDetailSchema = z.object({
  product: z.record(z.any()),
  draft: z.object({
    title: z.string(),
    seo_title: z.string(),
    seo_description: z.string(),
    body_html: z.string(),
    tags: z.string(),
    workflow_status: z.string(),
    workflow_notes: z.string()
  }),
  workflow: z.object({
    status: z.string(),
    notes: z.string(),
    updated_at: z.string().nullable().optional()
  }),
  recommendation: z.object({
    summary: z.string(),
    status: z.string(),
    model: z.string(),
    created_at: z.string().nullable().optional(),
    error_message: z.string(),
    details: z.record(z.any())
  }),
  recommendation_history: z.array(z.record(z.any())),
  signal_cards: z.array(z.object({
    label: z.string(),
    value: z.string(),
    sublabel: z.string(),
    updated_at: z.union([z.string(), z.number(), z.null()]).optional(),
    step: z.string(),
    action_label: z.string().nullable().optional(),
    action_href: z.string().nullable().optional()
  })),
  collections: z.array(z.record(z.any())),
  variants: z.array(z.record(z.any())),
  metafields: z.array(z.record(z.any())),
  product_images: z
    .array(
      z.object({
        shopify_id: z.string().optional(),
        url: z.string(),
        alt_text: z.string().optional().default(""),
        position: z.number().nullable().optional()
      })
    )
    .optional()
    .default([]),
  opportunity: z.record(z.any()),
  gsc_segment_summary: gscSegmentSummarySchema.default(defaultGscSegmentSummary),
  gsc_queries: z.array(gscQueryRowSchema).default([])
});

export const contentListItemSchema = z.object({
  handle: z.string(),
  title: z.string(),
  updated_at: z.string().nullable().optional(),
  score: z.number(),
  priority: z.string(),
  reasons: z.array(z.string()),
  seo_title: z.string(),
  seo_description: z.string(),
  body_length: z.number(),
  gsc_clicks: z.number(),
  gsc_impressions: z.number(),
  gsc_ctr: z.number(),
  gsc_position: z.number(),
  ga4_sessions: z.number(),
  ga4_views: z.number(),
  ga4_avg_session_duration: z.number(),
  index_status: z.string(),
  index_coverage: z.string(),
  google_canonical: z.string(),
  pagespeed_performance: z.number().nullable(),
  pagespeed_desktop_performance: z.number().nullable(),
  pagespeed_status: z.string(),
  workflow_status: z.string(),
  workflow_notes: z.string(),
  product_count: z.number(),
  gsc_segment_flags: z
    .object({ has_dimensional: z.boolean() })
    .optional()
    .default({ has_dimensional: false })
});

export const contentListSchema = z.object({
  items: z.array(contentListItemSchema),
  total: z.number(),
  limit: z.number().nullable(),
  offset: z.number(),
  query: z.string(),
  sort: z.string(),
  direction: z.string(),
  focus: z.string().nullable().optional()
});

export const blogListItemSchema = z.object({
  handle: z.string(),
  title: z.string(),
  updated_at: z.string().nullable().optional(),
  article_count: z.number()
});

export const blogListSchema = z.object({
  items: z.array(blogListItemSchema),
  total: z.number()
});

export const blogArticleListItemSchema = z.object({
  handle: z.string(),
  title: z.string(),
  blog_handle: z.string(),
  published_at: z.string().nullable().optional(),
  updated_at: z.string().nullable().optional(),
  is_published: z.boolean(),
  seo_title: z.string(),
  seo_description: z.string(),
  body_preview: z.string()
});

export const blogArticlesSchema = z.object({
  blog: blogListItemSchema,
  items: z.array(blogArticleListItemSchema),
  total: z.number()
});

export const allArticleListItemSchema = blogArticleListItemSchema.extend({
  blog_title: z.string(),
  score: z.number(),
  priority: z.string(),
  reasons: z.array(z.string()),
  body_length: z.number(),
  gsc_clicks: z.number(),
  gsc_impressions: z.number(),
  gsc_ctr: z.number(),
  gsc_position: z.number(),
  ga4_sessions: z.number(),
  ga4_views: z.number(),
  ga4_avg_session_duration: z.number(),
  index_status: z.string(),
  index_coverage: z.string(),
  google_canonical: z.string(),
  pagespeed_performance: z.number().nullable(),
  pagespeed_desktop_performance: z.number().nullable(),
  pagespeed_status: z.string(),
  workflow_status: z.string(),
  workflow_notes: z.string(),
  gsc_segment_flags: z
    .object({ has_dimensional: z.boolean() })
    .optional()
    .default({ has_dimensional: false })
});

export const allArticlesSchema = z.object({
  items: z.array(allArticleListItemSchema),
  total: z.number()
});

export const matchSchema = z.object({
  match_type: z.string(),
  match_handle: z.string(),
  match_title: z.string(),
});

export const clusterSchema = z.object({
  id: z.number(),
  name: z.string(),
  content_type: z.string(),
  primary_keyword: z.string(),
  content_brief: z.string(),
  keywords: z.array(z.string()),
  total_volume: z.number(),
  avg_difficulty: z.number(),
  avg_opportunity: z.number(),
  keyword_count: z.number(),
  suggested_match: matchSchema.nullable().optional(),
  gsc_segment_flags: z
    .object({ has_dimensional: z.boolean() })
    .optional()
    .default({ has_dimensional: false }),
  keyword_coverage: z.object({
    found: z.number(),
    total: z.number(),
  }).nullable().optional(),
  matched_vendor: z.object({
    name: z.string(),
    product_count: z.number(),
  }).nullable().optional(),
  stats: z
    .object({
      dominant_serp_features: z.string().optional(),
      content_format_hints: z.string().optional(),
      avg_cps: z.number().optional(),
    })
    .optional(),
});

export type Cluster = z.infer<typeof clusterSchema>;

export const articleIdeaSchema = z.object({
  id: z.number(),
  suggested_title: z.string(),
  brief: z.string(),
  primary_keyword: z.string().default(""),
  supporting_keywords: z.array(z.string()).default([]),
  search_intent: z.string().default("informational"),
  content_format: z.string().default(""),
  estimated_monthly_traffic: z.number().default(0),
  linked_cluster_id: z.number().nullable().optional(),
  linked_cluster_name: z.string().default(""),
  linked_collection_handle: z.string().default(""),
  linked_collection_title: z.string().default(""),
  source_type: z.string().default("cluster_gap"),
  gap_reason: z.string().default(""),
  status: z.string().default("idea"),
  created_at: z.number(),
  total_volume: z.number().default(0),
  avg_difficulty: z.number().default(0),
  opportunity_score: z.number().default(0),
  dominant_serp_features: z.string().default(""),
  content_format_hints: z.string().default(""),
  linked_keywords_json: z.array(z.record(z.any())).default([]),
  // Article link
  linked_article_handle: z.string().default(""),
  linked_blog_handle: z.string().default(""),
  shopify_article_id: z.string().default(""),
  // Aggregate metrics from idea_articles junction
  article_count: z.number().default(0),
  agg_gsc_clicks: z.number().default(0),
  agg_gsc_impressions: z.number().default(0),
  coverage_pct: z.number().nullable().optional(),
});
export type ArticleIdea = z.infer<typeof articleIdeaSchema>;

export const articleIdeasPayloadSchema = z.object({
  items: z.array(articleIdeaSchema),
  total: z.number()
});

export const linkedArticleSchema = z.object({
  id: z.number(),
  blog_handle: z.string(),
  article_handle: z.string(),
  shopify_article_id: z.string().default(""),
  angle_label: z.string().default(""),
  created_at: z.number(),
  article_title: z.string().default(""),
  is_published: z.boolean().default(false),
  gsc_clicks: z.number().default(0),
  gsc_impressions: z.number().default(0),
  gsc_position: z.number().nullable().optional(),
});
export type LinkedArticle = z.infer<typeof linkedArticleSchema>;

export const targetKeywordCoverageSchema = z.object({
  keyword: z.string(),
  is_primary: z.boolean().default(false),
  gsc_clicks: z.number().default(0),
  gsc_impressions: z.number().default(0),
  gsc_position: z.number().nullable().optional(),
  status: z.string().default("not_ranking"),
});

export const discoveredKeywordSchema = z.object({
  query: z.string(),
  clicks: z.number().default(0),
  impressions: z.number().default(0),
  position: z.number().nullable().optional(),
});

export const coverageSummarySchema = z.object({
  total_targets: z.number().default(0),
  ranking_count: z.number().default(0),
  gap_count: z.number().default(0),
  discovered_count: z.number().default(0),
  coverage_pct: z.number().default(0),
});

export const keywordCoveragePayloadSchema = z.object({
  target_keywords: z.array(targetKeywordCoverageSchema).default([]),
  discovered_keywords: z.array(discoveredKeywordSchema).default([]),
  summary: coverageSummarySchema.default({}),
});
export type KeywordCoveragePayload = z.infer<typeof keywordCoveragePayloadSchema>;

export const ideaPerformancePayloadSchema = z.object({
  articles: z.array(linkedArticleSchema).default([]),
  aggregate: z.record(z.any()).default({}),
  keyword_coverage: coverageSummarySchema.default({}),
});
export type IdeaPerformancePayload = z.infer<typeof ideaPerformancePayloadSchema>;

export const articleGenerateDraftResultSchema = z.object({
  id: z.string(),
  title: z.string(),
  handle: z.string(),
  blog_handle: z.string(),
  blog_title: z.string().default(""),
  is_published: z.boolean().default(false),
  seo_title: z.string().default(""),
  seo_description: z.string().default("")
});
export type ArticleGenerateDraftResult = z.infer<typeof articleGenerateDraftResultSchema>;

export const blogShopifyIdSchema = z.object({
  id: z.string(),
  title: z.string(),
  handle: z.string()
});

export const contentDetailSchema = z.object({
  object_type: z.string(),
  current: z.record(z.any()),
  draft: z.object({
    title: z.string(),
    seo_title: z.string(),
    seo_description: z.string(),
    body_html: z.string(),
    workflow_status: z.string(),
    workflow_notes: z.string()
  }),
  workflow: z.object({
    status: z.string(),
    notes: z.string(),
    updated_at: z.string().nullable().optional()
  }),
  recommendation: z.object({
    summary: z.string(),
    status: z.string(),
    model: z.string(),
    created_at: z.string().nullable().optional(),
    error_message: z.string(),
    details: z.record(z.any())
  }),
  recommendation_history: z.array(z.record(z.any())),
  signal_cards: z.array(z.object({
    label: z.string(),
    value: z.string(),
    sublabel: z.string(),
    updated_at: z.union([z.string(), z.number(), z.null()]).optional(),
    step: z.string(),
    action_label: z.string().nullable().optional(),
    action_href: z.string().nullable().optional()
  })),
  related_items: z.array(z.record(z.any())),
  metafields: z.array(z.record(z.any())),
  opportunity: z.record(z.any()),
  gsc_segment_summary: gscSegmentSummarySchema.default(defaultGscSegmentSummary),
  gsc_queries: z.array(gscQueryRowSchema).default([])
});

/** Shared row shape for PageSpeed + multi-service sync queue tables (`*_queue_details`). */
const syncQueueOutcomeSchema = z.enum(["downloaded", "skip_unchanged", "skip_304", "error"]);

const syncQueueDetailRowSchema = z.object({
  seq: z.coerce.number(),
  object_type: z.string(),
  handle: z.string(),
  url: z.string(),
  strategy: z.string().nullish().transform((s) => s ?? ""),
  code: z.string(),
  state: z.string(),
  error: z.string().nullish().transform((s) => s ?? ""),
  /** Catalog image warm only: worker fetch outcome. */
  outcome: syncQueueOutcomeSchema.optional(),
  http_status: z.number().optional(),
  response_body: z.string().nullish().transform((s) => s ?? undefined)
});

export const statusSchema = z.object({
  job_id: z.string().optional().default(""),
  running: z.boolean(),
  scope: z.string().optional().default(""),
  mode: z.string().optional().default(""),
  object_type: z.string().optional().default(""),
  handle: z.string().optional().default(""),
  field: z.string().optional().default(""),
  selected_scopes: z.array(z.string()).optional().default([]),
  force_refresh: z.boolean().optional().default(false),
  started_at: z.number().optional().default(0),
  finished_at: z.number().optional().default(0),
  stage: z.string().optional().default("idle"),
  stage_label: z.string().optional().default(""),
  active_scope: z.string().optional().default(""),
  active_model: z.string().optional().default(""),
  stage_started_at: z.number().optional().default(0),
  step_index: z.number().optional().default(0),
  step_total: z.number().optional().default(0),
  shopify_progress_done: z.number().optional().default(0),
  shopify_progress_total: z.number().optional().default(0),
  gsc_progress_done: z.number().optional().default(0),
  gsc_progress_total: z.number().optional().default(0),
  ga4_progress_done: z.number().optional().default(0),
  ga4_progress_total: z.number().optional().default(0),
  index_progress_done: z.number().optional().default(0),
  index_progress_total: z.number().optional().default(0),
  current: z.string().optional().default(""),
  products_synced: z.number().optional().default(0),
  products_total: z.number().optional().default(0),
  collections_synced: z.number().optional().default(0),
  collections_total: z.number().optional().default(0),
  pages_synced: z.number().optional().default(0),
  pages_total: z.number().optional().default(0),
  blogs_synced: z.number().optional().default(0),
  blogs_total: z.number().optional().default(0),
  blog_articles_synced: z.number().optional().default(0),
  blog_articles_total: z.number().optional().default(0),
  images_synced: z.number().optional().default(0),
  images_total: z.number().optional().default(0),
  gsc_refreshed: z.number().optional().default(0),
  gsc_skipped: z.number().optional().default(0),
  gsc_errors: z.number().optional().default(0),
  gsc_eligible_total: z.number().optional().default(0),
  gsc_precheck_skipped: z.number().optional().default(0),
  gsc_summary_pages: z.number().optional().default(0),
  gsc_summary_queries: z.number().optional().default(0),
  ga4_rows: z.number().optional().default(0),
  ga4_refreshed: z.number().optional().default(0),
  ga4_precheck_skipped: z.number().optional().default(0),
  ga4_url_errors: z.number().optional().default(0),
  ga4_errors: z.number().optional().default(0),
  index_refreshed: z.number().optional().default(0),
  index_skipped: z.number().optional().default(0),
  index_errors: z.number().optional().default(0),
  pagespeed_refreshed: z.number().optional().default(0),
  pagespeed_rate_limited: z.number().optional().default(0),
  pagespeed_skipped: z.number().optional().default(0),
  pagespeed_skipped_recent: z.number().optional().default(0),
  pagespeed_errors: z.number().optional().default(0),
  pagespeed_phase: z.string().optional().default(""),
  pagespeed_scanned: z.number().optional().default(0),
  pagespeed_scan_total: z.number().optional().default(0),
  pagespeed_queue_total: z.number().optional().default(0),
  pagespeed_queue_completed: z.number().optional().default(0),
  pagespeed_queue_inflight: z.number().optional().default(0),
  pagespeed_queue_baseline: z.number().optional().default(0),
  pagespeed_http_calls_last_60s: z.number().optional().default(0),
  gsc_queue_details: z.array(syncQueueDetailRowSchema).optional().default([]),
  ga4_queue_details: z.array(syncQueueDetailRowSchema).optional().default([]),
  index_queue_details: z.array(syncQueueDetailRowSchema).optional().default([]),
  shopify_queue_details: z.array(syncQueueDetailRowSchema).optional().default([]),
  gsc_sync_slots_last_60s: z.number().optional().default(0),
  ga4_sync_slots_last_60s: z.number().optional().default(0),
  index_sync_slots_last_60s: z.number().optional().default(0),
  sync_events: z.array(z.object({
    at: z.number(),
    tag: z.string(),
    msg: z.string()
  })).optional().default([]),
  pagespeed_error_details: z.array(z.object({
    seq: z.coerce.number().optional(),
    object_type: z.string(),
    handle: z.string(),
    url: z.string(),
    strategy: z.string().nullish().transform((s) => s ?? ""),
    error: z.string().nullish().transform((s) => s ?? ""),
    http_status: z.number().optional(),
    response_body: z.string().nullish().transform((s) => s ?? undefined)
  })).optional().default([]),
  pagespeed_queue_details: z.array(syncQueueDetailRowSchema).optional().default([]),
  cancel_requested: z.boolean().optional().default(false),
  successes: z.number().optional().default(0),
  failures: z.number().optional().default(0),
  last_error: z.string().optional().default(""),
  last_result: z.record(z.any()).nullable().optional(),
  steps: z.array(z.object({
    stage: z.string(),
    label: z.string(),
    model: z.string().optional().default(""),
    started_at: z.number().optional().default(0),
    finished_at: z.number().optional().default(0),
    duration_seconds: z.number().optional().default(0),
    status: z.string().optional().default("running"),
  })).optional().default([])
});

export const settingsSchema = z.object({
  values: z.object({
    store_name: z.string().default(""),
    store_description: z.string().default(""),
    primary_market_country: z.string().default(""),
    dashboard_timezone: z.string().default(""),
    store_custom_domain: z.string().default(""),
    shopify_shop: z.string().default(""),
    shopify_api_version: z.string().default(""),
    shopify_client_id: z.string().default(""),
    shopify_client_secret: z.string().default(""),
    google_client_id: z.string().default(""),
    google_client_secret: z.string().default(""),
    search_console_site: z.string().default(""),
    ga4_property_id: z.string().default(""),
    openai_api_key: z.string().default(""),
    openai_model: z.string().default(""),
    gemini_api_key: z.string().default(""),
    anthropic_api_key: z.string().default(""),
    dataforseo_api_login: z.string().default(""),
    dataforseo_api_password: z.string().default(""),
    openrouter_api_key: z.string().default(""),
    ollama_api_key: z.string().default(""),
    ollama_base_url: z.string().default(""),
    ai_generation_provider: z.string().default(""),
    ai_generation_model: z.string().default(""),
    ai_sidekick_provider: z.string().default(""),
    ai_sidekick_model: z.string().default(""),
    ai_review_provider: z.string().default(""),
    ai_review_model: z.string().default(""),
    ai_image_provider: z.string().default(""),
    ai_image_model: z.string().default(""),
    ai_vision_provider: z.string().default(""),
    ai_vision_model: z.string().default(""),
    ai_timeout_seconds: z.string().default(""),
    ai_max_retries: z.string().default(""),
    google_ads_developer_token: z.string().default(""),
    google_ads_customer_id: z.string().default(""),
    google_ads_login_customer_id: z.string().default("")
  }),
  google_configured: z.boolean(),
  google_connected: z.boolean(),
  ai_configured: z.boolean(),
  auth_url: z.string().nullable().optional(),
  available_gsc_sites: z.array(z.string()).default([]),
  available_ga4_properties: z.array(z.object({
    property_id: z.string(),
    display_name: z.string(),
    account_name: z.string()
  })).default([]),
  available_google_ads_customers: z.array(z.object({
    customer_id: z.string(),
    descriptive_name: z.string().default(""),
    resource_name: z.string().default("")
  })).default([]),
  ga4_api_activation_url: z.string().default(""),
  /** Present on current API; older backends may omit — default so the Settings UI still parses. */
  sync_scope_ready: z
    .union([
      z.object({
        shopify: z.boolean(),
        gsc: z.boolean(),
        ga4: z.boolean(),
        index: z.boolean(),
        pagespeed: z.boolean()
      }),
      z.null(),
      z.undefined()
    ])
    .transform((v) =>
      v && typeof v === "object" && typeof (v as { shopify?: unknown }).shopify === "boolean"
        ? (v as {
            shopify: boolean;
            gsc: boolean;
            ga4: boolean;
            index: boolean;
            pagespeed: boolean;
          })
        : {
            shopify: false,
            gsc: false,
            ga4: false,
            index: false,
            pagespeed: false
          }
    )
});

export const messageSchema = z.object({
  message: z.string().optional().default("ok"),
});

export const actionSchema = z.object({
  message: z.string(),
  state: z.record(z.any()).nullable().optional(),
  result: z.record(z.any()).nullable().optional(),
  steps: z.record(z.any()).nullable().optional()
});

export type Summary = z.infer<typeof summarySchema>;
export type ProductList = z.infer<typeof productListSchema>;
export type ProductDetail = z.infer<typeof productDetailSchema>;
export type ProductListItem = z.infer<typeof productListItemSchema>;
export type StatusPayload = z.infer<typeof statusSchema>;
export type ContentList = z.infer<typeof contentListSchema>;
export type ContentDetail = z.infer<typeof contentDetailSchema>;
export type SettingsPayload = z.infer<typeof settingsSchema>;

export const embeddingTypeStatusSchema = z.object({
  type: z.string(),
  embedded_objects: z.number(),
  source_objects: z.number(),
  coverage_pct: z.number(),
  chunk_count: z.number(),
  last_updated: z.string().nullable(),
  model_versions: z.string(),
});

export const embeddingStatusSchema = z.object({
  model: z.string(),
  dimensions: z.number(),
  total_embeddings: z.number(),
  total_chunks: z.number(),
  last_updated: z.string().nullable(),
  api_key_configured: z.boolean(),
  types: z.array(embeddingTypeStatusSchema),
});

export type EmbeddingStatus = z.infer<typeof embeddingStatusSchema>;
export type EmbeddingTypeStatus = z.infer<typeof embeddingTypeStatusSchema>;

const productImageSeoFlagsSchema = z.object({
  missing_or_weak_alt: z.boolean(),
  weak_filename: z.boolean(),
  /** True when CDN filename differs from template (rename on Optimize); not used for status icon. */
  seo_filename_mismatch: z.boolean().optional().default(false),
  not_webp: z.boolean(),
  is_featured: z.boolean()
});

export const catalogImageSeoRowSchema = z.object({
  resource_type: z.enum(["product", "collection", "page", "article"]),
  resource_shopify_id: z.string(),
  resource_handle: z.string(),
  resource_title: z.string(),
  blog_handle: z.string(),
  article_handle: z.string(),
  image_row_id: z.string(),
  image_shopify_id: z.string(),
  product_shopify_id: z.string(),
  product_handle: z.string(),
  product_title: z.string(),
  url: z.string(),
  alt_text: z.string(),
  position: z.number().nullable(),
  roles: z.array(z.string()),
  role_for_suggestions: z.string(),
  variant_labels: z.array(z.string()),
  suggested_filename_webp: z.string(),
  optimize_supported: z.boolean(),
  /** Product gallery only: post-sync `shopify_image_cache` has file for this URL. */
  local_file_cached: z.boolean().nullable().optional(),
  image_width: z.number().nullable().optional(),
  image_height: z.number().nullable().optional(),
  image_format: z.string().optional().default(""),
  file_size_bytes: z.number().nullable().optional(),
  flags: productImageSeoFlagsSchema
});

const imageSeoSummarySchema = z.object({
  total_images: z.number(),
  optimized: z.number(),
  missing_alt: z.number(),
  not_webp: z.number(),
  weak_filename: z.number(),
  locally_cached: z.number()
});

export const productImageSeoListSchema = z.object({
  items: z.array(catalogImageSeoRowSchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
  summary: imageSeoSummarySchema
});

export type ImageSeoSummary = z.infer<typeof imageSeoSummarySchema>;

export const productImageSeoSuggestAltResultSchema = z.object({
  ok: z.boolean().optional().default(true),
  message: z.string(),
  suggested_alt: z.string()
});

export const productImageSeoDraftStepSchema = z.object({
  id: z.string(),
  label: z.string(),
  status: z.enum(["ok", "warning", "skipped", "error"]),
  detail: z.string().optional().default("")
});

export const productImageSeoDraftResultSchema = z.object({
  ok: z.boolean(),
  message: z.string(),
  steps: z.array(productImageSeoDraftStepSchema),
  original_size_bytes: z.number(),
  draft_size_bytes: z.number(),
  draft_alt: z.string(),
  draft_filename: z.string(),
  draft_mime: z.string(),
  preview_base64: z.string().nullable().optional(),
  preview_omitted: z.boolean().optional()
});

export const productImageSeoOptimizeResultSchema = z.object({
  ok: z.boolean(),
  message: z.string(),
  dry_run: z.boolean().optional(),
  applied_alt: z.string().nullable().optional(),
  applied_filename: z.string().nullable().optional(),
  new_image_url: z.string().nullable().optional(),
  new_media_id: z.string().nullable().optional(),
  details: z.record(z.string(), z.unknown()).nullable().optional()
});

export type CatalogImageSeoRow = z.infer<typeof catalogImageSeoRowSchema>;
export type ProductImageSeoListPayload = z.infer<typeof productImageSeoListSchema>;
export type ProductImageSeoSuggestAltResult = z.infer<typeof productImageSeoSuggestAltResultSchema>;
export type ProductImageSeoDraftStep = z.infer<typeof productImageSeoDraftStepSchema>;
export type ProductImageSeoDraftResult = z.infer<typeof productImageSeoDraftResultSchema>;
export type ProductImageSeoOptimizeResult = z.infer<typeof productImageSeoOptimizeResultSchema>;

export const storeInfoSchema = z.object({
  store_url: z.string().default(""),
  store_name: z.string().default(""),
  store_description: z.string().default(""),
  primary_market_country: z.string().default(""),
  dashboard_timezone: z.string().default(""),
});

export type StoreInfo = z.infer<typeof storeInfoSchema>;

export const shopifyShopInfoSchema = z.object({
  available: z.boolean().default(false),
  shop_name: z.string().default(""),
  shop_description: z.string().default(""),
  shop_domain: z.string().default(""),
  error: z.string().default(""),
});

export type ShopifyShopInfo = z.infer<typeof shopifyShopInfoSchema>;
