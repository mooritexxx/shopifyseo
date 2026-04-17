import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";

import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Modal } from "../components/ui/modal";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Toast } from "../components/ui/toast";
import { detectToastVariant } from "../lib/toast-utils";
import { getJson, postJson } from "../lib/api";
import {
  fingerprintAiGeneration,
  fingerprintAiImage,
  fingerprintAiReview,
  fingerprintAiSidekick,
  fingerprintAiVision,
  fingerprintDataforseo,
  fingerprintGoogleAds,
  fingerprintShopify,
  loadConnectionStore,
  persistConnectionStore,
  type ConnectionStatusStore
} from "../lib/settings-connection-storage";
import { actionSchema, settingsSchema, shopifyShopInfoSchema } from "../types/api";
import { renderSettingsTabSections, settingsTabs, type SettingsTabId } from "./settings-page-fields";

const modelsSchema = z.object({
  models: z.array(z.string())
});

const IMAGE_TEST_STEPS: { label: string; thresholdMs: number }[] = [
  { label: "Preparing request", thresholdMs: 0 },
  { label: "Sent request to server", thresholdMs: 200 },
  { label: "Waiting for provider response", thresholdMs: 450 },
  { label: "Generating image", thresholdMs: 8000 }
];

function imageTestStepStates(elapsedMs: number): { label: string; state: "done" | "active" | "pending" }[] {
  let activeIndex = 0;
  for (let i = 0; i < IMAGE_TEST_STEPS.length; i++) {
    if (elapsedMs >= IMAGE_TEST_STEPS[i].thresholdMs) activeIndex = i;
  }
  return IMAGE_TEST_STEPS.map((step, i) => ({
    label: step.label,
    state: i < activeIndex ? "done" : i === activeIndex ? "active" : "pending"
  }));
}

