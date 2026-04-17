import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Plus, Sparkles, X } from "lucide-react";

import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { getJson, postJson } from "../../lib/api";
import { seedPayloadSchema } from "./schemas";
import { SOURCE_COLORS } from "./badges";

type SeedResearchStatus = "idle" | "running" | "error";

export type SeedKeywordsPanelProps = {
  seedResearchStatus: SeedResearchStatus;
  seedResearchProgress: string;
  seedResearchError: string;
  onRunSeedKeywordResearch: () => void;
  onDismissSeedResearchError: () => void;
};

export function SeedKeywordsPanel({
  seedResearchStatus,
  seedResearchProgress,
  seedResearchError,
  onRunSeedKeywordResearch,
  onDismissSeedResearchError,
}: SeedKeywordsPanelProps) {
  const queryClient = useQueryClient();
  const [newKeyword, setNewKeyword] = useState("");
  const [filterSource, setFilterSource] = useState<string>("all");

  const query = useQuery({
    queryKey: ["seed-keywords"],
    queryFn: () => getJson("/api/keywords/seed", seedPayloadSchema)
  });

  const generateMutation = useMutation({
    mutationFn: () => postJson("/api/keywords/seed/generate", seedPayloadSchema),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["seed-keywords"] })
  });

  const saveMutation = useMutation({
    mutationFn: (items: Array<{ keyword: string; source: string }>) =>
      postJson("/api/keywords/seed", seedPayloadSchema, { items }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["seed-keywords"] })
  });

  const items = query.data?.items ?? [];

  const sources = useMemo(() => {
    const set = new Set(items.map((i) => i.source));
    return Array.from(set).sort();
  }, [items]);

  const filtered = useMemo(() => {
    if (filterSource === "all") return items;
    return items.filter((i) => i.source === filterSource);
  }, [items, filterSource]);

  function handleAdd() {
    const kw = newKeyword.trim();
    if (!kw) return;
    const exists = items.some((i) => i.keyword.toLowerCase() === kw.toLowerCase());
    if (exists) {
      setNewKeyword("");
      return;
    }
    saveMutation.mutate([...items, { keyword: kw, source: "manual" }]);
    setNewKeyword("");
  }

  function handleRemove(keyword: string) {
    saveMutation.mutate(items.filter((i) => i.keyword !== keyword));
  }

  return (
    <div className="rounded-[24px] border border-line/80 bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-ink">Seed Keywords</h3>
          <p className="mt-1 text-sm text-slate-500">
            Add keywords manually or auto-generate from your store's brands, collections, and product types.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={generateMutation.isPending}
            onClick={() => generateMutation.mutate()}
          >
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
            {generateMutation.isPending ? "Generating…" : "Auto-generate from store"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={seedResearchStatus === "running"}
            onClick={onRunSeedKeywordResearch}
          >
            {seedResearchStatus === "running" ? (
              <>
                <LoaderCircle className="mr-1.5 h-3.5 w-3.5 animate-spin" aria-hidden />
                Running…
              </>
            ) : (
              <>
                <Sparkles className="mr-1.5 h-3.5 w-3.5" />
                Run seed keyword research
              </>
            )}
          </Button>
        </div>
      </div>

      {seedResearchStatus === "running" && seedResearchProgress && (
        <div className="mt-4 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-600">
          {seedResearchProgress}
        </div>
      )}

      {seedResearchStatus === "error" && (
        <div className="mt-4 flex items-start gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
          <span className="flex-1">
            {seedResearchError || "Research failed — please try again."}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0 opacity-60 hover:opacity-100"
            onClick={onDismissSeedResearchError}
            aria-label="Dismiss error"
          >
            <X className="size-4" />
          </Button>
        </div>
      )}

      <div className="mt-5 flex gap-2">
        <Input
          type="text"
          value={newKeyword}
          onChange={(e) => setNewKeyword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleAdd()}
          placeholder="Add a keyword…"
          className="flex-1 rounded-xl border-line bg-[#f7f9fc] px-4 py-2.5 text-sm text-ink placeholder:text-slate-400 focus:border-ocean focus:ring-ocean"
        />
        <Button variant="outline" size="sm" onClick={handleAdd} disabled={!newKeyword.trim()}>
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add
        </Button>
      </div>

      {items.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-1.5">
          <Button
            variant="ghost"
            type="button"
            onClick={() => setFilterSource("all")}
            className={`h-auto rounded-full px-3 py-1 text-xs font-medium transition ${
              filterSource === "all"
                ? "bg-ink text-white hover:bg-ink/90"
                : "bg-slate-100 text-slate-500 hover:bg-slate-200"
            }`}
          >
            All ({items.length})
          </Button>
          {sources.map((source) => {
            const count = items.filter((i) => i.source === source).length;
            return (
              <Button
                key={source}
                variant="ghost"
                type="button"
                onClick={() => setFilterSource(source === filterSource ? "all" : source)}
                className={`h-auto rounded-full px-3 py-1 text-xs font-medium transition ${
                  filterSource === source
                    ? "bg-ink text-white hover:bg-ink/90"
                    : "bg-slate-100 text-slate-500 hover:bg-slate-200"
                }`}
              >
                {source} ({count})
              </Button>
            );
          })}
        </div>
      )}

      {query.isLoading ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center text-sm text-slate-400">
          Loading…
        </div>
      ) : items.length === 0 ? (
        <div className="mt-6 flex min-h-[120px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
          No seed keywords yet — click "Auto-generate from store" to get started.
        </div>
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          {filtered.map((item) => (
            <span
              key={item.keyword}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium ${
                SOURCE_COLORS[item.source] ?? SOURCE_COLORS.manual
              }`}
            >
              {item.keyword}
              <Button
                variant="ghost"
                size="icon"
                type="button"
                className="ml-0.5 h-5 w-5 rounded-full p-0 opacity-50 transition hover:opacity-100"
                onClick={() => handleRemove(item.keyword)}
              >
                <X className="h-3 w-3" />
              </Button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
