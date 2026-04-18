import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Box,
  Check,
  ChevronLeft,
  ChevronRight,
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
  X
} from "lucide-react";
import { SidekickProvider } from "../sidekick/sidekick-context";
import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink } from "react-router-dom";
import type { PropsWithChildren } from "react";
import type { LucideIcon } from "lucide-react";
import { z } from "zod";

import { Button } from "../ui/button";
import { readStoredOverviewGscPeriod } from "../../lib/gsc-period";
import { cn, formatRelativeTimestamp } from "../../lib/utils";
import { getJson, postJson } from "../../lib/api";
import { settingsSchema, statusSchema, summarySchema } from "../../types/api";
import { SyncDrawer, type SyncDrawerMode } from "./sync/sync-drawer";
import { SyncPill } from "./sync/sync-pill";
import {
  SYNC_PIPELINE_SUBTITLE,
  SYNC_SCOPE_READY_HELP,
  syncSelectionSummary,
  syncServices,
  syncSortScopesInPipelineOrder,
  type SyncServiceValue
} from "./sync/constants";
import { activeServiceKey, derivePipelineRows, scopeBelongsToShopifyService } from "./sync/pipeline-derive";
import { useSyncEventLog } from "./sync/use-sync-event-log";
import { useSmoothSyncEta } from "../../hooks/use-smooth-sync-eta";

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

