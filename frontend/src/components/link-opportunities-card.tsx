import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronUp, Link2, Loader2, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { z } from "zod";

import { Button } from "./ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { getJson, postJson } from "../lib/api";
import { cn } from "../lib/utils";

const linkSuggestionSchema = z.object({
  id: z.number(),
  source_type: z.string(),
  source_handle: z.string(),
  target_type: z.string(),
  target_handle: z.string(),
  kind: z.enum(["phrase_wrap", "ai_woven"]),
  anchor_phrase: z.string().nullable(),
  ai_anchor_html: z.string().nullable(),
  score: z.number(),
  status: z.string(),
  created_at: z.number(),
  applied_at: z.number().nullable(),
});

const suggestionsResponseSchema = z.array(linkSuggestionSchema);
const actionResponseSchema = z.object({ status: z.string() }).passthrough();

type LinkSuggestion = z.infer<typeof linkSuggestionSchema>;

interface LinkOpportunitiesCardProps {
  sourceType: "products" | "collections" | "pages" | "articles";
  sourceHandle: string;
  blogHandle?: string;
}

function KindBadge({ kind }: { kind: "phrase_wrap" | "ai_woven" }) {
  if (kind === "phrase_wrap") {
    return (
      <span className="inline-flex items-center gap-1 rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700">
        <Link2 className="h-3 w-3" /> Phrase
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-medium text-violet-700">
      <Sparkles className="h-3 w-3" /> AI Woven
    </span>
  );
}

function formatTargetUrl(targetType: string, targetHandle: string): string {
  const typeMap: Record<string, string> = {
    products: "/products",
    collections: "/collections",
    pages: "/pages",
    articles: "/blogs",
  };
  const prefix = typeMap[targetType] || "";
  return `${prefix}/${targetHandle}`;
}

export function LinkOpportunitiesCard({ sourceType, sourceHandle, blogHandle }: LinkOpportunitiesCardProps) {
  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();

  const effectiveHandle = sourceType === "articles" && blogHandle
    ? `${blogHandle}/${sourceHandle}`
    : sourceHandle;

  const suggestionsQuery = useQuery({
    queryKey: ["link-suggestions", sourceType, effectiveHandle],
    queryFn: () =>
      getJson(
        `/api/internal-links/suggestions?source_type=${encodeURIComponent(sourceType)}&source_handle=${encodeURIComponent(effectiveHandle)}&status=suggested&limit=10`,
        suggestionsResponseSchema
      ),
    enabled: expanded,
    staleTime: 60_000,
  });

  const applyMutation = useMutation({
    mutationFn: (id: number) =>
      postJson(`/api/internal-links/suggestions/${id}/apply`, actionResponseSchema).then(() => id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["link-suggestions", sourceType, effectiveHandle] });
      void queryClient.invalidateQueries({ queryKey: ["link-summary"] });
    },
  });

  const dismissMutation = useMutation({
    mutationFn: (id: number) =>
      postJson(`/api/internal-links/suggestions/${id}/dismiss`, actionResponseSchema).then(() => id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["link-suggestions", sourceType, effectiveHandle] });
      void queryClient.invalidateQueries({ queryKey: ["link-summary"] });
    },
  });

  const generateAnchorMutation = useMutation({
    mutationFn: (id: number) =>
      postJson(`/api/internal-links/suggestions/${id}/generate-anchor`, actionResponseSchema).then(() => id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["link-suggestions", sourceType, effectiveHandle] });
    },
  });

  const suggestions = suggestionsQuery.data ?? [];
  const isLoading = suggestionsQuery.isLoading;
  const isEmpty = !isLoading && suggestions.length === 0;

  return (
    <Card className="border-white/70 bg-white/90 shadow-panel">
      <CardHeader className="pb-2">
        <button
          type="button"
          className="flex w-full items-center justify-between text-left"
          onClick={() => setExpanded((v) => !v)}
        >
          <CardTitle className="flex items-center gap-2 text-base font-semibold">
            <Link2 className="h-4 w-4 text-indigo-600" />
            Link Opportunities
            {suggestions.length > 0 && (
              <span className="ml-1 rounded-full bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                {suggestions.length}
              </span>
            )}
          </CardTitle>
          {expanded ? (
            <ChevronUp className="h-4 w-4 text-gray-400" />
          ) : (
            <ChevronDown className="h-4 w-4 text-gray-400" />
          )}
        </button>
      </CardHeader>
      {expanded && (
        <CardContent className="pt-2">
          {isLoading && (
            <div className="flex items-center justify-center py-6 text-gray-500">
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Loading suggestions...
            </div>
          )}
          {isEmpty && (
            <div className="py-4 text-center text-sm text-gray-500">
              No link suggestions for this page. Run a full sync to generate new suggestions.
            </div>
          )}
          {!isLoading && suggestions.length > 0 && (
            <div className="space-y-3">
              {suggestions.map((s) => {
                const isApplying = applyMutation.isPending && applyMutation.variables === s.id;
                const isDismissing = dismissMutation.isPending && dismissMutation.variables === s.id;
                const isGenerating = generateAnchorMutation.isPending && generateAnchorMutation.variables === s.id;

                return (
                  <div
                    key={s.id}
                    className={cn(
                      "rounded-lg border border-gray-200 bg-gray-50 p-3",
                      (isApplying || isDismissing) && "opacity-60"
                    )}
                  >
                    <div className="mb-2 flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1 flex items-center gap-2">
                          <KindBadge kind={s.kind} />
                          <span className="text-xs text-gray-500">
                            Score: {(s.score * 100).toFixed(0)}%
                          </span>
                        </div>
                        <div className="text-sm">
                          <span className="text-gray-600">Link to: </span>
                          <a
                            href={formatTargetUrl(s.target_type, s.target_handle)}
                            className="font-medium text-indigo-600 hover:underline"
                          >
                            {s.target_handle}
                          </a>
                          <span className="ml-1 text-xs text-gray-400">({s.target_type})</span>
                        </div>
                        {s.anchor_phrase && (
                          <div className="mt-1 text-sm">
                            <span className="text-gray-600">Anchor: </span>
                            <code className="rounded bg-white px-1 py-0.5 text-xs">
                              {s.anchor_phrase}
                            </code>
                          </div>
                        )}
                        {s.kind === "ai_woven" && !s.ai_anchor_html && (
                          <Button
                            size="sm"
                            variant="outline"
                            className="mt-2 h-7 text-xs"
                            onClick={() => generateAnchorMutation.mutate(s.id)}
                            disabled={isGenerating}
                          >
                            {isGenerating ? (
                              <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                            ) : (
                              <Sparkles className="mr-1 h-3 w-3" />
                            )}
                            Generate AI anchor
                          </Button>
                        )}
                        {s.ai_anchor_html && (
                          <div className="mt-2 rounded border border-violet-200 bg-violet-50 p-2">
                            <div className="mb-1 text-[10px] font-medium uppercase tracking-wide text-violet-600">
                              AI Suggested
                            </div>
                            <div
                              className="prose prose-sm max-w-none text-xs"
                              dangerouslySetInnerHTML={{ __html: s.ai_anchor_html }}
                            />
                          </div>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center justify-end gap-2 border-t border-gray-200 pt-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 px-2 text-xs text-gray-600 hover:text-red-600"
                        onClick={() => dismissMutation.mutate(s.id)}
                        disabled={isDismissing || isApplying}
                      >
                        {isDismissing ? (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        ) : (
                          <X className="mr-1 h-3 w-3" />
                        )}
                        Dismiss
                      </Button>
                      <Button
                        size="sm"
                        className="h-7 bg-indigo-600 px-2 text-xs hover:bg-indigo-700"
                        onClick={() => applyMutation.mutate(s.id)}
                        disabled={isApplying || isDismissing || (s.kind === "ai_woven" && !s.ai_anchor_html)}
                        title={s.kind === "ai_woven" && !s.ai_anchor_html ? "Generate AI anchor first" : undefined}
                      >
                        {isApplying ? (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        ) : (
                          <Check className="mr-1 h-3 w-3" />
                        )}
                        Apply
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      )}
    </Card>
  );
}
