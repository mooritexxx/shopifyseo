import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { z } from "zod";

import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { DataTable, listTableNameLinkClassName, type Column } from "../components/ui/data-table";
import { SearchInput } from "../components/ui/search-input";
import { SummaryCard } from "../components/ui/summary-card";
import { Toast, type ToastVariant } from "../components/ui/toast";
import { detectToastVariant } from "../lib/toast-utils";
import { useStoreUrl } from "../hooks/use-store-info";
import { getJson, postJson } from "../lib/api";
import { formatNumber } from "../lib/utils";
import { contentListSchema } from "../types/api";

const CONTENT_SORT_KEYS = new Set([
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
  "pagespeed_performance"
]);

const columns: Column[] = [
  { key: "title", label: "Name", align: "left", widthClass: "min-w-[12rem] w-[19%]" },
  { key: "content_status", label: "Content", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_segments", label: "Segments", align: "center", widthClass: "w-[9%]" },
  { key: "index_status", label: "Status", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_impressions", label: "Impressions", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_clicks", label: "Clicks", align: "center", widthClass: "w-[9%]" },
  { key: "gsc_ctr", label: "CTR", align: "center", widthClass: "w-[9%]" },
  { key: "ga4_sessions", label: "Sessions", align: "center", widthClass: "w-[9%]" },
  { key: "pagespeed_performance", label: "Speed", align: "center", widthClass: "w-[9%]" },
  { key: "score", label: "Score", align: "center", widthClass: "w-[9%]" }
];

export function ContentListPage({ kind, title }: { kind: "collections" | "pages"; title: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [queryText, setQueryText] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const storeUrl = useStoreUrl();

  const sortRaw = searchParams.get("sort");
  const sort =
    sortRaw && CONTENT_SORT_KEYS.has(sortRaw) ? sortRaw : "gsc_impressions";
  const directionParam = searchParams.get("direction");
  const direction: "asc" | "desc" =
    directionParam === "asc" || directionParam === "desc" ? directionParam : "desc";
  const focus = searchParams.get("focus") === "missing_meta" ? "missing_meta" : null;

  const listUrl = useMemo(() => {
    const p = new URLSearchParams();
    if (queryText.trim()) p.set("query", queryText.trim());
    p.set("sort", sort);
    p.set("direction", direction);
    if (focus) p.set("focus", focus);
    return `/api/${kind}?${p.toString()}`;
  }, [kind, queryText, sort, direction, focus]);

  const query = useQuery({
    queryKey: [kind, queryText, sort, direction, focus],
    queryFn: () => getJson(listUrl, contentListSchema)
  });

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
  const saveCollectionMetaMutation = useMutation({
    mutationFn: () =>
      postJson(
        "/api/collections/save-meta",
        z.object({
          message: z.string(),
          state: z.object({
            saved: z.number(),
            skipped: z.number(),
            total: z.number(),
            skipped_handles: z.array(z.string()).optional().default([])
          }).nullable().optional()
        })
      ),
    onSuccess: (result) => {
      const summary = result.state
        ? `${result.message} (${result.state.saved} saved, ${result.state.skipped} skipped)`
        : result.message;
      setToast(summary);
      void queryClient.invalidateQueries({ queryKey: [kind] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: (mutationError) => setToast((mutationError as Error).message)
  });
  const savePageMetaMutation = useMutation({
    mutationFn: () =>
      postJson(
        "/api/pages/save-meta",
        z.object({
          message: z.string(),
          state: z.object({
            saved: z.number(),
            skipped: z.number(),
            total: z.number(),
            skipped_handles: z.array(z.string()).optional().default([])
          }).nullable().optional()
        })
      ),
    onSuccess: (result) => {
      const summary = result.state
        ? `${result.message} (${result.state.saved} saved, ${result.state.skipped} skipped)`
        : result.message;
      setToast(summary);
      void queryClient.invalidateQueries({ queryKey: [kind] });
      void queryClient.invalidateQueries({ queryKey: ["summary"] });
    },
    onError: (mutationError) => setToast((mutationError as Error).message)
  });

  const rows = query.data?.items ?? [];
  const summary = useMemo(
    () => ({
      visible_rows: rows.length,
      high_priority: rows.filter((row) => row.priority === "High").length,
      index_issues: rows.filter((row) => (row.index_status || "").trim().toLowerCase() !== "indexed").length,
      average_score: rows.length ? Math.round(rows.reduce((sum, row) => sum + row.score, 0) / rows.length) : 0
    }),
    [rows]
  );

  return (
    <div className="space-y-6 pb-8">
      {toast ? <Toast variant={detectToastVariant(toast)}>{toast}</Toast> : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-slate-500">{title}</p>
          <h2 className="mt-2 text-4xl font-bold text-ink">{title} overview</h2>
          <p className="mt-2 text-sm text-slate-500">A cleaner catalog view for scanning search visibility, inspection health, and page speed at a glance.</p>
          {focus ? (
            <p className="mt-2 text-sm font-medium text-[#5746d9]">
              Filter: SEO title and description both empty ·{" "}
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
        {kind === "collections" ? (
          <Button type="button" onClick={() => saveCollectionMetaMutation.mutate()} disabled={saveCollectionMetaMutation.isPending}>
            {saveCollectionMetaMutation.isPending ? "Saving collection SEO…" : "Save Collection SEO Title, Description & Body"}
          </Button>
        ) : null}
        {kind === "pages" ? (
          <Button type="button" onClick={() => savePageMetaMutation.mutate()} disabled={savePageMetaMutation.isPending}>
            {savePageMetaMutation.isPending ? "Saving page SEO…" : "Save Page SEO Title, Description & Body"}
          </Button>
        ) : null}
      </div>

      <section className="grid gap-4 xl:grid-cols-4">
        <SummaryCard
          label="Visible rows"
          value={formatNumber(summary.visible_rows)}
          hint={`Matching ${kind} across the current filtered list.`}
          tone="border-[#dbe5f3] bg-[linear-gradient(135deg,#ffffff_0%,#eef6ff_100%)]"
        />
        <SummaryCard
          label="High priority"
          value={formatNumber(summary.high_priority)}
          hint={`${title} with the largest SEO upside right now.`}
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
          <SearchInput
            value={queryText}
            onChange={setQueryText}
            placeholder={`Search ${title.toLowerCase()} by name or SEO title`}
          />
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
          getRowLink={(row) => `/${kind}/${row.handle}`}
          getRowExternalLink={storeUrl ? (row) => `${storeUrl}/${kind}/${row.handle}` : undefined}
          getRowExternalLinkTitle={() => `Open live ${kind === "collections" ? "collection" : "page"}`}
          isLoading={query.isLoading}
          error={query.error as Error | null}
        />
      </Card>
    </div>
  );
}
