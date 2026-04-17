import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Sparkles, Trash2, X } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../components/ui/table";
import { getJson, postJson } from "../../lib/api";
import { competitorPayloadSchema } from "./schemas";
import { startKeywordResearchSse } from "./sse";

export function CompetitorsPanel() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [newDomain, setNewDomain] = useState("");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [researchStatus, setResearchStatus] = useState<"idle" | "running" | "error">("idle");
  const [researchProgress, setResearchProgress] = useState("");
  const [researchError, setResearchError] = useState("");

  const query = useQuery({
    queryKey: ["competitor-domains"],
    queryFn: () => getJson("/api/keywords/competitors", competitorPayloadSchema)
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

  function fmt(n: number | undefined | null): string {
    if (n == null) return "—";
    return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  }

  return (
    <div className="rounded-[24px] border border-line/80 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-ink">Competitor Domains</h3>
          <p className="mt-1 text-sm text-slate-500">
            Run competitor research to pull organic keywords (merged into target keywords), competitor profiles, top
            pages, and gaps.             When your shop domain is set, auto-discovery merges suggested peers via DataForSEO Labs
            competitors_domain.
            The Traffic column is each domain&apos;s estimated monthly organic volume (ETV) from Labs Bulk Traffic
            Estimation — not a sum of keyword rows. Manual domains not in the discovery list still get traffic from the
            organic-keyword sample where available. Check &quot;Last competitor research&quot; below for per-domain API
            errors.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          disabled={researchStatus === "running"}
          onClick={runCompetitorResearch}
        >
          <Sparkles className="mr-1.5 h-3.5 w-3.5" />
          {researchStatus === "running" ? "Running…" : "Run competitor research"}
        </Button>
      </div>

      {researchStatus === "running" && researchProgress && (
        <div className="mt-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-600">
          {researchProgress}
        </div>
      )}

      {researchStatus === "error" && (
        <div className="mt-3 flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
          <span className="flex-1">{researchError || "Research failed — please try again."}</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 opacity-60 hover:opacity-100"
            onClick={() => setResearchStatus("idle")}
            aria-label="Dismiss error"
          >
            <X className="size-4" />
          </Button>
        </div>
      )}

      {deleteError ? (
        <p className="mt-3 rounded-xl border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">{deleteError}</p>
      ) : null}

      {lastResearch?.finished_at ? (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          <p className="font-medium text-ink">Last competitor research</p>
          <p className="mt-1 text-slate-600">
            {new Date(lastResearch.finished_at).toLocaleString()}
            {lastResearch.unit_cost != null
              ? ` · DataForSEO cost (USD, this run): ${Number(lastResearch.unit_cost).toFixed(4)}`
              : null}
          </p>
          {(lastResearch.organic_keywords_ok != null || lastResearch.organic_keywords_failed != null) && (
            <p className="mt-1 text-slate-600">
              Organic keywords API: {lastResearch.organic_keywords_ok ?? 0} ok
              {(lastResearch.organic_keywords_failed ?? 0) > 0
                ? `, ${lastResearch.organic_keywords_failed} failed`
                : ""}
              {lastResearch.competitors_total != null
                ? ` · ${lastResearch.competitors_total} domains in list`
                : ""}
            </p>
          )}
          {lastResearch.errors && lastResearch.errors.length > 0 ? (
            <ul className="mt-2 max-h-48 list-disc space-y-1 overflow-y-auto pl-5 text-red-700">
              {lastResearch.errors.map((err, i) => (
                <li key={`${i}-${err.slice(0, 24)}`} className="break-words">
                  {err}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-emerald-800">No per-domain API errors on that run.</p>
          )}
        </div>
      ) : null}

      <div className="mt-5 flex gap-2">
        <Input
          type="text"
          value={newDomain}
          onChange={(e) => setNewDomain(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="e.g. 180smoke.ca"
          className="flex-1 rounded-xl border-line bg-[#f7f9fc] px-4 py-2.5 text-sm text-ink placeholder:text-slate-400 focus:border-ocean focus:ring-ocean"
        />
        <Button variant="outline" size="sm" onClick={handleAdd} disabled={!newDomain.trim() || addMutation.isPending}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add
        </Button>
      </div>

      {query.isLoading ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center text-sm text-slate-400">
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 px-4 text-center text-sm text-slate-400">
          No competitors in the list yet. Add domains above, or run competitor research to auto-discover from your
          shop domain and merge organic keywords into target keywords.
        </div>
      ) : (
        <div className="mt-5">
          <Table className="w-full text-sm">
            <TableHeader>
              <TableRow className="border-b border-line text-left text-xs font-medium uppercase tracking-wider text-slate-400">
                <TableHead className="pb-2 pr-4">Domain</TableHead>
                <TableHead className="pb-2 pr-4 text-right">Traffic</TableHead>
                <TableHead className="pb-2 pr-4 text-right">Common</TableHead>
                <TableHead className="pb-2 pr-4 text-right">Gap</TableHead>
                <TableHead className="pb-2 pr-4 text-center">Source</TableHead>
                <TableHead className="pb-2 text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((comp) => (
                <TableRow
                  key={comp.domain}
                  className="border-b border-line/50 transition hover:bg-slate-50 cursor-pointer"
                  onClick={() => navigate(`/keywords/competitors/${encodeURIComponent(comp.domain)}`)}
                >
                  <TableCell className="py-3 pr-4">
                    <span className="font-medium text-ink">{comp.domain}</span>
                  </TableCell>
                  <TableCell className="py-3 pr-4 text-right tabular-nums text-slate-600">{fmt(comp.traffic)}</TableCell>
                  <TableCell className="py-3 pr-4 text-right tabular-nums text-slate-600">{fmt(comp.keywords_common)}</TableCell>
                  <TableCell className="py-3 pr-4 text-right tabular-nums text-slate-600">{fmt(comp.keywords_they_have)}</TableCell>
                  <TableCell className="py-3 pr-4 text-center">
                    {comp.is_manual ? (
                      <span className="inline-block rounded-full bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-600">Manual</span>
                    ) : (
                      <span className="inline-block rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-600">Auto</span>
                    )}
                  </TableCell>
                  <TableCell className="py-3 text-right">
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
      )}
    </div>
  );
}
