import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Box,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ClipboardCopy,
  Clock3,
  Database,
  FileText,
  FlaskConical,
  HelpCircle,
  Image as ImageIcon,
  Key,
  Layers3,
  LayoutDashboard,
  Lightbulb,
  LoaderCircle,
  Rss,
  Settings2,
  Sparkles,
  Square,
  X
} from "lucide-react";
import { SidekickProvider } from "../sidekick/sidekick-context";
import { FlowButton } from "../ui/flow-button";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import type { PropsWithChildren } from "react";
import type { LucideIcon } from "lucide-react";
import { z } from "zod";

import { Button } from "../ui/button";
import { Progress } from "../ui/progress";
import { ToggleSwitch } from "../ui/toggle-switch";
import { readStoredOverviewGscPeriod } from "../../lib/gsc-period";
import { cn, formatRelativeTimestamp } from "../../lib/utils";
import { getJson, postJson } from "../../lib/api";
import { settingsSchema, statusSchema, summarySchema } from "../../types/api";

type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  disabled?: boolean;
  group: string;
  badge?: string;
  countKey?: "products" | "collections" | "pages" | "blogs";
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
  gsc: "Configure Google OAuth in Settings → Data sources, then pick a Search Console property.",
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

/** Legacy payloads concatenated Python tracebacks after the user-facing line. */
function splitSyncError(raw: string): { summary: string; details: string | null } {
  const idx = raw.indexOf("\n\nTraceback");
  if (idx !== -1) {
    return { summary: raw.slice(0, idx).trim(), details: raw.slice(idx + 2).trim() };
  }
  return { summary: raw.trim(), details: null };
}

function syncErrorSuggestsSettings(err: string): boolean {
  const e = err.toLowerCase();
  return (
    e.includes("search console") ||
    e.includes("ga4") ||
    e.includes("gsc") ||
    e.includes("pagespeed") ||
    e.includes("page speed") ||
    e.includes("url inspection") ||
    (e.includes("google") && (e.includes("oauth") || e.includes("not connected")))
  );
}

const items: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, group: "Workspace" },
  { to: "/products", label: "Products", icon: Box, group: "Catalog", countKey: "products" },
  { to: "/collections", label: "Collections", icon: Layers3, group: "Catalog", countKey: "collections" },
  { to: "/pages", label: "Pages", icon: BookOpen, group: "Catalog", countKey: "pages" },
  { to: "/blogs", label: "Blogs", icon: Rss, group: "Catalog", countKey: "blogs" },
  { to: "/articles", label: "Articles", icon: FileText, group: "Content" },
  { to: "/article-ideas", label: "Article Ideas", icon: Lightbulb, group: "Content", badge: "NEW" },
  { to: "/keywords", label: "Keyword Research", icon: Key, group: "Research" },
  { to: "/image-seo", label: "Image Optimization", icon: ImageIcon, group: "Research" },
  { to: "/google-ads-lab", label: "Google Ads lab", icon: FlaskConical, group: "Research" },
  { to: "/embeddings", label: "Embeddings", icon: Database, group: "System" },
  { to: "/api-usage", label: "API Usage", icon: Activity, group: "System" },
  { to: "/settings", label: "Settings", icon: Settings2, group: "System" }
];

function lastSyncShort(lastAt: string | null | undefined) {
  if (!lastAt?.trim()) return "Never synced";
  const full = formatRelativeTimestamp(lastAt);
  return full.split(" · ")[0] ?? full;
}

