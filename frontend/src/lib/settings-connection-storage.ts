/** Persisted validation state for Settings connection badges (localStorage). */

export const SETTINGS_CONNECTION_STORAGE_KEY = "shopifyseo.settings.connectionStatus.v1";

export type LiveEntry = {
  status: "live";
  fingerprint: string;
  validatedAt: string;
};

export type AiFlowKey = "generation" | "sidekick" | "review" | "image" | "vision";

export type ConnectionStatusStore = {
  dataforseo?: LiveEntry;
  serpapi?: LiveEntry;
  googleAds?: LiveEntry;
  shopify?: LiveEntry;
  ai?: Partial<Record<AiFlowKey, LiveEntry>>;
};

export function stableFingerprint(parts: Record<string, string>): string {
  const keys = Object.keys(parts).sort();
  return keys.map((k) => `${k}=${parts[k] ?? ""}`).join("|");
}

export function fingerprintDataforseo(values: Record<string, string>): string {
  return stableFingerprint({
    dataforseo_api_login: (values.dataforseo_api_login || "").trim(),
    dataforseo_api_password: (values.dataforseo_api_password || "").trim()
  });
}

export function fingerprintSerpapi(values: Record<string, string>): string {
  return stableFingerprint({
    serpapi_api_key: (values.serpapi_api_key || "").trim()
  });
}

export function fingerprintGoogleAds(values: Record<string, string>): string {
  return stableFingerprint({
    google_ads_developer_token: (values.google_ads_developer_token || "").trim(),
    google_ads_customer_id: (values.google_ads_customer_id || "").trim(),
    google_ads_login_customer_id: (values.google_ads_login_customer_id || "").trim()
  });
}

export function fingerprintShopify(values: Record<string, string>): string {
  return stableFingerprint({
    shopify_shop: (values.shopify_shop || "").trim(),
    shopify_api_version: (values.shopify_api_version || "").trim(),
    shopify_client_id: (values.shopify_client_id || "").trim(),
    shopify_client_secret: (values.shopify_client_secret || "").trim()
  });
}

function allProviderKeys(values: Record<string, string>): Record<string, string> {
  return {
    openai_api_key: (values.openai_api_key || "").trim(),
    gemini_api_key: (values.gemini_api_key || "").trim(),
    anthropic_api_key: (values.anthropic_api_key || "").trim(),
    openrouter_api_key: (values.openrouter_api_key || "").trim(),
    ollama_api_key: (values.ollama_api_key || "").trim(),
    ollama_base_url: (values.ollama_base_url || "").trim()
  };
}

export function fingerprintAiGeneration(values: Record<string, string>): string {
  return stableFingerprint({
    ...allProviderKeys(values),
    ai_generation_provider: (values.ai_generation_provider || "openrouter").trim().toLowerCase(),
    ai_generation_model: (values.ai_generation_model || "").trim()
  });
}

export function fingerprintAiSidekick(values: Record<string, string>): string {
  const gen = (values.ai_generation_provider || "openrouter").trim().toLowerCase();
  const sp = (values.ai_sidekick_provider || "").trim().toLowerCase();
  const sidekick = sp || gen;
  return stableFingerprint({
    ...allProviderKeys(values),
    ai_generation_provider: gen,
    ai_sidekick_provider: sidekick,
    ai_sidekick_model: (values.ai_sidekick_model || "").trim()
  });
}

export function fingerprintAiReview(values: Record<string, string>): string {
  return stableFingerprint({
    ...allProviderKeys(values),
    ai_review_provider: (values.ai_review_provider || "openrouter").trim().toLowerCase(),
    ai_review_model: (values.ai_review_model || "").trim()
  });
}

export function fingerprintAiImage(values: Record<string, string>): string {
  return stableFingerprint({
    ...allProviderKeys(values),
    ai_image_provider: (values.ai_image_provider || "openrouter").trim().toLowerCase(),
    ai_image_model: (values.ai_image_model || "").trim()
  });
}

export function fingerprintAiVision(values: Record<string, string>): string {
  const gen = (values.ai_generation_provider || "openrouter").trim().toLowerCase();
  const vp = (values.ai_vision_provider || "").trim().toLowerCase();
  const vision = vp || gen;
  return stableFingerprint({
    ...allProviderKeys(values),
    ai_generation_provider: gen,
    ai_vision_provider: vision,
    ai_vision_model: (values.ai_vision_model || "").trim()
  });
}

export function loadConnectionStore(): ConnectionStatusStore {
  if (typeof window === "undefined") return {};
  try {
    const raw = localStorage.getItem(SETTINGS_CONNECTION_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as ConnectionStatusStore;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

export function persistConnectionStore(store: ConnectionStatusStore): void {
  if (typeof window === "undefined") return;
  try {
    localStorage.setItem(SETTINGS_CONNECTION_STORAGE_KEY, JSON.stringify(store));
  } catch {
    /* ignore quota */
  }
}
