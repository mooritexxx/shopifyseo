import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../test/test-utils";
import { GoogleSignalsPage } from "./google-signals-page";

vi.mock("../lib/api", () => ({
  getJson: vi.fn(),
  postJson: vi.fn()
}));

import { getJson, postJson } from "../lib/api";

const mockedGetJson = vi.mocked(getJson);
const mockedPostJson = vi.mocked(postJson);

const emptyBreakdownSlice = {
  rows: [] as unknown[],
  error: "",
  cache: { label: "Never fetched", kind: "medium", text: "Never fetched", meta: null },
  top_bucket_impressions_pct_vs_prior: null as number | null
};

const emptyGscPropertyBreakdowns = {
  available: false,
  period_mode: "mtd",
  anchor_date: "",
  window: { start_date: "", end_date: "" },
  country: emptyBreakdownSlice,
  device: emptyBreakdownSlice,
  searchAppearance: emptyBreakdownSlice,
  errors: [] as unknown[],
  error: ""
};

describe("GoogleSignalsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("loads connected Google data and saves updated selection", async () => {
    mockedGetJson.mockResolvedValue({
      configured: true,
      connected: true,
      auth_url: "/auth/google/start",
      selected_site: "sc-domain:example.com",
      available_sites: ["sc-domain:example.com", "https://example.com/"],
      ga4_property_id: "123456789",
      summary_period: { start_date: "2026-02-01", end_date: "2026-02-29" },
      gsc_pages: [{ keys: ["/collections/disposables"], clicks: "11", impressions: "250", ctr: 0.04, position: 6.2 }],
      gsc_queries: [{ keys: ["best disposable vape"], clicks: 7, impressions: 90, ctr: 0.07, position: 4.5 }],
      ga4_rows: [{ dimensionValues: [{ value: "/collections/disposables" }], metricValues: [{ value: "42" }] }],
      gsc_cache: { label: "GSC cache", kind: "success", text: "Updated 5 minutes ago", meta: null },
      ga4_cache: { label: "GA4 cache", kind: "success", text: "Updated 10 minutes ago", meta: null },
      gsc_property_breakdowns: {
        ...emptyGscPropertyBreakdowns,
        available: true,
        anchor_date: "2026-03-01",
        window: { start_date: "2026-03-01", end_date: "2026-03-15" },
        country: {
          rows: [{ keys: ["can"], clicks: 1, impressions: 10, ctr: 0.1, position: 5 }],
          error: "",
          cache: { label: "GSC cache", kind: "success", text: "Fresh", meta: null }
        },
        device: emptyBreakdownSlice,
        searchAppearance: emptyBreakdownSlice
      },
      error: ""
    });
    mockedPostJson.mockResolvedValue({ message: "Google settings saved", result: undefined });

    renderWithProviders(<GoogleSignalsPage />);

    expect((await screen.findAllByText("/collections/disposables")).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("combobox"));
    const option = await screen.findByRole("option", { name: "https://example.com/" });
    fireEvent.click(option);

    fireEvent.change(screen.getByLabelText("GA4 property ID"), { target: { value: "987654321" } });
    fireEvent.click(screen.getByText("Save settings"));

    await waitFor(() => {
      expect(mockedPostJson).toHaveBeenCalledWith(
        "/api/google-signals/site",
        expect.anything(),
        { site_url: "https://example.com/", ga4_property_id: "987654321" }
      );
    });
  });

  it("shows the auth call-to-action when Google is not connected", async () => {
    mockedGetJson.mockResolvedValue({
      configured: false,
      connected: false,
      auth_url: "/auth/google/start",
      selected_site: "",
      available_sites: [],
      ga4_property_id: "",
      summary_period: { start_date: "", end_date: "" },
      gsc_pages: [],
      gsc_queries: [],
      ga4_rows: [],
      gsc_cache: { label: "Never fetched", kind: "medium", text: "Never fetched", meta: null },
      ga4_cache: { label: "Never fetched", kind: "medium", text: "Never fetched", meta: null },
      gsc_property_breakdowns: emptyGscPropertyBreakdowns,
      error: "Google is not connected."
    });

    renderWithProviders(<GoogleSignalsPage />);

    expect(await screen.findByText("Google is not connected.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect Google" })).toHaveAttribute("href", "/auth/google/start");
  });

  it("points users to Overview for Search Console performance tables", async () => {
    mockedGetJson.mockResolvedValue({
      configured: true,
      connected: true,
      auth_url: null,
      selected_site: "sc-domain:example.com",
      available_sites: ["sc-domain:example.com"],
      ga4_property_id: "",
      summary_period: { start_date: "2026-02-01", end_date: "2026-02-29" },
      gsc_pages: [],
      gsc_queries: [],
      ga4_rows: [],
      gsc_cache: { label: "GSC", kind: "success", text: "ok", meta: null },
      ga4_cache: { label: "GA4", kind: "success", text: "ok", meta: null },
      gsc_property_breakdowns: {
        ...emptyGscPropertyBreakdowns,
        available: true,
        window: { start_date: "2026-03-01", end_date: "2026-03-15" },
        country: emptyBreakdownSlice,
        device: emptyBreakdownSlice
      },
      error: ""
    });

    renderWithProviders(<GoogleSignalsPage />);

    expect(await screen.findByText("Performance tables moved to Overview")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open Overview/i })).toHaveAttribute("href", "/");
  });
});
