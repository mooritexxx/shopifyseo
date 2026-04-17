import { Check, ChevronRight, Loader2, RefreshCw } from "lucide-react";
import { cn } from "../../../lib/utils";

type SyncPillProps = {
  drawerOpen: boolean;
  onToggle: () => void;
  running: boolean;
  hasError: boolean;
  doneVisible: boolean;
  accent: string;
  title: string;
  subtitle: string;
};

export function SyncPill({
  drawerOpen,
  onToggle,
  running,
  hasError,
  doneVisible,
  accent,
  title,
  subtitle
}: SyncPillProps) {
  const statusColor = hasError ? "var(--sync-danger, #ea6075)" : doneVisible ? "var(--sync-success, #91efbb)" : running ? accent : "rgba(255,255,255,0.3)";

  return (
    <button
      type="button"
      onClick={onToggle}
      className={cn(
        "relative flex w-full cursor-pointer items-center gap-2.5 overflow-hidden rounded-[14px] border border-solid p-3 text-left text-white transition-[background,border-color] duration-200",
        drawerOpen ? "border-white/[0.12] bg-white/[0.06]" : "",
        !drawerOpen && hasError ? "border-[rgba(234,96,117,0.2)] bg-[rgba(234,96,117,0.08)]" : "",
        !drawerOpen && running ? "border-white/[0.08] bg-white/[0.04]" : "",
        !drawerOpen && !hasError && !running ? "border-white/[0.08] bg-white/[0.035]" : ""
      )}
    >
      <div className="relative h-7 w-7 shrink-0">
        <div
          className="absolute inset-0 rounded-full"
          style={{
            background: running
              ? `color-mix(in oklab, ${accent} 22%, transparent)`
              : hasError
                ? "rgba(234,96,117,0.14)"
                : doneVisible
                  ? "rgba(145,239,187,0.12)"
                  : "rgba(255,255,255,0.06)"
          }}
        />
        <div
          className="absolute inset-0 grid place-items-center"
          style={{
            color: running ? accent : hasError ? "var(--sync-danger, #ea6075)" : doneVisible ? "var(--sync-success, #91efbb)" : "rgba(255,255,255,0.6)"
          }}
        >
          {running ? (
            <Loader2 size={14} className="animate-spin" strokeWidth={2.25} />
          ) : hasError ? (
            <span className="text-[13px] font-bold">!</span>
          ) : doneVisible ? (
            <Check size={14} strokeWidth={2.5} />
          ) : (
            <RefreshCw size={14} strokeWidth={2.25} className="opacity-70" />
          )}
        </div>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 text-[12.5px] font-semibold leading-tight">
          <span
            className={cn("h-1.5 w-1.5 shrink-0 rounded-full", running && "animate-pulse")}
            style={{ background: statusColor }}
          />
          <span className="truncate">{title}</span>
        </div>
        <div className="mt-0.5 truncate text-[10.5px] text-white/50">{subtitle}</div>
      </div>
      <div
        className={cn(
          "grid h-[22px] w-[22px] shrink-0 place-items-center rounded-md bg-white/[0.06] text-white/70 transition-transform duration-200",
          drawerOpen && "rotate-180"
        )}
      >
        <ChevronRight size={12} strokeWidth={2.25} />
      </div>
    </button>
  );
}
