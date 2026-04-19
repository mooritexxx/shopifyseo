import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { DataTable, listTableNameLinkClassName, type Column } from "../components/ui/data-table";
import { SearchInput } from "../components/ui/search-input";
import { SummaryCard } from "../components/ui/summary-card";
import { useStoreUrl } from "../hooks/use-store-info";
import { getJson } from "../lib/api";
import { formatNumber } from "../lib/utils";
import { productListSchema } from "../types/api";

const PRODUCT_SORT_KEYS = new Set([
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
  "pagespeed_desktop_performance"
]);

const columns: Column[] = [
  { key: "title", label: "Product name", align: "left", widthClass: "min-w-[12rem] w-[19%]" },
  { key: "content_status", label: "Content", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_segments", label: "Segments", align: "center", widthClass: "w-[9%]" },
  { key: "index_status", label: "Status", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_impressions", label: "Impressions", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_clicks", label: "Clicks", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_ctr", label: "CTR", align: "center", widthClass: "w-[9%]" },
  { key: "ga4_views", label: "Views", align: "center", widthClass: "w-[9%]" },
  { key: "pagespeed_performance", label: "Mobile", align: "center", widthClass: "w-[7%]" },
  { key: "pagespeed_desktop_performance", label: "Desktop", align: "center", widthClass: "w-[7%]" },
  { key: "score", label: "Score", align: "center", widthClass: "w-[9%]" }
];

export function ProductsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const storeUrl = useStoreUrl();

  const sortRaw = searchParams.get("sort");
  const sort =
    sortRaw && PRODUCT_SORT_KEYS.has(sortRaw) ? sortRaw : "gsc_impressions";
  const directionParam = searchParams.get("direction");
  const direction: "asc" | "desc" =
    directionParam === "asc" || directionParam === "desc" ? directionParam : "desc";
  const focusRaw = searchParams.get("focus");
  const focus =
    focusRaw === "missing_meta" || focusRaw === "thin_body" ? focusRaw : null;

  const listUrl = useMemo(() => {
    const p = new URLSearchParams();
    if (query.trim()) p.set("query", query.trim());
    p.set("sort", sort);
    p.set("direction", direction);
    if (focus) p.set("focus", focus);
    return `/api/products?${p.toString()}`;
  }, [query, sort, direction, focus]);

  const productsQuery = useQuery({
    queryKey: ["products", query, sort, direction, focus],
    queryFn: () => getJson(listUrl, productListSchema)
  });

  const rows = productsQuery.data?.items ?? [];
  const summary = productsQuery.data?.summary ?? {
    visible_rows: 0,
    high_priority: 0,
    index_issues: 0,
    average_score: 0
  };

  const patchSearch = (next: {
    sort?: string;
    direction?: "asc" | "desc";
    focus?: string | null;
  }) => {
    const p = new URLSearchParams(searchParams);
    const s = next.sort ?? sort;
    const d = next.direction ?? direction;
    p.set("sort", s);
    p.set("direction", d);
    if (next.focus !== undefined) {
      if (next.focus) p.set("focus", next.focus);
      else p.delete("focus");
    } else if (focus) {
      p.set("focus", focus);
    }
    setSearchParams(p, { replace: true });
  };

  return (
    <div className="space-y-6 pb-8">
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Products</p>
        <h2 className="mt-2 text-4xl font-bold text-ink">Product overview</h2>
        <p className="mt-2 text-sm text-slate-500">A cleaner catalog view for scanning search visibility, inspection health, and page speed at a glance.</p>
        {focus ? (
          <p className="mt-2 text-sm font-medium text-[#5746d9]">
            Filter:{" "}
            {focus === "missing_meta" ? "SEO title and description both empty" : "Thin body (under 200 chars)"}
            {" · "}
            <Button
              variant="link"
              className="h-auto p-0 text-inherit font-inherit underline-offset-4 hover:underline"
              onClick={() => patchSearch({ focus: null })}
            >
              Clear filter
            </Button>
          </p>
        ) : null}
      </div>

      <section className="grid gap-4 xl:grid-cols-4">
        <SummaryCard
          label="Visible rows"
          value={formatNumber(summary.visible_rows)}
          hint="Matching products across the full filtered catalog."
          tone="border-[#dbe5f3] bg-[linear-gradient(135deg,#ffffff_0%,#eef6ff_100%)]"
        />
        <SummaryCard
          label="High priority"
          value={formatNumber(summary.high_priority)}
          hint="Products with the largest SEO upside right now."
          tone="border-[#f2d9cf] bg-[linear-gradient(135deg,#fff7f4_0%,#ffe7de_100%)]"
        />
        <SummaryCard
          label="Index issues"
          value={formatNumber(summary.index_issues)}
          hint="Rows that likely need better indexing confidence."
          tone="border-[#efe2bf] bg-[linear-gradient(135deg,#fffdf5_0%,#fff3cf_100%)]"
        />
        <SummaryCard
          label="Average score"
          value={formatNumber(summary.average_score)}
          hint="Average opportunity score across the visible rows."
          tone="border-[#d8e9e1] bg-[linear-gradient(135deg,#f8fffb_0%,#e3f7ee_100%)]"
        />
      </section>

      <Card className="overflow-hidden border-[#dfe7f3] bg-[linear-gradient(180deg,#ffffff_0%,#fbfdff_100%)] p-0">
        <div className="border-b border-[#e5ecf5] px-6 py-5">
          <SearchInput value={query} onChange={setQuery} placeholder="Search product name or SEO title" />
        </div>

        <DataTable
          columns={columns}
          rows={rows}
          sort={sort}
          direction={direction}
          nameLinkClassName={listTableNameLinkClassName}
          onSortChange={(key) => {
            if (sort === key) {
              patchSearch({ direction: direction === "asc" ? "desc" : "asc" });
            } else {
              patchSearch({
                sort: key,
                direction: key === "title" || key === "index_status" ? "asc" : "desc"
              });
            }
          }}
          getRowLink={(row) => `/products/${row.handle}`}
          getRowExternalLink={storeUrl ? (row) => `${storeUrl}/products/${row.handle}` : undefined}
          getRowExternalLinkTitle={() => "Open live product page"}
          isLoading={productsQuery.isLoading}
          error={productsQuery.error as Error | null}
        />
      </Card>
    </div>
  );
}
