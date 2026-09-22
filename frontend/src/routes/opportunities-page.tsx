import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Inbox,
  TrendingUp,
  Target,
  Zap,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Search,
} from "lucide-react";

import { Button } from "../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { getJson } from "../lib/api";
import { cn } from "../lib/utils";
import { opportunitiesPayloadSchema, opportunityStatsSchema } from "../types/api";

type PageTypeFilter = "all" | "product" | "collection" | "page" | "blog_article";
type SortKey = "opportunity_score" | "impressions" | "clicks" | "position" | "ctr";
type SortDir = "asc" | "desc";

const PAGE_TYPE_OPTIONS: { value: PageTypeFilter; label: string }[] = [
  { value: "all", label: "All Pages" },
  { value: "product", label: "Products" },
  { value: "collection", label: "Collections" },
  { value: "page", label: "Pages" },
  { value: "blog_article", label: "Articles" },
];

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "opportunity_score", label: "Opportunity Score" },
  { value: "impressions", label: "Impressions" },
  { value: "clicks", label: "Clicks" },
  { value: "position", label: "Position" },
  { value: "ctr", label: "CTR" },
];

function ScoreBadge({ score }: { score: number }) {
  const color =
    score >= 70
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : score >= 50
        ? "bg-amber-50 text-amber-700 border-amber-200"
        : "bg-slate-50 text-slate-600 border-slate-200";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-xs font-semibold ${color}`}>
      {score.toFixed(0)}
    </span>
  );
}

function PositionBadge({ position }: { position: number }) {
  const color =
    position <= 3
      ? "text-emerald-600"
      : position <= 10
        ? "text-blue-600"
        : position <= 20
          ? "text-amber-600"
          : "text-slate-500";
  return <span className={`font-semibold ${color}`}>{position.toFixed(1)}</span>;
}

function PageTypeBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    Product: "bg-purple-50 text-purple-700 border-purple-200",
    Collection: "bg-blue-50 text-blue-700 border-blue-200",
    Page: "bg-slate-50 text-slate-700 border-slate-200",
    "Blog Article": "bg-emerald-50 text-emerald-700 border-emerald-200",
  };
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${colors[type] || colors.Page}`}>
      {type}
    </span>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  description,
}: {
  label: string;
  value: number | string;
  icon: typeof Inbox;
  description?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-2 text-slate-500">
        <Icon className="h-4 w-4" />
        <span className="text-sm">{label}</span>
      </div>
      <div className="mt-1 text-2xl font-semibold text-slate-800">
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
      {description && <p className="mt-1 text-xs text-slate-400">{description}</p>}
    </div>
  );
}

