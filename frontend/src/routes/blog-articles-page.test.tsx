import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import { expect, it, vi } from "vitest";
import { BlogArticlesPage } from "./blog-articles-page";

vi.mock("../lib/api", () => ({ getJson: vi.fn(async () => ({
  blog: { title: "Canada", handle: "canada" }, total: 1,
  items: [{ title: "A guide", handle: "guide & tips", seo_title: "Guide", body_preview: "Preview", is_published: true, published_at: "2026-09-30" }]
})) }));

function ArticleDestination() {
  const { blog, article } = useParams();
  return <h1>Editing {blog}: {article}</h1>;
}

it("opens the article in its owning blog instead of returning to the blog list", async () => {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={["/blogs/canada"]}>
      <Routes>
        <Route path="/blogs/:blogHandle" element={<BlogArticlesPage />} />
        <Route path="/articles/:blog/:article" element={<ArticleDestination />} />
      </Routes>
    </MemoryRouter>
  </QueryClientProvider>);
  const link = await screen.findByRole("link", { name: "A guide" });
  expect(link).toHaveAttribute("href", "/articles/canada/guide%20%26%20tips");
  await userEvent.click(link);
  expect(await screen.findByRole("heading", { name: "Editing canada: guide & tips" })).toBeVisible();
});
