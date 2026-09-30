// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { LinkPreview, LinkSuggestion } from "../hooks/use-internal-links";

const state = vi.hoisted(() => ({ data: null as LinkPreview | null, loading: false, error: null as Error | null }));
vi.mock("../hooks/use-internal-links", () => ({ useLinkPreview: () => ({ data: state.data, isLoading: state.loading, isFetching: state.loading, error: state.error }) }));
import { PreviewDialog, SuggestionRow } from "./internal-links-page";

beforeEach(() => {
  state.data = { suggestion_id: 1, old_html: "<p>Text</p>", new_html: "<p>Text</p>", text_diff: "", html_diff: '-<p>Text</p>\n+<p><a href="/target">Text</a></p>', allowed: true, preview_token: "signed-preview", target_url: "/target", anchor_phrase: "Text" };
  state.loading = false; state.error = null;
});
afterEach(cleanup);

it("shows escaped HTML changes and sends the approved preview token only on Confirm", async () => {
  const confirm = vi.fn();
  render(<PreviewDialog suggestionId={1} onClose={vi.fn()} onConfirmApply={confirm} applying={false} />);
  expect(confirm).not.toHaveBeenCalled();
  expect(screen.getByText(/Existing wording is unchanged/)).toBeTruthy();
  expect(document.querySelector('a[href="/target"]')).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Confirm and apply" }));
  expect(confirm).toHaveBeenCalledWith("signed-preview");
});

it.each(["loading", "error", "blocked"])("cannot confirm a %s preview", async (kind) => {
  if (kind === "loading") state.loading = true;
  if (kind === "error") state.error = new Error("Shopify unavailable");
  if (kind === "blocked") state.data = { ...state.data!, allowed: false, preview_token: null, reason: "Content changed" };
  const confirm = vi.fn();
  render(<PreviewDialog suggestionId={1} onClose={vi.fn()} onConfirmApply={confirm} applying={false} />);
  await userEvent.click(screen.getByRole("button", { name: "Confirm and apply" }));
  expect(confirm).not.toHaveBeenCalled();
});

it("routes every ready suggestion through Review", async () => {
  const review = vi.fn();
  const suggestion = { id: 1, source_type: "product", source_handle: "source", target_type: "collection", target_handle: "target", kind: "phrase_wrap", anchor_phrase: "Text", score: 1.4, ai_enabled: true } as LinkSuggestion;
  render(<table><tbody><SuggestionRow suggestion={suggestion} onReconcile={vi.fn()} onDismiss={vi.fn()} onGenerate={vi.fn()} onPreview={review} applying={false} dismissing={false} generating={false} /></tbody></table>);
  expect(screen.queryByRole("button", { name: "Apply" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "Review" }));
  expect(review).toHaveBeenCalledOnce();
});

it("blocks Generate and Review for disabled AI source types", () => {
  const suggestion = { id: 1, source_type: "collection", source_handle: "source", target_type: "product", target_handle: "target", kind: "ai_woven", score: 1.4, ai_enabled: false, ai_edit_json: null } as LinkSuggestion;
  render(<table><tbody><SuggestionRow suggestion={suggestion} onReconcile={vi.fn()} onDismiss={vi.fn()} onGenerate={vi.fn()} onPreview={vi.fn()} applying={false} dismissing={false} generating={false} /></tbody></table>);
  expect((screen.getByRole("button", { name: "Generate" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.queryByRole("button", { name: "Review" })).toBeNull();
});
