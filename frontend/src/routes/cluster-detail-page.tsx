import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { z } from "zod";

import { getJson } from "../lib/api";
import { clusterSchema } from "../types/api";
import { Button } from "../components/ui/button";
import { Modal } from "../components/ui/modal";
import { Skeleton } from "../components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { ClusterKeywordsTable } from "./keywords/cluster-keywords-table";
import { clusterFormatMatchHint } from "./keywords/cluster-ui";
import { targetPayloadSchema, type TargetKeyword } from "./keywords/schemas";


const relatedUrlSchema = z.object({
  url_type: z.string(),
  handle: z.string(),
  title: z.string(),
  source: z.string(),
  keyword_coverage: z.object({
    found: z.number(),
    total: z.number(),
    keywords_found: z.array(z.string()),
    keywords_missing: z.array(z.string()),
  }),
});

const clusterDetailPayloadSchema = z.object({
  cluster: clusterSchema,
  related_urls: z.array(relatedUrlSchema),
});

const CONTENT_TYPE_COLORS: Record<string, string> = {
  collection_page: "bg-blue-100 text-blue-700",
  product_page: "bg-purple-100 text-purple-700",
  blog_post: "bg-green-100 text-green-700",
  buying_guide: "bg-amber-100 text-amber-700",
  landing_page: "bg-rose-100 text-rose-700",
};

const CONTENT_TYPE_LABELS: Record<string, string> = {
  collection_page: "Collection Page",
  product_page: "Product Page",
  blog_post: "Blog Post",
  buying_guide: "Buying Guide",
  landing_page: "Landing Page",
};

function coverageColor(found: number, total: number) {
  if (total === 0) return "bg-slate-100 text-slate-600";
  const pct = found / total;
  if (pct >= 0.5) return "bg-green-100 text-green-700";
  if (pct >= 0.25) return "bg-yellow-100 text-yellow-700";
  return "bg-red-100 text-red-700";
}

function sourceLabel(source: string) {
  switch (source) {
    case "suggested_match": return "Match";
    case "vendor": return "Vendor";
    case "collection_products": return "Collection";
    default: return source;
  }
}

function sourceColor(source: string) {
  switch (source) {
    case "suggested_match": return "bg-blue-50 text-blue-600";
    case "vendor": return "bg-purple-50 text-purple-600";
    case "collection_products": return "bg-amber-50 text-amber-600";
    default: return "bg-slate-50 text-slate-600";
  }
}

function typeLabel(urlType: string) {
  switch (urlType) {
    case "collection": return "Collection";
    case "product": return "Product";
    case "page": return "Page";
    case "blog_article": return "Blog Article";
    default: return urlType;
  }
}

function detailLink(urlType: string, handle: string) {
  switch (urlType) {
    case "collection": return `/collections/${handle}`;
    case "product": return `/products/${handle}`;
    case "page": return `/pages/${handle}`;
    case "blog_article": {
      const [blogHandle, articleHandle] = handle.split("/", 2);
      if (!blogHandle || !articleHandle) return "#";
      return `/articles/${blogHandle}/${articleHandle}`;
    }
    default: return "#";
  }
}

type CoverageModalState = {
  pageTitle: string;
  subtitle: string;
  found: string[];
  missing: string[];
};

