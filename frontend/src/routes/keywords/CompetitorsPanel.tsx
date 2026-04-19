import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, RotateCcw, Search, Sparkles, Trash2, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow
} from "../../components/ui/table";
import type { z } from "zod";

import { getJson, postJson } from "../../lib/api";
import { cn } from "../../lib/utils";
import { competitorPayloadSchema, competitorProfileSchema } from "./schemas";
import { startKeywordResearchSse } from "./sse";

type CompetitorProfileRow = z.infer<typeof competitorProfileSchema>;

const metricTh =
  "pb-2 pr-2 text-right text-[11px] font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap";
const metricTd = "py-2.5 pr-2 text-right tabular-nums text-xs text-slate-600";

function fmtInt(n: number | undefined | null): string {
  if (n == null) return "—";
  if (n === 0) return "0";
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function fmtVis(v: number): string {
  if (v == null || v === 0) return "—";
  if (Math.abs(v) < 1) return v.toFixed(4);
  return v.toFixed(2);
}

function fmtShare(s: number): string {
  return `${(s * 100).toFixed(1)}%`;
}

function LabsMetricHeaders() {
  return (
    <>
      <TableHead className={metricTh} title="Displayed ETV = max(Labs seed-set ETV, Labs bulk domain ETV)">
        Traffic
      </TableHead>
      <TableHead className={metricTh} title="Labs serp_competitors ETV for your seed keywords">
        Seed ETV
      </TableHead>
      <TableHead className={metricTh} title="Labs bulk traffic estimation (full domain)">
        Bulk ETV
      </TableHead>
      <TableHead className={metricTh} title="keywords_count ÷ seeds sent (capped at 100%)">
        Share
      </TableHead>
      <TableHead className={metricTh} title="Labs keywords_count — seeds this domain ranks for">
        Seeds hit
      </TableHead>
      <TableHead className={metricTh} title="Number of seed keywords sent to Labs (cap 200)">
        Seed pool
      </TableHead>
      <TableHead
        className={metricTh}
        title="After research: organic keyword sample size. Before research: same as Labs rating from discovery."
      >
        Org. rows
      </TableHead>
      <TableHead className={metricTh} title="Labs serp_competitors rating">
        Labs rt
      </TableHead>
      <TableHead className={metricTh} title="Labs visibility">
        Vis.
      </TableHead>
      <TableHead className={metricTh} title="Labs average position">
        Avg
      </TableHead>
      <TableHead className={metricTh} title="Labs median position">
        Med
      </TableHead>
    </>
  );
}

function LabsMetricCells({ row }: { row: CompetitorProfileRow }) {
  return (
    <>
      <TableCell className={metricTd}>{fmtInt(row.traffic)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.labs_seed_etv)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.labs_bulk_etv)}</TableCell>
      <TableCell className={metricTd}>{fmtShare(row.share)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.keywords_common)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.keywords_we_have)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.keywords_they_have)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.labs_rating)}</TableCell>
      <TableCell className={metricTd}>{fmtVis(row.labs_visibility)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.labs_avg_position)}</TableCell>
      <TableCell className={metricTd}>{fmtInt(row.labs_median_position)}</TableCell>
    </>
  );
}

type CompetitorStatusTab = "approved" | "new" | "dismissed";

const COMPETITOR_TABS: { id: CompetitorStatusTab; label: string }[] = [
  { id: "approved", label: "Approved" },
  { id: "new", label: "New" },
  { id: "dismissed", label: "Dismissed" }
];

export type CompetitorsPanelProps = {
  onOpenSeedKeywordsTab?: () => void;
  onOpenTargetKeywordsTab?: () => void;
};