function formatTestElapsed(ms: number): string {
  if (ms < 60000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const [toast, setToast] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<SettingsTabId>(() => {
    const tab = searchParams.get("tab");
    return tab && settingsTabs.some((t) => t.id === tab) ? (tab as SettingsTabId) : "integrations";
  });
  const [testModalOpen, setTestModalOpen] = useState(false);
  const [testTarget, setTestTarget] = useState<"generation" | "review" | "sidekick" | "image" | "vision">("generation");
  const [openRouterModels, setOpenRouterModels] = useState<string[]>([]);
  const [dfsStatus, setDfsStatus] = useState<"idle" | "checking" | "ok" | "error">("idle");
  const [dfsDetail, setDfsDetail] = useState("");
  const [googleAdsStatus, setGoogleAdsStatus] = useState<"idle" | "checking" | "ok" | "error">("idle");
  const [googleAdsDetail, setGoogleAdsDetail] = useState("");
  const [shopifyStatus, setShopifyStatus] = useState<"idle" | "checking" | "ok" | "error">("idle");
  const [shopifyDetail, setShopifyDetail] = useState("");
  const [gscCacheStatus, setGscCacheStatus] = useState<"idle" | "refreshing" | "ok" | "error">("idle");
  const [gscCacheDetail, setGscCacheDetail] = useState("");
  const [ga4CacheStatus, setGa4CacheStatus] = useState<"idle" | "refreshing" | "ok" | "error">("idle");
  const [ga4CacheDetail, setGa4CacheDetail] = useState("");
  const [connectionStore, setConnectionStore] = useState<ConnectionStatusStore>(() => loadConnectionStore());
  const valuesRef = useRef<Record<string, string>>({});
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: () => getJson("/api/settings", settingsSchema)
  });
  const shopifyShopInfoQuery = useQuery({
    queryKey: ["settings", "shopify-shop-info"],
    queryFn: () => getJson("/api/settings/shopify-shop-info", shopifyShopInfoSchema),
    enabled: query.data?.sync_scope_ready?.shopify === true,
    staleTime: 60_000
  });
  const [values, setValues] = useState<Record<string, string>>({});
  const [imageTestElapsedMs, setImageTestElapsedMs] = useState(0);

  useEffect(() => {
    if (!query.data) return;
    const raw = query.data.values;
    const next = { ...raw } as Record<string, string>;
    if (!(next.ai_generation_provider || "").trim()) next.ai_generation_provider = "openrouter";
    if (!(next.ai_review_provider || "").trim()) next.ai_review_provider = "openrouter";
    if (!(next.ai_image_provider || "").trim()) next.ai_image_provider = "openrouter";
    setValues(next);
  }, [query.data]);

  useEffect(() => {
    valuesRef.current = values;
  }, [values]);

  useEffect(() => {
    persistConnectionStore(connectionStore);
  }, [connectionStore]);

  useEffect(() => {
    const tab = searchParams.get("tab");
    if (tab && settingsTabs.some((t) => t.id === tab)) {
      setActiveTab(tab as SettingsTabId);
    }
  }, [searchParams]);

  const saveMutation = useMutation({
    mutationFn: () => postJson("/api/settings", actionSchema, values),
    onSuccess: (result) => {
      setToast(result.message);
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
    onError: (error) => setToast((error as Error).message)
  });
  const testMutation = useMutation({
    mutationFn: (target: "generation" | "review" | "sidekick") =>
      postJson("/api/settings/ai-test", actionSchema, { ...valuesRef.current, target }),
    onSuccess: (_data, target) => {
      setTestModalOpen(true);
      const v = valuesRef.current;
      const flow =
        target === "generation" ? "generation" : target === "sidekick" ? "sidekick" : "review";
      const fp =
        target === "generation"
          ? fingerprintAiGeneration(v)
          : target === "sidekick"
            ? fingerprintAiSidekick(v)
            : fingerprintAiReview(v);
      setConnectionStore((prev) => ({
        ...prev,
        ai: {
          ...prev.ai,
          [flow]: { status: "live", fingerprint: fp, validatedAt: new Date().toISOString() }
        }
      }));
    },
    onError: () => setTestModalOpen(true)
  });
  const imageTestMutation = useMutation({
    mutationFn: () => postJson("/api/settings/image-model-test", actionSchema, valuesRef.current),
    onSuccess: () => {
      setTestModalOpen(true);
      const v = valuesRef.current;
      setConnectionStore((prev) => ({
        ...prev,
        ai: {
          ...prev.ai,
          image: {
            status: "live",
            fingerprint: fingerprintAiImage(v),
            validatedAt: new Date().toISOString()
          }
        }
      }));
    },
    onError: () => setTestModalOpen(true)
  });
  const visionTestMutation = useMutation({
    mutationFn: () => postJson("/api/settings/vision-model-test", actionSchema, valuesRef.current),
    onSuccess: () => {
      setTestModalOpen(true);
      const v = valuesRef.current;
      setConnectionStore((prev) => ({
        ...prev,
        ai: {
          ...prev.ai,
          vision: {
            status: "live",
            fingerprint: fingerprintAiVision(v),
            validatedAt: new Date().toISOString()
          }
        }
      }));
    },
    onError: () => setTestModalOpen(true)
  });

  useEffect(() => {
    if (!testModalOpen) {
      setImageTestElapsedMs(0);
      return;
    }
    if (testTarget !== "image" || !imageTestMutation.isPending) {
      return;
    }
    const t0 = performance.now();
    setImageTestElapsedMs(0);
    const id = window.setInterval(() => {
      setImageTestElapsedMs(Math.floor(performance.now() - t0));
    }, 100);
    return () => {
      window.clearInterval(id);
      setImageTestElapsedMs(Math.floor(performance.now() - t0));
    };
  }, [testModalOpen, testTarget, imageTestMutation.isPending]);

  async function validateDataforseo() {
    setDfsStatus("checking");
    setDfsDetail("");
    try {
      const res = await fetch("/api/keywords/target/validate-dataforseo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dataforseo_api_login: valuesRef.current.dataforseo_api_login || "",
          dataforseo_api_password: valuesRef.current.dataforseo_api_password || ""
        })
      });
      const json = (await res.json()) as { ok: boolean; detail: string };
      setDfsStatus(json.ok ? "ok" : "error");
      setDfsDetail(json.detail || "");
      if (json.ok) {
        const fp = fingerprintDataforseo(valuesRef.current);
        setConnectionStore((prev) => ({
          ...prev,
          dataforseo: { status: "live", fingerprint: fp, validatedAt: new Date().toISOString() }
        }));
      }
    } catch {
      setDfsStatus("error");
      setDfsDetail("Network error — could not reach the server.");
    }
  }

  async function validateGoogleAds() {
    setGoogleAdsStatus("checking");
    setGoogleAdsDetail("");
    try {
      const res = await postJson("/api/settings/google-ads-test", actionSchema, {
        google_ads_developer_token: values.google_ads_developer_token || ""
      });
      const count = (res.result as { accessible_customer_count?: number } | null | undefined)?.accessible_customer_count;
      setGoogleAdsStatus("ok");
      setGoogleAdsDetail(
        typeof count === "number"
          ? `${res.message} (${count} accessible customer${count === 1 ? "" : "s"}).`
          : res.message
      );
      setConnectionStore((prev) => ({
        ...prev,
        googleAds: {
          status: "live",
          fingerprint: fingerprintGoogleAds(valuesRef.current),
          validatedAt: new Date().toISOString()
        }
      }));
      void queryClient.invalidateQueries({ queryKey: ["settings"] });
    } catch (e) {
      setGoogleAdsStatus("error");
      setGoogleAdsDetail((e as Error).message);
    }
  }

  async function refreshGscCache() {
    setGscCacheStatus("refreshing");
    setGscCacheDetail("");
    try {
      const res = await postJson("/api/google-signals/refresh", actionSchema, {
        message: "",
        result: { scope: "search_console_summary" }
      });
      setGscCacheStatus("ok");
      setGscCacheDetail(res.message);
    } catch (e) {
      setGscCacheStatus("error");
      setGscCacheDetail((e as Error).message);
    }
  }

  async function refreshGa4Cache() {
    setGa4CacheStatus("refreshing");
    setGa4CacheDetail("");
    try {
      const res = await postJson("/api/google-signals/refresh", actionSchema, {
        message: "",
        result: { scope: "ga4_summary" }
      });
      setGa4CacheStatus("ok");
      setGa4CacheDetail(res.message);
    } catch (e) {
      setGa4CacheStatus("error");
      setGa4CacheDetail((e as Error).message);
    }
  }

  async function validateShopify() {
    setShopifyStatus("checking");
    setShopifyDetail("");
    try {
      const res = await postJson("/api/settings/shopify-test", actionSchema, {
        shopify_shop: valuesRef.current.shopify_shop || "",
        shopify_client_id: valuesRef.current.shopify_client_id || "",
        shopify_client_secret: valuesRef.current.shopify_client_secret || "",
        shopify_api_version: valuesRef.current.shopify_api_version || ""
      });
      setShopifyStatus("ok");
      setShopifyDetail(res.message);
      setConnectionStore((prev) => ({
        ...prev,
        shopify: {
          status: "live",
          fingerprint: fingerprintShopify(valuesRef.current),
          validatedAt: new Date().toISOString()
        }
      }));
    } catch (e) {
      setShopifyStatus("error");
      setShopifyDetail((e as Error).message);
    }
  }

  const openRouterModelsMutation = useMutation({
    mutationFn: (payload: { openrouter_api_key: string }) =>
      postJson("/api/settings/openrouter-models", modelsSchema, payload),
    onSuccess: (result) => setOpenRouterModels(result.models),
    onError: () => setOpenRouterModels([])
  });
  const openrouterApiKey = values.openrouter_api_key || "";

  useEffect(() => {
    if (!query.data) return;
    openRouterModelsMutation.mutate({
      openrouter_api_key: openrouterApiKey
    });
  }, [query.data, openrouterApiKey]);

  if (query.isLoading) return <div className="rounded-[30px] border border-white/70 bg-white/90 p-8 shadow-panel">Loading settings…</div>;
  if (query.error || !query.data) return <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">{(query.error as Error)?.message || "Could not load settings."}</div>;

  const generationProvider = values.ai_generation_provider || "openrouter";
  const sidekickProvider = values.ai_sidekick_provider || values.ai_generation_provider || "openrouter";
  const reviewProvider = values.ai_review_provider || "openrouter";
  const imageProvider = values.ai_image_provider || "openrouter";
  const activeTabConfig = settingsTabs.find((tab) => tab.id === activeTab) ?? settingsTabs[0];

  function openTestModal(target: "generation" | "review" | "sidekick") {
    setTestTarget(target);
    setTestModalOpen(true);
    testMutation.mutate(target);
  }

  function openImageTestModal() {
    setTestTarget("image");
    setTestModalOpen(true);
    imageTestMutation.mutate();
  }

  function openVisionTestModal() {
    setTestTarget("vision");
    setTestModalOpen(true);
    visionTestMutation.mutate();
  }

  const aiTestBusy =
    testMutation.isPending || imageTestMutation.isPending || visionTestMutation.isPending;
  const isImageTestTarget = testTarget === "image";
  const isVisionTestTarget = testTarget === "vision";
  const activeTestPending = isImageTestTarget
    ? imageTestMutation.isPending
    : isVisionTestTarget
      ? visionTestMutation.isPending
      : testMutation.isPending;
  const activeTestError = isImageTestTarget
    ? imageTestMutation.error
    : isVisionTestTarget
      ? visionTestMutation.error
      : testMutation.error;
  const activeTestData = isImageTestTarget
    ? imageTestMutation.data
    : isVisionTestTarget
      ? visionTestMutation.data
      : testMutation.data;

  const testResult = activeTestData?.result as Record<string, unknown> | undefined;
  const testMeta = (testResult?._meta as Record<string, unknown> | undefined) || undefined;
  const visionPreviewProvider = (values.ai_vision_provider ?? "").trim() || generationProvider;
  const visionPreviewModel =
    (values.ai_vision_model ?? "").trim() ||
    (visionPreviewProvider === generationProvider ? values.ai_generation_model || "" : "");

  const displayedProvider =
    testTarget === "image"
      ? imageProvider
      : testTarget === "vision"
        ? visionPreviewProvider
        : testTarget === "review"
          ? reviewProvider
          : testTarget === "sidekick"
            ? sidekickProvider
            : generationProvider;
  const displayedModel =
    testTarget === "image"
      ? (values.ai_image_model || "")
      : testTarget === "vision"
        ? visionPreviewModel
        : testTarget === "review"
          ? (values.ai_review_model || "")
          : testTarget === "sidekick"
            ? (values.ai_sidekick_model || values.ai_generation_model || "")
            : (values.ai_generation_model || "");

  const fieldsProps = {
    values,
    setValues,
    query: query.data,
    openRouterModels,
    openTestModal,
    openImageTestModal,
    openVisionTestModal,
    aiTestBusy,
    dfsStatus,
    dfsDetail,
    validateDataforseo,
    googleAdsStatus,
    googleAdsDetail,
    validateGoogleAds,
    shopifyStatus,
    shopifyDetail,
    validateShopify,
    gscCacheStatus,
    gscCacheDetail,
    refreshGscCache,
    ga4CacheStatus,
    ga4CacheDetail,
    refreshGa4Cache,
    connectionStore,
    shopifyShopInfo: shopifyShopInfoQuery.data ?? null
  };

  return (
    <div className="space-y-6">
      {toast ? <Toast variant={detectToastVariant(toast)}>{toast}</Toast> : null}
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Settings</p>
        <h2 className="mt-2 text-4xl font-bold text-ink">Platform configuration</h2>
        <p className="mt-2 text-sm text-slate-500">
          Credentials and options are stored in the local service settings database on this machine. For normal setup you can
          configure everything here — no <code className="rounded bg-slate-100 px-1 font-mono text-xs">.env</code> file is
          required. Advanced deployments (Docker, CI) can still inject the same keys via environment variables.
        </p>
      </div>
      <Card>
        <div className="mb-6 flex flex-wrap gap-3 text-sm text-slate-600">
          <span>AI configured: {query.data.ai_configured ? "yes" : "no"}</span>
        </div>
        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as SettingsTabId)} className="space-y-5">
          <TabsList className="grid h-auto w-full grid-cols-1 gap-2 rounded-[22px] border border-line bg-[#f7f9fc] p-2 md:grid-cols-2 xl:grid-cols-4">
            {settingsTabs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="justify-start rounded-[18px] px-4 py-3 text-left data-[state=active]:bg-white data-[state=active]:shadow-[0_12px_30px_rgba(13,28,64,0.08)] data-[state=inactive]:text-slate-500 data-[state=inactive]:hover:bg-white/70"
              >
                <span className="text-sm font-semibold">{tab.label}</span>
              </TabsTrigger>
            ))}
          </TabsList>
          <div className="space-y-4">
            <div className="rounded-2xl border border-line bg-[#f7f9fc] px-5 py-4">
              <p className="text-xs uppercase tracking-[0.2em] text-slate-500">{activeTabConfig.label}</p>
              <p className="mt-2 text-sm text-slate-600">{activeTabConfig.description}</p>
            </div>
            <TabsContent value="integrations" className="mt-0 space-y-4">
              {renderSettingsTabSections({ ...fieldsProps, tabKey: "integrations" })}
            </TabsContent>
            <TabsContent value="ai-models" className="mt-0 space-y-4">
              {renderSettingsTabSections({ ...fieldsProps, tabKey: "ai-models" })}
            </TabsContent>
            <TabsContent value="runtime" className="mt-0 space-y-4">
              {renderSettingsTabSections({ ...fieldsProps, tabKey: "runtime" })}
            </TabsContent>
            <TabsContent value="data-sources" className="mt-0 space-y-4">
              {renderSettingsTabSections({ ...fieldsProps, tabKey: "data-sources" })}
            </TabsContent>
          </div>
        </Tabs>

        <div className="mt-6 flex flex-wrap gap-3">
          <Button onClick={() => saveMutation.mutate()}>Save settings</Button>
        </div>
      </Card>
      <Modal
        open={testModalOpen}
        onOpenChange={setTestModalOpen}
        title={
          testTarget === "image"
            ? "Image model test"
            : testTarget === "vision"
              ? "Vision model test"
              : testTarget === "review"
                ? "Review QA connection test"
                : testTarget === "sidekick"
                  ? "Sidekick connection test"
                  : "Generation connection test"
        }
        description={
          testTarget === "image"
            ? "Generates a small sample image with your selected provider and model. Nothing is saved to Shopify."
            : testTarget === "vision"
              ? "Sends a tiny test image to your Vision provider and model (including OpenRouter-routed multimodal models). Nothing is saved to Shopify."
              : "Runs a targeted AI provider and model connection test for the selected settings block."
        }
      >
        <div className="space-y-4">
          <div className="rounded-2xl border border-line bg-[#f7f9fc] p-4 text-sm text-slate-600">
            <p>
              <span className="font-semibold text-ink">Target:</span>{" "}
              {testTarget === "image"
                ? "Image generation"
                : testTarget === "vision"
                  ? "Vision (alt captions)"
                  : testTarget === "review"
                    ? "Review QA"
                    : testTarget === "sidekick"
                      ? "Sidekick"
                      : "Generation"}
            </p>
            <p className="mt-1">
              <span className="font-semibold text-ink">Provider:</span> {String(testMeta?.provider || displayedProvider || "Not selected")}
            </p>
            <p className="mt-1">
              <span className="font-semibold text-ink">Model:</span> {String(testMeta?.model || displayedModel || "Not selected")}
            </p>
          </div>
          {activeTestPending ? (
            testTarget === "image" ? (
              <div className="rounded-2xl border border-line bg-white p-4 text-sm text-slate-600">
                <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line/80 pb-3">
                  <div>
                    <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Elapsed</p>
                    <p className="mt-0.5 text-2xl font-semibold tabular-nums text-ink">{formatTestElapsed(imageTestElapsedMs)}</p>
                  </div>
                  <p className="max-w-[14rem] text-right text-xs text-slate-500">Timed in your browser while the request is in flight.</p>
                </div>
                <ul className="mt-4 space-y-2.5">
                  {imageTestStepStates(imageTestElapsedMs).map((step) => (
                    <li key={step.label} className="flex items-center gap-3">
                      <span
                        className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                          step.state === "done"
                            ? "border-[#8eb89a] bg-[#e8f4ec] text-[#255b38]"
                            : step.state === "active"
                              ? "border-ocean bg-ocean/10 text-ocean"
                              : "border-line bg-[#f7f9fc] text-slate-400"
                        }`}
                        aria-hidden
                      >
                        {step.state === "done" ? "✓" : step.state === "active" ? "●" : ""}
                      </span>
                      <span
                        className={
                          step.state === "active"
                            ? "font-semibold text-ink"
                            : step.state === "done"
                              ? "text-slate-600"
                              : "text-slate-400"
                        }
                      >
                        {step.label}
                        {step.state === "active" && step.label === "Generating image" ? " — provider still working" : ""}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="mt-4 text-xs text-slate-500">
                  Image models often need 15–60 seconds. Steps advance on elapsed time so you can tell the UI is live; the provider does not stream finer-grained status yet.
                </p>
              </div>
            ) : (
              <div className="rounded-2xl border border-line bg-white p-4 text-sm text-slate-600">Testing connection…</div>
            )
          ) : activeTestError ? (
            <div className="rounded-2xl border border-[#ffd2c5] bg-[#fff4ef] p-4 text-sm text-[#8f3e20]">
              {(activeTestError as Error).message}
              {testTarget === "image" && imageTestElapsedMs > 0 ? (
                <p className="mt-2 text-xs font-normal text-[#a65d45]">Stopped after {formatTestElapsed(imageTestElapsedMs)}</p>
              ) : null}
            </div>
          ) : activeTestData ? (
            testTarget === "image" &&
            typeof testResult?.image_base64 === "string" &&
            testResult.image_base64.length > 0 ? (
              <div className="space-y-3 rounded-2xl border border-[#cfe8d8] bg-[#f4fbf6] p-4 text-sm text-[#255b38]">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <p className="font-semibold text-[#184127]">{activeTestData.message}</p>
                  {imageTestElapsedMs > 0 ? (
                    <p className="text-xs font-medium text-[#3d7349]">Total time {formatTestElapsed(imageTestElapsedMs)}</p>
                  ) : null}
                </div>
                <div className="overflow-hidden rounded-xl border border-line bg-white p-2">
                  <img
                    alt="Sample generated by your image model"
                    className="mx-auto max-h-[min(24rem,55vh)] w-auto max-w-full object-contain"
                    src={`data:${String(testResult.mime_type || "image/png")};base64,${testResult.image_base64}`}
                  />
                </div>
                {testMeta && typeof testMeta.bytes === "number" ? (
                  <p className="text-xs text-slate-600">
                    Received {testMeta.bytes.toLocaleString()} bytes · MIME {String(testResult.mime_type || "image/png")}
                  </p>
                ) : null}
              </div>
            ) : testTarget === "vision" && typeof testResult?.suggested_alt === "string" ? (
              <div className="space-y-3 rounded-2xl border border-[#cfe8d8] bg-[#f4fbf6] p-4 text-sm text-[#255b38]">
                <p className="font-semibold text-[#184127]">{activeTestData.message}</p>
                <p className="rounded-xl border border-line bg-white/90 px-4 py-3 text-ink">
                  <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Model caption</span>
                  <span className="mt-1 block text-base font-medium leading-snug">{testResult.suggested_alt}</span>
                </p>
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-xl bg-white/80 p-3 text-xs text-ink">
                  {JSON.stringify(activeTestData.result, null, 2)}
                </pre>
              </div>
            ) : (
              <div className="rounded-2xl border border-[#cfe8d8] bg-[#f4fbf6] p-4 text-sm text-[#255b38]">
                <p className="font-semibold text-[#184127]">{activeTestData.message}</p>
                <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-xl bg-white/80 p-3 text-xs text-ink">
                  {JSON.stringify(activeTestData.result, null, 2)}
                </pre>
              </div>
            )
          ) : null}
        </div>
      </Modal>
    </div>
  );
}
