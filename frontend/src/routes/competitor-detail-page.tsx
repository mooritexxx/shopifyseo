import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { getJson } from "../lib/api";
import { Skeleton } from "../components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";

const profileSchema = z.object({
  domain: z.string(),
  keywords_common: z.number(),
  keywords_they_have: z.number(),
  keywords_we_have: z.number(),
  share: z.number(),
  traffic: z.number(),
  is_manual: z.number(),
  updated_at: z.number(),
});

/** SQLite / API may emit null for numeric columns; coerce so the page does not fail Zod parse. */
const intField = z.union([z.number(), z.null()]).transform((v) => v ?? 0);

const topPageSchema = z.object({
  url: z.string(),
  top_keyword: z.string(),
  top_keyword_volume: intField,
  top_keyword_position: intField,
  total_keywords: intField,
  estimated_traffic: intField,
  traffic_value: intField,
  page_type: z.string(),
});

const gapSchema = z.object({
  keyword: z.string(),
  competitor_position: z.number().nullable(),
  competitor_url: z.string().nullable(),
  our_ranking_status: z.string(),
  our_gsc_position: z.number().nullable(),
  volume: intField,
  difficulty: intField,
  traffic_potential: intField,
  gap_type: z.string(),
});

const detailSchema = z.object({
  profile: profileSchema,
  top_pages: z.array(topPageSchema),
  keyword_gaps: z.array(gapSchema),
});

function fmt(n: number | undefined | null): string {
  if (n == null) return "—";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 text-center">
      <div className="text-2xl font-bold tabular-nums text-ink">{typeof value === "number" ? fmt(value) : value}</div>
      <div className="mt-1 text-xs font-medium uppercase tracking-wider text-slate-400">{label}</div>
    </div>
  );
}

export function CompetitorDetailPage() {
  const { domain } = useParams<{ domain: string }>();
  const decodedDomain = decodeURIComponent(domain ?? "");

  const query = useQuery({
    queryKey: ["competitor-detail", decodedDomain],
    queryFn: () => getJson(`/api/keywords/competitors/${encodeURIComponent(decodedDomain)}/detail`, detailSchema),
    enabled: !!decodedDomain,
  });

  if (!decodedDomain) {
    return <div className="p-8 text-center text-slate-500">No domain specified.</div>;
  }

  const data = query.data;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link to="/keywords" className="rounded-full p-2 text-slate-400 transition hover:bg-slate-100">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-xl font-semibold text-ink">{decodedDomain}</h1>
          <p className="text-sm text-slate-500">Competitor Intelligence</p>
        </div>
      </div>

      {query.isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-24 rounded-xl" />
          <Skeleton className="h-64 rounded-xl" />
        </div>
      ) : query.isError ? (
        <div className="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700">
          Failed to load competitor data. Run keyword research first to populate competitor profiles.
        </div>
      ) : data ? (
        <>
          {/* Profile stats */}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard label="Est. Traffic" value={data.profile.traffic} />
            <StatCard label="Common Keywords" value={data.profile.keywords_common} />
            <StatCard label="Their Keywords" value={data.profile.keywords_they_have} />
            <StatCard label="Keyword Share" value={`${(data.profile.share * 100).toFixed(1)}%`} />
          </div>

          {/* Top pages */}
          <div className="rounded-[24px] border border-line/80 bg-white p-5">
            <h2 className="text-lg font-semibold text-ink">Top Pages</h2>
            <p className="mt-1 text-sm text-slate-500">
              Pages driving the most organic traffic for this competitor.
            </p>
            {data.top_pages.length === 0 ? (
              <div className="mt-4 rounded-xl border-2 border-dashed border-slate-200 py-8 text-center text-sm text-slate-400">
                No top pages data yet. Run keyword research to collect this data.
              </div>
            ) : (
              <div className="mt-4">
                <Table className="w-full text-sm">
                  <TableHeader>
                    <TableRow className="border-b border-line text-left text-xs font-medium uppercase tracking-wider text-slate-400">
                      <TableHead className="pb-2 pr-4">URL</TableHead>
                      <TableHead className="pb-2 pr-4">Top Keyword</TableHead>
                      <TableHead className="pb-2 pr-4 text-right">Volume</TableHead>
                      <TableHead className="pb-2 pr-4 text-right">Position</TableHead>
                      <TableHead className="pb-2 pr-4 text-right">Keywords</TableHead>
                      <TableHead className="pb-2 text-right">Traffic</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.top_pages.map((page) => (
                      <TableRow key={page.url} className="border-b border-line/50">
                        <TableCell className="max-w-[280px] truncate py-2.5 pr-4 text-slate-600" title={page.url}>
                          {page.url.replace(/^https?:\/\/[^/]+/, "")}
                        </TableCell>
                        <TableCell className="py-2.5 pr-4 font-medium text-ink">{page.top_keyword || "—"}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{fmt(page.top_keyword_volume)}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{page.top_keyword_position || "—"}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{fmt(page.total_keywords)}</TableCell>
                        <TableCell className="py-2.5 text-right tabular-nums text-slate-600">{fmt(page.estimated_traffic)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>

          {/* Keyword gaps */}
          <div className="rounded-[24px] border border-line/80 bg-white p-5">
            <h2 className="text-lg font-semibold text-ink">Keyword Gaps</h2>
            <p className="mt-1 text-sm text-slate-500">
              Keywords this competitor ranks for that you don't — sorted by search volume.
            </p>
            {data.keyword_gaps.length === 0 ? (
              <div className="mt-4 rounded-xl border-2 border-dashed border-slate-200 py-8 text-center text-sm text-slate-400">
                No keyword gap data yet. Run keyword research to detect gaps.
              </div>
            ) : (
              <div className="mt-4">
                <Table className="w-full text-sm">
                  <TableHeader>
                    <TableRow className="border-b border-line text-left text-xs font-medium uppercase tracking-wider text-slate-400">
                      <TableHead className="pb-2 pr-4">Keyword</TableHead>
                      <TableHead className="pb-2 pr-4 text-right">Volume</TableHead>
                      <TableHead className="pb-2 pr-4 text-right">Difficulty</TableHead>
                      <TableHead className="pb-2 pr-4 text-right">Their Pos.</TableHead>
                      <TableHead className="pb-2 pr-4 text-center">Our Status</TableHead>
                      <TableHead className="pb-2 text-center">Gap Type</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.keyword_gaps.map((gap) => (
                      <TableRow key={gap.keyword} className="border-b border-line/50">
                        <TableCell className="py-2.5 pr-4 font-medium text-ink">{gap.keyword}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{fmt(gap.volume)}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{gap.difficulty}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-right tabular-nums text-slate-600">{gap.competitor_position ?? "—"}</TableCell>
                        <TableCell className="py-2.5 pr-4 text-center">
                          {gap.our_ranking_status === "not_ranking" ? (
                            <span className="inline-block rounded-full bg-red-50 px-2 py-0.5 text-xs font-medium text-red-600">Not Ranking</span>
                          ) : gap.our_ranking_status === "ranking_lower" ? (
                            <span className="inline-block rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-600">Lower</span>
                          ) : (
                            <span className="inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-500">{gap.our_ranking_status}</span>
                          )}
                        </TableCell>
                        <TableCell className="py-2.5 text-center">
                          <span className="inline-block rounded-full bg-violet-50 px-2 py-0.5 text-xs font-medium text-violet-600">
                            {gap.gap_type.replace(/_/g, " ")}
                          </span>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
