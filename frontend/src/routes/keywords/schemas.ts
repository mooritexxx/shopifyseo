import { z } from "zod";
import { matchSchema, clusterSchema } from "../../types/api";

export const seedKeywordSchema = z.object({
  keyword: z.string(),
  source: z.string()
});

export const seedPayloadSchema = z.object({
  items: z.array(seedKeywordSchema),
  total: z.number()
});

export const targetKeywordSchema = z.object({
  keyword: z.string(),
  volume: z.number().nullable(),
  difficulty: z.number().nullable(),
  traffic_potential: z.number().nullable(),
  cpc: z.number().nullable(),
  intent: z.string().nullable(),
  intent_raw: z.record(z.boolean()).nullable().optional(),
  content_type: z.string().nullable(),
  parent_topic: z.string().nullable().optional(),
  opportunity: z.number().nullable(),
  seed_keywords: z.array(z.string()).optional(),
  gsc_position: z.number().nullable().optional(),
  gsc_clicks: z.number().nullable().optional(),
  gsc_impressions: z.number().nullable().optional(),
  ranking_status: z.string().nullable().optional(),
  status: z.string(),
  /** Google Ads Keyword Planner — last ``GenerateKeywordHistoricalMetrics`` refresh. */
  ads_avg_monthly_searches: z.number().nullable().optional(),
  ads_competition: z.string().nullable().optional(),
  ads_competition_index: z.number().nullable().optional()
});

export const googleAdsPlannerRefreshDataSchema = z.object({
  updated: z.number(),
  requested: z.number(),
  planner_batches: z.number().optional(),
  planner_parts: z.number().optional(),
  matched_metrics: z.number(),
  errors: z.array(z.string()),
  items: z.array(targetKeywordSchema),
  total: z.number(),
  last_run: z.string().nullable().optional(),
  unit_cost: z.number().nullable().optional()
});

export const targetPayloadSchema = z.object({
  items: z.array(targetKeywordSchema),
  total: z.number(),
  last_run: z.string().nullable().optional(),
  unit_cost: z.number().nullable().optional()
});

export type TargetKeyword = z.infer<typeof targetKeywordSchema>;

export const clustersPayloadSchema = z.object({
  clusters: z.array(clusterSchema),
  generated_at: z.string().nullable(),
});

export const matchOptionsPayloadSchema = z.object({
  options: z.array(matchSchema),
});

export const competitorProfileSchema = z.object({
  domain: z.string(),
  keywords_common: z.number(),
  keywords_they_have: z.number(),
  keywords_we_have: z.number(),
  share: z.number(),
  traffic: z.number(),
  is_manual: z.number(),
  updated_at: z.number(),
  labs_visibility: z.number().optional().default(0),
  labs_avg_position: z.number().optional().default(0),
  labs_median_position: z.number().optional().default(0),
  labs_seed_etv: z.number().optional().default(0),
  labs_bulk_etv: z.number().optional().default(0),
  labs_rating: z.number().optional().default(0),
});

export const competitorResearchMetaSchema = z
  .object({
    finished_at: z.string().optional(),
    keyword_provider: z.string().optional(),
    unit_cost: z.number().optional(),
    errors: z.array(z.string()).optional(),
    organic_keywords_ok: z.number().optional(),
    organic_keywords_failed: z.number().optional(),
    competitors_total: z.number().optional()
  })
  .optional();

export const competitorPayloadSchema = z.object({
  items: z.array(competitorProfileSchema),
  total: z.number(),
  last_research: competitorResearchMetaSchema.optional(),
  pending_suggestions: z.array(competitorProfileSchema).optional().default([]),
  dismissed_competitors: z.array(competitorProfileSchema).optional().default([]),
  suggestions: z.array(competitorProfileSchema).optional(),
  target_domain: z.string().optional(),
  unit_cost: z.number().optional()
});