function shopInitials(name: string, shop: string) {
  const base = (name || shop || "S").trim();
  const parts = base.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase().slice(0, 2);
  return base.slice(0, 2).toUpperCase() || "S";
}

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
  const [syncErrorTechnicalOpen, setSyncErrorTechnicalOpen] = useState(false);
  const [syncErrorCopied, setSyncErrorCopied] = useState(false);
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("seo-sidebar-rail") === "1";
  });
  const queryClient = useQueryClient();
  const overviewGscPeriod = readStoredOverviewGscPeriod();
  const settingsQuery = useQuery({
    queryKey: ["settings"],
    queryFn: () => getJson("/api/settings", settingsSchema),
    staleTime: 30_000
  });
  const summaryQuery = useQuery({
    queryKey: ["summary", overviewGscPeriod, "all"],
    queryFn: () =>
      getJson(
        `/api/summary?gsc_period=${overviewGscPeriod}&gsc_segment=${encodeURIComponent("all")}`,
        summarySchema
      ),
    staleTime: 60_000
  });
  const syncStatusQuery = useQuery({
    queryKey: ["sync-status"],
    queryFn: () => getJson("/api/sync-status", statusSchema),
    // Poll frequently while sync runs so per-product / per-page progress is visible in the sidebar.
    refetchInterval: (query) => (query.state.data?.running ? 250 : false)
  });
  const syncStatus = syncStatusQuery.data;
  const syncScopeReady = settingsQuery.data?.sync_scope_ready;
  const summary = summaryQuery.data;
  const navGroups = useMemo(() => {
    const m = new Map<string, NavItem[]>();
    for (const it of items) {
      if (!m.has(it.group)) m.set(it.group, []);
      m.get(it.group)!.push(it);
    }
    return Array.from(m.entries());
  }, []);
  const shopBlock = useMemo(() => {
    const v = settingsQuery.data?.values;
    const name = (v?.store_name || "").trim() || "Shopify SEO";
    const domain = (v?.store_custom_domain || v?.shopify_shop || "").trim() || "Local dashboard";
    return { name, domain, initials: shopInitials(v?.store_name || "", v?.shopify_shop || "") };
  }, [settingsQuery.data?.values]);

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
  const rawSyncError = (syncStatus?.last_error || "").trim();
  const syncErrorParts = useMemo(() => splitSyncError(rawSyncError), [rawSyncError]);
  const showSyncErrorPanel = !syncRunning && Boolean(rawSyncError);
  const progressDetail = syncRunning
    ? syncStatus?.current || activeStageLabel
    : message || (showSyncErrorPanel ? "" : "Select services and run a sync.");
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

  const hideTopSyncPanel = syncRunning;
  const syncAccent = "oklch(0.62 0.18 262)";

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
    window.localStorage.setItem("seo-sidebar-rail", sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1023px)");
    const clearRail = () => {
      if (mq.matches) setSidebarCollapsed(false);
    };
    clearRail();
    mq.addEventListener("change", clearRail);
    return () => mq.removeEventListener("change", clearRail);
  }, []);

  useEffect(() => {
    if (syncRunning) setSidebarCollapsed(false);
  }, [syncRunning]);

  useEffect(() => {
    if (sidebarCollapsed) setSettingsOpen(false);
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (!syncRunning || !syncStartedAt) return undefined;
    const intervalId = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, [syncRunning, syncStartedAt]);

  useEffect(() => {
    setSyncErrorTechnicalOpen(false);
    setSyncErrorCopied(false);
  }, [rawSyncError]);

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
      <div
        className={cn(
          "mx-0 grid min-h-screen w-full max-w-none grid-cols-1 gap-4 px-4 py-4 lg:gap-4 lg:px-6",
          sidebarCollapsed ? "lg:grid-cols-[72px_minmax(0,1fr)]" : "lg:grid-cols-[260px_minmax(0,1fr)]"
        )}
      >
        <aside
          className={cn(
            "flex w-full flex-col gap-3 rounded-[24px] border border-white/70 bg-[#0d172b] text-white shadow-[0_20px_60px_-30px_rgba(13,23,43,0.55)] transition-[padding,gap] duration-200 ease-out",
            "max-lg:rounded-[30px] max-lg:p-5",
            sidebarCollapsed ? "lg:gap-2 lg:p-2.5 lg:py-3" : "lg:p-4",
            "lg:z-10 lg:max-h-[calc(100vh-2rem)] lg:self-start lg:sticky lg:top-4 lg:overflow-x-hidden lg:overflow-y-hidden"
          )}
        >
          {/* Shop header — V1 refined dark */}
          <div
            className={cn(
              "flex shrink-0 items-center gap-2.5",
              sidebarCollapsed ? "lg:flex-col lg:justify-center lg:gap-2" : "border-b border-white/[0.08] pb-3",
              !sidebarCollapsed && "max-lg:border-b max-lg:pb-3"
            )}
          >
            <div
              className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] text-[15px] font-bold text-white shadow-lg"
              style={{
                background: `linear-gradient(135deg, ${syncAccent}, oklch(0.55 0.18 220))`,
                boxShadow: `0 6px 18px -6px color-mix(in oklab, ${syncAccent} 55%, transparent)`
              }}
            >
              {shopBlock.initials}
            </div>
            {!sidebarCollapsed ? (
              <>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[13px] font-semibold leading-tight">{shopBlock.name}</div>
                  <div className="truncate text-[11px] text-white/50">{shopBlock.domain}</div>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  title="Narrow sidebar"
                  aria-label="Narrow sidebar"
                  onClick={() => setSidebarCollapsed(true)}
                  className="hidden h-7 w-7 shrink-0 rounded-lg border-0 bg-white/[0.06] text-white/70 hover:bg-white/[0.1] hover:text-white lg:inline-flex"
                >
                  <ChevronLeft size={16} strokeWidth={2} />
                </Button>
              </>
            ) : null}
          </div>

          {sidebarCollapsed ? (
            <button
              type="button"
              title="Expand sidebar"
              aria-label="Expand sidebar"
              onClick={() => setSidebarCollapsed(false)}
              className="hidden h-7 w-full shrink-0 items-center justify-center rounded-lg border-0 bg-white/[0.06] text-white/70 hover:bg-white/[0.1] hover:text-white lg:flex"
            >
              <ChevronRight size={14} strokeWidth={2} />
            </button>
          ) : null}

          {!hideTopSyncPanel && !sidebarCollapsed ? (
            <div id="app-sync-panel" className="scroll-mt-24 shrink-0 rounded-[14px] border border-white/[0.08] bg-white/[0.035] p-3">
              <div className="mb-2.5 flex items-center gap-2">
                <div className="h-2 w-2 shrink-0 rounded-full bg-emerald-400/90" />
                <div className="flex-1 text-xs font-medium text-white/85">Ready to sync</div>
                <span className="font-mono text-[10px] tabular-nums text-white/45">
                  {lastSyncShort(summary?.last_dashboard_sync_at)}
                </span>
              </div>
              <div className="mb-2.5 flex gap-1">
                {syncServices.map((s) => (
                  <div key={s.value} className="h-0.5 flex-1 rounded-sm bg-white/10" />
                ))}
              </div>
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
                  text={
                    syncStatus?.running
                      ? "Syncing"
                      : `Run sync · ${selectedScopes.length} service${selectedScopes.length === 1 ? "" : "s"}`
                  }
                />
                <Button
                  type="button"
                  variant="secondary"
                  className="border border-white/15 bg-white/10 px-3 text-white hover:bg-white/15"
                  onClick={() => setSettingsOpen((open) => !open)}
                  disabled={syncRunning}
                  title="Sync services & options"
                >
                  <Settings2 size={16} />
                </Button>
              </div>
              {settingsOpen ? (
                <div className="mt-3 rounded-2xl border border-white/10 bg-white/5 p-3">
                  <p className="text-xs uppercase tracking-[0.18em] text-white/45">Sync settings</p>
                  <p className="mt-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-white/60">
                    {syncSelectionSummary(selectedScopes)}
                  </p>
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
                            checked
                              ? "border-[oklch(0.62_0.18_262/0.55)] bg-[oklch(0.62_0.18_262/0.18)] text-white hover:bg-[oklch(0.62_0.18_262/0.22)]"
                              : "border-white/10 bg-white/5 text-white/70 hover:bg-white/10",
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
                          <span
                            className={cn(
                              "flex h-5 w-5 items-center justify-center rounded-full border",
                              checked ? "border-white/30 bg-white/15" : "border-white/20"
                            )}
                          >
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
          ) : null}

          {!hideTopSyncPanel && sidebarCollapsed ? (
            <div className="relative hidden shrink-0 lg:block">
              <Button
                type="button"
                variant="ghost"
                title={
                  hasSyncCard && !syncRunning
                    ? "Expand sidebar to view sync status"
                    : "Run sync (expand sidebar for options)"
                }
                aria-label="Run sync"
                onClick={() => {
                  if (hasSyncCard && !syncRunning) {
                    setSidebarCollapsed(false);
                    return;
                  }
                  startSyncMutation.mutate();
                }}
                disabled={
                  startSyncMutation.isPending ||
                  !selectedScopes.length ||
                  selectedScopes.some((s) => !scopeServiceReady(s))
                }
                className={cn(
                  "h-10 w-full rounded-xl border-0 text-white shadow-md",
                  startSyncMutation.isPending ? "bg-white/10" : "bg-[oklch(0.62_0.18_262)] hover:opacity-95"
                )}
              >
                {startSyncMutation.isPending ? (
                  <LoaderCircle className="animate-spin" size={16} />
                ) : (
                  <Sparkles size={16} />
                )}
              </Button>
              {hasSyncCard && !syncRunning ? (
                <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-amber-400 shadow-[0_0_0_2px_#0d172b]" />
              ) : null}
            </div>
          ) : null}

            {!sidebarCollapsed && showSyncCard ? (
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
                    indicatorClassName="rounded-full bg-[linear-gradient(90deg,oklch(0.62_0.18_262)_0%,oklch(0.78_0.12_195)_100%)]"
                  />
                  {/* Only show progressCountLabel if it's not redundant with the detailed list below (for Shopify syncs) */}
                  {!shopifyEntityScope ? (
                    <div className="flex items-center justify-between gap-3 text-xs uppercase tracking-[0.16em] text-white/45">
                      <span>{progressCountLabel}</span>
                    </div>
                  ) : null}
                </div>
                {/* Hide while running: backend `current` is per-item noise ("Index status: product:…", GSC, PageSpeed). Progress bar + counts are enough. */}
                {showSyncErrorPanel ? (
                  <div className="mt-3 rounded-2xl border border-[#5c2833] bg-[#2a141a]/70 p-3">
                    <p className="text-xs uppercase tracking-[0.18em] text-[#f0b7c1]">Sync failed</p>
                    <p className="mt-2 break-words text-sm leading-snug text-white/85">{syncErrorParts.summary}</p>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        className="h-8 border border-white/15 bg-white/10 px-3 text-xs text-white hover:bg-white/15"
                        onClick={() => {
                          void navigator.clipboard.writeText(rawSyncError).then(() => {
                            setSyncErrorCopied(true);
                            window.setTimeout(() => setSyncErrorCopied(false), 2000);
                          });
                        }}
                      >
                        <ClipboardCopy size={14} className="mr-1.5 shrink-0 opacity-80" />
                        {syncErrorCopied ? "Copied" : "Copy error"}
                      </Button>
                      {syncErrorSuggestsSettings(rawSyncError) ? (
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          className="h-8 border border-white/15 bg-white/10 px-0 hover:bg-white/15"
                          asChild
                        >
                          <NavLink to="/settings" className="inline-flex items-center px-3 text-xs text-white">
                            <Settings2 size={14} className="mr-1.5 shrink-0 opacity-80" />
                            Open Settings
                          </NavLink>
                        </Button>
                      ) : null}
                    </div>
                    {syncErrorParts.details ? (
                      <div className="mt-2">
                        <Button
                          type="button"
                          variant="ghost"
                          className="h-auto w-full justify-between rounded-xl px-2 py-2 text-left text-xs text-white/65 hover:bg-white/10 hover:text-white/85"
                          onClick={() => setSyncErrorTechnicalOpen((o) => !o)}
                        >
                          <span className="uppercase tracking-[0.14em]">Technical details</span>
                          <ChevronDown size={16} className={cn("shrink-0 transition-transform", syncErrorTechnicalOpen ? "rotate-180" : "")} />
                        </Button>
                        {syncErrorTechnicalOpen ? (
                          <pre className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-xl border border-white/10 bg-black/35 p-2.5 font-mono text-[10px] leading-relaxed text-white/70">
                            {syncErrorParts.details}
                          </pre>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {progressDetail && !syncRunning && !shopifyEntityScope ? (
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

          <div className="mt-1 flex min-h-0 flex-1 flex-col overflow-hidden lg:min-h-[120px]">
            <nav className="min-h-0 flex-1 overflow-y-auto pr-0.5">
              <div className={cn("space-y-1", sidebarCollapsed ? "lg:space-y-0" : "")}>
                {navGroups.map(([group, groupItems], gi) => (
                  <div key={group}>
                    {!sidebarCollapsed ? (
                      <div className="px-3 pb-1 pt-2.5 text-[9px] font-semibold uppercase tracking-[0.22em] text-white/35">
                        {group}
                      </div>
                    ) : (
                      <div
                        className={cn(
                          "mx-1 my-2 hidden h-px bg-white/[0.08] lg:block",
                          gi === 0 && "lg:hidden"
                        )}
                      />
                    )}
                    <div className="space-y-0.5">
                      {groupItems.map((item) => {
                        const Icon = item.icon;
                        const count =
                          item.countKey && summary?.counts ? summary.counts[item.countKey] : undefined;
                        if (item.disabled) {
                          return (
                            <span
                              key={item.to}
                              className={cn(
                                "flex items-center gap-2.5 rounded-[10px] py-2.5 text-sm text-white/45",
                                sidebarCollapsed ? "justify-center px-0 lg:px-0" : "px-3"
                              )}
                            >
                              <Icon size={16} />
                              {!sidebarCollapsed ? item.label : null}
                              {!sidebarCollapsed ? (
                                <span className="ml-auto rounded-full bg-white/10 px-2 py-0.5 text-[10px] uppercase tracking-[0.18em]">
                                  Later
                                </span>
                              ) : null}
                            </span>
                          );
                        }
                        return (
                          <NavLink
                            key={item.to}
                            to={item.to}
                            end={item.to === "/"}
                            title={sidebarCollapsed ? item.label : undefined}
                            className={({ isActive }) =>
                              cn(
                                "relative flex items-center rounded-[10px] text-[13px] transition-colors duration-150",
                                sidebarCollapsed
                                  ? "justify-center px-0 py-2 lg:py-2.5"
                                  : "gap-2.5 px-3 py-2.5 text-left",
                                isActive ? "font-medium text-white" : "font-normal text-white/65 hover:text-white"
                              )
                            }
                          >
                            {({ isActive }) => (
                              <>
                                {isActive && !sidebarCollapsed ? (
                                  <>
                                    <span
                                      className="pointer-events-none absolute bottom-1.5 left-0 top-1.5 w-[3px] rounded-r-full"
                                      style={{ background: syncAccent }}
                                    />
                                    <span
                                      className="pointer-events-none absolute inset-0 rounded-[10px] bg-gradient-to-r to-transparent"
                                      style={{
                                        background: `linear-gradient(90deg, color-mix(in oklab, ${syncAccent} 22%, transparent) 0%, transparent 100%)`
                                      }}
                                    />
                                  </>
                                ) : null}
                                {isActive && sidebarCollapsed ? (
                                  <span
                                    className="pointer-events-none absolute inset-0.5 rounded-[10px]"
                                    style={{ background: `color-mix(in oklab, ${syncAccent} 33%, transparent)` }}
                                  />
                                ) : null}
                                <Icon
                                  size={16}
                                  className="relative z-[1] shrink-0"
                                  style={{ color: isActive ? syncAccent : undefined }}
                                  strokeWidth={isActive ? 2.25 : 2}
                                />
                                {!sidebarCollapsed ? (
                                  <>
                                    <span className="relative z-[1] min-w-0 flex-1 truncate">{item.label}</span>
                                    {item.badge ? (
                                      <span
                                        className="relative z-[1] shrink-0 rounded-md px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.06em] text-white"
                                        style={{ background: syncAccent }}
                                      >
                                        {item.badge}
                                      </span>
                                    ) : null}
                                    {count !== undefined && !item.badge ? (
                                      <span className="relative z-[1] shrink-0 font-mono text-[11px] tabular-nums text-white/40">
                                        {count}
                                      </span>
                                    ) : null}
                                  </>
                                ) : null}
                              </>
                            )}
                          </NavLink>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            </nav>
          </div>

          {!sidebarCollapsed ? (
            <div className="mt-2 flex shrink-0 items-center gap-2.5 rounded-xl border border-white/[0.06] bg-white/[0.04] p-2.5">
              <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white/[0.12] text-xs font-semibold text-white">
                {(shopBlock.initials[0] || "?").toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-xs font-medium text-white/90">{shopBlock.name}</div>
                <div className="truncate text-[10px] text-white/45">
                  {lastSyncShort(summary?.last_dashboard_sync_at)}
                </div>
              </div>
              <Button type="button" variant="ghost" size="icon" className="h-7 w-7 shrink-0 text-white/50 hover:bg-white/10 hover:text-white" asChild title="Settings">
                <NavLink to="/settings">
                  <HelpCircle size={15} strokeWidth={2} />
                </NavLink>
              </Button>
            </div>
          ) : (
            <div className="mt-1 hidden shrink-0 flex-col items-center gap-2 lg:flex">
              <NavLink
                to="/settings"
                title="Settings"
                className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/[0.12] text-xs font-semibold text-white hover:bg-white/[0.18]"
              >
                {(shopBlock.initials[0] || "?").toUpperCase()}
              </NavLink>
            </div>
          )}
        </aside>
        <main className="min-w-0">{children}</main>
      </div>
    </div>
    </SidekickProvider>
  );
}
