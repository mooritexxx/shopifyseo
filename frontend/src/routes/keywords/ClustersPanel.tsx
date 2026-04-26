import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Sparkles } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { Button } from "../../components/ui/button";
import { Toast } from "../../components/ui/toast";
import { AiRunningToastBody } from "../../components/ui/ai-running-toast-body";
import { getJson, patchJson } from "../../lib/api";
import { clustersPayloadSchema, matchOptionsPayloadSchema } from "./schemas";
import { CONTENT_TYPE_COLORS, CONTENT_TYPE_LABELS } from "./badges";
import { clusterFormatMatchHint, suggestedMatchHref } from "./cluster-ui";

export function ClustersPanel() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const clustersQuery = useQuery({
    queryKey: ["keyword-clusters"],
    queryFn: () => getJson("/api/keywords/clusters", clustersPayloadSchema),
  });

  const [genStatus, setGenStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [genProgress, setGenProgress] = useState("");
  const [genError, setGenError] = useState("");
  const [genStartedAt, setGenStartedAt] = useState<number | null>(null);
  const [elapsedNow, setElapsedNow] = useState(Date.now());
  useEffect(() => {
    if (genStatus !== "running") return;
    const id = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [genStatus]);

  const [editingMatchIndex, setEditingMatchIndex] = useState<number | null>(null);

  const matchOptionsQuery = useQuery({
    queryKey: ["cluster-match-options"],
    queryFn: () => getJson("/api/keywords/clusters/match-options", matchOptionsPayloadSchema),
    enabled: editingMatchIndex !== null,
  });

  const matchMutation = useMutation({
    mutationFn: (vars: { cluster_id: number; match_type: string; match_handle: string; match_title: string }) =>
      patchJson("/api/keywords/clusters/match", clustersPayloadSchema, vars),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["keyword-clusters"] });
      setEditingMatchIndex(null);
    },
  });

  function runClustering() {
    setGenStatus("running");
    setGenProgress("Starting clustering…");
    setGenError("");
    setGenStartedAt(Date.now());
    setElapsedNow(Date.now());

    fetch("/api/keywords/clusters/generate", { method: "POST" })
      .then((res) => {
        if (!res.ok) {
          setGenStatus("error");
          setGenError(`Clustering failed (${res.status}). Check that you are logged in and the API is running.`);
          setGenProgress("");
          return;
        }
        const reader = res.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        /** Must persist across read() chunks — event + data lines often arrive separately. */
        let pendingEventType = "";
        let streamTerminal: "ok" | "err" | null = null;

        function handleSseData(eventType: string, raw: string) {
          let data: { message?: string; detail?: string };
          try {
            data = JSON.parse(raw) as { message?: string; detail?: string };
          } catch {
            return;
          }
          const et = eventType.replace(/\r$/, "").trim();
          if (et === "progress" && typeof data.message === "string") {
            setGenProgress(data.message);
          } else if (et === "done") {
            streamTerminal = "ok";
            setGenStatus("done");
            setGenProgress("");
            void queryClient.invalidateQueries({ queryKey: ["keyword-clusters"] });
          } else if (et === "error") {
            streamTerminal = "err";
            setGenStatus("error");
            setGenError(typeof data.detail === "string" ? data.detail : "Clustering failed.");
            setGenProgress("");
          }
        }

        function consumeLines(lines: string[]) {
          for (const line of lines) {
            const trimmed = line.replace(/\r$/, "");
            if (trimmed.startsWith("event: ")) {
              pendingEventType = trimmed.slice(7);
            } else if (trimmed.startsWith("data: ")) {
              handleSseData(pendingEventType, trimmed.slice(6));
            }
          }
        }

        function read(): Promise<void> {
          if (!reader) return Promise.resolve();
          return reader.read().then(({ done, value }) => {
            if (value) {
              buffer += decoder.decode(value, { stream: true });
            }
            if (done) {
              buffer += decoder.decode();
            }

            const lines = buffer.split("\n");
            if (done) {
              buffer = "";
            } else {
              buffer = lines.pop() ?? "";
            }
            consumeLines(lines);

            if (done) {
              setGenProgress("");
              if (!streamTerminal) {
                void queryClient.invalidateQueries({ queryKey: ["keyword-clusters"] });
              }
              if (streamTerminal === "ok") {
                window.setTimeout(() => setGenStatus("idle"), 2200);
              } else if (streamTerminal !== "err") {
                setGenStatus("idle");
              }
              return;
            }
            return read();
          });
        }

        void read();
      })
      .catch(() => {
        setGenStatus("error");
        setGenError("Network error — please try again.");
        setGenProgress("");
      });
  }

  const data = clustersQuery.data;
  const clusters = data?.clusters ?? [];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            type="button"
            disabled={genStatus === "running"}
            onClick={runClustering}
            className="inline-flex items-center gap-2 rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white hover:bg-ink/90 disabled:opacity-50"
          >
            <Sparkles className="h-4 w-4" />
            {genStatus === "running" ? "Generating…" : "Generate Clusters"}
          </Button>
          {data?.generated_at && (
            <span className="text-xs text-slate-400">
              Last generated: {new Date(data.generated_at).toLocaleString()}
            </span>
          )}
        </div>
        {clusters.length > 0 && (
          <span className="text-sm text-slate-500">{clusters.length} clusters</span>
        )}
      </div>

      {/* Toast notifications — portaled to document.body so position:fixed is viewport-relative.
          Ancestors (e.g. Card backdrop-blur) establish a containing block and trap fixed children. */}
      {typeof document !== "undefined" &&
        (genStatus === "running" || genStatus === "done" || (genStatus === "error" && genError)) &&
        createPortal(
          <>
            {genStatus === "running" ? (
              <Toast variant="info" duration={0} customIcon={<LoaderCircle className="animate-spin" size={18} />}>
                <AiRunningToastBody
                  headline={genProgress || "Generating clusters…"}
                  stepElapsedMs={genStartedAt ? elapsedNow - genStartedAt : 0}
                />
              </Toast>
            ) : null}
            {genStatus === "done" ? (
              <Toast variant="success" duration={5000} onClose={() => setGenStatus("idle")}>
                Clusters generated successfully
              </Toast>
            ) : null}
            {genStatus === "error" && genError ? (
              <Toast variant="error" duration={8000} onClose={() => setGenStatus("idle")}>
                {genError}
              </Toast>
            ) : null}
          </>,
          document.body
        )}

      {/* Empty state */}
      {clusters.length === 0 && genStatus !== "running" && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
          <p className="text-sm text-slate-500">
            No clusters yet. Approve target keywords, then click &quot;Generate Clusters&quot; to
            group them into content topics.
          </p>
        </div>
      )}

      {/* Cluster cards */}
      <div className="grid gap-4">
        {clusters.map((cluster) => {
          const contentColor =
            CONTENT_TYPE_COLORS[cluster.content_type] ?? "bg-slate-100 text-slate-600";
          const contentLabel =
            CONTENT_TYPE_LABELS[cluster.content_type] ?? cluster.content_type;
          const formatHint = clusterFormatMatchHint(
            cluster.content_type,
            cluster.suggested_match?.match_type,
          );

          return (
            <div
              key={cluster.name}
              role="button"
              tabIndex={0}
              onClick={() => navigate(`/keywords/clusters/${cluster.id}`)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  navigate(`/keywords/clusters/${cluster.id}`);
                }
              }}
              className="group rounded-xl border border-line bg-white p-5 space-y-3 cursor-pointer text-left outline-none transition-colors hover:bg-slate-50/90 focus-visible:ring-2 focus-visible:ring-blue-500/40 focus-visible:ring-offset-2"
            >
              {/* Card header */}
              <div className="flex items-start justify-between">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-base font-semibold text-ink group-hover:text-blue-600 group-hover:underline">
                      {cluster.name}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${contentColor}`}
                      title="Recommended content format from clustering (not the same as the linked Shopify URL type)"
                    >
                      {contentLabel}
                    </span>
                    {cluster.matched_vendor && (
                      <span className="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700 whitespace-nowrap">
                        {cluster.matched_vendor.name} · {cluster.matched_vendor.product_count} products
                      </span>
                    )}
                  </div>
                  <p className="text-sm font-medium text-slate-700">
                    Primary: {cluster.primary_keyword}
                  </p>
                  <p className="text-sm text-slate-500">{cluster.content_brief}</p>
                  {formatHint ? (
                    <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-1.5">
                      {formatHint}
                    </p>
                  ) : null}
                </div>
              </div>

              {/* Match display */}
              <div className="flex items-center gap-2 text-sm">
                {cluster.suggested_match ? (
                  cluster.suggested_match.match_type === "new" ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="text-slate-400">→</span>
                      <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                        New content
                      </span>
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 flex-wrap">
                      <span className="text-slate-400">→</span>
                      <Link
                        to={suggestedMatchHref(
                          cluster.suggested_match.match_type,
                          cluster.suggested_match.match_handle,
                        )}
                        className="text-blue-600 hover:text-blue-800 hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {cluster.suggested_match.match_title}
                      </Link>
                      <span className="text-xs text-slate-400">
                        ({cluster.suggested_match.match_type === "blog_article"
                          ? "Blog Article"
                          : cluster.suggested_match.match_type === "collection"
                          ? "Collection"
                          : cluster.suggested_match.match_type === "product"
                          ? "Product"
                          : "Page"})
                      </span>
                      {cluster.gsc_segment_flags?.has_dimensional ? (
                        <span
                          className="rounded-full bg-violet-100 px-2 py-0.5 text-[10px] font-semibold text-violet-800"
                          title="Matched URL has query×segment GSC rows in cache (country, device, search appearance)"
                        >
                          GSC segments
                        </span>
                      ) : null}
                    </span>
                  )
                ) : (
                  <span className="text-slate-400">→ No match suggested</span>
                )}
                <Button
                  type="button"
                  variant="link"
                  className="h-auto p-0 text-xs font-medium text-blue-600 hover:text-blue-800"
                  onClick={(e) => {
                    e.stopPropagation();
                    const idx = clusters.indexOf(cluster);
                    setEditingMatchIndex(editingMatchIndex === idx ? null : idx);
                  }}
                >
                  Change
                </Button>
                {cluster.keyword_coverage && (
                  <span
                    className={`ml-auto rounded-full px-2 py-0.5 text-xs font-medium ${
                      cluster.keyword_coverage.found / cluster.keyword_coverage.total >= 0.5
                        ? "bg-green-100 text-green-700"
                        : cluster.keyword_coverage.found / cluster.keyword_coverage.total >= 0.25
                        ? "bg-yellow-100 text-yellow-700"
                        : "bg-red-100 text-red-700"
                    }`}
                  >
                    {cluster.keyword_coverage.found}/{cluster.keyword_coverage.total} keywords in content
                  </span>
                )}
              </div>

              {/* Match override dropdown */}
              {editingMatchIndex === clusters.indexOf(cluster) && (
                <div
                  className="rounded-lg border border-line bg-[#f7f9fc] p-3 max-h-60 overflow-y-auto"
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  {matchOptionsQuery.isLoading ? (
                    <p className="text-xs text-slate-400">Loading options…</p>
                  ) : matchOptionsQuery.data?.options ? (
                    <div className="space-y-1">
                      {["new", "none", "collection", "page", "blog_article"].map((type) => {
                        const group = matchOptionsQuery.data!.options.filter(
                          (o) => o.match_type === type
                        );
                        if (group.length === 0) return null;
                        const groupLabel =
                          type === "new"
                            ? null
                            : type === "none"
                            ? null
                            : type === "collection"
                            ? "Collections"
                            : type === "page"
                            ? "Pages"
                            : "Blog Articles";
                        return (
                          <div key={type}>
                            {groupLabel && (
                              <p className="text-xs font-semibold text-slate-500 mt-2 mb-1 px-2">
                                {groupLabel}
                              </p>
                            )}
                            {group.map((option) => (
                              <Button
                                key={`${option.match_type}-${option.match_handle}`}
                                type="button"
                                variant="ghost"
                                disabled={matchMutation.isPending}
                                onClick={() =>
                                  matchMutation.mutate({
                                    cluster_id: cluster.id,
                                    match_type: option.match_type,
                                    match_handle: option.match_handle,
                                    match_title: option.match_title,
                                  })
                                }
                                className="h-auto w-full justify-start rounded px-2 py-1 text-sm hover:bg-blue-50 disabled:opacity-50"
                              >
                                {option.match_title}
                                {option.match_type !== "new" && option.match_type !== "none" && (
                                  <span className="ml-1 text-xs text-slate-400">
                                    ({option.match_handle})
                                  </span>
                                )}
                              </Button>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  ) : (
                    <p className="text-xs text-slate-400">No options available</p>
                  )}
                </div>
              )}

              {/* Stats row */}
              <div className="flex gap-6 text-xs text-slate-500">
                <span>
                  <span className="font-medium text-ink">{cluster.keyword_count}</span> keywords
                </span>
                <span>
                  <span className="font-medium text-ink">
                    {cluster.total_volume.toLocaleString()}
                  </span>{" "}
                  total volume
                </span>
                <span>
                  Avg difficulty:{" "}
                  <span className="font-medium text-ink">{cluster.avg_difficulty}</span>
                </span>
                <span>
                  Priority:{" "}
                  <span className="font-medium text-ink">
                    {(cluster.priority_score || cluster.avg_opportunity).toFixed(1)}
                  </span>
                </span>
                <span>
                  Avg opportunity:{" "}
                  <span className="font-medium text-ink">{cluster.avg_opportunity}</span>
                </span>
              </div>

              <p className="text-xs text-slate-500">
                Open this cluster for the full keyword list with volume, difficulty, and ranking.
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
