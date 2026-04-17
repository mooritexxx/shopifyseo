import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Activity, ArrowRight, FileSearch, LayoutDashboard, MousePointerClick, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "../components/ui/select";
import { Toast, type ToastVariant } from "../components/ui/toast";
import { detectToastVariant } from "../lib/toast-utils";
import { getJson, postJson } from "../lib/api";
import { cn, formatNumber, formatRelativeTimestamp } from "../lib/utils";
import { actionSchema, googleSignalsSchema } from "../types/api";

const cardSurface =
  "rounded-2xl border border-[#e8e4f8] bg-white shadow-[0_2px_12px_rgba(87,70,217,0.06)]";
const cardElevated = "rounded-[24px] border border-[#e8e4f8] bg-white shadow-[0_2px_20px_rgba(15,23,42,0.04)]";
const inputClass =
  "rounded-xl border border-[#e8e4f8] bg-white px-4 py-3 text-sm text-ink outline-none transition placeholder:text-slate-400 focus:border-[#5746d9] focus:ring-2 focus:ring-[#5746d9]/15";

function errorMessage(error: unknown) {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string") return error;
  if (error && typeof error === "object") {
    const candidate = (error as { message?: unknown }).message;
    if (typeof candidate === "string" && candidate) return candidate;
    try {
      return JSON.stringify(error);
    } catch {
      return "Request failed";
    }
  }
  return "Request failed";
}

function formatCompactTimestamp() {
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit"
  }).format(new Date());
}

function cacheHintBlock(cache: { label: string; text: string; meta?: Record<string, unknown> | null }) {
  const raw = cache.meta?.fetched_at;
  const ts =
    raw != null ? (typeof raw === "number" ? raw : Number(raw)) : null;
  const relative =
    ts != null && Number.isFinite(ts) ? formatRelativeTimestamp(ts).split(" · ")[0] : null;
  return (
    <div className="space-y-0.5">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">{cache.label}</p>
      {relative ? <p className="text-xs font-medium text-slate-700">Refreshed {relative}</p> : null}
      <p className="text-xs text-slate-500">{cache.text}</p>
    </div>
  );
}

function CacheStatCard({
  title,
  cache
}: {
  title: string;
  cache: { label: string; text: string; meta?: Record<string, unknown> | null };
}) {
  return (
    <div className={cn(cardSurface, "p-4")}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">{title}</p>
      <p className="mt-2 font-mono text-lg font-bold tabular-nums text-[#5746d9]">{cache.label}</p>
      <div className="mt-2 border-t border-[#ede9f7] pt-3">{cacheHintBlock(cache)}</div>
    </div>
  );
}

function DataTableCard({
  icon: Icon,
  title,
  rangeLabel,
  children,
  empty,
  className
}: {
  icon: React.ComponentType<{ className?: string; size?: number }>;
  title: string;
  rangeLabel?: string;
  children: React.ReactNode;
  empty?: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={cn("overflow-hidden border-[#e8e4f8] p-0", cardElevated, className)}>
      <div className="border-b border-[#ede9f7] bg-[linear-gradient(180deg,#ffffff_0%,#faf8ff_100%)] px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Icon className="shrink-0 text-[#5746d9]" size={18} />
            <h3 className="text-base font-bold text-ink">{title}</h3>
          </div>
          {rangeLabel ? (
            <span className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
              {rangeLabel}
            </span>
          ) : null}
        </div>
      </div>
      <div className="p-2">
        {empty ? <div className="px-3 py-6 text-center text-sm text-slate-500">{empty}</div> : children}
      </div>
    </Card>
  );
}

function MetricRow({
  left,
  right,
  muted
}: {
  left: React.ReactNode;
  right: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
        muted ? "text-slate-400" : "text-slate-700 hover:bg-[#f5f3ff]/60"
      )}
    >
      {/* flex-1 + min-w-0: reserve space for the label after the metrics column sizes (narrow cards used to collapse this to 0). */}
      <span className="min-w-0 flex-1 truncate font-medium">{left}</span>
      <span className="shrink-0 tabular-nums text-slate-600">{right}</span>
    </div>
  );
}

function PageSkeleton() {
  return (
    <div className="space-y-8 pb-8">
      <div className="space-y-3">
        <div className="h-3 w-32 animate-pulse rounded bg-slate-200" />
        <div className="h-10 w-2/3 max-w-md animate-pulse rounded-lg bg-slate-200" />
        <div className="h-4 w-full max-w-xl animate-pulse rounded bg-slate-100" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-48 animate-pulse rounded-[24px] bg-slate-100" />
        ))}
      </div>
    </div>
  );
}

