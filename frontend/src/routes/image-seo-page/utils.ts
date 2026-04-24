import type { CatalogImageSeoRow } from "../../types/api";

export const PAGE_SIZE_OPTIONS = [50, 100, 500] as const;
export const BATCH_CONCURRENCY = 5;
/** Shown in the New column when a field matches the pre-optimize / Current row. */
export const COMPARISON_NO_CHANGE = "No change";

export type ImageSeoListSort = "handle" | "title" | "type" | "alt" | "status" | "optimize";

export function createPool(concurrency: number) {
  let active = 0;
  const queue: Array<() => void> = [];
  function next() {
    if (active >= concurrency || queue.length === 0) return;
    active++;
    const execute = queue.shift();
    if (!execute) return;
    execute();
  }
  return {
    run<T>(fn: () => Promise<T>): Promise<T> {
      return new Promise<T>((resolve, reject) => {
        const execute = () =>
          fn().then(resolve, reject).finally(() => { active--; next(); });
        queue.push(execute);
        next();
      });
    },
  };
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function formatPixelDimensions(
  w: number | null | undefined,
  h: number | null | undefined
): string {
  if (w != null && h != null && w > 0 && h > 0) return `${w}×${h}`;
  return "—";
}

export function formatImageFormatLabel(fmt: string | null | undefined): string {
  const t = (fmt ?? "").trim();
  return t || "—";
}

export function filenameFromUrl(url: string): string {
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/").filter(Boolean);
    const last = parts[parts.length - 1];
    if (!last) return "—";
    return decodeURIComponent(last.split("?")[0]) || "—";
  } catch {
    return "—";
  }
}

/** Human-readable image format from a filename or path (extension). */
export function imageFormatLabelFromFilename(nameOrUrl: string): string {
  const seg = nameOrUrl.split("/").pop() || nameOrUrl;
  const dot = seg.lastIndexOf(".");
  if (dot < 0 || dot >= seg.length - 1) return "—";
  const ext = seg
    .slice(dot + 1)
    .split("?")[0]
    .toLowerCase();
  const map: Record<string, string> = {
    jpg: "JPEG",
    jpeg: "JPEG",
    png: "PNG",
    webp: "WebP",
    gif: "GIF",
    avif: "AVIF",
    heic: "HEIC",
    svg: "SVG",
  };
  return map[ext] || ext.toUpperCase();
}

/** Positive percent = bytes saved vs original (smaller output). */
export function fileReductionPercent(originalBytes: number, newBytes: number): number | null {
  if (originalBytes <= 0 || newBytes >= originalBytes) return null;
  return Math.round(((originalBytes - newBytes) / originalBytes) * 100);
}

export const RESOURCE_TYPE_LABEL: Record<CatalogImageSeoRow["resource_type"], string> = {
  product: "Product",
  collection: "Collection",
  page: "Page",
  article: "Article",
};

export function resourceLink(row: CatalogImageSeoRow): { to: string; handleLine: string } {
  switch (row.resource_type) {
    case "product":
      return {
        to: `/products/${encodeURIComponent(row.product_handle)}`,
        handleLine: row.product_handle,
      };
    case "collection":
      return {
        to: `/collections/${encodeURIComponent(row.resource_handle)}`,
        handleLine: row.resource_handle,
      };
    case "page":
      return {
        to: `/pages/${encodeURIComponent(row.resource_handle)}`,
        handleLine: row.resource_handle,
      };
    case "article":
      return {
        to: `/articles/${encodeURIComponent(row.blog_handle)}/${encodeURIComponent(row.article_handle)}`,
        handleLine: `${row.blog_handle} · ${row.article_handle}`,
      };
    default:
      return { to: "/", handleLine: "" };
  }
}

export function isRowSeoOptimized(row: CatalogImageSeoRow): boolean {
  const f = row.flags;
  return !f.missing_or_weak_alt && !f.weak_filename && !f.seo_filename_mismatch && !f.not_webp;
}
