import { useState } from "react";
import { Link2, AlertTriangle, RefreshCw, Check, X, Sparkles, ArrowDownLeft, ArrowUpRight } from "lucide-react";

import {
  useApplySuggestion,
  useDismissSuggestion,
  useGenerateAnchor,
  useGraphStatsAll,
  useLinkSuggestions,
  useLinkSummary,
  useOrphans,
  useRebuildLinks,
  type LinkSuggestion,
  type GraphEntity,
} from "../hooks/use-internal-links";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Toast } from "../components/ui/toast";

function StatCard({ label, value, loading }: { label: string; value: number | string; loading?: boolean }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-6">
        {loading ? (
          <Skeleton className="mb-1 h-8 w-16" />
        ) : (
          <div className="text-3xl font-bold text-ink">{value}</div>
        )}
        <div className="text-sm text-muted-foreground">{label}</div>
      </CardContent>
    </Card>
  );
}

function KindBadge({ kind }: { kind: "phrase_wrap" | "ai_woven" }) {
  if (kind === "phrase_wrap") {
    return (
      <Badge variant="success" className="gap-1">
        <Check size={12} />
        Wraps existing text
      </Badge>
    );
  }
  return (
    <Badge variant="warning" className="gap-1">
      <Sparkles size={12} />
      Modifies copy
    </Badge>
  );
}