export function OpportunitiesPage() {
  const [pageType, setPageType] = useState<PageTypeFilter>("all");
  const [sortBy, setSortBy] = useState<SortKey>("opportunity_score");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [limit] = useState(50);
  const [offset, setOffset] = useState(0);

  const statsQuery = useQuery({
    queryKey: ["opportunities-stats"],
    queryFn: () => getJson("/api/opportunities/stats", opportunityStatsSchema),
    staleTime: 60_000,
  });

  const opportunitiesQuery = useQuery({
    queryKey: ["opportunities", pageType, sortBy, sortDir, limit, offset],
    queryFn: () => {
      const params = new URLSearchParams();
      if (pageType !== "all") params.set("page_type", pageType);
      params.set("sort_by", sortBy);
      params.set("sort_dir", sortDir);
      params.set("limit", String(limit));
      params.set("offset", String(offset));
      return getJson(`/api/opportunities?${params}`, opportunitiesPayloadSchema);
    },
    staleTime: 30_000,
  });

  const stats = statsQuery.data;
  const opportunities = opportunitiesQuery.data;

  const toggleSort = (key: SortKey) => {
    if (sortBy === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortBy(key);
      setSortDir(key === "position" ? "asc" : "desc");
    }
    setOffset(0);
  };

  const SortHeader = ({ field, children }: { field: SortKey; children: React.ReactNode }) => (
    <button
      onClick={() => toggleSort(field)}
      className="inline-flex items-center gap-1 hover:text-slate-900"
    >
      {children}
      {sortBy === field &&
        (sortDir === "asc" ? (
          <ChevronUp className="h-3 w-3" />
        ) : (
          <ChevronDown className="h-3 w-3" />
        ))}
    </button>
  );

  const getDetailLink = (objectType: string, objectHandle: string) => {
    if (objectType === "product") return `/products/${objectHandle}`;
    if (objectType === "collection") return `/collections/${objectHandle}`;
    if (objectType === "page") return `/pages/${objectHandle}`;
    if (objectType === "blog_article") {
      const parts = objectHandle.split("/");
      if (parts.length === 2) return `/articles/${parts[0]}/${parts[1]}`;
    }
    return null;
  };

  return (
    <div className="rounded-[30px] border border-white/70 bg-white/90 p-6 shadow-panel">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 text-white">
            <Inbox className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-slate-800">Opportunity Inbox</h1>
            <p className="text-sm text-slate-500">
              GSC queries with untapped SEO potential
            </p>
          </div>
        </div>
      </div>

      {stats && (
        <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <StatCard
            label="Total Queries"
            value={stats.total_queries}
            icon={Search}
            description="All tracked GSC queries"
          />
          <StatCard
            label="Striking Distance"
            value={stats.striking_distance}
            icon={Target}
            description="Positions 4-20"
          />
          <StatCard
            label="Quick Wins"
            value={stats.quick_wins}
            icon={Zap}
            description="Pos 11-20, 50+ impressions"
          />
          <StatCard
            label="Page Types"
            value={Object.keys(stats.by_page_type).length}
            icon={TrendingUp}
          />
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Select value={pageType} onValueChange={(v) => { setPageType(v as PageTypeFilter); setOffset(0); }}>
          <SelectTrigger className="w-[160px]">
            <SelectValue placeholder="Page Type" />
          </SelectTrigger>
          <SelectContent>
            {PAGE_TYPE_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="gap-1">
              Sort: {SORT_OPTIONS.find((o) => o.value === sortBy)?.label}
              <ChevronDown className="h-3 w-3" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            <DropdownMenuRadioGroup value={sortBy} onValueChange={(v) => { setSortBy(v as SortKey); setOffset(0); }}>
              {SORT_OPTIONS.map((opt) => (
                <DropdownMenuRadioItem key={opt.value} value={opt.value}>
                  {opt.label}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        {opportunities && (
          <span className="ml-auto text-sm text-slate-500">
            Showing {opportunities.items.length} of {opportunities.total} opportunities
          </span>
        )}
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200">
        <Table>
          <TableHeader>
            <TableRow className="bg-slate-50">
              <TableHead className="w-[300px]">Query</TableHead>
              <TableHead>Page Type</TableHead>
              <TableHead className="text-right">
                <SortHeader field="opportunity_score">Score</SortHeader>
              </TableHead>
              <TableHead className="text-right">
                <SortHeader field="position">Position</SortHeader>
              </TableHead>
              <TableHead className="text-right">
                <SortHeader field="impressions">Impressions</SortHeader>
              </TableHead>
              <TableHead className="text-right">
                <SortHeader field="clicks">Clicks</SortHeader>
              </TableHead>
              <TableHead className="text-right">
                <SortHeader field="ctr">CTR</SortHeader>
              </TableHead>
              <TableHead>Suggested Action</TableHead>
              <TableHead className="w-[50px]"></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {opportunitiesQuery.isLoading && (
              <TableRow>
                <TableCell colSpan={9} className="py-8 text-center text-slate-500">
                  Loading opportunities…
                </TableCell>
              </TableRow>
            )}
            {opportunities?.items.length === 0 && (
              <TableRow>
                <TableCell colSpan={9} className="py-8 text-center text-slate-500">
                  No opportunities found. Run a GSC sync to populate data.
                </TableCell>
              </TableRow>
            )}
            {opportunities?.items.map((opp) => {
              const detailLink = getDetailLink(opp.object_type, opp.object_handle);
              return (
                <TableRow key={opp.id} className="hover:bg-slate-50/50">
                  <TableCell>
                    <div className="max-w-[300px]">
                      <span className="font-medium text-slate-800" title={opp.query}>
                        {opp.query.length > 50 ? `${opp.query.slice(0, 50)}…` : opp.query}
                      </span>
                      <p className="mt-0.5 truncate text-xs text-slate-400" title={opp.page_url}>
                        {opp.page_url}
                      </p>
                    </div>
                  </TableCell>
                  <TableCell>
                    <PageTypeBadge type={opp.page_type} />
                  </TableCell>
                  <TableCell className="text-right">
                    <ScoreBadge score={opp.opportunity_score} />
                  </TableCell>
                  <TableCell className="text-right">
                    <PositionBadge position={opp.position} />
                  </TableCell>
                  <TableCell className="text-right font-medium">
                    {opp.impressions.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right font-medium">
                    {opp.clicks.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <span className={cn(
                      "font-medium",
                      opp.ctr < 1 ? "text-red-500" : opp.ctr < 3 ? "text-amber-500" : "text-emerald-500"
                    )}>
                      {opp.ctr.toFixed(1)}%
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="text-xs text-slate-600">{opp.suggested_action}</span>
                  </TableCell>
                  <TableCell>
                    {detailLink && (
                      <Link
                        to={detailLink}
                        className="inline-flex items-center text-blue-600 hover:text-blue-800"
                      >
                        <ExternalLink className="h-4 w-4" />
                      </Link>
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {opportunities && opportunities.has_more && (
        <div className="mt-4 flex justify-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - limit))}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!opportunities.has_more}
            onClick={() => setOffset(offset + limit)}
          >
            Next
          </Button>
        </div>
      )}
    </div>
  );
}

export default OpportunitiesPage;
