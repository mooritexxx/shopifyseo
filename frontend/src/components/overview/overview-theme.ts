import type { CSSProperties } from "react";

export const OVERVIEW_GSC_PERIOD_OPTIONS = [
  { value: "rolling_30d" as const, label: "Last 30 days" },
  { value: "since_2026_02_15" as const, label: "All time" }
] as const;

export const GSC_SEGMENT_OPTIONS = [
  { value: "all" as const, label: "All URLs" },
  { value: "products" as const, label: "Products" },
  { value: "collections" as const, label: "Collections" },
  { value: "pages" as const, label: "Pages" },
  { value: "blogs" as const, label: "Blogs" }
] as const;

export const CHART_TOOLTIP_STYLE: CSSProperties = {
  borderRadius: 12,
  border: "1px solid #e8e4f8",
  boxShadow: "0 8px 24px rgba(15,23,42,0.08)"
};

/** Metorik-adjacent accent: confident purple on analytics surfaces */
export const CHART_PRIMARY = "#5746d9";
export const CHART_GRID = "#e8e4f4";
export const GA4_CHART_SESSIONS = "#0891b2";
export const GA4_CHART_VIEWS = "#94a3b8";
export const CHART_META_COMPLETE = "#22c55e";
export const CHART_MISSING_META = "#f59e0b";
export const CHART_THIN_BODY = "#e879f9";

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  product: "Product",
  collection: "Collection",
  page: "Page",
  blog_article: "Article"
};

export const ENTITY_TYPE_COLORS: Record<string, string> = {
  product: "#5746d9",
  collection: "#0891b2",
  page: "#10b981",
  blog_article: "#f59e0b"
};

export function formatChartAxisDate(iso: string) {
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString("en-CA", { month: "short", day: "numeric" });
}

export function entityAppPath(entityType: string, handle: string): string {
  if (entityType === "product") return `/products/${handle}`;
  if (entityType === "collection") return `/collections/${handle}`;
  if (entityType === "page") return `/pages/${handle}`;
  if (entityType === "blog_article") {
    const [blog, ...rest] = handle.split("/");
    return `/articles/${blog}/${rest.join("/")}`;
  }
  return "/";
}
