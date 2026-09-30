import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { OpportunitiesPage } from "./opportunities-page";

const item = { id: "1", query: "First query", page_url: "https://example.test/products/example", page_type: "Product", object_type: "product", object_handle: "example", impressions: 100, clicks: 1, ctr: 1, position: 12, opportunity_score: 70, suggested_action: "Review metadata", content_type: "product" };
afterEach(() => vi.unstubAllGlobals());
function mount() {
  return render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><MemoryRouter><OpportunitiesPage /></MemoryRouter></QueryClientProvider>);
}
it("keeps Previous available on the last page and returns to the first page", async () => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    let data: unknown;
    if (url.includes("/tasks")) data = [];
    else if (url.includes("/stats")) data = { total_queries: 51, striking_distance: 10, quick_wins: 5, high_impressions_low_ctr: 2, by_page_type: {} };
    else { const offset = Number(new URL(url, "http://local.test").searchParams.get("offset")); data = { items: [{...item, query: offset ? "Last query" : "First query"}], total: 51, limit: 50, offset, has_more: offset === 0 }; }
    return {ok:true,text:async()=>JSON.stringify({ok:true,data})};
  }));
  const user = userEvent.setup(); mount();
  await screen.findByText("First query");
  await user.click(screen.getByRole("button",{name:"Next"}));
  await screen.findByText("Last query");
  expect(screen.getByRole("button",{name:"Next"})).toBeDisabled();
  await user.click(screen.getByRole("button",{name:"Previous"}));
  await waitFor(()=>expect(screen.getByText("First query")).toBeVisible());
});
