import type { SyncQueueDetailItem } from "./sync-queue-table";

/** Narrow sync-status fields used for queue + throughput selection (keeps tests free of full z.infer). */
export type SyncQueueStatusInput = {
  active_scope?: string;
  pagespeed_queue_details?: SyncQueueDetailItem[];
  gsc_queue_details?: SyncQueueDetailItem[];
  ga4_queue_details?: SyncQueueDetailItem[];
  index_queue_details?: SyncQueueDetailItem[];
  shopify_queue_details?: SyncQueueDetailItem[];
  pagespeed_http_calls_last_60s?: number;
  gsc_sync_slots_last_60s?: number;
  ga4_sync_slots_last_60s?: number;
  index_sync_slots_last_60s?: number;
};

export type SyncQueueView = {
  queueItems: SyncQueueDetailItem[];
  queueTitle: string;
  throughputLast60s: number | null;
  throughputMetricTitle: string | undefined;
};

/** Scopes that show the live queue stream panel (single title for all). */
const QUEUE_STREAM_SCOPES = new Set(["pagespeed", "gsc", "ga4", "index", "shopify"]);

export function selectSyncQueueView(status: SyncQueueStatusInput | null | undefined): SyncQueueView {
  if (!status) {
    return { queueItems: [], queueTitle: "Sync queue", throughputLast60s: null, throughputMetricTitle: undefined };
  }
  const sc = (status.active_scope || "").toLowerCase();
  const queueTitle = QUEUE_STREAM_SCOPES.has(sc) ? "Queue Stream" : "Sync queue";

  let queueItems: SyncQueueDetailItem[] = [];
  if (sc === "gsc") queueItems = status.gsc_queue_details || [];
  else if (sc === "ga4") queueItems = status.ga4_queue_details || [];
  else if (sc === "index") queueItems = status.index_queue_details || [];
  else if (sc === "shopify") queueItems = status.shopify_queue_details || [];
  else if (sc === "pagespeed") queueItems = status.pagespeed_queue_details || [];

  let throughputLast60s: number | null = null;
  let throughputMetricTitle: string | undefined;
  if (sc === "pagespeed") {
    throughputMetricTitle = "PSI HTTP calls (60s)";
    throughputLast60s = status.pagespeed_http_calls_last_60s ?? 0;
  } else if (sc === "gsc") {
    throughputMetricTitle = "GSC rate slots (60s)";
    throughputLast60s = status.gsc_sync_slots_last_60s ?? 0;
  } else if (sc === "ga4") {
    throughputMetricTitle = "GA4 rate slots (60s)";
    throughputLast60s = status.ga4_sync_slots_last_60s ?? 0;
  } else if (sc === "index") {
    throughputMetricTitle = "Speed";
    throughputLast60s = status.index_sync_slots_last_60s ?? 0;
  }

  return { queueItems, queueTitle, throughputLast60s, throughputMetricTitle };
}
