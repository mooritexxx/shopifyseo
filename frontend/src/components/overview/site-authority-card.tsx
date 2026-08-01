import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";

import { getJson, postJson } from "../../lib/api";
import { MiniSparkline } from "../ui/mini-sparkline";
import { KpiCard } from "./overview-cards";

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

/**
 * Compact Overview tile for Open PageRank domain authority.
 *
 * Renders "—" when Open PageRank has no entry for the domain. That is unknown,
 * not a score of zero, so no number is shown in that state.
 */
export function SiteAuthorityCard({ className }: { className?: string }) {
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
    return <div className={`h-28 animate-pulse rounded-2xl bg-slate-100 ${className ?? ""}`} />;
  }

  const series = (data?.history ?? []).map((h) => h.authority);
  const bench = data?.benchmark;

  const hint = data?.found ? (
    <>
      {data.referring_domains?.toLocaleString() ?? "—"} referring domains
      {bench?.avg_authority != null ? ` · peers avg ${bench.avg_authority.toFixed(2)}` : ""}
    </>
  ) : (
    <button
      type="button"
      onClick={() => refresh.mutate()}
      disabled={refresh.isPending}
      className="text-left underline-offset-2 hover:text-ocean hover:underline disabled:opacity-60"
      title={`Open PageRank has no entry for ${data?.domain || "this domain"} — too few referring domains to appear in the Common Crawl web graph. Not a score of zero. Click to re-check.`}
    >
      {refresh.isPending ? "Checking…" : "Not indexed yet — re-check"}
    </button>
  );

  return (
    <KpiCard
      className={className}
      label="Domain authority"
      value={data?.found && data.authority != null ? data.authority.toFixed(2) : "—"}
      sparkline={
        series.length > 1 ? (
          <MiniSparkline values={series} color="#5746d9" ariaLabel="Domain authority trend" />
        ) : undefined
      }
      hint={hint}
    />
  );
}
