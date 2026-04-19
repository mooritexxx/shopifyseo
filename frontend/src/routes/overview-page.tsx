import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight, FileSearch, Globe, Layers, Monitor, MousePointerClick, TrendingUp } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { summarySchema } from "../types/api";

import { GscPerformanceSection } from "../components/gsc/gsc-performance-section";
import {
  CompletionBar,
  DeltaInline,
  KpiCard,
  SegmentMixTile,
  overviewCacheHint
} from "../components/overview/overview-cards";
import { OverviewOnboarding, overviewShowsOnboarding } from "../components/overview/overview-onboarding";
import {
  CHART_GRID,
  CHART_META_COMPLETE,
  CHART_MISSING_META,
  CHART_PRIMARY,
  CHART_THIN_BODY,
  CHART_TOOLTIP_STYLE,
  ENTITY_TYPE_COLORS,
  ENTITY_TYPE_LABELS,
  GA4_CHART_SESSIONS,
  GA4_CHART_VIEWS,
  GSC_SEGMENT_OPTIONS,
  OVERVIEW_GSC_PERIOD_OPTIONS,
  entityAppPath,
  formatChartAxisDate
} from "../components/overview/overview-theme";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { MiniSparkline } from "../components/ui/mini-sparkline";
import { getJson } from "../lib/api";
import {
  persistOverviewGscPeriod,
  readStoredOverviewGscPeriod,
  type OverviewGscPeriod
} from "../lib/gsc-period";
import { cn, formatDurationSeconds, formatNumber, formatPercent } from "../lib/utils";

type GscChartTab = "traffic" | "ctr_position";

