import type { z } from "zod";
import type { statusSchema } from "../../../types/api";
import type { SyncServiceValue } from "./constants";
import { syncServices } from "./constants";

type SyncStatusPayload = z.infer<typeof statusSchema>;

export type PipelineRowStatus = "queued" | "active" | "done" | "failed" | "off";

export type PipelineRowModel = {
  key: SyncServiceValue;
  label: string;
  status: PipelineRowStatus;
  pct: number;
  count: number;
  total: number;
};

export function scopeBelongsToShopifyService(scope: string): boolean {
  return (
    scope === "shopify" ||
    scope === "products" ||
    scope === "collections" ||
    scope === "pages" ||
    scope === "blogs"
  );
}

function serviceIndexInOrder(order: SyncServiceValue[], service: SyncServiceValue): number {
  return order.indexOf(service);
}

/** Map backend active_scope to pipeline service key. */
export function activeServiceKey(activeScope: string): SyncServiceValue | null {
  if (!activeScope) return null;
  if (scopeBelongsToShopifyService(activeScope)) return "shopify";
  const allowed = new Set(syncServices.map((s) => s.value));
  if (allowed.has(activeScope as SyncServiceValue)) return activeScope as SyncServiceValue;
  return null;
}

function countsForService(
  service: SyncServiceValue,
  s: SyncStatusPayload | undefined
): { done: number; total: number } {
  if (!s) return { done: 0, total: 0 };
  switch (service) {
    case "shopify":
      return {
        done:
          (s.products_synced || 0) +
          (s.collections_synced || 0) +
          (s.pages_synced || 0) +
          (s.blogs_synced || 0) +
          (s.blog_articles_synced || 0) +
          (s.images_synced || 0),
        total:
          (s.products_total || 0) +
          (s.collections_total || 0) +
          (s.pages_total || 0) +
          (s.blogs_total || 0) +
          (s.blog_articles_total || 0) +
          (s.images_total || 0)
      };
    case "gsc": {
      const done = (s.gsc_refreshed || 0) + (s.gsc_errors || 0);
      const total = Math.max(s.total || 0, 1);
      return { done: Math.min(done, total), total };
    }
    case "ga4": {
      const rows = s.ga4_rows || 0;
      const stage = (s.stage || "").toLowerCase();
      const scope = (s.active_scope || "").toLowerCase();
      if (s.running && (stage === "refreshing_ga4" || scope === "ga4")) {
        const total = Math.max(s.total || 0, 1);
        const done = Math.min(s.done || 0, total);
        return { done, total };
      }
      const refreshed = s.ga4_refreshed || 0;
      const urlErr = s.ga4_url_errors || 0;
      const doneFin = refreshed + urlErr;
      if (doneFin > 0) {
        return { done: doneFin, total: Math.max(doneFin, 1) };
      }
      if (rows > 0) {
        return { done: rows, total: rows };
      }
      return { done: 0, total: 0 };
    }
    case "index": {
      const done = (s.index_refreshed || 0) + (s.index_errors || 0);
      const total = Math.max(s.total || 0, 1);
      return { done: Math.min(done, total), total };
    }
    case "pagespeed": {
      const phase = (s.pagespeed_phase || "").toLowerCase();
      if (phase === "queueing" || (phase === "complete" && (s.pagespeed_queue_total || 0) > 0)) {
        const t = s.pagespeed_queue_total || 0;
        const d = s.pagespeed_queue_completed || 0;
        return { done: d, total: t };
      }
      if (phase === "complete") {
        return { done: 0, total: 0 };
      }
      const t = s.pagespeed_scan_total || s.total || 0;
      const d = s.pagespeed_scanned || s.done || 0;
      return { done: d, total: t };
    }
    case "structured":
      return { done: s.done || 0, total: s.total || 0 };
    default:
      return { done: s.done || 0, total: s.total || 0 };
  }
}

export function derivePipelineRows(args: {
  orderedScopes: SyncServiceValue[];
  syncStatus: SyncStatusPayload | undefined;
  running: boolean;
  hasError: boolean;
  syncPercent: number;
  activeScope: string;
  stepIndex: number;
}): PipelineRowModel[] {
  const { orderedScopes, syncStatus, running, hasError, syncPercent, activeScope, stepIndex } = args;
  const activeKey = activeServiceKey(activeScope);
  const failedIdx = hasError
    ? Math.max(
        0,
        activeKey !== null ? serviceIndexInOrder(orderedScopes, activeKey) : Math.min(Math.max(0, stepIndex - 1), orderedScopes.length - 1)
      )
    : -1;

  return orderedScopes.map((key, i) => {
    const label = syncServices.find((s) => s.value === key)?.label || key;
    const { done, total } = countsForService(key, syncStatus);

    if (!running && !hasError && syncStatus?.stage === "complete") {
      const fin = countsForService(key, syncStatus);
      return {
        key,
        label,
        status: "done" as const,
        pct: 100,
        count: fin.total > 0 ? fin.done : fin.done || 0,
        total: fin.total || fin.done || 0
      };
    }

    if (hasError && !running) {
      if (i < failedIdx) {
        const fin = countsForService(key, syncStatus);
        return {
          key,
          label,
          status: "done",
          pct: 100,
          count: fin.done || fin.total,
          total: fin.total || fin.done || 0
        };
      }
      if (i === failedIdx) {
        return { key, label, status: "failed", pct: 0, count: 0, total: total || 0 };
      }
      return { key, label, status: "queued", pct: 0, count: 0, total: 0 };
    }

    if (!running && !hasError) {
      return { key, label, status: "queued", pct: 0, count: 0, total: 0 };
    }

    // running
    if (activeKey === null) {
      return { key, label, status: i === 0 ? "active" : "queued", pct: i === 0 ? syncPercent : 0, count: done, total };
    }
    const activeI = serviceIndexInOrder(orderedScopes, activeKey);
    if (i < activeI) {
      const fin = countsForService(key, syncStatus);
      return {
        key,
        label,
        status: "done",
        pct: 100,
        count: fin.done || fin.total,
        total: fin.total || fin.done || 0
      };
    }
    if (i === activeI) {
      return {
        key,
        label,
        status: "active",
        pct: syncPercent,
        count: done,
        total: total || 0
      };
    }
    return { key, label, status: "queued", pct: 0, count: 0, total: 0 };
  });
}
