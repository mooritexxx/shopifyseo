import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronLeft, ChevronRight, ExternalLink, LoaderCircle, RefreshCw, Save, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { Toast, type ToastVariant } from "../components/ui/toast";
import { useAiJobStatus } from "../hooks/use-ai-job-status";
import { useAiJobStepClock } from "../hooks/use-ai-job-step-clock";
import { useAiStream } from "../hooks/use-ai-stream";
import { detectToastVariant } from "../lib/toast-utils";
import { TooltipProvider } from "../components/ui/tooltip";
import { useSidekickBinding } from "../components/sidekick/sidekick-context";
import { useStoreUrl } from "../hooks/use-store-info";
import { getJson, postJson } from "../lib/api";
import { useDashboardGscPeriodSync } from "../lib/gsc-period";
import { cleanSeoTitle } from "../lib/utils";
import { actionSchema, contentDetailSchema, statusSchema } from "../types/api";

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

/** Shopify Admin ``image`` on the collection (stored as ``image_json``). */
function parseCollectionFeaturedImage(current: Record<string, unknown>): { url: string; alt: string } | null {
  const raw = current.image_json;
  if (typeof raw !== "string" || !raw.trim()) return null;
  try {
    const obj = JSON.parse(raw) as { url?: string; altText?: string; alt?: string };
    const url = String(obj.url ?? "").trim();
    if (!/^https?:\/\//i.test(url)) return null;
    const alt = String(obj.altText ?? obj.alt ?? "").trim() || "Collection image";
    return { url, alt };
  } catch {
    return null;
  }
}

/** First Shopify CDN ``<img>`` in collection description when no featured image is set. */
function firstInlineImageFromDescriptionHtml(html: string): { url: string; alt: string } | null {
  if (!html || !/<img/i.test(html)) return null;
  const srcMatch = html.match(/<img[^>]+src\s*=\s*["']([^"']+)["'][^>]*>/i);
  const url = (srcMatch?.[1] ?? "").trim();
  if (!/^https?:\/\//i.test(url)) return null;
  const altMatch = html.match(/<img[^>]+alt\s*=\s*["']([^"']*)["'][^>]*>/i);
  const alt = (altMatch?.[1] ?? "").trim() || "Image in collection description";
  return { url, alt };
}

export function ContentDetailPage({ kind }: { kind: "collections" | "pages" }) {
  const { handle = "" } = useParams();
  const queryClient = useQueryClient();
  const gscPeriod = useDashboardGscPeriodSync();
  const storeUrl = useStoreUrl();
  const [modalOpen, setModalOpen] = useState(false);
  const [fieldModalOpen, setFieldModalOpen] = useState(false);
  const [activeFieldRegeneration, setActiveFieldRegeneration] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [aiStartedAt, setAiStartedAt] = useState<number | null>(null);
  const [fieldStartedAt, setFieldStartedAt] = useState<number | null>(null);
  const [aiJobId, setAiJobId] = useState("");
  const aiStream = useAiStream(aiJobId);
  const [fieldJobId, setFieldJobId] = useState("");
  const [elapsedNow, setElapsedNow] = useState(() => Date.now());
  const [savedDraftBaseline, setSavedDraftBaseline] = useState(emptyDraft);
  const [collectionGallerySelected, setCollectionGallerySelected] = useState(0);
  const [collectionGalleryPreviewOpen, setCollectionGalleryPreviewOpen] = useState(false);
  const aiStatusQuery = useAiJobStatus(aiJobId);
  const fieldStatusQuery = useAiJobStatus(fieldJobId);
  const detailQuery = useQuery({
    queryKey: [kind, handle, gscPeriod],
    queryFn: () =>
      getJson(`/api/${kind}/${encodeURIComponent(handle)}?gsc_period=${gscPeriod}`, contentDetailSchema),
    staleTime: 0,
    structuralSharing: false
  });
  const [draft, setDraft] = useState(emptyDraft);

  const collectionGalleryImages = useMemo(() => {
    if (kind !== "collections" || !detailQuery.data) return [];
    const current = detailQuery.data.current as Record<string, unknown>;
    const out: { url: string; alt: string; key: string }[] = [];
    const featured = parseCollectionFeaturedImage(current);
    if (featured) out.push({ ...featured, key: "featured" });
    const inline = firstInlineImageFromDescriptionHtml(String(current.description_html ?? ""));
    if (inline && (!featured || inline.url !== featured.url)) {
      out.push({ ...inline, key: "inline" });
    }
    return out;
  }, [kind, detailQuery.data]);

  useEffect(() => {
    setCollectionGallerySelected(0);
  }, [handle, kind]);

  useEffect(() => {
    if (collectionGalleryImages.length > 0 && collectionGallerySelected >= collectionGalleryImages.length) {
      setCollectionGallerySelected(0);
    }
  }, [collectionGalleryImages.length, collectionGallerySelected]);

  useEffect(() => {
    if (!collectionGalleryPreviewOpen || collectionGalleryImages.length <= 1) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        setCollectionGallerySelected((s) => (s - 1 + collectionGalleryImages.length) % collectionGalleryImages.length);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setCollectionGallerySelected((s) => (s + 1) % collectionGalleryImages.length);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [collectionGalleryPreviewOpen, collectionGalleryImages.length]);

  /** Only reset draft from server when navigating to another page/collection — not on every detail refetch (avoids wiping AI-filled draft). */
  const lastHydratedContentKeyRef = useRef<string | null>(null);

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
    resourceType: kind === "collections" ? "collection" : "page",
    handle,
    draftRef: assistantDraftRef,
    applyUpdates: applyAssistantUpdates
  });

  const saveMutation = useMutation({
    mutationFn: (payload: typeof emptyDraft) =>
      postJson(`/api/${kind}/${encodeURIComponent(handle)}/update?gsc_period=${gscPeriod}`, actionSchema, payload),
    onSuccess: (data, variables) => {
      setToast(data.message);
      // Trust the payload we just saved — API `result.draft` can lag SQLite/Shopify sync and would show stale text.
      setSavedDraftBaseline(variables);
      setDraft(variables);
      if (data.result && typeof data.result === "object") {
        queryClient.setQueryData([kind, handle, gscPeriod], { ...data.result, draft: variables });
      } else {
        void queryClient.invalidateQueries({ queryKey: [kind, handle, gscPeriod] });
      }
    },
    onError: (error) => setToast((error as Error).message)
  });
  const refreshMutation = useMutation({
    mutationFn: (step?: string) =>
      postJson(`/api/${kind}/${encodeURIComponent(handle)}/refresh?gsc_period=${gscPeriod}`, actionSchema, { step }),
    onMutate: () => {
      setToast(null);
    },
    onSuccess: (data, step) => {
      if (step && step !== "index") {
        setToast(data.message);
      }
      void queryClient.invalidateQueries({ queryKey: [kind, handle, gscPeriod] });
    },
    onError: (error) => setToast((error as Error).message)
  });
  const inspectionLinkMutation = useMutation({
    mutationFn: () =>
      postJson(`/api/${kind}/${encodeURIComponent(handle)}/inspection-link`, z.object({ href: z.string() })),
    onSuccess: (data) => {
      window.open(data.href, "_blank", "noopener,noreferrer");
    },
    onError: (error) => setToast((error as Error).message)
  });
  const aiMutation = useMutation({
    mutationFn: () => postJson(`/api/${kind}/${encodeURIComponent(handle)}/generate-ai`, actionSchema),
    onSuccess: (data) => {
      const jobId = typeof data.state?.job_id === "string" ? data.state.job_id : "";
      setAiJobId(jobId);
      setModalOpen(false); // Reset modal state on new generation
    },
    onError: (error) => {
      setToast((error as Error).message);
      setModalOpen(true); // Open modal for mutation errors
    }
  });

  const fieldRegenMutation = useMutation({
    mutationFn: ({ field, accepted_fields }: { field: string; accepted_fields: Record<string, string> }) =>
      postJson(`/api/${kind}/${encodeURIComponent(handle)}/regenerate-field/start`, actionSchema, {
        field,
        accepted_fields
      }),
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
  const stopAiMutation = useMutation({
    mutationFn: (jobId: string) => postJson("/api/ai-stop", statusSchema, { job_id: jobId }),
    onSuccess: async () => {
      setToast("AI stop requested");
      if (aiJobId) {
        await aiStatusQuery.refetch();
      }
      if (fieldJobId) {
        await fieldStatusQuery.refetch();
      }
    },
    onError: (error) => setToast((error as Error).message)
  });

  useEffect(() => {
    if (!detailQuery.data) return;
    const contentKey = `${kind}:${handle}`;
    if (lastHydratedContentKeyRef.current === contentKey) return;
    const cleaned = { ...detailQuery.data.draft, seo_title: cleanSeoTitle(detailQuery.data.draft.seo_title) };
    setDraft(cleaned);
    setSavedDraftBaseline(cleaned);
    lastHydratedContentKeyRef.current = contentKey;
  }, [detailQuery.data, handle, kind]);

  // Apply AI-generated fields as they arrive via SSE stream
  const appliedFieldCountRef = useRef(0);
  useEffect(() => {
    if (!aiStream.fields.length) return;
    const newFields = aiStream.fields.slice(appliedFieldCountRef.current);
    if (!newFields.length) return;
    appliedFieldCountRef.current = aiStream.fields.length;

    setDraft((current) => {
      const updates: Partial<typeof emptyDraft> = {};
      let hasUpdates = false;

      for (const { field, value: rawValue } of newFields) {
        const value = field === "seo_title" ? cleanSeoTitle(rawValue) : rawValue;
        if (!value.trim()) continue;
        switch (field) {
          case "seo_title":
            if (value !== current.seo_title) {
              updates.seo_title = value;
              hasUpdates = true;
            }
            break;
          case "seo_description":
            if (value !== current.seo_description) {
              updates.seo_description = value;
              hasUpdates = true;
            }
            break;
          case "body":
            if (value !== current.body_html) {
              updates.body_html = value;
              hasUpdates = true;
            }
            break;
        }
      }

      return hasUpdates ? { ...current, ...updates } : current;
    });
  }, [aiStream.fields]);

  // Show toast when SSE stream completes
  useEffect(() => {
    if (aiStream.done && !aiStream.error && appliedFieldCountRef.current > 0) {
      setToast("AI fields applied — review and save when ready");
    }
  }, [aiStream.done, aiStream.error]);

  // Reset applied count when job changes
  useEffect(() => {
    appliedFieldCountRef.current = 0;
  }, [aiJobId]);

  // Invalidate detail query when generation completes
  useEffect(() => {
    if (aiStream.done && !aiStream.error) {
      void queryClient.invalidateQueries({ queryKey: [kind, handle, gscPeriod] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    }
  }, [aiStream.done, aiStream.error, kind, handle, gscPeriod, queryClient]);

  // Auto-open error modal when status query detects an error
  useEffect(() => {
    const status = aiStatusQuery.data;
    if (status?.last_error && !status.running && !aiMutation.isPending) {
      setModalOpen(true);
    }
  }, [aiStatusQuery.data?.last_error, aiStatusQuery.data?.running, aiMutation.isPending]);

  // Timer for full AI generation - updates elapsedNow every second
  useEffect(() => {
    const running = aiStatusQuery.data?.running === true;
    const starting = aiMutation.isPending;
    if (!aiStartedAt || (!running && !starting)) return undefined;
    const intervalId = window.setInterval(() => setElapsedNow(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, [aiStartedAt, aiStatusQuery.data?.running, aiMutation.isPending]);

  // Track when AI generation starts
  useEffect(() => {
    if (aiMutation.isPending && !aiStartedAt) {
      const startedAt = Date.now();
      setAiStartedAt(startedAt);
      setElapsedNow(startedAt);
      return;
    }

    if (!aiStatusQuery.data?.running && !aiMutation.isPending && aiStartedAt) {
      setElapsedNow(Date.now());
    }
  }, [aiMutation.isPending, aiStartedAt, aiStatusQuery.data?.running]);

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

  const activeFieldStatus = fieldStatusQuery.data && fieldStatusQuery.data.mode === "field_regeneration" && fieldStatusQuery.data.handle === handle
    ? fieldStatusQuery.data
    : null;
  const activeFieldResult = activeFieldStatus?.running
    ? null
    : (activeFieldStatus?.last_result && activeFieldStatus.last_result.field === activeFieldRegeneration ? activeFieldStatus.last_result : null);

  const mainAiStepStartedAtMs = useAiJobStepClock(
    Boolean(aiStatusQuery.data?.running),
    aiStatusQuery.data?.step_index ?? 0,
    aiStatusQuery.data?.stage ?? ""
  );
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

  function startAiGeneration() {
    const startedAt = Date.now();
    setAiStartedAt(startedAt);
    setElapsedNow(startedAt);
    setModalOpen(false);
    aiMutation.mutate();
  }

  const detail = detailQuery.data;
  if (detailQuery.isLoading) {
    return <DetailPageSkeleton />;
  }
  if (detailQuery.error || !detail) {
    return <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">{(detailQuery.error as Error)?.message || "Could not load detail."}</div>;
  }
  const recommendation = detail.recommendation.details;
  const isDirty = draftChanged(draft, savedDraftBaseline);
  const previewUrl = storeUrl ? `${storeUrl}/${kind}/${handle}` : null;

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
  const formatSegmentValue = (value: string) => {
    const raw = (value || "").trim();
    if (!raw) return "Unknown";
    if (raw.length <= 3) return raw.toUpperCase();
    return raw;
  };
  const signalCards = (() => {
    type DetailSignalCard = (typeof detail.signal_cards)[number];
    const cards: DetailSignalCard[] = [...detail.signal_cards];
    const segmentSummary = detail.gsc_segment_summary;
    const segmentFetchedAt = segmentSummary?.fetched_at ?? null;
    const deviceMix = segmentSummary?.device_mix ?? [];
    const topCountry = segmentSummary?.top_countries?.[0];
    const desktopSegment = deviceMix.find(
      (segment) => String(segment.segment || "").trim().toLowerCase() === "desktop"
    );
    const mobileSegment = deviceMix.find(
      (segment) => String(segment.segment || "").trim().toLowerCase() === "mobile"
    );
    const opportunityCard: DetailSignalCard = {
      label: "Opportunity score",
      value: String(detail.opportunity.score),
      sublabel: String(detail.opportunity.priority ?? ""),
      updated_at: null,
      step: "opportunity",
      action_label: null,
      action_href: null
    };
    const segmentCards: DetailSignalCard[] = [];
    if (desktopSegment) {
      segmentCards.push({
        label: "Desktop",
        value: formatSegmentValue(desktopSegment.segment),
        sublabel: `${desktopSegment.impressions.toLocaleString()} impressions · ${(desktopSegment.share * 100).toFixed(1)}%`,
        updated_at: segmentFetchedAt,
        step: "segment_desktop",
        action_label: null,
        action_href: null
      });
    }
    if (mobileSegment) {
      segmentCards.push({
        label: "Mobile",
        value: formatSegmentValue(mobileSegment.segment),
        sublabel: `${mobileSegment.impressions.toLocaleString()} impressions · ${(mobileSegment.share * 100).toFixed(1)}%`,
        updated_at: segmentFetchedAt,
        step: "segment_mobile",
        action_label: null,
        action_href: null
      });
    }
    if (topCountry) {
      segmentCards.push({
        label: "Top country",
        value: formatSegmentValue(topCountry.segment),
        sublabel: `${topCountry.impressions.toLocaleString()} impressions · ${(topCountry.share * 100).toFixed(1)}%`,
        updated_at: segmentFetchedAt,
        step: "segment_country",
        action_label: null,
        action_href: null
      });
    }
    const speedIndex = cards.findIndex((signal) => signal.step === "speed");
    if (speedIndex >= 0) {
      cards.splice(speedIndex + 1, 0, opportunityCard, ...segmentCards);
      return cards;
    }
    return [...cards, opportunityCard, ...segmentCards];
  })();

  // AI generation status toast (shows elapsed time for current step only)
  const aiGenerationToast = (() => {
    const status = aiStatusQuery.data;

    if (aiMutation.isPending && !status?.running) {
      const totalMs = aiStartedAt ? elapsedNow - aiStartedAt : 0;
      return {
        message: (
          <AiRunningToastBody
            headline="Starting AI generation…"
            stepElapsedMs={totalMs}
          />
        ),
        variant: "info" as ToastVariant,
        duration: 0
      };
    }
    if (status?.running) {
      const stepLabel = status.stage_label || humanizeAiStage(status.stage || "");
      const stepInfo = status.step_total
        ? `Step ${Math.max(status.step_index || 0, 0)}/${status.step_total}: ${stepLabel}`
        : stepLabel;
      return {
        message: (
          <AiRunningToastBody
            headline={stepInfo}
            stepElapsedMs={elapsedNow - mainAiStepStartedAtMs}
          />
        ),
        variant: "info" as ToastVariant,
        duration: 0,
        isRunning: true
      };
    }
    if (aiJobId && !status?.running && !aiMutation.isPending) {
      if (status?.last_error) {
        return {
          message: `AI generation failed: ${status.last_error}`,
          variant: "error" as ToastVariant,
          duration: 5000
        };
      }
      return { message: "AI generation complete", variant: "success" as ToastVariant, duration: 3000 };
    }
    return null;
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
        {aiGenerationToast ? (
          <Toast
            variant={aiGenerationToast.variant}
            duration={aiGenerationToast.duration}
            customIcon={aiGenerationToast.isRunning ? <LoaderCircle className="animate-spin" size={18} /> : undefined}
          >
            {aiGenerationToast.message}
          </Toast>
        ) : null}
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
          <Link to={`/${kind}`} className="inline-flex items-center gap-2 text-sm font-medium text-slate-600 transition hover:text-ink">
            <ArrowLeft size={16} />
            {kind === "collections" ? "Collections" : "Pages"}
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

        <section className="space-y-0">
          <Card className="border-[#e2eaf4] bg-[linear-gradient(180deg,#ffffff_0%,#fbfdff_100%)]">
            <CardHeader className="pb-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Main fields</p>
                  <h2 className="mt-2 text-2xl font-bold text-ink">{kind === "collections" ? "Collection" : "Page"} details</h2>
                </div>
                <div className="flex flex-wrap gap-3">
                  {previewUrl ? (
                    <Button
                      variant="secondary"
                      onClick={() => window.open(previewUrl, "_blank", "noopener,noreferrer")}
                    >
                      <ExternalLink className="mr-2" size={16} />
                      Preview
                    </Button>
                  ) : null}
                  <Button variant="secondary" onClick={startAiGeneration} disabled={aiMutation.isPending}>
                    <Sparkles className="mr-2" size={16} />
                    {aiMutation.isPending ? "Starting…" : "Generate AI"}
                  </Button>
                  <Button onClick={() => saveMutation.mutate(draft)} disabled={!isDirty || saveMutation.isPending}>
                    <Save className="mr-2" size={16} />
                    {saveMutation.isPending ? "Saving…" : "Save to Shopify"}
                  </Button>
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-6 pt-0">
              {kind === "collections" ? (
                <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
                  <div className="flex w-full flex-col gap-2 lg:w-[220px] lg:max-w-[240px] lg:shrink-0">
                    {collectionGalleryImages.length > 0 ? (
                      <>
                        <button
                          type="button"
                          onClick={() => setCollectionGalleryPreviewOpen(true)}
                          className="group mx-auto aspect-square w-full max-w-[240px] overflow-hidden rounded-xl border border-[#e2eaf4] bg-slate-50 text-left outline-none ring-offset-2 transition hover:border-[#b8cce4] focus-visible:ring-2 focus-visible:ring-[#2b6cb0]"
                          aria-label="Open image preview"
                        >
                          <img
                            src={collectionGalleryImages[collectionGallerySelected]?.url}
                            alt={collectionGalleryImages[collectionGallerySelected]?.alt || ""}
                            className="h-full w-full object-contain object-center transition group-hover:opacity-95"
                          />
                        </button>
                        {collectionGalleryImages.length > 1 ? (
                          <div className="mx-auto flex max-w-[240px] gap-1.5 overflow-x-auto pb-0.5">
                            {collectionGalleryImages.map((im, i) => (
                              <button
                                key={im.key}
                                type="button"
                                aria-label={`Collection image ${i + 1}`}
                                aria-current={i === collectionGallerySelected ? "true" : undefined}
                                onClick={() => setCollectionGallerySelected(i)}
                                onDoubleClick={() => {
                                  setCollectionGallerySelected(i);
                                  setCollectionGalleryPreviewOpen(true);
                                }}
                                className={`h-14 w-14 shrink-0 overflow-hidden rounded-lg border-2 transition ${
                                  i === collectionGallerySelected
                                    ? "border-[#2b6cb0] ring-1 ring-[#2b6cb0]/30"
                                    : "border-transparent opacity-80 hover:opacity-100"
                                }`}
                              >
                                <img src={im.url} alt="" className="h-full w-full object-cover" />
                              </button>
                            ))}
                          </div>
                        ) : null}
                        <p className="text-center text-[11px] text-slate-500">
                          {collectionGalleryImages.length} image{collectionGalleryImages.length === 1 ? "" : "s"} · Shopify collection
                          {collectionGalleryImages.length > 1 ? (
                            <span className="block text-[10px] text-slate-400">Double-click a thumbnail to preview</span>
                          ) : null}
                        </p>
                      </>
                    ) : (
                      <div className="mx-auto flex min-h-[160px] w-full max-w-[240px] flex-col items-center justify-center gap-1 rounded-xl border border-dashed border-[#d7e2f0] bg-[#fbfdff] px-3 py-6 text-center">
                        <p className="text-xs font-medium text-slate-600">No collection image</p>
                        <p className="text-[11px] leading-snug text-slate-400">
                          Add an image in Shopify Admin or sync from Shopify to load it here.
                        </p>
                      </div>
                    )}
                  </div>
                  <div className="min-w-0 flex-1 space-y-6">
                    <div className="grid gap-2">
                      <Label htmlFor={`${kind}-title`}>Title</Label>
                      <Input
                        id={`${kind}-title`}
                        value={draft.title}
                        onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                      />
                    </div>

                    <Separator />

                    <div className="grid gap-2">
                      <Label htmlFor={`${kind}-seo-title`}>SEO title</Label>
                      <Input
                        id={`${kind}-seo-title`}
                        value={draft.seo_title}
                        onChange={(event) => setDraft((current) => ({ ...current, seo_title: event.target.value }))}
                      />
                      <CharacterBar current={draft.seo_title.trim().length} max={65} goodMin={45} />
                      <div className="flex items-center justify-between gap-3 text-xs text-slate-500">
                        <span className={draft.seo_title.trim().length > 65 ? "text-red-500 font-medium" : ""}>
                          {draft.seo_title.trim().length}/65 characters
                        </span>
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
              ) : (
                <>
                  <div className="grid gap-2">
                    <Label htmlFor={`${kind}-title`}>Title</Label>
                    <Input
                      id={`${kind}-title`}
                      value={draft.title}
                      onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))}
                    />
                  </div>

                  <Separator />

                  <div className="grid gap-2">
                    <Label htmlFor={`${kind}-seo-title`}>SEO title</Label>
                    <Input
                      id={`${kind}-seo-title`}
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
                </>
              )}

              <div className="grid gap-2">
                <Label htmlFor={`${kind}-seo-description`}>SEO description</Label>
                <Textarea
                  id={`${kind}-seo-description`}
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
                title={draft.seo_title || draft.title || `Untitled ${kind === "collections" ? "collection" : "page"}`}
                url={previewUrl || `${storeUrl}/${kind}/${handle}`}
                description={draft.seo_description || "Your meta description preview will appear here."}
              />

              <Separator />

              <div className="grid gap-2">
                <div className="flex items-center justify-between gap-3">
                  <Label htmlFor={`${kind}-body-html`}>Body</Label>
                  <span className="flex gap-2">
                    <Button variant="ghost" size="sm" onClick={() => startFieldRegeneration("body")} disabled={fieldRegenMutation.isPending}>
                      <RefreshCw className={`mr-1 h-3 w-3 ${isRegeneratingField("body") ? "animate-spin" : ""}`} />
                      {isRegeneratingField("body") ? "Regenerating…" : "Regenerate"}
                    </Button>
                  </span>
                </div>
                <RichBodyEditor
                  id={`${kind}-body-html`}
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

        {kind === "collections" && collectionGalleryImages.length > 0 ? (
          <Modal
            open={collectionGalleryPreviewOpen}
            onOpenChange={setCollectionGalleryPreviewOpen}
            title="Image preview"
            description="Collection image from Shopify (featured or description)."
            contentClassName="w-[min(960px,96vw)] max-h-[min(920px,92vh)] overflow-y-auto"
          >
            <div className="flex flex-col items-center gap-4">
              <div className="flex w-full max-w-full items-center justify-center gap-1 sm:gap-3">
                {collectionGalleryImages.length > 1 ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="h-9 w-9 shrink-0 rounded-full"
                    aria-label="Previous image"
                    onClick={() =>
                      setCollectionGallerySelected((s) => (s - 1 + collectionGalleryImages.length) % collectionGalleryImages.length)
                    }
                  >
                    <ChevronLeft className="h-5 w-5" />
                  </Button>
                ) : null}
                <div className="flex min-h-0 min-w-0 flex-1 items-center justify-center rounded-2xl bg-slate-50 p-2">
                  <img
                    src={collectionGalleryImages[collectionGallerySelected]?.url}
                    alt={collectionGalleryImages[collectionGallerySelected]?.alt || ""}
                    className="max-h-[min(72vh,720px)] w-full max-w-full object-contain"
                  />
                </div>
                {collectionGalleryImages.length > 1 ? (
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="h-9 w-9 shrink-0 rounded-full"
                    aria-label="Next image"
                    onClick={() => setCollectionGallerySelected((s) => (s + 1) % collectionGalleryImages.length)}
                  >
                    <ChevronRight className="h-5 w-5" />
                  </Button>
                ) : null}
              </div>
              {collectionGalleryImages[collectionGallerySelected]?.alt ? (
                <p className="max-w-full text-center text-sm text-slate-600">{collectionGalleryImages[collectionGallerySelected].alt}</p>
              ) : null}
              {collectionGalleryImages.length > 1 ? (
                <p className="text-xs text-slate-500">
                  {collectionGallerySelected + 1} of {collectionGalleryImages.length}
                  <span className="ml-2 text-slate-400">Use arrow keys when this dialog is open</span>
                </p>
              ) : null}
            </div>
          </Modal>
        ) : null}

        <GscTopQueriesSection queries={detail.gsc_queries} gscPeriod={gscPeriod} />

        <GscSearchSegmentsSection summary={detail.gsc_segment_summary} />

      {/* Error modal - only show for actual errors */}
      <Modal
        open={modalOpen && Boolean(aiStatusQuery.data?.last_error && !aiStatusQuery.data?.running)}
        onOpenChange={setModalOpen}
        title="AI generation error"
        description="An error occurred during AI generation."
      >
        <div className="space-y-4">
          <div className="rounded-2xl border border-[#ffd2c5] bg-[#fff4ef] px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#991b1b]">Error</p>
            <p className="mt-1.5 text-sm text-[#8f3e20]">{aiStatusQuery.data?.last_error || "An unknown error occurred"}</p>
          </div>
          <p className="text-xs text-slate-500">
            The generation process encountered an error and could not complete. Please try again.
          </p>
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