const syncStageLabels: Record<string, string> = {
  idle: "Idle",
  starting: "Starting sync",
  syncing_shopify: "Shopify sync",
  syncing_products: "Products sync",
  syncing_collections: "Collections sync",
  syncing_pages: "Pages sync",
  syncing_blogs: "Blogs sync",
  /** Legacy backend stage; treat as unified Shopify run */
  syncing_product_images: "Shopify sync",
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

/** Formats remaining ETA from server-provided whole seconds (sync drawer). */
function formatEtaCountdown(totalSeconds: number) {
  const s = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(s / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (hours > 0) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

function titleCaseLabel(value: string) {
  return value
    .split(/[_-]/g)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function syncStageLabel(stage?: string, scope?: string) {
  if (stage && syncStageLabels[stage]) return syncStageLabels[stage];
  if (!scope) return "Sync status";
  return scope === "custom" ? "Custom sync" : "Sync status";
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
  { to: "/api-usage", label: "API Usage", icon: Activity, group: "System" }
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
  const [selectedScopes, setSelectedScopes] = useState<SyncServiceValue[]>(
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
        return normalized.length ? syncSortScopesInPipelineOrder(normalized) : syncServices.map((item) => item.value);
      } catch {
        return syncServices.map((item) => item.value);
      }
    }
  );
  const [forceRefresh, setForceRefresh] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("seo-sync-force-refresh") === "true";
  });
  const [message, setMessage] = useState<string | null>(null);
  const [syncSummaryDismissed, setSyncSummaryDismissed] = useState(false);
  const [syncErrorTechnicalOpen, setSyncErrorTechnicalOpen] = useState(false);
  const [syncErrorCopied, setSyncErrorCopied] = useState(false);
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());
  const [syncDrawerOpen, setSyncDrawerOpen] = useState(false);
  const userDrawerDismissedRef = useRef(false);
  const errStreamPushRef = useRef("");
  const prevSyncRunningForDrawerRef = useRef(false);
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
  const { lines: eventLogLines, pushLine, clear: clearEventLog } = useSyncEventLog(
    syncStatus?.current,
    syncStatus?.active_scope || "",
    Boolean(syncStatus?.running)
  );
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

  function scopeServiceReady(value: SyncServiceValue): boolean {
    if (!syncScopeReady) return true;
    return Boolean(syncScopeReady[value]);
  }
  const syncRunning = Boolean(syncStatus?.running);
  const startSyncMutation = useMutation({
    mutationFn: () => {
      const scopesInPipelineOrder = syncSortScopesInPipelineOrder(selectedScopes);
      return postJson("/api/sync", statusSchema, {
        scope: scopesInPipelineOrder.length === syncServices.length ? "all" : "custom",
        selected_scopes: scopesInPipelineOrder,
        force_refresh: forceRefresh
      });
    },
    onMutate: () => {
      clearEventLog();
      userDrawerDismissedRef.current = false;
    },
    onSuccess: (state) => {
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
  const rawSyncError = (syncStatus?.last_error || "").trim();
  const syncErrorParts = useMemo(() => splitSyncError(rawSyncError), [rawSyncError]);
  const showSyncErrorPanel = !syncRunning && Boolean(rawSyncError);
  const pagespeedErrorDetails = syncStatus?.pagespeed_error_details || [];

  const syncAccent = "oklch(0.62 0.18 262)";

  const smoothEtaSeconds = useSmoothSyncEta(
    syncRunning,
    syncStatus?.eta_seconds,
    syncStatus?.stage,
    syncStatus?.active_scope
  );

  const drawerMode: SyncDrawerMode = useMemo(() => {
    if (syncRunning) return "running";
    if (showSyncErrorPanel) return "error";
    if (!syncSummaryDismissed && (syncStatus?.stage === "complete" || syncStatus?.stage === "cancelled")) return "done";
    return "idle";
  }, [syncRunning, showSyncErrorPanel, syncSummaryDismissed, syncStatus?.stage]);

  const orderedScopes = useMemo(() => {
    const picked = activeSelectedScopes.filter(Boolean);
    return syncServices.map((s) => s.value).filter((v) => picked.includes(v)) as SyncServiceValue[];
  }, [activeSelectedScopes]);

  const pipelineRows = useMemo(() => {
    const order = (orderedScopes.length ? orderedScopes : selectedScopes) as SyncServiceValue[];
    return derivePipelineRows({
      orderedScopes: order,
      syncStatus,
      running: syncRunning,
      hasError: showSyncErrorPanel,
      syncPercent,
      activeScope: effectiveActiveScope,
      stepIndex: activeStepIndex
    });
  }, [
    orderedScopes,
    selectedScopes,
    syncStatus,
    syncRunning,
    showSyncErrorPanel,
    syncPercent,
    effectiveActiveScope,
    activeStepIndex
  ]);

  const shopifyBreakdown = useMemo(() => {
    if (!syncRunning || !syncStatus) return undefined;
    const scope = (effectiveActiveScope || "").toLowerCase();
    const stage = (syncStatus.stage || "").toLowerCase();
    const shopifyPhase =
      scopeBelongsToShopifyService(scope) ||
      stage === "syncing_shopify" ||
      stage === "syncing_products" ||
      stage === "syncing_collections" ||
      stage === "syncing_pages" ||
      stage === "syncing_blogs";
    if (!shopifyPhase) return undefined;
    return [
      { label: "Products", synced: syncStatus.products_synced ?? 0, total: syncStatus.products_total ?? 0 },
      { label: "Collections", synced: syncStatus.collections_synced ?? 0, total: syncStatus.collections_total ?? 0 },
      { label: "Pages", synced: syncStatus.pages_synced ?? 0, total: syncStatus.pages_total ?? 0 },
      { label: "Blogs", synced: syncStatus.blogs_synced ?? 0, total: syncStatus.blogs_total ?? 0 },
      { label: "Articles", synced: syncStatus.blog_articles_synced ?? 0, total: syncStatus.blog_articles_total ?? 0 },
      { label: "Images", synced: syncStatus.images_synced ?? 0, total: syncStatus.images_total ?? 0 }
    ];
  }, [syncRunning, syncStatus, effectiveActiveScope]);

  const pipelineFraction = useMemo(() => {
    const n = Math.max(orderedScopes.length || selectedScopes.length, 1);
    if (syncRunning) {
      const ai = pipelineRows.findIndex((r) => r.status === "active");
      return `${ai >= 0 ? ai + 1 : Math.min(Math.max(activeStepIndex, 1), n)}/${n}`;
    }
    if (showSyncErrorPanel) {
      const fi = pipelineRows.findIndex((r) => r.status === "failed");
      return `${fi >= 0 ? fi + 1 : 1}/${n}`;
    }
    if (syncStatus?.stage === "complete" || syncStatus?.stage === "cancelled") return `${n}/${n}`;
    return `0/${n}`;
  }, [
    orderedScopes.length,
    selectedScopes.length,
    syncRunning,
    showSyncErrorPanel,
    syncStatus?.stage,
    pipelineRows,
    activeStepIndex
  ]);

  const changeCards = useMemo(() => {
    const c = summary?.counts;
    if (!c) {
      return [
        { label: "Products", total: 0 },
        { label: "Collections", total: 0 },
        { label: "Pages", total: 0 },
        { label: "Blogs", total: 0 }
      ];
    }
    return [
      { label: "Products", total: c.products },
      { label: "Collections", total: c.collections },
      { label: "Pages", total: c.pages },
      { label: "Blogs", total: c.blogs }
    ];
  }, [summary?.counts]);

  const activePipelineKey = activeServiceKey(effectiveActiveScope);
  const runningHeroSubtitle =
    activePipelineKey && SYNC_PIPELINE_SUBTITLE[activePipelineKey]
      ? SYNC_PIPELINE_SUBTITLE[activePipelineKey]
      : activeStageLabel;

  const canRunSync =
    !syncRunning &&
    !startSyncMutation.isPending &&
    selectedScopes.length > 0 &&
    !selectedScopes.some((s) => !scopeServiceReady(s));

  const closeDrawerOnly = () => {
    userDrawerDismissedRef.current = true;
    setSyncDrawerOpen(false);
  };

  const closeDrawer = () => {
    closeDrawerOnly();
    if (syncStatus?.stage === "complete" || syncStatus?.stage === "cancelled") {
      setSyncSummaryDismissed(true);
      setMessage(null);
    }
  };

  const pillTitle = syncRunning
    ? "Syncing…"
    : showSyncErrorPanel
      ? "Sync failed"
      : drawerMode === "done"
        ? "Up to date"
        : "Ready to sync";

  const stepHintTotal = Math.max(activeStepTotal || orderedScopes.length || selectedScopes.length, 1);
  const pillSubtitle = syncRunning
    ? `${activeSelectedScopes.length} services · live`
    : showSyncErrorPanel
      ? `Step ${Math.max(activeStepIndex, 1)} of ${stepHintTotal}`
      : lastSyncShort(summary?.last_dashboard_sync_at);

  const [mqLg, setMqLg] = useState(true);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const fn = () => setMqLg(mq.matches);
    fn();
    mq.addEventListener("change", fn);
    return () => mq.removeEventListener("change", fn);
  }, []);

  const drawerProps = {
    accent: syncAccent,
    mode: drawerMode,
    onClose: closeDrawer,
    headerKicker:
      drawerMode === "running"
        ? "Syncing your store"
        : drawerMode === "done"
          ? syncStatus?.stage === "cancelled"
            ? "Sync cancelled"
            : "Sync complete"
          : drawerMode === "error"
            ? "Sync stopped"
            : "Sync ready",
    headerBadge:
      drawerMode === "running" ? "Running" : drawerMode === "done" ? "Done" : drawerMode === "error" ? "Failed" : "Idle",
    headerBadgeClass:
      drawerMode === "running"
        ? "bg-[color-mix(in_oklab,oklch(0.62_0.18_262)_20%,transparent)] text-[oklch(0.62_0.18_262)]"
        : drawerMode === "done"
          ? "bg-[rgba(145,239,187,0.12)] text-[#91efbb]"
          : drawerMode === "error"
            ? "bg-[rgba(234,96,117,0.16)] text-[#ea6075]"
            : "bg-white/[0.06] text-white/40",
    headerDotColor:
      drawerMode === "running"
        ? syncAccent
        : drawerMode === "done"
          ? "#91efbb"
          : drawerMode === "error"
            ? "#ea6075"
            : "rgba(255,255,255,0.35)",
    headerDotPulse: drawerMode === "running",
    runningHero:
      drawerMode === "running"
        ? {
            pct: syncPercent,
            title: activeStageLabel,
            subtitle: runningHeroSubtitle,
            elapsed: elapsedLabel || "00:00",
            eta:
              smoothEtaSeconds != null
                ? smoothEtaSeconds === 0 &&
                    syncRunning &&
                    syncStatus?.eta_seconds != null &&
                    syncStatus.eta_seconds > 0
                  ? "Finishing…"
                  : formatEtaCountdown(smoothEtaSeconds)
                : "--:--"
          }
        : undefined,
    shopifyBreakdown,
    doneHero:
      drawerMode === "done"
        ? {
            title: syncStatus?.stage === "cancelled" ? "Sync cancelled" : "All services up to date",
            subtitle: `${Math.max(orderedScopes.length, activeSelectedScopes.length)} services finished`,
            finishedIn: `${(elapsedMs / 1000).toFixed(1)}s`,
            relative: syncFinishedAt ? formatRelativeTimestamp(syncFinishedAt) : "—"
          }
        : undefined,
    errorHero:
      drawerMode === "error"
        ? {
            title: syncErrorParts.summary || "Sync failed",
            subtitle: "Fix the issue and retry, or open Settings.",
            codeLine: `step ${Math.max(activeStepIndex, 1)}/${Math.max(activeStepTotal || stepHintTotal, 1)}`
          }
        : undefined,
    idleHero:
      drawerMode === "idle"
        ? {
            lastRunLine: `${lastSyncShort(summary?.last_dashboard_sync_at)} · ${selectedScopes.length} service${selectedScopes.length === 1 ? "" : "s"} configured`,
            serviceCount: selectedScopes.length
          }
        : undefined,
    pipelineRows,
    pipelineFraction,
    showEventStream: syncRunning || showSyncErrorPanel,
    eventLines: eventLogLines,
    showChangesGrid: drawerMode === "done",
    changeCards,
    pagespeedErrorDetails,
    rawSyncError,
    errorSummary: syncErrorParts.summary || "",
    errorDetails: syncErrorParts.details,
    syncErrorTechnicalOpen,
    setSyncErrorTechnicalOpen,
    syncErrorCopied,
    onCopyError: () => {
      void navigator.clipboard.writeText(rawSyncError).then(() => {
        setSyncErrorCopied(true);
        window.setTimeout(() => setSyncErrorCopied(false), 2000);
      });
    },
    errorSuggestsSettings: syncErrorSuggestsSettings(rawSyncError),
    selectedScopes,
    onToggleScope: (v: SyncServiceValue) => {
      if (syncRunning) return;
      setSelectedScopes((cur) => {
        const raw = cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v];
        return syncSortScopesInPipelineOrder(raw);
      });
    },
    scopeServiceReady,
    scopeHelp: (v: SyncServiceValue) => SYNC_SCOPE_READY_HELP[v],
    forceRefresh,
    onForceRefresh: setForceRefresh,
    syncRunning,
    onRunSync: () => startSyncMutation.mutate(),
    canRunSync,
    runPending: startSyncMutation.isPending,
    onStopSync: () => stopSyncMutation.mutate(),
    stopPending: stopSyncMutation.isPending,
    onRunBackground: closeDrawerOnly,
    onRunAgain: () => startSyncMutation.mutate(),
    onRetrySync: () => startSyncMutation.mutate(),
    cancelRequested: Boolean(syncStatus?.cancel_requested)
  };

  useEffect(() => {
    window.localStorage.setItem("seo-sync-services", JSON.stringify(selectedScopes));
  }, [selectedScopes]);

  /** One-time: legacy sessions may have stored scopes in click order. */
  useEffect(() => {
    setSelectedScopes((prev) => {
      const sorted = syncSortScopesInPipelineOrder(prev);
      if (sorted.length === prev.length && sorted.every((v, i) => v === prev[i])) return prev;
      return sorted;
    });
  }, []);

  const syncReadyKey = syncScopeReady ? JSON.stringify(syncScopeReady) : "";
  useEffect(() => {
    if (!syncScopeReady) return;
    setSelectedScopes((prev) => {
      const next = prev.filter((s) => syncScopeReady[s as keyof typeof syncScopeReady]);
      const ordered = syncSortScopesInPipelineOrder(next);
      if (ordered.length) {
        if (ordered.length === prev.length && ordered.every((v, i) => v === prev[i])) return prev;
        return ordered;
      }
      const fallback = syncServices.map((item) => item.value).filter((s) => syncScopeReady[s as keyof typeof syncScopeReady]);
      const fbOrdered = fallback.length ? syncSortScopesInPipelineOrder(fallback) : [];
      return fbOrdered;
    });
  }, [syncReadyKey]);

  useEffect(() => {
    window.localStorage.setItem("seo-sync-force-refresh", String(forceRefresh));
  }, [forceRefresh]);

  useEffect(() => {
    if (syncRunning) {
      setSyncSummaryDismissed(false);
    }
  }, [syncRunning]);

  useEffect(() => {
    if (syncRunning) {
      userDrawerDismissedRef.current = false;
      setSyncDrawerOpen(true);
    }
  }, [syncRunning]);

  useEffect(() => {
    const was = prevSyncRunningForDrawerRef.current;
    prevSyncRunningForDrawerRef.current = syncRunning;
    if (was && !syncRunning) {
      userDrawerDismissedRef.current = false;
      setSyncDrawerOpen(true);
    }
  }, [syncRunning]);

  useEffect(() => {
    if (!syncRunning && rawSyncError && !syncSummaryDismissed && !userDrawerDismissedRef.current) {
      setSyncDrawerOpen(true);
    }
  }, [rawSyncError, syncRunning, syncSummaryDismissed]);

  useEffect(() => {
    if (
      !syncRunning &&
      !syncSummaryDismissed &&
      (syncStatus?.stage === "complete" || syncStatus?.stage === "cancelled") &&
      !userDrawerDismissedRef.current
    ) {
      setSyncDrawerOpen(true);
    }
  }, [syncRunning, syncSummaryDismissed, syncStatus?.stage]);

  useEffect(() => {
    if (!syncRunning && rawSyncError && rawSyncError !== errStreamPushRef.current) {
      errStreamPushRef.current = rawSyncError;
      pushLine("error", splitSyncError(rawSyncError).summary.slice(0, 200));
    }
    if (syncRunning) {
      errStreamPushRef.current = "";
    }
  }, [syncRunning, rawSyncError, pushLine]);

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
          "mx-0 grid min-h-screen w-full max-w-none grid-cols-1 gap-4 px-4 py-4 lg:gap-0 lg:px-0 lg:py-0",
          syncDrawerOpen
            ? sidebarCollapsed
              ? "lg:grid-cols-[72px_380px_minmax(0,1fr)]"
              : "lg:grid-cols-[260px_380px_minmax(0,1fr)]"
            : sidebarCollapsed
              ? "lg:grid-cols-[72px_minmax(0,1fr)]"
              : "lg:grid-cols-[260px_minmax(0,1fr)]"
        )}
      >
        <aside
          className={cn(
            "flex w-full flex-col gap-3 rounded-[24px] border border-white/70 bg-[#0d172b] text-white shadow-[0_20px_60px_-30px_rgba(13,23,43,0.55)] transition-[padding,gap] duration-200 ease-out",
            "max-lg:rounded-[30px] max-lg:p-5",
            sidebarCollapsed ? "lg:gap-2 lg:p-2.5 lg:py-3" : "lg:p-4",
            "lg:z-10 lg:max-h-none lg:h-[100dvh] lg:min-h-0 lg:rounded-none lg:border-0 lg:border-r lg:border-r-white/[0.1] lg:shadow-none lg:self-start lg:sticky lg:top-0 lg:overflow-x-hidden lg:overflow-y-hidden"
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

          {!sidebarCollapsed ? (
            <div id="app-sync-panel" className="scroll-mt-24 shrink-0">
              <SyncPill
                drawerOpen={syncDrawerOpen}
                onToggle={() => {
                  userDrawerDismissedRef.current = false;
                  setSyncDrawerOpen((o) => !o);
                }}
                running={syncRunning}
                hasError={showSyncErrorPanel}
                doneVisible={drawerMode === "done"}
                accent={syncAccent}
                title={pillTitle}
                subtitle={pillSubtitle}
              />
            </div>
          ) : null}

          {sidebarCollapsed ? (
            <div className="relative hidden shrink-0 lg:block">
              <button
                type="button"
                title="Open sync panel"
                aria-label="Open sync panel"
                onClick={() => {
                  userDrawerDismissedRef.current = false;
                  setSyncDrawerOpen(true);
                }}
                className={cn(
                  "flex h-10 w-full items-center justify-center rounded-xl border-0 text-white shadow-md",
                  syncRunning ? "bg-white/[0.05]" : showSyncErrorPanel ? "bg-[rgba(234,96,117,0.18)] text-[#ea6075]" : "bg-[oklch(0.62_0.18_262)] hover:opacity-95"
                )}
              >
                {syncRunning ? (
                  <LoaderCircle className="animate-spin" size={16} />
                ) : (
                  <RefreshCw size={16} strokeWidth={2.25} />
                )}
              </button>
              {hasSyncCard && !syncRunning ? (
                <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-amber-400 shadow-[0_0_0_2px_#0d172b]" />
              ) : null}
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
                  <Settings2 size={15} strokeWidth={2} />
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
        {syncDrawerOpen && mqLg ? (
          <div className="hidden h-[100dvh] min-h-0 lg:flex lg:w-[380px] lg:shrink-0 lg:self-start lg:sticky lg:top-0 lg:overflow-hidden">
            <SyncDrawer {...drawerProps} />
          </div>
        ) : null}
        <main className="min-w-0 max-lg:min-h-0 lg:min-h-screen lg:p-6">{children}</main>
      </div>
      {syncDrawerOpen && !mqLg ? (
        <>
          <button
            type="button"
            className="fixed inset-0 z-40 bg-black/45 lg:hidden"
            aria-label="Close sync panel"
            onClick={closeDrawerOnly}
          />
          <div className="fixed right-4 top-4 z-50 max-h-[calc(100vh-2rem)] w-[min(380px,calc(100vw-2rem))] overflow-hidden lg:hidden">
            <SyncDrawer {...drawerProps} />
          </div>
        </>
      ) : null}
    </div>
    </SidekickProvider>
  );
}
