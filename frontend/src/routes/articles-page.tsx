import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Sparkles } from "lucide-react";

import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { DataTable, listTableNameLinkClassName, type Column } from "../components/ui/data-table";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Modal } from "../components/ui/modal";
import { SearchInput } from "../components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from "../components/ui/select";
import { SummaryCard } from "../components/ui/summary-card";
import { Textarea } from "../components/ui/textarea";
import { ArticleDraftProgressPanel } from "../components/article-draft-progress-panel";
import { getJson } from "../lib/api";
import { formatNumber } from "../lib/utils";
import { runArticleDraftStream, type ArticleDraftProgressEvent } from "../lib/run-article-draft-stream";
import { defaultDraftSlugHint } from "../lib/seo-slug";
import { allArticlesSchema, blogShopifyIdSchema } from "../types/api";
import { z } from "zod";

const ARTICLE_SORT_KEYS = new Set([
  "article_name",
  "title",
  "updated_at",
  "score",
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
  { key: "article_name", label: "Article", align: "left", widthClass: "w-[24%] min-w-0" },
  { key: "content_status", label: "Content", align: "center", widthClass: "w-[7.6%]" },
  { key: "gsc_segments", label: "Segments", align: "center", widthClass: "w-[7.6%]" },
  { key: "index_status", label: "Status", align: "center", widthClass: "w-[7.6%]" },
  { key: "gsc_impressions", label: "Impressions", align: "center", widthClass: "w-[7.6%]" },
  { key: "gsc_clicks", label: "Clicks", align: "center", widthClass: "w-[7.6%]" },
  { key: "gsc_ctr", label: "CTR", align: "center", widthClass: "w-[7.6%]" },
  { key: "ga4_sessions", label: "Sessions", align: "center", widthClass: "w-[7.6%]" },
  { key: "pagespeed_performance", label: "Mobile", align: "center", widthClass: "w-[6.5%]" },
  { key: "pagespeed_desktop_performance", label: "Desktop", align: "center", widthClass: "w-[6.5%]" },
  { key: "score", label: "Score", align: "center", widthClass: "w-[7.6%]" },
  { key: "published_label", label: "Live", align: "center", sortable: false, widthClass: "w-[7.6%]" }
];

function compareArticleRows(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
  sort: string,
  rev: boolean
): number {
  const mul = rev ? -1 : 1;
  if (sort === "updated_at") {
    const av = String(a.updated_at || "");
    const bv = String(b.updated_at || "");
    return mul * av.localeCompare(bv);
  }
  if (sort === "article_name" || sort === "title") {
    const av = String(a.article_name ?? a.title ?? "");
    const bv = String(b.article_name ?? b.title ?? "");
    return mul * av.localeCompare(bv);
  }
  const numericKeys = new Set([
    "score",
    "body_length",
    "gsc_clicks",
    "gsc_impressions",
    "gsc_ctr",
    "gsc_position",
    "ga4_sessions",
    "ga4_views",
    "ga4_avg_session_duration",
    "pagespeed_performance",
    "pagespeed_desktop_performance"
  ]);
  if (numericKeys.has(sort)) {
    const read = (row: Record<string, unknown>) => {
      const v = row[sort];
      if (
        (sort === "pagespeed_performance" || sort === "pagespeed_desktop_performance") &&
        (v === null || v === undefined)
      )
        return -1;
      const n = Number(v ?? 0);
      return Number.isFinite(n) ? n : 0;
    };
    return mul * (read(a) - read(b));
  }
  const av = String(a[sort] ?? "");
  const bv = String(b[sort] ?? "");
  return mul * av.localeCompare(bv);
}

const blogShopifyIdsSchema = z.array(blogShopifyIdSchema);

const emptyDraftForm = {
  blog_id: "",
  blog_handle: "",
  topic: "",
  keywords: "",
  slug: "",
  author_name: ""
};

export function ArticlesPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [queryText, setQueryText] = useState("");
  const [sort, setSort] = useState("gsc_impressions");
  const [direction, setDirection] = useState<"asc" | "desc">("desc");
  const focusMissingMeta = searchParams.get("focus") === "missing_meta";

  const [draftModalOpen, setDraftModalOpen] = useState(false);
  const [draftForm, setDraftForm] = useState(emptyDraftForm);
  const [draftError, setDraftError] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [draftGenerating, setDraftGenerating] = useState(false);
  const [draftProgressEvents, setDraftProgressEvents] = useState<ArticleDraftProgressEvent[]>([]);
  const [draftRunKey, setDraftRunKey] = useState(0);
  const [draftModalStep, setDraftModalStep] = useState<1 | 2>(1);

  const query = useQuery({
    queryKey: ["all-articles"],
    queryFn: () => getJson("/api/articles", allArticlesSchema)
  });

  const blogsQuery = useQuery({
    queryKey: ["blogs-shopify-ids"],
    queryFn: () => getJson("/api/blogs/shopify-ids", blogShopifyIdsSchema),
    enabled: draftModalOpen
  });

  useEffect(() => {
    if (!draftModalOpen || draftModalStep !== 1) return;
    const blogs = blogsQuery.data;
    if (!blogs || blogs.length !== 1 || !blogs[0]?.id) return;
    setDraftForm((f) => {
      if (f.blog_id.trim()) return f;
      return { ...f, blog_id: blogs[0].id, blog_handle: blogs[0].handle ?? "" };
    });
  }, [draftModalOpen, draftModalStep, blogsQuery.data]);

  async function submitDraftFromModal() {
    setDraftError("");
    setDraftModalStep(2);
    setDraftRunKey((k) => k + 1);
    setDraftProgressEvents([]);
    setDraftGenerating(true);
    try {
      const keywords = draftForm.keywords
        ? draftForm.keywords
            .split(",")
            .map((k) => k.trim())
            .filter(Boolean)
        : [];
      const data = await runArticleDraftStream(
        {
          blog_id: draftForm.blog_id,
          blog_handle: draftForm.blog_handle,
          topic: draftForm.topic,
          keywords,
          author_name: draftForm.author_name,
          slug_hint: draftForm.slug.trim()
        },
        (evt) => setDraftProgressEvents((prev) => [...prev, evt])
      );
      setDraftModalOpen(false);
      setDraftModalStep(1);
      setDraftForm(emptyDraftForm);
      setSlugTouched(false);
      setDraftProgressEvents([]);
      void queryClient.invalidateQueries({ queryKey: ["all-articles"] });
      navigate(
        `/articles/${encodeURIComponent(data.blog_handle)}/${encodeURIComponent(data.handle)}`
      );
    } catch (err) {
      setDraftError((err as Error).message || "Failed to generate draft");
    } finally {
      setDraftGenerating(false);
    }
  }

  const filtered = useMemo(() => {
    let items = query.data?.items ?? [];
    if (focusMissingMeta) {
      items = items.filter(
        (row) => !(row.seo_title || "").trim() && !(row.seo_description || "").trim()
      );
    }
    if (!queryText.trim()) return items;
    const needle = queryText.trim().toLowerCase();
    return items.filter(
      (row) =>
        row.title.toLowerCase().includes(needle) ||
        row.handle.toLowerCase().includes(needle) ||
        row.blog_handle.toLowerCase().includes(needle) ||
        row.blog_title.toLowerCase().includes(needle) ||
        (row.seo_title || "").toLowerCase().includes(needle) ||
        (row.body_preview || "").toLowerCase().includes(needle)
    );
  }, [query.data?.items, queryText, focusMissingMeta]);

  const rows = useMemo(() => {
    const copy = filtered.map((row) => ({
      ...row,
      article_name: row.title || row.handle,
      published_label: row.is_published ? "Yes" : "No"
    })) as Record<string, unknown>[];
    const rev = direction === "desc";
    const sortKey = ARTICLE_SORT_KEYS.has(sort) ? sort : "gsc_impressions";
    copy.sort((a, b) => {
      const primary = compareArticleRows(a, b, sortKey, rev);
      if (primary !== 0) return primary;
      const ta = String(a.article_name ?? a.title ?? "");
      const tb = String(b.article_name ?? b.title ?? "");
      return ta.localeCompare(tb);
    });
    return copy;
  }, [filtered, sort, direction]);

  const summary = useMemo(
    () => ({
      visible_rows: filtered.length,
      high_priority: filtered.filter((row) => row.priority === "High").length,
      index_issues: filtered.filter((row) => (row.index_status || "").trim().toLowerCase() !== "indexed").length,
      average_score: filtered.length
        ? Math.round(filtered.reduce((sum, row) => sum + row.score, 0) / filtered.length)
        : 0
    }),
    [filtered]
  );

  const canSubmitDraft =
    draftForm.blog_id.trim() && draftForm.topic.trim() && !draftGenerating;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-slate-500">Articles</p>
          <h2 className="mt-2 text-4xl font-bold text-ink">Shopify articles</h2>
          <p className="mt-2 text-sm text-slate-500">
            All blog posts synced from Shopify across every blog.{" "}
            <Link to="/blogs" className="font-medium text-ocean underline-offset-4 hover:underline">
              Browse by blog
            </Link>
            .
          </p>
          {focusMissingMeta ? (
            <p className="mt-2 text-sm font-medium text-[#5746d9]">
              Showing articles with SEO title and description both empty ·{" "}
              <Button
                variant="link"
                className="h-auto p-0 text-inherit font-inherit underline-offset-4 hover:underline"
                onClick={() => {
                  const p = new URLSearchParams(searchParams);
                  p.delete("focus");
                  setSearchParams(p, { replace: true });
                }}
              >
                Clear filter
              </Button>
            </p>
          ) : null}
        </div>
        <div className="shrink-0 pt-2">
          <Button
            variant="secondary"
            onClick={() => {
              setDraftForm(emptyDraftForm);
              setDraftError("");
              setSlugTouched(false);
              setDraftProgressEvents([]);
              setDraftModalStep(1);
              setDraftModalOpen(true);
            }}
          >
            <Sparkles className="mr-2" size={16} />
            Draft new article
          </Button>
        </div>
      </div>

      <section className="grid gap-4 xl:grid-cols-4">
        <SummaryCard
          label="Visible rows"
          value={formatNumber(summary.visible_rows)}
          hint="Matching articles across the current filtered list."
          tone="border-[#dbe5f3] bg-[linear-gradient(135deg,#ffffff_0%,#eef6ff_100%)]"
        />
        <SummaryCard
          label="High priority"
          value={formatNumber(summary.high_priority)}
          hint="Articles with the largest SEO upside right now."
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
            if (!ARTICLE_SORT_KEYS.has(key)) return;
            if (sort === key) {
              setDirection(direction === "asc" ? "desc" : "asc");
            } else {
              setSort(key);
              setDirection(
                key === "article_name" || key === "title" || key === "updated_at"
                  ? "asc"
                  : "desc"
              );
            }
          }}
          getRowLink={(row) =>
            `/articles/${encodeURIComponent(String(row.blog_handle))}/${encodeURIComponent(String(row.handle))}`
          }
          isLoading={query.isLoading}
          error={query.error as Error | null}
        />
      </Card>

      {/* Draft New Article modal */}
      <Modal
        open={draftModalOpen}
        onOpenChange={(open) => {
          if (!draftGenerating) {
            setDraftModalOpen(open);
            if (!open) {
              setDraftProgressEvents([]);
              setDraftModalStep(1);
            }
          }
        }}
        title={draftModalStep === 2 ? "Creating your draft" : "Draft new article"}
        description={
          draftModalStep === 2
            ? "Generating content and images, then creating the Shopify draft."
            : "Use AI to write a brand-new SEO-optimised blog article draft"
        }
      >
        <div className="space-y-4">
          {draftModalStep === 1 ? (
            <>
          <div className="grid gap-2">
            <Label htmlFor="draft-blog">Blog</Label>
            <Select
              value={draftForm.blog_id}
              onValueChange={(val) => {
                const blog = (blogsQuery.data ?? []).find((b) => b.id === val);
                setDraftForm((f) => ({
                  ...f,
                  blog_id: val,
                  blog_handle: blog?.handle ?? ""
                }));
              }}
            >
              <SelectTrigger id="draft-blog">
                <SelectValue placeholder={blogsQuery.isLoading ? "Loading blogs…" : "Select a blog"} />
              </SelectTrigger>
              <SelectContent>
                {(blogsQuery.data ?? []).map((blog) => (
                  <SelectItem key={blog.id} value={blog.id}>
                    {blog.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="draft-topic">
              Topic / working title{" "}
              <span className="text-slate-400 font-normal">(required)</span>
            </Label>
            <Input
              id="draft-topic"
              placeholder="e.g. Best Disposable Vapes for Beginners"
              value={draftForm.topic}
              onChange={(e) => {
                const topic = e.target.value;
                setDraftForm((f) =>
                  !slugTouched
                    ? { ...f, topic, slug: defaultDraftSlugHint(topic, f.keywords) }
                    : { ...f, topic }
                );
              }}
            />
            <p className="text-xs text-slate-500">
              Describe the article you want — the AI will generate the headline, SEO title, meta
              description and full body.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="draft-keywords">
              Target keywords{" "}
              <span className="text-slate-400 font-normal">(optional, comma-separated)</span>
            </Label>
            <Textarea
              id="draft-keywords"
              placeholder="disposable vapes, best vape 2026, elf bar"
              value={draftForm.keywords}
              rows={2}
              onChange={(e) => {
                const keywords = e.target.value;
                setDraftForm((f) =>
                  !slugTouched
                    ? { ...f, keywords, slug: defaultDraftSlugHint(f.topic, keywords) }
                    : { ...f, keywords }
                );
              }}
            />
          </div>

          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-2">
              <Label htmlFor="draft-slug">
                URL slug{" "}
                <span className="text-slate-400 font-normal">(Shopify handle)</span>
              </Label>
              <Button
                variant="link"
                className="h-auto p-0 text-xs font-medium text-ocean underline-offset-4 hover:underline disabled:opacity-40"
                disabled={draftGenerating}
                onClick={() => {
                  setSlugTouched(false);
                  setDraftForm((f) => ({
                    ...f,
                    slug: defaultDraftSlugHint(f.topic, f.keywords)
                  }));
                }}
              >
                Reset to suggested
              </Button>
            </div>
            <Input
              id="draft-slug"
              placeholder="e.g. best-disposable-vapes-beginners"
              value={draftForm.slug}
              onChange={(e) => {
                setSlugTouched(true);
                setDraftForm((f) => ({ ...f, slug: e.target.value }));
              }}
            />
            <p className="text-xs text-slate-500">
              Suggested from your topic and first keyword (lowercase, hyphenated). Edit for the exact
              phrase you want in the URL. Clear the field to fall back to the AI-generated headline.
            </p>
          </div>

          <div className="grid gap-2">
            <Label htmlFor="draft-author">
              Author name{" "}
              <span className="text-slate-400 font-normal">(optional)</span>
            </Label>
            <Input
              id="draft-author"
              placeholder="Your brand"
              value={draftForm.author_name}
              onChange={(e) => setDraftForm((f) => ({ ...f, author_name: e.target.value }))}
            />
          </div>

          {draftError ? (
            <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{draftError}</p>
          ) : null}

          <div className="flex justify-end gap-3 pt-2">
            <Button
              variant="secondary"
              onClick={() => {
                setDraftModalOpen(false);
                setDraftProgressEvents([]);
                setDraftModalStep(1);
              }}
              disabled={draftGenerating}
            >
              Cancel
            </Button>
            <Button onClick={() => void submitDraftFromModal()} disabled={!canSubmitDraft}>
              <Sparkles className="mr-2" size={16} />
              Generate & create draft
            </Button>
          </div>
            </>
          ) : (
            <>
              <ArticleDraftProgressPanel
                events={draftProgressEvents}
                isRunning={draftGenerating}
                runKey={draftRunKey}
              />
              {draftError ? (
                <p className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700">{draftError}</p>
              ) : null}
              <div className="flex flex-wrap justify-end gap-3 pt-2">
                {!draftGenerating && draftError ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setDraftModalStep(1);
                      setDraftError("");
                      setDraftProgressEvents([]);
                    }}
                  >
                    Back to details
                  </Button>
                ) : null}
                <Button variant="secondary" disabled={draftGenerating} onClick={() => setDraftModalOpen(false)}>
                  {draftGenerating ? "Please wait…" : "Close"}
                </Button>
              </div>
            </>
          )}
        </div>
      </Modal>
    </div>
  );
}
