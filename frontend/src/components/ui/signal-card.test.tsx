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
