import { Search } from "lucide-react";

import type { DashboardGscPeriod } from "../lib/gsc-period";
import { catalogGscWindowDescription } from "../lib/gsc-period";
import { formatNumber, formatPercent } from "../lib/utils";
import type { GscQueryRow } from "../types/api";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";

type Props = {
  queries: GscQueryRow[];
  gscPeriod: DashboardGscPeriod;
};

export function GscTopQueriesSection({ queries, gscPeriod }: Props) {
  const windowHint = catalogGscWindowDescription(gscPeriod);

  return (
    <Card className="border-[#e8e4f8] bg-white shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
      <CardHeader className="pb-2">
        <div className="mb-1 flex items-center gap-2">
          <Search className="text-[#5746d9]" size={18} aria-hidden />
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Organic performance</p>
        </div>
        <CardTitle className="text-xl font-bold text-ink">Top search queries</CardTitle>
        <p className="mt-1 text-sm text-slate-500">
          {windowHint}           Refresh Search Console on this page if the list looks empty. Google returns up to 20 queries per
          URL in this view.
        </p>
      </CardHeader>
      <CardContent className="pt-0">
        {queries.length === 0 ? (
          <p className="text-sm text-slate-600">
            No query-level rows cached for this URL yet. Run a Search Console refresh for this item, or try another
            period from the dashboard GSC selector.
          </p>
        ) : (
          <div className="mt-2">
            <Table className="w-full min-w-[480px] text-sm">
              <TableHeader>
                <TableRow className="border-b border-[#e8e4f8]">
                  <TableHead className="pb-2 text-left text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                    Query
                  </TableHead>
                  <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                    Clicks
                  </TableHead>
                  <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                    Impressions
                  </TableHead>
                  <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                    CTR
                  </TableHead>
                  <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                    Avg pos.
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className="divide-y divide-[#f1eeff]">
                {queries.map((row) => (
                  <TableRow key={row.query}>
                    <TableCell className="max-w-[280px] py-2.5 pr-4 font-medium text-ink" title={row.query}>
                      <span className="line-clamp-2">{row.query}</span>
                    </TableCell>
                    <TableCell className="py-2.5 pl-4 text-right tabular-nums font-semibold text-ink">
                      {formatNumber(row.clicks)}
                    </TableCell>
                    <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600">
                      {formatNumber(row.impressions)}
                    </TableCell>
                    <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600">
                      {formatPercent(row.ctr)}
                    </TableCell>
                    <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600">
                      {Number.isFinite(row.position) ? row.position.toFixed(1) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
