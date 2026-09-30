import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { CompletionBar, IndexingSummary, NeedsAttention } from "./overview-cards";

afterEach(cleanup);
const wrap = (ui: React.ReactNode) => render(<MemoryRouter>{ui}</MemoryRouter>);

describe("Overview health summaries", () => {
  it("keeps unknown and review states separate from not indexed", () => {
    render(<IndexingSummary total={100} indexed={70} not_indexed={15} needs_review={5} unknown={10} />);
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("Indexed: 70, Not indexed: 15, Needs review: 5, Unknown: 10");
    expect(screen.getByText("70.0% indexed")).toBeTruthy();
  });
  it("does not display invalid percentages for an empty catalog", () => {
    render(<IndexingSummary total={0} indexed={0} not_indexed={0} needs_review={0} unknown={0} />);
    expect(screen.getByText("No inspection data yet")).toBeTruthy();
    expect(screen.queryByText(/NaN|Infinity/)).toBeNull();
  });
  it("links incomplete coverage to the review destination", () => {
    wrap(<CompletionBar label="Pages" complete={11} total={16} missing={5} href="/pages" issueHref="/pages?focus=missing_meta" />);
    expect(screen.getByRole("link", { name: "5 missing →" }).getAttribute("href")).toBe("/pages?focus=missing_meta");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("68.75");
  });
  it("shows active issues in count order without adding overlapping counts", () => {
    wrap(<NeedsAttention hasCatalog items={[
      { label: "Missing metadata", count: 2, href: "/products?focus=missing_meta", action: "Review" },
      { label: "Pages", count: 0, href: "/pages", action: "Review" },
      { label: "Short descriptions", count: 5, href: "/products?focus=thin_body", action: "Improve" }
    ]} />);
    const links = screen.getAllByRole("link");
    expect(links).toHaveLength(2);
    expect(links[0].getAttribute("href")).toBe("/products?focus=thin_body");
  });
  it("distinguishes an unsynced catalog from one with no detected issues", () => {
    const view = wrap(<NeedsAttention hasCatalog={false} items={[]} />);
    expect(screen.getByText(/Sync your catalog to check/)).toBeTruthy();
    view.rerender(<MemoryRouter><NeedsAttention hasCatalog items={[]} /></MemoryRouter>);
    expect(screen.getByText(/No missing metadata or short product descriptions found/)).toBeTruthy();
  });
});
