import { describe, expect, it } from "vitest";

import { LIST_SORT_KEYS, sortListRows } from "./list-sort";

const row = (over: Record<string, unknown> = {}) => ({
  handle: "h",
  title: "T",
  seo_title: "s",
  seo_description: "d",
  body_length: 10,
  score: 0,
  updated_at: "",
  index_status: "",
  gsc_impressions: 0,
  gsc_clicks: 0,
  gsc_ctr: 0,
  gsc_position: 0,
  ga4_sessions: 0,
  ga4_views: 0,
  pagespeed_performance: null,
  pagespeed_desktop_performance: null,
  gsc_segment_flags: { has_dimensional: false },
  trend: null,
  ...over
});

describe("sortListRows", () => {
  it("covers every sort key the list pages expose", () => {
    for (const key of [
      "score",
      "title",
      "updated_at",
      "content_status",
      "gsc_segments",
      "index_status",
      "gsc_impressions",
      "gsc_clicks",
      "gsc_ctr",
      "gsc_position",
      "ga4_sessions",
      "ga4_views",
      "body_length",
      "pagespeed_performance",
      "pagespeed_desktop_performance",
      "gsc_clicks_delta",
      "gsc_impressions_delta"
    ]) {
      expect(LIST_SORT_KEYS.has(key)).toBe(true);
    }
  });

  it("keeps ties in input order in both directions (Python stable-sort parity)", () => {
    const rows = [
      row({ handle: "a", score: 5 }),
      row({ handle: "b", score: 5 }),
      row({ handle: "c", score: 5 })
    ];
    expect(sortListRows(rows, "score", "desc").map((r) => r.handle)).toEqual(["a", "b", "c"]);
    expect(sortListRows(rows, "score", "asc").map((r) => r.handle)).toEqual(["a", "b", "c"]);
  });

  it("treats a zero or missing gsc_position as 999, matching `x or 999.0`", () => {
    const rows = [
      row({ handle: "zero", gsc_position: 0 }),
      row({ handle: "real", gsc_position: 12 }),
      row({ handle: "missing", gsc_position: null })
    ];
    // Ascending: the real position sorts ahead of both 999 fallbacks.
    expect(sortListRows(rows, "gsc_position", "asc").map((r) => r.handle)).toEqual([
      "real",
      "zero",
      "missing"
    ]);
  });

  it("sorts missing pagespeed scores as -1 rather than dropping them", () => {
    const rows = [
      row({ handle: "none", pagespeed_performance: null }),
      row({ handle: "zero", pagespeed_performance: 0 }),
      row({ handle: "good", pagespeed_performance: 90 })
    ];
    expect(sortListRows(rows, "pagespeed_performance", "desc").map((r) => r.handle)).toEqual([
      "good",
      "zero",
      "none"
    ]);
  });

  it("reads trend deltas and folds null to 0", () => {
    const rows = [
      row({ handle: "up", trend: { clicks_delta_pct: 40 } }),
      row({ handle: "flat", trend: { clicks_delta_pct: null } }),
      row({ handle: "down", trend: { clicks_delta_pct: -25 } })
    ];
    expect(sortListRows(rows, "gsc_clicks_delta", "desc").map((r) => r.handle)).toEqual([
      "up",
      "flat",
      "down"
    ]);
  });

  it("lowercases titles before comparing", () => {
    const rows = [row({ handle: "b", title: "beta" }), row({ handle: "a", title: "Alpha" })];
    expect(sortListRows(rows, "title", "asc").map((r) => r.handle)).toEqual(["a", "b"]);
  });

  it("marks content complete only when meta and body are all present", () => {
    const rows = [
      row({ handle: "incomplete", seo_title: "  ", seo_description: "d", body_length: 5 }),
      row({ handle: "complete", seo_title: "t", seo_description: "d", body_length: 5 }),
      row({ handle: "nobody", seo_title: "t", seo_description: "d", body_length: 0 })
    ];
    expect(sortListRows(rows, "content_status", "desc").map((r) => r.handle)).toEqual([
      "complete",
      "incomplete",
      "nobody"
    ]);
  });

  it("falls back to score for an unknown sort key", () => {
    const rows = [row({ handle: "lo", score: 1 }), row({ handle: "hi", score: 9 })];
    expect(sortListRows(rows, "not-a-key", "desc").map((r) => r.handle)).toEqual(["hi", "lo"]);
  });

  it("does not mutate the input array", () => {
    const rows = [row({ handle: "a", score: 1 }), row({ handle: "b", score: 9 })];
    const before = rows.map((r) => r.handle);
    sortListRows(rows, "score", "desc");
    expect(rows.map((r) => r.handle)).toEqual(before);
  });
});
