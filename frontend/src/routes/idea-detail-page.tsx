import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BarChart2,
  BookOpen,
  Bot,
  ExternalLink,
  FileText,
  Layers3,
  ListOrdered,
  MessagesSquare,
  Search,
  Sparkles,
  Tag,
  TrendingUp,
  Zap,
  RefreshCw,
} from "lucide-react";
import { z } from "zod";

import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader } from "../components/ui/card";
import { Label } from "../components/ui/label";
import { Input } from "../components/ui/input";
import { Modal } from "../components/ui/modal";
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
import { Textarea } from "../components/ui/textarea";
import { ArticleDraftProgressPanel } from "../components/article-draft-progress-panel";
import { getJson, patchJson, postJson } from "../lib/api";
import {
  runArticleDraftStream,
  type ArticleDraftProgressEvent,
} from "../lib/run-article-draft-stream";
import { defaultDraftSlugHint } from "../lib/seo-slug";
import { useStoreInfo } from "../hooks/use-store-info";
import {
  articleIdeasPayloadSchema,
  refreshArticleIdeaSerpSchema,
  blogShopifyIdSchema,
  ideaPerformancePayloadSchema,
  messageSchema,
  type ArticleIdea,
} from "../types/api";

const blogShopifyIdsSchema = z.array(blogShopifyIdSchema);