export function ClusterDetailPage() {
  const { id = "" } = useParams();
  const [coverageModal, setCoverageModal] = useState<CoverageModalState | null>(null);
  const [keywordsExpanded, setKeywordsExpanded] = useState(true);

  const query = useQuery({
    queryKey: ["cluster-detail", id],
    queryFn: () => getJson(`/api/keywords/clusters/${id}/detail`, clusterDetailPayloadSchema),
    enabled: !!id,
  });

  const targetQuery = useQuery({
    queryKey: ["target-keywords"],
    queryFn: () => getJson("/api/keywords/target", targetPayloadSchema),
    enabled: !!id,
  });

  const keywordMap = useMemo(() => {
    const items = targetQuery.data?.items ?? [];
    const map = new Map<string, TargetKeyword>();
    for (const item of items) {
      map.set(item.keyword.toLowerCase(), item);
    }
    return map;
  }, [targetQuery.data]);

  const keywordCoverageCounts = useMemo(() => {
    const urls = query.data?.related_urls;
    if (!urls || urls.length === 0) return { counts: new Map<string, number>(), total: 0 };
    const counts = new Map<string, number>();
    for (const url of urls) {
      for (const kw of url.keyword_coverage.keywords_found) {
        const key = kw.toLowerCase();
        counts.set(key, (counts.get(key) ?? 0) + 1);
      }
    }
    return { counts, total: urls.length };
  }, [query.data?.related_urls]);

  if (query.isLoading) {
    return (
      <div className="space-y-6 pb-10">
        <Skeleton className="h-5 w-32 rounded-lg" />
        <Skeleton className="h-48 rounded-[24px]" />
        <Skeleton className="h-64 rounded-[24px]" />
      </div>
    );
  }

  if (query.error || !query.data) {
    return (
      <div className="space-y-4">
        <Link to="/keywords" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-ink">
          <ArrowLeft className="h-4 w-4" /> Back to Keywords
        </Link>
        <div className="rounded-[30px] border border-[#ffd2c5] bg-[#fff4ef] p-8 text-[#8f3e20] shadow-panel">
          {(query.error as Error)?.message || "Could not load cluster."}
        </div>
      </div>
    );
  }

  const { cluster, related_urls } = query.data;
  const contentColor = CONTENT_TYPE_COLORS[cluster.content_type] ?? "bg-slate-100 text-slate-600";
  const contentLabel = CONTENT_TYPE_LABELS[cluster.content_type] ?? cluster.content_type;
  const formatHint = clusterFormatMatchHint(
    cluster.content_type,
    cluster.suggested_match?.match_type,
  );

  return (
    <div className="space-y-6 pb-10">
      <Modal
        open={coverageModal !== null}
        onOpenChange={(open) => {
          if (!open) setCoverageModal(null);
        }}
        title="Keyword coverage"
        description={
          coverageModal
            ? `Matched and missing cluster keywords for ${coverageModal.pageTitle}`
            : undefined
        }
      >
        {coverageModal ? (
          <div className="space-y-4 text-sm text-slate-700">
            <p className="text-xs text-slate-500 leading-relaxed">
              <span className="font-medium text-slate-600">How this is counted:</span> we scan the linked
              Shopify fields (title, SEO fields, body/HTML)—not the browser URL bar. A keyword counts only
              when the <span className="font-medium text-slate-600">full phrase</span> appears as a
              contiguous substring (case-insensitive, after stripping HTML tags). Synonyms and scattered
              words do not count.
            </p>
            <p className="text-xs text-slate-500">
              <span className="font-medium text-ink">{coverageModal.pageTitle}</span>
              <span className="text-slate-400"> · {coverageModal.subtitle}</span>
            </p>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800 mb-2">
                  Found in content ({coverageModal.found.length})
                </p>
                <ul className="max-h-[min(320px,45vh)] space-y-1.5 overflow-y-auto text-emerald-900 text-xs">
                  {coverageModal.found.length === 0 ? (
                    <li className="text-slate-500 italic">None</li>
                  ) : (
                    coverageModal.found.map((kw) => (
                      <li
                        key={kw}
                        className="break-words border-b border-emerald-100/80 pb-2 last:border-0 font-medium"
                      >
                        {kw}
                      </li>
                    ))
                  )}
                </ul>
              </div>
              <div className="rounded-xl border border-rose-100 bg-rose-50/50 p-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-rose-800 mb-2">
                  Not detected ({coverageModal.missing.length})
                </p>
                <ul className="max-h-[min(320px,45vh)] space-y-1 overflow-y-auto text-rose-900 text-xs">
                  {coverageModal.missing.length === 0 ? (
                    <li className="text-slate-500 italic">All cluster keywords matched</li>
                  ) : (
                    coverageModal.missing.map((kw) => (
                      <li key={kw} className="break-words border-b border-rose-100/80 pb-1 last:border-0">
                        {kw}
                      </li>
                    ))
                  )}
                </ul>
              </div>
            </div>
          </div>
        ) : null}
      </Modal>

      {/* Back link */}
      <Link to="/keywords" className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-ink">
        <ArrowLeft className="h-4 w-4" /> Back to Keywords
      </Link>

      {/* Cluster info card */}
      <div className="rounded-xl border border-line bg-white p-6 space-y-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-semibold text-ink">{cluster.name}</h1>
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${contentColor}`}
              title="Recommended format from clustering — the linked Shopify URL may be a different type"
            >
              {contentLabel}
            </span>
            {cluster.matched_vendor && (
              <span className="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700 whitespace-nowrap">
                {cluster.matched_vendor.name} · {cluster.matched_vendor.product_count} products
              </span>
            )}
          </div>
          <p className="text-sm font-medium text-slate-700">Primary: {cluster.primary_keyword}</p>
          <p className="text-sm text-slate-500">{cluster.content_brief}</p>
          {formatHint ? (
            <p className="text-xs text-amber-800 bg-amber-50 border border-amber-100 rounded-lg px-2.5 py-2 mt-2">
              {formatHint}
            </p>
          ) : null}
        </div>

        {/* Stats row */}
        <div className="flex flex-wrap gap-4 text-sm text-slate-500">
          <span>
            Volume: <span className="font-medium text-ink">{cluster.total_volume.toLocaleString()}</span>
          </span>
          <span>
            Avg difficulty: <span className="font-medium text-ink">{cluster.avg_difficulty}</span>
          </span>
          <span>
            Avg opportunity: <span className="font-medium text-ink">{cluster.avg_opportunity}</span>
          </span>
          <span>
            Keywords: <span className="font-medium text-ink">{cluster.keyword_count}</span>
          </span>
        </div>

        {/* Suggested match */}
        <div className="flex items-center gap-2 text-sm">
          {cluster.suggested_match ? (
            cluster.suggested_match.match_type === "new" ? (
              <span className="inline-flex items-center gap-1">
                <span className="text-slate-400">→</span>
                <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">New content</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1">
                <span className="text-slate-400">→</span>
                <Link
                  to={detailLink(cluster.suggested_match.match_type, cluster.suggested_match.match_handle)}
                  className="text-blue-600 hover:text-blue-800 hover:underline"
                >
                  {cluster.suggested_match.match_title}
                </Link>
                <span className="text-xs text-slate-400">
                  ({typeLabel(cluster.suggested_match.match_type)})
                </span>
              </span>
            )
          ) : (
            <span className="text-slate-400">→ No match suggested</span>
          )}
        </div>
      </div>

      {/* Cluster keywords (metrics from target keywords list) */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-ink">Keywords ({cluster.keywords.length})</h2>
          <Button
            variant="link"
            className="h-auto p-0 text-xs font-medium text-blue-600 hover:text-blue-800"
            onClick={() => setKeywordsExpanded((v) => !v)}
          >
            {keywordsExpanded ? "Hide keywords ▲" : `Show ${cluster.keywords.length} keywords ▼`}
          </Button>
        </div>
        {keywordsExpanded ? (
          targetQuery.isLoading ? (
            <div className="rounded-xl border border-line bg-white p-6">
              <Skeleton className="h-40 w-full rounded-lg" />
            </div>
          ) : (
            <ClusterKeywordsTable keywords={cluster.keywords} keywordMap={keywordMap} coverageCounts={keywordCoverageCounts.counts} coverageTotal={keywordCoverageCounts.total} />
          )
        ) : null}
      </div>

      {/* Related URLs section */}
      <div className="space-y-3">
        <h2 className="text-lg font-semibold text-ink">Related URLs ({related_urls.length})</h2>

        {related_urls.length === 0 ? (
          <div className="rounded-xl border border-line bg-white p-6 text-center text-sm text-slate-400">
            No related URLs discovered for this cluster.
          </div>
        ) : (
          <div className="rounded-xl border border-line bg-white">
            <Table className="w-full text-sm">
              <TableHeader>
                <TableRow className="border-b border-line text-left text-xs text-slate-500">
                  <TableHead className="px-4 py-3">Title</TableHead>
                  <TableHead className="px-4 py-3">Type</TableHead>
                  <TableHead className="px-4 py-3">Source</TableHead>
                  <TableHead
                    className="px-4 py-3 text-right"
                    title="Keywords found in page text: full phrase must appear as a contiguous substring"
                  >
                    Coverage
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {related_urls.map((url) => {
                  const cov = url.keyword_coverage;
                  return (
                    <TableRow key={`${url.url_type}-${url.handle}`} className="border-b border-line last:border-0">
                      <TableCell className="px-4 py-3">
                        <Link
                          to={detailLink(url.url_type, url.handle)}
                          className="font-medium text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          {url.title}
                        </Link>
                      </TableCell>
                      <TableCell className="px-4 py-3 text-slate-600">{typeLabel(url.url_type)}</TableCell>
                      <TableCell className="px-4 py-3">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${sourceColor(url.source)}`}>
                          {sourceLabel(url.source)}
                        </span>
                      </TableCell>
                      <TableCell className="px-4 py-3 text-right">
                        <Button
                          variant="ghost"
                          className={`h-auto rounded-full px-2 py-0.5 text-xs font-medium cursor-pointer transition hover:ring-2 hover:ring-blue-400/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${coverageColor(cov.found, cov.total)}`}
                          title="Show which cluster keywords were detected in this URL’s text"
                          onClick={() =>
                            setCoverageModal({
                              pageTitle: url.title,
                              subtitle: `${typeLabel(url.url_type)} · ${url.handle}`,
                              found: cov.keywords_found,
                              missing: cov.keywords_missing,
                            })
                          }
                        >
                          {cov.found}/{cov.total}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
}
