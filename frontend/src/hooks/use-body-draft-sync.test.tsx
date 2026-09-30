// @vitest-environment jsdom
import { useState } from "react";
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it } from "vitest";
import { useBodyDraftSync } from "./use-body-draft-sync";

afterEach(cleanup);
function wrapper({ children }: { children: React.ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}
function useEditor(incoming: string, initialBody = "old") {
  const [draft, setDraft] = useState({ body_html: initialBody, title: "Unpublished title" });
  const [baseline, setBaseline] = useState({ body_html: "old", title: "Original title" });
  const sync = useBodyDraftSync("product/one", incoming, draft, setDraft, baseline, setBaseline);
  return { draft, baseline, ...sync };
}
it("refreshes an unchanged body without erasing other draft fields", async () => {
  const { result, rerender } = renderHook(({ incoming }) => useEditor(incoming), { initialProps: { incoming: "old" }, wrapper });
  rerender({ incoming: "live with link" });
  await waitFor(() => expect(result.current.draft.body_html).toBe("live with link"));
  expect(result.current.draft.title).toBe("Unpublished title");
  expect(result.current.conflict).toBe(false);
});
it("preserves a divergent body draft and flags the conflict", async () => {
  const { result, rerender } = renderHook(({ incoming }) => useEditor(incoming, "My unsaved work"), { initialProps: { incoming: "old" }, wrapper });
  rerender({ incoming: "live with link" });
  expect(result.current.conflict).toBe(true);
  expect(result.current.draft.body_html).toBe("My unsaved work");
  act(() => result.current.useLatestBody());
  expect(result.current.draft.body_html).toBe("live with link");
  expect(result.current.draft.title).toBe("Unpublished title");
  expect(result.current.conflict).toBe(false);
});