export function CompetitorsPanel({
  onOpenSeedKeywordsTab,
  onOpenTargetKeywordsTab
}: CompetitorsPanelProps = {}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [newDomain, setNewDomain] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [researchStatus, setResearchStatus] = useState<"idle" | "running" | "error">("idle");
  const [researchProgress, setResearchProgress] = useState("");
  const [researchError, setResearchError] = useState("");
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [discoverNote, setDiscoverNote] = useState<string | null>(null);
  const [competitorTab, setCompetitorTab] = useState<CompetitorStatusTab>("approved");
  const [domainFilter, setDomainFilter] = useState("");
  const domainSearchRef = useRef<HTMLInputElement>(null);

  const query = useQuery({
    queryKey: ["competitor-domains"],
    queryFn: () => getJson("/api/keywords/competitors", competitorPayloadSchema)
  });

  const discoverMutation = useMutation({
    mutationFn: () =>
      postJson("/api/keywords/competitors/discover-from-seed", competitorPayloadSchema, {}),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setDiscoverError(null);
      setCompetitorTab("new");
      const n = data.pending_suggestions?.length ?? data.suggestions?.length ?? 0;
      const cost = data.unit_cost != null ? ` · DataForSEO cost (USD): ${Number(data.unit_cost).toFixed(4)}` : "";
      const tgt = data.target_domain ? ` for ${data.target_domain}` : "";
      setDiscoverNote(n > 0 ? `Found ${n} suggested competitor(s)${tgt}${cost}.` : `No new suggestions${tgt}${cost}.`);
    },
    onError: (e: Error) => {
      setDiscoverError(e.message);
      setDiscoverNote(null);
    }
  });

  const approvePendingMutation = useMutation({
    mutationFn: (domain: string) =>
      postJson(`/api/keywords/competitors/pending/${encodeURIComponent(domain)}/approve`, competitorPayloadSchema, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setDiscoverError(null);
    },
    onError: (e: Error) => setDiscoverError(e.message)
  });

  const rejectPendingMutation = useMutation({
    mutationFn: (domain: string) =>
      postJson(`/api/keywords/competitors/pending/${encodeURIComponent(domain)}/reject`, competitorPayloadSchema, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setDiscoverError(null);
    },
    onError: (e: Error) => setDiscoverError(e.message)
  });

  const clearPendingMutation = useMutation({
    mutationFn: () => postJson("/api/keywords/competitors/pending/clear", competitorPayloadSchema, {}),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setDiscoverNote(null);
    },
    onError: (e: Error) => setDiscoverError(e.message)
  });

  const restoreDismissedMutation = useMutation({
    mutationFn: (domain: string) =>
      postJson(
        `/api/keywords/competitors/dismissed/${encodeURIComponent(domain)}/restore`,
        competitorPayloadSchema,
        {}
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setDiscoverError(null);
    },
    onError: (e: Error) => setDiscoverError(e.message)
  });

  const addMutation = useMutation({
    mutationFn: (domain: string) =>
      postJson("/api/keywords/competitors", competitorPayloadSchema, { domain }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
      setNewDomain("");
    }
  });

  const deleteMutation = useMutation({
    mutationFn: async (domain: string) => {
      const res = await fetch(`/api/keywords/competitors/${encodeURIComponent(domain)}`, {
        method: "DELETE"
      });
      const text = await res.text();
      let body: unknown = null;
      try {
        body = text ? JSON.parse(text) : null;
      } catch {
        /* ignore */
      }
      if (!res.ok) {
        const rec = body && typeof body === "object" ? (body as Record<string, unknown>) : null;
        const errObj = rec?.error && typeof rec.error === "object" ? (rec.error as { message?: unknown }) : null;
        const msg =
          typeof errObj?.message === "string"
            ? errObj.message
            : typeof rec?.detail === "string"
              ? rec.detail
              : `Could not remove domain (${res.status})`;
        throw new Error(msg);
      }
      return body;
    },
    onSuccess: () => {
      setDeleteError(null);
      queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
    },
    onError: (err: Error) => setDeleteError(err.message)
  });

  const items = query.data?.items ?? [];
  const lastResearch = query.data?.last_research;
  const pendingSuggestions = query.data?.pending_suggestions ?? [];
  const dismissed = query.data?.dismissed_competitors ?? [];
  const showCompetitorTabs =
    !query.isLoading && !query.isError && (items.length > 0 || pendingSuggestions.length > 0 || dismissed.length > 0);

  const domainFilterNorm = domainFilter.trim().toLowerCase();
  const filteredApproved = useMemo(() => {
    if (!domainFilterNorm) return items;
    return items.filter((c) => c.domain.toLowerCase().includes(domainFilterNorm));
  }, [items, domainFilterNorm]);
  const filteredPending = useMemo(() => {
    if (!domainFilterNorm) return pendingSuggestions;
    return pendingSuggestions.filter((c) => c.domain.toLowerCase().includes(domainFilterNorm));
  }, [pendingSuggestions, domainFilterNorm]);
  const filteredDismissed = useMemo(() => {
    if (!domainFilterNorm) return dismissed;
    return dismissed.filter((c) => c.domain.toLowerCase().includes(domainFilterNorm));
  }, [dismissed, domainFilterNorm]);

  const tabTotalLen =
    competitorTab === "approved"
      ? items.length
      : competitorTab === "new"
        ? pendingSuggestions.length
        : dismissed.length;
  const tabFilteredLen =
    competitorTab === "approved"
      ? filteredApproved.length
      : competitorTab === "new"
        ? filteredPending.length
        : filteredDismissed.length;

  function runCompetitorResearch() {
    setResearchStatus("running");
    setResearchProgress("");
    setResearchError("");
    startKeywordResearchSse("/api/keywords/competitors/research", {
      onProgress: setResearchProgress,
      onDone: () => {
        setResearchStatus("idle");
        setResearchProgress("");
        queryClient.invalidateQueries({ queryKey: ["competitor-domains"] });
        queryClient.invalidateQueries({ queryKey: ["target-keywords"] });
      },
      onError: (detail) => {
        setResearchStatus("error");
        setResearchError(detail);
        setResearchProgress("");
      }
    });
  }

  function handleAdd() {
    const d = newDomain.trim();
    if (!d) return;
    addMutation.mutate(d);
  }

  return (
    <div className="rounded-[24px] border border-line/80 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line/60 pb-4">
        <div className="min-w-0">
          <h3 className="text-lg font-semibold text-ink">Competitors</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            {onOpenSeedKeywordsTab ? (
              <button
                type="button"
                className="font-medium text-ocean underline decoration-ocean/40 underline-offset-2 hover:decoration-ocean"
                onClick={onOpenSeedKeywordsTab}
              >
                Seed Keywords
              </button>
            ) : (
              <span className="font-medium text-slate-600">Seed keywords</span>
            )}{" "}
            power SERP discovery into the <span className="font-medium text-slate-600">New</span> tab. Research merges
            organic keywords into{" "}
            {onOpenTargetKeywordsTab ? (
              <button
                type="button"
                className="font-medium text-ocean underline decoration-ocean/40 underline-offset-2 hover:decoration-ocean"
                onClick={onOpenTargetKeywordsTab}
              >
                Target Keywords
              </button>
            ) : (
              <span className="font-medium text-slate-600">Target Keywords</span>
            )}
            .
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={() => discoverMutation.mutate()} disabled={discoverMutation.isPending}>
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            {discoverMutation.isPending ? "Searching…" : "Search with seeds"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={researchStatus === "running"}
            onClick={runCompetitorResearch}
          >
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            {researchStatus === "running" ? "Running…" : "Run research"}
          </Button>
        </div>
      </div>

      {discoverError ? <p className="mt-3 text-sm text-red-600">{discoverError}</p> : null}
      {discoverNote && !discoverError ? <p className="mt-3 text-sm text-emerald-800">{discoverNote}</p> : null}

      {researchStatus === "running" && researchProgress ? (
        <div className="mt-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-700">
          {researchProgress}
        </div>
      ) : null}

      {researchStatus === "error" ? (
        <div className="mt-3 flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-600">
          <span className="min-w-0 flex-1">{researchError || "Research failed — please try again."}</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={() => setResearchStatus("idle")}
            aria-label="Dismiss error"
          >
            <X className="size-4" />
          </Button>
        </div>
      ) : null}

      {deleteError ? (
        <p className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{deleteError}</p>
      ) : null}

      {lastResearch?.finished_at ? (
        <details className="mt-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
          <summary className="cursor-pointer font-medium text-slate-700 outline-none hover:text-ink">
            Last research · {new Date(lastResearch.finished_at).toLocaleString()}
            {lastResearch.unit_cost != null ? ` · $${Number(lastResearch.unit_cost).toFixed(4)}` : ""}
            {lastResearch.organic_keywords_ok != null
              ? ` · ${lastResearch.organic_keywords_ok} ok`
              : ""}
            {(lastResearch.errors?.length ?? 0) > 0 ? ` · ${lastResearch.errors?.length} errors` : ""}
          </summary>
          <div className="mt-2 border-t border-slate-200/80 pt-2 text-slate-600">
            {(lastResearch.organic_keywords_failed ?? 0) > 0 ? (
              <p>
                Organic keywords: {lastResearch.organic_keywords_failed} failed
                {lastResearch.competitors_total != null ? ` · ${lastResearch.competitors_total} domains` : ""}
              </p>
            ) : null}
            {lastResearch.errors && lastResearch.errors.length > 0 ? (
              <ul className="mt-1 max-h-40 list-disc space-y-1 overflow-y-auto pl-4 text-red-700">
                {lastResearch.errors.map((err, i) => (
                  <li key={`${i}-${err.slice(0, 24)}`} className="break-words">
                    {err}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-emerald-800">No per-domain API errors on that run.</p>
            )}
          </div>
        </details>
      ) : null}

      {!query.isLoading && !query.isError ? (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <div className="min-w-[12rem] flex-1">
            <label className="mb-1 block text-xs font-medium text-slate-500">Add competitor</label>
            <div className="flex gap-2">
              <Input
                type="text"
                value={newDomain}
                onChange={(e) => setNewDomain(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleAdd()}
                placeholder="example.com"
                className="rounded-xl border-line bg-[#f7f9fc] px-3 py-2 text-sm"
              />
              <Button variant="outline" size="sm" className="shrink-0" onClick={handleAdd} disabled={!newDomain.trim() || addMutation.isPending}>
                <Plus className="mr-1 h-3.5 w-3.5" />
                Add
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {query.isError ? (
        <div className="mt-6 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800" role="alert">
          {query.error instanceof Error ? query.error.message : "Could not load competitors."}
        </div>
      ) : query.isLoading ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center text-sm text-slate-400">Loading…</div>
      ) : !showCompetitorTabs ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
          No competitors yet. Add a domain, run <span className="font-medium text-slate-600">Search with seeds</span>, or
          run research.
        </div>
      ) : (
        <div className="mt-5">
          <div className="flex items-stretch gap-0 border-b border-line/60" role="tablist" aria-label="Competitor status">
            {COMPETITOR_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={competitorTab === tab.id}
                onClick={() => setCompetitorTab(tab.id)}
                className={cn(
                  "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                  competitorTab === tab.id
                    ? "border-ocean text-ocean"
                    : "border-transparent text-slate-500 hover:text-slate-700"
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-b border-line/60 py-3">
            <div className="relative min-w-[180px] max-w-md flex-1">
              <Search
                size={15}
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <input
                ref={domainSearchRef}
                type="text"
                placeholder="Search domains…"
                value={domainFilter}
                onChange={(e) => setDomainFilter(e.target.value)}
                className="h-9 w-full rounded-lg border border-line bg-white pl-9 pr-8 text-sm text-ink placeholder:text-slate-400 focus:border-[#2e6be6] focus:outline-none focus:ring-1 focus:ring-[#2e6be6]/30"
              />
              {domainFilter ? (
                <button
                  type="button"
                  onClick={() => {
                    setDomainFilter("");
                    domainSearchRef.current?.focus();
                  }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-400 hover:text-slate-600"
                  aria-label="Clear search"
                >
                  <X size={13} />
                </button>
              ) : null}
            </div>
            <span className="text-xs text-slate-400 tabular-nums">
              {domainFilterNorm ? `${tabFilteredLen} of ${tabTotalLen}` : `${tabTotalLen} total`}
            </span>
          </div>

          {competitorTab === "approved" ? (
            items.length === 0 ? (
              <div className="mt-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
                No approved competitors yet. Add a domain above, or approve suggestions from the New tab.
              </div>
            ) : filteredApproved.length === 0 ? (
              <div className="mt-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
                No domains match your search.
              </div>
            ) : (
              <div className="mt-4 overflow-x-auto">
                <Table className="min-w-[1180px] w-full text-sm">
                  <TableHeader>
                    <TableRow className="border-b border-line text-left text-xs font-medium uppercase tracking-wider text-slate-400">
                      <TableHead className="pb-2 pr-3 whitespace-nowrap">Domain</TableHead>
                      <LabsMetricHeaders />
                      <TableHead className="pb-2 pr-2 text-center text-[11px] font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap">
                        Source
                      </TableHead>
                      <TableHead className="pb-2 text-right text-[11px] font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap">
                        Actions
                      </TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filteredApproved.map((comp) => (
                      <TableRow
                        key={comp.domain}
                        className="cursor-pointer border-b border-line/50 transition hover:bg-slate-50"
                        onClick={() => navigate(`/keywords/competitors/${encodeURIComponent(comp.domain)}`)}
                      >
                        <TableCell className="py-2.5 pr-3">
                          <span className="font-medium text-ink">{comp.domain}</span>
                        </TableCell>
                        <LabsMetricCells row={comp} />
                        <TableCell className="py-2.5 pr-2 text-center">
                          {comp.is_manual ? (
                            <span className="inline-block rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-600">
                              Manual
                            </span>
                          ) : (
                            <span className="inline-block rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-600">
                              Auto
                            </span>
                          )}
                        </TableCell>
                        <TableCell className="py-2.5 text-right">
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8 rounded-lg text-slate-400 hover:bg-red-50 hover:text-red-500"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              deleteMutation.mutate(comp.domain);
                            }}
                            disabled={deleteMutation.isPending}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )
          ) : competitorTab === "new" ? (
            pendingSuggestions.length === 0 ? (
              <div className="mt-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
                No new suggestions. Run Search with seeds (requires seed keywords).
              </div>
            ) : filteredPending.length === 0 ? (
              <div className="mt-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
                No domains match your search.
              </div>
            ) : (
              <div className="mt-4">
                <div className="mb-2 flex flex-wrap items-center justify-end gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="text-slate-500"
                    onClick={() => clearPendingMutation.mutate()}
                    disabled={clearPendingMutation.isPending}
                  >
                    Clear all suggestions
                  </Button>
                </div>
                <div className="overflow-x-auto">
                  <Table className="min-w-[1180px] w-full text-sm">
                    <TableHeader>
                      <TableRow className="border-b border-line text-left text-xs font-medium uppercase tracking-wider text-slate-400">
                        <TableHead className="pb-2 pr-3 whitespace-nowrap">Domain</TableHead>
                        <LabsMetricHeaders />
                        <TableHead className="pb-2 text-right text-[11px] font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap">
                          Actions
                        </TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {filteredPending.map((row) => (
                        <TableRow key={row.domain} className="border-b border-line/50 bg-amber-50/40">
                          <TableCell className="py-2.5 pr-3 font-medium text-ink">{row.domain}</TableCell>
                          <LabsMetricCells row={row} />
                          <TableCell className="py-2.5 text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-8 border-emerald-200 text-emerald-800 hover:bg-emerald-50"
                                disabled={approvePendingMutation.isPending}
                                onClick={() => approvePendingMutation.mutate(row.domain)}
                              >
                                <Check className="mr-1 h-3.5 w-3.5" />
                                Approve
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-8 border-slate-200 text-slate-600 hover:bg-slate-100"
                                disabled={rejectPendingMutation.isPending}
                                onClick={() => rejectPendingMutation.mutate(row.domain)}
                              >
                                <X className="mr-1 h-3.5 w-3.5" />
                                Dismiss
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            )
          ) : dismissed.length === 0 ? (
            <div className="mt-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
              No dismissed competitors. Dismiss a suggestion from New or remove an approved competitor.
            </div>
          ) : filteredDismissed.length === 0 ? (
            <div className="mt-6 flex min-h-[100px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
              No domains match your search.
            </div>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <Table className="min-w-[1180px] w-full text-sm">
                <TableHeader>
                  <TableRow className="border-b border-line text-left text-xs font-medium uppercase tracking-wider text-slate-400">
                    <TableHead className="pb-2 pr-3 whitespace-nowrap">Domain</TableHead>
                    <LabsMetricHeaders />
                    <TableHead className="pb-2 pr-2 text-center text-[11px] font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap">
                      Source
                    </TableHead>
                    <TableHead className="pb-2 text-right text-[11px] font-medium uppercase tracking-wide text-slate-400 whitespace-nowrap">
                      Actions
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredDismissed.map((row) => (
                    <TableRow key={row.domain} className="border-b border-line/50">
                      <TableCell className="py-2.5 pr-3 font-medium text-ink">{row.domain}</TableCell>
                      <LabsMetricCells row={row} />
                      <TableCell className="py-2.5 pr-2 text-center">
                        <span className="inline-block rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                          Dismissed
                        </span>
                      </TableCell>
                      <TableCell className="py-2.5 text-right">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-8 border-slate-200 text-slate-600 hover:bg-slate-100"
                          disabled={restoreDismissedMutation.isPending}
                          onClick={() => restoreDismissedMutation.mutate(row.domain)}
                        >
                          <RotateCcw className="mr-1 h-3.5 w-3.5" />
                          Restore
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
