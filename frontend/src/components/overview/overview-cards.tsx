import type { ComponentType, ReactNode } from "react";
import { Link } from "react-router-dom";

import { formatGscBreakdownLabel } from "../gsc/gsc-performance-section";
import { cn, formatNumber } from "../../lib/utils";

export function DeltaInline({
  pct,
  unit = "percent",
  lowerIsBetter = false
}: {
  pct: number | null | undefined;
  unit?: "percent" | "points";
  lowerIsBetter?: boolean;
}) {
  if (pct == null || Number.isNaN(pct)) return null;
  const up = pct > 0;
  const down = pct < 0;
  const improved = lowerIsBetter ? down : up;
  const worsened = lowerIsBetter ? up : down;
  const suffix = unit === "points" ? " pp" : "%";
  return (
    <span
      className={cn(
        "ml-1.5 text-xs font-semibold tabular-nums",
        improved && "text-emerald-600",
        worsened && "text-rose-600",
        !up && !down && "text-slate-500"
      )}
    >
      {up ? "↑" : down ? "↓" : "→"} {Math.abs(pct).toFixed(1)}
      {suffix} vs prior
    </span>
  );
}

export function SegmentMixTile({ label, dimension, slice, icon: Icon, onExplore }: {
  label: string;
  dimension: "country" | "device" | "appearance";
  slice: { rows: Array<{ keys?: string[]; impressions?: number | string }>; top_bucket_impressions_pct_vs_prior?: number | null };
  icon: ComponentType<{ size?: number; strokeWidth?: number; "aria-hidden"?: boolean }>;
  onExplore?: () => void;
}) {
  const rows = slice.rows.filter(row => row.keys?.[0]?.trim()).map(row => ({
    key: String(row.keys![0]), impressions: Math.max(0, Number(row.impressions) || 0)
  })).sort((a, b) => b.impressions - a.impressions);
  const top = rows[0];
  const total = rows.reduce((sum, row) => sum + row.impressions, 0);
  const share = top && total > 0 ? top.impressions / total * 100 : null;
  return <div className="overview-audience-item min-w-0">
    <p className="flex items-center gap-2 text-xs font-medium text-slate-500"><Icon size={15} aria-hidden />{label}</p>
    <p className="mt-2 text-base font-semibold text-slate-800">{top ? formatGscBreakdownLabel(dimension === "appearance" ? "searchAppearance" : dimension, top.key) : "No data available"}</p>
    {top ? <>
      <p className="mt-1 text-sm tabular-nums text-slate-600">{formatNumber(top.impressions)} impressions</p>
      {share != null ? <>
        <div className="my-2 h-1.5 overflow-hidden rounded-full bg-slate-100" role="img" aria-label={`${share.toFixed(1)}% of returned ${label.toLowerCase()} impressions`}><div className="h-full rounded-full bg-[#5746d9]" style={{width: `${share}%`}} /></div>
        <p className="text-xs text-slate-500">{share.toFixed(1)}% of returned impressions</p>
      </> : null}
      <div className="mt-1"><DeltaInline pct={slice.top_bucket_impressions_pct_vs_prior ?? null} /></div>
    </> : <p className="mt-1 text-xs text-slate-500">No stored rows for this period.</p>}
    {onExplore ? <a className="mt-3 inline-block text-xs font-medium text-[#5746d9] hover:underline" href="#overview-search-details" onClick={onExplore}>View {dimension === "country" ? "countries" : "devices"} →</a> : null}
  </div>;
}

