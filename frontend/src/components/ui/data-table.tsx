import "./data-table.css";
import { AlertTriangle, ArrowUpDown, Check, Eye } from "lucide-react";
import { memo } from "react";
import { Link } from "react-router-dom";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "./table";
import { TrendCell } from "./trend-cell";
import type { Trend } from "../../types/api";

export const listTableNameLinkClassName = "text-[13px]";

export type Column = {
  key: string;
  label: string;
  align: "left" | "right" | "center";
  sortable?: boolean;
  widthClass?: string;
};

function columnButtonClass(align: "left" | "right" | "center") {
  const base =
    "inline-flex max-w-full min-w-0 flex-wrap items-center gap-0 text-[11px] font-medium text-slate-500 transition hover:text-ink";
  if (align === "center") {
    return `${base} mx-auto justify-center`;
  }
  return base;
}

function isContentComplete(row: { seo_title?: string; seo_description?: string; body_length?: number }) {
  const hasMetaTitle = (row.seo_title ?? "").trim().length > 0;
  const hasMetaDescription = (row.seo_description ?? "").trim().length > 0;
  const hasBody = (row.body_length ?? 0) > 0;
  return hasMetaTitle && hasMetaDescription && hasBody;
}

function formatCellValue(value: unknown, key: string): string {
  if (value === null || value === undefined) return "—";
  if (key.includes("gsc_ctr") || key.includes("ctr")) {
    return formatPercent(Number(value));
  }
  if (typeof value === "number") {
    return formatNumber(value);
  }
  return String(value);
}

/**
 * One table row, memoized on plain data props.
 *
 * Row links arrive as resolved strings rather than callbacks so that the inline
 * arrow functions the list pages pass to `DataTable` cannot invalidate the memo
 * on every render. Reordering the list (client-side sorting) then reuses these
 * rows: React moves the existing DOM nodes and skips re-rendering ~12 cells
 * apiece, instead of rebuilding every cell in the table.
 */