export function OverviewPage() {
  const [gscOverviewPeriod, setGscOverviewPeriod] = useState<OverviewGscPeriod>(() => readStoredOverviewGscPeriod());
  const [gscSegment, setGscSegment] = useState<(typeof GSC_SEGMENT_OPTIONS)[number]["value"]>("all");
  const [gscChartTab, setGscChartTab] = useState<GscChartTab>("traffic");
  const { data, isLoading, error } = useQuery({
    queryKey: ["summary", gscOverviewPeriod, gscSegment],
    staleTime: 60_000,
    queryFn: () =>
      getJson(
        `/api/summary?gsc_period=${gscOverviewPeriod}&gsc_segment=${encodeURIComponent(gscSegment)}`,
        summarySchema
      )
  });

  const catalogChartData = useMemo(() => {
    if (!data) return [];
    const cc = data.catalog_completion;
    return [
      {
        name: "Products",
        meta_complete: cc.products.meta_complete,
        missing_meta: cc.products.missing_meta,
        thin_body: cc.products.thin_body
      },
      {
        name: "Collections",
        meta_complete: cc.collections.meta_complete,
        missing_meta: cc.collections.missing_meta,
        thin_body: 0
      },
      {
        name: "Pages",
        meta_complete: cc.pages.meta_complete,
        missing_meta: cc.pages.missing_meta,
        thin_body: 0
      },
      {
        name: "Articles",
        meta_complete: cc.articles.meta_complete,
        missing_meta: cc.articles.missing_meta,
        thin_body: 0
      }
    ];
  }, [data]);

  const gscSeries = data?.gsc_site?.series;
  const ga4Series = data?.ga4_site?.series;
  const gscLineData = useMemo(() => gscSeries ?? [], [gscSeries]);
  const ga4LineData = useMemo(() => ga4Series ?? [], [ga4Series]);
  const gscSparkClicks = useMemo(() => (gscSeries ?? []).map((d) => d.clicks), [gscSeries]);
  const gscSparkImpressions = useMemo(() => (gscSeries ?? []).map((d) => d.impressions), [gscSeries]);
  const gscSparkCtrPct = useMemo(
    () => (gscSeries ?? []).map((d) => (d.impressions > 0 ? (d.clicks / d.impressions) * 100 : 0)),
    [gscSeries]
  );
  const ga4SparkSessions = useMemo(() => (ga4Series ?? []).map((d) => d.sessions), [ga4Series]);
  const ga4SparkViews = useMemo(() => (ga4Series ?? []).map((d) => d.views), [ga4Series]);
  const ga4SparkVps = useMemo(
    () => (ga4Series ?? []).map((d) => (d.sessions > 0 ? d.views / d.sessions : 0)),
    [ga4Series]
  );

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-28 animate-pulse rounded-2xl bg-slate-100" />
          ))}
        </div>
        <div className="h-80 animate-pulse rounded-[24px] bg-slate-100" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">
        {(error as Error)?.message || "Could not load overview."}
      </div>
    );
  }

  if (overviewShowsOnboarding(data)) {
    return <OverviewOnboarding data={data} />;
  }

  const impressions = data.metrics.gsc_impressions;
  const clicks = data.metrics.gsc_clicks;
  const ctrFraction = impressions > 0 ? clicks / impressions : 0;

  const gsc = data.gsc_site;
  const siteCur = gsc.available ? gsc.current : null;
  const siteCtr = siteCur && siteCur.impressions > 0 ? siteCur.clicks / siteCur.impressions : 0;

  const ga4 = data.ga4_site;
  const ga4Cur = ga4.available ? ga4.current : null;

  const idx = data.indexing_rollup;
  const idxTotal = idx.total;
  const idxPctIndexed = idxTotal > 0 ? (idx.indexed / idxTotal) * 100 : 0;

  const goals = data.overview_goals;

  return (
    <div className="space-y-8 pb-8">
      {/* Site-level GSC — property totals + trend (Phase 1) */}
      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Search Console (property)</p>
            <p className="text-sm text-slate-600">
              {gsc.available && siteCur
                ? `${siteCur.start_date} → ${siteCur.end_date} · timezone ${gsc.timezone} · data through ${gsc.anchor_date}`
                : "Connect Google and pick a Search Console property in Settings → Data sources to load site-level GSC."}
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <div className="flex flex-wrap gap-1 rounded-lg border border-[#e8e4f8] bg-white p-1">
              {OVERVIEW_GSC_PERIOD_OPTIONS.map(({ value, label }) => (
                <Button
                  key={value}
                  type="button"
                  variant="ghost"
                  onClick={() => {
                    setGscOverviewPeriod(value);
                    persistOverviewGscPeriod(value);
                  }}
                  className={cn(
                    "h-auto rounded-md px-3 py-1.5 text-sm font-medium transition",
                    gscOverviewPeriod === value
                      ? "bg-[#5746d9] text-white hover:bg-[#5746d9]/90"
                      : "text-slate-600 hover:bg-slate-100"
                  )}
                >
                  {label}
                </Button>
              ))}
            </div>
            <div className="flex max-w-full flex-wrap justify-end gap-1 rounded-lg border border-[#e8e4f8] bg-white p-1">
              <span className="self-center px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                URL path
              </span>
              {GSC_SEGMENT_OPTIONS.map(({ value, label }) => (
                <Button
                  key={value}
                  type="button"
                  variant="ghost"
                  onClick={() => setGscSegment(value)}
                  className={cn(
                    "h-auto rounded-md px-2.5 py-1.5 text-xs font-medium transition",
                    gscSegment === value
                      ? "bg-[#5746d9] text-white hover:bg-[#5746d9]/90"
                      : "text-slate-600 hover:bg-slate-100"
                  )}
                >
                  {label}
                </Button>
              ))}
            </div>
          </div>
        </div>

        {!gsc.available ? (
          <Card className="border-[#e8e4f8] bg-[#faf8ff] p-6">
            <p className="text-sm font-medium text-ink">Site-level GSC not available</p>
            <p className="mt-2 text-sm text-slate-600">{gsc.error || "Connect Google Search Console to see property rollups."}</p>
            <Link
              className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-[#5746d9] hover:underline"
              to="/settings?tab=data-sources"
            >
              Open Search Console settings
              <ArrowRight size={14} />
            </Link>
          </Card>
        ) : (
          <>
            <div
              className="flex gap-4 overflow-x-auto pb-1 max-sm:snap-x max-sm:snap-mandatory sm:grid sm:grid-cols-2 sm:overflow-visible lg:grid-cols-5"
              role="group"
              aria-label="Search Console KPIs"
            >
              <KpiCard
                className="min-w-[220px] shrink-0 sm:min-w-0"
                label="GSC clicks"
                value={formatNumber(siteCur?.clicks ?? 0)}
                sparkline={
                  <MiniSparkline
                    values={gscSparkClicks}
                    color={CHART_PRIMARY}
                    ariaLabel="Daily Search Console clicks in the selected period"
                  />
                }
                hint={
                  <span>
                    Property total
                    <DeltaInline pct={gsc.deltas.clicks_pct ?? null} />
                  </span>
                }
              />
              <KpiCard
                className="min-w-[220px] shrink-0 sm:min-w-0"
                label="GSC impressions"
                value={formatNumber(siteCur?.impressions ?? 0)}
                sparkline={
                  <MiniSparkline
                    values={gscSparkImpressions}
                    color="#94a3b8"
                    ariaLabel="Daily Search Console impressions in the selected period"
                  />
                }
                hint={
                  <span>
                    Property total
                    <DeltaInline pct={gsc.deltas.impressions_pct ?? null} />
                  </span>
                }
              />
              <KpiCard
                className="min-w-[220px] shrink-0 sm:min-w-0"
                label="Avg CTR"
                value={formatPercent(siteCtr)}
                sparkline={
                  <MiniSparkline
                    values={gscSparkCtrPct}
                    color="#7c6fd6"
                    ariaLabel="Daily average click-through rate in the selected period"
                  />
                }
                hint="Clicks ÷ impressions (property)"
              />
              <KpiCard
                className="min-w-[220px] shrink-0 sm:min-w-0"
                label="Avg position"
                value={
                  siteCur?.position != null && siteCur.position > 0 ? siteCur.position.toFixed(1) : "—"
                }
                hint={
                  <span>
                    Impression-weighted
                    <DeltaInline pct={gsc.deltas.position_improvement_pct ?? null} />
                  </span>
                }
              />
              <KpiCard
                className="min-w-[220px] shrink-0 sm:min-w-0"
                label="Cache"
                value={gsc.cache.label || "—"}
                hint={overviewCacheHint(gsc.cache)}
              />
            </div>

            {data.gsc_property_breakdowns.available ? (
              <Card className="mt-4 border-[#e8e4f8] bg-white p-6 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
                <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
                  <div className="flex min-w-0 items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-[#f4f2ff] text-[#5746d9] shadow-[0_2px_8px_rgba(87,70,217,0.12)]">
                      <Layers size={22} strokeWidth={1.75} aria-hidden />
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Segment mix</p>
                      <h3 className="mt-1 text-lg font-bold tracking-tight text-ink">Property splits (cached)</h3>
                      <p className="mt-1.5 text-sm leading-relaxed text-slate-500">
                        <span className="font-medium text-slate-600">
                          {data.gsc_property_breakdowns.window.start_date} →{" "}
                          {data.gsc_property_breakdowns.window.end_date}
                        </span>
                        {data.gsc_property_breakdowns.period_mode
                          ? ` · ${data.gsc_property_breakdowns.period_mode.replace(/_/g, " ")}`
                          : ""}
                        . Highest-impression bucket per dimension from the Tier A GSC cache — no extra API call on load.
                      </p>
                    </div>
                  </div>
                  <Link
                    className="inline-flex shrink-0 items-center gap-1.5 self-start rounded-lg border border-[#e8e4f8] bg-[#faf8ff] px-3 py-2 text-xs font-semibold text-[#5746d9] transition hover:border-[#d4ccf0] hover:bg-[#f4f2ff]"
                    to="/settings?tab=data-sources"
                  >
                    Search Console settings
                    <ArrowRight size={14} aria-hidden />
                  </Link>
                </div>
                <div
                  className="grid gap-4 sm:grid-cols-3"
                  role="group"
                  aria-label="Top Search Console segment buckets by dimension"
                >
                  <SegmentMixTile
                    label="Country"
                    dimension="country"
                    slice={data.gsc_property_breakdowns.country}
                    icon={Globe}
                  />
                  <SegmentMixTile
                    label="Device"
                    dimension="device"
                    slice={data.gsc_property_breakdowns.device}
                    icon={Monitor}
                  />
                  <SegmentMixTile
                    label="Search appearance"
                    dimension="appearance"
                    slice={data.gsc_property_breakdowns.searchAppearance}
                    icon={FileSearch}
                  />
                </div>
              </Card>
            ) : null}

            <Card className="mt-4 border-[#e8e4f8] bg-white p-6 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
              <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="mb-1 flex items-center gap-2">
                    <MousePointerClick className="text-[#5746d9]" size={18} />
                    <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Daily trend</p>
                  </div>
                  <h2 className="text-xl font-bold text-ink">
                    {gscChartTab === "traffic" ? "Clicks & impressions" : "CTR & average position"}
                  </h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {gscChartTab === "traffic"
                      ? "Current period only; prior window is used for % change on the KPIs."
                      : "Daily CTR (clicks ÷ impressions) and Search Console average position. Lower position is better."}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1 self-start rounded-lg border border-[#e8e4f8] bg-[#faf8ff] p-1">
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setGscChartTab("traffic")}
                    className={cn(
                      "h-auto rounded-md px-3 py-1.5 text-xs font-medium transition",
                      gscChartTab === "traffic"
                        ? "bg-[#5746d9] text-white hover:bg-[#5746d9]/90"
                        : "text-slate-600 hover:bg-slate-100"
                    )}
                  >
                    Clicks &amp; impressions
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setGscChartTab("ctr_position")}
                    className={cn(
                      "h-auto rounded-md px-3 py-1.5 text-xs font-medium transition",
                      gscChartTab === "ctr_position"
                        ? "bg-[#5746d9] text-white hover:bg-[#5746d9]/90"
                        : "text-slate-600 hover:bg-slate-100"
                    )}
                  >
                    CTR &amp; position
                  </Button>
                </div>
              </div>
              <div
                className="mt-4 h-[300px] w-full min-w-0"
                role="img"
                aria-label={
                  gscChartTab === "traffic"
                    ? "Line chart of daily Search Console clicks and impressions for the current period"
                    : "Line chart of daily Search Console CTR percent and average position for the current period"
                }
              >
                {gscLineData.length === 0 ? (
                  <p className="text-sm text-slate-500">No daily rows returned for this window.</p>
                ) : gscChartTab === "traffic" ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={gscLineData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                      <CartesianGrid stroke={CHART_GRID} strokeDasharray="4 4" />
                      <XAxis
                        dataKey="date"
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickFormatter={formatChartAxisDate}
                        axisLine={false}
                        tickLine={false}
                        minTickGap={24}
                      />
                      <YAxis
                        yAxisId="clicks"
                        width={44}
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        yAxisId="impr"
                        orientation="right"
                        width={52}
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        labelFormatter={(label) => String(label)}
                        contentStyle={CHART_TOOLTIP_STYLE}
                        formatter={(value: number, name: string) => [formatNumber(value), name === "clicks" ? "Clicks" : "Impressions"]}
                      />
                      <Legend wrapperStyle={{ color: "#475569", fontSize: 12 }} />
                      {goals.gsc_daily_clicks != null ? (
                        <ReferenceLine
                          yAxisId="clicks"
                          y={goals.gsc_daily_clicks}
                          stroke="#d97706"
                          strokeDasharray="5 5"
                          label={{ value: "Goal clicks/day", fill: "#d97706", fontSize: 10 }}
                        />
                      ) : null}
                      {goals.gsc_daily_impressions != null ? (
                        <ReferenceLine
                          yAxisId="impr"
                          y={goals.gsc_daily_impressions}
                          stroke="#0d9488"
                          strokeDasharray="5 5"
                          label={{ value: "Goal impr./day", fill: "#0d9488", fontSize: 10 }}
                        />
                      ) : null}
                      <Line
                        yAxisId="clicks"
                        type="monotone"
                        dataKey="clicks"
                        name="clicks"
                        stroke={CHART_PRIMARY}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        activeDot={{ r: 4 }}
                      />
                      <Line
                        yAxisId="impr"
                        type="monotone"
                        dataKey="impressions"
                        name="impressions"
                        stroke="#94a3b8"
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        activeDot={{ r: 4 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={gscLineData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                      <CartesianGrid stroke={CHART_GRID} strokeDasharray="4 4" />
                      <XAxis
                        dataKey="date"
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickFormatter={formatChartAxisDate}
                        axisLine={false}
                        tickLine={false}
                        minTickGap={24}
                      />
                      <YAxis
                        yAxisId="ctr"
                        width={48}
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        axisLine={false}
                        tickLine={false}
                        tickFormatter={(v) => `${v}%`}
                      />
                      <YAxis
                        yAxisId="pos"
                        orientation="right"
                        width={44}
                        reversed
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        labelFormatter={(label) => String(label)}
                        contentStyle={CHART_TOOLTIP_STYLE}
                        formatter={(value: number, name: string) => {
                          if (name === "CTR") {
                            return [`${Number(value).toFixed(2)}%`, "CTR"];
                          }
                          return [
                            value != null && !Number.isNaN(Number(value)) ? Number(value).toFixed(1) : "—",
                            "Avg position"
                          ];
                        }}
                      />
                      <Legend wrapperStyle={{ color: "#475569", fontSize: 12 }} />
                      <Line
                        yAxisId="ctr"
                        type="monotone"
                        dataKey="ctr_pct"
                        name="CTR"
                        stroke={CHART_PRIMARY}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        activeDot={{ r: 4 }}
                      />
                      <Line
                        yAxisId="pos"
                        type="monotone"
                        dataKey="position"
                        name="Avg position"
                        stroke="#94a3b8"
                        strokeWidth={2}
                        dot={false}
                        connectNulls
                        isAnimationActive={false}
                        activeDot={{ r: 4 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </Card>
          </>
        )}
      </section>

      {/* GA4 property — same calendar windows as Search Console */}
      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">GA4 (property)</p>
            <p className="text-sm text-slate-600">
              {ga4.available && ga4Cur
                ? `${ga4Cur.start_date} → ${ga4Cur.end_date} · timezone ${ga4.timezone} · reporting date ${ga4.anchor_date}`
                : "Configure a GA4 property in Settings → Data sources to load site-wide sessions and views."}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {gscOverviewPeriod === "rolling_30d"
                ? "Same window as Search Console: last 30 days (totals vs the prior 30 days in the % hints)."
                : "Same window as Search Console: all data since Feb 15, 2026 (nothing reliable before that)."}
            </p>
          </div>
        </div>

        {!ga4.available ? (
          <Card className="border-[#e8e4f8] bg-[#f0fdfa] p-6">
            <p className="text-sm font-medium text-ink">GA4 overview not available</p>
            <p className="mt-2 text-sm text-slate-600">{ga4.error || "Connect Google Analytics."}</p>
            <p className="mt-3 text-sm text-slate-600">
              For storefront sessions and acquisition without GA4, use{" "}
              <span className="font-medium text-ink">Shopify Admin → Analytics</span> (online store
              traffic is not duplicated here).
            </p>
            <Link
              className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-[#0891b2] hover:underline"
              to="/settings?tab=data-sources"
            >
              Open GA4 settings
              <ArrowRight size={14} />
            </Link>
          </Card>
        ) : (
          <>
            <div
              className="flex flex-col gap-4"
              role="group"
              aria-label="GA4 KPIs"
            >
              <div className="flex gap-4 overflow-x-auto pb-1 max-sm:snap-x max-sm:snap-mandatory sm:grid sm:grid-cols-2 sm:overflow-visible lg:grid-cols-4">
                <KpiCard
                  className="min-w-[220px] shrink-0 sm:min-w-0"
                  label="Sessions"
                  value={formatNumber(ga4Cur?.sessions ?? 0)}
                  sparkline={
                    <MiniSparkline
                      values={ga4SparkSessions}
                      color={GA4_CHART_SESSIONS}
                      ariaLabel="Daily GA4 sessions in the selected period"
                    />
                  }
                  hint={
                    <span>
                      Property total
                      <DeltaInline pct={ga4.deltas.sessions_pct ?? null} />
                    </span>
                  }
                />
                <KpiCard
                  className="min-w-[220px] shrink-0 sm:min-w-0"
                  label="Views"
                  value={formatNumber(ga4Cur?.views ?? 0)}
                  sparkline={
                    <MiniSparkline
                      values={ga4SparkViews}
                      color={GA4_CHART_VIEWS}
                      ariaLabel="Daily GA4 views in the selected period"
                    />
                  }
                  hint={
                    <span>
                      Screen / page views
                      <DeltaInline pct={ga4.deltas.views_pct ?? null} />
                    </span>
                  }
                />
                <KpiCard
                  label="Views / session"
                  value={
                    ga4Cur && ga4Cur.sessions > 0
                      ? (ga4Cur.views / ga4Cur.sessions).toFixed(2)
                      : "—"
                  }
                  sparkline={
                    <MiniSparkline
                      values={ga4SparkVps}
                      color="#0d9488"
                      ariaLabel="Daily views per session in the selected period"
                    />
                  }
                  hint="Simple ratio for the window"
                />
                <KpiCard
                  className="min-w-[220px] shrink-0 sm:min-w-0"
                  label="Cache"
                  value={ga4.cache.label || "—"}
                  hint={overviewCacheHint(ga4.cache)}
                />
              </div>
              <div className="flex gap-4 overflow-x-auto pb-1 max-sm:snap-x max-sm:snap-mandatory sm:grid sm:grid-cols-2 sm:overflow-visible lg:grid-cols-3">
                <KpiCard
                  className="min-w-[220px] shrink-0 sm:min-w-0"
                  label="New users"
                  value={formatNumber(ga4Cur?.new_users ?? 0)}
                  hint={
                    <span>
                      In this window
                      <DeltaInline pct={ga4.deltas.new_users_pct ?? null} />
                    </span>
                  }
                />
                <KpiCard
                  className="min-w-[220px] shrink-0 sm:min-w-0"
                  label="Avg engagement"
                  value={formatDurationSeconds(ga4Cur?.avg_session_duration ?? 0)}
                  hint={
                    <span>
                      Session-weighted avg
                      <DeltaInline pct={ga4.deltas.avg_session_duration_pct ?? null} />
                    </span>
                  }
                />
                <KpiCard
                  className="min-w-[220px] shrink-0 sm:min-w-0"
                  label="Bounce rate"
                  value={
                    ga4Cur && ga4Cur.sessions > 0
                      ? formatPercent(ga4Cur.bounce_rate)
                      : "—"
                  }
                  hint={
                    <span>
                      Session-weighted
                      <DeltaInline pct={ga4.deltas.bounce_rate_pp ?? null} unit="points" />
                    </span>
                  }
                />
              </div>
            </div>

            <Card className="mt-4 border-[#e8e4f8] bg-white p-6 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
              <div className="mb-1 flex items-center gap-2">
                <Activity className="text-[#0891b2]" size={18} />
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Daily trend</p>
              </div>
              <h2 className="text-xl font-bold text-ink">Sessions &amp; views</h2>
              <p className="mt-1 text-sm text-slate-500">Current period; % change on KPIs uses the prior window (same as GSC toggle).</p>
              <div
                className="mt-6 h-[300px] w-full min-w-0"
                role="img"
                aria-label="Line chart of daily GA4 sessions and views for the current period"
              >
                {ga4LineData.length === 0 ? (
                  <p className="text-sm text-slate-500">No daily rows returned for this window.</p>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={ga4LineData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                      <CartesianGrid stroke={CHART_GRID} strokeDasharray="4 4" />
                      <XAxis
                        dataKey="date"
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        tickFormatter={formatChartAxisDate}
                        axisLine={false}
                        tickLine={false}
                        minTickGap={24}
                      />
                      <YAxis
                        yAxisId="sess"
                        width={44}
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        yAxisId="views"
                        orientation="right"
                        width={52}
                        tick={{ fill: "#64748b", fontSize: 11 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Tooltip
                        labelFormatter={(label) => String(label)}
                        contentStyle={CHART_TOOLTIP_STYLE}
                        formatter={(value: number, name: string) => [
                          formatNumber(value),
                          name === "sessions" ? "Sessions" : "Views"
                        ]}
                      />
                      <Legend wrapperStyle={{ color: "#475569", fontSize: 12 }} />
                      {goals.ga4_daily_sessions != null ? (
                        <ReferenceLine
                          yAxisId="sess"
                          y={goals.ga4_daily_sessions}
                          stroke="#d97706"
                          strokeDasharray="5 5"
                          label={{ value: "Goal sessions/day", fill: "#d97706", fontSize: 10 }}
                        />
                      ) : null}
                      {goals.ga4_daily_views != null ? (
                        <ReferenceLine
                          yAxisId="views"
                          y={goals.ga4_daily_views}
                          stroke="#0d9488"
                          strokeDasharray="5 5"
                          label={{ value: "Goal views/day", fill: "#0d9488", fontSize: 10 }}
                        />
                      ) : null}
                      <Line
                        yAxisId="sess"
                        type="monotone"
                        dataKey="sessions"
                        name="sessions"
                        stroke={GA4_CHART_SESSIONS}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        activeDot={{ r: 4 }}
                      />
                      <Line
                        yAxisId="views"
                        type="monotone"
                        dataKey="views"
                        name="views"
                        stroke={GA4_CHART_VIEWS}
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        activeDot={{ r: 4 }}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                )}
              </div>
            </Card>
          </>
        )}

        {gsc.available ? (
          <div className="mt-4 space-y-2">
            {data.gsc_performance_error ? (
              <p className="rounded-xl border border-[#fecaca] bg-[#fff4ef] px-3 py-2 text-sm text-[#8f3e20]">
                {data.gsc_performance_error}
              </p>
            ) : null}
            <GscPerformanceSection
              gscRangeLabel={
                data.gsc_performance_period.start_date && data.gsc_performance_period.end_date
                  ? `${data.gsc_performance_period.start_date} → ${data.gsc_performance_period.end_date}`
                  : siteCur
                    ? `${siteCur.start_date} → ${siteCur.end_date}`
                    : ""
              }
              gsc_queries={data.gsc_queries}
              gsc_pages={data.gsc_pages}
              countrySlice={{
                rows: data.gsc_property_breakdowns.country.rows,
                error: data.gsc_property_breakdowns.country.error,
                cache: {
                  label: data.gsc_property_breakdowns.country.cache.label,
                  text: data.gsc_property_breakdowns.country.cache.text
                }
              }}
              deviceSlice={{
                rows: data.gsc_property_breakdowns.device.rows,
                error: data.gsc_property_breakdowns.device.error,
                cache: {
                  label: data.gsc_property_breakdowns.device.cache.label,
                  text: data.gsc_property_breakdowns.device.cache.text
                }
              }}
            />
          </div>
        ) : null}
      </section>

      {/* Indexing rollup — stored URL Inspection fields on synced entities */}
      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Indexing (database)</p>
            <p className="text-sm text-slate-600">
              Rollup of last-known Search Console inspection states on synced catalog URLs. Run a sync with index refresh
              to fill gaps; open any product, collection, page, or article for detail.
            </p>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <KpiCard
            label="Tracked URLs"
            value={formatNumber(idxTotal)}
            hint={idxTotal > 0 ? `${idxPctIndexed.toFixed(1)}% look indexed` : "No synced entities"}
          />
          <KpiCard label="Indexed" value={formatNumber(idx.indexed)} hint="Positive coverage signals" />
          <KpiCard label="Not indexed" value={formatNumber(idx.not_indexed)} hint="Blocked, excluded, or errors" />
          <KpiCard label="Needs review" value={formatNumber(idx.needs_review)} hint="Ambiguous or partial data" />
          <KpiCard label="Unknown" value={formatNumber(idx.unknown)} hint="No inspection text stored yet" />
        </div>
        <Card className="mt-4 border-[#e8e4f8] bg-white p-5 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
          <div className="flex flex-wrap items-center gap-2">
            <FileSearch className="text-[#5746d9]" size={18} />
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">By entity type</p>
          </div>
          <ul className="mt-4 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
            {(
              [
                ["product", "Products", "/products"],
                ["collection", "Collections", "/collections"],
                ["page", "Pages", "/pages"],
                ["blog_article", "Articles", "/articles"]
              ] as const
            ).map(([key, label, href]) => {
              const seg = idx.by_type[key];
              if (!seg) return null;
              const sub =
                seg.total > 0
                  ? `${formatNumber(seg.indexed)} indexed · ${formatNumber(seg.not_indexed)} not · ${formatNumber(seg.needs_review)} review · ${formatNumber(seg.unknown)} unknown`
                  : "No rows";
              return (
                <li key={key}>
                  <Link className="font-semibold text-[#5746d9] hover:underline" to={href}>
                    {label}
                  </Link>
                  <p className="mt-1 tabular-nums text-slate-600">{formatNumber(seg.total)} URLs</p>
                  <p className="mt-0.5 text-xs text-slate-500">{sub}</p>
                </li>
              );
            })}
          </ul>
        </Card>
      </section>

      {/* Tracked URL rollup — local DB facts (not full property) */}
      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Tracked URLs (database)</p>
            <p className="text-sm text-slate-600">Totals from synced entities with GSC/GA4 facts—subset of the property</p>
          </div>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <KpiCard label="GSC clicks" value={formatNumber(clicks)} hint="Sum across tracked URLs" />
          <KpiCard label="GSC impressions" value={formatNumber(impressions)} />
          <KpiCard label="Avg CTR" value={formatPercent(ctrFraction)} hint="Clicks ÷ impressions" />
          <KpiCard label="GA4 sessions" value={formatNumber(data.metrics.ga4_sessions)} />
          <KpiCard label="GA4 views" value={formatNumber(data.metrics.ga4_views)} />
        </div>
      </section>

      {/* Catalog SEO completion (plan S4) */}
      <section>
        <Card className="border-[#e8e4f8] bg-white p-6 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Catalog SEO completion</p>
          <h2 className="mt-2 text-xl font-bold text-ink">Meta coverage</h2>
          <p className="mt-1 text-sm text-slate-500">
            Share of synced entities with both SEO title and description filled. Products also show thin-body count
            (description under 200 characters).
          </p>
          <div className="mt-6 grid gap-8 sm:grid-cols-2">
            <CompletionBar
              label="Products"
              pct={data.catalog_completion.products.pct_meta_complete}
              href="/products"
              sub={`${formatNumber(data.catalog_completion.products.meta_complete)} / ${formatNumber(data.catalog_completion.products.total)} with meta · ${formatNumber(data.catalog_completion.products.thin_body)} thin body`}
            />
            <CompletionBar
              label="Collections"
              pct={data.catalog_completion.collections.pct_meta_complete}
              href="/collections"
              sub={`${formatNumber(data.catalog_completion.collections.meta_complete)} / ${formatNumber(data.catalog_completion.collections.total)} with meta`}
            />
            <CompletionBar
              label="Pages"
              pct={data.catalog_completion.pages.pct_meta_complete}
              href="/pages"
              sub={`${formatNumber(data.catalog_completion.pages.meta_complete)} / ${formatNumber(data.catalog_completion.pages.total)} with meta`}
            />
            <CompletionBar
              label="Articles"
              pct={data.catalog_completion.articles.pct_meta_complete}
              href="/articles"
              sub={`${formatNumber(data.catalog_completion.articles.meta_complete)} / ${formatNumber(data.catalog_completion.articles.total)} with meta · all blogs`}
            />
          </div>
        </Card>
      </section>

      {/* Catalog scale — meta coverage breakdown by entity type */}
      <section>
        <Card className="border-[#e8e4f8] bg-white p-6 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
          <div className="mb-1 flex items-center gap-2">
            <MousePointerClick className="text-[#5746d9]" size={18} />
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Catalog scale</p>
          </div>
          <h2 className="text-xl font-bold text-ink">Meta coverage by entity type</h2>
          <p className="mt-1 text-sm text-slate-500">
            Stacked count of entities with complete meta vs. missing title or description. Products also show thin-body
            copy (&lt;200 chars).
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-slate-600">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_META_COMPLETE }} />
              Meta complete
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_MISSING_META }} />
              Missing title or description
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_THIN_BODY }} />
              Thin body (products)
            </span>
          </div>
          <div className="mt-4 h-[300px] w-full min-w-0">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={catalogChartData} margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
                <CartesianGrid stroke={CHART_GRID} strokeDasharray="4 4" vertical={false} />
                <XAxis dataKey="name" tick={{ fill: "#64748b", fontSize: 12 }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fill: "#64748b", fontSize: 12 }} axisLine={false} tickLine={false} width={48} />
                <Tooltip
                  cursor={{ fill: "rgba(87, 70, 217, 0.06)" }}
                  contentStyle={CHART_TOOLTIP_STYLE}
                  formatter={(value: number, name: string) => {
                    const labels: Record<string, string> = {
                      meta_complete: "Meta complete",
                      missing_meta: "Missing meta",
                      thin_body: "Thin body"
                    };
                    return [formatNumber(value), labels[name] ?? name];
                  }}
                />
                <Bar dataKey="meta_complete" stackId="a" fill={CHART_META_COMPLETE} maxBarSize={80}>
                  {catalogChartData.map((entry) => (
                    <Cell
                      key={entry.name}
                      fill={CHART_META_COMPLETE}
                      radius={(entry.missing_meta === 0 && entry.thin_body === 0 ? [6, 6, 0, 0] : [0, 0, 0, 0]) as never}
                    />
                  ))}
                </Bar>
                <Bar dataKey="missing_meta" stackId="a" fill={CHART_MISSING_META} maxBarSize={80}>
                  {catalogChartData.map((entry) => (
                    <Cell
                      key={entry.name}
                      fill={CHART_MISSING_META}
                      radius={(entry.thin_body === 0 ? [6, 6, 0, 0] : [0, 0, 0, 0]) as never}
                    />
                  ))}
                </Bar>
                <Bar dataKey="thin_body" stackId="a" fill={CHART_THIN_BODY} radius={[6, 6, 0, 0] as never} maxBarSize={80} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </section>

      {/* Top organic pages by GSC clicks */}
      {data.top_pages.length > 0 && (
        <section>
          <Card className="border-[#e8e4f8] bg-white p-6 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
            <div className="mb-1 flex items-center gap-2">
              <TrendingUp className="text-[#5746d9]" size={18} />
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Organic performance</p>
            </div>
            <h2 className="text-xl font-bold text-ink">Top pages by GSC clicks</h2>
            <p className="mt-1 text-sm text-slate-500">
              Highest-click entities across all types from locally-synced GSC data. Click any title to open its detail
              page.
            </p>
            <div className="mt-5">
              <Table className="w-full min-w-[560px] text-sm">
                <TableHeader>
                  <TableRow className="border-b border-[#e8e4f8]">
                    <TableHead className="pb-2 text-left text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                      Page
                    </TableHead>
                    <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                      Clicks
                    </TableHead>
                    <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                      Impressions
                    </TableHead>
                    <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                      CTR
                    </TableHead>
                    <TableHead className="pb-2 pl-4 text-right text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
                      Avg pos.
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody className="divide-y divide-[#f1eeff]">
                  {data.top_pages.map((page) => (
                    <TableRow key={`${page.entity_type}:${page.handle}`} className="group">
                      <TableCell className="py-2.5 pr-4">
                        <div className="flex items-start gap-2.5">
                          <span
                            className="mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white"
                            style={{ background: ENTITY_TYPE_COLORS[page.entity_type] ?? "#64748b" }}
                          >
                            {ENTITY_TYPE_LABELS[page.entity_type] ?? page.entity_type}
                          </span>
                          <Link
                            to={entityAppPath(page.entity_type, page.handle)}
                            className="font-medium text-[#5746d9] underline-offset-2 hover:underline"
                          >
                            {page.title || page.handle}
                          </Link>
                        </div>
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums font-semibold text-ink">
                        {formatNumber(page.gsc_clicks)}
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600">
                        {formatNumber(page.gsc_impressions)}
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600">
                        {formatPercent(page.gsc_ctr)}
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600">
                        {page.gsc_position != null ? page.gsc_position.toFixed(1) : "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Card>
        </section>
      )}

      {/* SEO debt (entity counts live under Catalog scale above) */}
      <section>
        <Card className="border-[#e8e4f8] p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">SEO debt snapshot</p>
          <div className="mt-4 grid gap-5 sm:grid-cols-2">
            <div>
              <p className="text-sm text-slate-500">Products missing meta</p>
              <Link
                to="/products?focus=missing_meta&sort=score&direction=desc"
                className="mt-1 block text-3xl font-bold tabular-nums text-[#5746d9] underline-offset-4 hover:underline"
              >
                {formatNumber(data.metrics.products_missing_meta)}
              </Link>
            </div>
            <div>
              <p className="text-sm text-slate-500">Thin product copy</p>
              <Link
                to="/products?focus=thin_body&sort=body_length&direction=asc"
                className="mt-1 block text-3xl font-bold tabular-nums text-[#5746d9] underline-offset-4 hover:underline"
              >
                {formatNumber(data.metrics.products_thin_body)}
              </Link>
            </div>
            <div>
              <p className="text-sm text-slate-500">Collections missing meta</p>
              <Link
                to="/collections?focus=missing_meta&sort=score&direction=desc"
                className="mt-1 block text-3xl font-bold tabular-nums text-[#5746d9] underline-offset-4 hover:underline"
              >
                {formatNumber(data.metrics.collections_missing_meta)}
              </Link>
            </div>
            <div>
              <p className="text-sm text-slate-500">Pages missing meta</p>
              <Link
                to="/pages?focus=missing_meta&sort=score&direction=desc"
                className="mt-1 block text-3xl font-bold tabular-nums text-[#5746d9] underline-offset-4 hover:underline"
              >
                {formatNumber(data.metrics.pages_missing_meta)}
              </Link>
            </div>
          </div>
          <p className="mt-4 text-xs text-slate-500">
            <Link to="/articles?focus=missing_meta" className="font-medium text-[#5746d9] underline-offset-4 hover:underline">
              Articles missing meta: {formatNumber(data.catalog_completion.articles.missing_meta)}
            </Link>
            {" · "}
            URLs with GSC: {formatNumber(data.metrics.gsc_pages)} · With GA4: {formatNumber(data.metrics.ga4_pages)}
          </p>
        </Card>
      </section>
    </div>
  );
}

