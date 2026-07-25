import { TrendingUp } from "lucide-react";

import { formatNumber } from "../lib/utils";
import type { Trend } from "../types/api";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { MiniSparkline } from "./ui/mini-sparkline";

type Props = {
  trend?: Trend;
};

const UP = "#12a150";
const DOWN = "#ea6075";
const FLAT = "#94a3b8";

function tone(pct: number | null): string {
  if (pct === null || pct === 0) return FLAT;
  return pct > 0 ? UP : DOWN;
}

function deltaText(pct: number | null): string {
  if (pct === null) return "No prior period to compare";
  const r = Math.round(pct);
  if (r === 0) return "Flat vs the previous 30 days";
  return `${r > 0 ? "Up" : "Down"} ${Math.abs(r)}% vs the previous 30 days`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">{label}</p>
      <p className="text-lg font-bold tabular-nums text-ink">{value}</p>
    </div>
  );
}

/**
 * 30-day search performance for one page, with change vs the preceding 30 days.
 *
 * Sourced from stored daily history rather than the current-snapshot columns, so it
 * answers "is this page improving?" rather than only "how is it doing right now?".
 */
export function GscTrendSection({ trend }: Props) {
  const hasData = Boolean(trend && trend.series.length > 0);
  const pct = trend?.clicks_delta_pct ?? null;
  const color = tone(pct);

  return (
    <Card className="border-[#e8e4f8] bg-white shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
      <CardHeader className="pb-2">
        <div className="mb-1 flex items-center gap-2">
          <TrendingUp className="text-[#5746d9]" size={18} aria-hidden />
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Organic performance</p>
        </div>
        <CardTitle className="text-xl font-bold text-ink">Performance over time</CardTitle>
        <p className="mt-1 text-xs text-slate-500">
          {hasData ? deltaText(pct) : "No Search Console history stored for this page yet."}
        </p>
      </CardHeader>
      <CardContent>
        {hasData && trend ? (
          <div className="space-y-4">
            <div className="h-16 w-full">
              <MiniSparkline
                values={trend.series}
                color={color}
                ariaLabel="Daily clicks over the last 30 days"
              />
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat label="Clicks (30d)" value={formatNumber(trend.clicks_current)} />
              <Stat label="Previous 30d" value={formatNumber(trend.clicks_previous)} />
              <Stat label="Impressions (30d)" value={formatNumber(trend.impressions_current)} />
              <Stat label="Previous 30d" value={formatNumber(trend.impressions_previous)} />
            </div>
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            History builds automatically on each sync, and a one-off backfill can load everything
            Google still holds.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