const DataTableRow = memo(function DataTableRow({
  columns,
  row,
  rowLink,
  rowExternalLink,
  rowExternalLinkTitle,
  nameLinkClassName
}: {
  columns: Column[];
  row: Record<string, unknown>;
  rowLink: string;
  rowExternalLink?: string;
  rowExternalLinkTitle?: string;
  nameLinkClassName?: string;
}) {
  return (
    <TableRow className="border-b border-[#e8eef6] align-middle last:border-b-0">
      {columns.map((column) => {
        const cellPadding = "px-3";
        if (column.key === "title" || column.key === "article_name") {
          const label =
            column.key === "article_name"
              ? String(row.article_name ?? row.title ?? "")
              : String(row.title || "");
          return (
            <TableCell
              key={column.key}
              className={cn(
                "catalog-identity-cell border-b border-[#e8eef6] bg-white py-3 text-left min-w-0",
                cellPadding,
                column.widthClass
              )}
            >
              <div className="flex min-w-0 max-w-full items-center gap-2">
                <Link
                  className={cn(
                    "block min-w-0 line-clamp-2 font-medium text-ink transition hover:text-ocean",
                    nameLinkClassName ?? "text-[13px]"
                  )}
                  title={label}
                  to={rowLink}
                >
                  {label}
                </Link>
                {rowExternalLink ? (
                  <a
                    className="shrink-0 text-slate-400 transition hover:text-ocean"
                    href={rowExternalLink}
                    rel="noreferrer"
                    target="_blank"
                    title={rowExternalLinkTitle ?? "Open live page"}
                  >
                    <Eye size={14} />
                  </a>
                ) : null}
              </div>
            </TableCell>
          );
        }
        if (column.key === "content_status") {
          return (
            <TableCell key={column.key} className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-3 text-center min-w-0`} title={isContentComplete(row as { seo_title?: string; seo_description?: string; body_length?: number }) ? "Meta title, meta description, and body are filled" : "Meta title, meta description, or body is missing"}>
              {isContentComplete(row as { seo_title?: string; seo_description?: string; body_length?: number }) ? (
                <Check size={18} className="inline-block text-[#1c7a4b]" aria-label="Content complete" />
              ) : (
                <AlertTriangle size={18} className="inline-block text-[#b34747]" aria-label="Content incomplete" />
              )}
            </TableCell>
          );
        }
        if (column.key === "published_label") {
          const live =
            row.is_published === true ||
            String(row.published_label ?? "")
              .trim()
              .toLowerCase() === "yes";
          return (
            <TableCell
              key={column.key}
              className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-3 text-center min-w-0`}
              title={live ? "Published" : "Not published"}
            >
              {live ? (
                <Check size={18} className="inline-block text-[#1c7a4b]" aria-label="Published" />
              ) : (
                <span className="text-slate-400">—</span>
              )}
            </TableCell>
          );
        }
        if (column.key === "index_last_crawl_at") {
          const raw = row.index_last_crawl_at ? String(row.index_last_crawl_at) : "";
          const date = raw ? new Date(raw) : null;
          const label = date && !Number.isNaN(date.getTime())
            ? new Intl.DateTimeFormat("en-CA", { month: "short", day: "numeric", timeZone: "America/Vancouver" }).format(date)
            : "—";
          return <TableCell key={column.key} className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-3 text-center text-xs`} title={String(row.index_flag_reason || raw || "No Google crawl recorded")}>{label}</TableCell>;
        }
        if (column.key === "index_status") {
          const raw = String(row.index_status ?? "").trim() || "Unknown";
          const indexed = raw.toLowerCase() === "indexed";
          return (
            <TableCell
              key={column.key}
              className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-3 text-center min-w-0`}
              title={raw}
            >
              {indexed ? (
                <Check
                  size={18}
                  className="inline-block text-[#1c7a4b]"
                  aria-label={`Indexed (${raw})`}
                />
              ) : (
                <AlertTriangle
                  size={18}
                  className="inline-block text-[#b34747]"
                  aria-label={`Not indexed (${raw})`}
                />
              )}
            </TableCell>
          );
        }
        if (column.key === "gsc_segments") {
          const flags = row.gsc_segment_flags as { has_dimensional?: boolean } | undefined;
          const on = Boolean(flags?.has_dimensional);
          return (
            <TableCell
              key={column.key}
              className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-3 text-center text-[13px] text-slate-600 min-w-0`}
              title={
                on
                  ? "Query×segment GSC rows in cache (country, device, search appearance)"
                  : "No dimensional GSC rows cached for this URL yet"
              }
            >
              {on ? (
                <Check size={18} className="inline-block text-[#1c7a4b]" aria-label="Segments available" />
              ) : (
                <span className="text-slate-400">—</span>
              )}
            </TableCell>
          );
        }
        if (column.key === "gsc_clicks_delta") {
          const trend = row.trend as Trend | undefined;
          const pct = trend?.clicks_delta_pct ?? null;
          return (
            <TableCell
              key={column.key}
              className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-3 text-center min-w-0`}
              title={
                trend
                  ? `${trend.clicks_current} clicks in the last 30 days vs ${trend.clicks_previous} in the 30 before` +
                    (pct === null ? " (no prior data to compare)" : "")
                  : "No Search Console history stored for this page yet"
              }
            >
              <TrendCell trend={trend} label={String(row.title ?? row.handle ?? "")} />
            </TableCell>
          );
        }
        const cellAlign =
          column.align === "right"
            ? "text-right"
            : column.align === "center"
              ? "text-center"
              : "text-left";
        const numericCell = column.key === "article_count" || column.key.endsWith("_count");
        const longTextCell = column.key === "seo_title" || column.key === "body_preview";
        return (
          <TableCell
            key={column.key}
            className={cn(
              "border-b border-[#e8eef6] bg-white py-3 text-[13px] text-slate-600 min-w-0",
              cellPadding,
              cellAlign,
              column.widthClass
            )}
          >
            <span
              className={cn(
                "block min-w-0 max-w-full font-semibold text-[13px] text-ink",
                numericCell ? "tabular-nums" : "",
                longTextCell
                  ? "whitespace-normal break-words [overflow-wrap:anywhere] line-clamp-2"
                  : !numericCell
                    ? "truncate"
                    : ""
              )}
              title={longTextCell ? String(row[column.key] ?? "") : undefined}
            >
              {formatCellValue(row[column.key], column.key)}
            </span>
          </TableCell>
        );
      })}
    </TableRow>
  );
});

