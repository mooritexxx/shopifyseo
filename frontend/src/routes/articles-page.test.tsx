import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { PropsWithChildren } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";

import { ArticlesPage } from "./articles-page";

vi.mock("../lib/api", () => ({
  getJson: vi.fn(),
  postJson: vi.fn()
}));

import { getJson } from "../lib/api";

const mockedGetJson = vi.mocked(getJson);

function makeArticle(overrides: Record<string, unknown>) {
  return {
    handle: "sample-article",
    title: "Sample Article",
    blog_handle: "news",
    blog_title: "News",
    published_at: "2026-03-10",
    updated_at: "2026-03-10",
    is_published: true,
    seo_title: "SEO title",
    seo_description: "SEO description",
    body_preview: "",
    score: 50,
    priority: "High",
    reasons: [],
    body_length: 100,
    gsc_clicks: 0,
    gsc_impressions: 0,
    gsc_ctr: 0,
    gsc_position: 0,
    ga4_sessions: 0,
    ga4_views: 0,
    ga4_avg_session_duration: 0,
    index_status: "Indexed",
    index_coverage: "",
    google_canonical: "",
    pagespeed_performance: null,
    pagespeed_desktop_performance: null,
    pagespeed_status: "",
    workflow_status: "",
    workflow_notes: "",
    gsc_segment_flags: { has_dimensional: false },
    ...overrides
  };
}

function renderAt(route: string) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[route]}>{children}</MemoryRouter>
      </QueryClientProvider>
    );
  }
  return render(<ArticlesPage />, { wrapper: Wrapper });
}

describe("ArticlesPage missing_meta focus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps articles missing either SEO field, not only those missing both", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/articles")) {
        return {
          items: [
            makeArticle({ handle: "both", title: "Missing Both", seo_title: "", seo_description: "" }),
            makeArticle({ handle: "title-only", title: "Missing Title Only", seo_title: "   " }),
            makeArticle({
              handle: "desc-only",
              title: "Missing Description Only",
              seo_description: ""
            }),
            makeArticle({ handle: "complete", title: "Complete Meta" })
          ],
          total: 4
        };
      }
      throw new Error(`Unexpected path ${path}`);
    });

    renderAt("/articles?focus=missing_meta");

    expect(await screen.findByText("Missing Both")).toBeInTheDocument();
    expect(screen.getByText("Missing Title Only")).toBeInTheDocument();
    expect(screen.getByText("Missing Description Only")).toBeInTheDocument();
    expect(screen.queryByText("Complete Meta")).not.toBeInTheDocument();
    expect(
      screen.getByText(/Showing articles with SEO title or description missing/)
    ).toBeInTheDocument();
  });

  it("shows every article when the focus filter is absent", async () => {
    mockedGetJson.mockImplementation(async (path: string) => {
      if (path.startsWith("/api/articles")) {
        return {
          items: [
            makeArticle({ handle: "both", title: "Missing Both", seo_title: "", seo_description: "" }),
            makeArticle({ handle: "complete", title: "Complete Meta" })
          ],
          total: 2
        };
      }
      throw new Error(`Unexpected path ${path}`);
    });

    renderAt("/articles");

    expect(await screen.findByText("Missing Both")).toBeInTheDocument();
    expect(screen.getByText("Complete Meta")).toBeInTheDocument();
  });
});
