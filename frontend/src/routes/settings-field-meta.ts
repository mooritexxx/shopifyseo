/**
 * Display-only metadata for Settings fields. Does not change API keys or save payloads.
 */
export type SettingsFieldKey =
  | "openai_api_key"
  | "gemini_api_key"
  | "anthropic_api_key"
  | "openrouter_api_key"
  | "ollama_api_key"
  | "ollama_base_url"
  | "dataforseo_api_login"
  | "dataforseo_api_password"
  | "ai_generation_provider"
  | "ai_generation_model"
  | "ai_sidekick_provider"
  | "ai_sidekick_model"
  | "ai_review_provider"
  | "ai_review_model"
  | "ai_image_provider"
  | "ai_image_model"
  | "ai_vision_provider"
  | "ai_vision_model"
  | "ai_timeout_seconds"
  | "ai_max_retries"
  | "store_name"
  | "store_description"
  | "primary_market_country"
  | "dashboard_timezone"
  | "shopify_shop"
  | "store_custom_domain"
  | "shopify_api_version"
  | "shopify_client_id"
  | "shopify_client_secret"
  | "google_client_id"
  | "google_client_secret"
  | "search_console_site"
  | "ga4_property_id"
  | "google_ads_developer_token"
  | "google_ads_customer_id"
  | "google_ads_login_customer_id";

export type SettingsFieldMetaEntry = {
  label: string;
  hint: string;
  detail?: string;
};

/** API keys, tokens, and passwords — masked by default with a show/hide control in Settings. */
const SETTINGS_SECRET_FIELD_KEYS_ARR = [
  "openai_api_key",
  "gemini_api_key",
  "anthropic_api_key",
  "openrouter_api_key",
  "ollama_api_key",
  "dataforseo_api_password",
  "shopify_client_secret",
  "google_client_secret",
  "google_ads_developer_token"
] as const satisfies readonly SettingsFieldKey[];

export const SETTINGS_SECRET_FIELD_KEYS: readonly SettingsFieldKey[] = SETTINGS_SECRET_FIELD_KEYS_ARR;

export function isSettingsSecretField(key: string): key is SettingsFieldKey {
  return (SETTINGS_SECRET_FIELD_KEYS_ARR as readonly string[]).includes(key);
}

