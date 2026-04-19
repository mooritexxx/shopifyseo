// ---------------------------------------------------------------------------
// Shared badge components and filter UI for the Keywords page
// ---------------------------------------------------------------------------

import { ChevronDown } from "lucide-react";

import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";

export const SOURCE_COLORS: Record<string, string> = {
  brand: "bg-blue-100 text-blue-700",
  collection: "bg-purple-100 text-purple-700",
  product_type: "bg-amber-100 text-amber-700",
  industry: "bg-emerald-100 text-emerald-700",
  manual: "bg-slate-100 text-slate-600"
};

export const CONTENT_TYPE_COLORS: Record<string, string> = {
  collection_page: "bg-purple-100 text-purple-700",
  product_page: "bg-blue-100 text-blue-700",
  blog_post: "bg-green-100 text-green-700",
  buying_guide: "bg-yellow-100 text-yellow-700",
  landing_page: "bg-indigo-100 text-indigo-700",
};

export const CONTENT_TYPE_LABELS: Record<string, string> = {
  collection_page: "Collection Page",
  product_page: "Product Page",
  blog_post: "Blog Post",
  buying_guide: "Buying Guide",
  landing_page: "Landing Page",
};

const INTENT_COLORS: Record<string, string> = {
  informational: "bg-blue-100 text-blue-700",
  commercial: "bg-purple-100 text-purple-700",
  transactional: "bg-green-100 text-green-700",
  branded: "bg-amber-100 text-amber-700"
};

export const RANKING_COLORS: Record<string, string> = {
  ranking: "bg-blue-100 text-blue-700",
  quick_win: "bg-green-100 text-green-700",
  striking_distance: "bg-yellow-100 text-yellow-700",
  low_visibility: "bg-orange-100 text-orange-700",
  not_ranking: "bg-slate-100 text-slate-500"
};

export const RANKING_LABELS: Record<string, string> = {
  ranking: "Ranking",
  quick_win: "Quick Win",
  striking_distance: "Striking Dist.",
  low_visibility: "Low Visibility",
  not_ranking: "Not Ranking"
};

// ---------------------------------------------------------------------------
// Filter types and options
// ---------------------------------------------------------------------------

export type IntentFilter = "all" | "informational" | "commercial" | "transactional" | "branded";
export type StatusFilter = "all" | "new" | "approved" | "dismissed";
export type DifficultyFilter = "all" | "easy" | "medium" | "hard";
export type RankingFilter = "all" | "ranking" | "quick_win" | "striking_distance" | "low_visibility" | "not_ranking";

export const INTENT_OPTIONS: { value: IntentFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "informational", label: "Informational" },
  { value: "commercial", label: "Commercial" },
  { value: "transactional", label: "Transactional" },
  { value: "branded", label: "Branded" }
];

export const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "new", label: "New" },
  { value: "approved", label: "Approved" },
  { value: "dismissed", label: "Dismissed" }
];

export const DIFFICULTY_OPTIONS: { value: DifficultyFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "easy", label: "Easy 0–20" },
  { value: "medium", label: "Medium 21–50" },
  { value: "hard", label: "Hard 51–70" }
];

export const RANKING_OPTIONS: { value: RankingFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ranking", label: "Ranking" },
  { value: "quick_win", label: "Quick Win" },
  { value: "striking_distance", label: "Striking Dist." },
  { value: "low_visibility", label: "Low Visibility" },
  { value: "not_ranking", label: "Not Ranking" }
];

export type VolumeFilter =
  | "all"
  | "v0"
  | "v1_100"
  | "v101_500"
  | "v501_2000"
  | "v2001";

export const VOLUME_OPTIONS: { value: VolumeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "v0", label: "Vol. 0" },
  { value: "v1_100", label: "Vol. 1–100" },
  { value: "v101_500", label: "Vol. 101–500" },
  { value: "v501_2000", label: "Vol. 501–2k" },
  { value: "v2001", label: "Vol. 2k+" }
];

