import type { Dispatch, SetStateAction } from "react";

import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Textarea } from "../components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "../components/ui/select";
import { SettingsConnectionBadge } from "../components/settings/settings-connection-badge";
import { SettingsSecretInput } from "../components/settings/settings-secret-input";
import { isProviderKeyReady } from "../lib/ai-provider-readiness";
import {
  fingerprintAiGeneration,
  fingerprintAiImage,
  fingerprintAiReview,
  fingerprintAiSidekick,
  fingerprintAiVision,
  fingerprintDataforseo,
  fingerprintGoogleAds,
  fingerprintShopify,
  type ConnectionStatusStore
} from "../lib/settings-connection-storage";
import type { SettingsPayload, ShopifyShopInfo } from "../types/api";
import { SettingsFieldShell } from "./settings-field-shell";
import { isSettingsSecretField, metaForSettingsField, settingsFieldHintId } from "./settings-field-meta";

export const settingsTabs = [
  {
    id: "integrations",
    label: "Integrations",
    description: "Provider keys, DataForSEO, and connections that power AI and keyword research."
  },
  {
    id: "ai-models",
    label: "AI Models",
    description: "Providers and models for generation, Sidekick, review, images, and vision."
  },
  {
    id: "runtime",
    label: "Runtime",
    description: "Timeouts and retries for AI HTTP calls."
  },
  {
    id: "data-sources",
    label: "Data Sources",
    description: "Shop identity, Shopify Admin API, and Google Search Console / GA4."
  }
] as const;

export type SettingsTabId = (typeof settingsTabs)[number]["id"];

export const providerOptions = ["openai", "gemini", "anthropic", "openrouter", "ollama"] as const;

export const staticModelOptionsByProvider: Record<string, string[]> = {
  openai: ["gpt-5-mini", "gpt-5.4", "gpt-5.4-mini", "gpt-4.1-mini"],
  gemini: ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash"],
  anthropic: ["claude-opus-4-6", "claude-3-7-sonnet-latest", "claude-sonnet-4-20250514", "claude-3-5-sonnet-latest"],
  openrouter: ["z-ai/glm-4.5-air:free"],
  ollama: ["kimi-k2.5:cloud", "llama3.1", "llama3.1:8b", "qwen2.5", "qwen2.5:14b", "mistral", "deepseek-r1:8b"]
};

/** Mirrors `shopifyseo/dashboard_ai_engine_parts/config.py` (DEFAULT_TIMEOUT_SECONDS, DEFAULT_MAX_RETRIES). */
export const DEFAULT_AI_TIMEOUT_SECONDS = 120;
export const DEFAULT_AI_MAX_RETRIES = 2;

function settingsTextPlaceholder(key: string): string | undefined {
  if (key === "store_custom_domain") return "e.g. yourstore.com";
  if (key === "ga4_property_id") return "Enter property ID or enable the API below for auto-discovery";
  if (key === "google_ads_customer_id") return "Save developer token and refresh, or enter numeric customer ID";
  if (key === "google_ads_login_customer_id") return "Optional: MCC / manager ID when customer is a client account";
  if (key === "search_console_site") return "Connect Google above to auto-discover sites";
  return undefined;
}

export function displayNumericSetting(raw: string | undefined, defaultNum: number): string {
  const t = (raw ?? "").trim();
  return t === "" ? String(defaultNum) : t;
}

export function isNumericDefault(raw: string | undefined, defaultNum: number): boolean {
  const t = (raw ?? "").trim();
  if (t === "") return true;
  return t === String(defaultNum);
}

function sortModelOptions(options: string[]) {
  return [...options].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
}

