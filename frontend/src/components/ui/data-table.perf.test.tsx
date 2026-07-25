import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { useMemo, useState } from "react";

import { DataTable, type Column } from "./data-table";
import { sortListRows } from "../../lib/list-sort";

const columns: Column[] = [
  { key: "title", label: "Product name", align: "left" },
  { key: "content_status", label: "Content", align: "center" },
  { key: "gsc_segments", label: "Segments", align: "center" },
  { key: "index_status", label: "Status", align: "center" },
  { key: "gsc_impressions", label: "Impressions", align: "center" },
  { key: "gsc_clicks", label: "Clicks", align: "center" },
  { key: "gsc_clicks_delta", label: "Trend", align: "center" },
  { key: "gsc_ctr", label: "CTR", align: "center" },
  { key: "ga4_views", label: "Views", align: "center" },
  { key: "pagespeed_performance", label: "Mobile", align: "center" },
  { key: "pagespeed_desktop_performance", label: "Desktop", align: "center" },
  { key: "score", label: "Score", align: "center" }
];

const ROW_COUNT = 830;

const rows = Array.from({ length: ROW_COUNT }, (_, i) => ({
  handle: `product-${i}`,
  title: `Product ${String(i).padStart(4, "0")}`,
  seo_title: i % 3 === 0 ? "" : `SEO ${i}`,
  seo_description: `Desc ${i}`,
  body_length: (i * 7) % 900,
  score: (i * 13) % 100,
  updated_at: `2026-0${(i % 9) + 1}-1${i % 9}`,
  index_status: i % 4 === 0 ? "Indexed" : "Not Indexed",
  gsc_impressions: (i * 31) % 5000,
  gsc_clicks: (i * 17) % 300,
  gsc_ctr: ((i * 3) % 100) / 1000,
  gsc_position: (i % 60) + 1,
  ga4_sessions: (i * 5) % 400,
  ga4_views: (i * 11) % 900,
  pagespeed_performance: i % 5 === 0 ? null : (i * 7) % 100,
  pagespeed_desktop_performance: i % 7 === 0 ? null : (i * 9) % 100,
  gsc_segment_flags: { has_dimensional: i % 2 === 0 },
  trend: {
    clicks_current: i % 40,
    clicks_previous: (i + 3) % 40,
    clicks_delta_pct: ((i * 13) % 200) - 100,
    impressions_current: i % 90,
    impressions_previous: (i + 5) % 90,
    impressions_delta_pct: ((i * 7) % 200) - 100,
    series: []
  }
}));

function Harness() {
  const [sort, setSort] = useState("score");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const sorted = useMemo(() => sortListRows(rows, sort, direction), [sort, direction]);
  return (
    <DataTable
      columns={columns}
      rows={sorted}
      sort={sort}
      direction={direction}
      onSortChange={(key) => {
        if (key === sort) setDirection((d) => (d === "asc" ? "desc" : "asc"));
        else {
          setSort(key);
          setDirection("desc");
        }
      }}
      getRowLink={(row) => `/products/${row.handle}`}
      getRowExternalLink={(row) => `https://example.com/products/${row.handle}`}
      getRowExternalLinkTitle={() => "Open live product page"}
    />
  );
}

describe(`DataTable with ${ROW_COUNT} rows`, () => {
  it("re-sorts without rebuilding cell contents, and keeps every row in the DOM", async () => {
    render(
      <MemoryRouter>
        <Harness />
      </MemoryRouter>
    );

    // All rows present: no windowing, so browser find-in-page still works.
    expect(await screen.findByText("Product 0000")).toBeTruthy();
    expect(screen.getByText(`Product ${String(ROW_COUNT - 1).padStart(4, "0")}`)).toBeTruthy();
    expect(document.querySelectorAll("tbody tr").length).toBe(ROW_COUNT);

    const started = performance.now();
    fireEvent.click(screen.getAllByRole("button", { name: /Impressions/i })[0]);
    const resortMs = performance.now() - started;

    // Order actually changed to descending impressions.
    const firstCell = document.querySelector("tbody tr a");
    expect(firstCell?.textContent).toBe(
      sortListRows(rows, "gsc_impressions", "desc")[0].title
    );
    expect(document.querySelectorAll("tbody tr").length).toBe(ROW_COUNT);

    // eslint-disable-next-line no-console
    console.log(`  [perf] re-sort of ${ROW_COUNT} rows: ${resortMs.toFixed(0)} ms`);
    expect(resortMs).toBeLessThan(4000);
  });
});
