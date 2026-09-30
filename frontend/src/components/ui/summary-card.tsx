import { Info } from "lucide-react";
import { Card, CardContent } from "./card";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";

export function SummaryCard({ label, value, tone, hint, compact = false, loading = false, unavailable = false }: {
  label: string; value: string; tone: string; hint: string; compact?: boolean; loading?: boolean; unavailable?: boolean;
}) {
  if (compact) return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[13px] font-medium leading-5 text-slate-600">{label}</p>
          <Popover>
            <PopoverTrigger asChild>
              <button type="button" aria-label={`About ${label.toLowerCase()}`} className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-400 hover:bg-slate-100 hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <Info size={14} aria-hidden />
              </button>
            </PopoverTrigger>
            <PopoverContent className="max-w-[calc(100vw-2rem)] text-sm" side="bottom">{hint}</PopoverContent>
          </Popover>
        </div>
        <strong className="mt-1 block text-[28px] font-semibold leading-tight tracking-tight text-ink tabular-nums" aria-label={loading ? "Loading" : unavailable ? "Unavailable" : undefined}>{loading || unavailable ? "—" : value}</strong>
      </CardContent>
    </Card>
  );
  return (
    <Card className={`rounded-[26px] shadow-panel ${tone}`}>
      <CardContent className="p-5">
        <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{label}</p>
        <strong className="mt-4 block text-4xl font-bold text-ink">{value}</strong>
        <p className="mt-3 text-sm text-slate-600">{hint}</p>
      </CardContent>
    </Card>
  );
}
