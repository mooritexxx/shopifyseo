import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ProductsPage } from "./products-page";
import { renderWithProviders } from "../test/test-utils";

vi.mock("../lib/api", () => ({
  getJson: vi.fn(),
  postJson: vi.fn()
}));

import { getJson } from "../lib/api";

const mockedGetJson = vi.mocked(getJson);

describe("ProductsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders product rows from the API", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/products")) {
        return {
          items: [
            {
              handle: "sample-product",
              title: "Sample Product",
              vendor: "Vendor",
              status: "active",
              updated_at: "2026-03-10",
              score: 75,
              priority: "High",
              reasons: ["missing SEO title"],
              total_inventory: 10,
              body_length: 200,
              seo_title: "SEO title",
              seo_description: "SEO description",
              gsc_clicks: 2,
              gsc_impressions: 20,
              gsc_ctr: 0.1,
              gsc_position: 5,
              ga4_sessions: 3,
              ga4_views: 4,
              ga4_avg_session_duration: 30,
              index_status: "Not Indexed",
              index_coverage: "",
              google_canonical: "",
              pagespeed_performance: 80,
              pagespeed_desktop_performance: 92,
              pagespeed_status: "fresh",
              workflow_status: "Needs fix",
              workflow_notes: "",
              gsc_segment_flags: { has_dimensional: false }
            }
          ],
          total: 1,
          limit: null,
          offset: 0,
          query: "",
          sort: "score",
          direction: "desc",
          summary: {
            visible_rows: 135,
            high_priority: 42,
            index_issues: 60,
            average_score: 19
          }
        };
      }
      throw new Error(`Unexpected path ${path}`);
    });

    renderWithProviders(<ProductsPage />);

    expect(await screen.findByText("Sample Product")).toBeInTheDocument();
    expect(screen.getByText("Not Indexed")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("135")).toBeInTheDocument();
  });

  it("requests filtered products when the search box changes", async () => {
    mockedGetJson.mockResolvedValue({
      items: [
        {
          handle: "sample-product",
          title: "Sample Product",
          vendor: "Vendor",
          status: "active",
          updated_at: "2026-03-10",
          score: 75,
          priority: "High",
          reasons: ["missing SEO title"],
          total_inventory: 10,
          body_length: 200,
          seo_title: "SEO title",
          seo_description: "SEO description",
          gsc_clicks: 2,
          gsc_impressions: 20,
          gsc_ctr: 0.1,
          gsc_position: 5,
          ga4_sessions: 3,
          ga4_views: 4,
          ga4_avg_session_duration: 30,
          index_status: "Not Indexed",
          index_coverage: "",
          google_canonical: "",
          pagespeed_performance: 80,
          pagespeed_desktop_performance: 90,
          pagespeed_status: "fresh",
          workflow_status: "Needs fix",
          workflow_notes: "",
          gsc_segment_flags: { has_dimensional: false }
        },
        {
          handle: "other-product",
          title: "Other Product",
          vendor: "Other Vendor",
          status: "active",
          updated_at: "2026-03-10",
          score: 42,
          priority: "Medium",
          reasons: [],
          total_inventory: 0,
          body_length: 100,
          seo_title: "",
          seo_description: "",
          gsc_clicks: 0,
          gsc_impressions: 0,
          gsc_ctr: 0,
          gsc_position: 0,
          ga4_sessions: 0,
          ga4_views: 0,
          ga4_avg_session_duration: 0,
          index_status: "",
          index_coverage: "",
          google_canonical: "",
          pagespeed_performance: null,
          pagespeed_desktop_performance: null,
          pagespeed_status: "",
          workflow_status: "Needs fix",
          workflow_notes: "",
          gsc_segment_flags: { has_dimensional: false }
        }
      ],
      total: 2,
      limit: null,
      offset: 0,
      query: "",
      sort: "score",
      direction: "desc",
      summary: {
        visible_rows: 2,
        high_priority: 1,
        index_issues: 1,
        average_score: 59
      }
    });

    renderWithProviders(<ProductsPage />);
    const search = (await screen.findAllByPlaceholderText("Search product name or SEO title"))[0];
    fireEvent.change(search, { target: { value: "other" } });

    expect(mockedGetJson).toHaveBeenCalledWith(
      expect.stringContaining("/api/products?query=other"),
      expect.anything()
    );
  });

  it("requests a new sort when a column header is clicked", async () => {
    mockedGetJson.mockResolvedValue({
      items: [],
      total: 0,
      limit: null,
      offset: 0,
      query: "",
      sort: "gsc_impressions",
      direction: "desc",
      summary: {
        visible_rows: 0,
        high_priority: 0,
        index_issues: 0,
        average_score: 0
      }
    });

    renderWithProviders(<ProductsPage />);

    fireEvent.click((await screen.findAllByRole("button", { name: /clicks/i }))[0]);

    expect(mockedGetJson).toHaveBeenCalledWith(
      expect.stringContaining("sort=gsc_clicks&direction=desc"),
      expect.anything()
    );
  });
});
