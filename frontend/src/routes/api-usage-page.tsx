import { useQuery } from "@tanstack/react-query";
import { Activity, BarChart3, ChevronDown, DollarSign, Zap, Hash, TrendingUp } from "lucide-react";
import { useState } from "react";
import { z } from "zod";

import { getJson } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";

const periodSchema = z.object({
  total_cost: z.number(),
  total_calls: z.number(),
  total_input_tokens: z.number(),
  total_output_tokens: z.number(),
});

const breakdownRowSchema = z.object({
  calls: z.number(),
  input_tokens: z.number(),
  output_tokens: z.number(),
  cost: z.number(),
});

const recentCallRowSchema = z.object({
  id: z.number(),
  provider: z.string(),
  model: z.string(),
  call_type: z.string(),
  stage: z.string(),
  input_tokens: z.number(),
  output_tokens: z.number(),
  total_tokens: z.number(),
  estimated_cost_usd: z.number(),
  created_at: z.string(),
});

const usageSummarySchema = z.object({
  periods: z.object({
    today: periodSchema,
    last_7d: periodSchema,
    last_30d: periodSchema,
    all_time: periodSchema,
  }),
  by_model: z.array(breakdownRowSchema.extend({ model: z.string() })),
  by_call_type: z.array(breakdownRowSchema.extend({ call_type: z.string() })),
  by_process: z.array(breakdownRowSchema.extend({ process: z.string() })),
  by_stage: z.array(breakdownRowSchema.extend({ stage: z.string() })),
  daily: z.array(z.object({ day: z.string(), calls: z.number(), cost: z.number() })),
  recent: z.array(recentCallRowSchema),
  days: z.number(),
  seo: z.object({
    periods: z.object({
      today: periodSchema,
      last_7d: periodSchema,
      last_30d: periodSchema,
      all_time: periodSchema,
    }),
    daily: z.array(z.object({ day: z.string(), calls: z.number(), cost: z.number() })),
    by_endpoint: z.array(
      z.object({
        endpoint: z.string(),
        calls: z.number(),
        cost: z.number(),
      })
    ),
    recent: z.array(recentCallRowSchema),
  }),
});

type UsageSummary = z.infer<typeof usageSummarySchema>;

function usd(amount: number): string {
  if (amount >= 1) return `$${amount.toFixed(2)}`;
  if (amount >= 0.01) return `$${amount.toFixed(3)}`;
  if (amount >= 0.001) return `$${amount.toFixed(4)}`;
  if (amount === 0) return "$0.00";
  return `$${amount.toFixed(5)}`;
}

function shortNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

const CALL_TYPE_LABELS: Record<string, string> = {
  chat: "Chat / Completions",
  embedding: "Embeddings",
  image: "Image Generation",
  vision: "Vision / Alt Text",
};

const STAGE_LABELS: Record<string, string> = {
  generation: "SEO Generation",
  review: "Content Review",
  sidekick: "Sidekick Chat",
  embedding_sync: "Embedding Sync",
  image_generation: "Image Generation",
  vision_caption: "Vision Caption",
  keyword_clustering: "Keyword Clustering",
};

