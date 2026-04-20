import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import {
  Lightbulb,
  RefreshCw,
  Sparkles,
  AlertCircle,
  FileText,
  Check,
  X,
  ChevronDown,
  ChevronUp,
  Search,
  Info,
} from "lucide-react";

import { Button } from "../components/ui/button";
import { Checkbox } from "../components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { getJson, patchJson, postJson } from "../lib/api";
import { cn } from "../lib/utils";
import { articleIdeasPayloadSchema, messageSchema } from "../types/api";

// ---------------------------------------------------------------------------
// Constants & helpers
// ---------------------------------------------------------------------------

const INTENT_LABELS: Record<string, { label: string; color: string }> = {
  informational: { label: "Info", color: "bg-blue-50 text-blue-700 border-blue-200" },
  commercial: { label: "Commercial", color: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  transactional: { label: "Transactional", color: "bg-purple-50 text-purple-700 border-purple-200" },
  navigational: { label: "Nav", color: "bg-orange-50 text-orange-700 border-orange-200" },
};

type ArticleIdeaStatusTab = "approved" | "new" | "rejected";

const STATUS_TABS: { id: ArticleIdeaStatusTab; label: string }[] = [
  { id: "approved", label: "Approved" },
  { id: "new", label: "New" },
  { id: "rejected", label: "Rejected" },
];

type IntentFilter = "all" | "informational" | "commercial" | "transactional";
type SourceFilter = "all" | "cluster_gap" | "competitor_gap" | "collection_gap" | "query_gap";

function ideaMatchesStatusTab(idea: { status: string }, tab: ArticleIdeaStatusTab): boolean {
  const s = idea.status?.toLowerCase() ?? "idea";
  if (tab === "new") return s === "idea";
  if (tab === "rejected") return s === "rejected";
  return s === "approved" || s === "published";
}

const INTENT_OPTIONS: { value: IntentFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "informational", label: "Informational" },
  { value: "commercial", label: "Commercial" },
  { value: "transactional", label: "Transactional" },
];

const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "cluster_gap", label: "Cluster Gap" },
  { value: "competitor_gap", label: "Competitor Gap" },
  { value: "collection_gap", label: "Collection Gap" },
  { value: "query_gap", label: "Query Gap" },
];

type SortKey =
  | "suggested_title"
  | "primary_keyword"
  | "total_volume"
  | "avg_difficulty"
  | "article_count"
  | "agg_gsc_clicks"
  | "agg_gsc_impressions"
  | "coverage_pct"
  | "created_at";
type SortDir = "asc" | "desc";

function DifficultyBadge({ kd }: { kd: number }) {
  if (!kd) return <span className="text-slate-400">—</span>;
  const color =
    kd < 30 ? "text-emerald-600" : kd < 60 ? "text-amber-600" : "text-red-600";
  return <span className={`font-semibold ${color}`}>{kd.toFixed(0)}</span>;
}

