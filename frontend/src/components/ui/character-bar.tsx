import { Progress } from "./progress";
import { cn } from "../../lib/utils";

export function CharacterBar({ current, max, goodMin }: { current: number; max: number; goodMin: number }) {
  const pct = Math.min(100, Math.round((current / max) * 100));
  const indicatorColor =
    current === 0 ? "bg-line"
    : current < goodMin ? "bg-amber-400"
    : current <= max ? "bg-emerald-500"
    : "bg-red-500";

  return (
    <Progress
      value={pct}
      className="mt-1.5 h-1 bg-line"
      indicatorClassName={cn("rounded-full transition-all duration-300", indicatorColor)}
    />
  );
}
