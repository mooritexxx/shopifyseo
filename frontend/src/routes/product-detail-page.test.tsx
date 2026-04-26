import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../test/test-utils";
import { ProductDetailPage } from "./product-detail-page";

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useParams: () => ({ handle: "sample-product" })
  };
});

vi.mock("../lib/api", () => ({
  getJson: vi.fn(),
  postJson: vi.fn()
}));

import { getJson, postJson } from "../lib/api";

const mockedGetJson = vi.mocked(getJson);
const mockedPostJson = vi.mocked(postJson);

describe("ProductDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(window, "open").mockImplementation(() => null);
  });

  it("loads product detail with draft fields and posts to generate-ai (no recommendation modal)", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path === "/api/ai-status") {
        return {
          running: false,
          scope: "",
          stage: "idle",
          total: 0,
          done: 0,
          current: "",
          successes: 0,
          failures: 0,
          last_error: "",
          last_result: null
        };
      }
      return {
        product: {
          handle: "sample-product",
          title: "Sample Product",
          vendor: "Vendor",
          status: "active",
          updated_at: "2026-03-10"
        },
        draft: {
          title: "Sample Product",
          seo_title: "Original SEO title",
          seo_description: "Original description",
          body_html: "<p>Original body</p>",
          tags: "tag-a, tag-b",
          workflow_status: "Needs fix",
          workflow_notes: ""
        },
        workflow: {
          status: "Needs fix",
          notes: "",
          updated_at: null
        },
        recommendation: {
          summary: "Use stronger intent in the search snippet.",
          status: "success",
          model: "gpt-5.4",
          created_at: "2026-03-10T10:00:00Z",
          error_message: "",
          // Match draft so on-load auto-apply does not overwrite (generation flow is tested elsewhere)
          details: {
            seo_title: "Original SEO title",
            seo_description: "Original description",
            body: "<p>Original body</p>",
            tags: ["tag-a", "tag-b"],
            internal_links: []
          }
        },
        recommendation_history: [],
        signal_cards: [
          {
            label: "Index",
            value: "Indexed",
            sublabel: "Seen in Google",
            updated_at: "2026-03-10",
            step: "index",
            action_label: "Request indexing",
            action_href: "https://search.google.com/search-console"
          }
        ],
        collections: [],
        variants: [],
        metafields: [],
        gsc_queries: [],
        gsc_segment_summary: null,
        opportunity: {
          priority: "High",
          score: 82,
          reasons: ["Missing strong snippet"],
          handle: "sample-product",
          title: "Sample Product",
          object_type: "product",
          gsc_impressions: 0,
          gsc_clicks: 0,
          gsc_position: 0,
          ga4_sessions: 0,
          pagespeed_performance: 90
        }
      };
    });
    mockedPostJson.mockResolvedValue({
      message: "Started",
      state: null,
      result: null,
      steps: null
    });

    renderWithProviders(<ProductDetailPage />);

    expect(await screen.findByDisplayValue("Sample Product")).toBeInTheDocument();
    expect(screen.getByLabelText("SEO title")).toHaveValue("Original SEO title");
    expect(screen.getByText("Product details")).toBeInTheDocument();
    expect(screen.getByText("Search preview")).toBeInTheDocument();
    expect(screen.queryByText("Recommendation status")).not.toBeInTheDocument();
    expect(screen.getByText("Request indexing")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Generate AI/i }));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith("/api/products/sample-product/generate-ai", expect.anything());
    });
  });

  it("renders regenerate buttons for each field", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path === "/api/ai-status") {
        return {
          running: false,
          scope: "",
          stage: "idle",
          total: 0,
          done: 0,
          current: "",
          successes: 0,
          failures: 0,
          last_error: "",
          last_result: null
        };
      }
      return {
        product: { handle: "sample-product", title: "Sample", vendor: "V", status: "active", updated_at: "2026-03-10" },
        draft: { title: "Sample", seo_title: "Title", seo_description: "Desc", body_html: "<p>Body</p>", tags: "", workflow_status: "Needs fix", workflow_notes: "" },
        workflow: { status: "Needs fix", notes: "", updated_at: null },
        recommendation: { summary: "S", status: "success", model: "gpt-5.4", created_at: null, error_message: "", details: { seo_title: "AI Title", seo_description: "AI Desc", body: "<p>AI Body</p>", tags: [], internal_links: [] } },
        recommendation_history: [],
        signal_cards: [],
        collections: [],
        variants: [],
        metafields: [],
        gsc_queries: [],
        gsc_segment_summary: null,
        opportunity: { priority: "High", score: 50, reasons: [], handle: "sample-product", title: "Sample", object_type: "product", gsc_impressions: 0, gsc_clicks: 0, gsc_position: 0, ga4_sessions: 0, pagespeed_performance: 90 }
      };
    });

    renderWithProviders(<ProductDetailPage />);
    expect(await screen.findByLabelText("SEO title")).toBeInTheDocument();
    expect(screen.getAllByText(/Regenerate/).length).toBeGreaterThanOrEqual(3);
  });

  it("opens cached Search Console inspection link without refreshing first", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path === "/api/ai-status") {
        return {
          running: false,
          scope: "",
          stage: "idle",
          total: 0,
          done: 0,
          current: "",
          successes: 0,
          failures: 0,
          last_error: "",
          last_result: null
        };
      }
      return {
        product: { handle: "sample-product", title: "Sample", vendor: "V", status: "active", updated_at: "2026-03-10" },
        draft: { title: "Sample", seo_title: "Title", seo_description: "Desc", body_html: "<p>Body</p>", tags: "", workflow_status: "Needs fix", workflow_notes: "" },
        workflow: { status: "Needs fix", notes: "", updated_at: null },
        recommendation: { summary: "S", status: "success", model: "gpt-5.4", created_at: null, error_message: "", details: { seo_title: "Title", seo_description: "Desc", body: "<p>Body</p>", tags: [], internal_links: [] } },
        recommendation_history: [],
        signal_cards: [
          {
            label: "Index",
            value: "Indexed",
            sublabel: "Seen in Google",
            updated_at: "2026-03-10",
            step: "index",
            action_label: "Request indexing",
            action_href: "https://search.google.com/search-console/inspect"
          }
        ],
        collections: [],
        variants: [],
        metafields: [],
        gsc_queries: [],
        gsc_segment_summary: null,
        opportunity: { priority: "High", score: 50, reasons: [], handle: "sample-product", title: "Sample", object_type: "product", gsc_impressions: 0, gsc_clicks: 0, gsc_position: 0, ga4_sessions: 0, pagespeed_performance: 90 }
      };
    });

    renderWithProviders(<ProductDetailPage />);
    fireEvent.click(await screen.findByText("Request indexing"));

    expect(window.open).toHaveBeenCalledWith(
      "https://search.google.com/search-console/inspect",
      "_blank",
      "noopener,noreferrer"
    );
    expect(mockedPostJson).not.toHaveBeenCalledWith(
      "/api/products/sample-product/inspection-link",
      expect.anything()
    );
  });

  it("fetches a fresh inspection link when only the generic Search Console URL is cached", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path === "/api/ai-status") {
        return {
          running: false,
          scope: "",
          stage: "idle",
          total: 0,
          done: 0,
          current: "",
          successes: 0,
          failures: 0,
          last_error: "",
          last_result: null
        };
      }
      return {
        product: { handle: "sample-product", title: "Sample", vendor: "V", status: "active", updated_at: "2026-03-10" },
        draft: { title: "Sample", seo_title: "Title", seo_description: "Desc", body_html: "<p>Body</p>", tags: "", workflow_status: "Needs fix", workflow_notes: "" },
        workflow: { status: "Needs fix", notes: "", updated_at: null },
        recommendation: { summary: "S", status: "success", model: "gpt-5.4", created_at: null, error_message: "", details: { seo_title: "Title", seo_description: "Desc", body: "<p>Body</p>", tags: [], internal_links: [] } },
        recommendation_history: [],
        signal_cards: [
          {
            label: "Index",
            value: "Unknown",
            sublabel: "No index detail",
            updated_at: null,
            step: "index",
            action_label: "Request indexing",
            action_href: "https://search.google.com/search-console?resource_id=sc-domain%3Aexample.com"
          }
        ],
        collections: [],
        variants: [],
        metafields: [],
        gsc_queries: [],
        gsc_segment_summary: null,
        opportunity: { priority: "High", score: 50, reasons: [], handle: "sample-product", title: "Sample", object_type: "product", gsc_impressions: 0, gsc_clicks: 0, gsc_position: 0, ga4_sessions: 0, pagespeed_performance: 90 }
      };
    });
    mockedPostJson.mockResolvedValue({
      href: "https://search.google.com/search-console/inspect?resource_id=sc-domain%3Aexample.com&id=https%3A%2F%2Fexample.com%2Fproducts%2Fsample-product"
    });

    renderWithProviders(<ProductDetailPage />);
    fireEvent.click(await screen.findByText("Request indexing"));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/products/sample-product/inspection-link",
        expect.anything()
      );
    });
    expect(window.open).toHaveBeenCalledWith(
      "https://search.google.com/search-console/inspect?resource_id=sc-domain%3Aexample.com&id=https%3A%2F%2Fexample.com%2Fproducts%2Fsample-product",
      "_blank",
      "noopener,noreferrer"
    );
  });

  it("saves the edited draft", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path === "/api/ai-status") {
        return {
          running: false,
          scope: "",
          stage: "idle",
          total: 0,
          done: 0,
          current: "",
          successes: 0,
          failures: 0,
          last_error: "",
          last_result: null
        };
      }
      return {
        product: {
          handle: "sample-product",
          title: "Sample Product",
          vendor: "Vendor",
          status: "active",
          updated_at: "2026-03-10"
        },
        draft: {
          title: "Sample Product",
          seo_title: "Original SEO title",
          seo_description: "Original description",
          body_html: "<p>Original body</p>",
          tags: "tag-a, tag-b",
          workflow_status: "Needs fix",
          workflow_notes: ""
        },
        workflow: {
          status: "Needs fix",
          notes: "",
          updated_at: null
        },
        recommendation: {
          summary: "Summary",
          status: "success",
          model: "gpt-5.4",
          created_at: null,
          error_message: "",
          details: {}
        },
        recommendation_history: [],
        signal_cards: [],
        collections: [],
        variants: [],
        metafields: [],
        gsc_queries: [],
        gsc_segment_summary: null,
        opportunity: {
          priority: "High",
          score: 82,
          reasons: ["Missing strong snippet"],
          handle: "sample-product",
          title: "Sample Product",
          object_type: "product",
          gsc_impressions: 0,
          gsc_clicks: 0,
          gsc_position: 0,
          ga4_sessions: 0,
          pagespeed_performance: 90
        }
      };
    });
    mockedPostJson.mockResolvedValue({ message: "Saved", result: undefined });

    renderWithProviders(<ProductDetailPage />);

    const titleInputs = await screen.findAllByDisplayValue("Sample Product");
    fireEvent.change(titleInputs[0], { target: { value: "Updated Product Title" } });
    const saveButtons = screen.getAllByText("Save to Shopify");
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/products/sample-product/update?gsc_period=mtd",
        expect.anything(),
        expect.objectContaining({ title: "Updated Product Title" })
      );
    });
  });
});
