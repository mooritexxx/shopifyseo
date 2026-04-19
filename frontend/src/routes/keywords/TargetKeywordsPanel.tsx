import { useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Download, RefreshCw, Search, Sparkles, X } from "lucide-react";

import { Button } from "../../components/ui/button";
import { Checkbox } from "../../components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../components/ui/select";
import { downloadGoogleAdsKeywordsCsv } from "../../lib/google-ads-keywords-csv";
import { cn } from "../../lib/utils";
import { getJson, patchJson, postJson } from "../../lib/api";
import { messageSchema } from "../../types/api";
import { targetPayloadSchema, type TargetKeyword } from "./schemas";
import {
  DifficultyBadge,
  IntentBadge,
  OpportunityBadge,
  RankingBadge,
  FilterDropdown,
  INTENT_OPTIONS,
  DIFFICULTY_OPTIONS,
  RANKING_OPTIONS,
  VOLUME_OPTIONS,
  OPPORTUNITY_OPTIONS,
  TRAFFIC_POTENTIAL_OPTIONS,
  CONTENT_TYPE_LABELS,
  CONTENT_TYPE_UNSET,
  type IntentFilter,
  type DifficultyFilter,
  type RankingFilter,
  type VolumeFilter,
  type OpportunityFilter,
  type TrafficPotentialFilter,
} from "./badges";
import { startKeywordResearchSse } from "./sse";

type SortKey = keyof TargetKeyword;
type SortDir = "asc" | "desc";

type TargetStatusTab = "approved" | "new" | "dismissed";

const STATUS_TABS: { id: TargetStatusTab; label: string }[] = [
  { id: "approved", label: "Approved" },
  { id: "new", label: "New" },
  { id: "dismissed", label: "Dismissed" },
];

/** Shared grid for header + virtual rows so columns stay aligned with horizontal scroll. */
const TARGET_KW_GRID_TEMPLATE =
  "40px minmax(200px,2.2fr) 5rem 3.75rem 5.5rem 4.25rem 5.75rem minmax(7rem,1fr) 5.5rem 4rem 4rem 4rem 5.25rem 7rem";

export type TargetKeywordsPanelProps = {
  /** True while seed keyword research (SSE) is running from the Seed Keywords tab. */
  seedResearchRunning?: boolean;
};