/** Buckets align with OpportunityBadge coloring (70+ / 30–69 / under 30). */
export type OpportunityFilter = "all" | "opp_high" | "opp_mid" | "opp_low" | "opp_none";

export const OPPORTUNITY_OPTIONS: { value: OpportunityFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "opp_high", label: "High (70+)" },
  { value: "opp_mid", label: "Mid (30–69)" },
  { value: "opp_low", label: "Low (1–29)" },
  { value: "opp_none", label: "No score" }
];

export type TrafficPotentialFilter =
  | "all"
  | "tp0"
  | "tp1_500"
  | "tp501_2000"
  | "tp2001";

export const TRAFFIC_POTENTIAL_OPTIONS: { value: TrafficPotentialFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "tp0", label: "TP 0" },
  { value: "tp1_500", label: "TP 1–500" },
  { value: "tp501_2000", label: "TP 501–2k" },
  { value: "tp2001", label: "TP 2k+" }
];

/** Sentinel for “no content type” in target keyword filters. */
export const CONTENT_TYPE_UNSET = "__unset__";

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

interface FilterGroupProps<T extends string> {
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}

export function FilterGroup<T extends string>({ options, value, onChange }: FilterGroupProps<T>) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => (
        <Button
          key={opt.value}
          type="button"
          variant="ghost"
          onClick={() => onChange(opt.value)}
          className={`h-auto rounded-full px-3 py-1 text-xs font-medium transition ${
            value === opt.value
              ? "bg-ink text-white hover:bg-ink/90"
              : "bg-slate-100 text-slate-500 hover:bg-slate-200"
          }`}
        >
          {opt.label}
        </Button>
      ))}
    </div>
  );
}

export function FilterDropdown<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  const active = value !== ("all" as T);
  const displayLabel = active
    ? options.find((o) => o.value === value)?.label ?? label
    : label;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition whitespace-nowrap ${
            active
              ? "border-[#2e6be6]/30 bg-[#2e6be6]/5 text-[#2e6be6]"
              : "border-line bg-white text-slate-600 hover:border-slate-300 hover:text-ink"
          }`}
        >
          {displayLabel}
          <ChevronDown size={12} className="opacity-50" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[140px]">
        <DropdownMenuRadioGroup
          value={value}
          onValueChange={(v) => onChange(v as T)}
        >
          {options.map((opt) => (
            <DropdownMenuRadioItem key={opt.value} value={opt.value}>
              {opt.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function DifficultyBadge({ kd }: { kd: number | null }) {
  if (kd === null) return <span className="text-slate-400">—</span>;
  const color =
    kd <= 20
      ? "bg-green-100 text-green-700"
      : kd <= 50
        ? "bg-yellow-100 text-yellow-700"
        : "bg-red-100 text-red-700";
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${color}`}>{kd}</span>;
}

export function OpportunityBadge({
  opp,
  decimals,
}: {
  opp: number | null;
  decimals?: number;
}) {
  if (opp === null) return <span className="text-slate-400">—</span>;
  const color =
    opp >= 70
      ? "bg-green-100 text-green-700"
      : opp >= 30
        ? "bg-yellow-100 text-yellow-700"
        : "bg-slate-100 text-slate-500";
  const text = decimals != null ? opp.toFixed(decimals) : String(opp);
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium tabular-nums ${color}`}>{text}</span>;
}

export function IntentBadge({ intent }: { intent: string | null }) {
  if (!intent) return <span className="text-slate-400">—</span>;
  const color = INTENT_COLORS[intent.toLowerCase()] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium capitalize ${color}`}>
      {intent}
    </span>
  );
}

export function RankingBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-slate-400">—</span>;
  const color = RANKING_COLORS[status] ?? "bg-slate-100 text-slate-500";
  const label = RANKING_LABELS[status] ?? status;
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${color}`}>
      {label}
    </span>
  );
}
