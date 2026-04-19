import { RefreshCw } from "lucide-react";
import { Button } from "./button";
import { Card, CardContent } from "./card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./tooltip";
import { formatRelativeTimestamp } from "../../lib/utils";

const signalCardTones: Record<string, string> = {
  index: "border-[#dbe5f3] bg-[linear-gradient(135deg,#ffffff_0%,#eef6ff_100%)]",
  gsc: "border-[#f2d9cf] bg-[linear-gradient(135deg,#fff7f4_0%,#ffe7de_100%)]",
  ga4: "border-[#efe2bf] bg-[linear-gradient(135deg,#fffdf5_0%,#fff3cf_100%)]",
  speed: "border-[#d8e9e1] bg-[linear-gradient(135deg,#f8fffb_0%,#e3f7ee_100%)]",
  speed_desktop: "border-[#d8e9e1] bg-[linear-gradient(135deg,#f8fffb_0%,#e3f7ee_100%)]",
  opportunity: "border-[#e1dbf5] bg-[linear-gradient(135deg,#fbf9ff_0%,#ede8ff_100%)]"
};

function formatSignalCard(signal: {
  step: string;
  value: string;
  sublabel: string;
  updated_at?: string | number | null;
}) {
  if (signal.step.startsWith("gsc_")) {
    return {
      metric: signal.value,
      secondary: signal.sublabel || "Google Search",
      accent: null
    };
  }

  if (signal.step === "ga4") {
    return {
      metric: signal.value,
      secondary: signal.sublabel || "No GA4 data",
      accent: null
    };
  }

  if (signal.step === "speed" || signal.step === "speed_desktop") {
    const perfMatch = signal.value.match(/(\d+)/);
    return {
      metric: perfMatch?.[1] ?? signal.value,
      secondary: "Performance",
      accent: signal.sublabel || null
    };
  }

  if (signal.step === "opportunity") {
    return {
      metric: signal.value,
      secondary: signal.sublabel ? `${signal.sublabel} priority` : "Score summary",
      accent: null
    };
  }

  return {
    metric: signal.value,
    secondary: signal.sublabel,
    accent: null
  };
}

export function SignalCard({
  signal,
  onRefresh,
  isRefreshing,
  actionLabel,
  onAction
}: {
  signal: {
    label: string;
    value: string;
    sublabel: string;
    updated_at?: string | number | null;
    step: string;
    action_label?: string | null;
    action_href?: string | null;
  };
  onRefresh?: () => void;
  isRefreshing?: boolean;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const formatted = formatSignalCard(signal);
  const gscFamily = signal.step.startsWith("gsc_");
  const tone =
    signalCardTones[gscFamily ? "gsc" : signal.step] ||
    "border-[#dde6f3] bg-[linear-gradient(180deg,#ffffff_0%,#f7fbff_100%)]";

  return (
    <Card className={tone}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex min-h-[168px] min-w-0 flex-1 flex-col pr-2">
            <p className="text-xs uppercase tracking-[0.18em] text-slate-500">{signal.label}</p>
            <div className="mt-4">
              <strong className="block text-[clamp(1.45rem,1.8vw,1.95rem)] font-bold leading-[1.12] tracking-[-0.03em] text-ink break-words">
                {formatted.metric}
              </strong>
              <p className="mt-2 text-sm leading-6 text-slate-600 [text-wrap:balance]">
                {formatted.secondary}
              </p>
              {formatted.accent ? (
                <p className="mt-1 text-sm font-medium leading-6 text-slate-500">{formatted.accent}</p>
              ) : null}
            </div>
            <p className="mt-auto pt-4 text-xs leading-5 text-slate-400">
              {signal.updated_at ? `Updated: ${formatRelativeTimestamp(signal.updated_at)}` : "Score summary"}
            </p>
            {signal.step === "index" && (signal.action_label || actionLabel) ? (
              <button
                className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-ocean transition hover:text-[#155eef]"
                type="button"
                onClick={onAction}
              >
                {actionLabel || signal.action_label}
              </button>
            ) : null}
          </div>
          {signal.step !== "opportunity" && onRefresh ? (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" className="mt-0.5 h-10 w-10 shrink-0 rounded-full bg-white/70 p-0" onClick={onRefresh} disabled={isRefreshing}>
                    <RefreshCw className={isRefreshing ? "animate-spin" : ""} size={16} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Refresh {signal.label}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}
