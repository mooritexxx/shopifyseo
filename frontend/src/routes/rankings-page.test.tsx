// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RankCheck } from "../hooks/use-rankings";

const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  remove: vi.fn(),
  run: vi.fn(),
  estimate: vi.fn(),
  stop: vi.fn(),
  job: null as null | { id: string; status: string; completed: number; keyword_ids: string; cancel_requested: number },
}));
vi.mock("../hooks/use-rankings", () => ({
  useRankings: () => ({
    data: {
      items: [
        {
          id: 1,
          term: "abt vape",
          grp: null,
          target_url: null,
          latest: null,
          trend: [],
          change: null,
          movement: null,
          target_mismatch: false,
          top_competitor: null,
        },
      ],
      job: mocks.job,
      month_used: 0,
      monthly_budget: 250,
      reserved: 0,
    },
  }),
  useRankHistory: () => ({ data: [] }),
  useRankActions: () => ({
    save: { mutateAsync: mocks.save },
    remove: { mutateAsync: mocks.remove },
    run: { mutateAsync: mocks.run },
    stop: { mutateAsync: mocks.stop, isPending: false },
  }),
  estimateRanks: mocks.estimate,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { RankingsPage, rankLabel } from "./rankings-page";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.job = null;
  mocks.stop.mockResolvedValue({});
  mocks.save.mockResolvedValue({});
  mocks.remove.mockResolvedValue({});
  mocks.run.mockResolvedValue({});
});
afterEach(cleanup);
const mount = () =>
  render(
    <MemoryRouter>
      <RankingsPage />
    </MemoryRouter>,
  );

describe("Rankings", () => {
  it("keeps unknown, unverified and checked depth distinct", () => {
    const c = { status: "ok", position: null, checked_depth: 20 } as RankCheck;
    expect(rankLabel(c)).toBe(">20");
    expect(rankLabel({ ...c, status: "error" })).toBe("Unknown · error");
    expect(
      rankLabel({ ...c, status: "unverified", reported_position: 7 }),
    ).toBe("Unverified · reported #7");
  });
  it("adds a keyword with optional metadata", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Add keyword" }));
    await user.type(
      screen.getByLabelText("Keyword", { exact: true }),
      "new term",
    );
    await user.type(screen.getByLabelText("Group (optional)"), "brand");
    await user.click(screen.getByRole("button", { name: "Save keyword" }));
    await waitFor(() =>
      expect(mocks.save).toHaveBeenCalledWith({
        term: "new term",
        target_url: "",
        grp: "brand",
      }),
    );
  });
  it("requires confirmation before removing a keyword", async () => {
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Remove abt vape" }));
    expect(mocks.remove).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: /^Remove keyword$/ }),
    );
    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith(1));
  });
  it("estimates and requires confirmation before spending requests", async () => {
    mocks.estimate.mockResolvedValue({
      keyword_ids: [1],
      searches_base: 5,
      searches_worst_case: 10,
      month_used: 0,
      monthly_budget: 250,
      serpapi_remaining: 1000,
      allowed: true,
      reason: null,
      max_pages: 5,
    });
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Check all" }));
    await screen.findByRole("button", { name: "Start check" });
    expect(mocks.run).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Start check" }));
    await waitFor(() =>
      expect(mocks.run).toHaveBeenCalledWith(
        expect.objectContaining({
          keyword_ids: [1],
          max_pages: 5,
          request_key: expect.any(String),
        }),
      ),
    );
  });
  it("cannot start a check when its estimate exceeds the budget", async () => {
    mocks.estimate.mockResolvedValue({
      keyword_ids: [1],
      searches_base: 5,
      searches_worst_case: 10,
      month_used: 249,
      monthly_budget: 250,
      serpapi_remaining: 1000,
      allowed: false,
      reason: "Monthly budget exceeded",
      max_pages: 5,
    });
    const user = userEvent.setup();
    mount();
    await user.click(screen.getByRole("button", { name: "Check all" }));
    const button = await screen.findByRole("button", { name: "Start check" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(mocks.run).not.toHaveBeenCalled();
  });
});


it("shows limited page coverage and cancellation distinctly", () => {
  const c = { status: "ok", position: null, checked_depth: 0, coverage_complete: 0, pages_checked: 5 } as RankCheck;
  expect(rankLabel(c)).toBe("Not found in 5 pages checked");
  expect(rankLabel({ ...c, status: "error", cancelled: 1 })).toBe("Stopped · unknown");
});

it("uses the same main button to stop a running check", async () => {
  mocks.job = { id: "active-job", status: "running", completed: 1, keyword_ids: "[1,2]", cancel_requested: 0 };
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "Stop check" }));
  expect(mocks.stop).toHaveBeenCalledWith("active-job");
  expect(mocks.run).not.toHaveBeenCalled();
  expect(mocks.estimate).not.toHaveBeenCalled();
  expect((screen.getByRole("button", { name: "Stopping…" }) as HTMLButtonElement).disabled).toBe(true);
});

it("shows durable stopping state after reload", () => {
  mocks.job = { id: "active-job", status: "running", completed: 1, keyword_ids: "[1,2]", cancel_requested: 1 };
  mount();
  expect((screen.getByRole("button", { name: "Stopping…" }) as HTMLButtonElement).disabled).toBe(true);
});

it("restores stop control if the request fails", async () => {
  mocks.job = { id: "active-job", status: "running", completed: 1, keyword_ids: "[1,2]", cancel_requested: 0 };
  mocks.stop.mockRejectedValue(new Error("Network unavailable"));
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "Stop check" }));
  await waitFor(() => expect((screen.getByRole("button", { name: "Stop check" }) as HTMLButtonElement).disabled).toBe(false));
});