export function GoogleSignalsPage() {
  const queryClient = useQueryClient();
  const [toast, setToast] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["google-signals"],
    queryFn: () => getJson("/api/google-signals", googleSignalsSchema)
  });

  const data = query.data;
  const [selection, setSelection] = useState({ site_url: "", ga4_property_id: "" });
  useEffect(() => {
    if (data) {
      setSelection({
        site_url: data.selected_site,
        ga4_property_id: data.ga4_property_id
      });
    }
  }, [data]);
  const saveMutation = useMutation({
    mutationFn: () => postJson("/api/google-signals/site", actionSchema, selection),
    onSuccess: (result) => {
      setToast(result.message);
      setLastAction(`Settings saved at ${formatCompactTimestamp()}`);
      void queryClient.invalidateQueries({ queryKey: ["google-signals"] });
    },
    onError: (error) => setToast(errorMessage(error))
  });
  const refreshMutation = useMutation({
    mutationFn: (scope: string) =>
      postJson("/api/google-signals/refresh", actionSchema, { message: "", result: { scope } }),
    onSuccess: (result) => {
      setToast(result.message);
      setLastAction(`${result.message} at ${formatCompactTimestamp()}`);
      void queryClient.invalidateQueries({ queryKey: ["google-signals"] });
    },
    onError: (error) => setToast(errorMessage(error))
  });

  if (query.isLoading) {
    return <PageSkeleton />;
  }
  if (query.error || !data) {
    return (
      <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">
        {(query.error as Error)?.message || "Could not load Google signals."}
      </div>
    );
  }

  const refreshingScope = refreshMutation.isPending ? refreshMutation.variables : null;
  const ga4Empty = data.ga4_rows.length === 0;
  const bd = data.gsc_property_breakdowns;
  const bdTopError = bd.error || "";

  return (
    <div className="space-y-8 pb-8">
      {toast ? <Toast variant={detectToastVariant(toast)}>{toast}</Toast> : null}

      <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-500">Integrations</p>
          <h2 className="mt-2 text-4xl font-bold text-ink">Google Signals</h2>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Connect Search Console and GA4, choose properties, and refresh cached summaries used on the overview
            dashboard.
          </p>
        </div>
        <Link
          to="/"
          className="inline-flex items-center gap-2 self-start rounded-xl border border-[#e8e4f8] bg-white px-4 py-2.5 text-sm font-semibold text-[#5746d9] shadow-[0_2px_12px_rgba(87,70,217,0.08)] transition hover:border-[#d4ccf5] hover:bg-[#faf8ff]"
        >
          <LayoutDashboard size={18} />
          Overview dashboard
          <ArrowRight size={14} />
        </Link>
      </div>

      {!data.configured || !data.connected ? (
        <Card className="border-[#e8e4f8] bg-[linear-gradient(135deg,#ffffff_0%,#faf8ff_100%)] p-8 shadow-[0_2px_20px_rgba(15,23,42,0.04)]">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-6">
            <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-[#e8e4f8] bg-white shadow-[0_2px_12px_rgba(87,70,217,0.08)]">
              <MousePointerClick className="text-[#5746d9]" size={28} />
            </div>
            <div className="min-w-0 flex-1 space-y-3">
              <p className="text-lg font-bold text-ink">Google is not connected</p>
              <p className="text-sm text-slate-600">{data.error || "Sign in with Google to use Search Console and GA4 data in this app."}</p>
              {data.auth_url ? (
                <a
                  className="inline-flex items-center gap-2 rounded-xl bg-[#5746d9] px-5 py-3 text-sm font-semibold text-white shadow-[0_4px_20px_rgba(87,70,217,0.35)] transition hover:bg-[#4a3bc4]"
                  href={data.auth_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  Connect Google
                  <ArrowRight size={16} />
                </a>
              ) : null}
            </div>
          </div>
        </Card>
      ) : (
        <div className="space-y-10">
          <div className="grid w-full gap-8 xl:grid-cols-[minmax(16rem,22rem)_minmax(0,1fr)] xl:items-start">
          <div className="min-w-0 space-y-6">
            <Card className={cn("w-full p-6", cardElevated)}>
              <div className="mb-5 flex items-center gap-2 border-b border-[#ede9f7] pb-4">
                <FileSearch className="text-[#5746d9]" size={20} />
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">Configuration</p>
                  <p className="text-lg font-bold text-ink">Properties</p>
                </div>
              </div>
              <div className="space-y-5">
                <label className="grid gap-2">
                  <span className="text-sm font-semibold text-ink">Search Console property</span>
                  <Select
                    value={selection.site_url || undefined}
                    onValueChange={(value) =>
                      setSelection((current) => ({ ...current, site_url: value }))
                    }
                  >
                    <SelectTrigger className={inputClass}>
                      <SelectValue placeholder="Select a property" />
                    </SelectTrigger>
                    <SelectContent>
                      {data.available_sites.map((site) => (
                        <SelectItem key={site} value={site}>
                          {site}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
                <label className="grid gap-2">
                  <span className="text-sm font-semibold text-ink">GA4 property ID</span>
                  <Input
                    className={inputClass}
                    placeholder="properties/123456789 or numeric ID"
                    value={selection.ga4_property_id}
                    onChange={(event) =>
                      setSelection((current) => ({ ...current, ga4_property_id: event.target.value }))
                    }
                  />
                </label>
                <div className="flex flex-wrap gap-2">
                  <Button
                    className="rounded-xl bg-[#5746d9] text-white hover:bg-[#4a3bc4]"
                    onClick={() => saveMutation.mutate()}
                    disabled={saveMutation.isPending}
                  >
                    {saveMutation.isPending ? <RefreshCw className="animate-spin" size={16} /> : null}
                    {saveMutation.isPending ? "Saving…" : "Save settings"}
                  </Button>
                  <Button
                    variant="secondary"
                    className="rounded-xl border-[#e8e4f8] bg-white"
                    onClick={() => refreshMutation.mutate("search_console_summary")}
                    disabled={refreshMutation.isPending}
                  >
                    <RefreshCw
                      className={refreshingScope === "search_console_summary" ? "animate-spin" : ""}
                      size={16}
                    />
                    {refreshingScope === "search_console_summary" ? "Refreshing GSC…" : "Refresh GSC"}
                  </Button>
                  <Button
                    variant="secondary"
                    className="rounded-xl border-[#e8e4f8] bg-white"
                    onClick={() => refreshMutation.mutate("ga4_summary")}
                    disabled={refreshMutation.isPending}
                  >
                    <RefreshCw
                      className={refreshingScope === "ga4_summary" ? "animate-spin" : ""}
                      size={16}
                    />
                    {refreshingScope === "ga4_summary" ? "Refreshing GA4…" : "Refresh GA4"}
                  </Button>
                </div>
              </div>
            </Card>

            <div className={cn(cardSurface, "w-full p-5")}>
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">Quick guide</p>
              <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm text-slate-600">
                <li>Confirm the Search Console property matches your live site.</li>
                <li>Paste your GA4 property ID (numeric or full resource name).</li>
                <li>Save, then refresh GSC and GA4 to populate caches.</li>
              </ol>
              <p className="mt-4 border-t border-[#ede9f7] pt-4 text-xs text-slate-500">
                <span className="font-semibold text-slate-600">Last action:</span>{" "}
                {lastAction || "None yet"}
              </p>
            </div>

            <div className="grid w-full gap-4 sm:grid-cols-2">
              <CacheStatCard title="Search Console cache" cache={data.gsc_cache} />
              <CacheStatCard title="GA4 cache" cache={data.ga4_cache} />
            </div>

            {refreshingScope ? (
              <p className="rounded-xl border border-[#dbe5f3] bg-[#f0f9ff] px-4 py-3 text-sm font-medium text-[#0369a1]">
                Refreshing {refreshingScope === "search_console_summary" ? "Search Console" : "GA4"}…
              </p>
            ) : null}
            {data.error ? (
              <p className="rounded-xl border border-[#fecaca] bg-[#fff4ef] px-4 py-3 text-sm text-[#8f3e20]">{data.error}</p>
            ) : null}
            {bdTopError ? (
              <p className="rounded-xl border border-[#fecaca] bg-[#fff4ef] px-4 py-3 text-sm text-[#8f3e20]">
                {bdTopError}
              </p>
            ) : null}
          </div>

          <div className="min-w-0 space-y-5">
            <Card className={cn("w-full p-5", cardElevated)}>
              <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">Search Console</p>
              <p className="mt-2 text-sm font-semibold text-ink">Performance tables moved to Overview</p>
              <p className="mt-2 text-sm text-slate-600">
                Queries, pages, countries, and devices for your selected period and URL path now load on the main
                dashboard with the same GSC connection.
              </p>
              <Link
                to="/"
                className="mt-4 inline-flex items-center gap-2 text-sm font-semibold text-[#5746d9] hover:underline"
              >
                Open Overview
                <ArrowRight size={14} />
              </Link>
            </Card>

            <DataTableCard
              className="w-full"
              icon={Activity}
              title="GA4 landing pages"
              rangeLabel={`${formatNumber(data.ga4_rows.length)} rows cached`}
              empty={
                ga4Empty ? (
                  <>
                    No GA4 rows yet. Save the property ID, then <strong>Refresh GA4</strong>.
                  </>
                ) : undefined
              }
            >
              {!ga4Empty
                ? data.ga4_rows.slice(0, 10).map((row, index) => (
                    <MetricRow
                      key={`${index}-${row.dimensionValues?.[0]?.value || ""}`}
                      left={row.dimensionValues?.[0]?.value || "/"}
                      right={
                        <>
                          {formatNumber(Number(row.metricValues?.[0]?.value || 0))}{" "}
                          <span className="text-slate-400">views</span>
                        </>
                      }
                    />
                  ))
                : null}
            </DataTableCard>
          </div>
          </div>
        </div>
      )}
    </div>
  );
}
