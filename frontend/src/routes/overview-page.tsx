import { OverviewActions } from "../components/overview/overview-actions";
import "./overview.css";
import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight, FileSearch, Globe, Monitor, MousePointerClick, TrendingUp } from "lucide-react";
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
  IndexingSummary,
  NeedsAttention,
  DeltaInline,
  KpiCard,
  SegmentMixTile,
} from "../components/overview/overview-cards";
import { OverviewOnboarding, overviewShowsOnboarding } from "../components/overview/overview-onboarding";
import { SiteAuthorityCard } from "../components/overview/site-authority-card";
import {
  CHART_GRID,
  CHART_META_COMPLETE,
  CHART_MISSING_META,
  CHART_PRIMARY,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { OverviewFreshness, PreviousPeriodComparison, periodDays } from "../components/overview/overview-reporting";
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
  const [comparePrevious, setComparePrevious] = useState(false);
  const [detailTab, setDetailTab] = useState<"queries" | "pages" | "countries" | "devices">("queries");
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
        missing_meta: cc.products.missing_meta
      },
      {
        name: "Collections",
        meta_complete: cc.collections.meta_complete,
        missing_meta: cc.collections.missing_meta
      },
      {
        name: "Pages",
        meta_complete: cc.pages.meta_complete,
        missing_meta: cc.pages.missing_meta
      },
      {
        name: "Articles",
        meta_complete: cc.articles.meta_complete,
        missing_meta: cc.articles.missing_meta
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
        <div className="overview-metrics">
          {Array.from({ length: 4 }).map((_, i) => (
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

  // Rollups also exist for empty windows; do not imply that missing history is a zero baseline.
  const gscPrior = gsc.previous && gsc.previous.impressions > 0 ? gsc.previous : null;
  const ga4Prior = ga4.previous && ga4.previous.sessions > 0 ? ga4.previous : null;

  const idx = data.indexing_rollup;
  const idxTotal = idx.total;

  const goals = data.overview_goals;

  const searchScope = gscSegment === "all" ? "Whole site" : `Search URLs · ${GSC_SEGMENT_OPTIONS.find(option => option.value === gscSegment)?.label}`;

  return (
    <div className="overview-page space-y-6 pb-8">
      <header className="space-y-3">
        <h1 className="overview-title">Overview</h1>
        <p className="mt-1 text-sm text-slate-500">Your search performance, priorities, and catalog health.</p>
          <div className="overview-toolbar flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1 rounded-lg border border-[#e8e4f8] bg-white p-1">
              {OVERVIEW_GSC_PERIOD_OPTIONS.map(({ value, label }) => (
                <Button
                  key={value}
                  type="button"
                  variant="ghost"
                  aria-pressed={gscOverviewPeriod === value}
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
            <div className="flex max-w-full flex-wrap gap-1 rounded-lg border border-[#e8e4f8] bg-white p-1">
              <span className="self-center px-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500">
                Search URLs
              </span>
              {GSC_SEGMENT_OPTIONS.map(({ value, label }) => (
                <Button
                  key={value}
                  type="button"
                  variant="ghost"
                  aria-pressed={gscSegment === value}
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
        <p className="text-xs text-slate-500">Period applies to Search and Analytics. Search URLs filters Search metrics and query/page reports only.</p>
        <nav className="overview-section-nav" aria-label="Overview sections">
          <a href="#overview-performance">Performance</a><a href="#overview-attention">Attention</a><a href="#overview-actions">SEO actions</a><a href="#overview-health">Catalog health</a><a href="#overview-details">Details</a>
        </nav>
      </header>

      <section aria-label="Search snapshot" className="space-y-3">
        <div className="overview-section-heading">
          <div><p className="overview-eyebrow">Search snapshot</p><h2 className="overview-section-title">{searchScope}</h2></div>
          {gsc.available ? <OverviewFreshness cache={gsc.cache} timezone={gsc.timezone} anchorDate={gsc.anchor_date} source="Search Console" /> : null}
        </div>
        {siteCur ? <p className="text-xs text-slate-500">{siteCur.start_date} → {siteCur.end_date}{gscPrior ? " · changes versus the previous period" : ""}</p> : null}
        {!gsc.available ? (
          <Card className="overview-panel p-6">
            <p className="text-sm font-medium text-ink">Site-level GSC not available</p>
            <p className="mt-2 text-sm text-slate-600">Check your Google connection and selected Search Console property in Settings.</p>
            {gsc.error ? <details className="mt-3 text-xs text-slate-500"><summary className="cursor-pointer font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600">Connection details</summary><p className="mt-2 break-words [overflow-wrap:anywhere]">{gsc.error}</p></details> : null}
            <Link
              className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-[#5746d9] hover:underline"
              to="/settings?tab=data-sources"
            >
              Open Search Console settings
              <ArrowRight size={14} />
            </Link>
          </Card>

        ) : (
            <div
              className="overview-metrics"
              role="group"
              aria-label="Search Console KPIs"
            >
              <KpiCard
                className="min-w-0"
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
                    {searchScope}
                    <DeltaInline pct={gsc.deltas.clicks_pct ?? null} />
                  </span>
                }
              />
              <KpiCard
                className="min-w-0"
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
                    {searchScope}
                    <DeltaInline pct={gsc.deltas.impressions_pct ?? null} />
                  </span>
                }
              />
              <KpiCard
                className="min-w-0"
                label="Avg CTR"
                value={formatPercent(siteCtr)}
                sparkline={
                  <MiniSparkline
                    values={gscSparkCtrPct}
                    color="#7c6fd6"
                    ariaLabel="Daily average click-through rate in the selected period"
                  />
                }
                hint="Clicks ÷ impressions"
              />
              <KpiCard
                className="min-w-0"
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
            </div>
        )}
      </section>
      <section id="overview-attention" aria-label="Catalog issues">
        <NeedsAttention hasCatalog={idxTotal > 0} items={[
          { label: "Products missing metadata", count: data.metrics.products_missing_meta, href: "/products?focus=missing_meta&sort=score&direction=desc", action: "Review products" },
          { label: "Short product descriptions", count: data.metrics.products_thin_body, href: "/products?focus=thin_body&sort=body_length&direction=asc", action: "Improve copy" },
          { label: "Collections missing metadata", count: data.metrics.collections_missing_meta, href: "/collections?focus=missing_meta&sort=score&direction=desc", action: "Review collections" },
          { label: "Pages missing metadata", count: data.metrics.pages_missing_meta, href: "/pages?focus=missing_meta&sort=score&direction=desc", action: "Review pages" },
          { label: "Articles missing metadata", count: data.catalog_completion.articles.missing_meta, href: "/articles", action: "Browse articles" }
        ]} />

      </section>


      <OverviewActions />
      <section id="overview-performance" className="space-y-4">
        <Tabs defaultValue="search">
          <div className="overview-section-heading">
            <div><p className="overview-eyebrow">Explore your traffic</p><h2 className="overview-section-title">Performance</h2></div>
            <TabsList aria-label="Performance source"><TabsTrigger value="search">Search</TabsTrigger><TabsTrigger value="analytics">Analytics</TabsTrigger></TabsList>
          </div>
          <TabsContent value="search" className="space-y-4">
            <p className="text-xs text-slate-500">Chart and query/page reports: {searchScope}. Audience, countries, and devices: whole site.</p>
            {gsc.available ? <>
            <Card className="overview-panel mt-4 p-6">
              <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="mb-1 flex items-center gap-2">
                    <MousePointerClick className="text-[#5746d9]" size={18} />
                    <p className="overview-eyebrow">Daily trend</p>
                  </div>
                  <h2 className="overview-section-title">
                    {gscChartTab === "traffic" ? "Clicks & impressions" : "CTR & average position"}
                  </h2>
                  <p className="mt-1 text-sm text-slate-500">
                    {gscChartTab === "traffic"
                      ? "Daily totals · clicks on the left axis, impressions on the right."
                      : "CTR on the left axis; average position on the right. Lower position is better."}
                  </p>
                </div>
                <Tabs value={gscChartTab} onValueChange={value => setGscChartTab(value as GscChartTab)}>
                  <TabsList aria-label="Search chart metrics">
                    <TabsTrigger value="traffic" aria-controls="overview-search-chart">Clicks &amp; impressions</TabsTrigger>
                    <TabsTrigger value="ctr_position" aria-controls="overview-search-chart">CTR &amp; position</TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>
              <PreviousPeriodComparison previous={gscPrior} checked={comparePrevious} onChange={setComparePrevious} />
              <div
                id="overview-search-chart" className="overview-chart mt-4 h-[280px] w-full min-w-0"
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
                      {comparePrevious && gscPrior && periodDays(gscPrior) > 0 ? <ReferenceLine yAxisId="clicks" y={gscPrior.clicks / periodDays(gscPrior)} stroke={CHART_PRIMARY} strokeDasharray="5 5" ifOverflow="extendDomain" /> : null}
                      {comparePrevious && gscPrior && periodDays(gscPrior) > 0 ? <ReferenceLine yAxisId="impr" y={gscPrior.impressions / periodDays(gscPrior)} stroke="#64748b" strokeDasharray="5 5" ifOverflow="extendDomain" /> : null}
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
                      {comparePrevious && gscPrior ? <ReferenceLine yAxisId="ctr" y={gscPrior.ctr * 100} stroke={CHART_PRIMARY} strokeDasharray="5 5" ifOverflow="extendDomain" /> : null}
                      {comparePrevious && gscPrior && gscPrior.position != null && gscPrior.position > 0 ? <ReferenceLine yAxisId="pos" y={gscPrior.position} stroke="#64748b" strokeDasharray="5 5" ifOverflow="extendDomain" /> : null}
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
            {data.gsc_property_breakdowns.available ? (
              <Card className="overview-panel p-5">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                  <h3 className="overview-section-title">Audience at a glance</h3><span className="overview-scope">Whole site · impressions</span>
                </div>
                <div className="overview-audience-grid">
                  <SegmentMixTile label="Country" dimension="country" slice={data.gsc_property_breakdowns.country} icon={Globe} onExplore={() => { setDetailTab("countries"); }} />
                  <SegmentMixTile label="Device" dimension="device" slice={data.gsc_property_breakdowns.device} icon={Monitor} onExplore={() => { setDetailTab("devices"); }} />
                  <SegmentMixTile label="Search appearance" dimension="appearance" slice={data.gsc_property_breakdowns.searchAppearance} icon={FileSearch} />
                </div>
                <details className="overview-disclosure mt-4">
                  <summary>About audience data</summary>
                  <p className="mt-2 text-xs text-slate-500">{data.gsc_property_breakdowns.window.start_date} → {data.gsc_property_breakdowns.window.end_date}. Shares are of the returned impression rows in each dimension, which may not cover the full property. These whole-site breakdowns are updated by Search Console sync and are unaffected by the Search URL filter.</p>
                  <Link className="mt-2 inline-block text-xs font-medium text-[#5746d9]" to="/settings?tab=data-sources">Search Console settings →</Link>
                </details>
              </Card>
            ) : null}
              <section id="overview-search-details" className="overview-panel p-5">
                <h2 className="overview-section-title">Search details</h2>
        {gsc.available ? (
          <div className="mt-4 space-y-2">
            {data.gsc_performance_error ? (
              <p className="rounded-xl border border-[#fecaca] bg-[#fff4ef] px-3 py-2 text-sm text-[#8f3e20]">
                {data.gsc_performance_error}
              </p>
            ) : null}
            <GscPerformanceSection
              activeTab={detailTab}
              onTabChange={setDetailTab}
              queryPageScope={searchScope}
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
            </> : <p className="overview-panel p-5 text-sm text-slate-500">Connect Search Console using the settings link above to see trends and detailed reports.</p>}
          </TabsContent>
          <TabsContent value="analytics">
      {/* GA4 property — same calendar windows as Search Console */}
      <section className="space-y-4" aria-label="Whole-site Analytics">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 className="overview-section-title">Whole-site Analytics</h3>
            <OverviewFreshness cache={ga4.cache} timezone={ga4.timezone} anchorDate={ga4.anchor_date} source="Analytics" />
            <p className="text-sm text-slate-600">
              {ga4.available && ga4Cur
                ? `${ga4Cur.start_date} → ${ga4Cur.end_date} · all site traffic, unaffected by the Search URL filter`
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
          <Card className="overview-panel p-6">
            <p className="text-sm font-medium text-ink">GA4 overview not available</p>
            <p className="mt-2 text-sm text-slate-600">Check your Google connection and selected Analytics property in Settings.</p>
            {ga4.error ? <details className="mt-3 text-xs text-slate-500"><summary className="cursor-pointer font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600">Connection details</summary><p className="mt-2 break-words [overflow-wrap:anywhere]">{ga4.error}</p></details> : null}
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
              className="overview-metrics"
              role="group"
              aria-label="GA4 KPIs"
            >
                <KpiCard
                  className="min-w-0"
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
                  className="min-w-0"
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
                  className="min-w-0"
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
                  className="min-w-0"
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
                  className="min-w-0"
                  label="Bounce rate"
                  value={
                    ga4Cur && ga4Cur.sessions > 0
                      ? formatPercent(ga4Cur.bounce_rate)
                      : "—"
                  }
                  hint={
                    <span>
                      Session-weighted
                      <DeltaInline pct={ga4.deltas.bounce_rate_pp ?? null} unit="points" lowerIsBetter />
                    </span>
                  }
                />
            </div>

            <Card className="overview-panel mt-4 p-6">
              <div className="mb-1 flex items-center gap-2">
                <Activity className="text-[#0891b2]" size={18} />
                <p className="overview-eyebrow">Daily trend</p>
              </div>
              <h2 className="overview-section-title">Sessions &amp; views</h2>
              <p className="mt-1 text-sm text-slate-500">Daily totals · sessions on the left axis, views on the right.</p>
              <PreviousPeriodComparison previous={ga4Prior} checked={comparePrevious} onChange={setComparePrevious} />
              <div
                className="overview-chart mt-4 h-[280px] w-full min-w-0"
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
                      {comparePrevious && ga4Prior && periodDays(ga4Prior) > 0 ? <ReferenceLine yAxisId="sess" y={ga4Prior.sessions / periodDays(ga4Prior)} stroke={GA4_CHART_SESSIONS} strokeDasharray="5 5" ifOverflow="extendDomain" /> : null}
                      {comparePrevious && ga4Prior && periodDays(ga4Prior) > 0 ? <ReferenceLine yAxisId="views" y={ga4Prior.views / periodDays(ga4Prior)} stroke="#64748b" strokeDasharray="5 5" ifOverflow="extendDomain" /> : null}
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

      </section>


          </TabsContent>
        </Tabs>
      </section>
      <section id="overview-health" className="space-y-4">
        <div className="overview-section-heading"><h2 className="overview-section-title">Catalog health</h2><span className="overview-scope">Synced catalog · latest stored status</span></div>
        <div className="overview-health-grid">
      {/* Indexing rollup — stored URL Inspection fields on synced entities */}
      <section className="overview-panel p-5">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="overview-section-title">Indexing</h2>
            <p className="text-sm text-slate-600">
              Latest stored inspection status for synced URLs. Refresh indexing during sync to update coverage.
            </p>
          </div>
        </div>
        <IndexingSummary {...idx} />
        <div className="overview-report-section mt-4">
          <h3 className="overview-section-title">Indexing by entity type</h3>
          <ul className="overview-entity-grid mt-4 text-sm">
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
        </div>
      </section>

      {/* Catalog SEO completion (plan S4) */}
      <section className="min-w-0">
        <Card className="overview-panel p-6">

          <h2 className="overview-section-title">Metadata coverage</h2>
          <p className="mt-1 text-sm text-slate-500">
            Synced entities with both an SEO title and description. Open a missing count to review the affected catalog.
          </p>
          <div className="mt-5 divide-y divide-slate-100">
            {([
              ["products", "Products", "/products"],
              ["collections", "Collections", "/collections"],
              ["pages", "Pages", "/pages"],
              ["articles", "Articles", "/articles"]
            ] as const).map(([key, label, href]) => {
              const coverage = data.catalog_completion[key];
              return <CompletionBar key={key} label={label} complete={coverage.meta_complete} total={coverage.total} missing={coverage.missing_meta} href={href} issueHref={key === "articles" ? href : `${href}?focus=missing_meta&sort=score&direction=desc`} />;
            })}
          </div>
          <div className="overview-report-section mt-4">
            <h3 className="overview-section-title">Coverage counts by entity type</h3>
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-slate-600">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_META_COMPLETE }} />
              Meta complete
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: CHART_MISSING_META }} />
              Missing title or description
            </span>

          </div>
          <div className="mt-4 h-[300px] w-full min-w-0">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart layout="vertical" data={catalogChartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                <CartesianGrid stroke={CHART_GRID} strokeDasharray="4 4" horizontal={false} />
                <XAxis type="number" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis type="category" dataKey="name" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} width={80} />
                <Tooltip
                  cursor={{ fill: "rgba(87, 70, 217, 0.06)" }}
                  contentStyle={CHART_TOOLTIP_STYLE}
                  formatter={(value: number, name: string) => {
                    const labels: Record<string, string> = {
                      meta_complete: "Meta complete",
                      missing_meta: "Missing meta"
                    };
                    return [formatNumber(value), labels[name] ?? name];
                  }}
                />
                <Bar dataKey="meta_complete" stackId="a" fill={CHART_META_COMPLETE} maxBarSize={24}>
                  {catalogChartData.map((entry) => (
                    <Cell
                      key={entry.name}
                      fill={CHART_META_COMPLETE}
                      radius={(entry.missing_meta === 0 ? [0, 4, 4, 0] : [0, 0, 0, 0]) as never}
                    />
                  ))}
                </Bar>
                <Bar dataKey="missing_meta" stackId="a" fill={CHART_MISSING_META} maxBarSize={24}>
                  {catalogChartData.map((entry) => (
                    <Cell
                      key={entry.name}
                      fill={CHART_MISSING_META}
                      radius={[0, 4, 4, 0] as never}
                    />
                  ))}
                </Bar>

              </BarChart>
            </ResponsiveContainer>
          </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-slate-100 pt-4 text-sm">
            <span>Short product descriptions <strong className="ml-2 tabular-nums">{formatNumber(data.metrics.products_thin_body)}</strong></span>
            <Link className="font-medium text-[#5746d9] hover:underline" to="/products?focus=thin_body&sort=body_length&direction=asc">Review descriptions →</Link>
            <p className="w-full text-xs text-slate-500">Separate from metadata coverage; a product can have both issues.</p>
          </div>
        </Card>
      </section>


        </div>
      </section>
      <section id="overview-details" className="space-y-4">
        <div className="overview-report-section overview-panel p-5">
          <h3 className="overview-section-title">Synced catalog performance</h3>
          <div className="mt-5 space-y-5">
      {/* Tracked URL rollup — local DB facts (not full property) */}
      <section>
        <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="overview-section-title">Synced catalog metrics</h2>
            <p className="text-sm text-slate-600">Latest stored totals across all synced catalog URLs. These use per-URL sync windows, not the period or Search URL filter above.</p>
          </div>
        </div>
        <div className="overview-metrics overview-tracked-metrics">
          <KpiCard label="GSC clicks" value={formatNumber(clicks)} hint="Sum across tracked URLs" />
          <KpiCard label="GSC impressions" value={formatNumber(impressions)} />
          <KpiCard label="Avg CTR" value={formatPercent(ctrFraction)} hint="Clicks ÷ impressions" />
          <KpiCard label="GA4 sessions" value={formatNumber(data.metrics.ga4_sessions)} />
          <KpiCard label="GA4 views" value={formatNumber(data.metrics.ga4_views)} />
        </div>
      </section>

      {/* Top organic pages by GSC clicks */}
      {data.top_pages.length > 0 && (
        <section>
          <Card className="overview-panel p-6">
            <div className="mb-1 flex items-center gap-2">
              <TrendingUp className="text-[#5746d9]" size={18} />
              <p className="overview-eyebrow">Organic performance</p>
            </div>
            <h2 className="overview-section-title">Top pages by GSC clicks</h2>
            <p className="mt-1 text-sm text-slate-500">
              Latest stored Search Console totals across all catalog types. Open any title to review its detail page.
            </p>
            <div className="mt-5">
              <Table className="overview-top-pages w-full min-w-[560px] text-sm">
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
                      <TableCell className="py-2.5 pr-4" data-label="Page">
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
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums font-semibold text-ink" data-label="Clicks">
                        {formatNumber(page.gsc_clicks)}
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600" data-label="Impressions">
                        {formatNumber(page.gsc_impressions)}
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600" data-label="CTR">
                        {formatPercent(page.gsc_ctr)}
                      </TableCell>
                      <TableCell className="py-2.5 pl-4 text-right tabular-nums text-slate-600" data-label="Avg position">
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


          </div>
        </div>
        <div className="overview-support-grid">
          <SiteAuthorityCard />
          <Card className="overview-panel p-5">
            <h2 className="overview-section-title">Data coverage</h2>
        <p className="mt-3 text-xs text-slate-500">URLs with Search Console data: {formatNumber(data.metrics.gsc_pages)} · With Analytics data: {formatNumber(data.metrics.ga4_pages)}</p>
            <p className="mt-2 text-xs text-slate-500">These counts reflect the latest stored catalog signals. Individual URL reporting windows may differ from whole-site reports.</p>
            <Link to="/settings?tab=data-sources" className="mt-3 inline-block text-sm font-medium text-[#5746d9]">Manage data sources →</Link>
          </Card>
        </div>
      </section>
    </div>
  );
}
