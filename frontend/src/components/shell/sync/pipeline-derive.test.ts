import { describe, expect, it } from "vitest";
import { derivePipelineRows } from "./pipeline-derive";

const runArgs = {
  running: true,
  hasError: false,
  syncPercent: 50,
  stepIndex: 1
};

describe("derivePipelineRows accurate counts", () => {
  it("GSC progress uses refreshed+errors only, not precheck skipped", () => {
    const rows = derivePipelineRows({
      orderedScopes: ["gsc"],
      syncStatus: {
        running: true,
        stage: "refreshing_gsc",
        active_scope: "gsc",
        gsc_refreshed: 2,
        gsc_errors: 0,
        gsc_skipped: 40,
        gsc_progress_total: 5
      } as never,
      activeScope: "gsc",
      ...runArgs
    });
    const gsc = rows.find((r) => r.key === "gsc");
    expect(gsc?.status).toBe("active");
    expect(gsc?.count).toBe(2);
    expect(gsc?.total).toBe(5);
  });

  it("Index progress excludes index_skipped precheck from done", () => {
    const rows = derivePipelineRows({
      orderedScopes: ["index"],
      syncStatus: {
        running: true,
        stage: "refreshing_index",
        active_scope: "index",
        index_refreshed: 3,
        index_errors: 0,
        index_skipped: 200,
        index_progress_total: 10
      } as never,
      activeScope: "index",
      ...runArgs
    });
    const idx = rows.find((r) => r.key === "index");
    expect(idx?.count).toBe(3);
    expect(idx?.total).toBe(10);
  });

  it("PageSpeed complete uses refreshed count vs queue baseline when queue had work", () => {
    const rows = derivePipelineRows({
      orderedScopes: ["pagespeed"],
      syncStatus: {
        running: true,
        stage: "refreshing_pagespeed",
        active_scope: "pagespeed",
        pagespeed_phase: "complete",
        pagespeed_queue_baseline: 4,
        pagespeed_refreshed: 4,
        pagespeed_scan_total: 100,
        pagespeed_scanned: 100
      } as never,
      activeScope: "pagespeed",
      ...runArgs
    });
    const ps = rows.find((r) => r.key === "pagespeed");
    expect(ps?.count).toBe(4);
    expect(ps?.total).toBe(4);
  });

  it("after sync complete, Search Console row shows full refreshed count", () => {
    const rows = derivePipelineRows({
      orderedScopes: ["gsc", "pagespeed"],
      syncStatus: {
        running: false,
        stage: "complete",
        active_scope: "pagespeed",
        gsc_refreshed: 667,
        gsc_errors: 0
      } as never,
      running: false,
      hasError: false,
      syncPercent: 100,
      activeScope: "pagespeed",
      stepIndex: 2
    });
    const gsc = rows.find((r) => r.key === "gsc");
    expect(gsc?.status).toBe("done");
    expect(gsc?.count).toBe(667);
    expect(gsc?.total).toBe(667);
  });
});