function SuggestionRow({
  suggestion,
  onApply,
  onDismiss,
  onGenerate,
  applying,
  dismissing,
  generating,
}: {
  suggestion: LinkSuggestion;
  onApply: () => void;
  onDismiss: () => void;
  onGenerate: () => void;
  applying: boolean;
  dismissing: boolean;
  generating: boolean;
}) {
  const canApply = suggestion.kind === "phrase_wrap" || suggestion.ai_anchor_html;

  return (
    <TableRow>
      <TableCell>
        <div className="text-sm font-medium">{suggestion.source_type}</div>
        <div className="text-xs text-muted-foreground">{suggestion.source_handle}</div>
      </TableCell>
      <TableCell>
        <div className="text-sm font-medium">{suggestion.target_type}</div>
        <div className="text-xs text-muted-foreground">{suggestion.target_handle}</div>
      </TableCell>
      <TableCell>
        <KindBadge kind={suggestion.kind} />
        {suggestion.anchor_phrase && (
          <div className="mt-1 text-xs text-muted-foreground">"{suggestion.anchor_phrase}"</div>
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-sm">{suggestion.score.toFixed(2)}</TableCell>
      <TableCell>
        <div className="flex items-center justify-end gap-2">
          {suggestion.kind === "ai_woven" && !suggestion.ai_anchor_html && (
            <Button
              size="sm"
              variant="outline"
              onClick={onGenerate}
              disabled={generating}
              className="gap-1"
            >
              <Sparkles size={14} />
              {generating ? "Generating…" : "Generate"}
            </Button>
          )}
          {canApply && (
            <Button
              size="sm"
              variant="default"
              onClick={onApply}
              disabled={applying}
              className="gap-1"
            >
              <Check size={14} />
              {applying ? "Applying…" : "Apply"}
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={onDismiss}
            disabled={dismissing}
            className="gap-1 text-muted-foreground hover:text-red-600"
          >
            <X size={14} />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

export function InternalLinksPage() {
  const [toast, setToast] = useState<{ message: string; variant: "success" | "error" | "info" } | null>(null);
  const [tab, setTab] = useState<"suggestions" | "orphans" | "graph">("suggestions");
  const [processingId, setProcessingId] = useState<number | null>(null);

  const summary = useLinkSummary();
  const suggestions = useLinkSuggestions();
  const orphans = useOrphans();
  const graphStats = useGraphStatsAll();
  const apply = useApplySuggestion();
  const dismiss = useDismissSuggestion();
  const generate = useGenerateAnchor();
  const rebuild = useRebuildLinks();

  const handleApply = async (id: number) => {
    setProcessingId(id);
    try {
      await apply.mutateAsync(id);
      setToast({ message: "Link applied successfully", variant: "success" });
    } catch (e) {
      setToast({ message: `Apply failed: ${(e as Error).message}`, variant: "error" });
    } finally {
      setProcessingId(null);
    }
  };

  const handleDismiss = async (id: number) => {
    setProcessingId(id);
    try {
      await dismiss.mutateAsync(id);
      setToast({ message: "Suggestion dismissed", variant: "info" });
    } catch (e) {
      setToast({ message: `Dismiss failed: ${(e as Error).message}`, variant: "error" });
    } finally {
      setProcessingId(null);
    }
  };

  const handleGenerate = async (id: number) => {
    setProcessingId(id);
    try {
      await generate.mutateAsync(id);
      setToast({ message: "Anchor generated - review and apply", variant: "success" });
    } catch (e) {
      setToast({ message: `Generate failed: ${(e as Error).message}`, variant: "error" });
    } finally {
      setProcessingId(null);
    }
  };

  const handleRebuild = async () => {
    try {
      await rebuild.mutateAsync();
      setToast({ message: "Rebuilding link suggestions in background", variant: "info" });
    } catch (e) {
      setToast({ message: `Rebuild failed: ${(e as Error).message}`, variant: "error" });
    }
  };

  const isLoading = summary.isLoading;

  return (
    <div className="w-full min-w-0 space-y-6 p-6 lg:p-8">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-2xl font-bold text-ink">
          <Link2 className="h-6 w-6" />
          Internal Links
        </h1>
        <Button
          onClick={handleRebuild}
          disabled={rebuild.isPending || summary.data?.progress?.running}
          className="gap-2"
        >
          <RefreshCw size={16} className={rebuild.isPending || summary.data?.progress?.running ? "animate-spin" : ""} />
          {summary.data?.progress?.running ? "Rebuilding…" : "Rebuild"}
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Total Links" value={summary.data?.total_links ?? 0} loading={isLoading} />
        <StatCard label="Orphan Pages" value={summary.data?.orphan_count ?? 0} loading={isLoading} />
        <StatCard label="Pending Suggestions" value={summary.data?.suggested ?? 0} loading={isLoading} />
        <StatCard label="Applied" value={summary.data?.applied ?? 0} loading={isLoading} />
      </div>

      {summary.data?.progress?.running && (
        <Card>
          <CardContent className="flex items-center gap-3 py-4">
            <RefreshCw size={16} className="animate-spin text-blue-600" />
            <span className="text-sm text-muted-foreground">
              {summary.data.progress.stage}: {summary.data.progress.done}/{summary.data.progress.total}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Tabs */}
      <div className="flex gap-2 border-b">
        <button
          onClick={() => setTab("suggestions")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            tab === "suggestions"
              ? "border-b-2 border-blue-600 text-blue-600"
              : "text-muted-foreground hover:text-ink"
          }`}
        >
          Suggestions ({summary.data?.suggested ?? 0})
        </button>
        <button
          onClick={() => setTab("orphans")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            tab === "orphans"
              ? "border-b-2 border-blue-600 text-blue-600"
              : "text-muted-foreground hover:text-ink"
          }`}
        >
          Orphans ({summary.data?.orphan_count ?? 0})
        </button>
        <button
          onClick={() => setTab("graph")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            tab === "graph"
              ? "border-b-2 border-blue-600 text-blue-600"
              : "text-muted-foreground hover:text-ink"
          }`}
        >
          Graph Stats
        </button>
      </div>

      {/* Suggestions tab */}
      {tab === "suggestions" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Link Suggestions</CardTitle>
          </CardHeader>
          <CardContent>
            {suggestions.isLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-16 rounded-lg" />
                ))}
              </div>
            ) : suggestions.error ? (
              <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
                <span>Failed to load suggestions: {suggestions.error.message}</span>
              </div>
            ) : (suggestions.data?.length ?? 0) === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                No pending suggestions. Run a Rebuild to generate new opportunities.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Source</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-right">Score</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {suggestions.data?.map((s) => (
                    <SuggestionRow
                      key={s.id}
                      suggestion={s}
                      onApply={() => handleApply(s.id)}
                      onDismiss={() => handleDismiss(s.id)}
                      onGenerate={() => handleGenerate(s.id)}
                      applying={processingId === s.id && apply.isPending}
                      dismissing={processingId === s.id && dismiss.isPending}
                      generating={processingId === s.id && generate.isPending}
                    />
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Orphans tab */}
      {tab === "orphans" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Orphan Pages</CardTitle>
          </CardHeader>
          <CardContent>
            {orphans.isLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-12 rounded-lg" />
                ))}
              </div>
            ) : orphans.error ? (
              <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
                <span>Failed to load orphans: {orphans.error.message}</span>
              </div>
            ) : (orphans.data?.length ?? 0) === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                No orphan pages found. All published content has inbound links.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Type</TableHead>
                    <TableHead>Handle</TableHead>
                    <TableHead className="text-right">Clicks</TableHead>
                    <TableHead className="text-right">Impressions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {orphans.data?.map((o, i) => (
                    <TableRow key={i}>
                      <TableCell>
                        <Badge variant="outline">{o.object_type}</Badge>
                      </TableCell>
                      <TableCell className="font-mono text-sm">{o.handle}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{o.gsc_clicks}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{o.gsc_impressions}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {/* Graph Stats tab */}
      {tab === "graph" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Link Graph Statistics</CardTitle>
          </CardHeader>
          <CardContent>
            {graphStats.isLoading ? (
              <div className="space-y-2">
                {[...Array(5)].map((_, i) => (
                  <Skeleton key={i} className="h-12 rounded-lg" />
                ))}
              </div>
            ) : graphStats.error ? (
              <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
                <span>Failed to load graph stats: {graphStats.error.message}</span>
              </div>
            ) : !graphStats.data?.entities || graphStats.data.entities.length === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                No link graph data yet. Run a sync to build the link graph.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Type</TableHead>
                    <TableHead>Handle</TableHead>
                    <TableHead className="text-right">
                      <span className="flex items-center justify-end gap-1">
                        <ArrowDownLeft size={14} /> Inbound
                      </span>
                    </TableHead>
                    <TableHead className="text-right">
                      <span className="flex items-center justify-end gap-1">
                        <ArrowUpRight size={14} /> Outbound
                      </span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {graphStats.data?.entities.map((e, i) => (
                    <TableRow key={i}>
                      <TableCell>
                        <Badge variant="outline">{e.object_type}</Badge>
                      </TableCell>
                      <TableCell className="font-mono text-sm">{e.handle}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{e.inbound}</TableCell>
                      <TableCell className="text-right font-mono text-sm">{e.outbound}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      )}

      {toast && (
        <Toast
          variant={toast.variant}
          onClose={() => setToast(null)}
        >
          {toast.message}
        </Toast>
      )}
    </div>
  );
}
