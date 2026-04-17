import { useEffect, useMemo, useState } from "react";
import type { ArticleDraftProgressEvent } from "../lib/run-article-draft-stream";

const PHASE_ORDER = ["content", "image", "encode", "body", "shopify", "attach", "local"] as const;

const PHASE_LABELS: Record<string, string> = {
  content: "Generate article content (AI)",
  image: "Featured image — generate & upload",
  encode: "Convert images to WebP (Pillow)",
  body: "Hero image in article body",
  shopify: "Create draft in Shopify",
  attach: "Featured image on article record",
  local: "Save to local database"
};

type PhaseUiStatus = "pending" | "active" | "done" | "skipped";

function formatDraftElapsed(ms: number): string {
  if (ms < 60000) {
    return `${(ms / 1000).toFixed(1)}s`;
  }
  const totalSec = Math.floor(ms / 1000);
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function buildPhaseStatuses(events: ArticleDraftProgressEvent[]): Record<string, PhaseUiStatus> {
  const map: Record<string, PhaseUiStatus> = {};
  for (const id of PHASE_ORDER) {
    map[id] = "pending";
  }
  for (const e of events) {
    if (!e.phase) continue;
    const st = (e.state || "").toLowerCase();
    if (st === "done") map[e.phase] = "done";
    else if (st === "skipped") map[e.phase] = "skipped";
    else if (st) map[e.phase] = "active";
  }
  return map;
}

export function ArticleDraftProgressPanel(props: {
  events: ArticleDraftProgressEvent[];
  isRunning: boolean;
  runKey: number;
}) {
  const { events, isRunning, runKey } = props;
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!isRunning) {
      setElapsedMs(0);
      return;
    }
    const t0 = performance.now();
    setElapsedMs(0);
    const id = window.setInterval(() => {
      setElapsedMs(Math.floor(performance.now() - t0));
    }, 100);
    return () => {
      window.clearInterval(id);
      setElapsedMs(Math.floor(performance.now() - t0));
    };
  }, [isRunning, runKey]);

  const phaseStatuses = useMemo(() => buildPhaseStatuses(events), [events]);
  const latestMessage = events.length ? events[events.length - 1].message : "";
  const imageProgress = useMemo(() => {
    let total: number | undefined;
    let done: number | undefined;
    for (const e of events) {
      if (typeof e.images_total === "number") total = e.images_total;
      if (typeof e.images_done === "number") done = e.images_done;
    }
    if (total == null) return null;
    return { total, done: done ?? 0 };
  }, [events]);

  if (!isRunning && events.length === 0) {
    return null;
  }

  return (
    <div className="rounded-2xl border border-line bg-white p-4 text-sm text-slate-600">
      <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line/80 pb-3">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Elapsed</p>
          <p className="mt-0.5 text-2xl font-semibold tabular-nums text-ink">{formatDraftElapsed(elapsedMs)}</p>
        </div>
        <div className="max-w-[16rem] text-right text-xs text-slate-500">
          {imageProgress ? (
            <p className="mb-1 font-medium text-ink">
              Images: {imageProgress.done}/{imageProgress.total} uploaded
            </p>
          ) : null}
          <p>{isRunning ? "Timed in your browser while the pipeline runs." : "Run finished."}</p>
        </div>
      </div>

      <ul className="mt-4 space-y-2.5">
        {PHASE_ORDER.map((phaseId) => {
          const label = PHASE_LABELS[phaseId] || phaseId;
          const st = phaseStatuses[phaseId] || "pending";
          return (
            <li key={phaseId} className="flex items-center gap-3">
              <span
                className={`inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-semibold ${
                  st === "done"
                    ? "border-[#8eb89a] bg-[#e8f4ec] text-[#255b38]"
                    : st === "skipped"
                      ? "border-line bg-slate-100 text-slate-400"
                      : st === "active"
                        ? "border-ocean bg-ocean/10 text-ocean"
                        : "border-line bg-[#f7f9fc] text-slate-300"
                }`}
                aria-hidden
              >
                {st === "done" ? "✓" : st === "skipped" ? "—" : st === "active" ? "●" : ""}
              </span>
              <span
                className={
                  st === "active"
                    ? "font-semibold text-ink"
                    : st === "done"
                      ? "text-slate-600"
                      : st === "skipped"
                        ? "text-slate-400 line-through decoration-slate-300"
                        : "text-slate-400"
                }
              >
                {label}
              </span>
            </li>
          );
        })}
      </ul>

      {latestMessage ? (
        <p className="mt-4 rounded-xl bg-[#f7f9fc] px-3 py-2 text-xs text-slate-700">
          <span className="font-semibold text-ink">Latest: </span>
          {latestMessage}
        </p>
      ) : null}

      <p className="mt-3 text-xs text-slate-500">
        Content generation is one long AI request; images are generated, encoded to WebP when possible, then uploaded. Shopify
        steps run after the article JSON is ready. If Shopify omits the image on create, we retry with{" "}
        <span className="font-medium text-ink">articleUpdate</span> (with short delays).
      </p>
    </div>
  );
}