export function DataTable({
  columns,
  rows,
  sort,
  direction,
  onSortChange,
  getRowLink,
  getRowExternalLink,
  getRowExternalLinkTitle,
  nameLinkClassName,
  isLoading,
  error,
  tableLayout = "fixed"
}: {
  columns: Column[];
  rows: Array<Record<string, unknown>>;
  sort: string;
  direction: "asc" | "desc";
  onSortChange: (key: string) => void;
  getRowLink: (row: Record<string, unknown>) => string;
  getRowExternalLink?: (row: Record<string, unknown>) => string;
  getRowExternalLinkTitle?: (row: Record<string, unknown>) => string;
  nameLinkClassName?: string;
  isLoading?: boolean;
  error?: Error | null;
  tableLayout?: "auto" | "fixed";
}) {
  return (
    <div className={cn("catalog-table", columns.length > 8 ? "catalog-table-dense" : "catalog-table-simple")}>
      <p className="catalog-scroll-hint">Scroll horizontally to see all columns</p>
      <div className="catalog-table-scroll" role="region" aria-label="Catalog results" tabIndex={0}>
      <table
        className={cn(
          "w-full caption-bottom text-sm border-separate border-spacing-0",
          tableLayout === "fixed" ? "table-fixed" : "table-auto"
        )}
      >
        <TableHeader>
          <TableRow className="border-b border-[#e5ecf5]">
            {columns.map((column, colIndex) => {
              const isLastColumn = colIndex === columns.length - 1;
              return (
                <TableHead
                  key={column.key}
                  className={cn(
                    "bg-[#fbfdff] py-3 h-auto min-w-0",
                    "px-3",
                    colIndex === 0 && "catalog-identity-cell",
                    column.align === "right"
                      ? "text-right"
                      : column.align === "center"
                        ? "text-center"
                        : "text-left",
                    column.widthClass
                  )}
                  scope="col"
                  aria-sort={sort === column.key ? (direction === "asc" ? "ascending" : "descending") : undefined}
                >
                  {"sortable" in column && column.sortable === false ? (
                    <span className="inline-flex max-w-full truncate text-[11px] font-medium text-slate-500">
                      {column.label}
                    </span>
                  ) : (
                    <button
                      className={`group rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-w-full min-w-0 ${columnButtonClass(column.align)} ${isLastColumn && column.align === "right" ? "mr-0" : ""}`}
                      onClick={() => onSortChange(column.key)}
                      type="button"
                      title={`Sort by ${column.label}`}
                    >
                      <span className="min-w-0 whitespace-nowrap leading-4">{column.label}</span>
                      <ArrowUpDown
                        aria-hidden
                        size={14}
                        className={cn(
                          "inline-block shrink-0 overflow-hidden transition-[max-width,opacity,margin] duration-150",
                          sort === column.key ? "ml-1 max-w-[14px] opacity-100" : "ml-0 max-w-0 opacity-0",
                          "group-hover:ml-1 group-hover:max-w-[14px] group-hover:opacity-100",
                          "group-focus-visible:ml-1 group-focus-visible:max-w-[14px] group-focus-visible:opacity-100",
                          sort === column.key ? "text-ocean" : "text-slate-300"
                        )}
                      />
                    </button>
                  )}
                </TableHead>
              );
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row, index) => (
            <DataTableRow
              key={
                row.blog_handle != null && row.handle != null
                  ? `${String(row.blog_handle)}/${String(row.handle)}`
                  : String(row.handle ?? index)
              }
              columns={columns}
              row={row}
              rowLink={getRowLink(row)}
              rowExternalLink={getRowExternalLink ? getRowExternalLink(row) : undefined}
              rowExternalLinkTitle={
                getRowExternalLinkTitle ? getRowExternalLinkTitle(row) : undefined
              }
              nameLinkClassName={nameLinkClassName}
            />
          ))}
        </TableBody>
      </table>
      </div>

      {isLoading ? <p role="status" className="px-4 py-6 text-sm text-slate-500">Loading catalog…</p> : null}
      {error ? <p role="alert" className="px-4 py-6 text-sm text-red-700">{error.message}</p> : null}
      {!isLoading && !error && rows.length === 0 ? <p role="status" className="px-4 py-8 text-sm text-slate-500">No items match your search.</p> : null}
    </div>
  );
}
