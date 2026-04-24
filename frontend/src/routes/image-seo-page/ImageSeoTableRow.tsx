import { ArrowDown, ArrowUp, ArrowUpDown, Check, CircleAlert } from "lucide-react";
import { Link } from "react-router-dom";

import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import { TableCell, TableHead, TableRow } from "../../components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "../../components/ui/tooltip";
import type { CatalogImageSeoRow } from "../../types/api";
import {
  formatBytes,
  formatImageFormatLabel,
  formatPixelDimensions,
  isRowSeoOptimized,
  RESOURCE_TYPE_LABEL,
  resourceLink,
  type ImageSeoListSort,
} from "./utils";

// ---------------------------------------------------------------------------
// Sortable column header
// ---------------------------------------------------------------------------

interface ImageSeoSortableThProps {
  label: string;
  column: ImageSeoListSort;
  sortColumn: ImageSeoListSort;
  sortDirection: "asc" | "desc";
  onSort: (col: ImageSeoListSort) => void;
  align?: "start" | "center" | "end";
}

export function ImageSeoSortableTh({
  label,
  column,
  sortColumn,
  sortDirection,
  onSort,
  align = "start",
}: ImageSeoSortableThProps) {
  const active = sortColumn === column;
  const thAlign = align === "end" ? "text-right" : align === "center" ? "text-center" : "";
  const btnAlign =
    align === "end" ? "ml-auto" : align === "center" ? "mx-auto" : "";
  return (
    <TableHead scope="col" className={`px-3 py-3 ${thAlign}`}>
      <Button
        variant="ghost"
        type="button"
        onClick={() => onSort(column)}
        className={`inline-flex items-center gap-1 h-auto p-0 font-medium text-muted-foreground transition hover:text-ink ${btnAlign}`}
        aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
      >
        {label}
        {active ? (
          sortDirection === "asc" ? (
            <ArrowUp className="h-3.5 w-3.5 shrink-0 text-ink" aria-hidden />
          ) : (
            <ArrowDown className="h-3.5 w-3.5 shrink-0 text-ink" aria-hidden />
          )
        ) : (
          <ArrowUpDown className="h-3.5 w-3.5 shrink-0 opacity-40" aria-hidden />
        )}
      </Button>
    </TableHead>
  );
}

// ---------------------------------------------------------------------------
// Table row
// ---------------------------------------------------------------------------

interface ImageSeoTableRowProps {
  row: CatalogImageSeoRow;
  selected: boolean;
  onToggleSelect: (id: string) => void;
  onView: (row: CatalogImageSeoRow) => void;
}

