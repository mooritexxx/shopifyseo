import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../test/test-utils";
import { SettingsPage } from "./settings-page";

vi.mock("../lib/api", () => ({
  getJson: vi.fn(),
  postJson: vi.fn()
}));

import { getJson, postJson } from "../lib/api";

const mockedGetJson = vi.mocked(getJson);
const mockedPostJson = vi.mocked(postJson);

const DEFAULT_SYNC_SCOPE_READY = {
  shopify: true,
  gsc: true,
  ga4: true,
  index: true,
  pagespeed: true,
  structured: true
} as const;

function getSettingsTab(name: RegExp | string) {
  const [tablist] = screen.getAllByRole("tablist");
  return within(tablist).getByRole("tab", { name });
}

function mockModelsPostJson() {
  mockedPostJson.mockImplementation(async (path) => {
    if (path === "/api/settings/openrouter-models") {
      return { models: ["z-ai/glm-4.5-air:free", "openai/gpt-4.1-mini", "google/gemini-2.0-flash-001"] };
    }
    return { message: "Settings saved", result: undefined };
  });
}

describe("SettingsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads settings and saves updated values", async () => {
    mockedGetJson.mockResolvedValue({
      values: {
        shopify_shop: "my-store.myshopify.com",
        shopify_api_version: "2026-01",
        shopify_client_id: "",
        shopify_client_secret: "",
        google_client_id: "",
        google_client_secret: "",
        search_console_site: "",
        ga4_property_id: "",
        openai_api_key: "",
        gemini_api_key: "",
        anthropic_api_key: "",
        openrouter_api_key: "",
        ollama_api_key: "",
        ollama_base_url: "http://localhost:11434",
        ai_generation_provider: "openai",
        ai_generation_model: "gpt-5-mini",
        ai_sidekick_provider: "",
        ai_sidekick_model: "",
        ai_review_provider: "openai",
        ai_review_model: "gpt-5.4",
        ai_image_provider: "openai",
        ai_image_model: "dall-e-3",
        ai_vision_provider: "",
        ai_vision_model: "",
        ai_prompt_profile: "",
        ai_prompt_version: "",
        ai_timeout_seconds: "60",
        ai_max_retries: "2"
      },
      google_configured: false,
      google_connected: false,
      ai_configured: false,
      auth_url: null,
      available_gsc_sites: [],
      available_ga4_properties: [],
      available_google_ads_customers: [],
      ga4_api_activation_url: "",
      sync_scope_ready: DEFAULT_SYNC_SCOPE_READY
    });
    mockModelsPostJson();

    renderWithProviders(<SettingsPage />);

    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/Data Sources/i));
    await waitFor(() => {
      expect(screen.getByDisplayValue("my-store.myshopify.com")).toBeVisible();
    });
    const shopInput = screen.getByDisplayValue("my-store.myshopify.com");
    fireEvent.change(shopInput, { target: { value: "new-shop.myshopify.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/settings",
        expect.anything(),
        expect.objectContaining({ shopify_shop: "new-shop.myshopify.com" })
      );
    });
    const saveCall = mockedPostJson.mock.calls.find((call) => call[0] === "/api/settings");
    const payload = saveCall?.[2] as Record<string, string> | undefined;
    expect(payload).toBeDefined();
    expect(payload).toMatchObject({
      shopify_shop: "new-shop.myshopify.com",
      ai_timeout_seconds: "60",
      ai_generation_provider: "openai",
      ai_generation_model: "gpt-5-mini",
      google_client_id: ""
    });
  });

  it("triggers the generation AI connection test", async () => {
    mockedGetJson.mockResolvedValue({
      values: {
        shopify_shop: "my-store.myshopify.com",
        shopify_api_version: "2026-01",
        shopify_client_id: "",
        shopify_client_secret: "",
        google_client_id: "",
        google_client_secret: "",
        search_console_site: "",
        ga4_property_id: "",
        openai_api_key: "",
        gemini_api_key: "",
        anthropic_api_key: "",
        openrouter_api_key: "",
        ollama_api_key: "",
        ollama_base_url: "http://localhost:11434",
        ai_generation_provider: "openai",
        ai_generation_model: "gpt-5-mini",
        ai_sidekick_provider: "",
        ai_sidekick_model: "",
        ai_review_provider: "openai",
        ai_review_model: "gpt-5.4",
        ai_image_provider: "openai",
        ai_image_model: "dall-e-3",
        ai_vision_provider: "",
        ai_vision_model: "",
        ai_prompt_profile: "",
        ai_prompt_version: "",
        ai_timeout_seconds: "60",
        ai_max_retries: "2"
      },
      google_configured: false,
      google_connected: false,
      ai_configured: false,
      auth_url: null,
      available_gsc_sites: [],
      available_ga4_properties: [],
      available_google_ads_customers: [],
      ga4_api_activation_url: "",
      sync_scope_ready: DEFAULT_SYNC_SCOPE_READY
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free", "openai/gpt-4.1-mini"] };
      }
      return { message: "AI connection successful", result: { ok: true } };
    });

    renderWithProviders(<SettingsPage />);
    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/AI Models/i));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test generation" })).toBeVisible();
    });
    await user.click(screen.getByRole("button", { name: "Test generation" }));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/settings/ai-test",
        expect.anything(),
        expect.objectContaining({
          target: "generation",
          ai_generation_provider: "openai",
          ai_generation_model: "gpt-5-mini",
          ai_review_provider: "openai",
          ai_review_model: "gpt-5.4"
        })
      );
    });
  });

  it("triggers the image model test", async () => {
    mockedGetJson.mockResolvedValue({
      values: {
        shopify_shop: "my-store.myshopify.com",
        shopify_api_version: "2026-01",
        shopify_client_id: "",
        shopify_client_secret: "",
        google_client_id: "",
        google_client_secret: "",
        search_console_site: "",
        ga4_property_id: "",
        openai_api_key: "",
        gemini_api_key: "x",
        anthropic_api_key: "",
        openrouter_api_key: "sk-or-test",
        ollama_api_key: "",
        ollama_base_url: "http://localhost:11434",
        ai_generation_provider: "openai",
        ai_generation_model: "gpt-5-mini",
        ai_sidekick_provider: "",
        ai_sidekick_model: "",
        ai_review_provider: "openai",
        ai_review_model: "gpt-5.4",
        ai_image_provider: "gemini",
        ai_image_model: "gemini-3.1-flash-image-preview",
        ai_vision_provider: "",
        ai_vision_model: "",
        ai_prompt_profile: "",
        ai_prompt_version: "",
        ai_timeout_seconds: "60",
        ai_max_retries: "2"
      },
      google_configured: false,
      google_connected: false,
      ai_configured: false,
      auth_url: null,
      available_gsc_sites: [],
      available_ga4_properties: [],
      available_google_ads_customers: [],
      ga4_api_activation_url: "",
      sync_scope_ready: DEFAULT_SYNC_SCOPE_READY
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free", "google/gemini-2.5-flash-image"] };
      }
      if (path === "/api/settings/image-model-test") {
        return {
          message: "Sample image generated successfully",
          result: {
            mime_type: "image/png",
            image_base64: "iVBORw0KGgo=",
            _meta: {
              target: "image",
              provider: "openrouter",
              model: "google/gemini-2.5-flash-image",
              bytes: 10
            }
          }
        };
      }
      return { message: "ok", result: undefined };
    });

    renderWithProviders(<SettingsPage />);
    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/AI Models/i));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test model" })).toBeVisible();
    });
    await user.click(screen.getByRole("button", { name: "Test model" }));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/settings/image-model-test",
        expect.anything(),
        expect.objectContaining({
          ai_image_provider: "gemini",
          ai_image_model: "gemini-3.1-flash-image-preview"
        })
      );
    });
    expect(await screen.findByRole("img", { name: /Sample generated/i })).toBeInTheDocument();
  });

  it("triggers the vision model test", async () => {
    mockedGetJson.mockResolvedValue({
      values: {
        shopify_shop: "my-store.myshopify.com",
        shopify_api_version: "2026-01",
        shopify_client_id: "",
        shopify_client_secret: "",
        google_client_id: "",
        google_client_secret: "",
        search_console_site: "",
        ga4_property_id: "",
        openai_api_key: "sk-test",
        gemini_api_key: "",
        anthropic_api_key: "",
        openrouter_api_key: "sk-or-test",
        ollama_api_key: "",
        ollama_base_url: "http://localhost:11434",
        ai_generation_provider: "openai",
        ai_generation_model: "gpt-4.1-mini",
        ai_sidekick_provider: "",
        ai_sidekick_model: "",
        ai_review_provider: "openai",
        ai_review_model: "gpt-5.4",
        ai_image_provider: "openai",
        ai_image_model: "dall-e-3",
        ai_vision_provider: "",
        ai_vision_model: "",
        ai_prompt_profile: "",
        ai_prompt_version: "",
        ai_timeout_seconds: "60",
        ai_max_retries: "2"
      },
      google_configured: false,
      google_connected: false,
      ai_configured: false,
      auth_url: null,
      available_gsc_sites: [],
      available_ga4_properties: [],
      available_google_ads_customers: [],
      ga4_api_activation_url: "",
      sync_scope_ready: DEFAULT_SYNC_SCOPE_READY
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free"] };
      }
      if (path === "/api/settings/vision-model-test") {
        return {
          message: "Vision model test succeeded",
          result: {
            ok: true,
            suggested_alt: "Solid blue",
            _meta: { target: "vision", provider: "openrouter", model: "google/gemini-2.0-flash-001" }
          }
        };
      }
      return { message: "ok", result: undefined };
    });

    renderWithProviders(<SettingsPage />);
    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/AI Models/i));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Test vision" })).toBeVisible();
    });
    await user.click(screen.getByRole("button", { name: "Test vision" }));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/settings/vision-model-test",
        expect.anything(),
        expect.objectContaining({
          ai_generation_provider: "openai",
          ai_generation_model: "gpt-4.1-mini"
        })
      );
    });
    expect(await screen.findByText("Solid blue")).toBeInTheDocument();
  });

  it("lists all AI vendors as generation provider options", async () => {
    mockedGetJson.mockResolvedValue({
      values: {
        shopify_shop: "",
        shopify_api_version: "",
        shopify_client_id: "",
        shopify_client_secret: "",
        google_client_id: "",
        google_client_secret: "",
        search_console_site: "",
        ga4_property_id: "",
        openai_api_key: "",
        gemini_api_key: "",
        anthropic_api_key: "",
        openrouter_api_key: "",
        ollama_api_key: "",
        ollama_base_url: "http://localhost:11434",
        ai_generation_provider: "openai",
        ai_generation_model: "gpt-5-mini",
        ai_sidekick_provider: "",
        ai_sidekick_model: "",
        ai_review_provider: "openai",
        ai_review_model: "gpt-5.4",
        ai_image_provider: "openai",
        ai_image_model: "dall-e-3",
        ai_vision_provider: "",
        ai_vision_model: "",
        ai_prompt_profile: "",
        ai_prompt_version: "",
        ai_timeout_seconds: "60",
        ai_max_retries: "2"
      },
      google_configured: false,
      google_connected: false,
      ai_configured: false,
      auth_url: null,
      available_gsc_sites: [],
      available_ga4_properties: [],
      available_google_ads_customers: [],
      ga4_api_activation_url: "",
      sync_scope_ready: DEFAULT_SYNC_SCOPE_READY
    });
    mockModelsPostJson();

    renderWithProviders(<SettingsPage />);
    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/AI Models/i));
    await waitFor(() => {
      expect(screen.getAllByRole("combobox").length).toBeGreaterThan(0);
    });

    const comboboxes = screen.getAllByRole("combobox");
    await user.click(comboboxes[0]);
    expect(await screen.findByRole("option", { name: "OpenRouter" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "OpenAI" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Gemini" })).toBeInTheDocument();
  });

  const defaultSettingsApiPayload = {
    values: {
      store_name: "",
      primary_market_country: "",
      dashboard_timezone: "",
      store_custom_domain: "",
      shopify_shop: "my-store.myshopify.com",
      shopify_api_version: "2026-01",
      shopify_client_id: "",
      shopify_client_secret: "",
      google_client_id: "",
      google_client_secret: "",
      search_console_site: "",
      ga4_property_id: "",
      openai_api_key: "",
      openai_model: "",
      gemini_api_key: "",
      anthropic_api_key: "",
      dataforseo_api_login: "",
      dataforseo_api_password: "",
      openrouter_api_key: "",
      ollama_api_key: "",
      ollama_base_url: "http://localhost:11434",
      ai_generation_provider: "openai",
      ai_generation_model: "gpt-5-mini",
      ai_sidekick_provider: "",
      ai_sidekick_model: "",
      ai_review_provider: "openai",
      ai_review_model: "gpt-5.4",
      ai_image_provider: "openai",
      ai_image_model: "dall-e-3",
      ai_vision_provider: "",
      ai_vision_model: "",
      ai_timeout_seconds: "60",
      ai_max_retries: "2",
      google_ads_developer_token: "",
      google_ads_customer_id: "",
      google_ads_login_customer_id: ""
    },
    google_configured: false,
    google_connected: false,
    ai_configured: false,
    auth_url: null,
    available_gsc_sites: [] as string[],
    available_ga4_properties: [] as { property_id: string; display_name: string; account_name: string }[],
    available_google_ads_customers: [] as { customer_id: string; descriptive_name: string; resource_name: string }[],
    ga4_api_activation_url: "",
    sync_scope_ready: DEFAULT_SYNC_SCOPE_READY
  };

  it("opens Data sources tab when the URL contains ?tab=data-sources", async () => {
    mockedGetJson.mockResolvedValue(defaultSettingsApiPayload);
    mockModelsPostJson();

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
    });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/settings?tab=data-sources"]}>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    expect(await screen.findByText(/Shop identity, Shopify Admin API/i)).toBeInTheDocument();
  });

  it("shows show/hide controls for secret fields on Data sources", async () => {
    mockedGetJson.mockResolvedValue(defaultSettingsApiPayload);
    mockModelsPostJson();

    renderWithProviders(<SettingsPage />);
    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/Data Sources/i));
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: /Show value/i }).length).toBeGreaterThan(0);
    });
  });
});
