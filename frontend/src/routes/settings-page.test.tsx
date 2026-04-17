import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

function getSettingsTab(name: RegExp | string) {
  const [tablist] = screen.getAllByRole("tablist");
  return within(tablist).getByRole("tab", { name });
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
      auth_url: null
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/gemini-models") {
        return { models: ["gemini-2.0-flash", "gemini-2.5-flash"] };
      }
      if (path === "/api/settings/anthropic-models") {
        return { models: ["claude-opus-4-6", "claude-sonnet-4-20250514"] };
      }
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free", "openai/gpt-4.1-mini"] };
      }
      if (path === "/api/settings/ollama-models") {
        return { models: ["llama3.2:latest"] };
      }
      return { message: "Settings saved", result: undefined };
    });

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
      auth_url: null
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/gemini-models") {
        return { models: ["gemini-2.0-flash", "gemini-2.5-flash"] };
      }
      if (path === "/api/settings/anthropic-models") {
        return { models: ["claude-opus-4-6", "claude-sonnet-4-20250514"] };
      }
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free", "openai/gpt-4.1-mini"] };
      }
      if (path === "/api/settings/ollama-models") {
        return { models: ["llama3.2:latest"] };
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
        openrouter_api_key: "",
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
      auth_url: null
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/gemini-models") {
        return { models: ["gemini-2.0-flash", "gemini-3.1-flash-image-preview"] };
      }
      if (path === "/api/settings/anthropic-models") {
        return { models: ["claude-opus-4-6"] };
      }
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free"] };
      }
      if (path === "/api/settings/ollama-models") {
        return { models: ["llama3.2:latest"] };
      }
      if (path === "/api/settings/image-model-test") {
        return {
          message: "Sample image generated successfully",
          result: {
            mime_type: "image/png",
            image_base64: "iVBORw0KGgo=",
            _meta: { target: "image", provider: "gemini", model: "gemini-3.1-flash-image-preview", bytes: 10 }
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
        openrouter_api_key: "",
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
      auth_url: null
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/gemini-models") {
        return { models: ["gemini-2.0-flash"] };
      }
      if (path === "/api/settings/anthropic-models") {
        return { models: ["claude-opus-4-6"] };
      }
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free"] };
      }
      if (path === "/api/settings/ollama-models") {
        return { models: ["llama3.2:latest"] };
      }
      if (path === "/api/settings/vision-model-test") {
        return {
          message: "Vision model test succeeded",
          result: {
            ok: true,
            suggested_alt: "Solid blue",
            _meta: { target: "vision", provider: "openai", model: "gpt-4.1-mini" }
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

  it("updates model dropdowns when providers change", async () => {
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
      auth_url: null
    });
    mockedPostJson.mockImplementation(async (path) => {
      if (path === "/api/settings/gemini-models") {
        return { models: ["gemini-2.0-flash", "gemini-2.5-flash"] };
      }
      if (path === "/api/settings/anthropic-models") {
        return { models: ["claude-opus-4-6", "claude-sonnet-4-20250514"] };
      }
      if (path === "/api/settings/openrouter-models") {
        return { models: ["z-ai/glm-4.5-air:free", "openai/gpt-4.1-mini"] };
      }
      if (path === "/api/settings/ollama-models") {
        return { models: ["gemma3:4b", "llama3.2:latest"] };
      }
      return { message: "ok", result: undefined };
    });

    renderWithProviders(<SettingsPage />);
    const user = userEvent.setup();
    await screen.findByRole("tablist");
    await user.click(getSettingsTab(/AI Models/i));
    await waitFor(() => {
      expect(screen.getAllByRole("combobox").length).toBeGreaterThan(0);
    });

    const comboboxes = screen.getAllByRole("combobox");
    fireEvent.click(comboboxes[0]);
    fireEvent.click(await screen.findByRole("option", { name: "anthropic" }));

    await waitFor(() => {
      expect(screen.getAllByRole("combobox")[1]).toHaveTextContent("claude-opus-4-6");
    });

    fireEvent.click(screen.getAllByRole("combobox")[4]);
    fireEvent.click(await screen.findByRole("option", { name: "ollama" }));

    await waitFor(() => {
      expect(screen.getAllByRole("combobox")[4]).toHaveTextContent("ollama");
      expect(screen.getAllByRole("combobox")[5].textContent?.trim().length).toBeGreaterThan(0);
    });
  });
});
