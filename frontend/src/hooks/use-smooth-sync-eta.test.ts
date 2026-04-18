import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSmoothSyncEta } from "./use-smooth-sync-eta";

describe("useSmoothSyncEta", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("resets when segment changes", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ stage, scope, running, eta }: { stage: string; scope: string; running: boolean; eta: number | null }) =>
        useSmoothSyncEta(running, eta, stage, scope),
      { initialProps: { stage: "a", scope: "x", running: true, eta: 100 } }
    );
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(result.current).toBe(100);

    rerender({ stage: "b", scope: "x", running: true, eta: 50 });
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(result.current).toBe(50);
  });

  it("does not jump up faster than upward slew when server revises higher", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ eta }: { eta: number | null }) => useSmoothSyncEta(true, eta, "refreshing_index", "index"),
      { initialProps: { eta: 100 } }
    );
    act(() => {
      vi.advanceTimersByTime(250);
    });
    expect(result.current).toBe(100);

    rerender({ eta: 200 });
    act(() => {
      vi.advanceTimersByTime(250);
    });
    const afterOneTick = result.current;
    expect(afterOneTick).not.toBeNull();
    expect(afterOneTick!).toBeLessThanOrEqual(101);
  });
});
