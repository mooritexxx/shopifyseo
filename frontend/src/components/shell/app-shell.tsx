import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Box,
  Check,
  CheckCircle2,
  Clock3,
  Database,
  FileText,
  FlaskConical,
  Image as ImageIcon,
  Key,
  Layers3,
  LayoutDashboard,
  Lightbulb,
  LoaderCircle,
  RefreshCw,
  Rss,
  Settings2,
  Signal,
  Sparkles,
  Square,
  X
} from "lucide-react";
import { SidekickProvider } from "../sidekick/sidekick-context";
import { FlowButton } from "../ui/flow-button";
import { useEffect, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import type { PropsWithChildren } from "react";
import type { LucideIcon } from "lucide-react";
import { z } from "zod";

import { Button } from "../ui/button";
import { Progress } from "../ui/progress";
import { ToggleSwitch } from "../ui/toggle-switch";
import { cn } from "../../lib/utils";
import { getJson, postJson } from "../../lib/api";
import { settingsSchema, statusSchema } from "../../types/api";

type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  disabled?: boolean;
};

type SyncStatusPayload = z.infer<typeof statusSchema>;

/** True if this run included Shopify catalog sync (products + collections + pages + blogs). */
function shopifyCatalogScopesTouched(status: SyncStatusPayload | undefined): boolean {
  if (!status) return false;
  const scopes = status.selected_scopes;
  if (scopes?.length) {
    return scopes.includes("shopify");
  }
  const s = (status.scope || "").toLowerCase();
  return s === "all" || ["products", "collections", "pages", "blogs"].includes(s);
}

const syncServices = [
  { value: "shopify", label: "Shopify" },
  { value: "gsc", label: "Search Console" },
  { value: "ga4", label: "GA4" },
  { value: "index", label: "Index status" },
  { value: "pagespeed", label: "PageSpeed" },
  { value: "structured", label: "Structured SEO" }
] as const;

const SYNC_SCOPE_READY_HELP: Record<(typeof syncServices)[number]["value"], string> = {
  shopify: "Add your Shopify shop and Admin API credentials under Settings → Data sources, then save.",
  gsc: "Configure Google OAuth in Settings and connect your account on Google Signals.",
  ga4: "Connect Google OAuth (same as Search Console) before syncing GA4.",
  index: "URL Inspection needs a connected Google account with Search Console access.",
  pagespeed: "PageSpeed Insights sync uses your Google OAuth session.",
  structured: "Configure Shopify first — structured SEO runs against your synced catalog."
};

const syncStageLabels: Record<string, string> = {
  idle: "Idle",
  starting: "Starting sync",
  syncing_shopify: "Shopify sync",
  syncing_products: "Products sync",
  syncing_collections: "Collections sync",
  syncing_pages: "Pages sync",
  syncing_blogs: "Blogs sync",
  syncing_product_images: "Product images (local cache)",
  refreshing_gsc: "Search Console sync",
  refreshing_ga4: "GA4 sync",
  refreshing_index: "Index status sync",
  refreshing_pagespeed: "PageSpeed sync",
  updating_structured_seo: "Structured SEO rebuild",
  complete: "Sync complete"
};

