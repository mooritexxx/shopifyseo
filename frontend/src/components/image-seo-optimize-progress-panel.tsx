import { useEffect, useMemo, useState } from "react";

import { cn } from "../lib/utils";

const STEPS = [
  {
    id: "draft",
    label: "Build draft (download, AI alt, filename & encoding)",
    shortLabel: "Build draft (download, alt, filename)"
  },
  { id: "shopify", label: "Save to Shopify (alt & media)", shortLabel: "Save to Shopify (alt & media)" }
] as const;

function formatElapsed(ms: number): string {
  if (ms < 60000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

type StepUiStatus = "pending" | "active" | "done";

export type ImageSeoOptimizeProgressStatus = "idle" | "running" | "complete";

export function ImageSeoOptimizeProgressPanel(props: {
  status: ImageSeoOptimizeProgressStatus;
  pipelinePhase: "draft" | "shopify";
  runKey: number;
  latestMessage?: string;
  /** Hide the long explanatory footer (modal embed). */
  compact?: boolean;
  className?: string;
}) {
  const { status, pipelinePhase, runKey, latestMessage, compact, className } = props;
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (status === "idle") {
      setElapsedMs(0);
    }
  }, [status]);

  useEffect(() => {
    if (status !== "running") return;
    const t0 = performance.now();
    setElapsedMs(0);
    const id = window.setInterval(() => {
      setElapsedMs(Math.floor(performance.now() - t0));
    }, 100);
    return () => {
      window.clearInterval(id);
      setElapsedMs(Math.floor(performance.now() - t0));
    };
  }, [status, runKey]);

  const stepStatuses = useMemo(() => {
    const map: Record<string, StepUiStatus> = { draft: "pending", shopify: "pending" };
    if (status === "idle") return map;
    if (status === "complete") {
      map.draft = "done";
      map.shopify = "done";
      return map;
    }
    if (pipelinePhase === "draft") {
      map.draft = "active";
      map.shopify = "pending";
    } else {
      map.draft = "done";
      map.shopify = "active";
    }
    return map;
  }, [status, pipelinePhase]);

  const stepIndex = status === "idle" ? 0 : status === "complete" ? STEPS.length : pipelinePhase === "draft" ? 1 : 2;

  return (
    <div className={cn("rounded-2xl border border-line bg-white p-4 text-sm text-slate-600", className)}>
      <div className="flex flex-wrap items-end justify-between gap-2 border-b border-line/80 pb-2">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Elapsed</p>
          <p
            className={cn(
              "mt-0.5 font-semibold tabular-nums text-ink",
              compact ? "text-lg" : "text-xl sm:text-2xl"
            )}
          >
            {status === "idle" ? "—" : formatElapsed(elapsedMs)}
          </p>
        </div>
        <div className={cn("text-right text-xs text-slate-500", compact ? "max-w-[13rem]" : "max-w-[16rem]")}>
          <p className="mb-1 font-medium text-ink">
            {status === "idle" ? "Not started" : `Step ${stepIndex}/${STEPS.length}`}
          </p>
          <p>
            {status === "idle"
              ? "Tap Optimize below to run the pipeline."
              : status === "complete"
                ? "All steps finished."
                : "Timed in your browser while the pipeline runs."}
          </p>
        </div>
      </div>

      <ul className={cn("space-y-2", compact ? "mt-2" : "mt-3")}>
        {STEPS.map((step) => {
          const st = stepStatuses[step.id] || "pending";
          return (
            <li key={step.id} className={cn("flex items-center", compact ? "gap-2" : "gap-3")}>
              <span
                className={cn(
                  "inline-flex shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                  compact ? "h-5 w-5" : "h-6 w-6",
                  st === "done"
                    ? "border-[#8eb89a] bg-[#e8f4ec] text-[#255b38]"
                    : st === "active"
                      ? "border-ocean bg-ocean/10 text-ocean"
                      : "border-line bg-[#f7f9fc] text-slate-300"
                )}
                aria-hidden
              >
                {st === "done" ? "✓" : st === "active" ? "●" : ""}
              </span>
              <span
                className={
                  st === "active" ? "font-semibold text-ink" : st === "done" ? "text-slate-600" : "text-slate-400"
                }
              >
                {compact ? step.shortLabel : step.label}
              </span>
            </li>
          );
        })}
      </ul>

      {latestMessage ? (
        <p
          className={cn(
            "rounded-lg bg-[#f7f9fc] px-2.5 text-xs leading-snug text-slate-700",
            compact ? "mt-2 py-1" : "mt-3 py-1.5"
          )}
        >
          <span className="font-semibold text-ink">Latest: </span>
          {latestMessage}
        </p>
      ) : null}

      {!compact ? (
        <p className="mt-3 text-xs text-slate-500">
          The draft step downloads the storefront image, optionally suggests alt text with your Vision model, and prepares
          filename / WebP encoding from table flags. The Shopify step updates alt on the product media and replaces the file
          when filename or WebP changes are required (new media + variant repoint).
        </p>
      ) : null}
    </div>
  );
}
