import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, Eye, EyeOff, LoaderCircle, RefreshCw, Save, Sparkles } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { Button } from "../components/ui/button";
import { Card, CardContent, CardHeader } from "../components/ui/card";
import { CharacterBar } from "../components/ui/character-bar";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Modal } from "../components/ui/modal";
import { SearchPreview } from "../components/ui/search-preview";
import { Separator } from "../components/ui/separator";
import { GscSearchSegmentsSection } from "../components/gsc-search-segments-section";
import { GscTopQueriesSection } from "../components/gsc-top-queries-section";
import { SignalCard } from "../components/ui/signal-card";
import { DetailPageSkeleton } from "../components/ui/detail-skeleton";
import { RichBodyEditor } from "../components/ui/rich-body-editor";
import { Textarea } from "../components/ui/textarea";
import { AiRunningToastBody } from "../components/ui/ai-running-toast-body";
import { ArticleDraftProgressPanel } from "../components/article-draft-progress-panel";
import { Toast, type ToastVariant } from "../components/ui/toast";
import { useAiJobStatus } from "../hooks/use-ai-job-status";
import { useAiJobStepClock } from "../hooks/use-ai-job-step-clock";
import { detectToastVariant } from "../lib/toast-utils";
import { runArticleDraftStream, type ArticleDraftProgressEvent } from "../lib/run-article-draft-stream";
import { TooltipProvider } from "../components/ui/tooltip";
import { useSidekickBinding } from "../components/sidekick/sidekick-context";
import { useStoreUrl } from "../hooks/use-store-info";
import { getJson, patchJson, postJson } from "../lib/api";
import { useDashboardGscPeriodSync } from "../lib/gsc-period";
import { cleanSeoTitle } from "../lib/utils";
import { actionSchema, contentDetailSchema, keywordCoveragePayloadSchema, messageSchema, type KeywordCoveragePayload } from "../types/api";

const emptyDraft = {
  title: "",
  seo_title: "",
  seo_description: "",
  body_html: "",
  workflow_status: "Needs fix",
  workflow_notes: ""
};

function normalizeText(value: string) {
  return value.replace(/\r\n/g, "\n").trim();
}

function draftChanged(a: typeof emptyDraft, b: typeof emptyDraft) {
  return Object.keys(a).some((key) => normalizeText(a[key as keyof typeof a]) !== normalizeText(b[key as keyof typeof b]));
}

function humanizeAiStage(value: string) {
  return value ? value.replace(/_/g, " ") : "Preparing generation";
}

function parseBlogArticleFeaturedImage(current: Record<string, unknown>): { url: string; alt: string } | null {
  const raw = current.image_json;
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const obj = JSON.parse(raw) as { url?: string; altText?: string; alt?: string };
    const url = String(obj.url ?? "").trim();
    if (!/^https?:\/\//i.test(url)) return null;
    const alt = String(obj.altText ?? obj.alt ?? "").trim() || "Featured image";
    return { url, alt };
  } catch {
    return null;
  }
}

