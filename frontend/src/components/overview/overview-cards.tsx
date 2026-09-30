import type { ComponentType, ReactNode } from "react";
import { Link } from "react-router-dom";

import { cn, formatNumber, formatRelativeTimestamp } from "../../lib/utils";

export function DeltaInline({
  pct,
  unit = "percent"
}: {
  pct: number | null | undefined;
  unit?: "percent" | "points";
}) {
  if (pct == null || Number.isNaN(pct)) return null;
  const up = pct > 0;
  const down = pct < 0;
  const suffix = unit === "points" ? " pp" : "%";
  return (
    <span
      className={cn(
        "ml-1.5 text-xs font-semibold tabular-nums",
        up && "text-emerald-600",
        down && "text-rose-600",
        !up && !down && "text-slate-500"
      )}
    >
      {up ? "↑" : down ? "↓" : "→"} {Math.abs(pct).toFixed(1)}
      {suffix} vs prior
    </span>
  );
}

function topGscPropertyBreakdownRow(slice: {
  rows: Array<{ keys?: string[]; impressions?: number | string }>;
}): { rawKey: string; impressions: number } | null {
  const r = slice.rows?.[0];
  if (!r?.keys?.length) return null;
  const rawKey = String(r.keys[0] ?? "").trim();
  if (!rawKey) return null;
  return { rawKey, impressions: Number(r.impressions) || 0 };
}

function formatGscBreakdownSegmentLabel(rawKey: string, dimension: "country" | "device" | "appearance"): string {
  const s = rawKey.trim();
  if (!s) return "—";
  if (dimension === "country" && s.length <= 3) return s.toUpperCase();
  if (dimension === "device") {
    const lower = s.toLowerCase();
    return lower.charAt(0).toUpperCase() + lower.slice(1);
  }
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function SegmentMixTile({
  label,
  dimension,
  slice,
  icon: Icon
}: {
  label: string;
  dimension: "country" | "device" | "appearance";
  slice: {
    rows: Array<{ keys?: string[]; impressions?: number | string }>;
    top_bucket_impressions_pct_vs_prior?: number | null;
  };
  icon: ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean }>;
}) {
  const row = topGscPropertyBreakdownRow(slice);
  return (
    <div className="overview-metric min-w-0 p-5">
      <div className="flex items-start justify-between gap-2">
        <p className="overview-metric-label">{label}</p>
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-100 text-slate-500">
          <Icon size={16} strokeWidth={2} aria-hidden />
        </span>
      </div>
      {row ? (
        <>
          <p className="overview-metric-value">
            {formatNumber(row.impressions)}
          </p>
          <p className="mt-1 text-xs font-medium text-slate-600">
            {formatGscBreakdownSegmentLabel(row.rawKey, dimension)}
          </p>
          <p className="mt-2 text-[11px] leading-snug text-slate-500">
            Impressions · top bucket in window
            <DeltaInline pct={slice.top_bucket_impressions_pct_vs_prior ?? null} />
          </p>
        </>
      ) : (
        <>
          <p className="overview-metric-value text-slate-400">—</p>
          <p className="mt-1 text-xs text-slate-500">No rows in cache</p>
        </>
      )}
    </div>
  );
}

export function overviewCacheHint(cache: { text: string; meta?: unknown }) {
  const meta =
    cache.meta && typeof cache.meta === "object" && cache.meta !== null
      ? (cache.meta as Record<string, unknown>)
      : null;
  const raw = meta?.fetched_at;
  const ts =
    raw != null
      ? typeof raw === "number"
        ? raw
        : Number(raw)
      : null;
  const relative =
    ts != null && Number.isFinite(ts) ? formatRelativeTimestamp(ts).split(" · ")[0] : null;
  return (
    <span className="block space-y-0.5">
      {relative ? <span className="font-medium text-slate-700">Refreshed {relative}</span> : null}
      <span className="text-slate-500">{cache.text}</span>
    </span>
  );
}

export function CompletionBar({
  label,
  pct,
  href,
  sub
}: {
  label: string;
  pct: number;
  href: string;
  sub?: string;
}) {
  const w = Math.min(100, Math.max(0, pct));
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 text-sm">
        <Link className="font-semibold text-[#5746d9] hover:underline" to={href}>
          {label}
        </Link>
        <span className="shrink-0 tabular-nums text-slate-600">{pct.toFixed(1)}%</span>
      </div>
      <div className="mt-2 h-2.5 overflow-hidden rounded-full bg-[#ede9f7]">
        <div
          className="h-full rounded-full bg-[#5746d9] transition-[width] duration-500 ease-out"
          style={{ width: `${w}%` }}
        />
      </div>
      {sub ? <p className="mt-1.5 text-xs text-slate-500">{sub}</p> : null}
    </div>
  );
}

export function KpiCard({
  label,
  value,
  hint,
  sparkline,
  className
}: {
  label: string;
  value: string;
  hint?: ReactNode;
  sparkline?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "overview-metric min-w-0 p-5",
        className
      )}
    >
      <p className="overview-metric-label">{label}</p>
      <p className="overview-metric-value">{value}</p>
      {sparkline ? <div className="overview-metric-sparkline mt-3 w-full min-w-0">{sparkline}</div> : null}
      {hint ? <div className="overview-metric-hint text-xs text-slate-500">{hint}</div> : null}
    </div>
  );
}
