import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Database, RefreshCw, CheckCircle2, AlertTriangle, Key } from "lucide-react";
import { useState } from "react";
import { z } from "zod";

import { getJson, postJson } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Skeleton } from "../components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Toast } from "../components/ui/toast";
import { embeddingStatusSchema } from "../types/api";
import type { EmbeddingStatus, EmbeddingTypeStatus } from "../types/api";

const TYPE_LABELS: Record<string, string> = {
  product: "Products",
  collection: "Collections",
  page: "Pages",
  blog_article: "Blog Articles",
  cluster: "Clusters",
  gsc_queries: "GSC Queries",
  keyword: "Keywords",
  article_idea: "Article Ideas",
  competitor_page: "Competitor Pages",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "Never";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function CoverageBadge({ pct }: { pct: number }) {
  if (pct >= 100) return <Badge variant="success">{pct}%</Badge>;
  if (pct >= 50) return <Badge variant="warning">{pct}%</Badge>;
  if (pct > 0) return <Badge variant="error">{pct}%</Badge>;
  return <Badge variant="outline">0%</Badge>;
}

export default function EmbeddingsPage() {
  const queryClient = useQueryClient();
  const [toast, setToast] = useState<{ message: string; variant: "success" | "error" | "info" } | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["embedding-status"],
    staleTime: 30_000,
    refetchInterval: 30_000,
    queryFn: () => getJson("/api/embeddings/status", embeddingStatusSchema),
  });

  const refreshMutation = useMutation({
    mutationFn: () => postJson("/api/embeddings/refresh", z.object({ status: z.string(), message: z.string() })),
    onSuccess: () => {
      setToast({ message: "Embedding refresh started in background", variant: "success" });
      setTimeout(() => queryClient.invalidateQueries({ queryKey: ["embedding-status"] }), 5000);
    },
    onError: (err) => {
      setToast({ message: `Refresh failed: ${err.message}`, variant: "error" });
    },
  });

  if (isLoading) {
    return (
      <div className="w-full min-w-0 space-y-6 p-6 lg:p-8">
        <h1 className="text-2xl font-bold text-ink">Embeddings</h1>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-32 rounded-2xl border bg-muted/40" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-2xl border bg-muted/40" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="w-full min-w-0 space-y-6 p-6 lg:p-8">
        <h1 className="text-2xl font-bold text-ink">Embeddings</h1>
        <Card>
          <CardContent className="py-8 text-center text-muted-foreground">
            <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-amber-500" />
            <p>Failed to load embedding status: {error.message}</p>
          </CardContent>
        </Card>
      </div>
    );
  }

  const status = data as EmbeddingStatus;

  return (
    <div className="w-full min-w-0 space-y-6 p-6 lg:p-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Database className="h-6 w-6 text-primary" />
          <h1 className="text-2xl font-bold text-ink">Embeddings</h1>
        </div>
        <Button
          variant="ocean"
          size="sm"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
        >
          <RefreshCw className={`h-4 w-4 ${refreshMutation.isPending ? "animate-spin" : ""}`} />
          {refreshMutation.isPending ? "Starting..." : "Refresh All"}
        </Button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs uppercase tracking-widest text-muted-foreground">
              Total Embedded
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold text-ink">{status.total_embeddings.toLocaleString()}</p>
            <p className="mt-1 text-xs text-muted-foreground">
              {status.total_chunks !== status.total_embeddings
                ? `${status.total_chunks.toLocaleString()} chunks`
                : "objects"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs uppercase tracking-widest text-muted-foreground">
              Model
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-semibold text-ink truncate" title={status.model}>
              {status.model.replace("gemini-", "")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{status.dimensions}-dim vectors</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs uppercase tracking-widest text-muted-foreground">
              Last Sync
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-semibold text-ink">{timeAgo(status.last_updated)}</p>
            <p className="mt-1 truncate text-xs text-muted-foreground" title={status.last_updated ?? ""}>
              {status.last_updated ? new Date(status.last_updated).toLocaleString() : "No sync yet"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-xs uppercase tracking-widest text-muted-foreground">
              API Key
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex items-center gap-2">
              {status.api_key_configured ? (
                <>
                  <CheckCircle2 className="h-5 w-5 text-emerald-500" />
                  <span className="text-lg font-semibold text-ink">Configured</span>
                </>
              ) : (
                <>
                  <Key className="h-5 w-5 text-amber-500" />
                  <span className="text-lg font-semibold text-amber-600">Missing</span>
                </>
              )}
            </div>
            <p className="mt-1 text-xs text-muted-foreground">Gemini embedding key</p>
          </CardContent>
        </Card>
      </div>

      {/* Coverage table */}
      <Card>
        <CardHeader>
          <CardTitle>Coverage by Type</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table className="w-full text-sm">
            <TableHeader>
              <TableRow className="border-b bg-muted/30 text-left text-xs uppercase tracking-widest text-muted-foreground">
                <TableHead className="px-5 py-3 font-medium">Type</TableHead>
                <TableHead className="px-5 py-3 font-medium text-right">Embedded</TableHead>
                <TableHead className="px-5 py-3 font-medium text-right">Source</TableHead>
                <TableHead className="px-5 py-3 font-medium text-right">Coverage</TableHead>
                <TableHead className="px-5 py-3 font-medium text-right">Chunks</TableHead>
                <TableHead className="px-5 py-3 font-medium text-right">Last Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {status.types.map((t: EmbeddingTypeStatus) => (
                <TableRow
                  key={t.type}
                  className="border-b border-line/50 transition-colors hover:bg-muted/20"
                >
                  <TableCell className="px-5 py-3 font-medium text-ink">
                    {TYPE_LABELS[t.type] ?? t.type}
                  </TableCell>
                  <TableCell className="px-5 py-3 text-right tabular-nums">
                    {t.embedded_objects.toLocaleString()}
                  </TableCell>
                  <TableCell className="px-5 py-3 text-right tabular-nums text-muted-foreground">
                    {t.source_objects.toLocaleString()}
                  </TableCell>
                  <TableCell className="px-5 py-3 text-right">
                    <CoverageBadge pct={t.coverage_pct} />
                  </TableCell>
                  <TableCell className="px-5 py-3 text-right tabular-nums text-muted-foreground">
                    {t.chunk_count.toLocaleString()}
                  </TableCell>
                  <TableCell className="px-5 py-3 text-right text-muted-foreground">
                    {timeAgo(t.last_updated)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
            <TableFooter>
              <TableRow className="bg-muted/20 font-semibold text-ink">
                <TableCell className="px-5 py-3">Total</TableCell>
                <TableCell className="px-5 py-3 text-right tabular-nums">
                  {status.types.reduce((sum: number, t: EmbeddingTypeStatus) => sum + t.embedded_objects, 0).toLocaleString()}
                </TableCell>
                <TableCell className="px-5 py-3 text-right tabular-nums text-muted-foreground">
                  {status.types.reduce((sum: number, t: EmbeddingTypeStatus) => sum + t.source_objects, 0).toLocaleString()}
                </TableCell>
                <TableCell className="px-5 py-3 text-right">
                  {(() => {
                    const totalSource = status.types.reduce((s: number, t: EmbeddingTypeStatus) => s + t.source_objects, 0);
                    const totalEmbed = status.types.reduce((s: number, t: EmbeddingTypeStatus) => s + t.embedded_objects, 0);
                    const pct = totalSource > 0 ? Math.round(totalEmbed / totalSource * 100) : 0;
                    return <CoverageBadge pct={pct} />;
                  })()}
                </TableCell>
                <TableCell className="px-5 py-3 text-right tabular-nums text-muted-foreground">
                  {status.total_chunks.toLocaleString()}
                </TableCell>
                <TableCell className="px-5 py-3" />
              </TableRow>
            </TableFooter>
          </Table>
        </CardContent>
      </Card>

      {/* Toast */}
      {toast && (
        <Toast variant={toast.variant} onClose={() => setToast(null)}>
          {toast.message}
        </Toast>
      )}
    </div>
  );
}