export function TargetKeywordsPanel({ seedResearchRunning = false }: TargetKeywordsPanelProps) {
  const queryClient = useQueryClient();

  // Search & filters
  const [searchQuery, setSearchQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const listParentRef = useRef<HTMLDivElement>(null);
  const [intentFilter, setIntentFilter] = useState<IntentFilter>("all");
  const [statusTab, setStatusTab] = useState<TargetStatusTab>("approved");
  const [difficultyFilter, setDifficultyFilter] = useState<DifficultyFilter>("all");
  const [rankingFilter, setRankingFilter] = useState<RankingFilter>("all");
  const [volumeFilter, setVolumeFilter] = useState<VolumeFilter>("all");
  const [opportunityFilter, setOpportunityFilter] = useState<OpportunityFilter>("all");
  const [trafficPotentialFilter, setTrafficPotentialFilter] =
    useState<TrafficPotentialFilter>("all");
  const [contentTypeFilter, setContentTypeFilter] = useState<string>("all");

  const [sortKey, setSortKey] = useState<SortKey>("opportunity");
  const [sortDir, setSortDir] = useState<SortDir>("desc");

  const [selected, setSelected] = useState<Set<string>>(new Set());

  const query = useQuery({
    queryKey: ["target-keywords"],
    queryFn: () => getJson("/api/keywords/target", targetPayloadSchema),
  });

  const [refreshStatus, setRefreshStatus] = useState<"idle" | "running" | "error">("idle");
  const [refreshProgress, setRefreshProgress] = useState("");
  const [refreshError, setRefreshError] = useState("");

  function runRefreshMetrics() {
    setRefreshStatus("running");
    setRefreshProgress("");
    setRefreshError("");
    startKeywordResearchSse("/api/keywords/target/refresh-metrics", {
      onProgress: setRefreshProgress,
      onDone: () => {
        setRefreshStatus("idle");
        setRefreshProgress("");
        queryClient.invalidateQueries({ queryKey: ["target-keywords"] });
      },
      onError: (detail) => {
        setRefreshStatus("error");
        setRefreshError(detail);
        setRefreshProgress("");
      },
    });
  }

  const gscCrossrefMutation = useMutation({
    mutationFn: () => postJson("/api/keywords/target/gsc-crossref", targetPayloadSchema),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["target-keywords"] }),
  });

  const bulkStatusMutation = useMutation({
    mutationFn: ({ keywords, status }: { keywords: string[]; status: string }) =>
      patchJson("/api/keywords/target/bulk-status", messageSchema, { keywords, status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["target-keywords"] });
      setSelected(new Set());
    },
  });

  const singleStatusMutation = useMutation({
    mutationFn: ({ keyword, status }: { keyword: string; status: string }) =>
      patchJson(
        `/api/keywords/target/${encodeURIComponent(keyword)}/status`,
        messageSchema,
        { status },
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["target-keywords"] }),
  });

  const items = query.data?.items ?? [];
  const lastRun = query.data?.last_run ?? null;

  const approvedKeywords = useMemo(
    () => items.filter((i) => (i.status?.toLowerCase() ?? "") === "approved").map((i) => i.keyword),
    [items],
  );

  const contentTypeOptions = useMemo(() => {
    const seen = new Set<string>();
    let hasUnset = false;
    for (const i of items) {
      const c = (i.content_type ?? "").trim();
      if (!c) hasUnset = true;
      else seen.add(c);
    }
    const sorted = Array.from(seen).sort((a, b) => a.localeCompare(b));
    const opts: { value: string; label: string }[] = [{ value: "all", label: "All" }];
    if (hasUnset) opts.push({ value: CONTENT_TYPE_UNSET, label: "Not set" });
    for (const c of sorted) {
      opts.push({ value: c, label: CONTENT_TYPE_LABELS[c] ?? c });
    }
    return opts;
  }, [items]);

  const activeFilterCount =
    (intentFilter !== "all" ? 1 : 0) +
    (difficultyFilter !== "all" ? 1 : 0) +
    (rankingFilter !== "all" ? 1 : 0) +
    (volumeFilter !== "all" ? 1 : 0) +
    (opportunityFilter !== "all" ? 1 : 0) +
    (trafficPotentialFilter !== "all" ? 1 : 0) +
    (contentTypeFilter !== "all" ? 1 : 0);

  const filtered = useMemo(() => {
    let list = items;

    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      list = list.filter((i) => i.keyword.toLowerCase().includes(q));
    }

    list = list.filter((i) => (i.status?.toLowerCase() ?? "new") === statusTab);

    if (intentFilter !== "all") {
      list = list.filter((i) => i.intent?.toLowerCase() === intentFilter);
    }
    if (difficultyFilter !== "all") {
      list = list.filter((i) => {
        const kd = i.difficulty;
        if (kd === null) return false;
        if (difficultyFilter === "easy") return kd <= 20;
        if (difficultyFilter === "medium") return kd >= 21 && kd <= 50;
        if (difficultyFilter === "hard") return kd >= 51 && kd <= 70;
        return true;
      });
    }
    if (rankingFilter !== "all") {
      list = list.filter((item) => (item.ranking_status ?? "not_ranking") === rankingFilter);
    }
    if (volumeFilter !== "all") {
      list = list.filter((i) => {
        const v = i.volume;
        if (v === null) return false;
        if (volumeFilter === "v0") return v === 0;
        if (volumeFilter === "v1_100") return v >= 1 && v <= 100;
        if (volumeFilter === "v101_500") return v >= 101 && v <= 500;
        if (volumeFilter === "v501_2000") return v >= 501 && v <= 2000;
        if (volumeFilter === "v2001") return v >= 2001;
        return true;
      });
    }
    if (opportunityFilter !== "all") {
      list = list.filter((i) => {
        const o = i.opportunity;
        if (opportunityFilter === "opp_none") return o === null || o === 0;
        if (o === null || o === 0) return false;
        if (opportunityFilter === "opp_high") return o >= 70;
        if (opportunityFilter === "opp_mid") return o >= 30 && o < 70;
        if (opportunityFilter === "opp_low") return o >= 1 && o < 30;
        return true;
      });
    }
    if (trafficPotentialFilter !== "all") {
      list = list.filter((i) => {
        const tp = i.traffic_potential;
        if (tp === null) return false;
        if (trafficPotentialFilter === "tp0") return tp === 0;
        if (trafficPotentialFilter === "tp1_500") return tp >= 1 && tp <= 500;
        if (trafficPotentialFilter === "tp501_2000") return tp >= 501 && tp <= 2000;
        if (trafficPotentialFilter === "tp2001") return tp >= 2001;
        return true;
      });
    }
    if (contentTypeFilter !== "all") {
      list = list.filter((i) => {
        const c = (i.content_type ?? "").trim();
        if (contentTypeFilter === CONTENT_TYPE_UNSET) return !c;
        return c === contentTypeFilter;
      });
    }

    list = [...list].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });

    return list;
  }, [
    items,
    searchQuery,
    statusTab,
    intentFilter,
    difficultyFilter,
    rankingFilter,
    volumeFilter,
    opportunityFilter,
    trafficPotentialFilter,
    contentTypeFilter,
    sortKey,
    sortDir,
  ]);

  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => listParentRef.current,
    estimateSize: () => 52,
    overscan: 14,
    getItemKey: (index) => filtered[index]?.keyword ?? index,
  });

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  }

  function sortIndicator(key: SortKey) {
    if (sortKey !== key) return null;
    return sortDir === "asc" ? " ↑" : " ↓";
  }

  const allFilteredSelected =
    filtered.length > 0 && filtered.every((i) => selected.has(i.keyword));

  function toggleAll() {
    if (allFilteredSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        filtered.forEach((i) => next.delete(i.keyword));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        filtered.forEach((i) => next.add(i.keyword));
        return next;
      });
    }
  }

  function toggleOne(keyword: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(keyword)) next.delete(keyword);
      else next.add(keyword);
      return next;
    });
  }

  const selectedList = Array.from(selected);

  return (
    <div className="rounded-[24px] border border-line/80 bg-white">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 pt-5">
        <div>
          <h3 className="text-lg font-semibold text-ink">Target Keywords</h3>
          <p className="mt-1 text-sm text-slate-500">
            Keywords from your seeds via Keywords Explorer (related, matching,
            suggestions), merged into one list. Competitor organic keywords are
            pulled separately from the Competitors tab.
          </p>
          {lastRun && (
            <p className="mt-1 text-xs text-slate-400">
              Last run: {new Date(lastRun).toLocaleString()} · {items.length}{" "}
              keywords
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={approvedKeywords.length === 0}
            onClick={() => downloadGoogleAdsKeywordsCsv(approvedKeywords, "keywords-template.csv")}
            title="Single-column CSV (Keyword) for Google Ads import — all approved target keywords"
          >
            <Download className="mr-1.5 h-3.5 w-3.5" />
            Download CSV
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={gscCrossrefMutation.isPending}
            onClick={() => gscCrossrefMutation.mutate()}
          >
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            {gscCrossrefMutation.isPending
              ? "Matching…"
              : "Cross-reference GSC"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={refreshStatus === "running" || seedResearchRunning}
            onClick={runRefreshMetrics}
          >
            <RefreshCw className={cn("mr-1.5 h-3.5 w-3.5", refreshStatus === "running" && "animate-spin")} />
            {refreshStatus === "running"
              ? "Refreshing…"
              : "Refresh metrics"}
          </Button>
        </div>
      </div>

      {/* Refresh metrics progress */}
      {refreshStatus === "running" && refreshProgress && (
        <div className="mx-5 mt-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-600">
          {refreshProgress}
        </div>
      )}

      {/* Refresh metrics error */}
      {refreshStatus === "error" && (
        <div className="mx-5 mt-3 flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
          <span className="flex-1">
            {refreshError || "Refresh failed — please try again."}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 opacity-60 hover:opacity-100"
            onClick={() => {
              setRefreshStatus("idle");
              setRefreshError("");
            }}
            aria-label="Dismiss error"
          >
            <X className="size-4" />
          </Button>
        </div>
      )}

      {/* ── Toolbar: search + filter buttons + bulk actions ─────── */}
      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line/60 px-5 py-3 mt-4">
          {/* Search */}
          <div className="relative min-w-[180px] max-w-xs flex-1">
            <Search
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
            />
            <input
              ref={searchRef}
              type="text"
              placeholder="Search keywords…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-8 w-full rounded-lg border border-line bg-white pl-9 pr-8 text-sm text-ink placeholder:text-slate-400 focus:border-[#2e6be6] focus:outline-none focus:ring-1 focus:ring-[#2e6be6]/30"
            />
            {searchQuery ? (
              <button
                type="button"
                onClick={() => {
                  setSearchQuery("");
                  searchRef.current?.focus();
                }}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-600"
              >
                <X size={13} />
              </button>
            ) : null}
          </div>

          {/* Filter dropdowns */}
          <FilterDropdown
            label="Intent"
            options={INTENT_OPTIONS}
            value={intentFilter}
            onChange={setIntentFilter}
          />
          <FilterDropdown
            label="KD"
            options={DIFFICULTY_OPTIONS}
            value={difficultyFilter}
            onChange={setDifficultyFilter}
          />
          <FilterDropdown
            label="Volume"
            options={VOLUME_OPTIONS}
            value={volumeFilter}
            onChange={setVolumeFilter}
          />
          <FilterDropdown
            label="Traffic pot."
            options={TRAFFIC_POTENTIAL_OPTIONS}
            value={trafficPotentialFilter}
            onChange={setTrafficPotentialFilter}
          />
          <FilterDropdown
            label="Opportunity"
            options={OPPORTUNITY_OPTIONS}
            value={opportunityFilter}
            onChange={setOpportunityFilter}
          />
          {contentTypeOptions.length > 1 ? (
            <FilterDropdown
              label="Content type"
              options={contentTypeOptions}
              value={contentTypeFilter}
              onChange={setContentTypeFilter}
            />
          ) : null}
          <FilterDropdown
            label="Ranking"
            options={RANKING_OPTIONS}
            value={rankingFilter}
            onChange={setRankingFilter}
          />

          {activeFilterCount > 0 ? (
            <button
              type="button"
              onClick={() => {
                setIntentFilter("all");
                setDifficultyFilter("all");
                setRankingFilter("all");
                setVolumeFilter("all");
                setOpportunityFilter("all");
                setTrafficPotentialFilter("all");
                setContentTypeFilter("all");
              }}
              className="text-xs text-slate-400 hover:text-slate-600"
            >
              Clear
            </button>
          ) : null}

          {/* Spacer */}
          <div className="flex-1" />

          {/* Bulk actions */}
          {selectedList.length > 0 ? (
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-slate-500">
                {selectedList.length} selected
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={bulkStatusMutation.isPending}
                onClick={() =>
                  bulkStatusMutation.mutate({
                    keywords: selectedList,
                    status: "approved",
                  })
                }
              >
                <Check className="mr-1 h-3.5 w-3.5" />
                Approve
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={bulkStatusMutation.isPending}
                onClick={() =>
                  bulkStatusMutation.mutate({
                    keywords: selectedList,
                    status: "dismissed",
                  })
                }
              >
                <X className="mr-1 h-3.5 w-3.5" />
                Dismiss
              </Button>
            </div>
          ) : null}

          {/* Count */}
          <span className="text-xs text-slate-400 tabular-nums">
            {filtered.length} of {items.length}
          </span>
        </div>
      )}

      {/* Status tabs — above column headers */}
      {items.length > 0 && (
        <div
          className="flex items-stretch gap-0 border-b border-line/60 px-5"
          role="tablist"
          aria-label="Keyword status"
        >
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={statusTab === tab.id}
              onClick={() => setStatusTab(tab.id)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                statusTab === tab.id
                  ? "border-ocean text-ocean"
                  : "border-transparent text-slate-500 hover:text-slate-700",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      )}

      {/* ── Table ──────────────────────────────────────────────── */}
      {query.isError ? (
        <div
          className="mx-5 my-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
          role="alert"
        >
          <p className="font-medium">Could not load target keywords</p>
          <p className="mt-2 whitespace-pre-wrap text-red-800">
            {query.error instanceof Error ? query.error.message : String(query.error)}
          </p>
        </div>
      ) : query.isLoading ? (
        <div className="flex min-h-[120px] items-center justify-center text-sm text-slate-400 px-5 py-6">
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="mx-5 my-6 flex min-h-[120px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
          No target keywords yet — add seed keywords and run keyword research.
        </div>
      ) : filtered.length === 0 ? (
        <div className="mx-5 my-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
          {searchQuery.trim()
            ? "No keywords match your search."
            : activeFilterCount > 0
              ? "No keywords match the current filters."
              : `No ${STATUS_TABS.find((t) => t.id === statusTab)?.label.toLowerCase() ?? statusTab} keywords yet.`}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <div className="min-w-[1080px] text-sm">
            <div
              className="grid border-b border-line bg-white text-left text-xs font-medium text-slate-400"
              style={{ gridTemplateColumns: TARGET_KW_GRID_TEMPLATE }}
            >
              <div className="flex items-center py-2 pl-5 pr-2">
                <Checkbox
                  checked={allFilteredSelected}
                  onCheckedChange={() => toggleAll()}
                  className="h-4 w-4"
                />
              </div>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("keyword")}
              >
                Keyword{sortIndicator("keyword")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("volume")}
              >
                Volume{sortIndicator("volume")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("difficulty")}
              >
                KD{sortIndicator("difficulty")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("traffic_potential")}
              >
                Traffic Pot.{sortIndicator("traffic_potential")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("cpc")}
              >
                CPC{sortIndicator("cpc")}
              </button>
              <div className="whitespace-nowrap py-2 pr-2">Intent</div>
              <div className="whitespace-nowrap py-2 pr-2">Content Type</div>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("opportunity")}
              >
                Opportunity{sortIndicator("opportunity")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("gsc_position")}
              >
                Position{sortIndicator("gsc_position")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("gsc_clicks")}
              >
                Clicks{sortIndicator("gsc_clicks")}
              </button>
              <button
                type="button"
                className="cursor-pointer whitespace-nowrap py-2 pr-2 text-left hover:text-ink"
                onClick={() => toggleSort("gsc_impressions")}
              >
                Imp.{sortIndicator("gsc_impressions")}
              </button>
              <div className="whitespace-nowrap py-2 pr-2">Ranking</div>
              <div className="whitespace-nowrap py-2 pr-5">Status</div>
            </div>

            <div
              ref={listParentRef}
              className="max-h-[min(70vh,720px)] min-h-[200px] overflow-y-auto border-b border-line/60"
            >
              <div
                className="relative w-full"
                style={{ height: `${virtualizer.getTotalSize()}px` }}
              >
                {virtualizer.getVirtualItems().map((virtualRow) => {
                  const item = filtered[virtualRow.index]!;
                  return (
                    <div
                      key={item.keyword}
                      role="row"
                      data-index={virtualRow.index}
                      ref={virtualizer.measureElement}
                      className="group absolute left-0 top-0 box-border w-full border-b border-line/60 bg-white hover:bg-slate-50/60"
                      style={{
                        transform: `translateY(${virtualRow.start}px)`,
                      }}
                    >
                      <div
                        className="grid items-center py-2.5 text-sm"
                        style={{ gridTemplateColumns: TARGET_KW_GRID_TEMPLATE }}
                      >
                        <div className="flex items-center pl-5 pr-2">
                          <Checkbox
                            checked={selected.has(item.keyword)}
                            onCheckedChange={() => toggleOne(item.keyword)}
                            className="h-4 w-4"
                          />
                        </div>
                        <div className="min-w-0 pr-2 font-medium">
                          <a
                            href={`https://www.google.com/search?q=${encodeURIComponent(item.keyword)}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block truncate text-ink underline-offset-2 hover:text-ocean hover:underline focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ocean/25"
                            title={`Google: ${item.keyword}`}
                          >
                            {item.keyword}
                          </a>
                        </div>
                        <div className="pr-2 text-slate-600">
                          {item.volume !== null ? item.volume.toLocaleString() : "—"}
                        </div>
                        <div className="pr-2">
                          <DifficultyBadge kd={item.difficulty} />
                        </div>
                        <div className="pr-2 text-slate-600">
                          {item.traffic_potential !== null
                            ? item.traffic_potential.toLocaleString()
                            : "—"}
                        </div>
                        <div className="pr-2 text-slate-600">
                          {item.cpc !== null ? `$${(item.cpc / 100).toFixed(2)}` : "—"}
                        </div>
                        <div className="pr-2">
                          <IntentBadge intent={item.intent} />
                        </div>
                        <div className="min-w-0 pr-2 text-slate-600">
                          {item.content_type ?? (
                            <span className="text-slate-400">—</span>
                          )}
                        </div>
                        <div className="pr-2">
                          <OpportunityBadge opp={item.opportunity} />
                        </div>
                        <div className="pr-2 text-slate-600">
                          {item.gsc_position !== null && item.gsc_position !== undefined ? (
                            item.gsc_position.toFixed(1)
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </div>
                        <div className="pr-2 text-slate-600">
                          {item.gsc_clicks !== null && item.gsc_clicks !== undefined ? (
                            item.gsc_clicks
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </div>
                        <div className="pr-2 text-slate-600">
                          {item.gsc_impressions !== null &&
                          item.gsc_impressions !== undefined ? (
                            item.gsc_impressions
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </div>
                        <div className="pr-2">
                          <RankingBadge status={item.ranking_status} />
                        </div>
                        <div className="pr-5">
                          <Select
                            value={item.status}
                            onValueChange={(value) =>
                              singleStatusMutation.mutate({
                                keyword: item.keyword,
                                status: value,
                              })
                            }
                          >
                            <SelectTrigger className="h-7 rounded-lg border-line bg-white px-2 py-1 text-xs text-ink focus:border-ocean focus:ring-1 focus:ring-ocean">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="new">New</SelectItem>
                              <SelectItem value="approved">Approved</SelectItem>
                              <SelectItem value="dismissed">Dismissed</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Footer */}
      {items.length > 0 && (
        <div className="border-t border-line/60 px-5 py-2.5">
          <p className="text-xs text-slate-400">
            {filtered.length} of {items.length} keyword
            {items.length !== 1 ? "s" : ""}
          </p>
        </div>
      )}
    </div>
  );
}
