import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link2, AlertTriangle, RefreshCw, Check, X, Sparkles, ArrowDownLeft, ArrowUpRight, Undo2, Eye, Map, List, ExternalLink, AlertCircle } from "lucide-react";

import {
  useApplySuggestion,
  useDismissSuggestion,
  useGenerateAnchor,
  useGraphStatsAll,
  useLinkSuggestions,
  useLinkSummary,
  useOrphans,
  useRebuildLinks,
  useAppliedLinks,
  useUndoSuggestion,
  useLinkPreview,
  useGraphMapData,
  useLinkOutcomes,
  useAutoApplySettings,
  type LinkSuggestion,
  type GraphEntity,
  type AppliedLink,
  type GraphNode,
  type GraphEdge,
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";

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

function WeakAnchorWarning({ warning }: { warning: string | null | undefined }) {
  if (!warning) return null;
  return (
    <div className="mt-1 flex items-start gap-1 text-xs text-amber-600">
      <AlertCircle size={12} className="mt-0.5 flex-shrink-0" />
      <span>{warning}</span>
    </div>
  );
}

interface PreviewDialogProps {
  suggestionId: number | null;
  onClose: () => void;
  onConfirmApply: () => void;
  applying: boolean;
  weakAnchorWarning?: string | null;
}

function PreviewDialog({ suggestionId, onClose, onConfirmApply, applying, weakAnchorWarning }: PreviewDialogProps) {
  const { data: preview, isLoading } = useLinkPreview(suggestionId);
  const [acknowledged, setAcknowledged] = useState(false);

  useEffect(() => {
    setAcknowledged(false);
  }, [suggestionId]);

  const requiresAck = Boolean(weakAnchorWarning);

  return (
    <Dialog open={suggestionId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Preview Link Application</DialogTitle>
          <DialogDescription>
            Review how the link will be added before applying.
          </DialogDescription>
        </DialogHeader>
        
        {isLoading ? (
          <div className="space-y-4 py-4">
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </div>
        ) : preview ? (
          <div className="space-y-4 py-4">
            {preview.kind === "phrase_wrap" && (
              <>
                <div>
                  <h4 className="mb-2 text-sm font-medium">Current text:</h4>
                  <div className="rounded border bg-muted/50 p-3 text-sm">
                    {preview.current_body_snippet || "No snippet available"}
                  </div>
                </div>
                <div>
                  <h4 className="mb-2 text-sm font-medium">With link added:</h4>
                  <div 
                    className="rounded border bg-muted/50 p-3 text-sm"
                    dangerouslySetInnerHTML={{ __html: preview.preview_body_snippet || "No preview available" }}
                  />
                </div>
              </>
            )}
            {preview.kind === "ai_woven" && (
              <div>
                <h4 className="mb-2 text-sm font-medium">AI-modified content:</h4>
                <div 
                  className="rounded border bg-muted/50 p-3 text-sm"
                  dangerouslySetInnerHTML={{ __html: preview.preview_body_snippet || "No preview available" }}
                />
                <p className="mt-2 text-xs text-muted-foreground">
                  This will replace the existing body content with AI-modified version.
                </p>
              </div>
            )}
            <div className="text-sm">
              <span className="font-medium">Target URL:</span>{" "}
              <code className="rounded bg-muted px-1">{preview.target_url}</code>
            </div>
          </div>
        ) : null}

        {weakAnchorWarning && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
            <div className="flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 text-amber-600" />
              <div className="text-sm text-amber-800">
                <p className="font-medium">Weak Anchor Warning</p>
                <p className="mt-1">{weakAnchorWarning}</p>
                <label className="mt-2 flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(e) => setAcknowledged(e.target.checked)}
                    className="rounded"
                  />
                  <span>I understand and want to proceed anyway</span>
                </label>
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button 
            onClick={onConfirmApply} 
            disabled={applying || (requiresAck && !acknowledged)}
            className="gap-1"
          >
            <Check size={14} />
            {applying ? "Applying…" : "Apply Link"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SuggestionRow({
  suggestion,
  onApply,
  onDismiss,
  onGenerate,
  onPreview,
  applying,
  dismissing,
  generating,
}: {
  suggestion: LinkSuggestion;
  onApply: () => void;
  onDismiss: () => void;
  onGenerate: () => void;
  onPreview: () => void;
  applying: boolean;
  dismissing: boolean;
  generating: boolean;
}) {
  const canApply = suggestion.kind === "phrase_wrap" || suggestion.ai_anchor_html;
  const hasWeakAnchorWarning = Boolean(suggestion.weak_anchor_warning);

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
        <WeakAnchorWarning warning={suggestion.weak_anchor_warning} />
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
          {suggestion.kind === "ai_woven" && suggestion.ai_anchor_html && (
            <Button
              size="sm"
              variant="outline"
              onClick={onGenerate}
              disabled={generating}
              className="gap-1"
              title="Generate new AI content (regenerate)"
            >
              <RefreshCw size={14} />
              {generating ? "Regenerating…" : "Regenerate"}
            </Button>
          )}
          {canApply && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={onPreview}
                className="gap-1"
                title="Preview before applying"
              >
                <Eye size={14} />
              </Button>
              <Button
                size="sm"
                variant={hasWeakAnchorWarning ? "outline" : "default"}
                onClick={hasWeakAnchorWarning ? onPreview : onApply}
                disabled={applying}
                className="gap-1"
              >
                <Check size={14} />
                {applying ? "Applying…" : "Apply"}
              </Button>
            </>
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

function AppliedRow({
  link,
  onUndo,
  onRegenerate,
  undoing,
}: {
  link: AppliedLink;
  onUndo: () => void;
  onRegenerate: () => void;
  undoing: boolean;
}) {
  const appliedDate = link.applied_at 
    ? new Date(link.applied_at * 1000).toLocaleDateString() 
    : "—";

  return (
    <TableRow>
      <TableCell>
        <div className="text-sm font-medium">{link.source_type}</div>
        <div className="text-xs text-muted-foreground">{link.source_handle}</div>
      </TableCell>
      <TableCell>
        <div className="text-sm font-medium">{link.target_type}</div>
        <div className="text-xs text-muted-foreground">{link.target_handle}</div>
      </TableCell>
      <TableCell>
        <KindBadge kind={link.kind} />
        {link.anchor_phrase && (
          <div className="mt-1 text-xs text-muted-foreground">"{link.anchor_phrase}"</div>
        )}
      </TableCell>
      <TableCell className="text-center">
        {link.live_present === true && (
          <Badge variant="success" className="gap-1">
            <Check size={12} /> Live
          </Badge>
        )}
        {link.live_present === false && (
          <Badge variant="destructive" className="gap-1">
            <X size={12} /> Missing
          </Badge>
        )}
        {link.live_present === null && (
          <Badge variant="outline">Unknown</Badge>
        )}
      </TableCell>
      <TableCell className="text-center text-sm text-muted-foreground">
        {appliedDate}
      </TableCell>
      <TableCell>
        <div className="flex items-center justify-end gap-2">
          {link.href && (
            <a
              href={link.href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-ink"
              title="Open target page"
            >
              <ExternalLink size={14} />
            </a>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={onRegenerate}
            className="gap-1"
            title="Create a new suggestion for this source→target"
          >
            <Sparkles size={14} />
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onUndo}
            disabled={undoing}
            className="gap-1 text-muted-foreground hover:text-red-600"
            title="Remove this link from the page"
          >
            <Undo2 size={14} />
            {undoing ? "Undoing…" : "Undo"}
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

const TYPE_COLORS: Record<string, string> = {
  product: "#3b82f6",
  collection: "#10b981",
  page: "#8b5cf6",
  blog_article: "#f59e0b",
};

function GraphMap({ 
  nodes, 
  edges,
  onNodeClick,
}: { 
  nodes: GraphNode[];
  edges: GraphEdge[];
  onNodeClick?: (node: GraphNode) => void;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 500 });
  
  useEffect(() => {
    const updateDimensions = () => {
      if (svgRef.current?.parentElement) {
        const rect = svgRef.current.parentElement.getBoundingClientRect();
        setDimensions({ width: rect.width, height: Math.max(400, rect.height) });
      }
    };
    updateDimensions();
    window.addEventListener("resize", updateDimensions);
    return () => window.removeEventListener("resize", updateDimensions);
  }, []);

  const { nodePositions, edgePaths } = useMemo(() => {
    const { width, height } = dimensions;
    const padding = 60;
    const positions: Record<string, { x: number; y: number }> = {};
    
    // Simple force-directed-ish layout using circular arrangement
    // with adjustments based on degree
    const n = nodes.length;
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = Math.min(width, height) / 2 - padding;
    
    nodes.forEach((node, i) => {
      const angle = (2 * Math.PI * i) / n - Math.PI / 2;
      const r = node.is_focus ? 0 : radius * (0.5 + 0.5 * Math.random());
      positions[node.id] = {
        x: centerX + (node.is_focus ? 0 : r * Math.cos(angle)),
        y: centerY + (node.is_focus ? 0 : r * Math.sin(angle)),
      };
    });
    
    const paths = edges.map((edge) => {
      const from = positions[edge.source];
      const to = positions[edge.target];
      if (!from || !to) return null;
      return { ...edge, x1: from.x, y1: from.y, x2: to.x, y2: to.y };
    }).filter(Boolean) as Array<GraphEdge & { x1: number; y1: number; x2: number; y2: number }>;
    
    return { nodePositions: positions, edgePaths: paths };
  }, [nodes, edges, dimensions]);

  return (
    <svg
      ref={svgRef}
      width="100%"
      height={dimensions.height}
      className="bg-muted/20"
    >
      <defs>
        <marker
          id="arrowhead"
          markerWidth="10"
          markerHeight="7"
          refX="9"
          refY="3.5"
          orient="auto"
        >
          <polygon points="0 0, 10 3.5, 0 7" fill="#94a3b8" />
        </marker>
      </defs>
      
      {/* Edges */}
      {edgePaths.map((edge, i) => (
        <line
          key={i}
          x1={edge.x1}
          y1={edge.y1}
          x2={edge.x2}
          y2={edge.y2}
          stroke="#94a3b8"
          strokeWidth={1}
          strokeOpacity={0.5}
          markerEnd="url(#arrowhead)"
        />
      ))}
      
      {/* Nodes */}
      {nodes.map((node) => {
        const pos = nodePositions[node.id];
        if (!pos) return null;
        const size = 8 + Math.min(12, (node.inbound + node.outbound) * 2);
        const color = TYPE_COLORS[node.object_type] || "#6b7280";
        
        return (
          <g
            key={node.id}
            transform={`translate(${pos.x}, ${pos.y})`}
            onClick={() => onNodeClick?.(node)}
            className="cursor-pointer"
          >
            <circle
              r={size}
              fill={color}
              stroke={node.is_focus ? "#000" : "white"}
              strokeWidth={node.is_focus ? 3 : 2}
              opacity={node.is_focus ? 1 : 0.8}
            />
            <title>{`${node.object_type}: ${node.handle}\nInbound: ${node.inbound}, Outbound: ${node.outbound}`}</title>
          </g>
        );
      })}
      
      {/* Legend */}
      <g transform="translate(10, 20)">
        {Object.entries(TYPE_COLORS).map(([type, color], i) => (
          <g key={type} transform={`translate(0, ${i * 20})`}>
            <circle r={6} cx={6} cy={0} fill={color} />
            <text x={18} y={4} fontSize={12} fill="#64748b">{type}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}

export function InternalLinksPage() {
  const [toast, setToast] = useState<{ message: string; variant: "success" | "error" | "info" } | null>(null);
  const [tab, setTab] = useState<"suggestions" | "applied" | "orphans" | "graph" | "outcomes">("suggestions");
  const [processingId, setProcessingId] = useState<number | null>(null);
  const [previewId, setPreviewId] = useState<number | null>(null);
  const [graphView, setGraphView] = useState<"list" | "map">("list");
  const [focusNode, setFocusNode] = useState<{ type: string; handle: string } | null>(null);

  const summary = useLinkSummary();
  const suggestions = useLinkSuggestions();
  const appliedLinks = useAppliedLinks();
  const orphans = useOrphans();
  const graphStats = useGraphStatsAll();
  const graphMapData = useGraphMapData(
    focusNode ? { focusType: focusNode.type, focusHandle: focusNode.handle } : { maxNodes: 100 }
  );
  const outcomes = useLinkOutcomes(28);
  const autoApplySettings = useAutoApplySettings();
  const apply = useApplySuggestion();
  const dismiss = useDismissSuggestion();
  const generate = useGenerateAnchor();
  const rebuild = useRebuildLinks();
  const undo = useUndoSuggestion();
  const wasRebuildRunning = useRef(false);

  const previewSuggestion = useMemo(() => {
    if (previewId === null) return null;
    return suggestions.data?.find(s => s.id === previewId) ?? null;
  }, [previewId, suggestions.data]);

  useEffect(() => {
    const running = Boolean(summary.data?.progress?.running);
    const error = summary.data?.progress?.error;
    if (wasRebuildRunning.current && !running) {
      if (error) {
        setToast({ message: `Rebuild failed: ${error}`, variant: "error" });
      } else {
        setToast({ message: "Link rebuild complete", variant: "success" });
      }
    }
    wasRebuildRunning.current = running;
  }, [summary.data?.progress?.running, summary.data?.progress?.error]);

  const handleApply = async (id: number) => {
    setProcessingId(id);
    setPreviewId(null);
    try {
      await apply.mutateAsync(id);
      setToast({ message: "Link applied successfully", variant: "success" });
      // Show nudge for ai_woven to regenerate other suggestions
      const sug = suggestions.data?.find(s => s.id === id);
      if (sug?.kind === "ai_woven") {
        setTimeout(() => {
          setToast({ 
            message: "Remember to Generate again before applying another link on this page", 
            variant: "info" 
          });
        }, 2000);
      }
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

  const handleUndo = async (id: number) => {
    setProcessingId(id);
    try {
      const result = await undo.mutateAsync(id);
      if (result.link_not_found) {
        setToast({ message: "Link was already removed from the page", variant: "info" });
      } else {
        setToast({ message: "Link removed successfully", variant: "success" });
      }
    } catch (e) {
      setToast({ message: `Undo failed: ${(e as Error).message}`, variant: "error" });
    } finally {
      setProcessingId(null);
    }
  };

  const handleNodeClick = useCallback((node: GraphNode) => {
    if (focusNode?.type === node.object_type && focusNode?.handle === node.handle) {
      setFocusNode(null);
    } else {
      setFocusNode({ type: node.object_type, handle: node.handle });
    }
  }, [focusNode]);

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
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        <StatCard label="Total Links" value={summary.data?.total_links ?? 0} loading={isLoading} />
        <StatCard label="Orphan Pages" value={summary.data?.orphan_count ?? 0} loading={isLoading} />
        <StatCard label="Pending Suggestions" value={summary.data?.suggested ?? 0} loading={isLoading} />
        <StatCard label="Applied" value={summary.data?.applied ?? 0} loading={isLoading} />
        <StatCard label="Dismissed" value={summary.data?.dismissed ?? 0} loading={isLoading} />
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

      {!summary.data?.progress?.running && summary.data?.progress?.error && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="flex items-center gap-3 py-4">
            <AlertTriangle size={16} className="text-red-600" />
            <span className="text-sm text-red-700">
              Last rebuild failed: {summary.data.progress.error}
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
          onClick={() => setTab("applied")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            tab === "applied"
              ? "border-b-2 border-blue-600 text-blue-600"
              : "text-muted-foreground hover:text-ink"
          }`}
        >
          Applied ({summary.data?.applied ?? 0})
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
        <button
          onClick={() => setTab("outcomes")}
          className={`px-4 py-2 text-sm font-medium transition-colors ${
            tab === "outcomes"
              ? "border-b-2 border-blue-600 text-blue-600"
              : "text-muted-foreground hover:text-ink"
          }`}
        >
          Outcomes
        </button>
      </div>

      {/* Suggestions tab */}
      {tab === "suggestions" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Link Suggestions</CardTitle>
            <p className="text-sm text-muted-foreground">
              Suggestions are ranked by similarity, traffic, and target value. 
              <strong> Prefer phrase wraps</strong> (green badge) over AI-modified copy (amber). 
              Weak single-word anchors like "products" or "here" are demoted automatically.
            </p>
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
                      onPreview={() => setPreviewId(s.id)}
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

      {/* Applied tab */}
      {tab === "applied" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Applied Links</CardTitle>
          </CardHeader>
          <CardContent>
            {appliedLinks.isLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-16 rounded-lg" />
                ))}
              </div>
            ) : appliedLinks.error ? (
              <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
                <span>Failed to load applied links: {appliedLinks.error.message}</span>
              </div>
            ) : (appliedLinks.data?.length ?? 0) === 0 ? (
              <div className="py-8 text-center text-muted-foreground">
                No links applied yet. Apply suggestions to see them here.
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Source</TableHead>
                    <TableHead>Target</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className="text-center">Status</TableHead>
                    <TableHead className="text-center">Applied</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {appliedLinks.data?.map((link) => (
                    <AppliedRow
                      key={link.id}
                      link={link}
                      onUndo={() => handleUndo(link.id)}
                      onRegenerate={() => {
                        setToast({ message: "Use Rebuild to regenerate suggestions", variant: "info" });
                      }}
                      undoing={processingId === link.id && undo.isPending}
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
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg">Link Graph Statistics</CardTitle>
              <div className="flex items-center gap-2">
                {focusNode && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setFocusNode(null)}
                    className="gap-1"
                  >
                    <X size={14} />
                    Clear focus
                  </Button>
                )}
                <div className="flex rounded-md border">
                  <button
                    onClick={() => setGraphView("list")}
                    className={`flex items-center gap-1 px-3 py-1.5 text-sm ${
                      graphView === "list" ? "bg-muted font-medium" : "text-muted-foreground hover:text-ink"
                    }`}
                  >
                    <List size={14} /> List
                  </button>
                  <button
                    onClick={() => setGraphView("map")}
                    className={`flex items-center gap-1 px-3 py-1.5 text-sm ${
                      graphView === "map" ? "bg-muted font-medium" : "text-muted-foreground hover:text-ink"
                    }`}
                  >
                    <Map size={14} /> Map
                  </button>
                </div>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {graphView === "list" ? (
              // List view
              graphStats.isLoading ? (
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
                      <TableRow 
                        key={i}
                        className="cursor-pointer hover:bg-muted/50"
                        onClick={() => setFocusNode({ type: e.object_type, handle: e.handle })}
                      >
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
              )
            ) : (
              // Map view
              graphMapData.isLoading ? (
                <div className="flex h-96 items-center justify-center">
                  <RefreshCw size={24} className="animate-spin text-muted-foreground" />
                </div>
              ) : graphMapData.error ? (
                <div className="flex h-96 items-center justify-center gap-2 text-muted-foreground">
                  <AlertTriangle className="h-5 w-5 text-amber-500" />
                  <span>Failed to load graph map: {graphMapData.error.message}</span>
                </div>
              ) : !graphMapData.data?.nodes || graphMapData.data.nodes.length === 0 ? (
                <div className="flex h-96 items-center justify-center text-muted-foreground">
                  No link graph data yet. Run a sync to build the link graph.
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="text-sm text-muted-foreground">
                    {focusNode ? (
                      <>Showing neighborhood of <strong>{focusNode.type}:{focusNode.handle}</strong></>
                    ) : (
                      <>Showing top {graphMapData.data.node_count} nodes by degree ({graphMapData.data.edge_count} edges)</>
                    )}
                    {" "} — Click a node to focus on its neighborhood.
                  </div>
                  <div className="rounded border">
                    <GraphMap
                      nodes={graphMapData.data.nodes}
                      edges={graphMapData.data.edges}
                      onNodeClick={handleNodeClick}
                    />
                  </div>
                </div>
              )
            )}
          </CardContent>
        </Card>
      )}

      {/* Outcomes tab */}
      {tab === "outcomes" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Link Outcomes (28 days)</CardTitle>
            <p className="text-sm text-muted-foreground">
              Measurement of applied links and their impact. Auto-apply is{" "}
              <strong>{autoApplySettings.data?.enabled ? "enabled" : "disabled"}</strong>
              {autoApplySettings.data?.enabled && ` (${autoApplySettings.data.applied_today}/${autoApplySettings.data.max_per_day} today)`}.
            </p>
          </CardHeader>
          <CardContent>
            {outcomes.isLoading ? (
              <div className="space-y-4">
                <Skeleton className="h-20" />
                <Skeleton className="h-40" />
              </div>
            ) : outcomes.error ? (
              <div className="flex items-center justify-center gap-2 py-8 text-muted-foreground">
                <AlertTriangle className="h-5 w-5 text-amber-500" />
                <span>Failed to load outcomes: {outcomes.error.message}</span>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Summary cards */}
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <div className="rounded-lg border p-4 text-center">
                    <div className="text-2xl font-bold text-green-600">{outcomes.data?.applied ?? 0}</div>
                    <div className="text-sm text-muted-foreground">Applied</div>
                  </div>
                  <div className="rounded-lg border p-4 text-center">
                    <div className="text-2xl font-bold text-blue-600">{outcomes.data?.auto_applied ?? 0}</div>
                    <div className="text-sm text-muted-foreground">Auto-applied</div>
                  </div>
                  <div className="rounded-lg border p-4 text-center">
                    <div className="text-2xl font-bold text-amber-600">{outcomes.data?.undone ?? 0}</div>
                    <div className="text-sm text-muted-foreground">Undone</div>
                  </div>
                  <div className="rounded-lg border p-4 text-center">
                    <div className="text-2xl font-bold text-muted-foreground">{outcomes.data?.undo_rate_pct ?? 0}%</div>
                    <div className="text-sm text-muted-foreground">Undo Rate</div>
                  </div>
                </div>

                {/* Clicks comparison */}
                {outcomes.data?.clicks_comparison && (
                  <div className="rounded-lg border p-4">
                    <h4 className="mb-2 font-medium">GSC Clicks (Source Pages)</h4>
                    <div className="grid grid-cols-3 gap-4 text-center">
                      <div>
                        <div className="text-lg font-semibold">{outcomes.data.clicks_comparison.at_apply}</div>
                        <div className="text-xs text-muted-foreground">At Apply</div>
                      </div>
                      <div>
                        <div className="text-lg font-semibold">{outcomes.data.clicks_comparison.current}</div>
                        <div className="text-xs text-muted-foreground">Current</div>
                      </div>
                      <div>
                        <div className={`text-lg font-semibold ${outcomes.data.clicks_comparison.change >= 0 ? "text-green-600" : "text-red-600"}`}>
                          {outcomes.data.clicks_comparison.change >= 0 ? "+" : ""}{outcomes.data.clicks_comparison.change}
                        </div>
                        <div className="text-xs text-muted-foreground">Change</div>
                      </div>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      Based on {outcomes.data.clicks_comparison.link_count} applied links with GSC data.
                    </p>
                  </div>
                )}

                {/* Top targets */}
                {outcomes.data?.top_targets && outcomes.data.top_targets.length > 0 && (
                  <div>
                    <h4 className="mb-2 font-medium">Top Link Targets</h4>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Type</TableHead>
                          <TableHead>Handle</TableHead>
                          <TableHead className="text-right">Links</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {outcomes.data.top_targets.map((t, i) => (
                          <TableRow key={i}>
                            <TableCell>
                              <Badge variant="outline">{t.target_type}</Badge>
                            </TableCell>
                            <TableCell className="font-mono text-sm">{t.target_handle}</TableCell>
                            <TableCell className="text-right font-mono text-sm">{t.count}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Preview Dialog */}
      <PreviewDialog
        suggestionId={previewId}
        onClose={() => setPreviewId(null)}
        onConfirmApply={() => previewId && handleApply(previewId)}
        applying={previewId !== null && processingId === previewId && apply.isPending}
        weakAnchorWarning={previewSuggestion?.weak_anchor_warning}
      />

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
