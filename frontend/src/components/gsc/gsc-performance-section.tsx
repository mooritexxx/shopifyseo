import { type ReactNode, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, FileSearch } from "lucide-react";

import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { cn, formatNumber, formatPercent } from "../../lib/utils";

const cardElevated =
  "rounded-[24px] border border-[#e8e4f8] bg-white shadow-[0_2px_20px_rgba(15,23,42,0.04)]";

function toNumber(value: number | string | undefined) {
  if (typeof value === "number") return value;
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** GSC searchAnalytics returns dimension values as keys[0]; normalize display (ISO alpha-3 countries, DEVICE enums, etc.). */
function formatGscBreakdownLabel(
  dimension: "country" | "device" | "searchAppearance",
  raw: string
): string {
  const s = raw.trim();
  if (!s) return "—";
  if (dimension === "device") {
    const u = s.toUpperCase().replace(/-/g, "_");
    const map: Record<string, string> = {
      MOBILE: "Mobile",
      DESKTOP: "Desktop",
      TABLET: "Tablet",
      SMART_TV: "Smart TV"
    };
    return (
      map[u] ??
      s
        .replace(/_/g, " ")
        .split(" ")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
        .join(" ")
    );
  }
  if (dimension === "country") {
    const lc = s.toLowerCase();
    const a3to2: Record<string, string> = {
      usa: "US",
      can: "CA",
      gbr: "GB",
      aus: "AU",
      deu: "DE",
      fra: "FR",
      ita: "IT",
      esp: "ES",
      nld: "NL",
      bel: "BE",
      che: "CH",
      aut: "AT",
      irl: "IE",
      nzl: "NZ",
      ind: "IN",
      jpn: "JP",
      kor: "KR",
      chn: "CN",
      bra: "BR",
      mex: "MX",
      pol: "PL",
      swe: "SE",
      nor: "NO",
      dnk: "DK",
      fin: "FI",
      prt: "PT",
      zaf: "ZA",
      sgp: "SG",
      hkg: "HK",
      twn: "TW",
      phl: "PH",
      tha: "TH",
      vnm: "VN",
      idn: "ID",
      mys: "MY",
      are: "AE",
      sau: "SA",
      isr: "IL",
      tur: "TR",
      rus: "RU",
      ukr: "UA",
      arg: "AR",
      chl: "CL",
      col: "CO",
      per: "PE",
      egy: "EG",
      nga: "NG",
      ken: "KE"
    };
    const two = s.length === 2 ? s.toUpperCase() : a3to2[lc];
    if (two && two.length === 2) {
      try {
        const name = new Intl.DisplayNames(undefined, { type: "region" }).of(two);
        if (name) return name;
      } catch {
        /* ignore */
      }
      return two;
    }
    return s.toUpperCase();
  }
  return s
    .replace(/_/g, " ")
    .split(" ")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())
    .join(" ");
}

export type GscMetricRow = {
  keys?: string[];
  clicks?: number | string;
  impressions?: number | string;
  ctr?: number;
  position?: number;
};

type GscPerfTab = "queries" | "pages" | "countries" | "devices";

type GscSortColumn = "clicks" | "impressions";

export type GscBreakdownSlice = {
  rows: GscMetricRow[];
  error: string;
  cache: { label: string; text: string };
};

function formatPositionValue(position: number | undefined) {
  const n = typeof position === "number" ? position : Number(position ?? 0);
  if (!Number.isFinite(n) || n <= 0) return "—";
  return n.toFixed(1);
}

function sortGscRows(rows: GscMetricRow[], column: GscSortColumn, direction: "asc" | "desc") {
  const mult = direction === "desc" ? -1 : 1;
  return [...rows].sort((a, b) => {
    const aVal = toNumber(column === "clicks" ? a.clicks : a.impressions);
    const bVal = toNumber(column === "clicks" ? b.clicks : b.impressions);
    if (aVal !== bVal) return (aVal - bVal) * mult;
    const aKey = (a.keys || [""])[0] || "";
    const bKey = (b.keys || [""])[0] || "";
    return aKey.localeCompare(bKey);
  });
}

const GSC_TABLE_PAGE_SIZE = 10;

