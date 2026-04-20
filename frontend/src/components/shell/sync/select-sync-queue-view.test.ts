import { describe, expect, it } from "vitest";
import { selectSyncQueueView } from "./select-sync-queue-view";

describe("selectSyncQueueView", () => {
  it("maps pagespeed scope to PSI queue and HTTP call counter", () => {
    const row = {
      seq: 1,
      object_type: "product",
      handle: "x",
      url: "https://x",
      strategy: "mobile",
      code: "RUN",
      state: "running"
    };
    const v = selectSyncQueueView({
      active_scope: "pagespeed",
      pagespeed_queue_details: [row],
      pagespeed_http_calls_last_60s: 12
    });
    expect(v.queueTitle).toBe("Queue Stream");
    expect(v.queueItems).toEqual([row]);
    expect(v.throughputLast60s).toBe(12);
    expect(v.throughputMetricTitle).toBe("PSI HTTP calls (60s)");
  });

  it("maps gsc scope to gsc queue and slot counter", () => {
    const row = {
      seq: 1,
      object_type: "product",
      handle: "a",
      url: "https://u",
      strategy: "",
      code: "RUN",
      state: "running"
    };
    const v = selectSyncQueueView({
      active_scope: "gsc",
      gsc_queue_details: [row],
      gsc_sync_slots_last_60s: 3
    });
    expect(v.queueTitle).toBe("Queue Stream");
    expect(v.queueItems).toEqual([row]);
    expect(v.throughputLast60s).toBe(3);
    expect(v.throughputMetricTitle).toBe("GSC rate slots (60s)");
  });

  it("maps index scope to index queue and Speed throughput label", () => {
    const row = {
      seq: 1,
      object_type: "product",
      handle: "h",
      url: "https://u",
      strategy: "",
      code: "RUN",
      state: "running"
    };
    const v = selectSyncQueueView({
      active_scope: "index",
      index_queue_details: [row],
      index_sync_slots_last_60s: 50
    });
    expect(v.queueTitle).toBe("Queue Stream");
    expect(v.queueItems).toEqual([row]);
    expect(v.throughputLast60s).toBe(50);
    expect(v.throughputMetricTitle).toBe("Speed");
  });

  it("hides throughput for shopify scope while still exposing the catalog sync queue", () => {
    const row = {
      seq: 1,
      object_type: "product",
      handle: "gid",
      url: "https://cdn/x.jpg",
      strategy: "",
      code: "RUN",
      state: "running"
    };
    const v = selectSyncQueueView({
      active_scope: "shopify",
      shopify_queue_details: [row]
    });
    expect(v.queueTitle).toBe("Queue Stream");
    expect(v.queueItems).toEqual([row]);
    expect(v.throughputLast60s).toBeNull();
    expect(v.throughputMetricTitle).toBeUndefined();
  });
});
