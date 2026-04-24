import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { defaultGscSegmentSummary, type GscSegmentSummary } from "../types/api";

function formatDimensionKind(k: string): string {
  if (k === "searchAppearance") return "Appearance";
  if (k === "country") return "Country";
  if (k === "device") return "Device";
  return k;
}

type Rollup = GscSegmentSummary["device_mix"][number];

function RollupColumn({ title, items }: { title: string; items: Rollup[] }) {
  if (!items.length) return null;
  return (
    <div className="min-w-0">
      <h4 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">{title}</h4>
      <ul className="mt-2 space-y-1.5 text-sm">
        {items.map((r) => (
          <li key={r.segment} className="flex justify-between gap-2 text-slate-700">
            <span className="truncate font-medium text-ink" title={r.segment}>
              {r.segment}
            </span>
            <span className="shrink-0 tabular-nums text-slate-500">
              {r.impressions.toLocaleString()} imp · {(r.share * 100).toFixed(1)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function GscSearchSegmentsSection({ summary }: { summary?: GscSegmentSummary | null }) {
  const s = summary ?? defaultGscSegmentSummary;
  const hasRollups =
    s.device_mix.length > 0 ||
    s.top_countries.length > 0 ||
    s.search_appearances.length > 0;
  const hasPairs = s.top_pairs.length > 0;

  const fetchedLabel =
    s.fetched_at != null && s.fetched_at > 0
      ? new Date(s.fetched_at * 1000).toLocaleString(undefined, {
          dateStyle: "medium",
          timeStyle: "short"
        })
      : null;

  return (
    <Card className="border-[#e2eaf4] bg-[linear-gradient(180deg,#ffffff_0%,#f8fbff_100%)]">
      <CardHeader className="pb-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <CardTitle className="text-base font-semibold text-ink">Search segments (Overview period)</CardTitle>
          {fetchedLabel ? (
            <span className="text-xs text-slate-500">Cached {fetchedLabel}</span>
          ) : null}
        </div>
        <p className="text-xs text-slate-500">
          Query performance split by device, country, and search appearance. This loads automatically when Search
          Console metrics are refreshed for this URL.
        </p>
      </CardHeader>
      <CardContent className="space-y-6 pt-0">
        {!hasRollups && !hasPairs ? (
          <p className="text-sm text-slate-600">No segmented GSC rows cached for this page yet.</p>
        ) : null}

        {hasRollups ? (
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
            <RollupColumn title="Device mix" items={s.device_mix} />
            <RollupColumn title="Top countries" items={s.top_countries} />
            <RollupColumn title="Search appearances" items={s.search_appearances} />
          </div>
        ) : null}

        {hasPairs ? (
          <div>
            <h4 className="text-xs font-semibold uppercase tracking-[0.18em] text-slate-500">Top query × segment pairs</h4>
            <div className="mt-2 overflow-x-auto rounded-lg border border-[#e8eef6]">
              <table className="w-full min-w-[520px] text-left text-sm">
                <thead className="bg-slate-50/80 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2 font-medium">Query</th>
                    <th className="px-3 py-2 font-medium">Segment</th>
                    <th className="px-3 py-2 font-medium text-right">Impr.</th>
                    <th className="px-3 py-2 font-medium text-right">Clicks</th>
                    <th className="px-3 py-2 font-medium text-right">Pos.</th>
                  </tr>
                </thead>
                <tbody>
                  {s.top_pairs.map((p, i) => (
                    <tr key={`${p.query}-${p.dimension_kind}-${p.dimension_value}-${i}`} className="border-t border-[#eef2f8]">
                      <td className="max-w-[200px] truncate px-3 py-2 text-slate-800" title={p.query}>
                        {p.query}
                      </td>
                      <td className="px-3 py-2 text-slate-600">
                        <span className="text-xs text-slate-400">{formatDimensionKind(p.dimension_kind)}</span>
                        <span className="ml-1">{p.dimension_value}</span>
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-700">{p.impressions.toLocaleString()}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-700">{p.clicks.toLocaleString()}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-slate-600">{p.position.toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