function DailyChart({
  daily,
  barClassName = "from-blue-500 to-cyan-400 group-hover:from-blue-600 group-hover:to-cyan-300",
}: {
  daily: UsageSummary["daily"];
  barClassName?: string;
}) {
  if (!daily.length) {
    return (
      <div className="flex h-40 items-center justify-center text-sm text-slate-400">
        No usage data yet
      </div>
    );
  }

  const maxCost = Math.max(...daily.map((d) => d.cost), 0.001);

  return (
    <div className="flex items-end gap-1" style={{ height: 160 }}>
      {daily.map((d) => {
        const pct = Math.max(2, (d.cost / maxCost) * 100);
        return (
          <div key={d.day} className="group relative flex flex-1 flex-col items-center justify-end" style={{ height: "100%" }}>
            <div
              className={`w-full min-w-[4px] rounded-t-md bg-gradient-to-t transition-all ${barClassName}`}
              style={{ height: `${pct}%` }}
            />
            <div className="pointer-events-none absolute -top-16 left-1/2 z-10 hidden -translate-x-1/2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs shadow-lg group-hover:block">
              <p className="font-semibold text-ink">{usd(d.cost)}</p>
              <p className="text-slate-500">{d.calls} calls</p>
              <p className="text-slate-400">{d.day}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function SeoPeriodCard({
  label,
  period,
  icon: Icon,
}: {
  label: string;
  period: z.infer<typeof periodSchema>;
  icon: typeof DollarSign;
}) {
  return (
    <Card className="rounded-[26px] shadow-panel">
      <CardContent className="p-5">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-slate-400" />
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</p>
        </div>
        <strong className="mt-3 block text-3xl font-bold text-ink">{usd(period.total_cost)}</strong>
        <p className="mt-3 text-sm text-slate-600">{period.total_calls.toLocaleString()} API calls</p>
      </CardContent>
    </Card>
  );
}

function PeriodCard({
  label,
  period,
  icon: Icon,
}: {
  label: string;
  period: z.infer<typeof periodSchema>;
  icon: typeof DollarSign;
}) {
  return (
    <Card className="rounded-[26px] shadow-panel">
      <CardContent className="p-5">
        <div className="flex items-center gap-2">
          <Icon size={14} className="text-slate-400" />
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</p>
        </div>
        <strong className="mt-3 block text-3xl font-bold text-ink">{usd(period.total_cost)}</strong>
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-600">
          <span>{period.total_calls.toLocaleString()} calls</span>
          <span>{shortNumber(period.total_input_tokens)} in</span>
          <span>{shortNumber(period.total_output_tokens)} out</span>
        </div>
      </CardContent>
    </Card>
  );
}

function StageDetail({ stages }: { stages: UsageSummary["by_stage"] }) {
  const [open, setOpen] = useState(false);
  return (
    <Card className="rounded-[26px] shadow-panel">
      <button
        type="button"
        className="flex w-full items-center justify-between px-6 py-4 text-left"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="text-sm font-medium text-slate-600">Raw stage detail</span>
        <ChevronDown
          size={16}
          className={`text-slate-400 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <CardContent className="px-0 pb-4 pt-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Stage</TableHead>
                <TableHead className="text-right">Calls</TableHead>
                <TableHead className="text-right">Input tokens</TableHead>
                <TableHead className="text-right">Output tokens</TableHead>
                <TableHead className="text-right">Cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {stages.map((row) => (
                <TableRow key={row.stage || "(empty)"}>
                  <TableCell className="font-mono text-xs">{row.stage || "(unknown)"}</TableCell>
                  <TableCell className="text-right">{row.calls.toLocaleString()}</TableCell>
                  <TableCell className="text-right">{shortNumber(row.input_tokens)}</TableCell>
                  <TableCell className="text-right">{shortNumber(row.output_tokens)}</TableCell>
                  <TableCell className="text-right font-semibold">{usd(row.cost)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      )}
    </Card>
  );
}

export function ApiUsagePage() {
  const [days, setDays] = useState(30);

  const { data, isLoading, error } = useQuery({
    queryKey: ["api-usage-summary", days],
    queryFn: () => getJson(`/api/usage/summary?days=${days}`, usageSummarySchema),
    refetchInterval: 60_000,
  });

  if (error) {
    return (
      <div className="rounded-[30px] border border-white/70 bg-white/90 p-8 shadow-panel">
        <p className="text-red-600">Failed to load usage data: {(error as Error).message}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="rounded-[30px] border border-white/70 bg-white/90 p-8 shadow-panel">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Activity size={22} className="text-slate-600" />
            <h1 className="text-2xl font-bold text-ink">API Usage</h1>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs uppercase tracking-wider text-slate-400">Period</span>
            {[7, 30, 90].map((d) => (
              <button
                key={d}
                onClick={() => setDays(d)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition ${
                  days === d
                    ? "bg-slate-800 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>

        {isLoading || !data ? (
          <div className="mt-6 space-y-8">
            <div className="space-y-4">
              <Skeleton className="h-6 w-48 rounded-md" />
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-28 rounded-[26px]" />
                ))}
              </div>
              <Skeleton className="h-48 rounded-[26px]" />
            </div>
            <div className="space-y-4">
              <Skeleton className="h-6 w-56 rounded-md" />
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={`seo-${i}`} className="h-28 rounded-[26px]" />
                ))}
              </div>
              <Skeleton className="h-48 rounded-[26px]" />
            </div>
          </div>
        ) : (
          <div className="mt-6 space-y-10">
            <div className="space-y-6">
              <h2 className="text-lg font-semibold text-ink">Gemini / LLM</h2>
              {/* Period summary cards */}
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <PeriodCard label="Today" period={data.periods.today} icon={DollarSign} />
                <PeriodCard label="Last 7 days" period={data.periods.last_7d} icon={TrendingUp} />
                <PeriodCard label="Last 30 days" period={data.periods.last_30d} icon={Zap} />
                <PeriodCard label="All time" period={data.periods.all_time} icon={Hash} />
              </div>

              {/* Daily cost chart */}
              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">
                    Daily spend — Gemini / LLM (last {days} days)
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5 pb-5">
                  <DailyChart daily={data.daily} />
                {data.daily.length > 0 && (
                  <div className="mt-2 flex justify-between text-[10px] text-slate-400">
                    <span>{data.daily[0]?.day}</span>
                    <span>{data.daily[data.daily.length - 1]?.day}</span>
                  </div>
                )}
                </CardContent>
              </Card>

              {/* Breakdown tables */}
              <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              {/* By model */}
              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">
                    Spend by model
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-4">
                  {data.by_model.length === 0 ? (
                    <p className="px-5 text-sm text-slate-400">No data</p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Model</TableHead>
                          <TableHead className="text-right">Calls</TableHead>
                          <TableHead className="text-right">In tokens</TableHead>
                          <TableHead className="text-right">Out tokens</TableHead>
                          <TableHead className="text-right">Cost</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.by_model.map((row) => (
                          <TableRow key={row.model}>
                            <TableCell className="font-mono text-xs">{row.model}</TableCell>
                            <TableCell className="text-right">{row.calls.toLocaleString()}</TableCell>
                            <TableCell className="text-right">{shortNumber(row.input_tokens)}</TableCell>
                            <TableCell className="text-right">{shortNumber(row.output_tokens)}</TableCell>
                            <TableCell className="text-right font-semibold">{usd(row.cost)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>

              {/* By call type */}
              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">
                    Spend by type
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-4">
                  {data.by_call_type.length === 0 ? (
                    <p className="px-5 text-sm text-slate-400">No data</p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Type</TableHead>
                          <TableHead className="text-right">Calls</TableHead>
                          <TableHead className="text-right">In tokens</TableHead>
                          <TableHead className="text-right">Out tokens</TableHead>
                          <TableHead className="text-right">Cost</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.by_call_type.map((row) => (
                          <TableRow key={row.call_type}>
                            <TableCell>{CALL_TYPE_LABELS[row.call_type] || row.call_type}</TableCell>
                            <TableCell className="text-right">{row.calls.toLocaleString()}</TableCell>
                            <TableCell className="text-right">{shortNumber(row.input_tokens)}</TableCell>
                            <TableCell className="text-right">{shortNumber(row.output_tokens)}</TableCell>
                            <TableCell className="text-right font-semibold">{usd(row.cost)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* By process */}
            {data.by_process.length > 0 && (
              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">
                    Spend by process
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-4">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Process</TableHead>
                        <TableHead className="text-right">Calls</TableHead>
                        <TableHead className="text-right">Input tokens</TableHead>
                        <TableHead className="text-right">Output tokens</TableHead>
                        <TableHead className="text-right">Cost</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {data.by_process.map((row) => (
                        <TableRow key={row.process}>
                          <TableCell className="font-medium">{row.process}</TableCell>
                          <TableCell className="text-right">{row.calls.toLocaleString()}</TableCell>
                          <TableCell className="text-right">{shortNumber(row.input_tokens)}</TableCell>
                          <TableCell className="text-right">{shortNumber(row.output_tokens)}</TableCell>
                          <TableCell className="text-right font-semibold">{usd(row.cost)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}

            {/* Raw stage detail (collapsible) */}
            {data.by_stage.length > 0 && (
              <StageDetail stages={data.by_stage} />
            )}

              {/* Recent calls log */}
              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">
                    Recent Gemini / LLM calls
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-4">
                  {data.recent.length === 0 ? (
                    <p className="px-5 text-sm text-slate-400">No calls logged yet</p>
                  ) : (
                    <div className="max-h-[400px] overflow-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Time</TableHead>
                            <TableHead>Model</TableHead>
                            <TableHead>Type</TableHead>
                            <TableHead>Stage</TableHead>
                            <TableHead className="text-right">In</TableHead>
                            <TableHead className="text-right">Out</TableHead>
                            <TableHead className="text-right">Cost</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {data.recent.map((row) => (
                            <TableRow key={row.id}>
                              <TableCell className="whitespace-nowrap text-xs text-slate-500">
                                {new Date(row.created_at + "Z").toLocaleString(undefined, {
                                  month: "short",
                                  day: "numeric",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </TableCell>
                              <TableCell className="font-mono text-xs">{row.model}</TableCell>
                              <TableCell className="text-xs">
                                {CALL_TYPE_LABELS[row.call_type] || row.call_type}
                              </TableCell>
                              <TableCell className="text-xs text-slate-500">
                                {STAGE_LABELS[row.stage] || row.stage || "—"}
                              </TableCell>
                              <TableCell className="text-right text-xs">
                                {shortNumber(row.input_tokens)}
                              </TableCell>
                              <TableCell className="text-right text-xs">
                                {shortNumber(row.output_tokens)}
                              </TableCell>
                              <TableCell className="text-right text-xs font-semibold">
                                {usd(row.estimated_cost_usd)}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            <div className="space-y-6 border-t border-slate-200/80 pt-10">
              <div className="flex items-center gap-3">
                <BarChart3 size={22} className="text-emerald-700" />
                <h2 className="text-lg font-semibold text-ink">DataForSEO (SEO API)</h2>
              </div>
              <p className="text-sm text-slate-600">
                Costs reflect DataForSEO&apos;s billed amount per response (keyword research, competitors,
                SERP, etc.).
              </p>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <SeoPeriodCard label="Today" period={data.seo.periods.today} icon={DollarSign} />
                <SeoPeriodCard label="Last 7 days" period={data.seo.periods.last_7d} icon={TrendingUp} />
                <SeoPeriodCard label="Last 30 days" period={data.seo.periods.last_30d} icon={Zap} />
                <SeoPeriodCard label="All time" period={data.seo.periods.all_time} icon={Hash} />
              </div>

              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">
                    Daily spend — DataForSEO (last {days} days)
                  </CardTitle>
                </CardHeader>
                <CardContent className="px-5 pb-5">
                  <DailyChart
                    daily={data.seo.daily}
                    barClassName="from-emerald-600 to-teal-400 group-hover:from-emerald-700 group-hover:to-teal-300"
                  />
                  {data.seo.daily.length > 0 && (
                    <div className="mt-2 flex justify-between text-[10px] text-slate-400">
                      <span>{data.seo.daily[0]?.day}</span>
                      <span>{data.seo.daily[data.seo.daily.length - 1]?.day}</span>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">Spend by endpoint</CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-4">
                  {data.seo.by_endpoint.length === 0 ? (
                    <p className="px-5 text-sm text-slate-400">No DataForSEO calls logged yet</p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Endpoint</TableHead>
                          <TableHead className="text-right">Calls</TableHead>
                          <TableHead className="text-right">Cost</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {data.seo.by_endpoint.map((row) => (
                          <TableRow key={row.endpoint}>
                            <TableCell className="max-w-[280px] truncate font-mono text-xs" title={row.endpoint}>
                              {row.endpoint}
                            </TableCell>
                            <TableCell className="text-right">{row.calls.toLocaleString()}</TableCell>
                            <TableCell className="text-right font-semibold">{usd(row.cost)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>

              <Card className="rounded-[26px] shadow-panel">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-slate-600">Recent DataForSEO calls</CardTitle>
                </CardHeader>
                <CardContent className="px-0 pb-4">
                  {data.seo.recent.length === 0 ? (
                    <p className="px-5 text-sm text-slate-400">No calls logged yet</p>
                  ) : (
                    <div className="max-h-[400px] overflow-auto">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Time</TableHead>
                            <TableHead>Endpoint</TableHead>
                            <TableHead className="text-right">Cost</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {data.seo.recent.map((row) => (
                            <TableRow key={row.id}>
                              <TableCell className="whitespace-nowrap text-xs text-slate-500">
                                {new Date(row.created_at + "Z").toLocaleString(undefined, {
                                  month: "short",
                                  day: "numeric",
                                  hour: "2-digit",
                                  minute: "2-digit",
                                })}
                              </TableCell>
                              <TableCell className="max-w-[320px] truncate font-mono text-xs" title={row.model}>
                                {row.model}
                              </TableCell>
                              <TableCell className="text-right text-xs font-semibold">
                                {usd(row.estimated_cost_usd)}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