export function ImageSeoTableRow({ row, selected, onToggleSelect, onView }: ImageSeoTableRowProps) {
  const { to, handleLine } = resourceLink(row);
  const seoOk = isRowSeoOptimized(row);

  return (
    <TableRow className="border-b border-border/80 last:border-0">
      <TableCell className="w-10 px-3 py-2 align-middle text-center">
        {row.optimize_supported ? (
          <Checkbox
            className="h-4 w-4"
            checked={selected}
            onCheckedChange={() => onToggleSelect(row.image_row_id)}
            aria-label={`Select ${row.resource_title}`}
          />
        ) : null}
      </TableCell>
      <TableCell className="px-3 py-2 align-middle">
        <img
          src={row.url}
          alt=""
          className="h-14 w-14 rounded-lg border border-border object-cover"
          loading="lazy"
        />
      </TableCell>
      <TableCell className="px-3 py-2 align-middle">
        <Badge variant="secondary" className="text-[10px] font-normal">
          {RESOURCE_TYPE_LABEL[row.resource_type]}
        </Badge>
      </TableCell>
      <TableCell className="px-3 py-2 align-middle">
        <Link to={to} className="font-medium text-primary hover:underline">
          {row.resource_title}
        </Link>
        <div className="text-xs text-muted-foreground">{handleLine}</div>
      </TableCell>
      <TableCell className="whitespace-nowrap px-3 py-2 align-middle text-xs text-muted-foreground tabular-nums">
        {row.resource_type === "product" && row.position != null ? (
          <>#{row.position}</>
        ) : (
          "—"
        )}
      </TableCell>
      <TableCell className="w-14 whitespace-nowrap px-3 py-2 align-middle text-center">
        {row.local_file_cached === true ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex items-center justify-center rounded-md p-1 text-emerald-600 transition hover:bg-muted/80">
                <Check className="h-5 w-5" strokeWidth={2.5} />
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">Cached locally</TooltipContent>
          </Tooltip>
        ) : row.local_file_cached === false ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex items-center justify-center rounded-md p-1 text-amber-500 transition hover:bg-muted/80">
                <CircleAlert className="h-5 w-5" strokeWidth={2} />
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">Not cached locally</TooltipContent>
          </Tooltip>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="whitespace-nowrap px-3 py-2 align-middle text-xs tabular-nums text-muted-foreground">
        {formatPixelDimensions(row.image_width, row.image_height)}
      </TableCell>
      <TableCell className="w-14 whitespace-nowrap px-3 py-2 align-middle text-center">
        {(() => {
          const fmt = formatImageFormatLabel(row.image_format);
          if (fmt === "—") return <span className="text-muted-foreground">—</span>;
          const isWebp = fmt.toLowerCase() === "webp";
          return isWebp ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex items-center justify-center rounded-md p-1 text-emerald-600 transition hover:bg-muted/80">
                  <Check className="h-5 w-5" strokeWidth={2.5} />
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">WebP</TooltipContent>
            </Tooltip>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="inline-flex items-center justify-center rounded-md p-1 text-amber-500 transition hover:bg-muted/80">
                  <CircleAlert className="h-5 w-5" strokeWidth={2} />
                </span>
              </TooltipTrigger>
              <TooltipContent side="top" className="text-xs">{fmt}</TooltipContent>
            </Tooltip>
          );
        })()}
      </TableCell>
      <TableCell className="whitespace-nowrap px-3 py-2 align-middle text-xs tabular-nums text-muted-foreground">
        {row.file_size_bytes != null && row.file_size_bytes > 0
          ? formatBytes(row.file_size_bytes)
          : "—"}
      </TableCell>
      <TableCell className="w-14 px-3 py-2 align-middle text-center">
        {row.alt_text.trim() ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="inline-flex items-center justify-center rounded-md p-1 text-emerald-600 transition hover:bg-muted/80"
                aria-label="Alt text present"
              >
                <Check className="h-5 w-5" strokeWidth={2.5} />
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-sm whitespace-pre-wrap break-words text-left text-xs">
              {row.alt_text}
            </TooltipContent>
          </Tooltip>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex items-center justify-center rounded-md p-1 text-amber-500 transition hover:bg-muted/80">
                <CircleAlert className="h-5 w-5" strokeWidth={2} />
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">Missing alt text</TooltipContent>
          </Tooltip>
        )}
      </TableCell>
      <TableCell className="px-3 py-2 align-middle text-right">
        {row.optimize_supported ? (
          <Button size="sm" variant="secondary" onClick={() => onView(row)}>
            View
          </Button>
        ) : (
          <span
            className="inline-block text-xs text-muted-foreground"
            title="View is available for optimizable product and collection images only."
          >
            —
          </span>
        )}
      </TableCell>
      <TableCell className="w-14 px-3 py-2 align-middle text-center">
        {seoOk ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="inline-flex items-center justify-center rounded-md p-1 text-emerald-600 transition hover:bg-muted/80"
                aria-label="Optimized"
              >
                <Check className="h-5 w-5" strokeWidth={2.5} />
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="text-xs">Optimized</TooltipContent>
          </Tooltip>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                className="inline-flex items-center justify-center rounded-md p-1 text-amber-500 transition hover:bg-muted/80"
                aria-label="Not optimized"
              >
                <CircleAlert className="h-5 w-5" strokeWidth={2} />
              </span>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs text-xs">
              {[
                row.flags.missing_or_weak_alt && "Weak or missing alt",
                row.flags.weak_filename && "Weak filename",
                row.flags.seo_filename_mismatch && "SEO filename mismatch",
                row.flags.not_webp && "Not WebP",
              ]
                .filter(Boolean)
                .join(", ")}
            </TooltipContent>
          </Tooltip>
        )}
      </TableCell>
    </TableRow>
  );
}