export type RenderSettingsTabSectionsProps = {
  tabKey: SettingsTabId;
  values: Record<string, string>;
  setValues: Dispatch<SetStateAction<Record<string, string>>>;
  query: SettingsPayload;
  ollamaModels: string[];
  geminiModels: string[];
  anthropicModels: string[];
  openRouterModels: string[];
  openTestModal: (target: "generation" | "review" | "sidekick") => void;
  openImageTestModal: () => void;
  openVisionTestModal: () => void;
  aiTestBusy: boolean;
  dfsStatus: "idle" | "checking" | "ok" | "error";
  dfsDetail: string;
  validateDataforseo: () => void | Promise<void>;
  googleAdsStatus: "idle" | "checking" | "ok" | "error";
  googleAdsDetail: string;
  validateGoogleAds: () => void | Promise<void>;
  shopifyStatus: "idle" | "checking" | "ok" | "error";
  shopifyDetail: string;
  validateShopify: () => void | Promise<void>;
  gscCacheStatus: "idle" | "refreshing" | "ok" | "error";
  gscCacheDetail: string;
  refreshGscCache: () => void | Promise<void>;
  ga4CacheStatus: "idle" | "refreshing" | "ok" | "error";
  ga4CacheDetail: string;
  refreshGa4Cache: () => void | Promise<void>;
  connectionStore: ConnectionStatusStore;
  shopifyShopInfo: ShopifyShopInfo | null;
};

