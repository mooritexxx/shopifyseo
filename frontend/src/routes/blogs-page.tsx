import "./catalog-page.css";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Card } from "../components/ui/card";
import { DataTable, listTableNameLinkClassName, type Column } from "../components/ui/data-table";
import { SearchInput } from "../components/ui/search-input";
import { SummaryCard } from "../components/ui/summary-card";
import { getJson } from "../lib/api";
import { blogListSchema } from "../types/api";

const columns: Column[] = [
  { key: "title", label: "Blog", align: "left", widthClass: "w-[32%] min-w-0" },
  { key: "handle", label: "Handle", align: "center", widthClass: "w-[18%]" },
  { key: "article_count", label: "Articles", align: "center", widthClass: "w-[15%]" },
  { key: "updated_at", label: "Updated", align: "center", widthClass: "w-[35%]" }
];

export function BlogsPage() {
  const [queryText, setQueryText] = useState("");
  const [sort, setSort] = useState("title");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");
  const query = useQuery({
    queryKey: ["blogs"],
    queryFn: () => getJson("/api/blogs", blogListSchema)
  });

  const filtered = useMemo(() => {
    const items = query.data?.items ?? [];
    if (!queryText.trim()) return items;
    const needle = queryText.trim().toLowerCase();
    return items.filter(
      (row) =>
        row.title.toLowerCase().includes(needle) || row.handle.toLowerCase().includes(needle)
    );
  }, [query.data?.items, queryText]);

  const rows = useMemo(() => {
    const copy = filtered.map((row) => ({
      ...row,
      title: row.title || row.handle,
      updated_at: row.updated_at || "—"
    }));
    const mul = direction === "desc" ? -1 : 1;
    copy.sort((a, b) => {
      if (sort === "article_count") {
        return mul * (a.article_count - b.article_count);
      }
      const av = String(a[sort as keyof typeof a] ?? "");
      const bv = String(b[sort as keyof typeof b] ?? "");
      return mul * av.localeCompare(bv);
    });
    return copy;
  }, [filtered, sort, direction]);

  const summary = useMemo(
    () => ({
      blogs: rows.length,
      articles: rows.reduce((sum, row) => sum + row.article_count, 0)
    }),
    [rows]
  );

  return (
    <div className="catalog-page min-w-0 space-y-5 pb-8">
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Blogs</p>
        <h1 className="catalog-title mt-1">Shopify blogs</h1>
        <p className="mt-2 text-sm text-slate-500">
          Blogs and articles synced from Shopify. Click a blog to see its posts.
        </p>
      </div>

      <section className="catalog-summary-grid" aria-label="Blog summary">
        <SummaryCard compact loading={query.isLoading} unavailable={query.isError}
          label="Blogs"
          value={String(summary.blogs)}
          hint="In local database"
          tone="border-[#dbe5f3] bg-[linear-gradient(135deg,_#ffffff_0%,_#eef6ff_100%)]"
        />
        <SummaryCard compact loading={query.isLoading} unavailable={query.isError}
          label="Articles"
          value={String(summary.articles)}
          hint="Across visible blogs"
          tone="border-[#d8e9e1] bg-[linear-gradient(135deg,_#f8fffb_0%,_#e3f7ee_100%)]"
        />
      </section>

      <Card className="overflow-hidden bg-white p-0">
        <div className="catalog-toolbar">
          <SearchInput
            value={queryText}
            onChange={setQueryText}
            placeholder="Filter by title or handle…"
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
              setDirection(direction === "asc" ? "desc" : "asc");
            } else {
              setSort(key);
              setDirection(key === "title" || key === "handle" || key === "updated_at" ? "asc" : "desc");
            }
          }}
          getRowLink={(row) => `/blogs/${encodeURIComponent(String(row.handle))}`}
          isLoading={query.isLoading}
          error={query.error as Error | null}
        />
      </Card>
    </div>
  );
}
