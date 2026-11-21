import { useQuery } from "@tanstack/react-query";
import { TrendingDown, AlertTriangle, ShoppingCart, BarChart3, ArrowDown, ArrowUp, ExternalLink } from "lucide-react";
import { useState } from "react";
import { z } from "zod";

import { getJson } from "../lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Skeleton } from "../components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { cn } from "../lib/utils";

// --- Schemas ---

const funnelFlagItemSchema = z.object({
  page_url: z.string(),
  object_type: z.string(),
  object_handle: z.string(),
  sessions: z.number(),
  flag: z.string(),
  add_to_carts: z.number().optional(),
  checkouts: z.number().optional(),
  purchases: z.number().optional(),
  revenue: z.number().optional(),
});

const funnelFlagsResponseSchema = z.object({
  ok: z.boolean(),
  data: z.object({
    high_sessions_no_carts: z.array(funnelFlagItemSchema),
    checkout_assists: z.array(funnelFlagItemSchema),
    window_days: z.number(),
  }),
});

const cannibalizationUrlSchema = z.object({
  url: z.string(),
  object_type: z.string(),
  object_handle: z.string(),
  clicks: z.number(),
  impressions: z.number(),
  ctr: z.number(),
  position: z.number(),
});

const cannibalizationQuerySchema = z.object({
  query: z.string(),
  urls: z.array(cannibalizationUrlSchema),
  total_impressions: z.number(),
  total_clicks: z.number(),
});

const cannibalizationResponseSchema = z.object({
  ok: z.boolean(),
  data: z.object({
    queries: z.array(cannibalizationQuerySchema),
    total: z.number(),
    limit: z.number(),
    offset: z.number(),
    has_more: z.boolean(),
  }),
});

const decayPageSchema = z.object({
  page_url: z.string(),
  object_type: z.string(),
  object_handle: z.string(),
  current: z.object({
    clicks: z.number(),
    impressions: z.number(),
    ctr: z.number(),
    position: z.number(),
  }),
  prior: z.object({
    clicks: z.number(),
    impressions: z.number(),
    ctr: z.number(),
    position: z.number(),
  }),
  changes: z.object({
    clicks_pct: z.number(),
    impressions_pct: z.number(),
  }),
  decay_reasons: z.array(z.string()),
  window: z.object({
    current_start: z.string(),
    current_end: z.string(),
    prior_start: z.string(),
    prior_end: z.string(),
  }),
});

const decayWatchlistResponseSchema = z.object({
  ok: z.boolean(),
  data: z.object({
    pages: z.array(decayPageSchema),
    total: z.number(),
    limit: z.number(),
    offset: z.number(),
    has_more: z.boolean(),
  }),
});

type FunnelFlagItem = z.infer<typeof funnelFlagItemSchema>;
type CannibalizationQuery = z.infer<typeof cannibalizationQuerySchema>;
type DecayPage = z.infer<typeof decayPageSchema>;

// --- Components ---

function ChangeIndicator({ value, suffix = "%" }: { value: number; suffix?: string }) {
  const isNegative = value < 0;
  return (
    <span className={cn("inline-flex items-center gap-0.5 font-medium", isNegative ? "text-red-600" : "text-green-600")}>
      {isNegative ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />}
      {Math.abs(value).toFixed(1)}{suffix}
    </span>
  );
}

function ObjectLink({ type, handle }: { type: string; handle: string }) {
  if (!handle) return <span className="text-muted-foreground">—</span>;
  const path = type === "product" ? `/products/${handle}` : type === "collection" ? `/collections/${handle}` : type === "page" ? `/pages/${handle}` : type === "blog_article" ? `/articles/${handle.replace("/", "/")}` : null;
  if (!path) return <span>{handle}</span>;
  return (
    <a href={`/app${path}`} className="text-blue-600 hover:underline inline-flex items-center gap-1">
      {handle}
      <ExternalLink className="h-3 w-3" />
    </a>
  );
}

