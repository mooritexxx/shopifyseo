import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";
import { z } from "zod";

import { getJson, postJson } from "../../lib/api";
import { Button } from "../ui/button";
import { Card } from "../ui/card";
import { MiniSparkline } from "../ui/mini-sparkline";

const siteAuthoritySchema = z.object({
  domain: z.string().default(""),
  found: z.boolean().default(false),
  authority: z.number().nullable().optional().default(null),
  rank: z.number().nullable().optional().default(null),
  referring_domains: z.number().nullable().optional().default(null),
  as_of: z.string().nullable().optional().default(null),
  checked_at: z.number().nullable().optional().default(null),
  history: z
    .array(z.object({ date: z.string(), authority: z.number(), estimated: z.boolean().default(false) }))
    .default([]),
  benchmark: z
    .object({
      scored_competitors: z.number().default(0),
      avg_authority: z.number().nullable().default(null),
      max_authority: z.number().nullable().default(null),
      top_domain: z.string().nullable().default(null)
    })
    .default({ scored_competitors: 0, avg_authority: null, max_authority: null, top_domain: null })
});

export function SiteAuthorityCard() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["site-authority"],
    staleTime: 300_000,
    queryFn: () => getJson("/api/site-authority", siteAuthoritySchema)
  });

  const refresh = useMutation({
    mutationFn: () => postJson("/api/site-authority/refresh", siteAuthoritySchema, {}),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["site-authority"] })
  });

  if (isLoading) {
    return <div className="h-40 animate-pulse rounded-[24px] bg-slate-100" />;
  }

  const bench = data?.benchmark;
  const history = data?.history ?? [];
  const series = history.map((h) => h.authority);
  const first = series.length > 0 ? series[0] : null;
  const last = series.length > 0 ? series[series.length - 1] : null;
  const delta = first !== null && last !== null ? last - first : null;

  return (
    <Card className="border-[#e8e4f8] bg-white p-5 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Link2 className="text-[#5746d9]" size={18} />
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Domain authority</p>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            Open PageRank score (0–10) for{" "}
            <span className="font-medium text-ink">{data?.domain || "your storefront"}</span>, from the Common
            Crawl open web graph.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
        >
          {refresh.isPending ? "Checking…" : "Refresh"}
        </Button>
      </div>

      {refresh.isError ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {(refresh.error as Error).message}
        </p>
      ) : null}

      {data?.found ? (
        <div className="mt-4 flex flex-wrap items-end gap-8">
          <div>
            <p className="text-3xl font-semibold tabular-nums text-ink">{data.authority?.toFixed(2)}</p>
            <p className="text-xs text-slate-500">
              {data.as_of ? `as of ${data.as_of}` : "current"}
              {delta !== null ? ` · ${delta >= 0 ? "+" : ""}${delta.toFixed(2)} since ${history[0]?.date}` : ""}
            </p>
          </div>
          <div>
            <p className="text-sm font-medium tabular-nums text-ink">
              {data.referring_domains?.toLocaleString() ?? "—"}
            </p>
            <p className="text-xs text-slate-500">referring domains</p>
          </div>
          <div>
            <p className="text-sm font-medium tabular-nums text-ink">
              {data.rank ? `#${data.rank.toLocaleString()}` : "—"}
            </p>
            <p className="text-xs text-slate-500">global rank</p>
          </div>
          {series.length > 1 ? (
            <div className="min-w-[140px] flex-1">
              <MiniSparkline values={series} color="#5746d9" ariaLabel="Domain authority trend" />
              <p className="mt-1 text-xs text-slate-500">{series.length} monthly points</p>
            </div>
          ) : null}
        </div>
      ) : (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-sm font-medium text-amber-900">Not in the Open PageRank index yet</p>
          <p className="mt-1 text-sm text-amber-800">
            Open PageRank has no entry for {data?.domain || "this domain"}, which means too few sites link to it
            for it to appear in the Common Crawl web graph. This is not a score of zero — there is no score. It
            will populate automatically once the domain is indexed, and the full monthly history back to 2018
            arrives with it.
          </p>
        </div>
      )}

      {bench && bench.scored_competitors > 0 ? (
        <p className="mt-4 border-t border-line/60 pt-3 text-xs text-slate-500">
          For context, across {bench.scored_competitors} scored competitors the average is{" "}
          <span className="font-medium text-slate-700">{bench.avg_authority?.toFixed(2)}</span> and the strongest
          is <span className="font-medium text-slate-700">{bench.top_domain}</span> at{" "}
          <span className="font-medium text-slate-700">{bench.max_authority?.toFixed(2)}</span>.
        </p>
      ) : null}
    </Card>
  );
}
