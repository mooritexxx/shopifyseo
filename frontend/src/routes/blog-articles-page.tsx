import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { Card } from "../components/ui/card";
import { DataTable, listTableNameLinkClassName, type Column } from "../components/ui/data-table";
import { SearchInput } from "../components/ui/search-input";
import { getJson } from "../lib/api";
import { blogArticlesSchema } from "../types/api";

const columns: Column[] = [
  { key: "article_name", label: "Article", align: "left", widthClass: "w-[22%] min-w-0" },
  { key: "handle", label: "Handle", align: "center", widthClass: "w-[13%]" },
  { key: "published_label", label: "Live", align: "center", widthClass: "w-[8%]" },
  { key: "published_at", label: "Published", align: "center", widthClass: "w-[12%]" },
  { key: "seo_title", label: "SEO title", align: "center", widthClass: "w-[20%]" },
  { key: "body_preview", label: "Preview", align: "center", widthClass: "w-[25%]" }
];

export function BlogArticlesPage() {
  const { blogHandle = "" } = useParams();
  const decoded = decodeURIComponent(blogHandle);
  const [queryText, setQueryText] = useState("");
  const [sort, setSort] = useState("article_name");
  const [direction, setDirection] = useState<"asc" | "desc">("asc");

  const query = useQuery({
    queryKey: ["blog-articles", decoded],
    queryFn: () =>
      getJson(`/api/blogs/${encodeURIComponent(decoded)}/articles`, blogArticlesSchema),
    enabled: Boolean(decoded)
  });

  const filtered = useMemo(() => {
    const items = query.data?.items ?? [];
    if (!queryText.trim()) return items;
    const needle = queryText.trim().toLowerCase();
    return items.filter(
      (row) =>
        row.title.toLowerCase().includes(needle) ||
        row.handle.toLowerCase().includes(needle) ||
        (row.seo_title || "").toLowerCase().includes(needle) ||
        (row.body_preview || "").toLowerCase().includes(needle)
    );
  }, [query.data?.items, queryText]);

  const rows = useMemo(() => {
    const copy = filtered.map((row) => ({
      ...row,
      article_name: row.title || row.handle,
      published_label: row.is_published ? "Yes" : "No",
      published_at: row.published_at || "—",
      seo_title: row.seo_title || "—",
      body_preview: row.body_preview || "—"
    }));
    const rev = direction === "desc";
    copy.sort((a, b) => {
      const av = String(a[sort as keyof typeof a] ?? "");
      const bv = String(b[sort as keyof typeof b] ?? "");
      return rev ? bv.localeCompare(av) : av.localeCompare(bv);
    });
    return copy;
  }, [filtered, sort, direction]);

  if (query.isError) {
    return (
      <div className="rounded-[30px] border border-red-200 bg-red-50/80 p-8 text-red-900 shadow-panel">
        <p className="font-semibold">Could not load blog</p>
        <p className="mt-2 text-sm">{(query.error as Error).message}</p>
        <Link to="/blogs" className="mt-4 inline-block text-sm font-medium underline">
          Back to blogs
        </Link>
      </div>
    );
  }

  const blogTitle = query.data?.blog.title || decoded;

  return (
    <div className="space-y-6 pb-8">
      <div>
        <p className="text-xs uppercase tracking-[0.24em] text-slate-500">
          <Link to="/blogs" className="text-slate-600 underline-offset-4 hover:underline">
            Blogs
          </Link>
          <span className="mx-2 text-slate-400">/</span>
          {decoded}
        </p>
        <h2 className="mt-2 text-4xl font-bold text-ink">{blogTitle}</h2>
        <p className="mt-2 text-sm text-slate-500">
          {query.data?.total ?? 0} article{query.data?.total === 1 ? "" : "s"} synced from Shopify (read-only list).
        </p>
      </div>

      <Card className="overflow-hidden border-[#dfe7f3] bg-[linear-gradient(180deg,_#ffffff_0%,_#fbfdff_100%)] p-0">
        <div className="border-b border-[#e5ecf5] px-6 py-5">
          <SearchInput value={queryText} onChange={setQueryText} placeholder="Filter articles…" />
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
              setDirection(
                key === "article_name" || key === "handle" || key === "published_at" ? "asc" : "desc"
              );
            }
          }}
          getRowLink={() => "/blogs"}
          isLoading={query.isLoading}
          error={query.error as Error | null}
        />
      </Card>
    </div>
  );
}