const GSC_PERF_TABS: { id: GscPerfTab; label: string }[] = [
  { id: "queries", label: "Queries" },
  { id: "pages", label: "Pages" },
  { id: "countries", label: "Countries" },
  { id: "devices", label: "Devices" }
];

function GscSortTh({
  column,
  activeColumn,
  direction,
  onSort,
  children
}: {
  column: GscSortColumn;
  activeColumn: GscSortColumn;
  direction: "asc" | "desc";
  onSort: (column: GscSortColumn) => void;
  children: ReactNode;
}) {
  const active = activeColumn === column;
  const ariaSort: "ascending" | "descending" | "none" = active
    ? direction === "asc"
      ? "ascending"
      : "descending"
    : "none";
  return (
    <th scope="col" className="px-3 py-2.5 text-right" aria-sort={ariaSort}>
      <button
        type="button"
        onClick={() => onSort(column)}
        className={cn(
          "inline-flex items-center justify-end gap-1 font-semibold tabular-nums transition hover:text-[#5746d9]",
          active ? "text-[#5746d9]" : "text-slate-600"
        )}
      >
        {children}
        {active ? (
          direction === "desc" ? (
            <ArrowDown className="shrink-0" size={14} aria-hidden />
          ) : (
            <ArrowUp className="shrink-0" size={14} aria-hidden />
          )
        ) : null}
      </button>
    </th>
  );
}

