import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

const SUMMARY_POLL_MS = 1500;

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
  status: "suggested" | "applied" | "dismissed" | "undone";
  weak_anchor_warning?: string | null;
  applied_at?: number | null;
}

export interface AppliedLink extends LinkSuggestion {
  live_present: boolean | null;
  href: string | null;
}

export interface LinkPreview {
  suggestion_id: number;
  kind: "phrase_wrap" | "ai_woven";
  current_body_snippet: string | null;
  preview_body_snippet: string | null;
  anchor_phrase: string | null;
  target_url: string;
}

export interface GraphNode {
  id: string;
  object_type: string;
  handle: string;
  inbound: number;
  outbound: number;
  is_focus: boolean;
}

export interface GraphEdge {
  source: string;
  target: string;
  anchor_text: string;
}

export interface GraphMapData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  node_count: number;
  edge_count: number;
}

export interface LinkSummary {
  total_links: number;
  orphan_count: number;
  suggested: number;
  applied: number;
  dismissed: number;
  progress: { running: boolean; stage: string; done: number; total: number; error: string | null };
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
  const qc = useQueryClient();
  const wasRunning = useRef(false);

  const query = useQuery({
    queryKey: ["internal-links", "summary"],
    queryFn: () => getJson<LinkSummary>("/api/internal-links/summary"),
    // Keep polling while a rebuild is in progress so the UI leaves "Rebuilding…"
    // and picks up final counts without a manual refresh.
    refetchInterval: (q) => (q.state.data?.progress?.running ? SUMMARY_POLL_MS : false),
  });

  useEffect(() => {
    const running = Boolean(query.data?.progress?.running);
    if (wasRunning.current && !running) {
      // Rebuild just finished — refresh suggestions/orphans/graph without polling them.
      void qc.invalidateQueries({
        queryKey: ["internal-links"],
        predicate: (q) => q.queryKey[1] !== "summary",
      });
    }
    wasRunning.current = running;
  }, [query.data?.progress?.running, qc]);

  return query;
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

export function useAppliedLinks(params: { sourceType?: string; sourceHandle?: string; problemsOnly?: boolean } = {}) {
  const search = new URLSearchParams();
  if (params.sourceType) search.set("source_type", params.sourceType);
  if (params.sourceHandle) search.set("source_handle", params.sourceHandle);
  if (params.problemsOnly) search.set("problems_only", "true");
  const query = search.toString();
  return useQuery({
    queryKey: ["internal-links", "applied", params],
    queryFn: () => getJson<AppliedLink[]>(`/api/internal-links/applied${query ? `?${query}` : ""}`),
  });
}

export function useUndoSuggestion() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: number) => postJson<{ status: string; url?: string; link_not_found?: boolean; message?: string }>(
      `/api/internal-links/suggestions/${id}/undo`,
    ),
    onSuccess: invalidate,
  });
}

export function useLinkPreview(suggestionId: number | null) {
  return useQuery({
    queryKey: ["internal-links", "preview", suggestionId],
    queryFn: () => getJson<LinkPreview>(`/api/internal-links/suggestions/${suggestionId}/preview`),
    enabled: suggestionId !== null,
  });
}

export function useGraphMapData(params: { focusType?: string; focusHandle?: string; maxNodes?: number } = {}) {
  const search = new URLSearchParams();
  if (params.focusType) search.set("focus_type", params.focusType);
  if (params.focusHandle) search.set("focus_handle", params.focusHandle);
  if (params.maxNodes) search.set("max_nodes", String(params.maxNodes));
  const query = search.toString();
  return useQuery({
    queryKey: ["internal-links", "graph-map", params],
    queryFn: () => getJson<GraphMapData>(`/api/internal-links/graph-map${query ? `?${query}` : ""}`),
  });
}

// Phase D: Outcomes for measurement
export interface LinkOutcomes {
  days: number;
  applied: number;
  auto_applied: number;
  undone: number;
  dismissed: number;
  undo_rate_pct: number;
  top_targets: Array<{ target_type: string; target_handle: string; count: number }>;
  clicks_comparison: {
    at_apply: number;
    current: number;
    change: number;
    link_count: number;
  } | null;
}

export function useLinkOutcomes(days: number = 28) {
  return useQuery({
    queryKey: ["internal-links", "outcomes", days],
    queryFn: () => getJson<LinkOutcomes>(`/api/internal-links/outcomes?days=${days}`),
  });
}

// Phase E: Auto-apply settings (legacy)
export interface AutoApplySettings {
  enabled: boolean;
  min_score: number;
  max_per_day: number;
  kinds: string[];
  applied_today: number;
}

export function useAutoApplySettings() {
  return useQuery({
    queryKey: ["internal-links", "auto-apply-settings"],
    queryFn: () => getJson<AutoApplySettings>("/api/internal-links/auto-apply/settings"),
  });
}

export function useRunAutoApply() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (dryRun: boolean = false) =>
      postJson<{ status: string; applied?: number; skipped?: number }>(
        `/api/internal-links/auto-apply/run?dry_run=${dryRun}`,
      ),
    onSuccess: invalidate,
  });
}

// Phase B+E: Comprehensive internal link settings
export interface InternalLinkSettings {
  sim_threshold: number;
  sim_threshold_default: number;
  ai_body_links_enabled: boolean;
  auto_apply_enabled: boolean;
  auto_apply_min_score: number;
  auto_apply_min_score_default: number;
  auto_apply_max_per_day: number;
  auto_apply_max_per_day_default: number;
  auto_apply_kinds: string[];
  auto_applied_today: number;
}

export function useInternalLinkSettings() {
  return useQuery({
    queryKey: ["internal-links", "settings"],
    queryFn: () => getJson<InternalLinkSettings>("/api/internal-links/settings"),
  });
}

export interface SaveSettingsParams {
  sim_threshold?: number;
  ai_body_links_enabled?: boolean;
  auto_apply_enabled?: boolean;
  auto_apply_min_score?: number;
  auto_apply_max_per_day?: number;
}

export function useSaveInternalLinkSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params: SaveSettingsParams) => {
      const search = new URLSearchParams();
      if (params.sim_threshold !== undefined) search.set("sim_threshold", String(params.sim_threshold));
      if (params.ai_body_links_enabled !== undefined) search.set("ai_body_links_enabled", String(params.ai_body_links_enabled));
      if (params.auto_apply_enabled !== undefined) search.set("auto_apply_enabled", String(params.auto_apply_enabled));
      if (params.auto_apply_min_score !== undefined) search.set("auto_apply_min_score", String(params.auto_apply_min_score));
      if (params.auto_apply_max_per_day !== undefined) search.set("auto_apply_max_per_day", String(params.auto_apply_max_per_day));
      return fetch(`/api/internal-links/settings?${search.toString()}`, { method: "PUT" })
        .then((r) => r.json())
        .then((j) => j.data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["internal-links"] });
    },
  });
}
