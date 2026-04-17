export const syncServices = [
  { value: "shopify", label: "Shopify" },
  { value: "gsc", label: "Search Console" },
  { value: "ga4", label: "GA4" },
  { value: "index", label: "Index status" },
  { value: "pagespeed", label: "PageSpeed" },
  { value: "structured", label: "Structured SEO" }
] as const;

export type SyncServiceValue = (typeof syncServices)[number]["value"];

/**
 * Canonical sidebar order (must match backend `SYNC_PIPELINE_ORDER`).
 * Use for React state so selection order never reflects click order.
 */
export function syncSortScopesInPipelineOrder(values: readonly string[]): SyncServiceValue[] {
  const picked = new Set(values);
  return syncServices.map((s) => s.value).filter((v) => picked.has(v)) as SyncServiceValue[];
}

export const SYNC_SCOPE_READY_HELP: Record<SyncServiceValue, string> = {
  shopify: "Add your Shopify shop and Admin API credentials under Settings → Data sources, then save.",
  gsc: "Configure Google OAuth in Settings → Data sources, then pick a Search Console property.",
  ga4: "Connect Google OAuth (same as Search Console) before syncing GA4.",
  index: "URL Inspection needs a connected Google account with Search Console access.",
  pagespeed: "PageSpeed Insights sync uses your Google OAuth session.",
  structured: "Configure Shopify first — structured SEO runs against your synced catalog."
};

/** Pipeline row subtitles (V1 design copy deck). */
export const SYNC_PIPELINE_SUBTITLE: Record<SyncServiceValue, string> = {
  shopify: "Products, collections, pages, blogs",
  gsc: "Impressions, clicks, coverage",
  ga4: "Sessions, views, acquisition",
  index: "URL inspection on tracked catalog",
  pagespeed: "Core Web Vitals per URL",
  structured: "JSON-LD & schema completion"
};

export function syncSelectionSummary(selectedScopes: string[]) {
  const ordered = syncSortScopesInPipelineOrder(selectedScopes);
  if (!ordered.length) return "No services selected";
  if (ordered.length === syncServices.length) return "All services";
  return ordered
    .map((value) => syncServices.find((item) => item.value === value)?.label || value)
    .join(" · ");
}
