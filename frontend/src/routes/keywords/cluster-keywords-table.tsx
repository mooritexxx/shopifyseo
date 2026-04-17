import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";

import { Button } from "../../components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { DifficultyBadge, OpportunityBadge, RankingBadge } from "./badges";
import type { TargetKeyword } from "./schemas";

type SortKey = "volume" | "difficulty" | "opportunity";
type SortDir = "asc" | "desc";

function metricForSort(detail: TargetKeyword | undefined, key: SortKey): number | null {
  if (!detail) return null;
  switch (key) {
    case "volume":
      return detail.volume;
    case "difficulty":
      return detail.difficulty;
    case "opportunity":
      return detail.opportunity;
    default:
      return null;
  }
}

function compareMetrics(a: number | null, b: number | null, dir: SortDir): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const delta = a - b;
  return dir === "asc" ? delta : -delta;
}

function SortHeader({
  label,
  sortKey,
  activeKey,
  dir,
  onSort,
  align,
}: {
  label: string;
  sortKey: SortKey;
  activeKey: SortKey;
  dir: SortDir;
  onSort: (key: SortKey) => void;
  align: "left" | "right";
}) {
  const active = activeKey === sortKey;
  return (
    <TableHead className={`px-4 py-3 ${align === "right" ? "text-right" : "text-left"}`}>
      <Button
        variant="ghost"
        type="button"
        onClick={() => onSort(sortKey)}
        className={`h-auto p-0 inline-flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-ink rounded ${
          align === "right" ? "flex-row-reverse" : ""
        } focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40`}
        aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      >
        {label}
        {active ? (
          dir === "asc" ? (
            <ArrowUp className="h-3.5 w-3.5 shrink-0 text-slate-600" aria-hidden />
          ) : (
            <ArrowDown className="h-3.5 w-3.5 shrink-0 text-slate-600" aria-hidden />
          )
        ) : (
          <ArrowUpDown className="h-3.5 w-3.5 shrink-0 text-slate-400 opacity-70" aria-hidden />
        )}
      </Button>
    </TableHead>
  );
}

export function ClusterKeywordsTable({
  keywords,
  keywordMap,
  coverageCounts,
  coverageTotal,
}: {
  keywords: string[];
  keywordMap: Map<string, TargetKeyword>;
  coverageCounts?: Map<string, number>;
  coverageTotal?: number;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("opportunity");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  function onSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  const sortedKeywords = useMemo(() => {
    const rows = keywords.map((kw) => ({
      kw,
      detail: keywordMap.get(kw.toLowerCase()),
    }));
    rows.sort((ra, rb) => {
      const va = metricForSort(ra.detail, sortKey);
      const vb = metricForSort(rb.detail, sortKey);
      const c = compareMetrics(va, vb, sortDir);
      if (c !== 0) return c;
      return ra.kw.localeCompare(rb.kw);
    });
    return rows;
  }, [keywords, keywordMap, sortKey, sortDir]);

  return (
    <div className="rounded-xl border border-line bg-white">
      <Table className="w-full text-sm">
        <TableHeader>
          <TableRow className="border-b border-line text-left text-xs text-slate-500">
            <TableHead className="px-4 py-3">Keyword</TableHead>
            <SortHeader
              label="Volume"
              sortKey="volume"
              activeKey={sortKey}
              dir={sortDir}
              onSort={onSort}
              align="right"
            />
            <SortHeader
              label="Difficulty"
              sortKey="difficulty"
              activeKey={sortKey}
              dir={sortDir}
              onSort={onSort}
              align="right"
            />
            <SortHeader
              label="Opportunity"
              sortKey="opportunity"
              activeKey={sortKey}
              dir={sortDir}
              onSort={onSort}
              align="right"
            />
            <TableHead className="px-4 py-3">Ranking</TableHead>
            {coverageCounts && <TableHead className="px-4 py-3 text-right">Coverage</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedKeywords.map(({ kw, detail }) => {
            const count = coverageCounts?.get(kw.toLowerCase()) ?? 0;
            const total = coverageTotal ?? 0;
            return (
              <TableRow key={kw} className="border-b border-line last:border-0">
                <TableCell className="px-4 py-3 font-medium text-ink">{kw}</TableCell>
                <TableCell className="px-4 py-3 text-right">
                  {detail?.volume != null ? (
                    <span className="inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium tabular-nums text-slate-700">
                      {detail.volume.toLocaleString()}
                    </span>
                  ) : (
                    <span className="text-slate-400">—</span>
                  )}
                </TableCell>
                <TableCell className="px-4 py-3 text-right">
                  <DifficultyBadge kd={detail?.difficulty ?? null} />
                </TableCell>
                <TableCell className="px-4 py-3 text-right">
                  <OpportunityBadge opp={detail?.opportunity ?? null} decimals={1} />
                </TableCell>
                <TableCell className="px-4 py-3">
                  <RankingBadge status={detail?.ranking_status} />
                </TableCell>
                {coverageCounts && (
                  <TableCell className="px-4 py-3 text-right">
                    <span
                      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium tabular-nums ${
                        count > 0
                          ? "bg-green-100 text-green-700"
                          : "bg-red-100 text-red-700"
                      }`}
                    >
                      {count}/{total}
                    </span>
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