function IntentBadge({ intent }: { intent: string }) {
  const cfg = INTENT_LABELS[intent] ?? INTENT_LABELS.informational;
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${cfg.color}`}
    >
      {cfg.label}
    </span>
  );
}

function CoverageBadge({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <span className="text-slate-400">—</span>;
  const color =
    pct >= 75 ? "text-emerald-600" : pct >= 40 ? "text-amber-600" : "text-red-600";
  return <span className={`font-semibold ${color}`}>{pct.toFixed(0)}%</span>;
}

// Generic filter-dropdown button
function FilterDropdown<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  const active = value !== ("all" as T);
  const displayLabel = active
    ? options.find((o) => o.value === value)?.label ?? label
    : label;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition whitespace-nowrap ${
            active
              ? "border-[#2e6be6]/30 bg-[#2e6be6]/5 text-[#2e6be6]"
              : "border-line bg-white text-slate-600 hover:border-slate-300 hover:text-ink"
          }`}
        >
          {displayLabel}
          <ChevronDown size={12} className="opacity-50" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[140px]">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(v) => onChange(v as T)}
        >
          {options.map((opt) => (
            <DropdownMenuRadioItem key={opt.value} value={opt.value}>
              {opt.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ---------------------------------------------------------------------------
// Main Page Component
// ---------------------------------------------------------------------------

export function ArticleIdeasPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  // Search & filters
  const [searchQuery, setSearchQuery] = useState("");
  const [statusTab, setStatusTab] = useState<ArticleIdeaStatusTab>("approved");
  const [intentFilter, setIntentFilter] = useState<IntentFilter>("all");
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const searchRef = useRef<HTMLInputElement>(null);

  // Selection
  const [selected, setSelected] = useState<Set<number>>(new Set());

  // Queries & mutations
  const ideasQuery = useQuery({
    queryKey: ["article-ideas"],
    queryFn: () => getJson("/api/article-ideas", articleIdeasPayloadSchema),
  });

  const generateMutation = useMutation({
    mutationFn: () =>
      postJson("/api/article-ideas/generate", articleIdeasPayloadSchema),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["article-ideas"] }),
  });

  const singleStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      patchJson(`/api/article-ideas/${id}/status`, messageSchema, { new_status: status }),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: ["article-ideas"] }),
  });

  const bulkStatusMutation = useMutation({
    mutationFn: ({ ids, status }: { ids: number[]; status: string }) =>
      patchJson("/api/article-ideas/bulk-status", messageSchema, { idea_ids: ids, status }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["article-ideas"] });
      setSelected(new Set());
    },
  });

  const ideas = ideasQuery.data?.items ?? [];

  const newIdeaCount = useMemo(
    () => ideas.filter((i) => (i.status?.toLowerCase() ?? "idea") === "idea").length,
    [ideas],
  );

  const activeFilterCount =
    (intentFilter !== "all" ? 1 : 0) + (sourceFilter !== "all" ? 1 : 0);

  const filtered = useMemo(() => {
    let list = ideas;

    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      list = list.filter(
        (i) =>
          i.suggested_title.toLowerCase().includes(q) ||
          i.primary_keyword.toLowerCase().includes(q) ||
          i.supporting_keywords.some((kw) => kw.toLowerCase().includes(q)),
      );
    }

    list = list.filter((i) => ideaMatchesStatusTab(i, statusTab));
    if (intentFilter !== "all")
      list = list.filter((i) => i.search_intent === intentFilter);
    if (sourceFilter !== "all")
      list = list.filter((i) => i.source_type === sourceFilter);

    list = [...list].sort((a, b) => {
      const av = a[sortKey] ?? -Infinity;
      const bv = b[sortKey] ?? -Infinity;
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return list;
  }, [ideas, searchQuery, statusTab, intentFilter, sourceFilter, sortKey, sortDir]);

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
    return sortDir === "asc" ? (
      <ChevronUp size={12} className="inline ml-0.5" />
    ) : (
      <ChevronDown size={12} className="inline ml-0.5" />
    );
  }

  const allFilteredSelected =
    filtered.length > 0 && filtered.every((i) => selected.has(i.id));

  function toggleAll() {
    if (allFilteredSelected) {
      setSelected((prev) => {
        const next = new Set(prev);
        filtered.forEach((i) => next.delete(i.id));
        return next;
      });
    } else {
      setSelected((prev) => {
        const next = new Set(prev);
        filtered.forEach((i) => next.add(i.id));
        return next;
      });
    }
  }

  function toggleOne(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const selectedList = Array.from(selected);
  const isGenerating = generateMutation.isPending;

  return (
    <div className="space-y-6 pb-12">
      {/* Page header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-slate-500">
            Content Strategy
          </p>
          <h2 className="mt-2 text-4xl font-bold text-ink">Article Ideas</h2>
          <p className="mt-2 text-sm text-slate-500 max-w-xl">
            AI-generated article recommendations based on your keyword cluster
            gaps, collection search demand, and informational queries landing on
            the wrong pages.
          </p>
        </div>
        <div className="shrink-0 pt-2 flex gap-2">
          <Button
            variant="secondary"
            onClick={() => generateMutation.mutate()}
            disabled={isGenerating}
          >
            <RefreshCw
              size={15}
              className={isGenerating ? "animate-spin" : ""}
            />
            {isGenerating ? "Analysing…" : "Generate Ideas"}
          </Button>
        </div>
      </div>

      {!ideasQuery.isLoading && ideas.length > 0 && newIdeaCount > 0 ? (
        <div
          className="flex gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
          role="status"
        >
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-800" aria-hidden />
          <div>
            <p className="font-medium text-amber-950">
              {newIdeaCount} article idea{newIdeaCount === 1 ? "" : "s"} waiting for review
            </p>
            <p className="mt-1 text-amber-950/90">
              Open the New tab to approve or reject each suggestion. Approved ideas stay in your queue for drafting.
            </p>
            <Button
              type="button"
              variant="link"
              className="mt-1 h-auto p-0 text-amber-950 underline-offset-4 hover:text-amber-900"
              onClick={() => setStatusTab("new")}
            >
              Go to New tab
            </Button>
          </div>
        </div>
      ) : null}

      {/* Generation progress / error */}
      {isGenerating ? (
        <div className="flex items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50 px-5 py-3 text-sm text-blue-600">
          <Sparkles size={15} className="animate-pulse" />
          Analysing keyword gaps and generating ideas…
        </div>
      ) : null}

      {generateMutation.isError ? (
        <div className="flex items-start gap-3 rounded-2xl border border-red-200 bg-red-50 px-5 py-4 text-sm text-red-700">
          <AlertCircle size={16} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold">Generation failed</p>
            <p className="mt-0.5 text-red-600">
              {(generateMutation.error as Error)?.message}
            </p>
          </div>
        </div>
      ) : null}

      {/* Loading */}
      {ideasQuery.isLoading ? (
        <div className="flex min-h-[200px] items-center justify-center text-sm text-slate-400">
          Loading ideas…
        </div>
      ) : ideas.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-[24px] border border-dashed border-[#ccd8ee] bg-[#f8faff] py-20 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[#eef4ff]">
            <Lightbulb size={28} className="text-[#2e6be6]" />
          </div>
          <h3 className="mt-4 text-lg font-bold text-ink">
            No article ideas yet
          </h3>
          <p className="mt-2 max-w-sm text-sm text-slate-500">
            Click "Generate Ideas" to analyse your keyword clusters, collection
            gaps, and GSC data — the AI will suggest targeted articles to write.
          </p>
          <Button
            className="mt-6"
            onClick={() => generateMutation.mutate()}
            disabled={isGenerating}
          >
            <Sparkles size={15} />
            Generate Ideas
          </Button>
        </div>
      ) : (
        <div className="rounded-[24px] border border-line/80 bg-white">
          {/* ── Toolbar: search + filter buttons + bulk actions ─────── */}
          <div className="flex flex-wrap items-center gap-2 px-5 py-3 border-b border-line/60">
            {/* Search */}
            <div className="relative min-w-[180px] max-w-xs flex-1">
              <Search
                size={15}
                className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
              />
              <input
                ref={searchRef}
                type="text"
                placeholder="Search ideas…"
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

            {/* Individual filter dropdowns */}
            <FilterDropdown
              label="Intent"
              options={INTENT_OPTIONS}
              value={intentFilter}
              onChange={setIntentFilter}
            />
            <FilterDropdown
              label="Source"
              options={SOURCE_OPTIONS}
              value={sourceFilter}
              onChange={setSourceFilter}
            />

            {activeFilterCount > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setIntentFilter("all");
                  setSourceFilter("all");
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
                      ids: selectedList,
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
                      ids: selectedList,
                      status: "rejected",
                    })
                  }
                >
                  <X className="mr-1 h-3.5 w-3.5" />
                  Reject
                </Button>
              </div>
            ) : null}

            {/* Count */}
            <span className="text-xs text-slate-400 tabular-nums">
              {filtered.length} of {ideas.length}
            </span>
          </div>

          {/* Status tabs — above table (same pattern as Target Keywords) */}
          <div
            className="flex items-stretch gap-0 border-b border-line/60 px-5"
            role="tablist"
            aria-label="Idea status"
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

          {/* ── Table ──────────────────────────────────────────────── */}
          {filtered.length === 0 ? (
            <div className="mx-5 my-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
              {searchQuery.trim()
                ? "No ideas match your search."
                : activeFilterCount > 0
                  ? "No ideas match the current filters."
                  : `No ${STATUS_TABS.find((t) => t.id === statusTab)?.label.toLowerCase() ?? statusTab} ideas yet.`}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <Table className="w-full text-sm">
                <TableHeader>
                  <TableRow className="border-b border-line text-left text-xs font-medium text-slate-400">
                    <TableHead className="pl-5 pb-2 pr-3 w-8">
                      <Checkbox
                        checked={allFilteredSelected}
                        onCheckedChange={() => toggleAll()}
                        className="h-4 w-4"
                      />
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink"
                      onClick={() => toggleSort("suggested_title")}
                    >
                      Title{sortIndicator("suggested_title")}
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink"
                      onClick={() => toggleSort("primary_keyword")}
                    >
                      Primary Keyword{sortIndicator("primary_keyword")}
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink text-right"
                      onClick={() => toggleSort("total_volume")}
                    >
                      Volume{sortIndicator("total_volume")}
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink text-right"
                      onClick={() => toggleSort("avg_difficulty")}
                    >
                      KD{sortIndicator("avg_difficulty")}
                    </TableHead>
                    <TableHead className="whitespace-nowrap pb-2 pr-3">
                      Intent
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink text-right"
                      onClick={() => toggleSort("article_count")}
                    >
                      Articles{sortIndicator("article_count")}
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink text-right"
                      onClick={() => toggleSort("agg_gsc_clicks")}
                    >
                      Clicks{sortIndicator("agg_gsc_clicks")}
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink text-right"
                      onClick={() => toggleSort("agg_gsc_impressions")}
                    >
                      Imp.{sortIndicator("agg_gsc_impressions")}
                    </TableHead>
                    <TableHead
                      className="cursor-pointer whitespace-nowrap pb-2 pr-3 hover:text-ink text-right"
                      onClick={() => toggleSort("coverage_pct")}
                    >
                      Coverage{sortIndicator("coverage_pct")}
                    </TableHead>
                    <TableHead className="whitespace-nowrap pb-2 pr-5">
                      Status
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody className="divide-y divide-line/60">
                  {filtered.map((idea) => (
                    <TableRow
                      key={idea.id}
                      className="group cursor-pointer hover:bg-slate-50/60"
                      onClick={(e) => {
                        const target = e.target as HTMLElement;
                        if (
                          target.closest(
                            "button, [role='combobox'], [role='listbox'], input, label",
                          )
                        )
                          return;
                        navigate(`/article-ideas/${idea.id}`);
                      }}
                    >
                      <TableCell
                        className="py-2.5 pl-5 pr-3"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Checkbox
                          checked={selected.has(idea.id)}
                          onCheckedChange={() => toggleOne(idea.id)}
                          className="h-4 w-4"
                        />
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 max-w-[260px]">
                        <span className="font-medium text-ink truncate block">
                          {idea.suggested_title}
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-slate-600 max-w-[160px] truncate">
                        {idea.primary_keyword || (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                        {idea.total_volume > 0 ? (
                          idea.total_volume.toLocaleString()
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-right">
                        <DifficultyBadge kd={idea.avg_difficulty} />
                      </TableCell>
                      <TableCell className="py-2.5 pr-3">
                        <IntentBadge intent={idea.search_intent} />
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                        {idea.article_count > 0 ? (
                          <Link
                            to={`/article-ideas/${idea.id}`}
                            className="inline-flex items-center justify-end gap-1 font-medium text-ocean hover:underline"
                            title="View linked articles on idea detail"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <FileText size={12} className="text-ocean/70" />
                            {idea.article_count}
                          </Link>
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                        {idea.article_count > 0 ? (
                          idea.agg_gsc_clicks.toLocaleString()
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                        {idea.article_count > 0 ? (
                          idea.agg_gsc_impressions.toLocaleString()
                        ) : (
                          <span className="text-slate-400">—</span>
                        )}
                      </TableCell>
                      <TableCell className="py-2.5 pr-3 text-right">
                        <CoverageBadge pct={idea.coverage_pct} />
                      </TableCell>
                      <TableCell
                        className="py-2.5 pr-5"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Select
                          value={idea.status}
                          onValueChange={(value) =>
                            singleStatusMutation.mutate({
                              id: idea.id,
                              status: value,
                            })
                          }
                        >
                          <SelectTrigger className="h-7 w-[110px] rounded-lg border-line bg-white px-2 py-1 text-xs text-ink focus:border-ocean focus:ring-1 focus:ring-ocean">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value="idea">New</SelectItem>
                            <SelectItem value="approved">Approved</SelectItem>
                            <SelectItem value="published">Published</SelectItem>
                            <SelectItem value="rejected">Rejected</SelectItem>
                          </SelectContent>
                        </Select>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          {/* Footer count */}
          <div className="border-t border-line/60 px-5 py-2.5">
            <p className="text-xs text-slate-400">
              {filtered.length} of {ideas.length} idea
              {ideas.length !== 1 ? "s" : ""}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
