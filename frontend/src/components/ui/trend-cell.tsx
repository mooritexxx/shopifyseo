import { memo } from "react";

import type { Trend } from "../../types/api";
import { MiniSparkline } from "./mini-sparkline";

type Props = {
  trend?: Trend;
  label: string;
};

const UP = "#12a150";
const DOWN = "#ea6075";
const FLAT = "#94a3b8";

function deltaColor(pct: number | null): string {
  if (pct === null || pct === 0) return FLAT;
  return pct > 0 ? UP : DOWN;
}

function deltaLabel(pct: number | null): string {
  if (pct === null) return "—";
  const rounded = Math.round(pct);
  if (rounded === 0) return "0%";
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

/**
 * 30-day click trend: sparkline plus change vs the preceding 30 days.
 *
 * A null delta means there is no prior-period baseline to compare against (a new or
 * newly-discovered page), which is shown as "—" rather than a misleading +100%.
 */
export const TrendCell = memo(function TrendCell({ trend, label }: Props) {
  if (!trend || trend.series.length === 0) {
    return <span className="text-xs text-slate-400">—</span>;
  }

  const pct = trend.clicks_delta_pct;
  const color = deltaColor(pct);
  const hasSeries = trend.series.some((v) => v > 0);

  return (
    <div className="flex flex-col items-center gap-0.5">
      <div className="h-[22px] w-full max-w-[92px]">
        {hasSeries ? (
          <MiniSparkline
            values={trend.series}
            color={color}
            ariaLabel={`Daily clicks over the last 30 days for ${label}`}
          />
        ) : null}
      </div>
      <span className="text-xs font-semibold tabular-nums" style={{ color }}>
        {deltaLabel(pct)}
      </span>
    </div>
  );
});
