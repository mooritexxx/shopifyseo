import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

export interface LinkSuggestion {
  id: number;
  source_type: string;
  source_handle: string;
  target_type: string;
  target_handle: string;
  kind: "phrase_wrap" | "ai_woven";
  anchor_phrase: string | null;
  ai_anchor_html: string | null;
  score: number;
  status: "suggested" | "applied" | "dismissed";
}

export interface LinkSummary {
  total_links: number;
  orphan_count: number;
  suggested: number;
  applied: number;
  dismissed: number;
  progress: { running: boolean; stage: string; done: number; total: number };
}

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  const body = await res.json();
  if (!body.ok) throw new Error(body.error?.message ?? "Request failed");
  return body.data as T;
}

async function postJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { method: "POST" });
  const body = await res.json();
  if (!body.ok) throw new Error(body.error?.message ?? body.detail ?? "Request failed");
  return body.data as T;
}

export function useLinkSummary() {
  return useQuery({ queryKey: ["internal-links", "summary"], queryFn: () => getJson<LinkSummary>("/api/internal-links/summary") });
}

export function useLinkSuggestions(params: { sourceType?: string; sourceHandle?: string } = {}) {
  const search = new URLSearchParams({ status: "suggested" });
  if (params.sourceType) search.set("source_type", params.sourceType);
  if (params.sourceHandle) search.set("source_handle", params.sourceHandle);
  return useQuery({
    queryKey: ["internal-links", "suggestions", params],
    queryFn: () => getJson<LinkSuggestion[]>(`/api/internal-links/suggestions?${search}`),
  });
}

export interface Orphan {
  object_type: string;
  handle: string;
  gsc_clicks: number;
  gsc_impressions: number;
}

export function useOrphans() {
  return useQuery({
    queryKey: ["internal-links", "orphans"],
    queryFn: () => getJson<Orphan[]>("/api/internal-links/orphans"),
  });
}

export interface GraphEntity {
  object_type: string;
  handle: string;
  inbound: number;
  outbound: number;
}

export function useGraphStatsAll() {
  return useQuery({
    queryKey: ["internal-links", "graph-stats", "all"],
    queryFn: () => getJson<{ entities: GraphEntity[] }>("/api/internal-links/graph-stats"),
  });
}

export function useGraphStatsForEntity(objectType: string, handle: string) {
  const url = `/api/internal-links/graph-stats?object_type=${encodeURIComponent(objectType)}&handle=${encodeURIComponent(handle)}`;
  return useQuery({
    queryKey: ["internal-links", "graph-stats", objectType, handle],
    queryFn: () => getJson<GraphEntity>(url),
    enabled: Boolean(objectType) && Boolean(handle),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["internal-links"] });
}

export function useApplySuggestion() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => postJson(`/api/internal-links/suggestions/${id}/apply`),
    onSuccess: invalidate,
  });
}

export function useDismissSuggestion() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => postJson(`/api/internal-links/suggestions/${id}/dismiss`),
    onSuccess: invalidate,
  });
}

export function useGenerateAnchor() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) =>
      postJson<{ ai_anchor_html: string; current_body: string }>(
        `/api/internal-links/suggestions/${id}/generate-anchor`,
      ),
    onSuccess: invalidate,
  });
}

export function useRebuildLinks() {
  const invalidate = useInvalidate();
  return useMutation({ mutationFn: () => postJson("/api/internal-links/rebuild"), onSuccess: invalidate });
}