function FunnelFlagsSection({ windowDays }: { windowDays: 28 | 90 }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics", "funnel-flags", windowDays],
    queryFn: () => getJson(`/api/analytics/organic-funnel-flags?window_days=${windowDays}`, funnelFlagsResponseSchema),
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) return <Skeleton className="h-48" />;
  if (error || !data?.ok) return <div className="text-red-500">Failed to load funnel flags</div>;

  const { high_sessions_no_carts, checkout_assists } = data.data;

  return (
    <div className="space-y-6">
      {/* High Sessions, Zero Carts */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <AlertTriangle className="h-4 w-4 text-amber-500" />
            High Organic Sessions, Zero Add-to-Carts
            <Badge variant="outline" className="ml-2">{high_sessions_no_carts.length}</Badge>
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Landing pages receiving organic traffic but not driving any add-to-cart events. Consider improving product visibility or CTA placement.
          </p>
        </CardHeader>
        <CardContent>
          {high_sessions_no_carts.length === 0 ? (
            <p className="text-muted-foreground text-sm">No pages flagged</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Page</TableHead>
                  <TableHead>Object</TableHead>
                  <TableHead className="text-right">Sessions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {high_sessions_no_carts.slice(0, 20).map((item, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs max-w-[300px] truncate">{item.page_url}</TableCell>
                    <TableCell><ObjectLink type={item.object_type} handle={item.object_handle} /></TableCell>
                    <TableCell className="text-right">{item.sessions}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Checkout Assists */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <ShoppingCart className="h-4 w-4 text-green-500" />
            Checkout / Purchase Assists
            <Badge variant="outline" className="ml-2">{checkout_assists.length}</Badge>
          </CardTitle>
          <p className="text-sm text-muted-foreground">
            Landing pages that contributed to checkout or purchase events from organic search.
          </p>
        </CardHeader>
        <CardContent>
          {checkout_assists.length === 0 ? (
            <p className="text-muted-foreground text-sm">No pages flagged</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Page</TableHead>
                  <TableHead>Object</TableHead>
                  <TableHead className="text-right">Sessions</TableHead>
                  <TableHead className="text-right">Add to Carts</TableHead>
                  <TableHead className="text-right">Checkouts</TableHead>
                  <TableHead className="text-right">Purchases</TableHead>
                  <TableHead className="text-right">Revenue</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {checkout_assists.slice(0, 20).map((item, i) => (
                  <TableRow key={i}>
                    <TableCell className="font-mono text-xs max-w-[200px] truncate">{item.page_url}</TableCell>
                    <TableCell><ObjectLink type={item.object_type} handle={item.object_handle} /></TableCell>
                    <TableCell className="text-right">{item.sessions}</TableCell>
                    <TableCell className="text-right">{item.add_to_carts ?? 0}</TableCell>
                    <TableCell className="text-right">{item.checkouts ?? 0}</TableCell>
                    <TableCell className="text-right">{item.purchases ?? 0}</TableCell>
                    <TableCell className="text-right">${(item.revenue ?? 0).toFixed(2)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CannibalizationSection() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics", "cannibalization"],
    queryFn: () => getJson("/api/analytics/cannibalization?min_impressions=20&limit=50", cannibalizationResponseSchema),
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) return <Skeleton className="h-48" />;
  if (error || !data?.ok) return <div className="text-red-500">Failed to load cannibalization data</div>;

  const queries = data.data.queries;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <AlertTriangle className="h-4 w-4 text-orange-500" />
          Query × Page Cannibalization
          <Badge variant="outline" className="ml-2">{data.data.total}</Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Queries where 2+ of your URLs rank with meaningful impressions. This may dilute click potential and confuse Google about intent.
        </p>
      </CardHeader>
      <CardContent>
        {queries.length === 0 ? (
          <p className="text-muted-foreground text-sm">No cannibalization detected</p>
        ) : (
          <div className="space-y-4">
            {queries.slice(0, 10).map((q, i) => (
              <div key={i} className="border rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="font-medium text-sm">&quot;{q.query}&quot;</span>
                  <div className="text-xs text-muted-foreground">
                    {q.total_clicks} clicks · {q.total_impressions} impressions
                  </div>
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>URL</TableHead>
                      <TableHead>Object</TableHead>
                      <TableHead className="text-right">Clicks</TableHead>
                      <TableHead className="text-right">Impressions</TableHead>
                      <TableHead className="text-right">CTR</TableHead>
                      <TableHead className="text-right">Position</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {q.urls.map((url, j) => (
                      <TableRow key={j}>
                        <TableCell className="font-mono text-xs max-w-[250px] truncate">{url.url}</TableCell>
                        <TableCell><ObjectLink type={url.object_type} handle={url.object_handle} /></TableCell>
                        <TableCell className="text-right">{url.clicks}</TableCell>
                        <TableCell className="text-right">{url.impressions}</TableCell>
                        <TableCell className="text-right">{url.ctr.toFixed(1)}%</TableCell>
                        <TableCell className="text-right">{url.position.toFixed(1)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DecayWatchlistSection() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["analytics", "decay-watchlist"],
    queryFn: () => getJson("/api/analytics/decay-watchlist?min_prior_impressions=100&limit=50", decayWatchlistResponseSchema),
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) return <Skeleton className="h-48" />;
  if (error || !data?.ok) return <div className="text-red-500">Failed to load decay watchlist</div>;

  const pages = data.data.pages;

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <TrendingDown className="h-4 w-4 text-red-500" />
          Decay Watch-List
          <Badge variant="outline" className="ml-2">{data.data.total}</Badge>
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          Pages with declining GSC metrics (clicks or impressions down) in the last 28 days vs prior 28 days.
        </p>
      </CardHeader>
      <CardContent>
        {pages.length === 0 ? (
          <p className="text-muted-foreground text-sm">No pages with significant decay detected</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Page</TableHead>
                <TableHead>Object</TableHead>
                <TableHead className="text-right">Prior Clicks</TableHead>
                <TableHead className="text-right">Current Clicks</TableHead>
                <TableHead className="text-right">Change</TableHead>
                <TableHead className="text-right">Prior Impr.</TableHead>
                <TableHead className="text-right">Current Impr.</TableHead>
                <TableHead className="text-right">Change</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pages.slice(0, 20).map((page, i) => (
                <TableRow key={i}>
                  <TableCell className="font-mono text-xs max-w-[200px] truncate">{page.page_url}</TableCell>
                  <TableCell><ObjectLink type={page.object_type} handle={page.object_handle} /></TableCell>
                  <TableCell className="text-right">{page.prior.clicks}</TableCell>
                  <TableCell className="text-right">{page.current.clicks}</TableCell>
                  <TableCell className="text-right"><ChangeIndicator value={page.changes.clicks_pct} /></TableCell>
                  <TableCell className="text-right">{page.prior.impressions}</TableCell>
                  <TableCell className="text-right">{page.current.impressions}</TableCell>
                  <TableCell className="text-right"><ChangeIndicator value={page.changes.impressions_pct} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

export function AnalyticsPage() {
  const [funnelWindow, setFunnelWindow] = useState<28 | 90>(28);

  return (
    <div className="rounded-[30px] border border-white/70 bg-white/90 p-8 shadow-panel space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
        <p className="text-muted-foreground mt-1">
          Advanced SEO analytics from Google Analytics 4 and Search Console data
        </p>
      </div>

      {/* Organic Funnel Section */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-medium flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            Organic Funnel by Landing Page
          </h2>
          <div className="flex gap-2">
            <Button
              variant={funnelWindow === 28 ? "default" : "outline"}
              size="sm"
              onClick={() => setFunnelWindow(28)}
            >
              28 days
            </Button>
            <Button
              variant={funnelWindow === 90 ? "default" : "outline"}
              size="sm"
              onClick={() => setFunnelWindow(90)}
            >
              90 days
            </Button>
          </div>
        </div>
        <FunnelFlagsSection windowDays={funnelWindow} />
      </section>

      {/* Cannibalization Section */}
      <section>
        <h2 className="text-lg font-medium flex items-center gap-2 mb-4">
          <AlertTriangle className="h-5 w-5" />
          Cannibalization Report
        </h2>
        <CannibalizationSection />
      </section>

      {/* Decay Watchlist Section */}
      <section>
        <h2 className="text-lg font-medium flex items-center gap-2 mb-4">
          <TrendingDown className="h-5 w-5" />
          Decay Watch-List
        </h2>
        <DecayWatchlistSection />
      </section>
    </div>
  );
}
