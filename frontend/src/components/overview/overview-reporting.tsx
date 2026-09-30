import { formatRelativeTimestamp } from "../../lib/utils";

type Period = { start_date: string; end_date: string };

/** Calendar days, independent of local daylight-saving transitions. */
export function periodDays(period: Period): number {
  const start = Date.parse(`${period.start_date}T00:00:00Z`);
  const end = Date.parse(`${period.end_date}T00:00:00Z`);
  return Number.isFinite(start) && Number.isFinite(end) && end >= start ? (end - start) / 86_400_000 + 1 : 0;
}

export function PreviousPeriodComparison({ previous, checked, onChange }: {
  previous: Period | null; checked: boolean; onChange: (checked: boolean) => void;
}) {
  if (!previous || periodDays(previous) === 0) return null;
  return <div className="overview-comparison">
    <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-slate-600">
      <input type="checkbox" checked={checked} onChange={event => onChange(event.target.checked)} className="h-4 w-4 accent-[#5746d9]" />
      Compare previous-period averages
    </label>
    {checked ? <p className="text-xs text-slate-500">Dashed lines: previous-period averages ({previous.start_date} → {previous.end_date}), not daily history. Counts use daily averages; CTR and position use period values. Colors match each metric.</p> : null}
  </div>;
}

export function OverviewFreshness({ cache, timezone, anchorDate, source }: {
  cache: { label: string; text: string; meta?: unknown }; timezone: string; anchorDate: string; source: string;
}) {
  const meta = cache.meta && typeof cache.meta === "object" ? cache.meta as Record<string, unknown> : null;
  const raw = meta?.fetched_at;
  const timestamp = raw == null ? NaN : Number(raw);
  const relative = Number.isFinite(timestamp) && timestamp > 0 ? formatRelativeTimestamp(timestamp).split(" · ")[0] : null;
  return <details className="overview-freshness">
    <summary aria-label={`${source} data freshness`}>{cache.label || "Stored data"}{relative ? ` · refreshed ${relative}` : ""}</summary>
    <div className="mt-2 space-y-1 text-xs text-slate-500">
      <p>{cache.text}</p><p>Data through {anchorDate || "unknown"} · {timezone}</p>
    </div>
  </details>;
}
