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
