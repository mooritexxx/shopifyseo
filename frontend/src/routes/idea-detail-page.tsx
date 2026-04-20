import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  BarChart2,
  BookOpen,
  ExternalLink,
  FileText,
  Layers3,
  Sparkles,
  Tag,
  TrendingUp,
  Zap,
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
import { getJson, patchJson } from "../lib/api";
import {
  runArticleDraftStream,
  type ArticleDraftProgressEvent,
} from "../lib/run-article-draft-stream";
import { defaultDraftSlugHint } from "../lib/seo-slug";
import { useStoreInfo } from "../hooks/use-store-info";
import {
  articleIdeasPayloadSchema,
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

const emptyDraftForm = {
  blog_id: "",
  blog_handle: "",
  topic: "",
  keywords: "",
  slug: "",
  author_name: "",
  angle_label: "",
};

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

  const blogsQuery = useQuery({
    queryKey: ["blogs-shopify-ids"],
    queryFn: () => getJson("/api/blogs/shopify-ids", blogShopifyIdsSchema),
    enabled: draftModalOpen,
  });

  function openDraftModal() {
    if (!idea) return;
    authorFieldTouchedRef.current = false;
    setDraftProgressEvents([]);
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

  async function submitDraft() {
    if (!idea) return;
    setDraftError("");
    setDraftModalStep(2);
    setDraftRunKey((k) => k + 1);
    setDraftProgressEvents([]);
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
        },
        (evt) => setDraftProgressEvents((prev) => [...prev, evt]),
      );
      setDraftModalOpen(false);
      setDraftModalStep(1);
      setDraftForm(emptyDraftForm);
      setSlugTouched(false);
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
      <div className="flex items-start justify-between gap-4">
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
        <div className="shrink-0 flex items-center gap-3 pt-2">
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
          <Button onClick={openDraftModal}>
            <Sparkles size={15} />
            Draft Article
          </Button>
        </div>
      </div>

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

          {/* SERP Signals */}
          {(idea.dominant_serp_features || idea.content_format_hints) ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-6 pt-6 pb-0">
                <h3 className="text-lg font-semibold text-ink">SERP Signals</h3>
              </CardHeader>
              <CardContent className="px-6 pb-6 pt-3 space-y-2">
                {idea.dominant_serp_features ? (
                  <div>
                    <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Dominant SERP Features
                    </p>
                    <p className="mt-1 text-sm text-slate-700">
                      {idea.dominant_serp_features}
                    </p>
                  </div>
                ) : null}
                {idea.content_format_hints ? (
                  <div>
                    <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">
                      Content Format Hints
                    </p>
                    <p className="mt-1 text-sm text-slate-700">
                      {idea.content_format_hints}
                    </p>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

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

          {/* Linked cluster / collection */}
          {(idea.linked_cluster_name || idea.linked_collection_title) ? (
            <Card className="border-[#e2eaf4]">
              <CardHeader className="px-5 pt-5 pb-0">
                <h4 className="text-sm font-semibold text-ink">Related</h4>
              </CardHeader>
              <CardContent className="px-5 pb-5 pt-3">
                <div className="flex flex-wrap gap-2">
                  {idea.linked_cluster_name ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-[#c7d9f8] bg-[#f0f6ff] px-2.5 py-1 text-xs text-[#2e6be6]">
                      <BookOpen size={11} />
                      {idea.linked_cluster_name}
                    </span>
                  ) : null}
                  {idea.linked_collection_title ? (
                    <span className="inline-flex items-center gap-1.5 rounded-full border border-[#d1e8d4] bg-[#f0faf1] px-2.5 py-1 text-xs text-emerald-700">
                      <Layers3 size={11} />
                      {idea.linked_collection_title}
                    </span>
                  ) : null}
                </div>
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
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setDraftModalStep(1);
                      setDraftError("");
                      setDraftProgressEvents([]);
                    }}
                  >
                    Back to details
                  </Button>
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