const INTENT_LABELS: Record<string, { label: string; color: string }> = {
  informational: { label: "Informational", color: "bg-blue-50 text-blue-700 border-blue-200" },
  commercial: { label: "Commercial", color: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  transactional: { label: "Transactional", color: "bg-purple-50 text-purple-700 border-purple-200" },
  navigational: { label: "Navigational", color: "bg-orange-50 text-orange-700 border-orange-200" },
};

const SOURCE_LABELS: Record<string, { label: string; color: string }> = {
  cluster_gap: { label: "Cluster Gap", color: "bg-blue-50 text-blue-600 border-blue-200" },
  competitor_gap: { label: "Competitor Gap", color: "bg-rose-50 text-rose-600 border-rose-200" },
  collection_gap: { label: "Collection Gap", color: "bg-violet-50 text-violet-600 border-violet-200" },
  query_gap: { label: "Query Gap", color: "bg-amber-50 text-amber-700 border-amber-200" },
};

const STATUS_LABELS: Record<string, string> = {
  idea: "New",
  approved: "Approved",
  published: "Published",
  rejected: "Rejected",
};

function keywordMetricLabel(row: Record<string, unknown>) {
  const bits: string[] = [];
  const volume = Number(row.volume ?? 0);
  const difficulty = Number(row.difficulty ?? 0);
  const position = row.gsc_position == null ? null : Number(row.gsc_position);
  const rankingStatus = typeof row.ranking_status === "string" ? row.ranking_status.replace(/_/g, " ") : "";
  if (Number.isFinite(volume) && volume > 0) bits.push(`${volume.toLocaleString()}/mo`);
  if (Number.isFinite(difficulty) && difficulty > 0) bits.push(`KD ${difficulty.toFixed(0)}`);
  if (position != null && Number.isFinite(position) && position > 0 && position < 900) bits.push(`pos ${position.toFixed(1)}`);
  if (rankingStatus && rankingStatus !== "not ranking") bits.push(rankingStatus);
  return bits.join(" · ");
}

const emptyDraftForm = {
  blog_id: "",
  blog_handle: "",
  topic: "",
  keywords: "",
  slug: "",
  author_name: "",
  angle_label: "",
};

type AiOverviewRef = {
  title?: unknown;
  link?: unknown;
  snippet?: unknown;
  source?: unknown;
  index?: unknown;
};

function isNonEmptyAiOverview(raw: ArticleIdea["ai_overview"]): raw is Record<string, unknown> {
  if (raw == null || typeof raw !== "object" || Array.isArray(raw)) return false;
  const o = raw as { text_blocks?: unknown; references?: unknown };
  const tb = Array.isArray(o.text_blocks) ? o.text_blocks.length : 0;
  const rf = Array.isArray(o.references) ? o.references.length : 0;
  return tb > 0 || rf > 0;
}

function SerpAiOverviewSection({ overview }: { overview: Record<string, unknown> }) {
  const blocks = Array.isArray(overview.text_blocks) ? overview.text_blocks : [];
  const refsIn = Array.isArray(overview.references) ? overview.references : [];
  const references = refsIn.filter(
    (r): r is AiOverviewRef => r != null && typeof r === "object" && typeof (r as AiOverviewRef).link === "string",
  );
  const refByIndex = new Map<number, AiOverviewRef>();
  for (const r of references) {
    const idx = typeof r.index === "number" && !Number.isNaN(r.index) ? r.index : undefined;
    if (idx !== undefined) refByIndex.set(idx, r);
  }

  const refSuperscripts = (indexes: unknown) => {
    if (!Array.isArray(indexes) || indexes.length === 0) return null;
    const nums = indexes.filter((x): x is number => typeof x === "number" && !Number.isNaN(x));
    if (nums.length === 0) return null;
    return (
      <span className="ml-1 inline-flex flex-wrap items-baseline gap-0.5 align-top">
        {nums.map((i) => {
          const ref = refByIndex.get(i);
          const label = String(i + 1);
          const href = ref && typeof ref.link === "string" ? ref.link : "";
          if (!href) {
            return (
              <sup key={`r-${i}`} className="text-[10px] text-slate-400 tabular-nums">
                [{label}]
              </sup>
            );
          }
          return (
            <a
              key={`r-${i}-${href.slice(0, 24)}`}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] font-semibold text-blue-600 hover:text-blue-800 tabular-nums"
            >
              <sup className="underline-offset-2 hover:underline">[{label}]</sup>
            </a>
          );
        })}
      </span>
    );
  };

  const sortedRefs = [...references].sort((a, b) => {
    const ia = typeof a.index === "number" ? a.index : 999;
    const ib = typeof b.index === "number" ? b.index : 999;
    return ia - ib;
  });

  return (
    <Card className="border-[#e2eaf4] bg-gradient-to-b from-violet-50/40 to-white">
      <CardHeader className="px-6 pt-6 pb-0">
        <div className="flex items-center gap-2">
          <Bot size={18} className="text-violet-500" />
          <h3 className="text-lg font-semibold text-ink">AI overview</h3>
        </div>
        <p className="mt-1 text-xs text-slate-400">
          Summarized answer from the Google SERP when available (same SerpAPI search as related questions and top
          pages). Inline numbers link to sources below.
        </p>
      </CardHeader>
      <CardContent className="space-y-5 px-6 pb-6 pt-4">
        {blocks.length > 0 ? (
          <div className="space-y-4 text-sm leading-relaxed text-slate-800">
            {blocks.map((block, bi) => {
              if (!block || typeof block !== "object") return null;
              const b = block as { type?: unknown; snippet?: unknown; list?: unknown; reference_indexes?: unknown };
              if (b.type === "paragraph") {
                const sn = typeof b.snippet === "string" ? b.snippet : "";
                if (!sn && !Array.isArray(b.reference_indexes)) return null;
                return (
                  <p key={`p-${bi}`} className="whitespace-pre-wrap">
                    {sn ? <span>{sn}</span> : null}
                    {refSuperscripts(b.reference_indexes)}
                  </p>
                );
              }
              if (b.type === "list" && Array.isArray(b.list)) {
                const listRefs = refSuperscripts(b.reference_indexes);
                return (
                  <div key={`l-${bi}`}>
                    <ul className="list-disc space-y-2.5 pl-5 marker:text-slate-400">
                      {b.list.map((item, li) => {
                        if (!item || typeof item !== "object") return null;
                        const row = item as { snippet?: unknown; snippet_latex?: unknown };
                        const snippet = typeof row.snippet === "string" ? row.snippet : "";
                        const latexArr = Array.isArray(row.snippet_latex)
                          ? row.snippet_latex.filter((x): x is string => typeof x === "string" && x.trim() !== "")
                          : [];
                        if (!snippet && latexArr.length === 0) return null;
                        return (
                          <li key={`li-${bi}-${li}`} className="pl-0.5">
                            {snippet ? <span className="whitespace-pre-wrap">{snippet}</span> : null}
                            {latexArr.length > 0 ? (
                              <div className="mt-1 space-y-0.5 font-mono text-xs text-slate-600">
                                {latexArr.map((lx, ix) => (
                                  <div key={`lx-${li}-${ix}`} className="rounded bg-slate-100/80 px-2 py-1">
                                    {lx}
                                  </div>
                                ))}
                              </div>
                            ) : null}
                          </li>
                        );
                      })}
                    </ul>
                    {listRefs ? <div className="mt-2">{listRefs}</div> : null}
                  </div>
                );
              }
              return null;
            })}
          </div>
        ) : null}

        {sortedRefs.length > 0 ? (
          <div className="rounded-lg border border-slate-200/80 bg-white/80 p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Sources</p>
            <ol className="mt-3 space-y-3 text-sm">
              {sortedRefs.map((ref, idx) => {
                const title = typeof ref.title === "string" ? ref.title : "";
                const link = typeof ref.link === "string" ? ref.link : "";
                const source = typeof ref.source === "string" ? ref.source : "";
                const snippet = typeof ref.snippet === "string" ? ref.snippet : "";
                const n = typeof ref.index === "number" ? ref.index + 1 : idx + 1;
                return (
                  <li key={`${n}-${link.slice(0, 48)}`} className="leading-snug">
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                      <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded bg-slate-100 px-1 text-[11px] font-semibold text-slate-600 tabular-nums">
                        {n}
                      </span>
                      <a
                        href={link}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-medium text-ink hover:text-blue-700 inline-flex items-center gap-1 group min-w-0"
                      >
                        <span className="group-hover:underline break-words">{title || link}</span>
                        <ExternalLink size={12} className="shrink-0 text-slate-400 group-hover:text-blue-600" />
                      </a>
                    </div>
                    {source ? <p className="mt-0.5 pl-8 text-xs text-slate-500">{source}</p> : null}
                    {snippet ? (
                      <p className="mt-1 pl-8 text-xs text-slate-600 line-clamp-3">{snippet}</p>
                    ) : null}
                  </li>
                );
              })}
            </ol>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function IdeaDetailPage() {
  const { ideaId } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const numericId = Number(ideaId);
  const storeInfoQuery = useStoreInfo();
  const authorFieldTouchedRef = useRef(false);

  // Fetch all ideas to find the one we need (lightweight — cached from list page)
  const ideasQuery = useQuery({
    queryKey: ["article-ideas"],
    queryFn: () => getJson("/api/article-ideas", articleIdeasPayloadSchema),
  });

  const idea: ArticleIdea | undefined = (ideasQuery.data?.items ?? []).find(
    (i) => i.id === numericId,
  );

  const perfQuery = useQuery({
    queryKey: ["idea-performance", numericId],
    queryFn: () =>
      getJson(`/api/article-ideas/${numericId}/performance`, ideaPerformancePayloadSchema),
    enabled: numericId > 0,
  });

  const singleStatusMutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) =>
      patchJson(`/api/article-ideas/${id}/status`, messageSchema, { new_status: status }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["article-ideas"] }),
  });

  // Draft modal
  const [draftModalOpen, setDraftModalOpen] = useState(false);
  const [draftForm, setDraftForm] = useState(emptyDraftForm);
  const [draftError, setDraftError] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [draftGenerating, setDraftGenerating] = useState(false);
  const [draftProgressEvents, setDraftProgressEvents] = useState<ArticleDraftProgressEvent[]>([]);
  const [draftRunKey, setDraftRunKey] = useState(0);
  const [draftModalStep, setDraftModalStep] = useState<1 | 2>(1);
  const [draftResumeRunId, setDraftResumeRunId] = useState("");
  const [serpRefreshBanner, setSerpRefreshBanner] = useState<
    null | { tone: "ok" | "err"; text: string }
  >(null);

  const refreshSerpMutation = useMutation({
    mutationFn: () =>
      postJson(`/api/article-ideas/${numericId}/refresh-serp`, refreshArticleIdeaSerpSchema, {}),
    onMutate: () => setSerpRefreshBanner(null),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["article-ideas"] });
      setSerpRefreshBanner({ tone: "ok", text: "SERP data updated from SerpAPI." });
    },
    onError: (e: unknown) => {
      const msg = e instanceof Error ? e.message : "Refresh failed.";
      setSerpRefreshBanner({ tone: "err", text: msg || "Refresh failed." });
    },
  });

  const blogsQuery = useQuery({
    queryKey: ["blogs-shopify-ids"],
    queryFn: () => getJson("/api/blogs/shopify-ids", blogShopifyIdsSchema),
    enabled: draftModalOpen,
  });

  function openDraftModal() {
    if (!idea) return;
    authorFieldTouchedRef.current = false;
    setDraftProgressEvents([]);
    setDraftResumeRunId("");
    setDraftModalStep(1);
    const keywords = [idea.primary_keyword, ...idea.supporting_keywords]
      .filter(Boolean)
      .join(", ");
    const defaultAuthor = storeInfoQuery.data?.store_name?.trim() ?? "";
    setDraftForm({
      blog_id: "",
      blog_handle: "",
      topic: idea.suggested_title,
      keywords,
      slug: defaultDraftSlugHint(idea.suggested_title, keywords),
      author_name: defaultAuthor,
      angle_label: "",
    });
    setSlugTouched(false);
    setDraftError("");
    setDraftModalOpen(true);
  }

  // If store name loads after the modal opens, fill author once (until the user edits the field).
  useEffect(() => {
    if (!draftModalOpen || draftModalStep !== 1 || authorFieldTouchedRef.current) return;
    const name = storeInfoQuery.data?.store_name?.trim();
    if (!name) return;
    setDraftForm((f) => {
      if (f.author_name.trim() !== "") return f;
      return { ...f, author_name: name };
    });
  }, [draftModalOpen, draftModalStep, storeInfoQuery.data?.store_name]);

  useEffect(() => {
    if (!draftModalOpen || draftModalStep !== 1) return;
    const blogs = blogsQuery.data;
    if (!blogs || blogs.length !== 1 || !blogs[0]?.id) return;
    setDraftForm((f) => {
      if (f.blog_id.trim()) return f;
      return { ...f, blog_id: blogs[0].id, blog_handle: blogs[0].handle ?? "" };
    });
  }, [draftModalOpen, draftModalStep, blogsQuery.data]);

  async function submitDraft(resumeRunId = "") {
    if (!idea) return;
    setDraftError("");
    setDraftModalStep(2);
    setDraftRunKey((k) => k + 1);
    setDraftProgressEvents([]);
    if (!resumeRunId) setDraftResumeRunId("");
    setDraftGenerating(true);
    try {
      const keywords = draftForm.keywords
        ? draftForm.keywords.split(",").map((k) => k.trim()).filter(Boolean)
        : [];
      const data = await runArticleDraftStream(
        {
          blog_id: draftForm.blog_id,
          blog_handle: draftForm.blog_handle,
          topic: draftForm.topic,
          keywords,
          author_name: draftForm.author_name,
          slug_hint: draftForm.slug.trim(),
          idea_id: idea.id,
          angle_label: draftForm.angle_label.trim(),
          ...(resumeRunId ? { resume_run_id: resumeRunId } : {})
        },
        (evt) => {
          if (evt.run_id) setDraftResumeRunId(evt.run_id);
          setDraftProgressEvents((prev) => [...prev, evt]);
        },
      );
      setDraftModalOpen(false);
      setDraftModalStep(1);
      setDraftForm(emptyDraftForm);
      setSlugTouched(false);
      setDraftResumeRunId("");
      setDraftProgressEvents([]);
      void queryClient.invalidateQueries({ queryKey: ["all-articles"] });
      void queryClient.invalidateQueries({ queryKey: ["article-ideas"] });
      void queryClient.invalidateQueries({ queryKey: ["idea-performance", numericId] });
      navigate(
        `/articles/${encodeURIComponent(data.blog_handle)}/${encodeURIComponent(data.handle)}`,
      );
    } catch (err) {
      setDraftError((err as Error).message || "Failed to generate draft");
    } finally {
      setDraftGenerating(false);
    }
  }

  const canSubmitDraft = draftForm.blog_id.trim() && draftForm.topic.trim() && !draftGenerating;

  // Loading / not found
  if (ideasQuery.isLoading) {
    return (
      <div className="flex min-h-[300px] items-center justify-center text-sm text-slate-400">
        Loading…
      </div>
    );
  }
  if (!idea) {
    return (
      <div className="space-y-4 pb-12">
        <Link
          to="/article-ideas"
          className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-ink transition"
        >
          <ArrowLeft size={14} /> Back to ideas
        </Link>
        <div className="flex min-h-[200px] items-center justify-center rounded-[24px] border-2 border-dashed border-slate-200 text-sm text-slate-400">
          Idea not found.
        </div>
      </div>
    );
  }

  const perf = perfQuery.data;
  const intent = INTENT_LABELS[idea.search_intent] ?? INTENT_LABELS.informational;
  const source = SOURCE_LABELS[idea.source_type] ?? SOURCE_LABELS.cluster_gap;
  const supportingKw: string[] = Array.isArray(idea.supporting_keywords)
    ? idea.supporting_keywords
    : [];
  const clusterKeywordRows = (idea.linked_keywords_json ?? [])
    .filter((row): row is Record<string, unknown> => row != null && typeof row === "object" && !Array.isArray(row))
    .filter((row) => typeof row.keyword === "string" && row.keyword.trim().length > 0);
  const clusterKeywords = clusterKeywordRows.slice(0, 18);
  const hasClusterRelated =
    idea.linked_cluster_id != null
    || (idea.linked_cluster_name?.trim() ?? "") !== ""
    || (idea.linked_collection_title?.trim() ?? "") !== ""
    || clusterKeywordRows.length > 0;
  const date = new Date(idea.created_at * 1000).toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  return (
    <div className="space-y-6 pb-12">
      {/* Back link */}
      <Link
        to="/article-ideas"
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-ink transition"
      >
        <ArrowLeft size={14} /> Back to ideas
      </Link>

      {/* Header */}
      <div className="space-y-2">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Article Idea</p>
            <h2 className="mt-2 text-3xl font-bold text-ink leading-snug">
              {idea.suggested_title}
            </h2>
            <div className="mt-3 flex flex-wrap gap-1.5">
              <span
                className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${intent.color}`}
              >
                {intent.label}
              </span>
              <span
                className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${source.color}`}
              >
                {source.label}
              </span>
              <span className="text-xs text-slate-400 self-center ml-1">Generated {date}</span>
            </div>
          </div>
          <div className="flex shrink-0 flex-col items-stretch gap-2 sm:items-end sm:pt-2">
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Select
                value={idea.status}
                onValueChange={(value) =>
                  singleStatusMutation.mutate({ id: idea.id, status: value })
                }
              >
                <SelectTrigger className="h-8 w-[130px] rounded-lg border-line bg-white px-3 py-1 text-sm text-ink">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="idea">New</SelectItem>
                  <SelectItem value="approved">Approved</SelectItem>
                  <SelectItem value="published">Published</SelectItem>
                  <SelectItem value="rejected">Rejected</SelectItem>
                </SelectContent>
              </Select>
              <Button
                type="button"
                variant="outline"
                className="border-slate-300 bg-white"
                disabled={
                  !idea.primary_keyword?.trim()
                  || refreshSerpMutation.isPending
                  || Number.isNaN(numericId)
                  || numericId <= 0
                }
                onClick={() => refreshSerpMutation.mutate()}
              >
                <RefreshCw size={15} className={refreshSerpMutation.isPending ? "animate-spin" : ""} />
                {refreshSerpMutation.isPending ? "Refreshing…" : "Refresh SERP data"}
              </Button>
              <Button onClick={openDraftModal}>
                <Sparkles size={15} />
                Draft Article
              </Button>
            </div>
            {serpRefreshBanner ? (
              <p
                className={
                  serpRefreshBanner.tone === "ok"
                    ? "text-right text-sm text-emerald-700"
                    : "text-right text-sm text-red-600"
                }
                role="status"
              >
                {serpRefreshBanner.text}
              </p>
            ) : null}
          </div>
        </div>
      </div>

      {idea.linked_cluster_id == null ? (
        <div
          className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950"
          role="status"
        >
          <span className="font-semibold">Cluster not linked.</span>{" "}
          This idea has no <code className="rounded bg-amber-100/80 px-1">linked_cluster_id</code>, so AI drafts will
          not receive cluster SEO keyword gaps from the database—only the keywords you enter here, SERP data on the
          idea, and interlink targets. Link the idea to a cluster (see maintainer docs / SQL) to restore full cluster
          gap coverage in drafts.
        </div>
      ) : null}

      {/* Two-column layout: Brief + Sidebar info */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Main content — 2/3 */}
        <div className="lg:col-span-2 space-y-6">
          {/* Brief */}
          <Card className="border-[#e2eaf4]">
            <CardHeader className="px-6 pt-6 pb-0">
              <h3 className="text-lg font-semibold text-ink">Brief</h3>
            </CardHeader>
            <CardContent className="px-6 pb-6 pt-3">
              <p className="text-sm text-slate-700 leading-relaxed whitespace-pre-line">
                {idea.brief}
              </p>
            </CardContent>
          </Card>

          {/* Gap reason */}
          {idea.gap_reason ? (
            <div className="flex items-start gap-3 rounded-2xl bg-amber-50 border border-amber-100 px-5 py-4">
              <TrendingUp size={16} className="mt-0.5 shrink-0 text-amber-600" />
              <div>
                <p className="text-sm font-semibold text-amber-800">Gap Reason</p>
                <p className="mt-1 text-sm text-amber-700">{idea.gap_reason}</p>
              </div>
            </div>
          ) : null}

          {idea && isNonEmptyAiOverview(idea.ai_overview) ? (
            <SerpAiOverviewSection overview={idea.ai_overview} />
          ) : null}

          {/* Top organic results (same SerpAPI Google search as related questions) */}
          <Card className="border-[#e2eaf4]">
            <CardHeader className="px-6 pt-6 pb-0">
              <div className="flex items-center gap-2">
                <Search size={18} className="text-slate-400" />
                <h3 className="text-lg font-semibold text-ink">Top Ranking Pages</h3>
              </div>
              <p className="mt-1 text-xs text-slate-400">
                Organic result titles and URLs from the Google SERP for the primary keyword (via SerpAPI at idea
                generation or after a manual refresh). Useful for competitive context and outline benchmarking.
              </p>
            </CardHeader>
            <CardContent className="px-6 pb-6 pt-3">
              {idea.top_ranking_pages && idea.top_ranking_pages.length > 0 ? (
                <ol className="list-decimal space-y-3 pl-4 text-sm leading-relaxed">
                  {idea.top_ranking_pages.map((row, idx) => (
                    <li key={`${idx}-${row.url.slice(0, 64)}`} className="pl-1 marker:text-slate-400">
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-medium text-ink hover:text-blue-700 inline-flex items-center gap-1 group"
                      >
                        <span className="group-hover:underline">{row.title}</span>
                        <ExternalLink size={13} className="shrink-0 text-slate-400 group-hover:text-blue-600" />
                      </a>
                      <p className="mt-0.5 text-xs text-slate-500 break-all font-normal">{row.url}</p>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-sm text-slate-400 leading-relaxed">
                  No ranking pages stored. Save a SerpAPI key under Settings → Integrations, then generate new ideas —
                  each idea captures the first-page organic list for its primary keyword.
                </p>
              )}
            </CardContent>
          </Card>

          {/* Related questions (SerpAPI at generation time) */}
          <Card className="border-[#e2eaf4]">
            <CardHeader className="px-6 pt-6 pb-0">
              <div className="flex items-center gap-2">
                <MessagesSquare size={18} className="text-slate-400" />
                <h3 className="text-lg font-semibold text-ink">Related questions</h3>
              </div>
              <p className="mt-1 text-xs text-slate-400">
                From SerpAPI (Google Search “People also ask”) when this idea was generated or after a manual refresh,
                using the primary keyword. Each row stores the question and Google’s ``snippet`` preview from the SERP
                JSON. Intended for headings, FAQ, and draft enrichment.
              </p>
            </CardHeader>
            <CardContent className="px-6 pb-6 pt-3">
              {idea.audience_questions && idea.audience_questions.length > 0 ? (
                <ol className="list-decimal space-y-3 pl-4 text-sm text-slate-700 leading-relaxed">
                  {idea.audience_questions.map((row, idx) => (
                    <li
                      key={`${idx}-${row.question.slice(0, 48)}`}
                      className="pl-1 marker:text-slate-400"
                    >
                      <span className="font-medium text-ink">{row.question}</span>
                      {row.snippet ? (
                        <p className="mt-1 text-sm font-normal text-slate-600 whitespace-pre-wrap">{row.snippet}</p>
                      ) : null}
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="text-sm text-slate-400 leading-relaxed">
                  No questions stored for this idea. Save a SerpAPI key under Settings → Integrations, then generate
                  new ideas — each idea fetches related questions for its primary keyword via SerpAPI.
                </p>
              )}
            </CardContent>
          </Card>

          {/* Linked articles table */}
          <Card className="border-[#e2eaf4]">
            <CardHeader className="px-6 pt-6 pb-0 flex flex-row items-center justify-between">
              <h3 className="text-lg font-semibold text-ink">
                Linked Articles
                {perf?.articles
                  ? <span className="ml-2 text-sm font-normal text-slate-400">({perf.articles.length})</span>
                  : null}
              </h3>
              <Button size="sm" variant="outline" onClick={openDraftModal}>
                <Sparkles size={13} />
                Draft another article
              </Button>
            </CardHeader>
            <CardContent className="px-6 pb-6 pt-4">
              {perfQuery.isLoading ? (
                <div className="flex min-h-[80px] items-center justify-center text-sm text-slate-400">
                  Loading…
                </div>
              ) : perf?.articles && perf.articles.length > 0 ? (
                <div className="overflow-x-auto">
                  <Table className="w-full text-sm">
                    <TableHeader>
                      <TableRow className="border-b border-line text-left text-xs font-medium text-slate-400">
                        <TableHead className="pb-2 pr-3">Title</TableHead>
                        <TableHead className="pb-2 pr-3">Angle</TableHead>
                        <TableHead className="pb-2 pr-3 text-right">Clicks</TableHead>
                        <TableHead className="pb-2 pr-3 text-right">Impressions</TableHead>
                        <TableHead className="pb-2 pr-3 text-right">Position</TableHead>
                        <TableHead className="pb-2">Published</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody className="divide-y divide-line/60">
                      {perf.articles.map((art) => (
                        <TableRow key={art.id} className="hover:bg-slate-50/60">
                          <TableCell className="py-2.5 pr-3">
                            <Link
                              to={`/articles/${art.blog_handle}/${art.article_handle}`}
                              className="inline-flex items-center gap-1.5 font-medium text-ink hover:text-[#2e6be6] transition"
                            >
                              {art.article_title || art.article_handle}
                              <ExternalLink size={12} className="text-slate-400" />
                            </Link>
                          </TableCell>
                          <TableCell className="py-2.5 pr-3 text-slate-500 italic">
                            {art.angle_label || <span className="text-slate-300">—</span>}
                          </TableCell>
                          <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                            {art.gsc_clicks.toLocaleString()}
                          </TableCell>
                          <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                            {art.gsc_impressions.toLocaleString()}
                          </TableCell>
                          <TableCell className="py-2.5 pr-3 text-right text-slate-600">
                            {art.gsc_position != null ? art.gsc_position.toFixed(1) : "—"}
                          </TableCell>
                          <TableCell className="py-2.5">
                            {art.is_published ? (
                              <span className="inline-block rounded-full bg-emerald-50 text-emerald-700 px-2 py-0.5 text-[11px] font-medium">
                                Published
                              </span>
                            ) : (
                              <span className="inline-block rounded-full bg-slate-100 text-slate-500 px-2 py-0.5 text-[11px] font-medium">
                                Draft
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <div className="flex min-h-[80px] items-center justify-center rounded-xl border-2 border-dashed border-slate-200 text-sm text-slate-400">
                  No articles linked yet. Draft your first article from this idea.
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Sidebar — 1/3 */}
        <div className="space-y-6">
          {/* Keywords */}
          <Card className="border-[#e2eaf4]">
            <CardHeader className="px-5 pt-5 pb-0">
              <h4 className="text-sm font-semibold text-ink">Keywords</h4>
            </CardHeader>
            <CardContent className="px-5 pb-5 pt-3">
              <div className="flex flex-wrap gap-1.5">
                {idea.primary_keyword ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-[#2e6be6]/[0.08] px-2.5 py-1 text-xs font-semibold text-[#2e6be6]">
                    <Tag size={10} />
                    {idea.primary_keyword}
                  </span>
                ) : null}
                {supportingKw.map((kw) => (
                  <span
                    key={kw}
                    className="rounded-full bg-slate-100 px-2.5 py-1 text-xs text-slate-600"
                  >
                    {kw}
                  </span>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Metrics */}
          {(idea.total_volume > 0 ||
            idea.avg_difficulty > 0 ||
            idea.opportunity_score > 0 ||
            idea.estimated_monthly_traffic > 0) ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-ink">Metrics</h4>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3 space-y-2.5">
                {idea.total_volume > 0 ? (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-slate-500">
                      <BarChart2 size={13} className="text-slate-400" /> Volume
                    </span>
                    <span className="font-semibold text-ink">
                      {idea.total_volume.toLocaleString()}
                    </span>
                  </div>
                ) : null}
                {idea.avg_difficulty > 0 ? (
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-slate-500">Keyword Difficulty</span>
                    <span
                      className={`font-semibold ${
                        idea.avg_difficulty < 30
                          ? "text-emerald-600"
                          : idea.avg_difficulty < 60
                          ? "text-amber-600"
                          : "text-red-600"
                      }`}
                    >
                      {idea.avg_difficulty.toFixed(0)}
                    </span>
                  </div>
                ) : null}
                {idea.opportunity_score > 0 ? (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-slate-500">
                      <Zap size={13} className="text-amber-500" /> Opportunity
                    </span>
                    <span className="font-semibold text-ink">
                      {idea.opportunity_score.toFixed(0)}
                    </span>
                  </div>
                ) : null}
                {idea.estimated_monthly_traffic > 0 ? (
                  <div className="flex items-center justify-between text-sm">
                    <span className="flex items-center gap-1.5 text-slate-500">
                      <TrendingUp size={13} className="text-emerald-500" /> Est. traffic
                    </span>
                    <span className="font-semibold text-ink">
                      ~{idea.estimated_monthly_traffic}/mo
                    </span>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          {/* SERP signals (gap analysis + live SerpAPI block) */}
          {(idea.dominant_serp_features || idea.content_format_hints) ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-ink">SERP signals</h4>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3 space-y-3">
                {idea.dominant_serp_features ? (
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                      Dominant SERP features
                    </p>
                    <p className="mt-1 text-sm text-slate-700 leading-relaxed">
                      {idea.dominant_serp_features}
                    </p>
                  </div>
                ) : null}
                {idea.content_format_hints ? (
                  <div>
                    <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                      Content format hints
                    </p>
                    <p className="mt-1 text-sm text-slate-700 leading-relaxed">
                      {idea.content_format_hints}
                    </p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          {/* Related searches from SerpAPI */}
          <Card className="border-[#e2eaf4]">
            <CardHeader className="px-5 pt-5 pb-0">
              <div className="flex items-center gap-2">
                <ListOrdered size={15} className="text-slate-400" />
                <h4 className="text-sm font-semibold text-ink">Related searches</h4>
              </div>
              <p className="mt-1 text-xs text-slate-500 leading-relaxed">
                From the same Google SERP (SerpAPI). Position uses the API when present; otherwise order on the page
                (1 = first).
              </p>
            </CardHeader>
            <CardContent className="px-5 pb-5 pt-3">
              {idea.related_searches && idea.related_searches.length > 0 ? (
                <div className="overflow-x-auto rounded-lg border border-slate-200/80">
                  <Table className="w-full text-xs">
                    <TableHeader>
                      <TableRow className="border-b border-line bg-slate-50/80 text-left font-medium text-slate-500">
                        <TableHead className="w-14 px-2 py-1.5">#</TableHead>
                        <TableHead className="px-2 py-1.5">Query</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody className="divide-y divide-line/60">
                      {[...idea.related_searches]
                        .sort((a, b) => a.position - b.position || a.query.localeCompare(b.query))
                        .map((row) => (
                          <TableRow key={`${row.position}-${row.query.slice(0, 48)}`} className="hover:bg-slate-50/50">
                            <TableCell className="px-2 py-2 font-mono text-slate-500 tabular-nums">
                              {row.position}
                            </TableCell>
                            <TableCell className="px-2 py-2 text-slate-800 leading-snug">{row.query}</TableCell>
                          </TableRow>
                        ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <p className="text-xs text-slate-500 leading-relaxed">
                  None stored yet. Use <span className="font-medium text-slate-600">Refresh SERP data</span> above when
                  a SerpAPI key is saved, or generate a new idea.
                </p>
              )}
            </CardContent>
          </Card>

          {/* Aggregate performance */}
          {perf?.aggregate && perf.articles && perf.articles.length > 0 ? (
            <Card className="border-emerald-200 bg-emerald-50/50">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-emerald-800">Performance</h4>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3 space-y-2.5">
                <div className="flex items-center justify-between text-sm">
                  <span className="text-emerald-700">Total clicks</span>
                  <span className="font-semibold text-emerald-800">
                    {(perf.aggregate.total_clicks ?? 0).toLocaleString()}
                  </span>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-emerald-700">Total impressions</span>
                  <span className="font-semibold text-emerald-800">
                    {(perf.aggregate.total_impressions ?? 0).toLocaleString()}
                  </span>
                </div>
                {perf.aggregate.avg_position != null ? (
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-emerald-700">Avg position</span>
                    <span className="font-semibold text-emerald-800">
                      {perf.aggregate.avg_position.toFixed(1)}
                    </span>
                  </div>
                ) : null}
                <div className="flex items-center justify-between text-sm">
                  <span className="text-emerald-700">Articles</span>
                  <span className="font-semibold text-emerald-800">
                    {perf.aggregate.article_count} ({perf.aggregate.published_count} published)
                  </span>
                </div>
              </CardContent>
            </Card>
          ) : null}

          {/* Keyword coverage */}
          {perf?.keyword_coverage && perf.keyword_coverage.total_targets > 0 ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-ink">Keyword Coverage</h4>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3 space-y-2">
                <div className="flex items-center gap-3">
                  <div className="flex-1 h-2.5 rounded-full bg-slate-200 overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        perf.keyword_coverage.coverage_pct >= 75
                          ? "bg-emerald-500"
                          : perf.keyword_coverage.coverage_pct >= 40
                          ? "bg-amber-500"
                          : "bg-red-500"
                      }`}
                      style={{ width: `${perf.keyword_coverage.coverage_pct}%` }}
                    />
                  </div>
                  <span className="text-sm font-bold text-slate-700">
                    {perf.keyword_coverage.coverage_pct.toFixed(0)}%
                  </span>
                </div>
                <p className="text-xs text-slate-500">
                  {perf.keyword_coverage.ranking_count} of{" "}
                  {perf.keyword_coverage.total_targets} target keywords ranking ·{" "}
                  {perf.keyword_coverage.gap_count} gap
                  {perf.keyword_coverage.gap_count !== 1 ? "s" : ""}
                </p>
              </CardContent>
            </Card>
          ) : null}

          {/* Linked cluster, snapshot keywords, and collection */}
          {hasClusterRelated ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-ink">{"Cluster & collection"}</h4>
                <p className="mt-1 text-xs text-slate-500">
                  Keyword cluster for topical coverage; collection link when the idea targets a specific category page.
                </p>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3 space-y-4">
                <div className="flex flex-wrap items-center gap-2">
                  {idea.linked_cluster_name ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-[#c7d9f8] bg-[#f0f6ff] px-2.5 py-1 text-xs text-[#2e6be6]">
                      <BookOpen size={11} />
                      {idea.linked_cluster_name}
                    </span>
                  ) : idea.linked_cluster_id != null ? (
                    <Link
                      to={`/keywords/clusters/${idea.linked_cluster_id}`}
                      className="inline-flex items-center gap-1.5 rounded-full border border-[#c7d9f8] bg-[#f0f6ff] px-2.5 py-1 text-xs font-medium text-[#2e6be6] hover:bg-[#e3eeff]"
                    >
                      <BookOpen size={11} />
                      View cluster #{idea.linked_cluster_id}
                    </Link>
                  ) : null}
                  {idea.linked_cluster_id != null && idea.linked_cluster_name ? (
                    <Link
                      to={`/keywords/clusters/${idea.linked_cluster_id}`}
                      className="text-[11px] font-medium text-[#2e6be6] hover:underline"
                    >
                      Open in Keywords →
                    </Link>
                  ) : null}
                  {idea.linked_collection_title ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d1e8d4] bg-[#f0faf1] px-2.5 py-1 text-xs text-emerald-700">
                      <Layers3 size={11} />
                      {idea.linked_collection_title}
                    </span>
                  ) : null}
                </div>
                {clusterKeywords.length > 0 ? (
                  <div>
                    <div className="mb-2 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                      <Tag size={11} />
                      Cluster keywords
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {clusterKeywords.map((row) => {
                        const keyword = String(row.keyword || "").trim();
                        const metric = keywordMetricLabel(row);
                        return (
                          <span
                            key={keyword}
                            className="inline-flex max-w-full flex-col rounded-md border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-slate-700"
                          >
                            <span className="truncate font-medium">{keyword}</span>
                            {metric ? <span className="mt-0.5 text-[11px] text-slate-500">{metric}</span> : null}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                ) : idea.linked_cluster_id != null ? (
                  <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50/80 px-3 py-2.5 text-xs text-slate-600">
                    <p className="font-medium text-slate-700">No keyword snapshot on this idea</p>
                    <p className="mt-1 leading-relaxed">
                      Open the cluster for the full keyword list and metrics, or generate new ideas to refresh stored
                      cluster keywords.
                    </p>
                    <Link
                      to={`/keywords/clusters/${idea.linked_cluster_id}`}
                      className="mt-2 inline-block font-medium text-[#2e6be6] hover:underline"
                    >
                      View all keywords in Keywords →
                    </Link>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          {/* Interlink targets — authority page + related pages */}
          {(idea.primary_target || (idea.secondary_targets && idea.secondary_targets.length > 0)) ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-ink">Interlink Targets</h4>
                <p className="mt-1 text-xs text-slate-500">
                  This article will link back to these pages to build topical authority.
                </p>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3 space-y-3">
                {idea.primary_target ? (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-1">
                      Primary authority page
                    </div>
                    <a
                      href={idea.primary_target.url || "#"}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-[#c7d9f8] bg-[#f0f6ff] px-2.5 py-1 text-xs font-medium text-[#2e6be6] hover:bg-[#e3eeff]"
                      title={idea.primary_target.url}
                    >
                      <span className="rounded bg-[#2e6be6] px-1 py-0.5 text-[10px] font-semibold text-white">
                        {idea.primary_target.type}
                      </span>
                      <span className="truncate">{idea.primary_target.title || idea.primary_target.handle}</span>
                    </a>
                  </div>
                ) : null}
                {idea.secondary_targets && idea.secondary_targets.length > 0 ? (
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500 mb-1">
                      Related pages ({idea.secondary_targets.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {idea.secondary_targets.map((t) => (
                        <a
                          key={`${t.type}:${t.handle}`}
                          href={t.url || "#"}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[11px] text-slate-700 hover:border-slate-300 hover:bg-slate-50"
                          title={`${t.url}${t.anchor_keyword ? ` — anchor: ${t.anchor_keyword}` : ""}`}
                        >
                          <span className="text-slate-400">{t.type}</span>
                          <span className="truncate max-w-[180px]">{t.title || t.handle}</span>
                          {t.anchor_keyword ? (
                            <span className="text-slate-400">· {t.anchor_keyword}</span>
                          ) : null}
                        </a>
                      ))}
                    </div>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}
        </div>
      </div>

      {/* Draft article modal */}
      <Modal
        open={draftModalOpen}
        onOpenChange={(open) => {
          if (!draftGenerating) {
            setDraftModalOpen(open);
            if (!open) {
              setDraftProgressEvents([]);
              setDraftResumeRunId("");
              setDraftModalStep(1);
            }
          }
        }}
        title={draftModalStep === 2 ? "Creating your draft" : "Draft article from idea"}
        description={
          draftModalStep === 2
            ? "Generating content and images, then creating the Shopify draft."
            : "Generate a full article using AI based on this idea"
        }
      >
        <div className="space-y-4">
          {draftModalStep === 1 ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="idea-draft-blog">Blog</Label>
                <Select
                  value={draftForm.blog_id}
                  onValueChange={(val) => {
                    const blog = (blogsQuery.data ?? []).find((b) => b.id === val);
                    setDraftForm((f) => ({
                      ...f,
                      blog_id: val,
                      blog_handle: blog?.handle ?? "",
                    }));
                  }}
                >
                  <SelectTrigger id="idea-draft-blog">
                    <SelectValue
                      placeholder={
                        blogsQuery.isLoading ? "Loading blogs…" : "Select a blog"
                      }
                    />
                  </SelectTrigger>
                  <SelectContent>
                    {(blogsQuery.data ?? []).map((blog) => (
                      <SelectItem key={blog.id} value={blog.id}>
                        {blog.title}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="idea-draft-topic">Topic / title</Label>
                <Input
                  id="idea-draft-topic"
                  value={draftForm.topic}
                  onChange={(e) => {
                    const topic = e.target.value;
                    setDraftForm((f) =>
                      !slugTouched
                        ? { ...f, topic, slug: defaultDraftSlugHint(topic, f.keywords) }
                        : { ...f, topic },
                    );
                  }}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="idea-draft-keywords">
                  Target keywords{" "}
                  <span className="text-slate-400 font-normal">(comma-separated)</span>
                </Label>
                <Textarea
                  id="idea-draft-keywords"
                  value={draftForm.keywords}
                  rows={2}
                  onChange={(e) => {
                    const keywords = e.target.value;
                    setDraftForm((f) =>
                      !slugTouched
                        ? { ...f, keywords, slug: defaultDraftSlugHint(f.topic, keywords) }
                        : { ...f, keywords },
                    );
                  }}
                />
              </div>

              <div className="grid gap-2">
                <Label htmlFor="idea-draft-angle">
                  Angle label{" "}
                  <span className="text-slate-400 font-normal">
                    (optional — e.g. "listicle", "how-to", "comparison")
                  </span>
                </Label>
                <Input
                  id="idea-draft-angle"
                  placeholder="e.g. how-to guide"
                  value={draftForm.angle_label}
                  onChange={(e) =>
                    setDraftForm((f) => ({ ...f, angle_label: e.target.value }))
                  }
                />
              </div>

              <div className="grid gap-2">
                <div className="flex items-center justify-between gap-2">
                  <Label htmlFor="idea-draft-slug">
                    URL slug{" "}
                    <span className="text-slate-400 font-normal">(Shopify handle)</span>
                  </Label>
                  <Button
                    variant="link"
                    className="h-auto p-0 text-xs font-medium text-ocean underline-offset-4 hover:underline disabled:opacity-40"
                    disabled={draftGenerating}
                    onClick={() => {
                      setSlugTouched(false);
                      setDraftForm((f) => ({
                        ...f,
                        slug: defaultDraftSlugHint(f.topic, f.keywords),
                      }));
                    }}
                  >
                    Reset to suggested
                  </Button>
                </div>
                <Input
                  id="idea-draft-slug"
                  placeholder="e.g. salt-nic-vs-freebase-guide"
                  value={draftForm.slug}
                  onChange={(e) => {
                    setSlugTouched(true);
                    setDraftForm((f) => ({ ...f, slug: e.target.value }));
                  }}
                />
                <p className="text-xs text-slate-500">
                  Suggested from topic and first keyword. Clear the field to use the AI
                  headline as the handle instead.
                </p>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="idea-draft-author">
                  Author name{" "}
                  <span className="text-slate-400 font-normal">(optional)</span>
                </Label>
                <Input
                  id="idea-draft-author"
                  placeholder={
                    storeInfoQuery.data?.store_name?.trim()
                      ? `Defaults to ${storeInfoQuery.data.store_name.trim()}`
                      : "Store name from settings"
                  }
                  value={draftForm.author_name}
                  onChange={(e) => {
                    authorFieldTouchedRef.current = true;
                    setDraftForm((f) => ({ ...f, author_name: e.target.value }));
                  }}
                />
              </div>

              {draftError ? (
                <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
                  {draftError}
                </p>
              ) : null}

              <div className="flex justify-end gap-3 pt-2">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setDraftModalOpen(false);
                    setDraftProgressEvents([]);
                    setDraftResumeRunId("");
                    setDraftModalStep(1);
                  }}
                  disabled={draftGenerating}
                >
                  Cancel
                </Button>
                <Button
                  onClick={() => void submitDraft()}
                  disabled={!canSubmitDraft}
                >
                  <Sparkles size={15} />
                  Generate & create draft
                </Button>
              </div>
            </>
          ) : (
            <>
              <ArticleDraftProgressPanel
                events={draftProgressEvents}
                isRunning={draftGenerating}
                runKey={draftRunKey}
              />
              {draftError ? (
                <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">
                  {draftError}
                </p>
              ) : null}
              <div className="flex flex-wrap justify-end gap-3 pt-2">
                {!draftGenerating && draftError ? (
                  <>
                    {draftResumeRunId ? (
                      <Button onClick={() => void submitDraft(draftResumeRunId)}>
                        Retry from checkpoint
                      </Button>
                    ) : null}
                    <Button
                      variant="secondary"
                      onClick={() => {
                        setDraftModalStep(1);
                        setDraftError("");
                        setDraftProgressEvents([]);
                        setDraftResumeRunId("");
                      }}
                    >
                      Back to details
                    </Button>
                  </>
                ) : null}
                <Button
                  variant="secondary"
                  disabled={draftGenerating}
                  onClick={() => setDraftModalOpen(false)}
                >
                  {draftGenerating ? "Please wait…" : "Close"}
                </Button>
              </div>
            </>
          )}
        </div>
      </Modal>
    </div>
  );
}