export const SETTINGS_FIELD_META: Record<SettingsFieldKey, SettingsFieldMetaEntry> = {
  openai_api_key: {
    label: "OpenAI API Key",
    hint: "Secret key from platform.openai.com. Used when OpenAI is selected as a provider.",
    detail:
      "Create a key in the OpenAI dashboard under API keys. Restrict the key if your account allows it. The key is stored locally in your service settings."
  },
  gemini_api_key: {
    label: "Gemini API Key",
    hint: "Google AI Studio or Vertex key for Gemini models.",
    detail: "Obtain an API key from Google AI Studio. Some models require enabling billing on the Google Cloud project linked to the key."
  },
  anthropic_api_key: {
    label: "Anthropic API Key",
    hint: "Console.anthropic.com API key for Claude models.",
    detail: "Generate an API key in the Anthropic console. Usage and rate limits follow your Anthropic plan."
  },
  openrouter_api_key: {
    label: "OpenRouter API Key",
    hint: "openrouter.ai key for multi-provider routing.",
    detail: "OpenRouter aggregates many model providers behind one API. Your key controls which models and spend limits apply on their dashboard."
  },
  ollama_api_key: {
    label: "Ollama API Key",
    hint: "Optional. Leave blank for local Ollama; set if your server requires auth.",
    detail: "Local Ollama at http://localhost:11434 usually needs no key. Remote or cloud Ollama deployments may require a bearer token."
  },
  ollama_base_url: {
    label: "Ollama Base URL",
    hint: "Root URL of your Ollama server, e.g. http://localhost:11434",
    detail: "Must be reachable from the machine running this dashboard. No trailing /v1 — the app calls the Ollama HTTP API relative to this base."
  },
  dataforseo_api_login: {
    label: "DataForSEO API Login",
    hint: "From app.dataforseo.com → API → API Access — used as HTTP Basic username.",
    detail: "Keyword and competitor research require DataForSEO Labs + SERP. Save settings, then use Validate access."
  },
  dataforseo_api_password: {
    label: "DataForSEO API Password",
    hint: "HTTP Basic password from the same API Access page. Stored locally like other integration secrets.",
    detail: "Save settings, then use Validate access. Per-request charges apply on your DataForSEO account."
  },
  ai_generation_provider: {
    label: "Generation Provider",
    hint: "LLM vendor used for first-pass article and content generation.",
    detail: "Changing provider resets the generation model to a sensible default for that vendor. Ensure the matching API key is set on the Integrations tab."
  },
  ai_generation_model: {
    label: "Generation Model",
    hint: "Model id for drafts. List refreshes when the provider key is valid (where supported).",
    detail: "If your saved model is not in the list, it is still shown so you do not lose the value. Pick a listed model after rotating keys."
  },
  ai_sidekick_provider: {
    label: "Sidekick Provider",
    hint: "Optional override for in-app Sidekick chat; otherwise matches Generation.",
    detail: "Sidekick appears on product, collection, and page detail screens. Leave provider/model empty to inherit Generation settings."
  },
  ai_sidekick_model: {
    label: "Sidekick Model",
    hint: "Model for Sidekick when an override provider is set or after first reply.",
    detail: "Choosing a model may auto-fill the Sidekick provider if it was blank, using your Generation provider as fallback."
  },
  ai_review_provider: {
    label: "Review Provider",
    hint: "Vendor for QA and improvement passes on generated content.",
    detail: "Review runs use this provider and the review model below. Use Test review to verify credentials and model availability."
  },
  ai_review_model: {
    label: "Review Model",
    hint: "Model id for review passes.",
    detail: "Same discovery rules as Generation: dynamic lists when the API allows, with fallback static options."
  },
  ai_image_provider: {
    label: "Image Provider",
    hint: "Vendor for blog featured and inline images.",
    detail: "Not every provider exposes image models in the same way. Use Test model to confirm your combination works end to end."
  },
  ai_image_model: {
    label: "Image Model",
    hint: "Image-capable model id for the selected provider.",
    detail: "Image generation can take 15–60+ seconds depending on provider and size. The test modal shows elapsed time while waiting."
  },
  ai_vision_provider: {
    label: "Vision Provider",
    hint: "Optional. Multimodal model for alt text and image description; “Same as generation” inherits.",
    detail: "Pick “Same as generation” to use your generation provider and model. Choosing another provider clears the vision model until you select one."
  },
  ai_vision_model: {
    label: "Vision Model",
    hint: "Model for vision/alt-caption when a specific vision provider is set.",
    detail: "When inheriting generation, the effective model matches your generation model. Override only when you need a different multimodal model."
  },
  ai_timeout_seconds: {
    label: "Request Timeout (Seconds)",
    hint: "Per HTTP request to the AI provider. Server clamps 10–600. Empty uses the default shown.",
    detail:
      "Long-running image or large-context calls may need a higher timeout. If requests fail with timeout errors, increase this value and save."
  },
  ai_max_retries: {
    label: "Max Retries",
    hint: "Retries for transient AI API failures. Empty uses the default shown.",
    detail: "The server applies a maximum retry count for provider errors that are safe to retry. Very low values may surface more transient failures to the UI."
  },
  store_name: {
    label: "Store Name",
    hint: "Display name used in AI and reporting context. Leave blank to use the name set in Shopify.",
    detail: "Can match your public brand name. Used to ground prompts and labels, not as the Shopify API hostname. When this field is blank the app falls back to your Shopify shop name."
  },
  store_description: {
    label: "Store Description",
    hint: "Short brand/positioning summary used to ground AI prompts. Leave blank to use the description set in Shopify.",
    detail: "Shown in prompt context so generated copy stays on-brand. When blank, the app falls back to the description configured in Shopify (Admin → Settings → Store details → Brand)."
  },
  primary_market_country: {
    label: "Primary Market (Country)",
    hint: "Drives spelling locale, geo modifiers in research, keyword research volumes, and image prompts.",
    detail: "Choose the main country you sell into. This does not change Shopify markets; it informs copy and keyword defaults."
  },
  dashboard_timezone: {
    label: "Dashboard Timezone",
    hint: "IANA timezone for dates and schedules in the dashboard.",
    detail: "Uses the browser’s supported timezone list when available. Pick the zone where your team reads reports."
  },
  shopify_shop: {
    label: "Shopify Shop",
    hint: "Admin API hostname: your-store.myshopify.com (not the public custom domain).",
    detail: "Custom domains go in Custom domain below. The shop field must remain the .myshopify.com domain for Admin API authentication."
  },
  store_custom_domain: {
    label: "Custom Domain (Public URL)",
    hint: "Public site URL for links and interlinking, e.g. yourstore.com. Leave blank to use the .myshopify.com domain.",
    detail:
      "Your public-facing domain for URLs in generated content. The Shopify Shop field above must stay as your .myshopify.com address for API access."
  },
  shopify_api_version: {
    label: "Shopify API Version",
    hint: "Admin API version string, e.g. 2026-01. Must match your app’s configured version.",
    detail: "Shopify releases new API versions quarterly. Using a deprecated version can break Admin API calls; align with your Shopify Partner app settings."
  },
  shopify_client_id: {
    label: "Shopify Client ID",
    hint: "Custom app Client ID from Shopify Admin → Settings → Apps → Develop apps.",
    detail: "Used for OAuth and token refresh for your custom app. Rotate in Shopify if the app credentials are regenerated."
  },
  shopify_client_secret: {
    label: "Shopify Client Secret",
    hint: "Custom app secret; stored locally like other credentials.",
    detail: "Treat like a password. If you rotate the secret in Shopify, update it here and re-authorize if required."
  },
  google_client_id: {
    label: "Google Client ID",
    hint: "OAuth 2.0 Client ID for Search Console and GA4 APIs.",
    detail:
      "Create an OAuth client (Web application) in Google Cloud Console. Add the redirect URI this app shows in docs or deployment notes, then Save settings and use Connect Google."
  },
  google_client_secret: {
    label: "Google Client Secret",
    hint: "Matching secret for the OAuth client above.",
    detail: "Stored locally. After changing Client ID or Secret, save settings and use Reconnect Google to obtain a new refresh token."
  },
  search_console_site: {
    label: "Search Console Site",
    hint: "Property URL or domain after Google is connected, or type manually if no list appears.",
    detail:
      "With Google connected and APIs enabled, saved sites from Search Console appear in the dropdown. Otherwise paste the exact property string from GSC (URL-prefix or domain property format)."
  },
  ga4_property_id: {
    label: "GA4 Property ID",
    hint: "Numeric property id (e.g. 123456789) or pick from the list when the Admin API is enabled.",
    detail:
      "Auto-discovery requires the Google Analytics Admin API enabled on your Google Cloud project. Use the enable link below the field if the dashboard provides one, then refresh the page."
  },
  google_ads_developer_token: {
    label: "Google Ads developer token",
    hint: "From Google Ads → Tools → API Center. Used with the same Google OAuth login after Ads API access is approved.",
    detail:
      "The test calls Google Ads listAccessibleCustomers using your saved token plus your connected Google account. Add the Google Ads API scope to your OAuth client, then Reconnect Google if you connected before Ads was enabled."
  },
  google_ads_customer_id: {
    label: "Google Ads customer ID",
    hint: "Numeric account ID (no dashes). Shown in the Google Ads UI and in the customers/… resource from the API.",
    detail:
      "When a developer token is saved and Google is connected, the list loads from the Ads API. Pick the account this dashboard should use for Ads data."
  },
  google_ads_login_customer_id: {
    label: "Google Ads login customer ID (optional)",
    hint: "Manager (MCC) ID when the account above is a client under that MCC — sent as login-customer-id on API calls.",
    detail:
      "Leave blank when using a standalone Ads account. For MCC → client access, set this to the 10-digit manager ID; the OAuth user must have access to both."
  }
};

export function settingsFieldHintId(fieldId: string): string {
  return `${fieldId}-hint`;
}

export function metaForSettingsField(key: string): SettingsFieldMetaEntry {
  const direct = SETTINGS_FIELD_META[key as SettingsFieldKey];
  if (direct) return direct;
  const label = key
    .replace(/^ai_/, "")
    .replace(/^openai_/, "OpenAI ")
    .replace(/^anthropic_/, "Anthropic ")
    .replace(/^openrouter_/, "OpenRouter ")
    .replace(/^ollama_/, "Ollama ")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  return { label, hint: "" };
}