export function GscPerformanceSection({
  gscRangeLabel,
  gsc_queries,
  gsc_pages,
  countrySlice,
  deviceSlice
}: {
  gscRangeLabel: string;
  gsc_queries: GscMetricRow[];
  gsc_pages: GscMetricRow[];
  countrySlice: GscBreakdownSlice;
  deviceSlice: GscBreakdownSlice;
}) {
  const [activeTab, setActiveTab] = useState<GscPerfTab>("queries");
  const [tabSort, setTabSort] = useState<
    Record<GscPerfTab, { column: GscSortColumn; direction: "asc" | "desc" }>
  >({
    queries: { column: "clicks", direction: "desc" },
    pages: { column: "clicks", direction: "desc" },
    countries: { column: "clicks", direction: "desc" },
    devices: { column: "clicks", direction: "desc" }
  });
  const [pageByTab, setPageByTab] = useState<Record<GscPerfTab, number>>({
    queries: 0,
    pages: 0,
    countries: 0,
    devices: 0
  });

  const sourceRows = useMemo(() => {
    switch (activeTab) {
      case "queries":
        return gsc_queries;
      case "pages":
        return gsc_pages;
      case "countries":
        return countrySlice.rows;
      case "devices":
        return deviceSlice.rows;
      default:
        return [];
    }
  }, [activeTab, gsc_queries, gsc_pages, countrySlice.rows, deviceSlice.rows]);

  const sliceError =
    activeTab === "countries" ? countrySlice.error : activeTab === "devices" ? deviceSlice.error : "";

  const sortedRows = useMemo(() => {
    const { column, direction } = tabSort[activeTab];
    return sortGscRows(sourceRows, column, direction);
  }, [sourceRows, activeTab, tabSort]);

  const pageCount = Math.max(1, Math.ceil(sortedRows.length / GSC_TABLE_PAGE_SIZE));
  const tablePage = pageByTab[activeTab];

  useEffect(() => {
    const maxPage = pageCount - 1;
    setPageByTab((prev) => {
      const cur = prev[activeTab];
      if (cur <= maxPage) return prev;
      return { ...prev, [activeTab]: maxPage };
    });
  }, [activeTab, pageCount, sortedRows.length]);

  const tableRows = useMemo(() => {
    const start = tablePage * GSC_TABLE_PAGE_SIZE;
    return sortedRows.slice(start, start + GSC_TABLE_PAGE_SIZE);
  }, [sortedRows, tablePage]);

  const paginationRangeLabel =
    sortedRows.length > 0
      ? (() => {
          const start = tablePage * GSC_TABLE_PAGE_SIZE + 1;
          const end = Math.min((tablePage + 1) * GSC_TABLE_PAGE_SIZE, sortedRows.length);
          return `${start}–${end} of ${sortedRows.length}`;
        })()
      : null;

  const dimensionLabel =
    activeTab === "queries"
      ? "Query"
      : activeTab === "pages"
        ? "Page"
        : activeTab === "countries"
          ? "Country"
          : "Device";

  const tableTitle =
    activeTab === "queries"
      ? "Top queries"
      : activeTab === "pages"
        ? "Top pages"
        : activeTab === "countries"
          ? "Countries"
          : "Devices";

  const emptyHint =
    activeTab === "queries"
      ? "Queries appear after a successful Search Console refresh."
      : activeTab === "pages"
        ? "No page data yet. Save settings, then use Refresh GSC."
        : activeTab === "countries"
          ? "No country breakdown yet. Refresh GSC after connecting."
          : "No device breakdown yet. Refresh GSC after connecting.";

  const gscListEmpty = gsc_pages.length === 0 && gsc_queries.length === 0;

  function onSortClick(column: GscSortColumn) {
    setPageByTab((prev) => ({ ...prev, [activeTab]: 0 }));
    setTabSort((prev) => {
      const cur = prev[activeTab];
      if (cur.column === column) {
        return {
          ...prev,
          [activeTab]: { column, direction: cur.direction === "desc" ? "asc" : "desc" }
        };
      }
      return { ...prev, [activeTab]: { column, direction: "desc" } };
    });
  }

  const sort = tabSort[activeTab];

  return (
    <Card className={cn("overflow-hidden border-[#e8e4f8] p-0", cardElevated, "w-full")}>
      <div className="border-b border-[#ede9f7] bg-[linear-gradient(180deg,#ffffff_0%,#faf8ff_100%)] px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <FileSearch className="shrink-0 text-[#5746d9]" size={18} />
            <h3 className="text-base font-bold text-ink">Search Console performance</h3>
          </div>
          {gscRangeLabel ? (
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
              {gscRangeLabel}
            </span>
          ) : null}
        </div>
        <div
          className="mt-3 flex gap-1 overflow-x-auto border-b border-transparent pb-px [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          role="tablist"
          aria-label="Search Console dimensions"
        >
          {GSC_PERF_TABS.map(({ id, label }) => (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={activeTab === id}
              id={`gsc-perf-tab-${id}`}
              aria-controls={`gsc-perf-panel-${id}`}
              onClick={() => setActiveTab(id)}
              className={cn(
                "shrink-0 border-b-2 px-2.5 py-2 text-[10px] font-bold uppercase tracking-[0.12em] transition",
                activeTab === id
                  ? "border-ink text-ink"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div
        className="p-2 sm:p-3"
        role="tabpanel"
        id={`gsc-perf-panel-${activeTab}`}
        aria-labelledby={`gsc-perf-tab-${activeTab}`}
      >
        {(activeTab === "queries" || activeTab === "pages") && gscListEmpty ? (
          <div className="px-3 py-8 text-center text-sm text-slate-500">{emptyHint}</div>
        ) : (
          <>
            {sliceError ? (
              <p className="mb-3 rounded-xl bg-[#fff4ef] px-3 py-3 text-sm text-[#8f3e20]">{sliceError}</p>
            ) : null}
            {!sliceError && sortedRows.length === 0 ? (
              <div className="px-3 py-8 text-center text-sm text-slate-500">{emptyHint}</div>
            ) : null}
            {sortedRows.length > 0 ? (
              <div className="min-w-0 overflow-x-auto">
                <table className="w-full min-w-[520px] border-collapse text-sm">
                  <caption className="sr-only">{tableTitle}</caption>
                  <thead>
                    <tr className="border-b border-[#ede9f7] text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                      <th
                        scope="col"
                        className="sticky left-0 z-10 bg-white px-3 py-2.5 shadow-[4px_0_8px_-4px_rgba(15,23,42,0.08)]"
                      >
                        {dimensionLabel}
                      </th>
                      <GscSortTh
                        column="clicks"
                        activeColumn={sort.column}
                        direction={sort.direction}
                        onSort={onSortClick}
                      >
                        <span className="text-[#1a73e8]">Clicks</span>
                      </GscSortTh>
                      <GscSortTh
                        column="impressions"
                        activeColumn={sort.column}
                        direction={sort.direction}
                        onSort={onSortClick}
                      >
                        <span className="text-[#9333ea]">Impressions</span>
                      </GscSortTh>
                      <th scope="col" className="px-3 py-2.5 text-right font-semibold text-slate-600">
                        CTR
                      </th>
                      <th scope="col" className="px-3 py-2.5 text-right font-semibold text-slate-600">
                        Position
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {tableRows.map((row, index) => {
                      const rawKey = (row.keys || []).find((k) => k && String(k).trim()) ?? "";
                      const label =
                        activeTab === "queries"
                          ? rawKey || "—"
                          : activeTab === "pages"
                            ? rawKey || "—"
                            : activeTab === "countries"
                              ? formatGscBreakdownLabel("country", rawKey)
                              : formatGscBreakdownLabel("device", rawKey);
                      const clicks = Math.round(toNumber(row.clicks));
                      const impr = Math.round(toNumber(row.impressions));
                      const ctrFrac =
                        row.ctr != null && Number.isFinite(Number(row.ctr))
                          ? Number(row.ctr)
                          : impr > 0
                            ? clicks / impr
                            : 0;
                      const stableIndex = tablePage * GSC_TABLE_PAGE_SIZE + index;
                      return (
                        <tr
                          key={`${activeTab}-${stableIndex}-${rawKey || stableIndex}`}
                          className="border-b border-[#f4f0ff] last:border-0 hover:bg-[#faf8ff]/80"
                        >
                          <th
                            scope="row"
                            className="sticky left-0 z-10 max-w-[min(40vw,280px)] bg-white px-3 py-2.5 text-left font-medium text-ink shadow-[4px_0_8px_-4px_rgba(15,23,42,0.06)]"
                          >
                            <span className="block truncate" title={label}>
                              {label}
                            </span>
                          </th>
                          <td className="px-3 py-2.5 text-right tabular-nums text-[#1a73e8]">
                            {formatNumber(clicks)}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-[#9333ea]">
                            {formatNumber(impr)}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                            {formatPercent(ctrFrac)}
                          </td>
                          <td className="px-3 py-2.5 text-right tabular-nums text-slate-700">
                            {formatPositionValue(row.position)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {sortedRows.length > GSC_TABLE_PAGE_SIZE ? (
                  <div className="mt-3 flex flex-col gap-2 border-t border-[#ede9f7] px-2 pt-3 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-xs text-slate-600">
                      <span className="font-medium text-slate-800">{paginationRangeLabel}</span>
                      <span className="text-slate-400"> · </span>
                      Page {tablePage + 1} of {pageCount}
                    </p>
                    <div className="flex items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-8 rounded-lg border-[#e8e4f8] text-xs"
                        disabled={tablePage <= 0}
                        onClick={() =>
                          setPageByTab((prev) => ({
                            ...prev,
                            [activeTab]: Math.max(0, prev[activeTab] - 1)
                          }))
                        }
                        aria-label="Previous page"
                      >
                        <ChevronLeft className="size-4" aria-hidden />
                        Previous
                      </Button>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-8 rounded-lg border-[#e8e4f8] text-xs"
                        disabled={tablePage >= pageCount - 1}
                        onClick={() =>
                          setPageByTab((prev) => ({
                            ...prev,
                            [activeTab]: Math.min(pageCount - 1, prev[activeTab] + 1)
                          }))
                        }
                        aria-label="Next page"
                      >
                        Next
                        <ChevronRight className="size-4" aria-hidden />
                      </Button>
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}
          </>
        )}

        {(activeTab === "countries" || activeTab === "devices") && !sliceError ? (
          <p className="mt-3 border-t border-[#ede9f7] px-2 pt-3 text-xs text-slate-500">
            <span className="font-semibold text-[#5746d9]">
              {activeTab === "countries" ? countrySlice.cache.label : deviceSlice.cache.label}
            </span>
            <span className="text-slate-400"> · </span>
            {activeTab === "countries" ? countrySlice.cache.text : deviceSlice.cache.text}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