export function renderSettingsTabSections({
  tabKey,
  values,
  setValues,
  query,
  ollamaModels,
  geminiModels,
  anthropicModels,
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
  shopifyShopInfo
}: RenderSettingsTabSectionsProps) {
  const tabSections = {
    integrations: [
      {
        title: "Provider Credentials",
        description: "API keys and base URL for each vendor. Only keys for providers you use need to be set.",
        fields: ["openai_api_key", "gemini_api_key", "anthropic_api_key", "openrouter_api_key", "ollama_api_key", "ollama_base_url"] as const
      },
      {
        title: "DataForSEO",
        description:
          "API login + password from app.dataforseo.com. Required for keyword and competitor research (Labs + SERP).",
        fields: ["dataforseo_api_login", "dataforseo_api_password"] as const
      }
    ],
    "ai-models": [
      {
        title: "Generation",
        description: "First-pass drafts and main content generation.",
        fields: ["ai_generation_provider", "ai_generation_model"] as const
      },
      {
        title: "Sidekick",
        description: "In-app chat on product, collection, and page details. Leave blank to inherit Generation.",
        fields: ["ai_sidekick_provider", "ai_sidekick_model"] as const
      },
      {
        title: "Review QA",
        description: "Second-pass review and improvement on generated content.",
        fields: ["ai_review_provider", "ai_review_model"] as const
      },
      {
        title: "Image generation",
        description: "Featured and inline blog images.",
        fields: ["ai_image_provider", "ai_image_model"] as const
      },
      {
        title: "Vision (alt captions)",
        description: "Multimodal descriptions for alt text. Choose “Same as generation” or override.",
        fields: ["ai_vision_provider", "ai_vision_model"] as const
      }
    ],
    runtime: [
      {
        title: "Execution Limits",
        description: "Optional overrides; empty fields use defaults (see hints). Server validates range on save.",
        fields: ["ai_timeout_seconds", "ai_max_retries"] as const
      }
    ],
    "data-sources": [
      {
        title: "Store Identity",
        description: "Name, description, primary market, and timezone—used in prompts, research, and scheduling context. Name and description auto-pull from Shopify when left blank.",
        fields: ["store_name", "store_description", "primary_market_country", "dashboard_timezone"] as const
      },
      {
        title: "Shopify",
        description: "Shop hostname (.myshopify.com), public domain, API version, and custom app OAuth credentials.",
        fields: ["shopify_shop", "store_custom_domain", "shopify_api_version", "shopify_client_id", "shopify_client_secret"] as const
      },
      {
        title: "Google OAuth",
        description:
          "Client ID and secret for the shared Google OAuth client that Search Console and GA4 both use.",
        fields: ["google_client_id", "google_client_secret"] as const
      },
      {
        title: "Google Search Console",
        description: "Pick the Search Console property used for site-level SEO rollups.",
        fields: ["search_console_site"] as const
      },
      {
        title: "Google Analytics 4",
        description: "Pick the GA4 property used for site-wide sessions and landing-page views.",
        fields: ["ga4_property_id"] as const
      },
      {
        title: "Google Ads",
        description: "Developer token and the Ads account to use. Save the token first so customer accounts can load.",
        fields: ["google_ads_developer_token", "google_ads_customer_id", "google_ads_login_customer_id"] as const
      }
    ]
  } as const;

  const generationProvider = values.ai_generation_provider || "openai";
  const sidekickProvider = values.ai_sidekick_provider || values.ai_generation_provider || "openai";
  const reviewProvider = values.ai_review_provider || "openai";
  const imageProvider = values.ai_image_provider || "openai";
  const visionProvider = values.ai_vision_provider || generationProvider || "openai";

  const dfsFp = fingerprintDataforseo(values);
  const dfsLive =
    connectionStore.dataforseo?.status === "live" && connectionStore.dataforseo.fingerprint === dfsFp;
  const googleAdsFp = fingerprintGoogleAds(values);
  const googleAdsLive =
    connectionStore.googleAds?.status === "live" && connectionStore.googleAds.fingerprint === googleAdsFp;
  const shopifyFp = fingerprintShopify(values);
  const shopifyLive =
    connectionStore.shopify?.status === "live" && connectionStore.shopify.fingerprint === shopifyFp;

  function modelOptionsFor(provider: string, currentValue: string) {
    const base =
      provider === "ollama"
        ? ollamaModels.length
          ? ollamaModels
          : staticModelOptionsByProvider.ollama
        : provider === "gemini"
          ? geminiModels.length
            ? geminiModels
            : staticModelOptionsByProvider.gemini
          : provider === "anthropic"
            ? anthropicModels.length
              ? anthropicModels
              : staticModelOptionsByProvider.anthropic
            : provider === "openrouter"
              ? openRouterModels.length
                ? openRouterModels
                : staticModelOptionsByProvider.openrouter
              : staticModelOptionsByProvider[provider] || [];
    const sortedBase = sortModelOptions(base);
    if (currentValue && !sortedBase.includes(currentValue)) {
      return sortModelOptions([currentValue, ...sortedBase]);
    }
    return sortedBase;
  }

  return tabSections[tabKey].map((section) => (
    <div key={section.title} className="rounded-[24px] border border-line/80 bg-white p-5">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-2xl">
          <h3 className="text-lg font-semibold text-ink">{section.title}</h3>
          <p className="mt-1 text-sm text-slate-500">{section.description}</p>
        </div>
        {(() => {
          const t = section.title;
          if (t === "Provider Credentials") {
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {query.ai_configured ? (
                  <SettingsConnectionBadge label="Ready" tone="neutral" />
                ) : (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                )}
              </div>
            );
          }
          if (t === "Generation") {
            const ready = isProviderKeyReady(generationProvider, values);
            const live =
              connectionStore.ai?.generation?.status === "live" &&
              connectionStore.ai.generation.fingerprint === fingerprintAiGeneration(values);
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!ready ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : live ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => openTestModal("generation")} disabled={aiTestBusy}>
                  Test generation
                </Button>
              </div>
            );
          }
          if (t === "Sidekick") {
            const ready = isProviderKeyReady(sidekickProvider, values);
            const live =
              connectionStore.ai?.sidekick?.status === "live" &&
              connectionStore.ai.sidekick.fingerprint === fingerprintAiSidekick(values);
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!ready ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : live ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => openTestModal("sidekick")} disabled={aiTestBusy}>
                  Test Sidekick
                </Button>
              </div>
            );
          }
          if (t === "Review QA") {
            const ready = isProviderKeyReady(reviewProvider, values);
            const live =
              connectionStore.ai?.review?.status === "live" &&
              connectionStore.ai.review.fingerprint === fingerprintAiReview(values);
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!ready ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : live ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => openTestModal("review")} disabled={aiTestBusy}>
                  Test review
                </Button>
              </div>
            );
          }
          if (t === "Image generation") {
            const ready = isProviderKeyReady(imageProvider, values);
            const live =
              connectionStore.ai?.image?.status === "live" &&
              connectionStore.ai.image.fingerprint === fingerprintAiImage(values);
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!ready ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : live ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => openImageTestModal()} disabled={aiTestBusy}>
                  Test model
                </Button>
              </div>
            );
          }
          if (t === "Vision (alt captions)") {
            const ready = isProviderKeyReady(visionProvider, values);
            const live =
              connectionStore.ai?.vision?.status === "live" &&
              connectionStore.ai.vision.fingerprint === fingerprintAiVision(values);
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!ready ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : live ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => openVisionTestModal()} disabled={aiTestBusy}>
                  Test vision
                </Button>
              </div>
            );
          }
          if (t === "DataForSEO") {
            const hasCreds = !!(values.dataforseo_api_login?.trim() && values.dataforseo_api_password?.trim());
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!hasCreds ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : dfsLive ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => void validateDataforseo()} disabled={dfsStatus === "checking"}>
                  {dfsStatus === "checking" ? "Checking…" : "Validate access"}
                </Button>
              </div>
            );
          }
          if (t === "Google Ads") {
            const hasToken = !!(values.google_ads_developer_token || "").trim();
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!hasToken ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : googleAdsLive ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button
                  variant="secondary"
                  onClick={() => void validateGoogleAds()}
                  disabled={googleAdsStatus === "checking"}
                >
                  {googleAdsStatus === "checking" ? "Testing…" : "Test connection"}
                </Button>
              </div>
            );
          }
          if (t === "Shopify") {
            const credsOnFile = query.sync_scope_ready.shopify;
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {!credsOnFile ? (
                  <SettingsConnectionBadge label="Not configured" tone="warning" />
                ) : shopifyLive ? (
                  <SettingsConnectionBadge label="Live" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Not tested" tone="neutral" />
                )}
                <Button variant="secondary" onClick={() => void validateShopify()} disabled={shopifyStatus === "checking"}>
                  {shopifyStatus === "checking" ? "Testing…" : "Test connection"}
                </Button>
              </div>
            );
          }
          if (t === "Store Identity") {
            const complete = !!(
              values.store_name?.trim() &&
              values.primary_market_country?.trim() &&
              values.dashboard_timezone?.trim()
            );
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                {complete ? (
                  <SettingsConnectionBadge label="Complete" tone="success" />
                ) : (
                  <SettingsConnectionBadge label="Incomplete" tone="neutral" />
                )}
              </div>
            );
          }
          if (t === "Google OAuth") {
            const configured = query.google_configured;
            const connected = query.google_connected;
            const statusLabel =
              configured && connected
                ? "Live"
                : configured && !connected
                  ? "Not connected"
                  : !configured && connected
                    ? "Credentials missing"
                    : "Not configured";
            const badgeTone: "success" | "warning" | "danger" =
              configured && connected ? "success" : !configured && connected ? "danger" : "warning";
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                <SettingsConnectionBadge label={statusLabel} tone={badgeTone} />
                {query.auth_url ? (
                  <a
                    href={query.auth_url}
                    className="inline-flex items-center gap-2 rounded-xl bg-ocean px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-ocean/90"
                  >
                    {connected ? "Reconnect Google" : "Connect Google"}
                  </a>
                ) : null}
              </div>
            );
          }
          if (t === "Google Search Console") {
            const connected = query.google_configured && query.google_connected;
            const hasProperty = !!(values.search_console_site || "").trim();
            const statusLabel = !connected
              ? "Google not connected"
              : hasProperty
                ? "Ready"
                : "Property not selected";
            const badgeTone: "success" | "warning" | "neutral" = !connected
              ? "warning"
              : hasProperty
                ? "success"
                : "neutral";
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                <SettingsConnectionBadge label={statusLabel} tone={badgeTone} />
                <Button
                  variant="secondary"
                  onClick={() => void refreshGscCache()}
                  disabled={!connected || !hasProperty || gscCacheStatus === "refreshing"}
                >
                  {gscCacheStatus === "refreshing" ? "Refreshing…" : "Refresh cache"}
                </Button>
              </div>
            );
          }
          if (t === "Google Analytics 4") {
            const connected = query.google_configured && query.google_connected;
            const hasProperty = !!(values.ga4_property_id || "").trim();
            const statusLabel = !connected
              ? "Google not connected"
              : hasProperty
                ? "Ready"
                : "Property not selected";
            const badgeTone: "success" | "warning" | "neutral" = !connected
              ? "warning"
              : hasProperty
                ? "success"
                : "neutral";
            return (
              <div className="flex flex-wrap items-center justify-end gap-3">
                <SettingsConnectionBadge label={statusLabel} tone={badgeTone} />
                <Button
                  variant="secondary"
                  onClick={() => void refreshGa4Cache()}
                  disabled={!connected || !hasProperty || ga4CacheStatus === "refreshing"}
                >
                  {ga4CacheStatus === "refreshing" ? "Refreshing…" : "Refresh cache"}
                </Button>
              </div>
            );
          }
          return null;
        })()}
      </div>
      {section.title === "Google OAuth" && !query.google_configured && query.google_connected ? (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-600">
          A previous Google token exists but the Client ID and Client Secret are missing. Enter them below and <strong>Save settings</strong>, then click <strong>Reconnect Google</strong> to restore the connection.
        </div>
      ) : section.title === "Google OAuth" && !query.google_configured && !query.google_connected ? (
        <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm text-blue-700">
          Enter your Google Client ID and Client Secret below, then <strong>Save settings</strong> to enable the Connect Google button.
        </div>
      ) : null}
      {section.title === "Google Search Console" && gscCacheStatus !== "idle" && gscCacheStatus !== "refreshing" ? (
        <div
          className={`mb-4 rounded-xl border px-4 py-2.5 text-sm ${gscCacheStatus === "ok" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-600"}`}
        >
          {gscCacheDetail}
        </div>
      ) : null}
      {section.title === "Google Analytics 4" && ga4CacheStatus !== "idle" && ga4CacheStatus !== "refreshing" ? (
        <div
          className={`mb-4 rounded-xl border px-4 py-2.5 text-sm ${ga4CacheStatus === "ok" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-600"}`}
        >
          {ga4CacheDetail}
        </div>
      ) : null}
      {section.title === "DataForSEO" && dfsStatus !== "idle" && dfsStatus !== "checking" ? (
        <div
          className={`mb-4 rounded-xl border px-4 py-2.5 text-sm ${dfsStatus === "ok" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-600"}`}
        >
          {dfsDetail}
        </div>
      ) : null}
      {section.title === "Google Ads" && googleAdsStatus !== "idle" && googleAdsStatus !== "checking" ? (
        <div
          className={`mb-4 rounded-xl border px-4 py-2.5 text-sm ${googleAdsStatus === "ok" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-600"}`}
        >
          {googleAdsDetail}
        </div>
      ) : null}
      {section.title === "Shopify" && shopifyStatus !== "idle" && shopifyStatus !== "checking" ? (
        <div
          className={`mb-4 rounded-xl border px-4 py-2.5 text-sm ${shopifyStatus === "ok" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-600"}`}
        >
          {shopifyDetail}
        </div>
      ) : null}
      <div className="grid gap-5 xl:grid-cols-2">
        {section.fields.map((key) => {
          const meta = metaForSettingsField(key);
          const fieldId = `setting-${key}`;
          const hintId = settingsFieldHintId(fieldId);
          const describeHint = meta.hint ? hintId : undefined;

          const control =
            key === "ai_timeout_seconds" || key === "ai_max_retries" ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    id={fieldId}
                    type="text"
                    inputMode="numeric"
                    autoComplete="off"
                    aria-describedby={describeHint}
                    className="min-w-[7rem] max-w-[12rem] rounded-2xl border border-line bg-white px-4 py-3 font-medium tabular-nums text-ink outline-none"
                    value={displayNumericSetting(
                      values[key],
                      key === "ai_timeout_seconds" ? DEFAULT_AI_TIMEOUT_SECONDS : DEFAULT_AI_MAX_RETRIES
                    )}
                    onChange={(event) => {
                      const digitsOnly = event.target.value.replace(/\D/g, "");
                      setValues((current) => ({ ...current, [key]: digitsOnly }));
                    }}
                  />
                  {isNumericDefault(
                    values[key],
                    key === "ai_timeout_seconds" ? DEFAULT_AI_TIMEOUT_SECONDS : DEFAULT_AI_MAX_RETRIES
                  ) ? (
                    <span className="text-xs font-medium text-slate-500">(default)</span>
                  ) : null}
                </div>
              </>
            ) : key === "primary_market_country" ? (
              <Select
                value={values[key] || "CA"}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select country" />
                </SelectTrigger>
                <SelectContent>
                  {[
                    { code: "CA", name: "Canada" },
                    { code: "US", name: "United States" },
                    { code: "GB", name: "United Kingdom" },
                    { code: "AU", name: "Australia" },
                    { code: "NZ", name: "New Zealand" },
                    { code: "IE", name: "Ireland" },
                    { code: "ZA", name: "South Africa" },
                    { code: "IN", name: "India" },
                    { code: "SG", name: "Singapore" },
                    { code: "AE", name: "United Arab Emirates" },
                    { code: "DE", name: "Germany" },
                    { code: "FR", name: "France" },
                    { code: "IT", name: "Italy" },
                    { code: "ES", name: "Spain" },
                    { code: "NL", name: "Netherlands" },
                    { code: "SE", name: "Sweden" },
                    { code: "NO", name: "Norway" },
                    { code: "DK", name: "Denmark" },
                    { code: "FI", name: "Finland" },
                    { code: "JP", name: "Japan" },
                    { code: "BR", name: "Brazil" },
                    { code: "MX", name: "Mexico" }
                  ].map((c) => (
                    <SelectItem key={c.code} value={c.code}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "dashboard_timezone" ? (
              (() => {
                const timezones: string[] = (() => {
                  try {
                    return (Intl as unknown as { supportedValuesOf: (k: string) => string[] }).supportedValuesOf("timeZone");
                  } catch {
                    return [
                      "America/Vancouver",
                      "America/Edmonton",
                      "America/Toronto",
                      "America/New_York",
                      "America/Chicago",
                      "America/Denver",
                      "America/Los_Angeles",
                      "Europe/London",
                      "Europe/Berlin",
                      "Europe/Paris",
                      "Australia/Sydney",
                      "Australia/Melbourne",
                      "Pacific/Auckland",
                      "Asia/Tokyo",
                      "Asia/Singapore",
                      "Asia/Dubai"
                    ];
                  }
                })();
                return (
                  <Select
                    value={values[key] || "America/Vancouver"}
                    onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
                  >
                    <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                      <SelectValue placeholder="Select timezone" />
                    </SelectTrigger>
                    <SelectContent className="max-h-72">
                      {timezones.map((tz) => (
                        <SelectItem key={tz} value={tz}>
                          {tz.replace(/_/g, " ")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                );
              })()
            ) : key === "ai_generation_provider" ||
              key === "ai_review_provider" ||
              key === "ai_sidekick_provider" ||
              key === "ai_image_provider" ||
              key === "ai_vision_provider" ? (
              <Select
                value={
                  key === "ai_sidekick_provider"
                    ? values.ai_sidekick_provider || values.ai_generation_provider || "openai"
                    : key === "ai_image_provider"
                      ? values.ai_image_provider || "openai"
                      : key === "ai_vision_provider"
                        ? (values.ai_vision_provider ?? "") === ""
                          ? "__inherit__"
                          : values.ai_vision_provider || values.ai_generation_provider || "openai"
                        : values[key] || "openai"
                }
                onValueChange={(nextProvider) =>
                  setValues((current) => {
                    if (key === "ai_generation_provider") {
                      const nextModelOptions = modelOptionsFor(nextProvider, "");
                      return {
                        ...current,
                        ai_generation_provider: nextProvider,
                        ai_generation_model: nextModelOptions[0] || current.ai_generation_model || ""
                      };
                    }
                    if (key === "ai_sidekick_provider") {
                      const nextModelOptions = modelOptionsFor(nextProvider, "");
                      return {
                        ...current,
                        ai_sidekick_provider: nextProvider,
                        ai_sidekick_model: nextModelOptions[0] || current.ai_sidekick_model || ""
                      };
                    }
                    if (key === "ai_image_provider") {
                      const nextModelOptions = modelOptionsFor(nextProvider, "");
                      return {
                        ...current,
                        ai_image_provider: nextProvider,
                        ai_image_model: nextModelOptions[0] || current.ai_image_model || ""
                      };
                    }
                    if (key === "ai_vision_provider") {
                      const genProv = current.ai_generation_provider || "openai";
                      const effectiveProvider = nextProvider === "__inherit__" ? "" : nextProvider;
                      if (!effectiveProvider.trim()) {
                        return {
                          ...current,
                          ai_vision_provider: "",
                          ai_vision_model: ""
                        };
                      }
                      const nextModelOptions = modelOptionsFor(effectiveProvider, "");
                      const nextVisionModel =
                        effectiveProvider === genProv
                          ? current.ai_generation_model || nextModelOptions[0] || ""
                          : nextModelOptions[0] || "";
                      return {
                        ...current,
                        ai_vision_provider: effectiveProvider,
                        ai_vision_model: nextVisionModel
                      };
                    }
                    const nextModelOptions = modelOptionsFor(nextProvider, "");
                    return {
                      ...current,
                      ai_review_provider: nextProvider,
                      ai_review_model: nextModelOptions[0] || current.ai_review_model || ""
                    };
                  })
                }
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {key === "ai_vision_provider" ? <SelectItem value="__inherit__">Same as generation</SelectItem> : null}
                  {providerOptions.map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "ai_generation_model" ? (
              <Select
                value={values[key] || undefined}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptionsFor(generationProvider, values[key] || "").map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "ai_sidekick_model" ? (
              <Select
                value={
                  (values.ai_sidekick_model ||
                    (!values.ai_sidekick_provider?.trim() ? values.ai_generation_model : "") ||
                    "") || undefined
                }
                onValueChange={(next) =>
                  setValues((current) => ({
                    ...current,
                    ai_sidekick_provider:
                      current.ai_sidekick_provider?.trim() || current.ai_generation_provider || "openai",
                    ai_sidekick_model: next
                  }))
                }
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptionsFor(
                    sidekickProvider,
                    values.ai_sidekick_model ||
                      (!values.ai_sidekick_provider?.trim() ? values.ai_generation_model : "") ||
                      ""
                  ).map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "ai_review_model" ? (
              <Select
                value={values[key] || undefined}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptionsFor(reviewProvider, values[key] || "").map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "ai_image_model" ? (
              <Select
                value={values[key] || undefined}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptionsFor(imageProvider, values[key] || "").map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "ai_vision_model" ? (
              <Select
                value={
                  (values.ai_vision_model ||
                    ((values.ai_vision_provider ?? "") === "" ? values.ai_generation_model : "") ||
                    "") || undefined
                }
                onValueChange={(next) => setValues((current) => ({ ...current, ai_vision_model: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select model" />
                </SelectTrigger>
                <SelectContent>
                  {modelOptionsFor(
                    visionProvider,
                    values.ai_vision_model ||
                      ((values.ai_vision_provider ?? "") === "" ? values.ai_generation_model : "") ||
                      ""
                  ).map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "search_console_site" && (query.available_gsc_sites?.length ?? 0) > 0 ? (
              <Select
                value={values[key] || undefined}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select a Search Console property" />
                </SelectTrigger>
                <SelectContent>
                  {query.available_gsc_sites!.map((site) => (
                    <SelectItem key={site} value={site}>
                      {site}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "ga4_property_id" && (query.available_ga4_properties?.length ?? 0) > 0 ? (
              <Select
                value={values[key] || undefined}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select a GA4 property" />
                </SelectTrigger>
                <SelectContent>
                  {query.available_ga4_properties!.map((prop) => (
                    <SelectItem key={prop.property_id} value={prop.property_id}>
                      {prop.display_name} ({prop.property_id}){prop.account_name ? ` · ${prop.account_name}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : key === "store_name" || key === "store_description" ? (
              (() => {
                const shopifyValue =
                  key === "store_name"
                    ? (shopifyShopInfo?.shop_name ?? "").trim()
                    : (shopifyShopInfo?.shop_description ?? "").trim();
                const currentValue = values[key] ?? "";
                const hasShopifyValue = !!(shopifyShopInfo?.available && shopifyValue);
                const matchesShopify = hasShopifyValue && currentValue.trim() === shopifyValue;
                const placeholder = hasShopifyValue
                  ? `Shopify: ${shopifyValue.length > 80 ? `${shopifyValue.slice(0, 80)}…` : shopifyValue}`
                  : key === "store_description"
                    ? "Short brand or positioning summary"
                    : "Your store display name";
                const control = key === "store_description" ? (
                  <Textarea
                    id={fieldId}
                    rows={3}
                    className="rounded-2xl border border-line bg-white px-4 py-3 outline-none"
                    placeholder={placeholder}
                    aria-describedby={describeHint}
                    value={currentValue}
                    onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}
                  />
                ) : (
                  <Input
                    id={fieldId}
                    type="text"
                    className="rounded-2xl border border-line bg-white px-4 py-3 outline-none"
                    placeholder={placeholder}
                    aria-describedby={describeHint}
                    value={currentValue}
                    onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}
                  />
                );
                return (
                  <>
                    {control}
                    {hasShopifyValue ? (
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        {currentValue.trim() === "" ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-[#f7f9fc] px-2.5 py-0.5 font-medium text-slate-600">
                            Using Shopify value
                          </span>
                        ) : matchesShopify ? (
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-[#f7f9fc] px-2.5 py-0.5 font-medium text-slate-600">
                            Matches Shopify
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-[#ffe2c5] bg-[#fff7ee] px-2.5 py-0.5 font-medium text-[#8f5a20]">
                            Overriding Shopify
                          </span>
                        )}
                        {!matchesShopify ? (
                          <button
                            type="button"
                            className="font-medium text-ocean underline-offset-2 hover:underline"
                            onClick={() => setValues((current) => ({ ...current, [key]: shopifyValue }))}
                          >
                            Use Shopify value
                          </button>
                        ) : null}
                        {currentValue.trim() !== "" ? (
                          <button
                            type="button"
                            className="font-medium text-slate-500 underline-offset-2 hover:underline"
                            onClick={() => setValues((current) => ({ ...current, [key]: "" }))}
                          >
                            Clear override
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </>
                );
              })()
            ) : key === "google_ads_customer_id" && (query.available_google_ads_customers?.length ?? 0) > 0 ? (
              <Select
                value={values[key] || undefined}
                onValueChange={(next) => setValues((current) => ({ ...current, [key]: next }))}
              >
                <SelectTrigger id={fieldId} aria-describedby={describeHint} className="rounded-2xl border border-line bg-white px-4 py-3 outline-none">
                  <SelectValue placeholder="Select a Google Ads account" />
                </SelectTrigger>
                <SelectContent>
                  {query.available_google_ads_customers!.map((c) => (
                    <SelectItem key={c.customer_id} value={c.customer_id}>
                      {c.descriptive_name ? `${c.descriptive_name} (${c.customer_id})` : c.customer_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <>
                {isSettingsSecretField(key) ? (
                  <SettingsSecretInput
                    id={fieldId}
                    placeholder={settingsTextPlaceholder(key)}
                    ariaDescribedBy={describeHint}
                    value={values[key] || ""}
                    onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}
                  />
                ) : (
                  <Input
                    id={fieldId}
                    type="text"
                    className="rounded-2xl border border-line bg-white px-4 py-3 outline-none"
                    placeholder={settingsTextPlaceholder(key)}
                    aria-describedby={describeHint}
                    value={values[key] || ""}
                    onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))}
                  />
                )}
                {key === "ga4_property_id" && query.ga4_api_activation_url ? (
                  <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    <p className="font-medium">The GA4 Admin API needs to be enabled in your Google Cloud project for auto-discovery.</p>
                    <a
                      href={query.ga4_api_activation_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 inline-flex items-center gap-2 rounded-lg bg-amber-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-amber-700"
                    >
                      Enable GA4 Admin API
                      <span aria-hidden>↗</span>
                    </a>
                    <p className="mt-2 text-xs text-amber-600">After enabling, refresh this page and the field above will become a dropdown of your GA4 properties.</p>
                  </div>
                ) : null}
              </>
            );

          return (
            <div key={key} className="min-w-0">
              <SettingsFieldShell fieldId={fieldId} label={meta.label} hint={meta.hint} detail={meta.detail}>
                {control}
              </SettingsFieldShell>
            </div>
          );
        })}
      </div>
    </div>
  ));
}