function formatElapsedTime(milliseconds: number) {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function titleCaseLabel(value: string) {
  return value
    .split(/[_-]/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function serviceStatus(activeScopes: string[], activeScope: string, running: boolean, stepIndex: number, service: string) {
  const serviceIndex = activeScopes.indexOf(service);
  if (serviceIndex === -1) return "idle" as const;
  if (!running) return "complete" as const;
  if (service === activeScope) return "running" as const;
  if (serviceIndex < Math.max(stepIndex - 1, 0)) return "complete" as const;
  return "queued" as const;
}

function syncMetricChips(syncStatus: z.infer<typeof statusSchema> | undefined) {
  if (!syncStatus) return [];
  switch (syncStatus.active_scope) {
    case "shopify":
    case "products":
    case "collections":
    case "pages":
      return [];
    case "blogs":
      return [];
    case "gsc":
      return [
        `${syncStatus.gsc_refreshed || 0} refreshed`,
        `${syncStatus.gsc_skipped || 0} skipped`,
        `${syncStatus.gsc_errors || 0} errors`
      ];
    case "ga4":
      return [`${syncStatus.ga4_rows || 0} rows cached`, `${syncStatus.ga4_errors || 0} errors`];
    case "index":
      return [
        `${syncStatus.index_refreshed || 0} refreshed`,
        `${syncStatus.index_skipped || 0} skipped (indexed)`,
        `${syncStatus.index_errors || 0} errors`
      ];
    case "pagespeed":
      return [
        `${syncStatus.pagespeed_refreshed || 0} refreshed`,
        `${syncStatus.pagespeed_skipped_recent || 0} recent skips`,
        `${syncStatus.pagespeed_rate_limited || 0} rate limited`,
        `${syncStatus.pagespeed_errors || 0} errors`
      ];
    default:
      return [];
  }
}

function syncStageLabel(stage?: string, scope?: string) {
  if (stage && syncStageLabels[stage]) return syncStageLabels[stage];
  if (!scope) return "Sync status";
  return scope === "custom" ? "Custom sync" : "Sync status";
}

function pagespeedPhaseLabel(phase?: string) {
  if (phase === "scanning") return "Scanning catalog";
  if (phase === "queueing") return "Running PageSpeed queue";
  if (phase === "complete") return "PageSpeed queue complete";
  return "PageSpeed sync";
}

function syncSelectionSummary(selectedScopes: string[]) {
  if (!selectedScopes.length) return "No services selected";
  if (selectedScopes.length === syncServices.length) return "All services";
  return selectedScopes
    .map((value) => syncServices.find((item) => item.value === value)?.label || value)
    .join(" · ");
}

const items: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard },
  { to: "/products", label: "Products", icon: Box },
  { to: "/collections", label: "Collections", icon: Layers3 },
  { to: "/pages", label: "Pages", icon: BookOpen },
  { to: "/blogs", label: "Blogs", icon: Rss },
  { to: "/articles", label: "Articles", icon: FileText },
  { to: "/article-ideas", label: "Article Ideas", icon: Lightbulb },
  { to: "/keywords", label: "Keyword Research", icon: Key },
  { to: "/image-seo", label: "Image Optimization", icon: ImageIcon },
  { to: "/google-signals", label: "Google Signals", icon: Signal },
  { to: "/google-ads-lab", label: "Google Ads lab", icon: FlaskConical },
  { to: "/embeddings", label: "Embeddings", icon: Database },
  { to: "/api-usage", label: "API Usage", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings2 }
];

export function AppShell({ children }: PropsWithChildren) {
  const [selectedScopes, setSelectedScopes] = useState<Array<(typeof syncServices)[number]["value"]>>(
    () => {
      if (typeof window === "undefined") {
        return syncServices.map((item) => item.value);
      }
      const saved = window.localStorage.getItem("seo-sync-services");
      if (!saved) {
        return syncServices.map((item) => item.value);
      }
      try {
        const parsed = JSON.parse(saved);
        if (!Array.isArray(parsed)) {
          return syncServices.map((item) => item.value);
        }
        const allowed = new Set(syncServices.map((item) => item.value));
        const normalized = parsed.filter((item) => allowed.has(item));
        return normalized.length ? normalized : syncServices.map((item) => item.value);
      } catch {
        return syncServices.map((item) => item.value);
      }
    }
  );
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [forceRefresh, setForceRefresh] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("seo-sync-force-refresh") === "true";
  });
  const [message, setMessage] = useState<string | null>(null);
  const [syncSummaryDismissed, setSyncSummaryDismissed] = useState(false);
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => getJson("/api/settings", settingsSchema),
    staleTime: 30_000
  });
  const syncStatusQuery = useQuery({
    queryKey: ["sync-status"],
    queryFn: () => getJson("/api/sync-status", statusSchema),
    // Poll frequently while sync runs so per-product / per-page progress is visible in the sidebar.
    refetchInterval: (query) => (query.state.data?.running ? 250 : false)
  });
  const syncStatus = syncStatusQuery.data;
  const syncScopeReady = settingsQuery.data?.sync_scope_ready;

  function scopeServiceReady(value: (typeof syncServices)[number]["value"]): boolean {
    if (!syncScopeReady) return true;
    return Boolean(syncScopeReady[value]);
  }
  const syncRunning = Boolean(syncStatus?.running);
  const startSyncMutation = useMutation({
    mutationFn: () =>
      postJson("/api/sync", statusSchema, {
        scope: selectedScopes.length === syncServices.length ? "all" : "custom",
        selected_scopes: selectedScopes,
        force_refresh: forceRefresh
      }),
    onSuccess: (state) => {
      setSettingsOpen(false);
      setMessage("Sync started");
      queryClient.setQueryData(["sync-status"], state);
      void syncStatusQuery.refetch();
    },
    onError: (error) => setMessage((error as Error).message)
  });
  const stopSyncMutation = useMutation({
    mutationFn: () => postJson("/api/sync/stop", statusSchema),
    onSuccess: (state) => {
      setMessage("Stop requested");
      queryClient.setQueryData(["sync-status"], state);
      void syncStatusQuery.refetch();
    },
    onError: (error) => setMessage((error as Error).message)
  });
  const hasSyncCard = Boolean(syncStatus?.running || syncStatus?.last_error || syncStatus?.last_result || message);
  const showSyncCard = hasSyncCard && (syncRunning || !syncSummaryDismissed);
  const activeStageLabel = syncStatus?.stage_label || syncStageLabel(syncStatus?.stage, syncStatus?.scope);
  const activeSelectedScopes = (syncStatus?.selected_scopes?.length ? syncStatus.selected_scopes : selectedScopes) as string[];
  const effectiveActiveScope = syncStatus?.active_scope || "";
  const activeStepIndex = syncStatus?.step_index || 0;
  const activeStepTotal = syncStatus?.step_total || 0;
  const syncStartedAt = syncStatus?.started_at ? syncStatus.started_at * 1000 : null;
  const syncFinishedAt = syncStatus?.finished_at ? syncStatus.finished_at * 1000 : null;
  const elapsedMs = syncStartedAt
    ? (syncRunning ? Math.max(0, elapsedNow - syncStartedAt) : Math.max(0, (syncFinishedAt || syncStartedAt) - syncStartedAt))
    : 0;
  const elapsedLabel = syncStartedAt ? formatElapsedTime(elapsedMs) : null;
  const isPagespeedActive = effectiveActiveScope === "pagespeed";
  const pagespeedPhase = syncStatus?.pagespeed_phase || "";
  const pagespeedScanTotal = syncStatus?.pagespeed_scan_total || 0;
  const pagespeedScanned = syncStatus?.pagespeed_scanned || 0;
  const pagespeedQueueTotal = syncStatus?.pagespeed_queue_total || 0;
  const pagespeedQueueCompleted = syncStatus?.pagespeed_queue_completed || 0;
  const progressTotal = isPagespeedActive && pagespeedPhase === "queueing" ? pagespeedQueueTotal : syncStatus?.total || 0;
  const progressDone = isPagespeedActive && pagespeedPhase === "queueing" ? pagespeedQueueCompleted : syncStatus?.done || 0;
  const syncPercent = progressTotal ? Math.max(0, Math.min(100, Math.round((progressDone / progressTotal) * 100))) : 0;
  const progressHeadline = syncRunning
    ? isPagespeedActive && pagespeedPhase
      ? pagespeedPhaseLabel(pagespeedPhase)
      : `${syncPercent}% complete`
    : syncStatus?.stage === "complete"
      ? "Sync complete"
      : syncStatus?.stage === "cancelled"
        ? "Sync cancelled"
        : syncStatus?.stage === "error"
          ? "Sync failed"
          : "Ready to sync";
  const progressDetail = syncRunning
    ? syncStatus?.current || activeStageLabel
    : message || syncStatus?.last_error || "Select services and run a sync.";
  const metricChips = syncMetricChips(syncStatus);
  const shopifyEntityScope =
    syncStatus?.active_scope === "shopify" ||
    syncStatus?.active_scope === "products" ||
    syncStatus?.active_scope === "collections" ||
    syncStatus?.active_scope === "pages" ||
    syncStatus?.active_scope === "blogs" ||
    syncStatus?.stage === "syncing_product_images";
  const progressCountLabel = isPagespeedActive && pagespeedPhase === "scanning"
    ? `${pagespeedScanned} / ${pagespeedScanTotal || progressTotal} URLs scanned`
    : isPagespeedActive && pagespeedPhase === "queueing"
      ? `${pagespeedQueueCompleted} / ${pagespeedQueueTotal} queued URLs finished`
      : shopifyEntityScope
        ? `Products ${syncStatus?.products_synced || 0}/${syncStatus?.products_total || 0} · Collections ${syncStatus?.collections_synced || 0}/${syncStatus?.collections_total || 0} · Pages ${syncStatus?.pages_synced || 0}/${syncStatus?.pages_total || 0} · Blogs ${syncStatus?.blogs_synced || 0}/${syncStatus?.blogs_total || 0} · Blog articles ${syncStatus?.blog_articles_synced || 0}/${syncStatus?.blog_articles_total || 0} · Images ${syncStatus?.images_synced || 0}/${syncStatus?.images_total || 0}`
      : progressTotal
        ? `${progressDone} / ${progressTotal} items processed`
        : "Waiting for first sync update";
  const pagespeedPhaseSummary = isPagespeedActive && pagespeedPhase === "scanning"
    ? `${pagespeedScanned} scanned, ${syncStatus?.pagespeed_skipped_recent || 0} recent skips, ${pagespeedQueueTotal || 0} queued so far`
    : null;
  const pagespeedErrorDetails = syncStatus?.pagespeed_error_details || [];

  useEffect(() => {
    window.localStorage.setItem("seo-sync-services", JSON.stringify(selectedScopes));
  }, [selectedScopes]);

  const syncReadyKey = syncScopeReady ? JSON.stringify(syncScopeReady) : "";
  useEffect(() => {
    if (!syncScopeReady) return;
    setSelectedScopes((prev) => {
      const next = prev.filter((s) => syncScopeReady[s as keyof typeof syncScopeReady]);
      if (next.length === prev.length) return prev;
      if (next.length) return next;
      const fallback = syncServices.map((item) => item.value).filter((s) => syncScopeReady[s as keyof typeof syncScopeReady]);
      return fallback.length ? fallback : [];
    });
  }, [syncReadyKey]);

  useEffect(() => {
    window.localStorage.setItem("seo-sync-force-refresh", String(forceRefresh));
  }, [forceRefresh]);

  useEffect(() => {
    if (syncRunning) {
      setSettingsOpen(false);
      setSyncSummaryDismissed(false);
    }
  }, [syncRunning]);

  useEffect(() => {
    if (!syncRunning || !syncStartedAt) return undefined;
    const intervalId = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, [syncRunning, syncStartedAt]);

  const prevSyncRunningRef = useRef(false);
  useEffect(() => {
    const running = Boolean(syncStatus?.running);
    const wasRunning = prevSyncRunningRef.current;
    prevSyncRunningRef.current = running;
    if (!wasRunning || running) return;
    const stage = syncStatus?.stage || "";
    if (stage !== "complete" && stage !== "cancelled" && stage !== "error") return;
    if (stage === "complete") {
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    }
    if (shopifyCatalogScopesTouched(syncStatus)) {
      void queryClient.invalidateQueries({ queryKey: ["image-seo-product-images"] });
    }
  }, [syncStatus, queryClient]);

  return (
    <SidekickProvider>
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_rgba(255,255,255,0.85),_transparent_28%),linear-gradient(180deg,_#f6f8fc_0%,_#ebf0f7_100%)] text-ink">
      <div className="mx-0 grid min-h-screen w-full max-w-none grid-cols-1 gap-4 px-4 py-4 lg:gap-4 lg:px-6 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="w-full rounded-[30px] border border-white/70 bg-[#0d172b] p-5 text-white shadow-panel lg:z-10 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto lg:self-start lg:sticky lg:top-4">
          <div className="space-y-4">
            <div id="app-sync-panel" className="scroll-mt-24 rounded-[24px] border border-white/10 bg-white/5 p-4">
              <p className="text-xs uppercase tracking-[0.22em] text-white/55">Sync</p>
              <div className="mt-3 grid gap-3">
                <p className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white/70">
                  {syncSelectionSummary(selectedScopes)}
                </p>
                <div className="grid grid-cols-[1fr_auto] gap-2">
                  <FlowButton
                    onClick={() => startSyncMutation.mutate()}
                    disabled={
                      syncRunning ||
                      startSyncMutation.isPending ||
                      !selectedScopes.length ||
                      selectedScopes.some((s) => !scopeServiceReady(s))
                    }
                    loading={syncRunning || startSyncMutation.isPending}
                    text={syncStatus?.running ? "Syncing" : "Sync"}
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    className="border border-white/15 bg-white/10 px-4 text-white hover:bg-white/15"
                    onClick={() => setSettingsOpen((open) => !open)}
                    disabled={syncRunning}
                  >
                    <Settings2 size={16} />
                  </Button>
                </div>
                {settingsOpen ? (
                  <div className="rounded-2xl border border-white/10 bg-white/5 p-3">
                    <p className="text-xs uppercase tracking-[0.18em] text-white/45">Sync settings</p>
                    <div className="mt-3 grid gap-2">
                      {syncServices.map((service) => {
                        const checked = selectedScopes.includes(service.value);
                        const canUse = scopeServiceReady(service.value);
                        return (
                          <Button
                            key={service.value}
                            type="button"
                            variant="ghost"
                            title={!canUse ? SYNC_SCOPE_READY_HELP[service.value] : undefined}
                            className={cn(
                              "flex h-auto items-center justify-between rounded-2xl border px-3 py-2 text-sm transition",
                              checked ? "border-[#7ea2ff] bg-[#2147b8]/45 text-white hover:bg-[#2147b8]/50" : "border-white/10 bg-white/5 text-white/70 hover:bg-white/10",
                              syncRunning ? "cursor-not-allowed opacity-60" : "",
                              !canUse ? "cursor-not-allowed opacity-45" : ""
                            )}
                            onClick={() => {
                              if (syncRunning || !canUse) return;
                              setSelectedScopes((current) =>
                                checked ? current.filter((value) => value !== service.value) : [...current, service.value]
                              );
                            }}
                            disabled={syncRunning || !canUse}
                          >
                            <span>{service.label}</span>
                            <span className={cn("flex h-5 w-5 items-center justify-center rounded-full border", checked ? "border-[#9bd9ff] bg-white/15" : "border-white/20")}>
                              {checked ? <Check size={12} /> : null}
                            </span>
                          </Button>
                        );
                      })}
                      <ToggleSwitch
                        id="seo-sync-force-refresh"
                        className="mt-2"
                        label="Force Refresh"
                        checked={forceRefresh}
                        onCheckedChange={setForceRefresh}
                        disabled={syncRunning}
                      />
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            {showSyncCard ? (
              <div className="rounded-[24px] border border-white/12 bg-[linear-gradient(180deg,rgba(255,255,255,0.09)_0%,rgba(255,255,255,0.05)_100%)] p-4">
                {syncStatus?.running ? (
                  <div className="mb-4 flex justify-center">
                    <Button variant="secondary" className="bg-white/12 px-6 py-3 text-base text-white hover:bg-white/18" onClick={() => stopSyncMutation.mutate()} disabled={stopSyncMutation.isPending}>
                      <Square className="mr-2" size={18} />
                      Stop
                    </Button>
                  </div>
                ) : null}
                <div className="mt-4 grid gap-2 rounded-2xl border border-white/10 bg-[#111b31]/70 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 flex-1 items-center gap-2 text-sm font-semibold text-white">
                      <Sparkles size={15} className={syncRunning ? "shrink-0 text-[#ffb36b]" : "shrink-0 text-white/60"} />
                      <span className="truncate">
                        {activeStepTotal && syncRunning
                          ? `${syncPercent}% complete`
                          : progressHeadline}
                      </span>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5 text-xs text-white/60">
                      {elapsedLabel ? (
                        <span className="inline-flex items-center gap-1 tabular-nums">
                          <Clock3 size={12} className="shrink-0" />
                          {elapsedLabel}
                        </span>
                      ) : null}
                      {!syncRunning ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 shrink-0 rounded-lg text-white/55 hover:bg-white/10 hover:text-white"
                          aria-label="Hide sync summary"
                          title="Hide sync summary"
                          onClick={() => {
                            setSyncSummaryDismissed(true);
                            setMessage(null);
                          }}
                        >
                          <X size={16} strokeWidth={2.25} />
                        </Button>
                      ) : null}
                    </div>
                  </div>
                  <Progress
                    value={syncPercent}
                    className="h-3 rounded-full bg-white/10"
                    indicatorClassName="rounded-full bg-[linear-gradient(90deg,#4f8cff_0%,#66e6c3_100%)]"
                  />
                  {/* Only show progressCountLabel if it's not redundant with the detailed list below (for Shopify syncs) */}
                  {!shopifyEntityScope ? (
                    <div className="flex items-center justify-between gap-3 text-xs uppercase tracking-[0.16em] text-white/45">
                      <span>{progressCountLabel}</span>
                    </div>
                  ) : null}
                </div>
                {/* Hide while running: backend `current` is per-item noise ("Index status: product:…", GSC, PageSpeed). Progress bar + counts are enough. */}
                {progressDetail &&
                !syncRunning &&
                !shopifyEntityScope ? (
                  <p className="mt-3 text-sm text-white/75">{progressDetail}</p>
                ) : null}
                {pagespeedPhaseSummary ? <p className="mt-2 text-xs uppercase tracking-[0.14em] text-white/45">{pagespeedPhaseSummary}</p> : null}
                {metricChips.length ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {metricChips.map((chip) => (
                      <span key={chip} className="rounded-full border border-white/12 bg-white/8 px-2.5 py-1 text-[11px] uppercase tracking-[0.16em] text-white/68">
                        {chip}
                      </span>
                    ))}
                  </div>
                ) : null}
                {(shopifyEntityScope ||
                  (!syncRunning &&
                    (syncStatus?.products_total ||
                      syncStatus?.collections_total ||
                      syncStatus?.pages_total ||
                      syncStatus?.blogs_total ||
                      syncStatus?.images_total))) ? (
                  <div className="mt-3 space-y-2 text-xs">
                    {[
                      { label: "Products", synced: syncStatus?.products_synced || 0, total: syncStatus?.products_total || 0 },
                      { label: "Collections", synced: syncStatus?.collections_synced || 0, total: syncStatus?.collections_total || 0 },
                      { label: "Pages", synced: syncStatus?.pages_synced || 0, total: syncStatus?.pages_total || 0 },
                      { label: "Blogs", synced: syncStatus?.blogs_synced || 0, total: syncStatus?.blogs_total || 0 },
                      { label: "Blog articles", synced: syncStatus?.blog_articles_synced || 0, total: syncStatus?.blog_articles_total || 0 },
                      { label: "Images", synced: syncStatus?.images_synced || 0, total: syncStatus?.images_total || 0 },
                    ].map(({ label, synced, total }) => {
                      return (
                        <div key={label} className="flex items-center justify-between gap-3">
                          <span className="text-[10px] uppercase tracking-[0.16em] text-white/50">{label}</span>
                          <span className="font-semibold text-white">{synced} / {total}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
                {pagespeedErrorDetails.length > 0 ? (
                  <div className="mt-3 rounded-2xl border border-[#5c2833] bg-[#2a141a]/70 p-3">
                    <p className="text-xs uppercase tracking-[0.18em] text-[#f0b7c1]">Recent PageSpeed Errors</p>
                    <div className="mt-2 grid gap-2">
                      {pagespeedErrorDetails.slice(-3).reverse().map((item) => (
                        <div key={`${item.object_type}:${item.handle}:${item.url}`} className="rounded-xl border border-white/8 bg-white/5 px-3 py-2">
                          <p className="text-[11px] uppercase tracking-[0.16em] text-[#f0b7c1]">{item.object_type}:{item.handle}</p>
                          <p className="mt-1 break-all text-xs text-white/70">{item.error}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <div className="mt-4 grid gap-2">
                  {syncServices.map((service) => {
                    const state = serviceStatus(activeSelectedScopes, effectiveActiveScope, syncRunning, activeStepIndex, service.value);
                    return (
                      <div key={service.value} className="flex items-center justify-between rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-sm">
                        <span className="text-white/80">{service.label}</span>
                        <span
                          className={cn(
                            "inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] uppercase tracking-[0.18em]",
                            state === "running" ? "bg-[#2147b8]/45 text-[#b7cdff]" : "",
                            state === "complete" ? "bg-[#173728] text-[#91efbb]" : "",
                            state === "queued" ? "bg-white/10 text-white/60" : "",
                            state === "idle" ? "bg-white/5 text-white/35" : ""
                          )}
                        >
                          {state === "running" ? <LoaderCircle size={10} className="animate-spin" /> : null}
                          {state === "complete" ? <Check size={10} /> : null}
                          {state === "running" ? "Running" : state === "complete" ? "Done" : state === "queued" ? "Queued" : "Off"}
                        </span>
                      </div>
                    );
                  })}
                </div>
                {syncStatus?.cancel_requested ? <p className="mt-3 text-xs uppercase tracking-[0.16em] text-[#ffcf9f]">Stopping sync...</p> : null}
              </div>
            ) : null}
          </div>

          <nav className="mt-6 grid gap-2">
            {items.map((item) => {
              const Icon = item.icon;
              if (item.disabled) {
                return (
                  <span key={item.to} className="flex items-center gap-3 rounded-2xl px-4 py-3 text-sm text-white/45">
                    <Icon size={16} />
                    {item.label}
                    <span className="ml-auto rounded-full bg-white/10 px-2 py-0.5 text-[10px] uppercase tracking-[0.18em]">Later</span>
                  </span>
                );
              }
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-3 rounded-2xl px-4 py-3 text-sm transition",
                      isActive ? "bg-white text-ink" : "text-white/80 hover:bg-white/10"
                    )
                  }
                >
                  <Icon size={16} />
                  {item.label}
                </NavLink>
              );
            })}
          </nav>
        </aside>
        <main className="min-w-0">{children}</main>
      </div>
    </div>
    </SidekickProvider>
  );
}