/** First <img> in body HTML — fallback when Shopify has not set featured image on the article record. */
function firstInlineHeroFromBodyHtml(html: string): { url: string; alt: string } | null {
  if (!html || !/<img/i.test(html)) return null;
  const srcMatch = html.match(/<img[^>]+src\s*=\s*["']([^"']+)["'][^>]*>/i);
  const url = (srcMatch?.[1] ?? "").trim();
  if (!/^https?:\/\//i.test(url)) return null;
  const altMatch = html.match(/<img[^>]+alt\s*=\s*["']([^"']*)["'][^>]*>/i);
  const alt = (altMatch?.[1] ?? "").trim() || "Image in article body";
  return { url, alt };
}

function KeywordCoverageSection({ blogHandle, articleHandle }: { blogHandle: string; articleHandle: string }) {
  const coverageQuery = useQuery({
    queryKey: ["keyword-coverage", blogHandle, articleHandle],
    queryFn: () =>
      getJson(
        `/api/articles/${encodeURIComponent(blogHandle)}/${encodeURIComponent(articleHandle)}/keyword-coverage`,
        keywordCoveragePayloadSchema,
      ),
    enabled: Boolean(blogHandle && articleHandle),
  });

  const data: KeywordCoveragePayload | undefined = coverageQuery.data;
  if (coverageQuery.isLoading) return null;
  if (!data || (data.summary.total_targets === 0 && data.discovered_keywords.length === 0)) return null;

  const { summary } = data;
  const pctColor = summary.coverage_pct >= 75 ? "bg-emerald-500" : summary.coverage_pct >= 40 ? "bg-amber-500" : "bg-red-500";

  return (
    <section>
      <Card className="border-[#e2eaf4] bg-[linear-gradient(180deg,#ffffff_0%,#f9fcff_100%)]">
        <CardHeader className="px-6 pt-6 pb-0">
          <p className="text-xs uppercase tracking-[0.24em] text-slate-500">SEO Performance</p>
          <h3 className="mt-2 text-2xl font-bold text-ink">Keyword Coverage</h3>
        </CardHeader>
        <CardContent className="px-6 pb-6 pt-5 space-y-5">
          {summary.total_targets > 0 ? (
            <div>
              <div className="flex items-center gap-3">
                <div className="flex-1 h-2.5 rounded-full bg-slate-200 overflow-hidden">
                  <div className={`h-full rounded-full ${pctColor} transition-all`} style={{ width: `${summary.coverage_pct}%` }} />
                </div>
                <span className="text-sm font-bold text-slate-700">{summary.coverage_pct.toFixed(0)}%</span>
              </div>
              <p className="mt-1.5 text-xs text-slate-500">
                {summary.ranking_count} of {summary.total_targets} target keywords ranking · {summary.gap_count} gap{summary.gap_count !== 1 ? "s" : ""}
                {summary.discovered_count > 0 ? ` · ${summary.discovered_count} discovered` : ""}
              </p>
            </div>
          ) : null}

          {data.target_keywords.length > 0 ? (
            <div>
              <p className="text-sm font-semibold text-ink mb-2">Target Keywords</p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
                      <th className="pb-2 pr-3 font-medium">Keyword</th>
                      <th className="pb-2 pr-3 font-medium text-right">Clicks</th>
                      <th className="pb-2 pr-3 font-medium text-right">Impressions</th>
                      <th className="pb-2 pr-3 font-medium text-right">Position</th>
                      <th className="pb-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {data.target_keywords.map((kw) => (
                      <tr key={kw.keyword} className="hover:bg-slate-50/60">
                        <td className="py-2 pr-3">
                          <span className={kw.is_primary ? "font-semibold text-[#2e6be6]" : "text-slate-700"}>
                            {kw.keyword}
                          </span>
                          {kw.is_primary ? (
                            <span className="ml-1.5 rounded-full bg-blue-50 px-1.5 py-0.5 text-[10px] font-medium text-blue-600">
                              primary
                            </span>
                          ) : null}
                        </td>
                        <td className="py-2 pr-3 text-right text-slate-600">{kw.gsc_clicks}</td>
                        <td className="py-2 pr-3 text-right text-slate-600">{kw.gsc_impressions}</td>
                        <td className="py-2 pr-3 text-right text-slate-600">
                          {kw.gsc_position != null ? kw.gsc_position.toFixed(1) : "—"}
                        </td>
                        <td className="py-2">
                          <span
                            className={`inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ${
                              kw.status === "ranking"
                                ? "bg-emerald-50 text-emerald-700"
                                : "bg-red-50 text-red-600"
                            }`}
                          >
                            {kw.status === "ranking" ? "Ranking" : "Gap"}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}

          {data.discovered_keywords.length > 0 ? (
            <div>
              <p className="text-sm font-semibold text-ink mb-2">
                Discovered Keywords
                <span className="ml-1.5 text-xs font-normal text-slate-400">
                  (GSC queries not in your target set)
                </span>
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-slate-400 border-b border-slate-100">
                      <th className="pb-2 pr-3 font-medium">Query</th>
                      <th className="pb-2 pr-3 font-medium text-right">Clicks</th>
                      <th className="pb-2 pr-3 font-medium text-right">Impressions</th>
                      <th className="pb-2 font-medium text-right">Position</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-50">
                    {data.discovered_keywords.slice(0, 20).map((kw) => (
                      <tr key={kw.query} className="hover:bg-slate-50/60">
                        <td className="py-2 pr-3 text-slate-700">{kw.query}</td>
                        <td className="py-2 pr-3 text-right text-slate-600">{kw.clicks}</td>
                        <td className="py-2 pr-3 text-right text-slate-600">{kw.impressions}</td>
                        <td className="py-2 text-right text-slate-600">
                          {kw.position != null ? kw.position.toFixed(1) : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {data.discovered_keywords.length > 20 ? (
                <p className="mt-2 text-xs text-slate-400">
                  Showing top 20 of {data.discovered_keywords.length} discovered queries
                </p>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </section>
  );
}

export function ArticleDetailPage() {
  const { blogHandle = "", articleHandle = "" } = useParams();
  const queryClient = useQueryClient();
  const gscPeriod = useDashboardGscPeriodSync();
  const storeUrl = useStoreUrl();
  const apiBase = `/api/articles/${encodeURIComponent(blogHandle)}/${encodeURIComponent(articleHandle)}`;
  const sidekickCompositeHandle = `${blogHandle}/${articleHandle}`;
  const detailQueryKey = ["article", blogHandle, articleHandle, gscPeriod] as const;
  const [fieldModalOpen, setFieldModalOpen] = useState(false);
  const [activeFieldRegeneration, setActiveFieldRegeneration] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [fieldStartedAt, setFieldStartedAt] = useState<number | null>(null);
  const [fieldJobId, setFieldJobId] = useState("");
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());
  const [savedDraftBaseline, setSavedDraftBaseline] = useState(emptyDraft);
  const fieldStatusQuery = useAiJobStatus(fieldJobId);
  const coverageQuery = useQuery({
    queryKey: ["keyword-coverage", blogHandle, articleHandle],
    queryFn: () =>
      getJson(
        `/api/articles/${encodeURIComponent(blogHandle)}/${encodeURIComponent(articleHandle)}/keyword-coverage`,
        keywordCoveragePayloadSchema
      ),
    enabled: Boolean(blogHandle && articleHandle)
  });
  const [regenerateModalOpen, setRegenerateModalOpen] = useState(false);
  const [regenerateModalStep, setRegenerateModalStep] = useState<1 | 2>(1);
  const [regenForm, setRegenForm] = useState({ topic: "", keywords: "", author_name: "" });
  const [regenGenerating, setRegenGenerating] = useState(false);
  const [regenProgressEvents, setRegenProgressEvents] = useState<ArticleDraftProgressEvent[]>([]);
  const [regenError, setRegenError] = useState("");
  const [regenRunKey, setRegenRunKey] = useState(0);
  const [articleImagePreviewOpen, setArticleImagePreviewOpen] = useState(false);
  const detailQuery = useQuery({
    queryKey: detailQueryKey,
    queryFn: () => getJson(`${apiBase}?gsc_period=${gscPeriod}`, contentDetailSchema),
    staleTime: 0,
    structuralSharing: false
  });
  const [draft, setDraft] = useState(emptyDraft);

  /** Only reset draft from server when navigating to another page/collection — not on every detail refetch (avoids wiping AI-filled draft). */
  const lastHydratedContentKeyRef = useRef<string | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const savedBaselineRef = useRef(savedDraftBaseline);
  savedBaselineRef.current = savedDraftBaseline;

  const assistantDraftRef = useRef<Record<string, string>>({});
  assistantDraftRef.current = {
    title: draft.title,
    seo_title: draft.seo_title,
    seo_description: draft.seo_description,
    body_html: draft.body_html
  };
  const applyAssistantUpdates = useCallback((updates: Record<string, string>) => {
    const cleaned = updates.seo_title ? { ...updates, seo_title: cleanSeoTitle(updates.seo_title) } : updates;
    setDraft((d) => ({ ...d, ...cleaned }));
  }, []);
  useSidekickBinding({
    resourceType: "blog_article",
    handle: sidekickCompositeHandle,
    draftRef: assistantDraftRef,
    applyUpdates: applyAssistantUpdates
  });

  const saveMutation = useMutation({
    mutationFn: (payload: typeof emptyDraft) =>
      postJson(`${apiBase}/update?gsc_period=${gscPeriod}`, actionSchema, payload),
    onSuccess: (data, variables) => {
      setToast(data.message);
      // Trust the payload we just saved — API `result.draft` can lag SQLite/Shopify sync and would show stale text.
      setSavedDraftBaseline(variables);
      setDraft(variables);
      if (data.result && typeof data.result === "object") {
        queryClient.setQueryData(detailQueryKey, { ...data.result, draft: variables });
      } else {
        void queryClient.invalidateQueries({ queryKey: detailQueryKey });
      }
    },
    onError: (error) => setToast((error as Error).message)
  });
  const refreshMutation = useMutation({
    mutationFn: (step?: string) => postJson(`${apiBase}/refresh?gsc_period=${gscPeriod}`, actionSchema, { step }),
    onMutate: () => {
      setToast(null);
    },
    onSuccess: (data, step) => {
      if (step && step !== "index") {
        setToast(data.message);
      }
      void queryClient.invalidateQueries({ queryKey: detailQueryKey });
    },
    onError: (error) => setToast((error as Error).message)
  });
  const inspectionLinkMutation = useMutation({
    mutationFn: () => postJson(`${apiBase}/inspection-link`, z.object({ href: z.string() })),
    onSuccess: (data) => {
      window.open(data.href, "_blank", "noopener,noreferrer");
    },
    onError: (error) => setToast((error as Error).message)
  });
  const fieldRegenMutation = useMutation({
    mutationFn: ({ field, accepted_fields }: { field: string; accepted_fields: Record<string, string> }) =>
      postJson(`${apiBase}/regenerate-field/start`, actionSchema, { field, accepted_fields }),
    onMutate: () => {
      const startedAt = Date.now();
      setFieldStartedAt(startedAt);
      setElapsedNow(startedAt);
      setToast(null);
    },
    onSuccess: async (data) => {
      const jobId = typeof data.state?.job_id === "string" ? data.state.job_id : "";
      setFieldJobId(jobId);
      setFieldModalOpen(false); // Don't open modal, use toast instead
    },
    onError: (error) => {
      setToast((error as Error).message);
      setFieldModalOpen(true); // Only open modal for errors
    },
  });
  const publishMutation = useMutation({
    mutationFn: (isPublished: boolean) =>
      patchJson(`${apiBase}/publish`, messageSchema, { is_published: isPublished }),
    onSuccess: (data) => {
      setToast(data?.message || "Status updated");
      void queryClient.invalidateQueries({ queryKey: detailQueryKey });
    },
    onError: (error) => setToast((error as Error).message),
  });

  useEffect(() => {
    if (!detailQuery.data) return;
    const incoming = detailQuery.data.draft;
    const contentKey = `article:${blogHandle}:${articleHandle}`;

    if (lastHydratedContentKeyRef.current !== contentKey) {
      lastHydratedContentKeyRef.current = contentKey;
      const cleaned = { ...incoming, seo_title: cleanSeoTitle(incoming.seo_title) };
      setDraft(cleaned);
      setSavedDraftBaseline(cleaned);
      return;
    }

    // Same article: apply refetched detail when the user has no unsaved edits (e.g. Refresh from Shopify
    // adds inline hero `<img>` that was missing from the first SQLite snapshot).
    if (draftChanged(draftRef.current, savedBaselineRef.current)) {
      return;
    }
    if (!draftChanged(draftRef.current, incoming)) {
      return;
    }
    setDraft(incoming);
    setSavedDraftBaseline(incoming);
  }, [detailQuery.data, detailQuery.dataUpdatedAt, blogHandle, articleHandle]);

  // Timer for field regeneration - updates elapsedNow every second
  useEffect(() => {
    const running = fieldStatusQuery.data?.running === true && fieldStatusQuery.data?.mode === "field_regeneration";
    const starting = fieldRegenMutation.isPending;
    const waitingForJob = Boolean(fieldJobId) && !running && !fieldRegenMutation.isPending;
    if (!fieldStartedAt || (!running && !starting && !waitingForJob)) return undefined;
    const intervalId = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, [fieldStartedAt, fieldStatusQuery.data?.running, fieldStatusQuery.data?.mode, fieldRegenMutation.isPending, fieldJobId]);

  // Auto-open error modal when field regeneration status query detects an error
  useEffect(() => {
    const status = fieldStatusQuery.data;
    if (status?.last_error && !status.running && !fieldRegenMutation.isPending && status.mode === "field_regeneration") {
      setFieldModalOpen(true);
    }
  }, [fieldStatusQuery.data?.last_error, fieldStatusQuery.data?.running, fieldStatusQuery.data?.mode, fieldRegenMutation.isPending]);

  function buildAcceptedFields(excludeField: string) {
    const fields: Record<string, string> = {};
    if (excludeField !== "seo_title") fields.seo_title = draft.seo_title;
    if (excludeField !== "seo_description") fields.seo_description = draft.seo_description;
    if (excludeField !== "body") fields.body = draft.body_html;
    return fields;
  }

  function isRegeneratingField(field: string) {
    return fieldRegenMutation.isPending && fieldRegenMutation.variables?.field === field;
  }

  function startFieldRegeneration(field: "seo_title" | "seo_description" | "body") {
    setActiveFieldRegeneration(field);
    setFieldModalOpen(false); // Don't open modal, use toast instead
    fieldRegenMutation.mutate({ field, accepted_fields: buildAcceptedFields(field) });
  }

  const activeFieldStatus = fieldStatusQuery.data && fieldStatusQuery.data.mode === "field_regeneration" && fieldStatusQuery.data.handle === sidekickCompositeHandle
    ? fieldStatusQuery.data
    : null;
  const activeFieldResult = activeFieldStatus?.running
    ? null
    : (activeFieldStatus?.last_result && activeFieldStatus.last_result.field === activeFieldRegeneration ? activeFieldStatus.last_result : null);

  const fieldAiStepStartedAtMs = useAiJobStepClock(
    Boolean(activeFieldStatus?.running),
    activeFieldStatus?.step_index ?? 0,
    activeFieldStatus?.stage ?? ""
  );

  useEffect(() => {
    if (!activeFieldResult || !activeFieldRegeneration) return;
    const fieldMap: Record<string, "seo_title" | "seo_description" | "body_html"> = {
      seo_title: "seo_title",
      seo_description: "seo_description",
      body: "body_html",
    };
    const draftKey = fieldMap[String(activeFieldResult.field)];
    const rawValue = typeof activeFieldResult.value === "string" ? activeFieldResult.value : "";
    const value = draftKey === "seo_title" ? cleanSeoTitle(rawValue) : rawValue;
    const reviewAction = typeof activeFieldResult.review_action === "string" ? activeFieldResult.review_action : "";
    if (draftKey && value) {
      setDraft((current) => ({ ...current, [draftKey]: value }));
      const actionSuffix = reviewAction && reviewAction !== "review_skipped" ? ` (${reviewAction})` : "";
      setToast(`Regenerated ${String(activeFieldResult.field).replace(/_/g, " ")}${actionSuffix}`);
    }
  }, [activeFieldResult, activeFieldRegeneration]);

  const fieldTimeline = activeFieldStatus?.steps ?? [];
  const completedFieldSteps = fieldTimeline.filter((step) => step.status === "completed").length;
  const fieldProgressPercent = fieldTimeline.length > 0
    ? Math.round((completedFieldSteps / Math.max(fieldTimeline.length, 1)) * 100)
    : (fieldRegenMutation.isPending ? 10 : 0);

  function fieldLabel(field: string | null) {
    if (!field) return "field";
    if (field === "seo_title") return "SEO title";
    if (field === "seo_description") return "SEO description";
    if (field === "body") return "body";
    return field.replace(/_/g, " ");
  }

  function defaultKeywordsFromCoverage(data: KeywordCoveragePayload | undefined): string {
    if (!data?.target_keywords?.length) return "";
    return data.target_keywords
      .map((t) => (typeof t.keyword === "string" ? t.keyword : "").trim())
      .filter(Boolean)
      .slice(0, 12)
      .join(", ");
  }

  function openRegenerateModal() {
    const cur = detailQuery.data;
    const title = (cur?.draft?.title || "").trim() || articleHandle;
    const author = String((cur?.current as Record<string, unknown>)?.author_name ?? "").trim();
    setRegenForm({
      topic: title,
      keywords: defaultKeywordsFromCoverage(coverageQuery.data),
      author_name: author
    });
    setRegenError("");
    setRegenProgressEvents([]);
    setRegenerateModalStep(1);
    setRegenerateModalOpen(true);
  }

  async function submitRegenerateDraft() {
    const cur = detailQuery.data;
    if (!cur) return;
    const blogId = String((cur.current as Record<string, unknown>).blog_shopify_id ?? "").trim();
    if (!blogId) {
      setRegenError("Missing blog id for this article. Sync blogs from Shopify and retry.");
      return;
    }
    if (!regenForm.topic.trim()) {
      setRegenError("Topic / working title is required.");
      return;
    }
    setRegenError("");
    setRegenRunKey((k) => k + 1);
    setRegenProgressEvents([]);
    setRegenerateModalStep(2);
    setRegenGenerating(true);
    try {
      const keywords = regenForm.keywords
        ? regenForm.keywords
            .split(",")
            .map((k) => k.trim())
            .filter(Boolean)
        : [];
      await runArticleDraftStream(
        {
          blog_id: blogId,
          blog_handle: blogHandle,
          topic: regenForm.topic.trim(),
          keywords,
          author_name: regenForm.author_name.trim(),
          slug_hint: articleHandle,
          regenerate_article_handle: articleHandle
        },
        (evt) => setRegenProgressEvents((prev) => [...prev, evt])
      );
      setRegenerateModalOpen(false);
      setRegenerateModalStep(1);
      setRegenProgressEvents([]);
      setToast("Article regenerated — draft engine updated Shopify.");
      void queryClient.invalidateQueries({ queryKey: detailQueryKey });
      void queryClient.invalidateQueries({ queryKey: ["all-articles"] });
      void queryClient.invalidateQueries({ queryKey: ["keyword-coverage", blogHandle, articleHandle] });
    } catch (err) {
      setRegenError((err as Error).message || "Regeneration failed");
    } finally {
      setRegenGenerating(false);
    }
  }

  const detail = detailQuery.data;
  if (detailQuery.isLoading) {
    return <DetailPageSkeleton showFooter={false} />;
  }
  if (detailQuery.error || !detail) {
    return <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">{(detailQuery.error as Error)?.message || "Could not load detail."}</div>;
  }
  const featuredImage = parseBlogArticleFeaturedImage(detail.current as Record<string, unknown>);
  const inlineBodyHero = firstInlineHeroFromBodyHtml(draft.body_html);
  const heroPreview = featuredImage ?? inlineBodyHero;
  const heroPreviewSource: "shopify_featured" | "body_html" | null = featuredImage
    ? "shopify_featured"
    : inlineBodyHero
      ? "body_html"
      : null;
  const isDirty = draftChanged(draft, savedDraftBaseline);
  const previewUrl =
    storeUrl && blogHandle && articleHandle
      ? `${storeUrl}/blogs/${encodeURIComponent(blogHandle)}/${encodeURIComponent(articleHandle)}`
      : null;
  const refreshingStep = refreshMutation.isPending ? refreshMutation.variables : null;
  const isSignalStepRefreshing = (step: string) => {
    if (!refreshMutation.isPending || refreshingStep === undefined || refreshingStep === null) {
      return false;
    }
    if (step.startsWith("gsc_")) {
      return String(refreshingStep).startsWith("gsc_");
    }
    return refreshingStep === step;
  };
  const signalCards = (() => {
    const cards = [...detail.signal_cards];
    const opportunityCard = {
      label: "Opportunity score",
      value: String(detail.opportunity.score),
      sublabel: detail.opportunity.priority,
      updated_at: null,
      step: "opportunity",
      action_label: null,
      action_href: null
    };
    const speedIndex = cards.findIndex((signal) => signal.step === "speed");
    if (speedIndex >= 0) {
      let insertAt = speedIndex + 1;
      if (cards[insertAt]?.step === "speed_desktop") {
        insertAt += 1;
      }
      cards.splice(insertAt, 0, opportunityCard);
      return cards;
    }
    return [...cards, opportunityCard];
  })();

  // Field regeneration status toast
  const fieldRegenToast = (() => {
    if (fieldRegenMutation.isPending && !activeFieldStatus?.running) {
      const fieldTotalMs = fieldStartedAt ? elapsedNow - fieldStartedAt : 0;
      return {
        message: (
          <AiRunningToastBody
            headline={`Starting ${fieldLabel(activeFieldRegeneration)} regeneration…`}
            stepElapsedMs={fieldTotalMs}
          />
        ),
        variant: "info" as ToastVariant,
        duration: 0
      };
    }
    if (activeFieldStatus?.running) {
      const stepLabel = activeFieldStatus.stage_label || "Preparing regeneration…";
      return {
        message: (
          <AiRunningToastBody
            headline={stepLabel}
            stepElapsedMs={elapsedNow - fieldAiStepStartedAtMs}
          />
        ),
        variant: "info" as ToastVariant,
        duration: 0,
        isRunning: true
      };
    }
    if (fieldJobId && !activeFieldStatus?.running && !fieldRegenMutation.isPending) {
      if (activeFieldStatus?.last_error && !activeFieldResult) {
        return {
          message: `${fieldLabel(activeFieldRegeneration)} regeneration failed: ${activeFieldStatus.last_error}`,
          variant: "error" as ToastVariant,
          duration: 5000
        };
      }
      if (activeFieldResult) {
        return { message: `${fieldLabel(activeFieldRegeneration)} regeneration complete`, variant: "success" as ToastVariant, duration: 3000 };
      }
    }
    return null;
  })();

  return (
    <TooltipProvider>
      <div className="space-y-6 pb-10">
        {toast ? <Toast variant={detectToastVariant(toast)}>{toast}</Toast> : null}
        {fieldRegenToast ? (
          <Toast
            variant={fieldRegenToast.variant}
            duration={fieldRegenToast.duration}
            customIcon={fieldRegenToast.isRunning ? <LoaderCircle className="animate-spin" size={18} /> : undefined}
          >
            {fieldRegenToast.message}
          </Toast>
        ) : null}

        <div>
          <Link to="/articles" className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 transition hover:text-ink">
            <ArrowLeft size={16} />
            Articles
          </Link>
        </div>

        <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-5">
          {signalCards.map((signal) => (
            <SignalCard
              key={signal.step}
              signal={signal}
              onRefresh={signal.step !== "opportunity" ? () => refreshMutation.mutate(signal.step) : undefined}
              isRefreshing={isSignalStepRefreshing(signal.step)}
              actionLabel={signal.step === "index" && signal.action_label ? (inspectionLinkMutation.isPending ? "Opening…" : signal.action_label) : undefined}
              onAction={signal.step === "index" && signal.action_label ? () => inspectionLinkMutation.mutate() : undefined}
            />
          ))}
        </section>

        <GscTopQueriesSection queries={detail.gsc_queries} gscPeriod={gscPeriod} />

        <GscSearchSegmentsSection summary={detail.gsc_segment_summary} />

        <section className="space-y-0">
          <Card className="border-[#e2eaf4] bg-[linear-gradient(180deg,#ffffff_0%,#fbfdff_100%)]">
            <CardHeader className="pb-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Main fields</p>
                  <h2 className="mt-2 text-2xl font-bold text-ink">Article details</h2>
                </div>
                <div className="flex flex-wrap gap-3">
                  {(() => {
                    const isPublished = Boolean((detail.current as Record<string, unknown>)?.is_published);
                    return (
                      <Button
                        variant="outline"
                        disabled={publishMutation.isPending}
                        onClick={() => publishMutation.mutate(!isPublished)}
                      >
                        {isPublished ? <EyeOff className="mr-2" size={16} /> : <Eye className="mr-2" size={16} />}
                        {publishMutation.isPending
                          ? "Updating…"
                          : isPublished
                            ? "Unpublish"
                            : "Publish"}
                      </Button>
                    );
                  })()}
                  {previewUrl ? (
                    <Button
                      variant="secondary"
                      onClick={() => window.open(previewUrl, "_blank", "noopener,noreferrer")}
                    >
                      <ExternalLink className="mr-2" size={16} />
                      Preview
                    </Button>
                  ) : null}
                  <Button variant="secondary" onClick={openRegenerateModal} disabled={regenGenerating}>
                    <Sparkles className="mr-2" size={16} />
                    Regenerate article
                  </Button>
                  <Button onClick={() => saveMutation.mutate(draft)} disabled={!isDirty || saveMutation.isPending}>
                    <Save className="mr-2" size={16} />
                    {saveMutation.isPending ? "Saving…" : "Save to Shopify"}
                  </Button>
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-6 pt-0">
              <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
                <div className="flex w-full flex-col gap-2 lg:w-[220px] lg:max-w-[240px] lg:shrink-0">
                  {heroPreview && heroPreviewSource ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setArticleImagePreviewOpen(true)}
                        className="group mx-auto aspect-square w-full max-w-[240px] overflow-hidden rounded-xl border border-[#e2eaf4] bg-slate-50 text-left outline-none ring-offset-2 transition hover:border-[#b8cce4] focus-visible:ring-2 focus-visible:ring-[#2b6cb0]"
                        aria-label="Open image preview"
                      >
                        <img
                          src={heroPreview.url}
                          alt={heroPreview.alt}
                          className="h-full w-full object-contain object-center transition group-hover:opacity-95"
                          loading="lazy"
                        />
                      </button>
                      <p className="text-center text-[11px] text-slate-500">
                        {heroPreviewSource === "shopify_featured"
                          ? "Featured image · Shopify"
                          : "Hero image · article body"}
                        <span className="mt-0.5 block text-[10px] text-slate-400">
                          {heroPreviewSource === "shopify_featured"
                            ? "Sync from Shopify to refresh."
                            : "Set a featured image in Shopify for cards and social previews."}
                        </span>
                      </p>
                    </>
                  ) : (
                    <div className="mx-auto flex min-h-[160px] w-full max-w-[240px] flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-[#d7e2f0] bg-[#fbfdff] px-3 py-6 text-center">
                      <p className="text-xs font-medium text-slate-600">No article image</p>
                      <p className="text-[11px] leading-snug text-slate-400">
                        Set a featured image in Shopify Admin or add an image in the body, then sync from Shopify.
                      </p>
                    </div>
                  )}
                </div>

                <div className="min-w-0 flex-1 space-y-6">
                  <div className="grid gap-2">
                    <Label htmlFor="article-title">Title</Label>
                    <Input
                      id="article-title"
                      value={draft.title}
                      onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                    />
                  </div>

                  <Separator />

                  <div className="grid gap-2">
                    <Label htmlFor="article-seo-title">SEO title</Label>
                    <Input
                      id="article-seo-title"
                      value={draft.seo_title}
                      onChange={(event) => setDraft((current) => ({ ...current, seo_title: event.target.value }))}
                    />
                    <CharacterBar current={draft.seo_title.trim().length} max={65} goodMin={45} />
                    <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
                      <span className={draft.seo_title.trim().length > 65 ? "text-red-500 font-medium" : ""}>{draft.seo_title.trim().length}/65 characters</span>
                      <span className="flex gap-2">
                        <Button variant="ghost" size="sm" onClick={() => startFieldRegeneration("seo_title")} disabled={fieldRegenMutation.isPending}>
                          <RefreshCw className={`mr-1 h-3 w-3 ${isRegeneratingField("seo_title") ? "animate-spin" : ""}`} />
                          {isRegeneratingField("seo_title") ? "Regenerating…" : "Regenerate"}
                        </Button>
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <div className="grid gap-2">
                <Label htmlFor="article-seo-description">SEO description</Label>
                <Textarea
                  id="article-seo-description"
                  className="min-h-[72px] resize-none"
                  value={draft.seo_description}
                  onChange={(event) => setDraft((current) => ({ ...current, seo_description: event.target.value }))}
                />
                <CharacterBar current={draft.seo_description.trim().length} max={160} goodMin={140} />
                <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
                  <span className={draft.seo_description.trim().length > 160 ? "text-red-500 font-medium" : ""}>{draft.seo_description.trim().length}/160 characters</span>
                  <span className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => startFieldRegeneration("seo_description")} disabled={fieldRegenMutation.isPending}>
                      <RefreshCw className={`mr-1 h-3 w-3 ${isRegeneratingField("seo_description") ? "animate-spin" : ""}`} />
                      {isRegeneratingField("seo_description") ? "Regenerating…" : "Regenerate"}
                    </Button>
                  </span>
                </div>
              </div>

              <Separator />

              <SearchPreview
                title={draft.seo_title || draft.title || "Untitled article"}
                url={previewUrl || storeUrl || ""}
                description={draft.seo_description || "Your meta description preview will appear here."}
              />

              <Separator />

              <div className="grid gap-2">
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor="article-body-html">Body</Label>
                  <span className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => startFieldRegeneration("body")} disabled={fieldRegenMutation.isPending}>
                      <RefreshCw className={`mr-1 h-3 w-3 ${isRegeneratingField("body") ? "animate-spin" : ""}`} />
                      {isRegeneratingField("body") ? "Regenerating…" : "Regenerate"}
                    </Button>
                  </span>
                </div>
                <RichBodyEditor
                  id="article-body-html"
                  value={draft.body_html}
                  onChange={(html) => setDraft((current) => ({ ...current, body_html: html }))}
                  placeholder="Write your page or collection content…"
                  disabled={fieldRegenMutation.isPending && isRegeneratingField("body")}
                />
                <p className="text-xs text-slate-500">
                  Rich text is saved as HTML for Shopify (same as the admin editor). Use <span className="font-semibold text-ink">Save to Shopify</span> when
                  you’re done.
                </p>
              </div>

              <Separator />
            </CardContent>
          </Card>
        </section>
        <KeywordCoverageSection blogHandle={blogHandle} articleHandle={articleHandle} />

        <section>
          <Card className="border-[#e2eaf4] bg-[linear-gradient(180deg,#ffffff_0%,#f9fcff_100%)]">
            <CardHeader className="px-6 pt-6 pb-0">
              <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Related data</p>
              <h3 className="mt-2 text-2xl font-bold text-ink">Related items</h3>
            </CardHeader>

            <CardContent className="px-6 pb-6 pt-5">
              <div className="space-y-3">
                <p className="text-sm font-semibold text-ink">Related items</p>
                <div className="flex flex-wrap gap-2">
                  {detail.related_items.length > 0 ? detail.related_items.map((item, index) => (
                    <Button
                      key={`${item.handle || item.title}-${index}`}
                      asChild
                      variant="outline"
                      size="sm"
                      className="h-auto rounded-full border-[#d7e2f0] bg-[linear-gradient(180deg,#fbfdff_0%,#f2f7ff_100%)] px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-700 hover:border-[#bfd1eb] hover:bg-[linear-gradient(180deg,#ffffff_0%,#ebf3ff_100%)]"
                    >
                      <Link to={item.type === "product" ? `/products/${item.handle || ""}` : item.type === "collection" ? `/collections/${item.handle || ""}` : `/pages/${item.handle || ""}`}>
                        {item.title}
                      </Link>
                    </Button>
                  )) : <span className="text-sm text-slate-500">No related items.</span>}
                </div>
              </div>
            </CardContent>
          </Card>
        </section>

      {heroPreview && heroPreviewSource ? (
        <Modal
          open={articleImagePreviewOpen}
          onOpenChange={setArticleImagePreviewOpen}
          title="Image preview"
          description={
            heroPreviewSource === "shopify_featured"
              ? "Featured image from the Shopify article (admin sidebar)."
              : "First image found in the article HTML body."
          }
          contentClassName="w-[min(960px,96vw)] max-h-[min(920px,92vh)] overflow-y-auto"
        >
          <div className="flex flex-col items-center gap-4">
            <div className="flex min-h-0 min-w-0 w-full flex-1 items-center justify-center rounded-2xl bg-slate-50 p-2">
              <img
                src={heroPreview.url}
                alt={heroPreview.alt}
                className="max-h-[min(72vh,720px)] w-full max-w-full object-contain"
              />
            </div>
            {heroPreview.alt ? <p className="max-w-full text-center text-sm text-slate-600">{heroPreview.alt}</p> : null}
          </div>
        </Modal>
      ) : null}

      <Modal
        open={regenerateModalOpen}
        onOpenChange={(open) => {
          if (!regenGenerating) {
            setRegenerateModalOpen(open);
            if (!open) {
              setRegenProgressEvents([]);
              setRegenerateModalStep(1);
            }
          }
        }}
        title={regenerateModalStep === 2 ? "Regenerating article" : "Regenerate article"}
        description={
          regenerateModalStep === 2
            ? "Running the full draft engine (content, SEO, images) and updating this post in Shopify. The URL handle stays the same."
            : "Same pipeline as new article drafts and idea drafts — replaces body and meta in Shopify."
        }
      >
        <div className="space-y-4">
          {regenerateModalStep === 1 ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="regen-topic">
                  Topic / working title <span className="text-slate-400 font-normal">(required)</span>
                </Label>
                <Input
                  id="regen-topic"
                  value={regenForm.topic}
                  onChange={(e) => setRegenForm((f) => ({ ...f, topic: e.target.value }))}
                  placeholder="Article topic or working title"
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="regen-keywords">Target keywords (optional, comma-separated)</Label>
                <Textarea
                  id="regen-keywords"
                  rows={2}
                  value={regenForm.keywords}
                  onChange={(e) => setRegenForm((f) => ({ ...f, keywords: e.target.value }))}
                  placeholder="keyword one, keyword two"
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="regen-author">Author name (optional)</Label>
                <Input
                  id="regen-author"
                  value={regenForm.author_name}
                  onChange={(e) => setRegenForm((f) => ({ ...f, author_name: e.target.value }))}
                  placeholder="Shown on the Shopify article"
                />
              </div>
              {regenError ? <p className="text-sm text-red-600">{regenError}</p> : null}
              <div className="flex justify-end gap-2 pt-2">
                <Button variant="outline" type="button" onClick={() => setRegenerateModalOpen(false)}>
                  Cancel
                </Button>
                <Button type="button" onClick={() => void submitRegenerateDraft()} disabled={!regenForm.topic.trim()}>
                  Start regeneration
                </Button>
              </div>
            </>
          ) : (
            <>
              <ArticleDraftProgressPanel
                events={regenProgressEvents}
                isRunning={regenGenerating}
                runKey={regenRunKey}
              />
              {regenError ? <p className="text-sm text-red-600">{regenError}</p> : null}
            </>
          )}
        </div>
      </Modal>

      {/* Field regeneration error modal - only show for actual errors */}
      <Modal
        open={fieldModalOpen && Boolean((fieldRegenMutation.isError || (activeFieldStatus?.last_error && !activeFieldResult)))}
        onOpenChange={setFieldModalOpen}
        title={`${fieldLabel(activeFieldRegeneration)} regeneration error`}
        description="An error occurred during field regeneration."
      >
        <div className="space-y-4">
          {fieldRegenMutation.isError ? (
            <div className="rounded-2xl border border-[#ffd2c5] bg-[#fff4ef] px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#991b1b]">Error</p>
              <p className="mt-1.5 text-sm text-[#8f3e20]">
                {(fieldRegenMutation.error as Error).message || "An unknown error occurred"}
              </p>
              {(fieldRegenMutation.error as Error).stack ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-[#991b1b] hover:text-[#7f1d1d]">Show details</summary>
                  <pre className="mt-2 max-h-40 overflow-auto rounded-lg bg-[#fee2e2] p-2 text-[10px] text-[#991b1b]">
                    {(fieldRegenMutation.error as Error).stack}
                  </pre>
                </details>
              ) : null}
            </div>
          ) : activeFieldStatus?.last_error ? (
            <div className="rounded-2xl border border-[#ffd2c5] bg-[#fff4ef] px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#991b1b]">Error</p>
              <p className="mt-1.5 text-sm text-[#8f3e20]">{activeFieldStatus.last_error}</p>
            </div>
          ) : null}
          <p className="text-xs text-slate-500">
            The generation process encountered an error and could not complete. Please try again.
          </p>
        </div>
      </Modal>
      </div>
    </TooltipProvider>
  );
}