export function CompletionBar({ label, complete, total, missing, href, issueHref }: {
  label: string; complete: number; total: number; missing: number; href: string; issueHref: string;
}) {
  const pct = total > 0 ? Math.min(100, Math.max(0, complete / total * 100)) : 0;
  return (
    <div className="overview-coverage-row">
      <div>
        <Link className="font-semibold text-slate-800 hover:text-[#5746d9] hover:underline" to={href}>{label}</Link>
        <p className="mt-1 text-xs text-slate-500">{formatNumber(complete)} of {formatNumber(total)} complete</p>
      </div>
      <div className="overview-coverage-progress">
        <div className="h-2 overflow-hidden rounded-full bg-slate-100" role="progressbar" aria-label={`${label} metadata coverage`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-valuetext={total > 0 ? `${pct.toFixed(1)}% complete` : "No synced entities"}>
          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: missing > 0 ? "#d97706" : "#059669" }} />
        </div>
        <span className="text-right text-sm tabular-nums text-slate-600">{total > 0 ? `${pct.toFixed(1)}%` : "—"}</span>
      </div>
      {missing > 0 ? <Link className="overview-coverage-status text-sm font-medium text-amber-800 underline-offset-4 hover:underline" to={issueHref}>{formatNumber(missing)} missing →</Link> : <span className="overview-coverage-status text-sm text-slate-500">{total > 0 ? "Complete" : "No data"}</span>}
    </div>
  );
}

export function IndexingSummary({ total, indexed, not_indexed, needs_review, unknown }: {
  total: number; indexed: number; not_indexed: number; needs_review: number; unknown: number;
}) {
  const segments = [
    { label: "Indexed", count: indexed, color: "#059669" },
    { label: "Not indexed", count: not_indexed, color: "#d97706" },
    { label: "Needs review", count: needs_review, color: "#7c3aed" },
    { label: "Unknown", count: unknown, color: "#94a3b8" }
  ];
  return (
    <div className="overview-panel p-5">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <p className="font-semibold text-slate-800">{formatNumber(total)} tracked URLs</p>
        <p className="text-sm text-slate-500">{total > 0 ? `${(indexed / total * 100).toFixed(1)}% indexed` : "No inspection data yet"}</p>
      </div>
      <div className="flex h-3 overflow-hidden rounded-full bg-slate-100" role="img" aria-label={total > 0 ? segments.map(s => `${s.label}: ${formatNumber(s.count)}`).join(", ") : "No tracked URLs"}>
        {segments.map(s => <span key={s.label} style={{ width: `${total > 0 ? s.count / total * 100 : 0}%`, background: s.color }} />)}
      </div>
      <dl className="overview-indexing-legend mt-5">
        {segments.map(s => <div key={s.label}>
          <dt className="flex items-center gap-2 text-sm text-slate-600"><span className="h-2 w-2 rounded-full" style={{ background: s.color }} aria-hidden />{s.label}</dt>
          <dd className="mt-1 text-xl font-semibold tabular-nums text-slate-800">{formatNumber(s.count)} <span className="text-xs font-normal text-slate-500">{total > 0 ? `${(s.count / total * 100).toFixed(1)}%` : "—"}</span></dd>
        </div>)}
      </dl>
    </div>
  );
}

export type AttentionItem = { label: string; count: number; href: string; action: string };
export function NeedsAttention({ items, hasCatalog }: { items: AttentionItem[]; hasCatalog: boolean }) {
  const active = items.filter(item => item.count > 0).sort((a, b) => b.count - a.count);
  return (
    <div className="overview-panel p-6">
      <h2 className="overview-section-title">Needs attention</h2>
      <p className="mt-1 text-sm text-slate-500">Missing metadata and short product descriptions in your synced catalog.</p>
      {active.length ? <ul className="mt-4 divide-y divide-slate-100">
        {active.map(item => <li key={item.label}><Link to={item.href} className="overview-attention-row group rounded-lg hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[#5746d9]">
          <span className="rounded-lg bg-amber-50 px-3 py-2 text-lg font-semibold tabular-nums text-amber-800">{formatNumber(item.count)}</span>
          <span className="min-w-0 text-sm font-medium text-slate-800">{item.label}</span>
          <span className="overview-attention-action text-sm font-medium text-[#5746d9]">{item.action} →</span>
        </Link></li>)}
      </ul> : <p className="mt-4 rounded-lg bg-slate-50 p-4 text-sm text-slate-600">{hasCatalog ? "No missing metadata or short product descriptions found in the synced catalog." : "Sync your catalog to check for missing metadata and short descriptions."}</p>}
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
