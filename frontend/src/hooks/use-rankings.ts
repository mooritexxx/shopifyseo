import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { deleteJson, getJson, postJson } from "../lib/api";

export const checkSchema = z.object({
  id: z.number(),
  checked_at: z.string(),
  position: z.number().nullable(),
  reported_position: z.number().nullable(),
  ranking_url: z.string().nullable(),
  status: z.enum(["ok", "error", "unverified"]),
  error: z.string().nullable(),
  checked_depth: z.number(),
  pages_checked: z.number(),
  coverage_complete: z.number(),
  cancelled: z.number(),
  searches_used: z.number(),
  source: z.string(),
  profile: z.string(),
});
export type RankCheck = z.infer<typeof checkSchema>;
const keywordSchema = z.object({
  id: z.number(),
  term: z.string(),
  target_url: z.string().nullable(),
  grp: z.string().nullable(),
  latest: checkSchema.nullable(),
  change: z.number().nullable(),
  movement: z.string().nullable(),
  target_mismatch: z.boolean(),
  top_competitor: z.string().nullable(),
  trend: z.array(checkSchema),
});
export type RankedKeyword = z.infer<typeof keywordSchema>;
const rankingsSchema = z.object({
  items: z.array(keywordSchema),
  month_used: z.number(),
  reserved: z.number(),
  monthly_budget: z.number(),
  job: z
    .object({
      id: z.string(),
      status: z.string(),
      completed: z.number(),
      cancel_requested: z.number(),
      keyword_ids: z.string(),
      error: z.string().nullable(),
    })
    .nullable(),
});
const estimateSchema = z.object({
  keyword_ids: z.array(z.number()),
  searches_base: z.number(),
  searches_worst_case: z.number(),
  month_used: z.number(),
  monthly_budget: z.number(),
  serpapi_remaining: z.number(),
  allowed: z.boolean(),
  reason: z.string().nullable(),
  max_pages: z.number(),
});
export type RankEstimate = z.infer<typeof estimateSchema>;
export const estimateRanks = (
  keyword_ids: number[] | undefined,
  max_pages: number,
) =>
  postJson("/api/rankings/estimate", estimateSchema, {
    keyword_ids,
    max_pages,
  });
export function useRankings() {
  return useQuery({
    queryKey: ["rankings"],
    queryFn: () => getJson("/api/rankings", rankingsSchema),
    refetchInterval: (q) =>
      q.state.data?.job?.status === "running" ? 2000 : 30000,
  });
}
export function useRankHistory(id?: number) {
  return useQuery({
    queryKey: ["rankings", "history", id],
    enabled: id !== undefined,
    refetchInterval: 5000,
    queryFn: () => getJson(`/api/rankings/${id}/history`, z.array(checkSchema)),
  });
}
export function useRankActions() {
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["rankings"] });
  };
  const save = useMutation({
    mutationFn: (body: { term: string; target_url: string; grp: string }) =>
      postJson("/api/rankings/keywords", z.object({ id: z.number() }), body),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: number) =>
      deleteJson(
        `/api/rankings/keywords/${id}`,
        z.object({ removed: z.boolean() }),
      ),
    onSuccess: refresh,
  });
  const run = useMutation({
    mutationFn: (body: {
      keyword_ids: number[];
      max_pages: number;
      request_key: string;
    }) =>
      postJson(
        "/api/rankings/check",
        z.object({ job_id: z.string(), status: z.string() }),
        body,
      ),
    onSuccess: refresh,
  });
  const stop = useMutation({
    mutationFn: (id: string) =>
      postJson(
        `/api/rankings/jobs/${encodeURIComponent(id)}/stop`,
        z.object({ job_id: z.string(), status: z.string() }),
        {},
      ),
    onSuccess: refresh,
  });
  return { save, remove, run, stop };
}
