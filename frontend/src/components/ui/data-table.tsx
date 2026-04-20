import { AlertTriangle, ArrowUpDown, Check, Eye } from "lucide-react";
import { Link } from "react-router-dom";
import { cn, formatNumber, formatPercent } from "../../lib/utils";
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from "./table";

export const listTableNameLinkClassName = "text-[calc(10px-1pt)]";

export type Column = {
  key: string;
  label: string;
  align: "left" | "right" | "center";
  sortable?: boolean;
  widthClass?: string;
};

function columnButtonClass(align: "left" | "right" | "center") {
  const base =
    "inline-flex max-w-full min-w-0 items-center gap-0 text-[8px] uppercase tracking-[0.18em] text-slate-500 transition hover:text-ink";
  if (align === "center") {
    return `${base} mx-auto`;
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
    <div className="w-full min-w-0 overflow-x-auto">
      <table
        className={cn(
          "w-full min-w-0 caption-bottom text-sm border-collapse",
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
                    "bg-[#fbfdff] py-4 h-auto min-w-0",
                    isLastColumn ? "pl-4 pr-8" : "px-4",
                    column.align === "right"
                      ? "text-right"
                      : column.align === "center"
                        ? "text-center"
                        : "text-left",
                    column.widthClass
                  )}
                  scope="col"
                >
                  {"sortable" in column && column.sortable === false ? (
                    <span className="inline-flex max-w-full truncate text-[8px] uppercase tracking-[0.18em] text-slate-500">
                      {column.label}
                    </span>
                  ) : (
                    <button
                      className={`group max-w-full min-w-0 ${columnButtonClass(column.align)} ${isLastColumn && column.align === "right" ? "mr-0" : ""}`}
                      onClick={() => onSortChange(column.key)}
                      type="button"
                      title={`Sort by ${column.label}`}
                    >
                      <span className="min-w-0 truncate">{column.label}</span>
                      <ArrowUpDown
                        aria-hidden
                        size={14}
                        className={cn(
                          "inline-block shrink-0 overflow-hidden transition-[max-width,opacity,margin] duration-150",
                          "ml-0 max-w-0 opacity-0",
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
            <TableRow
              key={
                row.blog_handle != null && row.handle != null
                  ? `${String(row.blog_handle)}/${String(row.handle)}`
                  : String(row.handle ?? index)
              }
              className="border-b border-[#e8eef6] align-middle last:border-b-0"
            >
              {columns.map((column, colIndex) => {
                const isLastColumn = colIndex === columns.length - 1;
                const cellPadding = isLastColumn ? "pl-4 pr-8" : "px-4";
                if (column.key === "title" || column.key === "article_name") {
                  const label =
                    column.key === "article_name"
                      ? String(row.article_name ?? row.title ?? "")
                      : String(row.title || "");
                  return (
                    <TableCell
                      key={column.key}
                      className={cn(
                        "border-b border-[#e8eef6] bg-white py-4 text-left min-w-0",
                        cellPadding,
                        column.widthClass
                      )}
                    >
                      <div className="flex min-w-0 max-w-full items-center gap-2">
                        <Link
                          className={cn(
                            "block min-w-0 truncate font-semibold text-ink transition hover:text-ocean",
                            nameLinkClassName ?? "text-[10px]"
                          )}
                          to={getRowLink(row)}
                        >
                          {label}
                        </Link>
                        {getRowExternalLink ? (
                          <a
                            className="shrink-0 text-slate-400 transition hover:text-ocean"
                            href={getRowExternalLink(row)}
                            rel="noreferrer"
                            target="_blank"
                            title={getRowExternalLinkTitle ? getRowExternalLinkTitle(row) : "Open live page"}
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
                    <TableCell key={column.key} className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-4 text-center min-w-0`} title={isContentComplete(row as { seo_title?: string; seo_description?: string; body_length?: number }) ? "Meta title, meta description, and body are filled" : "Meta title, meta description, or body is missing"}>
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
                      className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-4 text-center min-w-0`}
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
                if (column.key === "index_status") {
                  const raw = String(row.index_status ?? "").trim() || "Unknown";
                  const indexed = raw.toLowerCase() === "indexed";
                  return (
                    <TableCell
                      key={column.key}
                      className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-4 text-center min-w-0`}
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
                      className={`border-b border-[#e8eef6] bg-white ${cellPadding} py-4 text-center text-[10px] text-slate-600 min-w-0`}
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
                      "border-b border-[#e8eef6] bg-white py-4 text-[10px] text-slate-600 min-w-0",
                      cellPadding,
                      cellAlign,
                      column.widthClass
                    )}
                  >
                    <span
                      className={cn(
                        "block min-w-0 max-w-full font-semibold text-[10px] text-ink",
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
          ))}
        </TableBody>
      </table>

      {isLoading ? <p className="px-6 py-4 text-sm text-slate-500">Loading…</p> : null}
      {error ? <p className="px-6 py-4 text-sm text-[#a33f17]">{error.message}</p> : null}
    </div>
  );
}
