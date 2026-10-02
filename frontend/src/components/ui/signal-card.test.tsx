import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SignalCard } from "./signal-card";
afterEach(cleanup);
it("preserves refresh and indexing actions with accessible labels", () => {
  const refresh = vi.fn(); const action = vi.fn();
  const props = { signal: { label: "Index", value: "Not Indexed", sublabel: "Coverage", step: "index", action_label: "Request indexing" }, onRefresh: refresh, onAction: action };
  const view = render(<SignalCard {...props} />);
  expect(screen.getByText("Not Indexed")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Refresh Index" }));
  fireEvent.click(screen.getByRole("button", { name: "Request indexing" }));
  expect(refresh).toHaveBeenCalledOnce(); expect(action).toHaveBeenCalledOnce();
  view.rerender(<SignalCard {...props} isRefreshing />);
  expect(screen.getByRole("button", { name: "Refresh Index" }).hasAttribute("disabled")).toBe(true);
});
it("preserves an unavailable speed score instead of turning it into zero", () => {
  render(<SignalCard signal={{ label: "PageSpeed", value: "No score", sublabel: "Never fetched", step: "speed" }} />);
  expect(screen.getByText("No score")).toBeTruthy();
  expect(screen.getByText("Never fetched")).toBeTruthy();
});
it("distinguishes crawl evidence from inspection time and retains recrawl action", () => {
  const onAction = vi.fn();
  render(<SignalCard signal={{ label: "Index", value: "Not Indexed", step: "index", updated_at: 1790967600,
    sublabel: "Blocked by robots.txt · crawled Sep 15 (17d ago) · inspected Oct 2",
    badge: "Stale: crawl predates current robots.txt", flag_reason: "The current file allows this URL.",
    action_label: "Request indexing" }} onAction={onAction} />);
  expect(screen.getByText(/crawled Sep 15.*inspected Oct 2/)).toBeTruthy();
  expect(screen.getByText("Stale: crawl predates current robots.txt").title).toContain("allows this URL");
  expect(screen.queryByText(/^Updated:/)).toBeNull();
  fireEvent.click(screen.getByRole("button", {name: "Request indexing"}));
  expect(onAction).toHaveBeenCalledOnce();
});
