import { RefreshCw } from "lucide-react";
import { Button } from "./button";
import { Card, CardContent } from "./card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "./tooltip";
import { formatRelativeTimestamp } from "../../lib/utils";

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
    badge?: string | null;
    flag_reason?: string | null;
  };
  onRefresh?: () => void;
  isRefreshing?: boolean;
  actionLabel?: string;
  onAction?: () => void;
}) {
  const formatted = formatSignalCard(signal);
  return (
    <Card className="detail-signal-card">
      <CardContent className="detail-signal-content">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <p className="detail-signal-label">{signal.label}</p>
          {signal.step !== "opportunity" && onRefresh ? (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" className="h-8 w-8 shrink-0 rounded-lg p-0 text-slate-500 hover:bg-slate-100" aria-label={`Refresh ${signal.label}`} onClick={onRefresh} disabled={isRefreshing}>
                    <RefreshCw className={isRefreshing ? "animate-spin" : ""} size={15} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Refresh {signal.label}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : null}
        </div>
        <strong className="detail-signal-value">{formatted.metric}</strong>
        <p className="detail-signal-description">{formatted.secondary}</p>
        {formatted.accent ? <p className="text-xs text-slate-500">{formatted.accent}</p> : null}
        {signal.badge ? <span className="self-start rounded-md bg-amber-50 px-2 py-1 text-xs font-medium text-amber-900" title={signal.flag_reason || undefined}>{signal.badge}</span> : null}
        {signal.step !== "index" ? <p className="detail-signal-updated">{signal.updated_at ? `Updated: ${formatRelativeTimestamp(signal.updated_at)}` : "Score summary"}</p> : null}
        {signal.step === "index" && (signal.action_label || actionLabel) ? (
          <button className="self-start text-left text-xs font-medium text-[#5746d9] underline-offset-4 hover:underline" type="button" onClick={onAction}>
            {actionLabel || signal.action_label}
          </button>
        ) : null}
      </CardContent>
    </Card>
  );
}
